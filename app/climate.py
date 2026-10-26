"""Lagerklima: Temperatur, Luftfeuchte und CO₂ aus den Kartoffellagern (Meshtastic-Knoten mit SCD41 o. Ä.).

Die Brücke (`bridge/`) schickt die Telemetrie aller Knoten außer dem Hühnerstall an `POST /api/lager/klima`
(gleicher Schlüssel wie der Stall-Sensor). Unbekannte Knoten werden ohne Lager angelegt und erscheinen auf der
Klima-Seite zum Zuordnen (Lager, wahlweise Reihe). Grenzwerte je Lager (`Warehouse.climate_*`, sonst `DEFAULTS`).
Wie die übrigen Logik-Module ändern die Funktionen nur die Session; `check()` läuft aus `push.daily`.
"""
import re
from datetime import date, datetime, time, timedelta, timezone

from flask import url_for

from . import push
from .extensions import db
from .i18n import N_, gettext as _
from .models import ClimateReading, ClimateSensor, Warehouse, utcnow
from .push import Msg
from .storage import StorageError

# Kartoffellager: Ziel 4–8 °C und 90–95 % rF; gewarnt wird außerhalb dieser Grenzen
TARGET = {"temp": (4.0, 8.0), "hum": (90.0, 95.0)}
DEFAULTS = {"temp_min": 3.0, "temp_max": 10.0, "hum_min": 85.0, "hum_max": 97.0, "co2_max": 5000}
LIMIT_FIELDS = {"temp_min": (-10, 30), "temp_max": (-10, 30), "hum_min": (0, 100), "hum_max": (0, 100),
                "co2_max": (400, 40000)}
OFFLINE_AFTER = timedelta(hours=2)
KEEP_DAYS = 3 * 366
RANGES = {"24h": (timedelta(hours=24), None, N_("24 Stunden")),
          "7d": (timedelta(days=7), timedelta(hours=1), N_("7 Tage")),
          "30d": (timedelta(days=30), timedelta(hours=4), N_("30 Tage")),
          "saison": (None, timedelta(days=1), N_("Saison"))}
ISSUES = {"temp_low": N_("zu kalt"), "temp_high": N_("zu warm"), "hum_low": N_("zu trocken"),
          "hum_high": N_("zu feucht"), "co2_high": N_("CO₂ zu hoch – lüften"), "offline": N_("Sensor meldet sich nicht")}


class SensorError(ValueError):
    pass


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


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


# --- Werte annehmen -----------------------------------------------------------------------

def record(data, now=None):
    """Messwerte eines Knotens speichern: node (Pflicht), temperature, humidity, co2, battery, voltage, at.
    Unbekannter Knoten → neuer Sensor ohne Lager. Deaktivierte Sensoren werden ignoriert (None)."""
    now = now or utcnow()
    node = str(data.get("node") or "").strip().lower()[:20]
    if not node:
        raise SensorError("node fehlt")
    at = now
    if data.get("at"):
        try:
            at = datetime.fromtimestamp(float(data["at"]), timezone.utc)
        except (TypeError, ValueError, OverflowError):
            raise SensorError("at: Unix-Zeit erwartet")
        if at > now + timedelta(minutes=5):
            at = now
        elif at < now - timedelta(days=7):
            raise SensorError("at: älter als 7 Tage")
    values = {"temperature": _number(data, "temperature", -40, 70), "humidity": _number(data, "humidity", 0, 100),
              "co2": _number(data, "co2", 0, 40000), "battery": _number(data, "battery", 0, 101),
              "voltage": _number(data, "voltage", 0, 30)}
    if all(v is None for v in values.values()):
        raise SensorError("keine Werte")
    sensor = ClimateSensor.query.filter_by(node=node).first()
    if sensor is None:
        if values["temperature"] is None and values["co2"] is None:
            return None                                # nur Akku eines fremden Knotens – nicht anlegen
        sensor = ClimateSensor(node=node)
        db.session.add(sensor)
        db.session.flush()
    if not sensor.active:
        return None
    if values["co2"] is not None:
        values["co2"] = int(round(values["co2"]))
    reading = ClimateReading(sensor_id=sensor.id, at=at, **values)
    db.session.add(reading)
    db.session.flush()
    return reading


# --- Stammdaten ---------------------------------------------------------------------------

NODE_RE = re.compile(r"^!?([0-9a-f]{8})$")


def normalize_node(raw):
    """„!A1B2C3D4“, „a1b2c3d4“ → „!a1b2c3d4“; sonst None (Knoten-ID steht in der Meshtastic-App)."""
    m = NODE_RE.match((raw or "").strip().lower())
    return f"!{m.group(1)}" if m else None


def add_sensor(node, name, warehouse, row):
    """Sensor von Hand anlegen (bevor er Werte schickt)."""
    node = normalize_node(node)
    if node is None:
        raise StorageError(_("Bitte die Knoten-ID aus der Meshtastic-App angeben, z. B. !a1b2c3d4."))
    if ClimateSensor.query.filter_by(node=node).first():
        raise StorageError(_("Den Sensor {node} gibt es schon.", node=node))
    sensor = ClimateSensor(node=node)
    db.session.add(sensor)
    return save_sensor(sensor, name, warehouse, row)


def save_sensor(sensor, name, warehouse, row, active=True):
    if warehouse is not None and row is not None and not 1 <= row <= warehouse.rows:
        raise StorageError(_("Die Reihe gibt es in {name} nicht.", name=warehouse.name))
    sensor.name = " ".join((name or "").split())[:100] or None
    sensor.warehouse_id = warehouse.id if warehouse else None
    sensor.row = row if warehouse else None
    sensor.active = bool(active)
    db.session.flush()
    return sensor


def limits(warehouse):
    return {key: (getattr(warehouse, f"climate_{key}") if warehouse is not None
                  and getattr(warehouse, f"climate_{key}") is not None else default)
            for key, default in DEFAULTS.items()}


def save_limits(warehouse, values):
    for key, (low, high) in LIMIT_FIELDS.items():
        value = values.get(key)
        if value is not None and not low <= value <= high:
            raise StorageError(_("Grenzwert außerhalb des möglichen Bereichs."))
    merged = {k: (values.get(k) if values.get(k) is not None else v) for k, v in DEFAULTS.items()}
    if merged["temp_min"] >= merged["temp_max"] or merged["hum_min"] >= merged["hum_max"]:
        raise StorageError(_("Der untere Grenzwert muss kleiner als der obere sein."))
    for key in DEFAULTS:
        value = values.get(key)
        setattr(warehouse, f"climate_{key}", int(value) if key == "co2_max" and value is not None else value)
    db.session.flush()


# --- Auswertung ---------------------------------------------------------------------------

def _latest(sensor_id, column):
    row = (ClimateReading.query.filter(ClimateReading.sensor_id == sensor_id, column.isnot(None))
           .order_by(ClimateReading.at.desc()).first())
    return (getattr(row, column.key), _aware(row.at)) if row else (None, None)


def status(sensor, now=None):
    """{sensor, temperature, humidity, co2, battery, seen, offline, issues: [(Schlüssel, Text)], level}."""
    now = now or utcnow()
    values = {key: _latest(sensor.id, getattr(ClimateReading, key))
              for key in ("temperature", "humidity", "co2", "battery")}
    seen = max([t for _v, t in values.values() if t], default=None)
    result = {"sensor": sensor, "seen": seen, "offline": seen is None or now - seen > OFFLINE_AFTER,
              **{key: v for key, (v, _t) in values.items()}}
    issues = []
    if result["offline"]:
        issues.append("offline")
    elif sensor.warehouse is not None:
        lim = limits(sensor.warehouse)
        t, h, c = result["temperature"], result["humidity"], result["co2"]
        if t is not None and t < lim["temp_min"]:
            issues.append("temp_low")
        if t is not None and t > lim["temp_max"]:
            issues.append("temp_high")
        if h is not None and h < lim["hum_min"]:
            issues.append("hum_low")
        if h is not None and h > lim["hum_max"]:
            issues.append("hum_high")
        if c is not None and c > lim["co2_max"]:
            issues.append("co2_high")
    result["issues"] = [(key, ISSUES[key]) for key in issues]
    result["level"] = "danger" if any(k in ("temp_low", "temp_high", "co2_high") for k in issues) \
        else "warning" if issues else "ok"
    t, h = result["temperature"], result["humidity"]
    result["in_target"] = (t is not None and TARGET["temp"][0] <= t <= TARGET["temp"][1]
                           and (h is None or TARGET["hum"][0] <= h <= TARGET["hum"][1]))
    return result


def sensors(active_only=True):
    query = ClimateSensor.query
    if active_only:
        query = query.filter_by(active=True)
    return query.order_by(ClimateSensor.warehouse_id, ClimateSensor.row, ClimateSensor.id).all()


def overview(now=None):
    """{"warehouses": [(Lager, [Status …])], "unassigned": [Sensor …], "issues": n}."""
    by_wh, unassigned = {}, []
    for sensor in sensors():
        if sensor.warehouse is None:
            unassigned.append(sensor)
            continue
        by_wh.setdefault(sensor.warehouse_id, (sensor.warehouse, []))[1].append(status(sensor, now))
    groups = sorted(by_wh.values(), key=lambda g: g[0].name)
    return {"warehouses": groups, "unassigned": unassigned,
            "issues": sum(1 for _w, rows in groups for s in rows if s["issues"])}


def by_row(warehouse, now=None):
    """Für die Lagerkarte: {Reihe: Status} und Status ohne Reihe (ganzes Lager)."""
    rows, whole = {}, []
    for sensor in ClimateSensor.query.filter_by(warehouse_id=warehouse.id, active=True):
        info = status(sensor, now)
        if sensor.row:
            rows[sensor.row] = info
        else:
            whole.append(info)
    return rows, whole


def season_start(today=None):
    """Lagersaison ab 1. August (Ernte), sonst die des Vorjahres."""
    today = today or date.today()
    year = today.year if today.month >= 8 else today.year - 1
    return datetime.combine(date(year, 8, 1), time(), tzinfo=timezone.utc)


def series(sensor, key="24h", now=None):
    """Kurve für den Zeitraum: [(Zeit, °C, %, ppm)], bei längeren Zeiträumen gemittelt."""
    now = now or utcnow()
    span, step, _label = RANGES.get(key, RANGES["24h"])
    start = season_start(now.date()) if span is None else now - span
    rows = (ClimateReading.query.filter(ClimateReading.sensor_id == sensor.id, ClimateReading.at >= start)
            .order_by(ClimateReading.at).all())
    if step is None:
        return [(_aware(r.at), r.temperature, r.humidity, r.co2) for r in rows]
    buckets = {}
    for r in rows:
        at = _aware(r.at)
        slot = int((at - start) / step)
        buckets.setdefault(slot, []).append(r)
    result = []
    for slot in sorted(buckets):
        group = buckets[slot]

        def avg(attr):
            values = [getattr(r, attr) for r in group if getattr(r, attr) is not None]
            return sum(values) / len(values) if values else None
        result.append((start + step * slot + step / 2, avg("temperature"), avg("humidity"), avg("co2")))
    return result


def important(now=None):
    """Einträge für „Jetzt wichtig“ auf der Startseite."""
    items = []
    for sensor in sensors():
        if sensor.warehouse is None:
            continue
        info = status(sensor, now)
        if info["issues"]:
            items.append({"level": "danger" if info["level"] == "danger" else "warning",
                          "title": _("Lagerklima: {name}", name=sensor.label),
                          "sub": ", ".join(_(text) for _k, text in info["issues"]),
                          "url": url_for("climate.overview_page") + f"#s{sensor.id}"})
    return items


def check(now, done, mark):
    """Aus `push.daily`: je Sensor und Problem höchstens einmal am Tag eine Push-Nachricht."""
    today = now.date()
    recipients = [u for u in push.editors()]
    for sensor in sensors():
        if sensor.warehouse is None:
            continue
        info = status(sensor)
        for key, text in info["issues"]:
            job = f"climate_{sensor.id}_{key}"
            if done(job, today):
                continue
            mark(job, today)
            push.notify(recipients, "climate", Msg("Lagerklima {name}: {issue}", name=sensor.label, issue=Msg(text)),
                        _values_msg(info), url_for("climate.overview_page") + f"#s{sensor.id}",
                        tag=f"climate-{sensor.id}-{key}-{today.isoformat()}")
    if not done("climate_cleanup", today):
        mark("climate_cleanup", today)
        ClimateReading.query.filter(ClimateReading.at < utcnow() - timedelta(days=KEEP_DAYS)).delete()
        db.session.commit()


def _values_msg(info):
    parts = []
    if info["temperature"] is not None:
        parts.append(f"{info['temperature']:.1f} °C".replace(".", ","))
    if info["humidity"] is not None:
        parts.append(f"{info['humidity']:.0f} %")
    if info["co2"] is not None:
        parts.append(f"{info['co2']} ppm CO₂")
    return " · ".join(parts)


def warehouses():
    return Warehouse.query.order_by(Warehouse.name).all()
