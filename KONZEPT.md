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
- **Saatgut-Stufe**: Kürzel (PB, S, SE, E, BE, Z …), Beschreibung, Reihenfolge, aktiv
- **Kiste**: Code (QR), Kistentyp, Sorte, Verwendung, Stufe, Erntedatum, Schlag, Sortierung/Größe,
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
- Warnungen **nur in der App** (rote Anzeige auf der Startseite und in der Navigation), kein Push/Mail
- 3–6 Messpunkte, Sensoren noch nicht vorhanden → günstige WLAN-Sensoren mit MQTT vorschlagen (WLAN im Lager ist gut)

### Bestand von der Lagertafel übernommen
- Eine Partien-/Tafel-Arbeitsweise wurde ausprobiert und wieder verworfen: Es bleibt bei **Einzelkisten**.
- Kisten haben zusätzlich **Verwendung** (Saat / Speise, leer = wie bei der Sorte) und **Saatgut-Stufe**
  (verwaltbar, Start: PB, S, SE, E, BE, Z). Reihen pro Lager wahlweise mit Zahlen beschriftet.
- Der Bestand der Kreidetafel wurde in eine Kistenliste umgerechnet (K-0001 fortlaufend ab Reihe 1,
  Blöcke links → rechts, je Platz max. 5 Kisten, jeder Block auf einem neuen Platz, angebrochene
  Kiste oben) und kann über den Kisten-Import übernommen werden.

### Zwischendurch umgesetzt
- Import aus einer Excel-Vorlage (Zettel → Lagersystem) mit Vorschau, alles-oder-nichts
- Export je Kartoffeljahr (= Erntejahr) als Excel: Auswertung je Sorte/Schlag, Kisten, Bewegungen
- Momentaufnahme (Sorte, Schlag, Erntedatum, Füllstand) bei jeder Bewegung, damit frühere Jahre
  auswertbar bleiben; benutzte Sorten/Schläge werden deshalb nur deaktiviert, nie gelöscht

### Phase 3 – Komfort
- Entnahme-Vorschläge ("200 kg Belana": älteste und oben liegende Kisten zuerst)
- Saisonvergleich direkt in der App (Diagramme über mehrere Kartoffeljahre)

## Ausbau zur Hof-App

Aus dem Lagersystem wird schrittweise eine Hof-App. Alle Bausteine nutzen dieselbe Anmeldung, Rollen,
Sprachen (de/pl) und Datenbank und laufen im selben Portainer-Stack. Bausteine sind einzeln abschaltbar.
Rahmen: 4–10 Benutzer. **Hofname, Anschrift, Telefon, E-Mail** stehen unter Verwaltung → Hof (umgesetzt);
der Hofname ersetzt „Lagersystem“ in der Kopfzeile und kommt später auf die Lieferscheine.

### Baustein A – Verkauf & Lieferscheine (umgesetzt in 0.9.0)
Umsetzung: Lieferschein `LS-JJJJ-NNNN` (fortlaufend je Jahr) mit Status offen/abgeschlossen.
Mitarbeiter verladen auf der Seite „Verladen“ mit Dauer-Scanner (Kamera bleibt an, jede Kiste wird sofort
gebucht). Eine verladene Kiste wird vom Stapel genommen (Nachrücken wie beim Auslagern) und ist danach
**leer** und nicht eingelagert (Bewegung „Verladen“ mit Lieferscheinnummer); Sorte/Stufe/Füllstand stehen
als Momentaufnahme an der Position. Entfernen vom offenen Lieferschein stellt den Füllstand wieder her
(Kiste muss neu eingelagert werden). Abgeschlossene Lieferscheine sind gesperrt, der Admin kann sie wieder
öffnen. Pflanzenpass-Angaben (A–D) erscheinen nur, wenn unter Verwaltung → Hof eine Registriernummer
eingetragen ist; C = Anerkennungsnummer, sonst Lieferscheinnummer. Kisten auf Lieferscheinen lassen sich
nicht löschen, Kunden mit Lieferscheinen nur deaktivieren.

- **Kunden**: vor allem Pflanzgut-Abnehmer und Händler/Abpacker (Name, Anschrift, Kundennummer, Notiz).
- **Lieferschein** anlegen (nur Admin/Büro): Kunde, Datum, Fahrzeug/Kennzeichen.
- **Verladen per QR-Scan**: Mitarbeiter scannen jede Kiste beim Verladen → Kiste wird ausgelagert
  (Bewegung „Verladen“ mit Bezug zum Lieferschein) und erscheint auf dem Lieferschein.
- Ware geht als **ganze Kisten und als Säcke/kg** raus: Positionen sind entweder Kisten (gescannt) oder
  freie Mengen (Sorte, Stufe, Kaliber, Gebinde z. B. 25-kg-Sack/Big Bag, Anzahl). Abgepackte Ware wird aus
  angebrochenen Kisten entnommen (Füllstand der Kiste senken).
- **Gewicht über Fuhrwerkswaage**: Leergewicht und Vollgewicht des Fahrzeugs eintragen, Netto = Differenz.
  Das echte Netto steht auf dem Lieferschein; die geschätzten kg aus den Kisten nur zum Vergleich.
- **Pflanzgut-Angaben**, soweit vorhanden: Anerkennungsnummer, Stufe, Kaliber, Pflanzenpass-Angaben
  (EU-Pflanzenpass). Leere Felder werden nicht gedruckt; bei Speiseware entfällt der Block.
  → Neue Daten an der Ware: Anerkennungsnummer je Sorte/Stufe/Erntejahr (Partie-Stammdaten, nur Info,
  Kisten bleiben Einzelkisten).
- **Lieferschein als PDF** (reportlab wie die Etiketten), mit Hofname/Anschrift.
- **Keine Rechnungen** in der App – die macht weiter das Büro/der Steuerberater. Export der Lieferscheine
  je Kartoffeljahr nach Excel (passt zum bestehenden Jahres-Export).
- **Rückverfolgung**: Kiste → Schlag/Erntedatum → Lieferschein → Kunde, in beide Richtungen auffindbar.

### Baustein B – Lagerklima (= Phase 2)
Siehe Phase 2. 3–6 Messpunkte, Warnungen nur in der App, DWD-Außentemperatur als Vergleich möglich.

### Baustein C – Maschinen & Wartung (umgesetzt in 0.10.0)
Umsetzung: Maschinen mit QR-Code `M-001` (der normale Scanner erkennt ihn), Arten mit Vorschlägen für
Wartungsintervalle (nach Zähler und/oder Monaten, „was zuerst kommt“), Ampel überfällig / bald fällig
(≤ 30 Tage bzw. ≤ 10 % des Intervalls) / letzte Wartung fehlt. Mitarbeiter: Zählerstand, Störung melden,
Wartung abhaken, Fotos; Kosten nur Admin. Logbuch mit Fotos (Pillow verkleinert auf 1600 px, entfernt
Metadaten). Fotos liegen in `UPLOAD_DIR` – im Stack ein eigenes Volume `uploads`.
Offene Störungen und fällige Wartungen stehen auf der Übersicht.

- Maschinen mit Typ, Kennzeichen, Baujahr, Betriebsstunden.
- **Wartungserinnerung** nach Betriebsstunden oder Datum (Ölwechsel, TÜV, UVV) – Anzeige in der App.
- **Reparaturbuch**: was, wann, wer, Kosten, Fotos.
- **QR-Code an der Maschine** (gleiches Etikett-System wie Kisten): scannen → Stunden eintragen,
  Störung melden, Historie ansehen.

### Baustein D – Schlagkartei & Ertrag
- Schläge behalten ihren Namen über die Jahre (Fruchtfolge = je Jahr eine Kultur am Schlag).
- **Umrisse auf einer Karte** (Luftbild), Import z. B. aus dem Agrarantrag (Shape/GeoJSON).
  Kartenkacheln müssen über die CSP erlaubt oder über den Server geholt werden.
- Erfasst in der App: **Pflanzung** (Datum, Sorte, Pflanzgut-Partie, Menge) und **Rodung**.
- **Düngung und Pflanzenschutz bleiben im bisherigen Programm** und werden importiert (nur Anzeige).
  Welches Programm/Format: noch offen.
- **Ertrag** automatisch aus den Kisten: t/ha je Schlag, Sorte und Jahr, Jahresvergleich.

### Baustein E – Wetter
- Öffentliche Daten des DWD (z. B. über Bright Sky): Vorhersage und Messwerte für den Hof-Standort.
- **Krautfäule-Ampel** aus Temperatur/Feuchte/Blattnässe (einfaches Modell, nur als Hinweis).

### Baustein F – Aufgaben
- Heute werden Arbeiten mündlich morgens verteilt → einfache **Tagesliste je Mitarbeiter**
  (Aufgabe, Zuständig, Erledigt-Haken), keine Projektverwaltung.
- **Keine Zeiterfassung** (bewusst nicht gewünscht).

### Baustein G – Startseite „Hof heute“
- Warnungen (Klima, fällige Wartung, Krautfäule), offene Aufgaben, Bestand, Wetter, letzte Lieferscheine.

### Reihenfolge
1. Verkauf & Lieferscheine (inkl. Hofname-Einstellung)
2. Lagerklima
3. Maschinen & Wartung
4. Schlagkartei & Ertrag
5. Wetter & Krautfäule
6. Aufgaben und Startseite „Hof heute“ (wächst mit den anderen Bausteinen mit)

## Noch offen

1. Mehrere Kistengrößen? Das System unterstützt beliebig viele Kistentypen.
2. Lieferschein: Muss ein Pflanzenpass-Etikett mitgedruckt werden oder reicht die Angabe auf dem Lieferschein?
   Gibt es eine Vorlage, wie der Lieferschein aussehen soll?
3. Werden je Fahrzeug mehrere Kunden beliefert (ein Wiegevorgang, mehrere Lieferscheine)?
4. Aus welchem Programm kommen Düngung/Pflanzenschutz, und kann es exportieren (Excel/CSV/ISOXML)?
5. Welche Sensoren genau (Vorschlag folgt mit Baustein B).

Geklärt: WLAN im Lager ist gut (kein Offline-Modus nötig); Zugriff von unterwegs läuft über die eigene Domain mit HTTPS.
