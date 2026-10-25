"""Sensor am Hühnerstall: Temperatur, Luftfeuchte, Akku und Klappe (auf/zu).

Die Werte kommen per HTTP von der Meshtastic-Brücke (`bridge/`), die die MQTT-Nachrichten des Knotens im Haus
liest: `POST /api/huehner/sensor` mit dem Schlüssel aus der Hühner-Übersicht (nur der Hash liegt in `settings`).
Wie die übrigen Logik-Module ändern die Funktionen nur die Session; `alerts()` läuft aus `chickens.evening_check`.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from flask import url_for

from . import push
from .extensions import db
from .models import CoopDoorEvent, CoopReading, Setting, utcnow
from .push import Msg

TOKEN_KEY = "coop_api_token"                     # sha256 des Schlüssels
OFFLINE_AFTER = timedelta(hours=2)               # so lange ohne Nachricht = Sensor meldet sich nicht
FROST_C = 0.5                                    # Stall: Wasser friert, Kämme leiden
HEAT_C = 30.0                                    # Stall: Hitzestress
KEEP_DAYS = 400                                  # Messwerte so lange aufbewahren


class SensorError(ValueError):
    pass


# --- Schlüssel ----------------------------------------------------------------------------

def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_token():
    """Neuen Schlüssel erzeugen und (gehasht) speichern – der alte gilt danach nicht mehr. Gibt ihn einmal zurück."""
    token = secrets.token_urlsafe(24)
    Setting.put(TOKEN_KEY, _hash(token))
    return token


def has_token():
    setting = db.session.get(Setting, TOKEN_KEY)
    return bool(setting and setting.value)


def check_token(token):
    setting = db.session.get(Setting, TOKEN_KEY)
    return bool(token and setting and setting.value and hmac.compare_digest(setting.value, _hash(token)))


# --- Werte annehmen -----------------------------------------------------------------------

def _number(data, key, low, high):
    value = data.get(key)
    if value is None or value == "":
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise SensorError(f"{key}: keine Zahl")
    if not low <= value <= high:
        raise SensorError(f"{key}: außerhalb {low}…{high}")
    return value


def _door(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("open", "offen", "auf", "1", "true"):
        return True
    if text in ("closed", "zu", "geschlossen", "0", "false"):
        return False
    raise SensorError("door: open/closed erwartet")


def record(data, now=None):
    """Nachricht der Brücke speichern. `data`: temperature, humidity, battery, voltage, rssi, snr, door, node, at
    (alles wahlweise). Gibt {"reading": …, "door": …} zurück (None, wenn nichts davon dabei war)."""
    now = now or utcnow()
    at = now
    if data.get("at"):
        try:
            at = datetime.fromtimestamp(float(data["at"]), timezone.utc)
        except (TypeError, ValueError, OverflowError):
            raise SensorError("at: Unix-Zeit erwartet")
        if at > now + timedelta(minutes=5):
            at = now                                   # Uhr des Knotens geht vor → Empfangszeit
        elif at < now - timedelta(days=7):
            raise SensorError("at: älter als 7 Tage")
    node = str(data.get("node") or "")[:20] or None
    values = {"temperature": _number(data, "temperature", -40, 70), "humidity": _number(data, "humidity", 0, 100),
              "battery": _number(data, "battery", 0, 101), "voltage": _number(data, "voltage", 0, 30),
              "rssi": _number(data, "rssi", -200, 50), "snr": _number(data, "snr", -50, 50)}
    door = _door(data.get("door"))
    result = {"reading": None, "door": None}
    if any(v is not None for v in values.values()):
        result["reading"] = CoopReading(at=at, node=node, **values)
        db.session.add(result["reading"])
    if door is not None:
        last = door_state()
        if last is None or last.is_open != door:      # nur Änderungen speichern (Statusmeldungen wiederholen sich)
            result["door"] = CoopDoorEvent(at=at, is_open=door, node=node)
            db.session.add(result["door"])
    if result["reading"] is None and door is None:
        raise SensorError("keine Werte")
    db.session.flush()
    return result


# --- Anzeige ------------------------------------------------------------------------------

def door_state():
    return CoopDoorEvent.query.order_by(CoopDoorEvent.at.desc(), CoopDoorEvent.id.desc()).first()


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def last_seen():
    times = [_aware(t) for t in (db.session.query(db.func.max(CoopReading.at)).scalar(),
                                 db.session.query(db.func.max(CoopDoorEvent.at)).scalar()) if t]
    return max(times) if times else None


def status(now=None):
    """Für Karte und Übersicht: None (noch nie Werte) oder {latest, door, door_since, seen, offline, min24, max24}."""
    now = now or utcnow()
    seen = last_seen()
    if seen is None:
        return None
    latest = {}
    for key in ("temperature", "humidity", "battery", "voltage", "rssi", "snr"):
        row = (CoopReading.query.filter(getattr(CoopReading, key).isnot(None))
               .order_by(CoopReading.at.desc()).first())
        latest[key] = (getattr(row, key), _aware(row.at)) if row else (None, None)
    day = CoopReading.query.filter(CoopReading.at >= now - timedelta(hours=24), CoopReading.temperature.isnot(None))
    temps = [r.temperature for r in day]
    door = door_state()
    return {"temperature": latest["temperature"][0], "humidity": latest["humidity"][0],
            "battery": latest["battery"][0], "voltage": latest["voltage"][0], "rssi": latest["rssi"][0],
            "snr": latest["snr"][0], "measured": latest["temperature"][1] or latest["humidity"][1],
            "door": door.is_open if door else None, "door_since": _aware(door.at) if door else None,
            "seen": seen, "offline": now - seen > OFFLINE_AFTER,
            "min24": min(temps) if temps else None, "max24": max(temps) if temps else None}


def history(hours=48, now=None):
    """Temperatur/Feuchte der letzten Stunden für die Kurve: [(Zeit, °C, %)], ältester zuerst."""
    now = now or utcnow()
    rows = (CoopReading.query.filter(CoopReading.at >= now - timedelta(hours=hours))
            .filter((CoopReading.temperature.isnot(None)) | (CoopReading.humidity.isnot(None)))
            .order_by(CoopReading.at).all())
    return [(_aware(r.at), r.temperature, r.humidity) for r in rows]


def door_events(limit=20):
    return CoopDoorEvent.query.order_by(CoopDoorEvent.at.desc(), CoopDoorEvent.id.desc()).limit(limit).all()


def cleanup(now=None):
    now = now or utcnow()
    CoopReading.query.filter(CoopReading.at < now - timedelta(days=KEEP_DAYS)).delete()


# --- Warnungen ----------------------------------------------------------------------------

def alerts(now, done, mark, recipients):
    """Aus dem Zeitplan: Frost/Hitze im Stall, Sensor stumm – je höchstens einmal am Tag."""
    info = status()
    if info is None:
        return
    today = now.date()
    url = url_for("chickens.overview")
    if info["offline"] and not done("coop_offline", today):
        mark("coop_offline", today)
        push.notify(recipients, "coop_sensor", Msg("Hühnerstall: Sensor meldet sich nicht"),
                    Msg("Letzte Nachricht {time} Uhr.",
                        time=info["seen"].astimezone(now.tzinfo).strftime("%d.%m. %H:%M")),
                    url, tag=f"coop-offline-{today.isoformat()}")
        return
    temp = info["temperature"]
    if temp is None or info["offline"]:
        return
    if temp <= FROST_C and not done("coop_frost", today):
        mark("coop_frost", today)
        push.notify(recipients, "coop_sensor", Msg("Hühnerstall: Frost ({t} °C)", t=f"{temp:.1f}".replace(".", ",")),
                    Msg("Tränke prüfen – Wasser friert."), url, tag=f"coop-frost-{today.isoformat()}")
    if temp >= HEAT_C and not done("coop_heat", today):
        mark("coop_heat", today)
        push.notify(recipients, "coop_sensor", Msg("Hühnerstall: Hitze ({t} °C)", t=f"{temp:.1f}".replace(".", ",")),
                    Msg("Lüften, Schatten und genug Wasser."), url, tag=f"coop-heat-{today.isoformat()}")
