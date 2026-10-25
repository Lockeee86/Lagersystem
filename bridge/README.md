# Hühnerstall-Sensor über Meshtastic

Temperatur, Luftfeuchte, Akku und Klappe (auf/zu) vom Hühnerstall in die App – ohne WLAN am Stall.

```
Stall:  Meshtastic-Knoten + Sensor (SHT31/SHT4x/BME280) + Reedkontakt an der Klappe
          │  LoRa (30 m sind kein Problem)
Haus:   Meshtastic-Knoten mit WLAN (ESP32, z. B. Heltec V3)
          │  MQTT
Server: Mosquitto ──► coop-bridge ──► Lagersystem  POST /api/huehner/sensor
```

## 1. Verkabeln (Knoten am Stall)

| Teil | Anschluss |
|------|-----------|
| Temperatur/Feuchte: **SHT31, SHT4x oder BME280** (I²C) | VCC → 3,3 V, GND → GND, SDA/SCL → I²C-Pins des Boards (siehe Pinout; Meshtastic erkennt den Sensor beim Start selbst) |
| **Reedkontakt** (Schließer, Magnet an der Klappe) | ein Draht → freier GPIO, der andere → GND |

- Den Reedkontakt so anbringen, dass er **bei geschlossener Klappe geschlossen** ist (Magnet davor).
  Mit Pull-up gilt dann: Klappe zu = LOW, Klappe offen = HIGH (= „aktiv“).
- Sensor innen im Stall, nicht direkt an der Wärmelampe oder in der Sonne. Gehäuse mit Luftschlitzen.
- Strom: USB-Netzteil, wenn vorhanden – sonst ein 5–6-W-Solarpanel mit Laderegler und Akku (18650).

## 2. Meshtastic einstellen

Am besten mit der Meshtastic-App (Handy) oder `meshtastic --set …` per USB.

**Beide Knoten:** einen **eigenen, privaten Kanal** mit eigenem Schlüssel (PSK) anlegen – auf dem öffentlichen
„LongFast“ könnte jeder in der Nähe die Werte mitlesen. Region `EU_868`.

**Knoten am Stall**

| Einstellung | Wert |
|-------------|------|
| Telemetrie → Umweltwerte (Environment metrics) | **an**, Intervall z. B. 600 s |
| Telemetrie → Gerätewerte (Device metrics) | Intervall z. B. 900 s (Akku/Spannung) |
| Detection Sensor → aktiviert | **an** |
| Detection Sensor → Monitor-Pin | der GPIO vom Reedkontakt |
| Detection Sensor → Pull-up | **an** |
| Detection Sensor → Auslöser (trigger type) | **„beide Flanken, aktiv HIGH“** (EITHER_EDGE_ACTIVE_HIGH) – bei älterer Firmware „HIGH“ |
| Detection Sensor → Name | **Klappe** |
| Detection Sensor → Mindestabstand | 30 s |
| Detection Sensor → Status alle | 900 s (schickt regelmäßig „Klappe state: 0/1“ – so stimmt der Zustand auch nach einem verlorenen Paket) |

**Knoten im Haus (Gateway, ESP32 mit WLAN)**

| Einstellung | Wert |
|-------------|------|
| Netzwerk → WLAN | an, SSID + Passwort (Bluetooth geht am ESP32 dann aus – Einstellungen per WLAN/USB) |
| MQTT → aktiviert | **an** |
| MQTT → Server | IP des Servers mit dem Stack (Portainer), Port 1883 |
| MQTT → Benutzer/Passwort | leer |
| MQTT → JSON | egal – die Brücke versteht beide Formate (den Schalter gibt es nicht bei jedem Gerät) |
| MQTT → **Verschlüsselung** | **aus** (bleibt im Hof-Netz) – oder an lassen und im Stack `COOP_CHANNEL_PSK` setzen |
| Kanal (der private) → **Uplink** | **an** |

Den Stall-Knoten in die Nähe des Gateways halten und prüfen, ob er in der Knotenliste auftaucht. Seine ID
(z. B. `!a1b2c3d4`) steht dort – die braucht ihr für `COOP_NODE`.

## 3. Stack in Portainer ergänzen

1. In der App: **Hühner → Stall-Sensor → „Schlüssel für die Brücke erzeugen“** (nur Admin). Der Schlüssel wird
   genau einmal angezeigt.
2. Im Portainer-Stack bei den Umgebungsvariablen ergänzen:

| Variable | Wert |
|----------|------|
| `COMPOSE_PROFILES` | `huehner` (startet Mosquitto und die Brücke mit) |
| `COOP_API_TOKEN` | der Schlüssel aus Schritt 1 |
| `COOP_NODE` | ID des Stall-Knotens, z. B. `!a1b2c3d4` (leer = alle Knoten) |
| `MQTT_PORT` | wahlweise, Standard 1883 |
| `COOP_DOOR_ACTIVE` | `open` (Standard: „aktiv“ = Klappe offen) – zeigt die App es verkehrt herum, `closed` |
| `COOP_TOP_NODE` | wahlweise: ID des zweiten Knotens mit dem Kontakt oben (siehe unten) |
| `COOP_TOP_ACTIVE` | `away` (Standard: „aktiv“ = nicht oben) – verkehrt herum: `top` |
| `COOP_CHANNEL_PSK` | nur wenn die MQTT-Verschlüsselung am Gateway an bleibt: Kanal-Schlüssel (Base64) |
| `COOP_DEBUG` | `1`, um alle MQTT-Nachrichten im Log der Brücke zu sehen |

3. Stack neu bereitstellen (mit „Re-pull/Re-build“). Danach laufen zusätzlich `lagersystem-mqtt` und
   `lagersystem-coop-bridge`.
4. Port 1883 nur im Hof-Netz erreichbar lassen (nicht im Router freigeben) – Mosquitto ist ohne Passwort.

## Wahlweise: zweiter Knoten für „ganz offen“

Mit nur dem Kontakt unten kennt die App „zu“ und „nicht zu“. Soll sie auch sehen, ob die Klappe **ganz oben**
angekommen ist (Motor hängt, Klappe klemmt), kommt ein zweiter Meshtastic-Knoten mit eigenem Reedkontakt dazu:

- Kontakt **seitlich am Rahmen** auf der Höhe, wo der Magnet bei offener Klappe steht – oben würde er die Klappe
  blockieren. Er ist geschlossen, wenn die Klappe ganz oben ist.
- Detection Sensor am zweiten Knoten wie unten: Pin, Pull-up an, beide Flanken aktiv HIGH, Name z. B.
  **Klappe oben**, Status alle 900 s. Telemetrie braucht er nicht.
- Im Stack `COOP_TOP_NODE` auf seine ID setzen.

Die App zeigt dann **zu / offen / hängt** und schickt „Hühnerklappe hängt!“, wenn die Klappe länger als 5 Minuten
weder ganz unten noch ganz oben ist.

## 4. Prüfen

- Log von `lagersystem-coop-bridge`: „MQTT verbunden“, danach „eingetragen: {…}“ bei jedem Messwert.
- Klappe öffnen → nach wenigen Sekunden „eingetragen: {'door': 'open' …}“, in der App „Klappe offen“.
- Kommt nichts an: `COOP_DEBUG=1` setzen und schauen, ob überhaupt Nachrichten unter `msh/…/e/…` bzw.
  `msh/…/json/…` ankommen (sonst am Gateway MQTT-Server und Uplink prüfen). Meldet die Brücke
  „Verschlüsseltes Paket“, am Gateway die MQTT-Verschlüsselung ausschalten oder den Kanal-Schlüssel (PSK, Base64
  aus den Kanal-Einstellungen) als `COOP_CHANNEL_PSK` eintragen. Steht im Text etwas anderes als „Klappe detected“ bzw.
  „Klappe state: 1“, den Namen im Detection-Sensor-Modul bzw. `COOP_DOOR_NAME` anpassen.

## Was die App damit macht

- Hühner-Übersicht und Startseiten-Karte: Klappe offen/zu (mit oberem Knoten auch „hängt“) seit …, Temperatur,
  Feuchte, Akku, Kurve der letzten 48 Stunden, Fahrzeiten der letzten 7 Tage (auf, ganz oben, zu).
- Nach Sonnenuntergang: Klappe laut Sensor zu → keine Erinnerung; offen → Push „Hühnerklappe ist noch offen!“.
- Eine Stunde nach Sonnenaufgang noch zu → Push „Hühnerklappe heute noch nicht aufgegangen“ (Batterie, Lichtsensor
  oder Motor der Klappe prüfen).
- Push bei Frost (≤ 0,5 °C) oder Hitze (≥ 30 °C) im Stall und wenn der Sensor sich 2 Stunden nicht meldet.

## Eigene Sensoren ohne Meshtastic

Ein ESP32 im WLAN kann auch direkt JSON an Mosquitto schicken, Topic `huehnerstall/<irgendwas>`:

```json
{"temperature": 7.4, "humidity": 81, "battery": 90, "door": "open"}
```

Alle Felder sind wahlweise. Oder ohne MQTT direkt an die App:

```sh
curl -X POST http://<server>:5050/api/huehner/sensor \
     -H "Authorization: Bearer <Schlüssel>" -H "Content-Type: application/json" \
     -d '{"temperature": 7.4, "humidity": 81, "door": "closed"}'
```
