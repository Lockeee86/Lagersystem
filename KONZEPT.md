# Konzept: Lagersystem für die Kartoffelernte

Webbasiertes Lagersystem für Kartoffelkisten. Läuft als Docker-Stack in Portainer.
Später kommen Temperatur- und Luftfeuchtesensoren dazu.

## Entscheidungen

| # | Thema | Entscheidung |
|---|---|---|
| 1 | Kisten | Jede Kiste hat einen eigenen QR-Code mit fester ID (`K-0001` …). Der QR-Code enthält nur die ID, Etiketten müssen nie neu gedruckt werden. |
| 2 | Menge | Füllstand voll / ¾ / ½ / ¼ / leer. Gewicht = Füllstand × Standardgewicht des Kistentyps (einstellbar). |
| 3 | Nutzer | Mehrere Konten mit Rollen **Admin**, **Mitarbeiter**, **Betrachter**. Jede Buchung wird mit Benutzer und Zeitpunkt protokolliert. |
| 4 | Zugriff | Auch von unterwegs: Betrieb hinter Reverse Proxy, VPN oder Tunnel mit HTTPS. Oberfläche optimiert fürs Handy. |
| 5 | Lager | Mehrere Lager möglich, am Anfang eins. Struktur **Lager → Reihe → Stellplatz → Ebene** (Start: 10 × 15 × 5 = 750 Plätze). |
| 6 | Sensoren | Temperatur + Luftfeuchte per MQTT, Diagramme, Grenzwert-Warnungen, Warnung bei ausgefallenem Sensor. |
| 7 | Sprache | Deutsch und Polnisch, je Benutzer einstellbar. Der Admin legt die Sprache beim Anlegen fest, der Benutzer kann sie in den Einstellungen ändern. |
| 8 | Repo | `github.com/Lockeee86/Lagersystem`, Portainer baut den Stack direkt aus dem Repo. |

## Rollen

| Rolle | Darf |
|---|---|
| Admin | Alles: Lager, Sorten, Kistentypen, Benutzer, Kisten anlegen/löschen, Etiketten |
| Mitarbeiter | Scannen, befüllen, einlagern, umlagern, Füllstand ändern, auslagern, Notizen |
| Betrachter | Nur lesen: Lagerkarte, Bestände, Verlauf |

## Lageradressierung

```
Reihe – Stellplatz – Ebene
  C   –    07      –   3     →  "C-07-3"
```

- Reihen A, B, C … (ab 27 Reihen numerisch)
- Stellplatz 01 … n innerhalb der Reihe
- Ebene 1 (Boden) … max. Stapelhöhe

### Stapelregeln

- Eine Kiste wird immer **oben** auf einen Stapel gesetzt, also auf die nächste freie Ebene. Es gibt keine schwebenden Kisten.
- Maximale Stapelhöhe pro Lager (Start: 5).
- Wird eine Kiste aus der Mitte entnommen, zeigt das System, welche Kisten darüber liegen
  ("blockiert durch"). Nach der Entnahme rutschen die Kisten darüber eine Ebene nach unten.
  Das wird im Verlauf als "nachgerückt" protokolliert.

## Datenmodell

- **Lager**: Name, Anzahl Reihen, Stellplätze pro Reihe, max. Ebenen
- **Kistentyp**: Name, Gewicht bei voller Kiste (kg)
- **Sorte**: Name, Farbe (Lagerkarte), Verwendung (Speise / Futter / Pflanzgut / Sonstiges)
- **Schlag**: Name, Fläche (ha, optional), Notiz, aktiv. Wird beim Befüllen per Dropdown gewählt.
- **Kiste**: Code (QR), Kistentyp, Sorte, Erntedatum, Schlag, Sortierung/Größe,
  Füllstand, Notiz, aktueller Platz (Lager, Reihe, Platz, Ebene, leer = nicht eingelagert)
- **Bewegung**: Kiste, Aktion, von, nach, Füllstand vorher/nachher, Benutzer, Zeitpunkt, Notiz,
  Detail (sprachneutrale Systemangabe, wird bei der Anzeige übersetzt)
- **Benutzer**: Benutzername, Passwort-Hash, Rolle, Sprache, gewählte Kartenansicht, aktiv
- *(Phase 2)* **Sensor**: Name, MQTT-Topic, Lager, Reihe (optional)
- *(Phase 2)* **Messwert**: Sensor, Messgröße, Wert, Zeitpunkt. Das Schema ist allgemein gehalten, damit später z. B. CO₂ ergänzt werden kann.

## Technik

```
Portainer-Stack (docker-compose)
├── web    Flask + Gunicorn (Weboberfläche, Formulare, JSON-API)
├── db     PostgreSQL 16
└── mqtt   Mosquitto (ab Phase 2)
```

- Datenbank-Migrationen mit Alembic (Flask-Migrate), damit Updates die Daten erhalten
- Alle Frontend-Bibliotheken liegen lokal im Projekt (kein CDN): Bootstrap 5, Bootstrap Icons, html5-qrcode
- CSRF-Schutz, Login-Sperre nach Fehlversuchen, sichere Cookies hinter HTTPS

## Phasen

### Phase 1 – MVP
- Ersteinrichtung (erster Admin + erstes Lager)
- Login, Rollen, Benutzerverwaltung, Passwort ändern
- Lager, Sorten, Schläge und Kistentypen verwalten
- Kisten in Serie anlegen (K-0001 … K-0750), QR-Etiketten als PDF
- Lagerkarte mit drei Ansichten: Karte (Draufsicht), 3D (isometrisch, drehbar) und Liste (Reihen mit Zahlen/Sortenkürzeln), Stapelansicht
- Kisten-Detail: befüllen, einlagern, umlagern, Füllstand, auslagern
- Handy-Scan der QR-Codes mit der Kamera (benötigt HTTPS)
- Dashboard: Belegung, Bestand je Sorte (Kisten + geschätzte kg), letzte Bewegungen
- Verlauf aller Bewegungen

### Phase 2 – Klima
- Mosquitto-Container, Sensoren zuordnen (Lager / Reihe)
- Diagramme 24 h / 7 Tage / 30 Tage / Saison
- Grenzwerte pro Lager (Start: 4–8 °C, Warnung < 3 °C und > 10 °C; 90–95 % rF,
  Warnung < 85 % und > 97 %), Warnung bei Sensor-Ausfall
- Benachrichtigung (Telegram, ntfy oder E-Mail, noch offen)

### Zwischendurch umgesetzt
- Import aus einer Excel-Vorlage (Zettel → Lagersystem) mit Vorschau, alles-oder-nichts
- Export je Kartoffeljahr (= Erntejahr) als Excel: Auswertung je Sorte/Schlag, Kisten, Bewegungen
- Momentaufnahme (Sorte, Schlag, Erntedatum, Füllstand) bei jeder Bewegung, damit frühere Jahre
  auswertbar bleiben; benutzte Sorten/Schläge werden deshalb nur deaktiviert, nie gelöscht

### Phase 3 – Komfort
- Entnahme-Vorschläge ("200 kg Belana": älteste und oben liegende Kisten zuerst)
- Saisonvergleich direkt in der App (Diagramme über mehrere Kartoffeljahre)
- Offline-Modus für den Scan (falls im Lager kein Netz)

## Noch offen

1. WLAN oder Handyempfang im Lager? Davon hängt ab, ob ein Offline-Modus nötig ist.
2. Läuft in Portainer bereits ein Reverse Proxy oder VPN?
3. Benachrichtigungsweg für Warnungen (Phase 2)
4. Mehrere Kistengrößen? Das System unterstützt beliebig viele Kistentypen.
