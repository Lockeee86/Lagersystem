"""Brücke Meshtastic (MQTT, Protobuf oder JSON) → Lagersystem (`POST /api/meshtastic`).

Der Meshtastic-Knoten im Haus (mit WLAN) schickt alle Pakete an den MQTT-Server (Mosquitto im selben Stack).
Diese Brücke liest sie und reicht von jedem Knoten
  - Telemetrie: Temperatur, Feuchte, Luftdruck, Gas/IAQ, CO₂, Akku, Spannung (+ rssi/snr)
  - Texte vom Modul „Detection Sensor“ („<Name> detected“ bzw. „<Name> state: 0/1“)
an die App weiter. Wofür ein Knoten da ist (Hühnerstall, Lager …), legt der Admin in der App fest (Rubrik Sensoren). Eigene ESP32-Sensoren können unter `COOP_TOPIC` direkt fertiges JSON
({"temperature": …, "humidity": …, "door": "open"|"closed"}) veröffentlichen.

Einstellungen über Umgebungsvariablen (siehe README.md in diesem Ordner).
"""
import base64
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from collections import deque

MESH_URL = os.environ.get("MESH_URL", "http://web:5000/api/meshtastic")       # alle Meshtastic-Pakete
APP_URL = os.environ.get("APP_URL", "http://web:5000/api/huehner/sensor")      # fertiges JSON unter COOP_TOPIC
TOKEN = os.environ.get("COOP_API_TOKEN", "")
MQTT_HOST = os.environ.get("MQTT_HOST", "mqtt")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "msh/#")
COOP_TOPIC = os.environ.get("COOP_TOPIC", "huehnerstall/#")
# Wofür ein Knoten da ist, steht in der App (Rubrik Sensoren). COOP_NODE/COOP_TOP_NODE aus älteren Stacks ordnen
# einen neuen Knoten nur einmal vor.
COOP_NODE = (os.environ.get("COOP_NODE") or "").strip().lower()        # z. B. !a1b2c3d4
DOOR_NAME = (os.environ.get("DOOR_NAME") or "Klappe").strip().lower()   # Name im Detection-Sensor-Modul
DOOR_ACTIVE = (os.environ.get("DOOR_ACTIVE") or "open").strip().lower()  # „aktiv“ (detected / state: 1) heißt …
# Wahlweise zweiter Knoten mit Reedkontakt oben (Klappe ganz offen); „aktiv“ heißt dort „nicht oben“
# (Kontakt bei offener Klappe geschlossen = LOW, wie unten bei geschlossener Klappe).
COOP_TOP_NODE = (os.environ.get("COOP_TOP_NODE") or "").strip().lower()
TOP_ACTIVE = (os.environ.get("TOP_ACTIVE") or "away").strip().lower()   # away | top
HINTS = {node: hint for node, hint in ((COOP_NODE, "coop"), (COOP_TOP_NODE, "coop_top")) if node}
CHANNEL_PSK = (os.environ.get("CHANNEL_PSK") or "").strip()            # Base64 aus den Kanal-Einstellungen
DEBUG = os.environ.get("DEBUG", "") not in ("", "0", "false")

def log(*args):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *args, flush=True)


def node_id(value):
    """Meshtastic `from` (Zahl) → „!a1b2c3d4“."""
    if isinstance(value, int):
        return f"!{value & 0xFFFFFFFF:08x}"
    return str(value or "").lower() or None


def telemetry_values(payload):
    """Telemetrie (Meshtastic-Namen) → Werte für die App; 0 = vom Sensor nicht geliefert, Akku 101 = am Netz."""
    values = {
        "temperature": payload.get("temperature", payload.get("co2_temperature")),
        "humidity": payload.get("relative_humidity", payload.get("co2_humidity")),
        "battery": payload.get("battery_level"), "voltage": payload.get("voltage"),
        "pressure": payload.get("barometric_pressure"), "gas": payload.get("gas_resistance"),
        "iaq": payload.get("iaq"), "co2": payload.get("co2"),
    }
    if values["battery"] is not None and values["battery"] > 100:
        values["battery"] = None
    for key, low in (("pressure", 300), ("gas", 0), ("iaq", 0), ("co2", 0)):
        if values[key] is not None and not values[key] > low:
            values[key] = None
    return {k: v for k, v in values.items() if v is not None}


def packet(message, door_name=DOOR_NAME, hints=None):
    """Eine Meshtastic-Nachricht (JSON-Form) → Daten für `POST /api/meshtastic` oder None.
    Wohin die Werte gehören (Stall, Lager …), entscheidet die App anhand des Knotens."""
    node = node_id(message.get("from"))
    payload = message.get("payload")
    if not node or not isinstance(payload, dict):
        return None
    data = {"node": node}
    if message.get("timestamp"):
        data["at"] = message["timestamp"]
    for key in ("rssi", "snr"):
        if message.get(key) not in (None, 0):
            data[key] = message[key]
    if message.get("type") == "telemetry":
        values = telemetry_values(payload)
        if not values:
            return None
        data["values"] = values
    elif "text" in payload:                            # Detection-Sensor-Modul („Klappe state: 1“)
        text = str(payload.get("text") or "")
        if door_name and door_name not in text.lower():
            return None                                # andere Textnachrichten gehen niemanden etwas an
        data["text"] = text[:200]
    else:
        return None
    hint = (HINTS if hints is None else hints).get(node)
    if hint:
        data["hint"] = hint                            # Zuordnung aus dem Stack – nur für neue Knoten
    data["door_active"], data["top_active"] = DOOR_ACTIVE, TOP_ACTIVE
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


def handle(topic, payload, sender, seen, warned, key=None, coop_topic=False):
    """Eine MQTT-Nachricht verarbeiten und an die App schicken (aus `main` – eigene Funktion, damit testbar)."""
    if "/e/" in topic or "/c/" in topic:               # Protobuf (ohne JSON-Schalter am Gateway)
        try:
            message = envelope_to_message(payload, key)
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
            message = json.loads(payload.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(message, dict):
            return
        if coop_topic:
            sender.send(message)
            return
        if "/json/" not in topic:
            return
    seen_key = (message.get("from"), message.get("id"))
    if seen_key[1] and seen_key in seen:               # dieselbe Nachricht über mehrere Gateways nur einmal
        return
    seen.append(seen_key)
    if DEBUG:
        log("MQTT", topic, message)
    data = packet(message)
    if data:
        sender.send(data, MESH_URL)


def main():
    if not TOKEN:
        log("COOP_API_TOKEN fehlt – in der App unter Sensoren → Einrichtung einen Schlüssel erzeugen.")
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
        handle(msg.topic, msg.payload, sender, seen, warned, key,
               coop_topic=bool(COOP_TOPIC) and mqtt.topic_matches_sub(COOP_TOPIC, msg.topic))

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
