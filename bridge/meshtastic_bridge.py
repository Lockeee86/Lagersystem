"""Brücke Hühnerstall: Meshtastic (MQTT, Protobuf oder JSON) → Lagersystem (`POST /api/huehner/sensor`).

Der Meshtastic-Knoten im Haus (mit WLAN) schickt alle Pakete an den MQTT-Server (Mosquitto im selben Stack).
Diese Brücke liest sie, nimmt vom Stall-Knoten
  - Telemetrie: temperature, relative_humidity, battery_level, voltage (+ rssi/snr)
  - Klappe vom Modul „Detection Sensor“: Text „<Name> detected“ bzw. „<Name> state: 0/1“
und trägt sie per HTTP in die App ein. Eigene ESP32-Sensoren können unter `COOP_TOPIC` direkt fertiges JSON
({"temperature": …, "humidity": …, "door": "open"|"closed"}) veröffentlichen.

Einstellungen über Umgebungsvariablen (siehe README.md in diesem Ordner).
"""
import base64
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
from collections import deque

APP_URL = os.environ.get("APP_URL", "http://web:5000/api/huehner/sensor")
CLIMATE_URL = os.environ.get("CLIMATE_URL", "http://web:5000/api/lager/klima")
TOKEN = os.environ.get("COOP_API_TOKEN", "")
MQTT_HOST = os.environ.get("MQTT_HOST", "mqtt")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "msh/#")
COOP_TOPIC = os.environ.get("COOP_TOPIC", "huehnerstall/#")
COOP_NODE = (os.environ.get("COOP_NODE") or "").strip().lower()        # z. B. !a1b2c3d4 – leer: alle Knoten
DOOR_NAME = (os.environ.get("DOOR_NAME") or "Klappe").strip().lower()   # Name im Detection-Sensor-Modul
DOOR_ACTIVE = (os.environ.get("DOOR_ACTIVE") or "open").strip().lower()  # „aktiv“ (detected / state: 1) heißt …
# Wahlweise zweiter Knoten mit Reedkontakt oben (Klappe ganz offen); „aktiv“ heißt dort „nicht oben“
# (Kontakt bei offener Klappe geschlossen = LOW, wie unten bei geschlossener Klappe).
COOP_TOP_NODE = (os.environ.get("COOP_TOP_NODE") or "").strip().lower()
TOP_ACTIVE = (os.environ.get("TOP_ACTIVE") or "away").strip().lower()   # away | top
# Lagerklima: diese Knoten gehen an CLIMATE_URL. Leer: alle Knoten außer dem Hühnerstall (wenn COOP_NODE gesetzt ist)
CLIMATE_NODES = {n.strip().lower() for n in (os.environ.get("CLIMATE_NODES") or "").split(",") if n.strip()}
CHANNEL_PSK = (os.environ.get("CHANNEL_PSK") or "").strip()            # Base64 aus den Kanal-Einstellungen
DEBUG = os.environ.get("DEBUG", "") not in ("", "0", "false")

STATE_RE = re.compile(r"state:\s*([01])\b", re.I)
DETECTED_RE = re.compile(r"\bdetected\b", re.I)


def log(*args):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *args, flush=True)


def node_id(value):
    """Meshtastic `from` (Zahl) → „!a1b2c3d4“."""
    if isinstance(value, int):
        return f"!{value & 0xFFFFFFFF:08x}"
    return str(value or "").lower() or None


def _door_from_text(text, door_name=DOOR_NAME, active=DOOR_ACTIVE):
    """Text des Detection-Sensor-Moduls → True (offen) / False (zu) / None (nicht die Klappe)."""
    lower = (text or "").lower()
    if door_name and door_name not in lower:
        return None
    match = STATE_RE.search(lower)
    if match:
        is_active = match.group(1) == "1"
    elif DETECTED_RE.search(lower):
        is_active = True
    else:
        return None
    return is_active if active == "open" else not is_active


def parse_meshtastic(message, wanted_node=COOP_NODE, door_name=DOOR_NAME, active=DOOR_ACTIVE,
                     top_node=COOP_TOP_NODE, top_active=TOP_ACTIVE):
    """Eine Meshtastic-JSON-Nachricht (MQTT „…/json/…“) → Daten für die App oder None."""
    node = node_id(message.get("from"))
    is_top = bool(top_node) and node == top_node
    if wanted_node and node != wanted_node and not is_top:
        return None
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None
    data = {"node": node}
    if message.get("timestamp"):
        data["at"] = message["timestamp"]
    for key in ("rssi", "snr"):
        if message.get(key) not in (None, 0):
            data[key] = message[key]
    kind = message.get("type")
    if kind == "telemetry":
        if is_top:                                     # Messwerte nur vom Stall-Knoten, nicht vom oberen
            return None
        mapping = {"temperature": "temperature", "relative_humidity": "humidity",
                   "battery_level": "battery", "voltage": "voltage", "barometric_pressure": "pressure",
                   "gas_resistance": "gas", "iaq": "iaq"}
        values = {target: payload[source] for source, target in mapping.items() if payload.get(source) is not None}
        if values.get("battery", 0) > 100:            # 101 = am Netz
            values.pop("battery")
        for key, low in (("pressure", 300), ("gas", 0), ("iaq", 0)):   # 0 = vom Sensor nicht geliefert
            if key in values and not values[key] > low:
                values.pop(key)
        if not values:
            return None
        data.update(values)
        return data
    if "text" in payload:                              # Textnachricht bzw. Detection-Sensor-Modul
        if is_top:                                     # oberer Kontakt: „open“ = Klappe ganz oben
            at_top = _door_from_text(payload.get("text"), door_name, "closed" if top_active == "away" else "open")
            if at_top is None:
                return None
            data["door_top"] = "open" if at_top else "closed"
            return data
        door = _door_from_text(payload.get("text"), door_name, active)
        if door is None:
            return None
        data["door"] = "open" if door else "closed"
        return data
    return None


def route(message, coop_node=COOP_NODE, top_node=COOP_TOP_NODE, climate_nodes=CLIMATE_NODES):
    """Wohin gehört die Nachricht? „coop“ (Hühnerstall), „climate“ (Lager) oder None."""
    node = node_id(message.get("from"))
    if node in climate_nodes:
        return "climate"
    if (coop_node and node == coop_node) or (top_node and node == top_node):
        return "coop"
    if not coop_node:
        return "coop"                                  # ohne COOP_NODE wie bisher: alles für den Stall
    return "climate" if not climate_nodes else None


def parse_climate(message):
    """Telemetrie eines Lager-Knotens (SCD41: CO₂, Temperatur, Feuchte; dazu Akku) → Daten für die App oder None."""
    payload = message.get("payload")
    if message.get("type") != "telemetry" or not isinstance(payload, dict):
        return None
    data = {"node": node_id(message.get("from"))}
    if message.get("timestamp"):
        data["at"] = message["timestamp"]
    temperature = payload.get("temperature", payload.get("co2_temperature"))
    humidity = payload.get("relative_humidity", payload.get("co2_humidity"))
    values = {"temperature": temperature, "humidity": humidity, "co2": payload.get("co2"),
              "battery": payload.get("battery_level"), "voltage": payload.get("voltage")}
    if values["battery"] is not None and values["battery"] > 100:     # 101 = am Netz
        values["battery"] = None
    if not values["co2"]:                              # 0 = nicht geliefert (SCD41 braucht kurz nach dem Start)
        values["co2"] = None
    values = {k: v for k, v in values.items() if v is not None}
    if not values:
        return None
    data.update(values)
    return data


# --- Meshtastic-Protobuf ohne JSON-Schalter -------------------------------------------------
# Das Gateway schickt ohne „JSON“ ein ServiceEnvelope (Topic msh/…/e/…). Die wenigen Felder, die wir brauchen,
# lesen wir selbst (kein großes meshtastic-Paket nötig). Feldnummern laut meshtastic/protobufs.
PORT_TEXT, PORT_DETECTION, PORT_TELEMETRY = 1, 10, 67
DEFAULT_KEY = bytes([0xD4, 0xF1, 0xBB, 0x3A, 0x20, 0x29, 0x07, 0x59, 0xF0, 0xBC, 0xFF, 0xAB, 0xCF, 0x4E, 0x69, 0x01])


def _fields(data):
    """Protobuf → {Feldnummer: [Werte]} (varint → int, fixed32 → 4 Bytes, fixed64 → 8 Bytes, Länge → Bytes)."""
    fields, i = {}, 0
    while i < len(data):
        key, i = _varint(data, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(data, i)
        elif wire == 1:
            value, i = data[i:i + 8], i + 8
        elif wire == 2:
            length, i = _varint(data, i)
            value, i = data[i:i + length], i + length
        elif wire == 5:
            value, i = data[i:i + 4], i + 4
        else:
            raise ValueError("unbekannter Protobuf-Typ")
        fields.setdefault(number, []).append(value)
    return fields


def _varint(data, i):
    result = shift = 0
    while True:
        if i >= len(data):
            raise ValueError("Protobuf zu kurz")
        byte = data[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7


def _first(fields, number, default=None):
    return fields.get(number, [default])[0]


def _f32(raw):
    return struct.unpack("<f", raw)[0] if isinstance(raw, bytes) and len(raw) == 4 else None


def _u32(raw):
    return struct.unpack("<I", raw)[0] if isinstance(raw, bytes) and len(raw) == 4 else raw


def channel_key(psk=CHANNEL_PSK):
    """PSK aus den Kanal-Einstellungen (Base64) → AES-Schlüssel; „AQ==“ = Standardschlüssel."""
    if not psk:
        return None
    key = base64.b64decode(psk)
    if len(key) == 1:                                   # Kurzform: Standardschlüssel, letztes Byte verschoben
        return DEFAULT_KEY[:-1] + bytes([(DEFAULT_KEY[-1] + key[0] - 1) & 0xFF]) if key[0] else None
    return key


def _decrypt(encrypted, packet_id, sender, key):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    nonce = struct.pack("<Q", packet_id) + struct.pack("<I", sender) + bytes(4)
    decryptor = Cipher(algorithms.AES(key), modes.CTR(nonce)).decryptor()
    return decryptor.update(encrypted) + decryptor.finalize()


def _round(value, digits=2):
    return round(value, digits) if value is not None else None


def envelope_to_message(raw, key=None):
    """ServiceEnvelope (Bytes) → dieselbe Form wie Meshtastic-JSON ({from, id, type, payload, …}) oder None."""
    packet = _fields(_first(_fields(raw), 1, b""))
    sender, packet_id = _u32(_first(packet, 1)), _u32(_first(packet, 6, 0))
    if sender is None:
        return None
    decoded = _first(packet, 4)
    if decoded is None and _first(packet, 5) is not None:
        if key is None:
            return {"from": sender, "id": packet_id, "encrypted": True}
        decoded = _decrypt(_first(packet, 5), packet_id, sender, key)
    if decoded is None:
        return None
    try:
        data = _fields(decoded)
    except ValueError:
        return {"from": sender, "id": packet_id, "encrypted": True}   # falscher Schlüssel
    port, payload = _first(data, 1), _first(data, 2, b"")
    message = {"from": sender, "id": packet_id, "timestamp": _u32(_first(packet, 7)),
               "snr": _round(_f32(_first(packet, 8)))}
    rssi = _first(packet, 12)
    if isinstance(rssi, int):
        message["rssi"] = rssi - (1 << 64) if rssi >= 1 << 63 else rssi   # int32 als varint (negativ)
    if port in (PORT_TEXT, PORT_DETECTION):
        message.update(type="text", payload={"text": payload.decode("utf-8", errors="replace")})
        return message
    if port == PORT_TELEMETRY:
        telemetry = _fields(payload)
        values = {}
        if 2 in telemetry:                              # DeviceMetrics: 1 battery_level, 2 voltage
            device = _fields(telemetry[2][0])
            values["battery_level"] = _first(device, 1)
            values["voltage"] = _round(_f32(_first(device, 2)))
        if 3 in telemetry:                              # EnvironmentMetrics: 1 °C, 2 %, 3 hPa, 4 MOhm, 7 IAQ
            env = _fields(telemetry[3][0])
            values["temperature"] = _round(_f32(_first(env, 1)))
            values["relative_humidity"] = _round(_f32(_first(env, 2)), 1)
            values["barometric_pressure"] = _round(_f32(_first(env, 3)), 1)
            values["gas_resistance"] = _round(_f32(_first(env, 4)), 4)
            values["iaq"] = _first(env, 7)
        if 4 in telemetry:                              # AirQualityMetrics: 13 co2 (ppm), 14 °C, 15 % (SCD4x)
            air = _fields(telemetry[4][0])
            values["co2"] = _first(air, 13)
            values["co2_temperature"] = _round(_f32(_first(air, 14)))
            values["co2_humidity"] = _round(_f32(_first(air, 15)), 1)
        message.update(type="telemetry", payload={k: v for k, v in values.items() if v is not None})
        return message
    return None


class Sender:
    """Schickt an die App; bei Fehlern kurz zwischenspeichern und später nachreichen."""

    def __init__(self, url=APP_URL, token=TOKEN):
        self.url, self.token = url, token
        self.pending = deque(maxlen=200)

    def _post(self, data, url=None):
        request = urllib.request.Request(url or self.url, data=json.dumps(data).encode(), method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status

    def send(self, data, url=None):
        self.pending.append((url, data))
        while self.pending:
            target, item = self.pending[0]
            try:
                self._post(item, target)
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")[:200]
                log("App lehnt ab:", e.code, body, "–", item)
                if e.code in (400, 422):
                    self.pending.popleft()             # Werte ungültig → verwerfen, nicht ewig wiederholen
                    continue
                return False                           # 401 (Schlüssel) oder Serverfehler → später erneut
            except (urllib.error.URLError, OSError) as e:
                log("App nicht erreichbar:", e)
                return False
            self.pending.popleft()
            log("eingetragen:", item)
        return True


def main():
    if not TOKEN:
        log("COOP_API_TOKEN fehlt – in der App unter Hühner → Stall-Sensor einen Schlüssel erzeugen.")
        sys.exit(1)
    import paho.mqtt.client as mqtt

    sender = Sender()
    seen = deque(maxlen=500)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        log("MQTT verbunden:", reason_code, "– höre auf", MQTT_TOPIC, "und", COOP_TOPIC)
        client.subscribe(MQTT_TOPIC)
        if COOP_TOPIC:
            client.subscribe(COOP_TOPIC)

    key = channel_key()
    warned = set()

    def on_message(client, userdata, msg):
        if "/e/" in msg.topic or "/c/" in msg.topic:   # Protobuf (ohne JSON-Schalter am Gateway)
            try:
                message = envelope_to_message(msg.payload, key)
            except (ValueError, IndexError, struct.error):
                return
            if not message:
                return
            if message.get("encrypted"):
                if message["from"] not in warned:
                    warned.add(message["from"])
                    log("Verschlüsseltes Paket von", node_id(message["from"]), "– am Gateway unter MQTT die",
                        "Verschlüsselung ausschalten oder COOP_CHANNEL_PSK (Kanal-Schlüssel, Base64) eintragen.")
                return
        else:
            try:
                message = json.loads(msg.payload.decode())
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            if not isinstance(message, dict):
                return
            if COOP_TOPIC and mqtt.topic_matches_sub(COOP_TOPIC, msg.topic):
                sender.send(message)
                return
            if "/json/" not in msg.topic:
                return
        packet = (message.get("from"), message.get("id"))
        if packet[1] and packet in seen:               # dieselbe Nachricht über mehrere Gateways nur einmal
            return
        seen.append(packet)
        if DEBUG:
            log("MQTT", msg.topic, message)
        target = route(message)
        if target == "climate":
            data = parse_climate(message)
            if data:
                sender.send(data, CLIMATE_URL)
            return
        data = parse_meshtastic(message) if target == "coop" else None
        if data:
            sender.send(data)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="lagersystem-huehnerstall")
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except OSError as e:
            log("MQTT nicht erreichbar:", e, "– neuer Versuch in 10 s")
            time.sleep(10)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
