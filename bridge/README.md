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
| Temperatur/Feuchte: **SHT31, SHT4x, BME280 oder BME680** (I²C; BME680 zusätzlich Luftqualität) | VCC → 3,3 V, GND → GND, SDA/SCL → I²C-Pins des Boards (siehe Pinout; Meshtastic erkennt den Sensor beim Start selbst) |
| **Reedkontakt** (Schließer, Magnet an der Klappe) | ein Draht → freier GPIO, der andere → GND |

- Den Reedkontakt so anbringen, dass er **bei geschlossener Klappe geschlossen** ist (Magnet davor).
  Mit Pull-up gilt dann: Klappe zu = LOW, Klappe offen = HIGH (= „aktiv“).
- Sensor innen im Stall, nicht direkt an der Wärmelampe oder in der Sonne. Gehäuse mit Luftschlitzen.
- Strom: USB-Netzteil, wenn vorhanden – sonst ein 5–6-W-Solarpanel mit Laderegler und Akku (18650).

## 2. Meshtastic einstellen

Am besten mit der Meshtastic-App (Handy) oder `meshtastic --set …` per USB.

**Beide Knoten:** einen **eigenen, privaten Kanal** mit eigenem Schlüssel (PSK) anlegen – auf dem öffentlichen
„LongFast“ könnte jeder in der Nähe die Werte mitlesen. Region `EU_868`. Einfacher: den Kanal einmal in der App
unter **Sensoren → Kanal** hinterlegen (Schlüssel leer = neuer wird erzeugt). Dann zeigt die App einen QR-Code –
in der Meshtastic-App mit dem neuen Knoten verbinden, Kanäle → QR-Code scannen, fertig (fürs Gateway gibt es einen
eigenen Code mit Uplink).

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
(z. B. `!a1b2c3d4`) steht dort – die tragt ihr später in der App ein (Schritt 3).

## 3. Stack in Portainer ergänzen

1. In der App: **Sensoren → Einrichtung → „Schlüssel für die Brücke erzeugen“** (nur Admin). Der Schlüssel wird
   genau einmal angezeigt.
2. Im Portainer-Stack bei den Umgebungsvariablen ergänzen:

| Variable | Wert |
|----------|------|
| `COMPOSE_PROFILES` | `huehner` (startet Mosquitto und die Brücke mit) |
| `COOP_API_TOKEN` | der Schlüssel aus Schritt 1 |
| `MQTT_PORT` | wahlweise, Standard 1883 |
| `COOP_CHANNEL_PSK` | nur wenn die MQTT-Verschlüsselung am Gateway an bleibt: Kanal-Schlüssel (Base64) |
| `COOP_DEBUG` | `1`, um alle MQTT-Nachrichten im Log der Brücke zu sehen |

3. Stack neu bereitstellen (mit „Re-pull/Re-build“). Danach laufen zusätzlich `lagersystem-mqtt` und
   `lagersystem-coop-bridge`.
4. In der App unter **Sensoren** erscheint jeder Knoten, sobald er etwas schickt. Beim Stall-Knoten
   „Zuordnen“ → **Hühnerstall – Klappe unten und Messwerte** wählen (den Knoten oben als **Kontakt oben**).
   Mehr gehört nicht in den Stack – Knoten, Zuordnung und „Klappe verkehrt herum“ stellt ihr in der App ein.
5. Port 1883 nur im Hof-Netz erreichbar lassen (nicht im Router freigeben) – Mosquitto ist ohne Passwort.

## Wahlweise: zweiter Knoten für „ganz offen“

Mit nur dem Kontakt unten kennt die App „zu“ und „nicht zu“. Soll sie auch sehen, ob die Klappe **ganz oben**
angekommen ist (Motor hängt, Klappe klemmt), kommt ein zweiter Meshtastic-Knoten mit eigenem Reedkontakt dazu:

- Kontakt **seitlich am Rahmen** auf der Höhe, wo der Magnet bei offener Klappe steht – oben würde er die Klappe
  blockieren. Er ist geschlossen, wenn die Klappe ganz oben ist.
- Detection Sensor am zweiten Knoten wie unten: Pin, Pull-up an, beide Flanken aktiv HIGH, Name z. B.
  **Klappe oben**, Status alle 900 s. Telemetrie braucht er nicht.
- In der App unter Sensoren als **Hühnerstall – Kontakt oben** zuordnen.

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
- Morgens um 10 Uhr noch zu (Uhrzeit unter Sensoren → Einrichtung einstellbar; im Winter frühestens eine Stunde nach
  Sonnenaufgang) → Push „Hühnerklappe heute noch nicht aufgegangen“ (Batterie, Lichtsensor
  oder Motor der Klappe prüfen).
- Push bei Frost (≤ 0,5 °C) oder Hitze (≥ 30 °C) im Stall und wenn der Sensor sich 2 Stunden nicht meldet.

## Lagerklima (Kartoffellager) mit SCD41

Dieselbe Brücke bringt auch Temperatur, Luftfeuchte und **CO₂** aus den Lagern in die App (Lager → Lagerklima).

1. **Sensor:** SCD41 (z. B. als Grove-Modul) an den I²C- bzw. Grove-Anschluss eines Meshtastic-Knotens
   (3,3 V, GND, SDA, SCL). Meshtastic erkennt ihn beim Start.
2. **Meshtastic (Lager-Knoten):** Modul-Konfiguration → Telemetrie → **Luftqualität** (Air quality) **an** und
   **Umweltwerte** an, Intervall z. B. 600 s. Gleicher Kanal wie Gateway und Stall, Uplink am Gateway an.
3. **Im Stack ist nichts zu tun.**
4. **In der App:** Der Knoten erscheint nach seinen ersten Werten unter **Sensoren → „Neue Knoten“**. Dort
   „Lagerklima“, das Lager und wahlweise die Reihe wählen. Oder vorher über „Knoten von Hand anlegen“ mit der
   Knoten-ID. Die Werte stehen dann unter Lager → Lagerklima, Grenzwerte je Lager ebenda (leer = 3–10 °C,
   85–97 %, CO₂ bis 5000 ppm). Taucht ein fremder Knoten auf (z. B. vom Nachbarn auf dem öffentlichen Kanal),
   ihn auf „Nicht verwenden“ stellen.

Hinweise:
- Strom: Der SCD41 misst laufend und braucht mehr Strom als ein BME280 – im Lager am besten ein USB-Netzteil.
- Kondenswasser: Bei 90–95 % Feuchte nicht direkt in den kalten Luftstrom der Kühlung hängen, Gehäuse mit
  Öffnungen nach unten.
- **CO₂-Kalibrierung:** Der SCD41 gleicht sich von sich aus an die niedrigsten Werte der letzten Tage an
  (er nimmt an, dass er ab und zu Frischluft mit ~420 ppm sieht). Im geschlossenen Lager mit dauerhaft hohem CO₂
  kann er dadurch zu niedrig messen. Ab und zu draußen an der frischen Luft laufen lassen, bzw. die
  Selbstkalibrierung abschalten, wenn die Firmware das anbietet.

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
