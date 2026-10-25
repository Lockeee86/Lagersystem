"""Brücke Hühnerstall: Meshtastic (MQTT, JSON) → Lagersystem (`POST /api/huehner/sensor`).

Der Meshtastic-Knoten im Haus (mit WLAN) schickt alle Pakete an den MQTT-Server (Mosquitto im selben Stack).
Diese Brücke liest sie, nimmt vom Stall-Knoten
  - Telemetrie: temperature, relative_humidity, battery_level, voltage (+ rssi/snr)
  - Klappe vom Modul „Detection Sensor“: Text „<Name> detected“ bzw. „<Name> state: 0/1“
und trägt sie per HTTP in die App ein. Eigene ESP32-Sensoren können unter `COOP_TOPIC` direkt fertiges JSON
({"temperature": …, "humidity": …, "door": "open"|"closed"}) veröffentlichen.

Einstellungen über Umgebungsvariablen (siehe README.md in diesem Ordner).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import deque

APP_URL = os.environ.get("APP_URL", "http://web:5000/api/huehner/sensor")
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
                   "battery_level": "battery", "voltage": "voltage"}
        values = {target: payload[source] for source, target in mapping.items() if payload.get(source) is not None}
        if values.get("battery", 0) > 100:            # 101 = am Netz
            values.pop("battery")
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


class Sender:
    """Schickt an die App; bei Fehlern kurz zwischenspeichern und später nachreichen."""

    def __init__(self, url=APP_URL, token=TOKEN):
        self.url, self.token = url, token
        self.pending = deque(maxlen=200)

    def _post(self, data):
        request = urllib.request.Request(self.url, data=json.dumps(data).encode(), method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status

    def send(self, data):
        self.pending.append(data)
        while self.pending:
            item = self.pending[0]
            try:
                self._post(item)
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
    seen = deque(maxlen=500)                           # dieselbe Nachricht über mehrere Knoten nur einmal

    def on_connect(client, userdata, flags, reason_code, properties=None):
        log("MQTT verbunden:", reason_code, "– höre auf", MQTT_TOPIC, "und", COOP_TOPIC)
        client.subscribe(MQTT_TOPIC)
        if COOP_TOPIC:
            client.subscribe(COOP_TOPIC)

    def on_message(client, userdata, msg):
        try:
            message = json.loads(msg.payload.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return                                     # Protobuf („…/e/…“) o. Ä. – nur JSON auswerten
        if not isinstance(message, dict):
            return
        if COOP_TOPIC and mqtt.topic_matches_sub(COOP_TOPIC, msg.topic):
            data = message
        else:
            if "/json/" not in msg.topic:
                return
            key = (message.get("from"), message.get("id"))
            if key[1] is not None and key in seen:
                return
            seen.append(key)
            if DEBUG:
                log("MQTT", msg.topic, message)
            data = parse_meshtastic(message)
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
