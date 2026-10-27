"""Rubrik Sensoren: alle Meshtastic-Knoten an einer Stelle.

Die Brücke schickt jedes Paket an `/api/meshtastic` (`record`). Hier steht, wofür ein Knoten da ist (`MeshNode.purpose`):
Hühnerstall (Kontakt unten + Messwerte → `coop.record`), Kontakt oben (→ `coop.record` mit door_top), Lagerklima
(→ `climate.record`, Lager/Reihe in `ClimateSensor`) oder nicht verwenden. Neue Knoten warten, bis der Admin sie
zuordnet. Committet nicht.
"""
import base64
import re
import secrets
from datetime import timedelta, timezone

import segno

from . import climate, coop
from .extensions import db
from .i18n import N_
from .models import ClimateSensor, MeshNode, Setting, utcnow
from .storage import StorageError

PURPOSES = {"coop": N_("Hühnerstall – Klappe unten und Messwerte"), "coop_top": N_("Hühnerstall – Kontakt oben"),
            "climate": N_("Lagerklima"), "ignore": N_("Nicht verwenden")}
SINGLE = ("coop", "coop_top")                  # davon gibt es je nur einen Knoten
OFFLINE_AFTER = timedelta(hours=2)
NEW_DAYS = 30                                  # nicht zugeordnete Knoten so lange anzeigen
VALUE_KEYS = ("temperature", "humidity", "co2", "battery", "voltage", "pressure", "gas", "iaq")
# Klappe: „aktiv“ (detected / state: 1) heißt … – unten „open“ (Klappe offen), oben „away“ (nicht ganz oben)
DOOR_KEY, TOP_KEY = "coop_door_active", "coop_top_active"

STATE_RE = re.compile(r"state:\s*([01])\b", re.I)
DETECTED_RE = re.compile(r"\bdetected\b", re.I)


class SensorError(ValueError):
    pass


def _setting(key):
    setting = db.session.get(Setting, key)
    return setting.value if setting else None


def door_settings(bridge=None):
    """{"door": "open"|"closed", "top": "away"|"top"} – Einstellung in der App, sonst was die Brücke mitschickt."""
    bridge = bridge or {}
    door = _setting(DOOR_KEY) or bridge.get("door_active") or "open"
    top = _setting(TOP_KEY) or bridge.get("top_active") or "away"
    return {"door": door if door in ("open", "closed") else "open", "top": top if top in ("away", "top") else "away"}


def save_door_settings(door_inverted, top_inverted):
    Setting.put(DOOR_KEY, "closed" if door_inverted else "open")
    Setting.put(TOP_KEY, "top" if top_inverted else "away")


def detection(text):
    """Text des Detection-Sensor-Moduls → True (aktiv) / False / None (kein Schaltzustand)."""
    lower = (text or "").lower()
    match = STATE_RE.search(lower)
    if match:
        return match.group(1) == "1"
    return True if DETECTED_RE.search(lower) else None


# --- Kanal --------------------------------------------------------------------------------
# Der private Meshtastic-Kanal, auf dem alle Hof-Knoten funken. Die App erzeugt daraus den Kanal-Link/QR-Code der
# Meshtastic-App (meshtastic.org/e/#…, ein `ChannelSet` als Protobuf) – damit ist ein neuer Sensor in einem Schritt
# auf dem richtigen Kanal. Der Link ersetzt die Kanäle und LoRa-Einstellungen des Knotens.

CHANNEL_KEYS = {"name": "mesh_channel_name", "psk": "mesh_channel_psk", "region": "mesh_region",
                "preset": "mesh_preset"}
REGIONS = {"EU_868": 3, "EU_433": 2}                       # Config.LoRaConfig.RegionCode
PRESETS = {"LONG_FAST": (0, "LongFast"), "LONG_MODERATE": (7, "LongModerate"), "LONG_SLOW": (1, "LongSlow"),
           "MEDIUM_FAST": (4, "MediumFast"), "MEDIUM_SLOW": (3, "MediumSlow"), "SHORT_FAST": (6, "ShortFast"),
           "SHORT_SLOW": (5, "ShortSlow")}               # Config.LoRaConfig.ModemPreset
CHANNEL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,11}$")
HOP_LIMIT = 3


def channel():
    """{"name", "psk", "region", "preset"} oder None, solange kein Kanal hinterlegt ist."""
    values = {key: _setting(setting) for key, setting in CHANNEL_KEYS.items()}
    if not (values["name"] and values["psk"]):
        return None
    values["region"] = values["region"] if values["region"] in REGIONS else "EU_868"
    values["preset"] = values["preset"] if values["preset"] in PRESETS else "LONG_FAST"
    return values


def _psk_bytes(psk):
    try:
        key = base64.b64decode(psk, validate=True)
    except ValueError:
        return None
    return key if len(key) in (1, 16, 32) else None


def new_psk():
    return base64.b64encode(secrets.token_bytes(32)).decode()


def save_channel(name, psk, region, preset):
    """Kanal hinterlegen. Leerer Schlüssel = neuen erzeugen (dann alle Knoten neu einrichten)."""
    name, psk = (name or "").strip(), (psk or "").strip().replace(" ", "")
    if not CHANNEL_NAME_RE.match(name):
        raise SensorError(N_("Kanalname: 1–11 Zeichen, nur Buchstaben, Ziffern, - und _."))
    if name.lower() in {label.lower() for _, label in PRESETS.values()}:
        raise SensorError(N_("Bitte einen eigenen Kanalnamen wählen – nicht den öffentlichen."))
    psk = psk or new_psk()
    key = _psk_bytes(psk)
    if key is None or len(key) == 1:
        raise SensorError(N_("Der Schlüssel muss Base64 sein (16 oder 32 Byte), so wie in der Meshtastic-App."))
    if region not in REGIONS or preset not in PRESETS:
        raise SensorError(N_("Unbekannte Region oder unbekanntes Funkprofil."))
    for key_name, value in (("name", name), ("psk", psk), ("region", region), ("preset", preset)):
        Setting.put(CHANNEL_KEYS[key_name], value)


def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _field(number, value):
    """Protobuf-Feld: int/bool als varint, bytes/str längenbegrenzt."""
    if isinstance(value, (bool, int)):
        return _varint(number << 3) + _varint(int(value))
    data = value.encode() if isinstance(value, str) else value
    return _varint(number << 3 | 2) + _varint(len(data)) + data


def channel_url(ch, uplink=False):
    """Kanal-Link der Meshtastic-App. `uplink` nur fürs Gateway (schickt die Pakete an MQTT)."""
    settings = _field(2, _psk_bytes(ch["psk"])) + _field(3, ch["name"])
    if uplink:
        settings += _field(5, True)
    lora = (_field(1, True) + _field(2, PRESETS[ch["preset"]][0]) + _field(7, REGIONS[ch["region"]])
            + _field(8, HOP_LIMIT) + _field(9, True) + _field(13, True) + _field(105, True))
    data = _field(1, settings) + _field(2, lora)
    return "https://meshtastic.org/e/#" + base64.urlsafe_b64encode(data).decode().rstrip("=")


def channel_qr(url):
    return segno.make(url, error="l").svg_inline(scale=4, border=2, dark="#000", light="#fff")


# --- Knoten -------------------------------------------------------------------------------

def get(node):
    return MeshNode.query.filter_by(node=node).first()


def by_purpose(purpose):
    return MeshNode.query.filter_by(purpose=purpose).first()


def node_ids():
    """{"coop": "!…" | None, "top": … | None} – für die Hühner-Seite."""
    coop_node, top = by_purpose("coop"), by_purpose("coop_top")
    return {"coop": coop_node.node if coop_node else None, "top": top.node if top else None}


def assign(mesh, purpose, name=None, warehouse=None, row=None):
    """Zweck festlegen. Lagerklima braucht ein Lager; Stall und Kontakt oben gibt es je nur einmal."""
    purpose = purpose or None
    if purpose is not None and purpose not in PURPOSES:
        raise StorageError(N_("Unbekannte Verwendung."))
    if purpose == "climate" and warehouse is None:
        raise StorageError(N_("Bitte ein Lager wählen."))
    if row is not None and (row < 1 or (warehouse is not None and row > warehouse.rows)):
        raise StorageError(N_("Diese Reihe gibt es im Lager nicht."))
    if purpose in SINGLE:
        for other in MeshNode.query.filter(MeshNode.purpose == purpose, MeshNode.id != mesh.id):
            other.purpose = None
    mesh.purpose = purpose
    mesh.name = (name or "").strip()[:100] or None
    sensor = ClimateSensor.query.filter_by(node=mesh.node).first()
    if purpose == "climate":
        if sensor is None:
            sensor = ClimateSensor(node=mesh.node)
            db.session.add(sensor)
        sensor.name, sensor.warehouse, sensor.row, sensor.active = mesh.name, warehouse, row, True
    elif sensor is not None:
        sensor.active = False                  # Messwerte bleiben, der Sensor erscheint nicht mehr im Lagerklima
    return mesh


def add(node, name, purpose, warehouse=None, row=None):
    """Knoten von Hand anlegen (bevor er etwas schickt)."""
    node_id = climate.normalize_node(node)
    if node_id is None:
        raise StorageError(N_("Bitte die Knoten-ID aus der Meshtastic-App angeben, z. B. !a1b2c3d4."))
    if get(node_id) is not None:
        raise StorageError(N_("Diesen Knoten gibt es schon."))
    mesh = MeshNode(node=node_id)
    db.session.add(mesh)
    return assign(mesh, purpose, name, warehouse, row)


def delete(mesh):
    """Knoten vergessen (schickt er wieder etwas, erscheint er neu). Lagerklima-Messwerte werden mit gelöscht."""
    sensor = ClimateSensor.query.filter_by(node=mesh.node).first()
    if sensor is not None:
        db.session.delete(sensor)
    db.session.delete(mesh)


# --- Werte annehmen -----------------------------------------------------------------------

def _hint(data):
    """Zuordnung aus dem Stack (ältere Einrichtung: COOP_NODE usw.) – gilt nur für einen neuen Knoten."""
    hint = data.get("hint")
    if hint not in PURPOSES or hint == "climate":
        return None
    return None if hint in SINGLE and by_purpose(hint) else hint


def record(data, now=None):
    """Ein Paket der Brücke: node, at, rssi, snr, values {…}, text (Detection-Sensor), door_active/top_active.
    Gibt den Zweck zurück, an den es ging (oder None)."""
    now = now or utcnow()
    node = climate.normalize_node(str(data.get("node") or ""))
    if node is None:
        raise SensorError("node fehlt")
    values = data.get("values") if isinstance(data.get("values"), dict) else {}
    values = {k: values[k] for k in VALUE_KEYS if isinstance(values.get(k), (int, float))}
    mesh = get(node)
    if mesh is None:
        mesh = MeshNode(node=node, purpose=_hint(data))
        db.session.add(mesh)
    mesh.last_seen = now
    for key in ("temperature", "humidity", "co2", "battery", "voltage"):
        if key in values:
            setattr(mesh, key, values[key])
    for key in ("rssi", "snr"):
        if isinstance(data.get(key), (int, float)):
            setattr(mesh, key, data[key])
    common = {"node": node, **({"at": data["at"]} if data.get("at") else {})}
    active = detection(data.get("text")) if data.get("text") else None
    doors = door_settings(data)
    try:
        if mesh.purpose == "coop":
            payload = {**common, **{k: data[k] for k in ("rssi", "snr") if data.get(k) not in (None, 0)},
                       **values}
            if active is not None:
                payload["door"] = "open" if active == (doors["door"] == "open") else "closed"
            if len(payload) > len(common):
                coop.record(payload, now)
        elif mesh.purpose == "coop_top" and active is not None:
            at_top = active != (doors["top"] == "away")
            coop.record({**common, "door_top": "open" if at_top else "closed"}, now)
        elif mesh.purpose == "climate":
            payload = {**common, **{k: values[k] for k in ("temperature", "humidity", "co2", "battery", "voltage")
                                    if k in values}}
            if len(payload) > len(common):
                climate.record(payload, now)
        else:
            return None
    except (coop.SensorError, climate.SensorError) as e:
        raise SensorError(str(e)) from e
    return mesh.purpose


# --- Anzeige ------------------------------------------------------------------------------

def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def info(mesh, now=None):
    now = now or utcnow()
    seen = _aware(mesh.last_seen)
    return {"mesh": mesh, "seen": seen, "offline": seen is None or now - seen > OFFLINE_AFTER,
            "purpose_label": PURPOSES.get(mesh.purpose)}


def overview(now=None):
    """{"coop": [Info], "climate": [(Info, ClimateSensor)], "new": [Info], "ignored": [Info]}."""
    now = now or utcnow()
    sensors = {s.node: s for s in ClimateSensor.query}
    result = {"coop": [], "climate": [], "new": [], "ignored": []}
    since = now - timedelta(days=NEW_DAYS)
    for mesh in MeshNode.query.order_by(MeshNode.name, MeshNode.node):
        entry = info(mesh, now)
        if mesh.purpose in SINGLE:
            result["coop"].append(entry)
        elif mesh.purpose == "climate":
            result["climate"].append((entry, sensors.get(mesh.node)))
        elif mesh.purpose == "ignore":
            result["ignored"].append(entry)
        elif mesh.last_seen is None or _aware(mesh.last_seen) >= since:
            result["new"].append(entry)
    result["coop"].sort(key=lambda e: e["mesh"].purpose)
    result["climate"].sort(key=lambda p: ((p[1].warehouse.name if p[1] and p[1].warehouse else ""),
                                          (p[1].row or 0) if p[1] else 0))
    return result


def any_in_use():
    return db.session.query(MeshNode.id).filter(MeshNode.purpose.in_(("coop", "coop_top", "climate"))).first() \
        is not None


def new_count():
    since = utcnow() - timedelta(days=NEW_DAYS)
    return MeshNode.query.filter(MeshNode.purpose.is_(None),
                                 db.or_(MeshNode.last_seen.is_(None), MeshNode.last_seen >= since)).count()
