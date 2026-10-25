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
- **Unterschrift am Handy** (0.34.0): auf der Lieferschein-Seite unterschreiben Hof („Übergeben“) und Empfänger
  („Ware erhalten“) mit dem Finger, dazu der Name in Druckschrift. Gespeichert als PNG am Lieferschein
  (`Delivery.sign_farm` / `sign_customer`, zugeschnitten, schwarz auf weiß), im PDF über der Linie mit Name und
  Zeitpunkt. Unterschrieben = Positionen, Kunde und Waage gesperrt; entfernen kann nur der Admin bei offenem
  Lieferschein. Der Admin stellt bei den Lieferscheinen (Zahnrad) ein, ob beide Unterschriften zum Abschließen
  Pflicht sind (`settings` `delivery_sign_required`); ohne Pflicht kann auch nach dem Abschließen unterschrieben werden.
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

### Baustein D – Schlagkartei & Ertrag (umgesetzt in 0.13.0)
Umsetzung: Import der Schlagkartei aus Myfarm24 als KML/KMZ (Verwaltung → Schläge) mit Vorschau. Übernommen
werden Name, Schlagnummer, Fläche (SchlagHa, 4 Nachkommastellen), Bodenart, Rotes Gebiet, Umriss, Kultur
(Anbaujahr wählbar) und Vorfrucht (→ Kultur des Vorjahres, nur wenn dort leer). Abgleich beim erneuten
Import: Nummer + Name → Name (Schlag ohne Nummer) → gleicher Umriss (umbenannt) → eindeutige Nummer.
Schlagnummern und -namen sind in Myfarm24 nicht eindeutig (z. B. zweimal Nr. 7, zweimal „Mühlenfeld“);
doppelte Namen werden zu „Name (Nr)“. Schlagkarte als SVG ohne Kartenhintergrund (CSP), eingefärbt nach
Kultur, mit Zoom und Verschieben. Schlagseite: Fruchtfolge je Jahr, Pflanzung (Sorte, Pflanzgut, Datum)
und Rodung – Mitarbeiter dürfen eintragen, die Kultur ändert nur der Admin. Ertrag t/ha aus den Kisten
(wie im Jahres-Export) geteilt durch die Fläche aus der Schlagkartei.
Offen: Luftbild als Hintergrund (bräuchte einen Kartendienst und eine CSP-Ausnahme).

- Schläge behalten ihren Namen über die Jahre (Fruchtfolge = je Jahr eine Kultur am Schlag).
- **Umrisse auf einer Karte** (Luftbild), Import z. B. aus dem Agrarantrag (Shape/GeoJSON).
  Kartenkacheln müssen über die CSP erlaubt oder über den Server geholt werden.
- Erfasst in der App: **Pflanzung** (Datum, Sorte, Pflanzgut-Partie, Menge) und **Rodung**.
- **Düngung und Pflanzenschutz bleiben im bisherigen Programm** und werden importiert (nur Anzeige) –
  umgesetzt in 0.25.0 über die Myfarm24-Schlagdokumentation (CSV).
- **Ertrag** automatisch aus den Kisten: t/ha je Schlag, Sorte und Jahr, Jahresvergleich.

### Baustein E – Wetter (umgesetzt in 0.11.0)
Umsetzung: Standort (Breite, Länge) und Zeitraum der Krautfäule-Ampel unter Verwaltung → Hof.
Der Server holt über Bright Sky (DWD) 30 Tage Messwerte + ca. 9 Tage Vorhersage stündlich in einem Aufruf
und hält sie 30 Minuten im Speicher (Fehler: 5 Minuten, dann neuer Versuch). Wetterseite: Frost-Hinweis
(nächste 72 h, ≤ 0 °C Frost, ≤ 2 °C Bodenfrost möglich), Krautfäule nach Hutton-Kriterien (vorgestern bis
in 3 Tagen), Niederschlag 7/14/30 Tage + nächste 7 Tage, Vorhersage 7 Tage, stündlich 36 h.
Übersicht: Kurzwetter, Frost-Hinweis, Ampel nur im eingestellten Zeitraum.

- Öffentliche Daten des DWD (z. B. über Bright Sky): Vorhersage und Messwerte für den Hof-Standort.
- **Krautfäule-Ampel** aus Temperatur/Feuchte/Blattnässe (einfaches Modell, nur als Hinweis).

### Baustein F – Aufgaben (umgesetzt in 0.12.0)
Umsetzung: Nur der Admin legt an und verteilt. Zuteilung an mehrere Personen, keine Auswahl = alle;
einer hakt für alle ab (mit Notiz und Fotos). Fälligkeit, „wichtig“, Wiederholung täglich/werktags/
wöchentlich/alle 2 Wochen/monatlich – beim Abhaken entsteht die nächste Aufgabe im gleichen Rhythmus
(z. B. bleibt „montags“ montags, auch wenn zu spät erledigt). Bezug zu Maschine, Lieferschein oder
Lagerplatz (Lager/Reihe); dort werden offene Aufgaben angezeigt. Liste: Überfällig / Heute / Nächste 7 Tage /
Später / Ohne Termin, Filter Meine / Alle / je Person. Übersicht: „Deine Aufgaben für heute“, Zähler am
Menüpunkt (Handy: unten statt „Verlauf“, der Verlauf steht im Benutzermenü).

- Heute werden Arbeiten mündlich morgens verteilt → einfache **Tagesliste je Mitarbeiter**
  (Aufgabe, Zuständig, Erledigt-Haken), keine Projektverwaltung.
- **Keine Zeiterfassung** (bewusst nicht gewünscht).

### Baustein G – Startseite „Hof heute“
- Warnungen (Klima, fällige Wartung, Krautfäule), offene Aufgaben, Bestand, Wetter, letzte Lieferscheine.

### Reihenfolge
1. ✅ Verkauf & Lieferscheine (inkl. Hofname-Einstellung)
2. ✅ Maschinen & Wartung
3. ✅ Wetter & Krautfäule
4. ✅ Aufgaben (die Übersicht zeigt Aufgaben, Wetter, Maschinen und Lieferscheine – „Hof heute“)
5. ✅ Schlagkartei & Ertrag (Import aus Myfarm24)
6. Lagerklima (Sensoren) – auf Wunsch zurückgestellt

### Bestellungen und Aufgaben an die Admins (0.16.0)
Mitarbeiter schicken Bestellwünsche (was, Menge, Details, Fotos, dringend, optional Maschine/Störung) – auch
direkt aus einer offenen Störung über „Ersatzteil“. Bestellen dürfen Admins und Benutzer mit dem Häkchen
„Darf bestellen“ (Besteller). Ablauf: Neu → (Angefragt) → Bestellt → Geliefert → Erledigt, oder Abgelehnt mit
Begründung. Der Besteller trägt Lieferant, Bestellnummer, Preis, Bestell-/Liefertermin und eine Notiz ein;
der Anfragende sieht den Fortschritt und auf der Übersicht „Geliefert“. Badge für Besteller: neue Wünsche
und überfällige Lieferungen. Mitarbeiter dürfen Aufgaben anlegen, aber nur an Admins und Besteller
(ohne Auswahl an alle), ohne Wiederholung.

Status „Neu“ (0.16.2): Eine Meldung ist zuerst „Neu“. „Angefragt“ bedeutet: der Besteller hat beim
Lieferanten angefragt (Datum „Angefragt am“). Der Schritt ist optional – wer gleich bestellt, überspringt ihn
(gestrichelt in der Fortschrittsanzeige). Zurückziehen kann der Meldende nur, solange die Meldung „Neu“ ist.

Zuständigkeit (0.16.1): Jede Bestellung hat einen zuständigen Besteller. Neue Wünsche sind „noch frei“, bis
ein Besteller „Ich übernehme“ drückt (oder als Erster bearbeitet). Danach bearbeitet nur er (und der Admin);
andere Besteller sehen „X kümmert sich darum“ und können bewusst selbst übernehmen. Weitergeben an einen
anderen Besteller ist möglich. Zähler und Übersichtskarte zeigen jedem Besteller nur freie und eigene Vorgänge.

### Aufgaben aus Störungen und Wartungen (0.15.0)
Beim Melden einer Störung kann der Admin direkt eine Aufgabe anlegen (Zuständige, Termin, wichtig); offene
Störungen und fällige Wartungen haben einen Knopf „Aufgabe“ (Formular vorbefüllt). Aufgabe und Störung/Wartung
sind verknüpft: Abhaken der Aufgabe erledigt die Störung bzw. trägt die Wartung mit aktuellem Zählerstand ein;
umgekehrt schließt „Behoben“/„Erledigt“ an der Maschine die offene Aufgabe.

### Navigation (0.14.0)
PC: vier Bereiche oben (Übersicht, Lager, Feld & Hof, Verkauf), darunter die Unterpunkte des Bereichs;
rechts Suche, Aufgaben, Verwaltung (Zahnrad, nur Admin) und ein schlankes Benutzermenü (Einstellungen,
Hell/Dunkel, Sprache, Abmelden). Verwaltung hat eine eigene Startseite mit Kacheln. Handy: Startseite mit
Bereichs-Kacheln, Leiste unten Start · Lager · Scannen · Aufgaben · Suche. Die Suche findet Kisten,
Schläge, Maschinen, Lieferscheine, Kunden und Aufgaben; ein genauer Kisten-/Maschinencode oder eine
Lieferscheinnummer öffnet direkt die Seite.

## Ausbau, Runde 2 (Oktober 2026)

Interaktiv geklärt; Reihenfolge: Datensicherung → Push → Maschinen → Kalender.

1. ✅ **Datensicherung (0.17.0):** jede Nacht ab 3 Uhr Datenbank (pg_dump) + Fotos/Dokumente (nur neue)
   nach `/backup`; 14 Tage + je ein Stand pro Monat für ein Jahr. Ziel später ein NAS (wie es angebunden
   wird, ist noch offen – SMB, NFS oder gemounteter Ordner sind vorbereitet, siehe README). Verwaltung →
   Datensicherung zeigt Status, „Jetzt sichern“, Download; Admins sehen auf der Übersicht eine Warnung,
   wenn die Sicherung fehlschlägt oder älter als 36 Stunden ist. Zurückspielen nur über die Konsole.
2. ✅ **Push-Nachrichten + App auf dem Startbildschirm (0.18.0):** Einstellungen → Benachrichtigungen:
   je Gerät einschalten, **jeder wählt selbst** seine Themen (nur die, die zu seiner Rolle passen):
   neue Aufgabe für mich · morgens (ab 6 Uhr) meine Aufgaben für heute · neue/weitergegebene Bestellwünsche
   (Besteller) · Neuigkeiten zu meinen Bestellwünschen · neue Störung · Wartung wird fällig (nur neu fällige)
   · Frostwarnung (nachmittags, Frost in den nächsten 24 h) · Krautfäule-Ampel springt auf Rot.
   Die beiden **Wetter-Warnungen** (0.29.3) sind von sich aus nur für Admins an, alle anderen schalten sie
   bei Bedarf selbst ein (`User.push_on`; Themen mit `"default"` in `PUSH_TOPICS`).
   **Wartezeiten im Kalender** (0.29.4): bei laufender Pflanzenschutz-Wartezeit steht am ersten erlaubten Erntetag
   „<Schlag> erntefähig“ im Kalender (für alle, auch im iCal-Abo); nach der Ernte verschwindet der Termin.
   **QR-Code am Schüttlager** (0.29.5): jeder Lagerort hat einen Code `S-001`; Etikett (Admin, Seite des
   Lagerorts → QR-Etikett, Standard A6). Scannen öffnet für Mitarbeiter gleich das Einlagern.
   **Ernte-Modus** (0.29.6, seit 0.31.3 direkt in der Scan-Seite): über der Kamera ein Schalter „Ernte-Modus“ und
   ein Zahnrad für die Einstellungen (Sorte, Schlag, Verwendung, Stufe, Erntedatum, Füllstand, „nur befüllen“ oder
   „befüllen + einlagern“ mit Lager und Reihe – automatisch oder fest, seit 0.31.4). Schalter an: jeder Scan befüllt die Kiste (und lagert sie ein), die Kamera
   bleibt an, darunter die Liste der befüllten Kisten und der nächste Platz. Schalter aus: normaler Scan. Schalter
   und Einstellungen merkt sich jedes Gerät (Cookie `ernte`), auch wenn zwischendurch etwas anderes in der App
   gemacht wird; ein Erntedatum von einem anderen Tag wird zu heute. Platzvorschlag „gleiche Sorte zusammen“ –
   Stapel mit derselben Ware (Sorte, Verwendung, Stufe) und Platz, der höchste zuerst; sonst der freie Stellplatz
   am nächsten daran (Nachbar in derselben Reihe vor gegenüber), ohne solche Ware der erste freie. Schon befüllte
   Kisten werden abgewiesen.
   **Biogas** (0.38.0, eigene Rubrik „Biogas“, `/biogas`, `biogas.py`): BHKW-Motoren sind Maschinen der Art
   „BHKW / Gasmotor“ (Wartungsvorlagen nach Betriebsstunden: Ölwechsel, Zündkerzen, Ventilspiel 2.000 h, Luftfilter
   4.000 h, Ölanalyse 500 h, Revision 30.000 h, Generalüberholung 60.000 h – an den Herstellerplan anpassen) mit
   SKVE-Motor-Nr. und Nennleistung. Die App liest die Speicher-Kraftwerk-API nur (`SKVE_API_KEY`, optional
   `SKVE_GAS_STORAGE_ID` im Stack): jede Nacht ab 2 Uhr Fahrplan je Motor und Day-Ahead-Preis → `ChpDay`
   (Laufstunden, kWh, Starts, Erlös) und Betriebsstundenzähler = zuletzt abgelesener Stand + SKVE-Stunden der Tage
   danach (SKVE liefert den Fahrplan, nicht den echten Zähler – deshalb ab und zu am Motor ablesen und eintragen).
   Über den Zähler greifen die normalen Wartungsintervalle samt Push „Wartung wird fällig“; die Übersicht zeigt
   zusätzlich eine Prognose (Ø Laufzeit der letzten 28 Tage → voraussichtliches Datum), Leistung jetzt, heute erzeugt,
   Fahrplan und Gasspeicher der nächsten 24 h und den Strompreis (zwischengespeichert 10 Minuten). „Jetzt abrufen“
   (Admin) holt sofort. Weitere Ideen für die Rubrik: Warnungen Gasspeicher/Stillstand, Eigenkontrolle/Rundgang,
   Fütterung und Silo (über das Schüttlager), Gärrest, Prüffristen der Anlage.
   **BHKW auf der Startseite** (0.38.1): Karte „BHKW“ in der rechten Spalte – je Motor läuft/steht, Leistung jetzt
   von Nennleistung, Stunden gestern und die nächste Wartung nach Laufzeit (`biogas.dashboard()`). Die Startseite
   liest nur den Zwischenspeicher (`live(cached_only=True)`), den der Zeitplan alle 10 Minuten auffrischt
   (`refresh_live`) – sie wartet nie auf die API.
   **Nächster Start** (0.40.3): steht ein Motor, zeigen Startseite und Biogas-Seite den nächsten Start laut Fahrplan
   („Start heute 17:30 Uhr“ / „morgen …“; `biogas._next_start`, `start_label`).
   **Strompreis-Ampel** (0.40.0): Balken mit den Day-Ahead-Preisen je Viertelstunde von heute 0 Uhr bis morgen
   24 Uhr (sobald veröffentlicht), Farbe nach Rang im angezeigten Zeitraum (günstig grün → teuer rot), Strich =
   jetzt, gestrichelt = Tageswechsel; dazu Preis jetzt mit „günstig/mittel/teuer“ (unteres/oberes Drittel) und die
   günstigste Stunde ab jetzt (`biogas.price_strip`, Vorlage `partials/price_strip.html`). Auf der Startseite und
   der Biogas-Seite; die Startseite liest nur den Zwischenspeicher, den der Zeitplan auch ohne BHKW frisch hält.
   **Farben der Ampel** (0.40.1, Verwaltung → Startseite, gilt für alle, `settings` `price_colors`): „Verbraucher“
   (niedrig grün, hoch rot, günstigste Stunde) oder „Erzeuger“ (z. B. BGA: hoch grün, niedrig rot, teuerste Stunde);
   das Etikett günstig/mittel/teuer bleibt, seine Farbe folgt der Sicht (`biogas.price_view`).
   **Startseite einstellbar** (0.40.0): Bereiche (Strompreis, Jetzt wichtig, Tagesplan, Übergabe, Aufgaben, BHKW,
   Diese Woche, Wetter, Lager und Bestand) wie die Kacheln: Voreinstellung je Rolle unter Verwaltung → Startseite
   (`settings` `home_<rolle>`; von sich aus: Admin alles, Mitarbeiter ohne Strompreis/BHKW, Betrachter nur Wichtiges,
   Übergabe, Woche, Wetter, Bestand), jeder passt unter „Startseite anpassen“ an (`User.home_add/home_hide`,
   `tiles.sections_for`).
   **Reihenfolge** (0.40.2): mit den Pfeilen unter „Startseite anpassen“ (`User.home_order`, `tiles.section_order`;
   „Voreinstellung wiederherstellen“ setzt sie zurück). Am PC laufen die Bereiche zwischen Strompreis und Bestand
   in derselben Reihenfolge zweispaltig.
   **Rundgang** (0.39.0, Biogas → Rundgang, `rounds.py`): Stationen mit QR-Code `R-01` (Etiketten-PDF wie bei den
   Kisten), Prüfpunkte je Zeile und Intervall in Tagen; Vorschläge für die Biogasanlage (Fermenter, Nachgärer,
   BHKW-Raum, Pumpenhaus, Feststoffeintrag, Gasfackel). Scannen öffnet die Checkliste: antippen, was nicht in Ordnung
   ist, Notiz/Foto, speichern (`StationCheck`, Prüfpunkte als Text gespeichert). Mangel → Push „Rundgang: Mangel“
   (von sich aus Admins), offen bis „Behoben“; morgens Push für nicht kontrollierte Stationen; beides auch unter
   „Jetzt wichtig“. Nachweis der Eigenkontrolle über den Verlauf je Station.
   **Wochenbericht Biogas** (0.39.0): montags früh Push (Thema „Montags: Wochenbericht Biogas“, von sich aus Admins)
   mit MWh, Betriebsstunden, Ø Preis, Stillstandstagen je Motor, nächster Wartung und Rundgang-Mängeln der Vorwoche
   (`biogas.week_report` / `week_push`); dieselben Zahlen als Karte „Letzte Woche“ auf der Biogas-Seite.
   Zurückgestellt: Fütterung/Silo (läuft über die BGA-Software) und Erntemengen (Waagensoftware).
   **Hühner** (0.42.0, eigene Rubrik „Hühner“, `chickens.py` / `chickens_views.py`, ~30 Hennen): nur sichtbar für
   Benutzer mit „Rubrik Hühner“ in der Benutzerverwaltung (`User.chickens`; eintragen `keeps_chickens` = zusätzlich
   Mitarbeiter, Admin darf immer). Eier-Buch (`EggEntry`: Anzahl, davon kaputt, mehrere Einträge am Tag),
   Verteilung an Haushalte (`Household` anlegbar/deaktivierbar, `EggShare`; Vorrat = gute Eier − verteilt; kein
   Verkauf), Herde (`FlockEvent` Zu-/Abgang mit Grund, Rasse, Schlupf → Bestand, Gruppen mit Alter; Legeleistung =
   Eier ÷ Hühnertage), Futter (`FeedEntry` kg/€ → g je Huhn und Tag, Kosten je Ei je Monat), Stallbuch
   (`FlockTreatment` mit Wartezeit → „Eier gesperrt bis …“). Stallkontrolle über den Rundgang (`CheckStation.area`
   = huehner, Vorschlag „Hühnerstall“, Mängel per Push an die Hühner-Leute), Aufgaben mit `Task.area` (Vorschläge:
   ausmisten wöchentlich, Milben monatlich, Wurmkur alle 3, Impfung alle 6 Monate). Startseite: Bereich „Hühner“
   mit Schnelleingabe. Push: 18 Uhr „noch keine Eier eingetragen“, nach Sonnenuntergang (NOAA-Formel, Hof-Standort)
   „Hühnerklappe zu?“ (von sich aus aus).
   **Wer ist dran?** (0.42.2): `chickens.fair_order()` – wer am längsten keine Eier bekam (noch nie zuerst, dann
   ältestes „zuletzt“, bei Gleichstand weniger in 30 Tagen) steht vorn, ist beim Verteilen vorgewählt und steht als
   „Als Nächstes dran“ auf Startseite und Hühner-Übersicht.
   **Stall-Sensor über Meshtastic** (0.43.0, `coop.py`, `bridge/`): Knoten am Stall (SHT31/BME280 + Reedkontakt
   über das Detection-Sensor-Modul) → LoRa → Gateway im Haus → MQTT (Mosquitto im Stack, Profil `huehner`) →
   `coop-bridge` → `POST /api/huehner/sensor` (Schlüssel per Admin erzeugt, nur Hash in `settings`).
   Gespeichert: `CoopReading` (°C, %, Akku, Spannung, RSSI/SNR), `CoopDoorEvent` (nur Änderungen). Anzeige auf
   Hühner-Übersicht (Kurve 48 h, Klappen-Zeiten) und Startseiten-Karte. Klappen-Push nach Sonnenuntergang nur
   noch, wenn der Sensor „offen“ meldet (dann dringend, Thema `coop_open`); Push bei Frost/Hitze/Sensor stumm
   (`coop_sensor`). Einrichtung in `bridge/README.md`.
   Neue Bereiche nach gespeicherter Voreinstellung (0.42.1): `settings` `home_known_<rolle>` merkt sich, welche
   Bereiche es beim Speichern gab; später dazugekommene (Hühner) folgen der eingebauten Voreinstellung.
   **Wartungsfenster nach Strompreis** (0.38.2, Biogas-Seite): für BHKW-Wartungen, die in den nächsten 7 Tagen
   fällig werden (Prognose aus der Laufzeit) oder schon „bald fällig“/überfällig sind, sucht `biogas.best_windows` die
   drei günstigsten Zeitfenster – Länge = Stillstand der Wartung (`MaintenanceTask.downtime_hours`, Vorlagen z. B.
   Ölwechsel 4 h, Zündkerzen 3 h, Revision 24 h; 0 = läuft weiter), innerhalb der Arbeitszeit (`settings`
   `biogas_window_hours`, Standard 6–18 Uhr, längere Stillstände dürfen darüber hinaus), höchstens einer je Tag und
   bis zum Fälligkeitstag. Preise: Day-Ahead, für spätere Tage die SKVE-Preisprognose (`price_outlook`,
   30 Minuten zwischengespeichert). Entgangener Erlös = Nennleistung × Dauer × Ø Preis (nur Admins sehen €);
   „Als Aufgabe“ legt eine Aufgabe mit dem Datum des besten Fensters an.
   **Wartungen kopieren** (0.38.1): an der Maschine „Kopieren“ → Wartungen und Zielmaschinen wählen
   (`machines.copy_tasks`): fehlende werden angelegt, gleichnamige auf Wunsch angepasst (Intervall, Notiz),
   „zuletzt erledigt“ bleibt je Maschine; Wartungen nur nach Zähler werden bei Maschinen ohne Zähler übersprungen.
   **Beregnung** (0.37.0, Feld & Hof → Beregnung, `/beregnung`, `irrigation.py`): Import des RainDancer-Berichts
   „Wasserbuch“ als „Excel 97-2003“ (.xls über `xlrd`, auch .xlsx). Der CSV-Export von RainDancer ist unbrauchbar
   (nur ein Einsatz + Summe) und wird abgewiesen; die Summe der Einsätze muss zum „Total“ passen. Gruppen = Brunnen
   (`Well`, „Name (Nr)“), Einsätze = `Irrigation` (Schlag, Regner, Start/Ende in Ortszeit → UTC, Stunden, m³; der
   Mandant wird nicht gespeichert). Vorschau mit Zuordnung der Schläge (gleicher Name automatisch, sonst Auswahl –
   gemerkt in `settings` `raindancer_plots`); ein neuer Import ersetzt alle RainDancer-Einsätze im Zeitraum der Datei.
   Anzeige: je Brunnen m³ gegen die genehmigte Jahresmenge (Kalenderjahr, ab 80 % Hinweis, „Jetzt wichtig“ für
   Admins), je Schlag m³ und mm (m³ ÷ ha ÷ 10), Karte „Beregnung“ auf der Schlagseite, mm im Ertragsvergleich,
   Regen seit 1. Januar daneben.
   **Team** (0.36.0, `team.py` / `team_views.py`):
   *Tagesplan* (`/tagesplan`, Übersicht → Tagesplan): der Admin teilt je Mitarbeiter ein, was an einem Tag zu tun
   ist (Text, optional Maschine und Schlag; nachmittags öffnet sich der nächste Tag), „Vom … übernehmen“ kopiert den
   letzten Plan für alle ohne Einträge, „Plan aufs Handy schicken“ sendet jedem seine Einträge (Push-Thema
   „Mein Tagesplan“). Auf der Startseite steht „Dein Plan für heute“ (`PlanEntry`).
   **Abhaken** (0.41.0): die eingeteilte Person (oder der Admin) hakt ihre Einträge ab – durchgestrichen mit Uhrzeit
   (`PlanEntry.done_at/done_by`, `team.toggle_plan_entry`); das ✕ (löschen, nur Admin) fragt nach. „Unerledigtes vom …
   übernehmen“ hängt Offenes des letzten Tages (bis eine Woche zurück) an (`team.carry_over`).
   **Tagesplan pflegen** (0.41.1): in der Benutzerverwaltung „Darf den Tagesplan pflegen“ (`User.can_plan`,
   `is_planner`, `team_views.planner_required`) – wie der Admin einteilen, abhaken, löschen, übernehmen, verschicken.
   **Mehrere Schläge je Eintrag** (0.41.2): gleiche Auswahl wie bei Aufgaben (`macros.plot_pick`, `PlanEntry.plots`).
   *Übergabe* (`/uebergabe`): Notiz zum Feierabend („Was der Nächste wissen muss“), steht oben auf der Startseite,
   bis jemand sie abhakt oder sie älter als 3 Tage ist; Push-Thema „Neue Übergabe-Notiz“ ist von sich aus aus
   (`Handover`).
   *Unterweisungen* (Verwaltung → Unterweisungen, nur Admin): Themen mit Wiederholung in Monaten und Inhalt, für
   alle oder ausgewählte Mitarbeiter (Vorschläge: Arbeitsschutz, Stapler, Schlepper, Kistenlager, Pflanzenschutz,
   Brandschutz). Je Person Ampel (bis …, bald fällig ≤ 30 Tage, fällig, noch nie); der Mitarbeiter unterschreibt am
   Handy (`InstructionRecord.signature`, Technik wie beim Lieferschein: `signatures.py`, `static/js/signpad.js`,
   `macros.sign_modal`). Nachweis je Thema und Jahr als PDF (`instruction_pdf.py`, immer Deutsch). Fällige stehen
   für Admins unter „Jetzt wichtig“.
   *Unterlagen* (0.36.1): an jede Unterweisung lassen sich Dateien hängen – PDF, Bilder, Präsentationen und
   Office-Dateien der Berufsgenossenschaft (`InstructionDocument`, `uploads.save_document(office=True)`: Typ am
   Inhalt erkannt, ZIP-/OLE-Formate nur mit passender Endung). PDFs und Bilder öffnen im Browser, der Rest wird
   heruntergeladen; die Titel stehen auf dem Nachweis.
   **Kistenzustand** (0.35.0): auf der Kistenseite „Kiste defekt melden“ mit kurzer Angabe (Schnellwahl für die
   Metallkisten: Gitter, Rahmen, Schweißnaht, Stapelfuß; Foto über die Fotoleiste). Defekte Kisten stehen unter Lager → Kisten-Reparatur
   (`/kisten/reparatur`), werden im Ernte-Modus abgewiesen und auf Kistenliste/Stapel markiert; „Repariert“ setzt
   sie zurück. Der Admin kann leere, nicht eingelagerte Kisten ausmustern (nicht mehr befüll-/einlagerbar, aus der
   Kistenliste und den Zahlen raus, Verlauf bleibt). `Box.condition` (`defekt`/`ausgemustert`), Bewegungen
   `defekt`/`repariert`/`ausgemustert`, Logik `storage.report_defect/repair_box/retire_box`.
   **Feldnotizen** (0.33.0): Schlagkarte → „Ich bin hier – Notiz“ (Standort des Handys) findet den Schlag (im Umriss
   oder bis 300 m daneben) und öffnet auf der Schlagseite „Notizen vom Feld“ mit Text, Fotos und Standort zum
   aktuellen Anbaujahr (`FieldNote`, `fields.locate`). Löschen: Admin oder Verfasser.
   **Ertragsvergleich** (0.32.4, Feld & Hof → Erträge, `/ertraege`): t/ha je Schlag über die letzten Jahre (mit Sorten
   bzw. Kultur) und je Sorte (nur Schläge, auf denen die Sorte allein stand); Filter „nur Kartoffeln“ und Betrieb
   (`fields.yield_history`).
   **Ernte-Tagesbericht** (0.32.3, Lager → Erntebericht, `/ernte/bericht`): was an einem Tag befüllt wurde (leere
   Kiste → befüllt), Kisten und geschätzte Menge je Sorte, Schlag, Reihe und Mitarbeiter; Tage blättern. Abends um
   19 Uhr Push (Thema „Abends: Ernte-Tagesbericht“, von sich aus nur Admins), nur wenn geerntet wurde.
   **Nachsortieren per Scan** (0.32.2): zweiter Schalter auf der Scan-Seite (es ist immer nur Ernte oder Nachsortieren
   an). Kiste scannen → unter der Kamera Größe und Füllstand antippen → Speichern; die zuletzt gewählte Größe ist für
   die nächste Kiste vorgewählt (Cookie `sortgroesse`). Sorte, Schlag, Erntedatum usw. bleiben; der Verlauf zeigt
   die Änderung. Routen `main.sort_toggle` / `main.sort_box`.
   **Lagerdauer-Ampel** (0.32.1): eingelagerte Kisten nach Erntedatum gelb (ab 6 Monaten) bzw. rot (ab 9) umrandet
   in Karte, Liste, Stapel und an der Kiste; Filter „Alte Ware“ (auch 3D). Seite „Älteste Ware zuerst“
   (`/lager/aelteste`, „oben“ = gleich greifbar). Schwellen unter Verwaltung → Kartoffellager.
   **Kosten je Maschine und Jahr** (0.32.0, nur Admins): Karte auf der Maschinenseite (Jahr blättern) und Übersicht
   Maschinen → Kosten. Diesel = getankte Liter × Preis der Lieferungen im Jahr (Mittel nach Menge, sonst letzter
   Preis), Reparaturen/Wartungen = Beträge im Logbuch, Bestellungen = Bestellwünsche mit Preis (nicht abgelehnt);
   je Betriebsstunde bzw. 100 km aus den Zählerständen (`machines.costs`).
   **Zählerstand beim Tanken als Pflicht** (0.31.9): Admin-Schalter in den Tank-Einstellungen (`settings`
   `fuel_counter_required`); dann wird bei Maschinen mit Zähler ohne Stand nicht gebucht.
   **Maschinenarten und Tanken** (0.31.8): neue Arten Mähdrescher/Häcksler, Sä- und Drilltechnik, Bodenbearbeitung,
   Pflanzenschutzspritze, Düngerstreuer/Güllefass (mit Wartungsvorschlägen, z. B. Pflanzenschutzgeräte-Kontrolle alle
   3 Jahre). Getankt werden nur Maschinen mit eigenem Tank (`FUEL_CATEGORIES`: Schlepper, Fahrzeug, Mähdrescher,
   Roder, Stapler); je Maschine umstellbar („Wird getankt“, `Machine.refuels`). Anhänger & Co. haben keine
   Diesel-Karte und stehen nicht in der Auswahl im Tankbuch. Maschinen, die schon getankt wurden, behalten es.
   **Reihenfolge der Reihen** (0.31.7, Verwaltung → Kartoffellager): „A … n“ (erste Reihe oben/hinten) oder „n … A“
   (erste Reihe unten/vorne) – gilt gleich für Karte, Liste und 3D-Ansicht (`Warehouse.rows_bottom_up`).
   **Titelbild je Maschine** (0.31.6): auf der Maschinenseite oben (Mitarbeiter: „Bild hinzufügen/ändern“, Kamera
   oder Galerie), in der Maschinenliste als quadratische Vorschau (ohne Bild das Symbol der Art). `Machine.image`,
   Vorschau `uploads.save_with_thumb` / `thumb_name`; beim Ersetzen/Entfernen verschwinden die alten Dateien.
   **Kartoffelgrößen** (0.31.5, Verwaltung → Kistentypen): Liste der Kaliber (eine je Zeile, „25-28“ → „25–28 mm“),
   Auswahl beim Befüllen/Nachsortieren der Kiste („Sortierung / Größe“, ein alter abweichender Wert bleibt
   wählbar), im Ernte-Modus (optional; gleiche Größe kommt beim Platzvorschlag zusammen) und als Kaliber beim
   Lieferschein. Gespeichert in `settings` `potato_sizes`; an der Kiste steht der Text in `Box.grade`.
   **Lagerkarte am Handy** (0.29.7): größere Felder, mit zwei Fingern zoomen und verschieben, Knöpfe − / ganzes
   Lager / +. Antippen eines Stapels klappt unten ein Blatt mit seinen Kisten auf (ohne Seitenwechsel, die Karte
   bleibt bedienbar; der gewählte Stapel ist umrandet). Am PC bleibt das Fenster.
   **Fotos** (0.29.8) an Kisten (z. B. Mängel), Schlägen je Anbaujahr, Schüttlagern und Lieferscheinen: Mitarbeiter
   tippen auf „Foto“ (Kamera oder Galerie, wird gleich hochgeladen); löschen dürfen Admins und wer es hochgeladen
   hat. Fotos bleiben an der Kiste, auch wenn sie neu befüllt wird (mit Datum und Name).
   **Betriebsstätten** (0.30.0, Verwaltung → Betriebsstätten): Name, Anschrift, Telefon, E-Mail, eigene
   Pflanzenpass-Nummer. Zuordnen lassen sich Schläge (auch mehrere auf einmal), Kartoffellager, Schüttlager,
   Maschinen, der Hoftank und Lieferscheine (Absender; Vorauswahl = gewählter Filter oder die einzige
   Betriebsstätte). Der Lieferschein zeigt den Hofnamen, darunter die Betriebsstätte mit ihrer Anschrift.
   Filter „Betrieb“ auf Lager-Übersicht, Schlagkarte, Schüttlager, Maschinen, Lieferscheinen und in der
   Verwaltung der Schläge; die Wahl wird je Gerät gemerkt. Ohne Betriebsstätten bleibt alles wie bisher.
   **Änderungsprotokoll** (0.31.0, Verwaltung → Protokoll, nur Admins): jedes Ändern und Löschen in allen
   Bereichen mit Zeit, Benutzer und vorher → nachher (gelöscht: die letzten Werte). Nicht erfasst: Neuanlagen,
   Platz und Inhalt der Kisten (stehen im Verlauf), persönliche Einstellungen, Passwörter (nur „geändert“),
   Umrisse (nur „geändert“). Importe (KML, Myfarm24, Fruchtfolge, Excel) als ein Eintrag mit Zahlen.
   Filter nach Bereich, Aktion, Benutzer, Suche; Einträge bleiben 2 Jahre.
   Nie an sich selbst; wer zu einer Störung schon eine Aufgabe bekommt, erhält die Störung nicht doppelt.
   Texte in der Sprache des Empfängers. iPhone: erst „Zum Home-Bildschirm“, dann einschalten (ab iOS 16.4).
3. ✅ **Maschinen (0.19.0):**
   - **Tankbuch** (Feld & Hof → Tankbuch): ein Hoftank. Mitarbeiter tanken auf der Maschinenseite (QR-Code) mit
     Litern + Zählerstand (höherer Stand wird übernommen), Besteller tragen Lieferungen (Preis/l, Lieferant) und
     Peilungen ein (setzt den Stand neu). Füllstand = letzte Peilung + Lieferungen − Tankungen. Tankgröße und
     Warnschwelle stellt der Admin ein; beim Unterschreiten Push an die Besteller (Thema „Hoftank wird leer“).
     Verbrauch l/h bzw. l/100 km aus den Zählerständen (Annahme: volltanken), Liter je Maschine und Jahr.
   - **Ersatzteile** je Maschine (Name, Teilenummer, Hersteller, Lieferant, übliche Menge) mit „Bestellen“:
     öffnet einen vorbefüllten Bestellwunsch, der mit dem Teil verknüpft ist; Lieferant wird vorgeschlagen.
     Mitarbeiter legen an und ändern, löschen nur der Admin.
   - **Dokumente** je Maschine (PDF, JPEG, PNG; Art: Anleitung, Prüfbericht/TÜV/UVV, Rechnung, Ersatzteilliste,
     Sonstiges). Alle sehen und laden hoch, löschen der Hochladende oder der Admin. Typ wird am Inhalt geprüft,
     Auslieferung mit `Content-Security-Policy: sandbox`.
   - **Lieferanten** (Bestellungen → Lieferanten): Ansprechpartner, Telefon, E-Mail, Shop, unsere Kundennummer.
     Besteller pflegen, Admin löscht (verwendete werden nur ausgeblendet). Auswahl in Bestellung, Teil, Lieferung.
   - Suche findet Teilenummern, Dokumente und Lieferanten.
4. ✅ **Kalender (0.20.0)** (Übersicht → Kalender): Monatsraster (PC) bzw. Terminliste (Handy) mit fälligen
   Wartungen (Ampelfarbe) und erwarteten Lieferungen bestellter Teile. Daneben: überfällige Wartungen und Wartungen
   nach Zählerstand (ohne Datum). Persönlicher Abo-Link (iCal, `webcal://`) für iPhone/Google/Outlook – ohne
   Anmeldung abrufbar über einen geheimen Schlüssel je Benutzer, erneuerbar; Texte in der Sprache des Benutzers.

Später: Lagerkontrolle als Rundgang, Inventur per Scan, Rückverfolgung als PDF, Schwund & Sortierung.
Nicht gewünscht: Reservierungen für Kunden.

### Schlagdokumentation aus Myfarm24 (0.25.0)
Zweiter Import unter Verwaltung → Schläge: die CSV „Schlagdokumentation“ (cp1252, „;“, je Schlag ein Block
„Schlag: Name / Nr. n / FLIK: …“). Die KML-Datei bleibt für die Umrisse zuständig, die CSV bringt alles andere:
Fläche, FLIK, USchlag-ID, Bodenart, Rotes Gebiet, Bodenuntersuchung (pH, P-/K-Klasse, Mg, Datum), Kultur, Sorte
(Kartoffelsorten werden der Sorte in der App zugeordnet, sonst als Text), Vorfrucht/Vorvorfrucht (→ Vorjahre,
nur wenn leer), N-Bedarf nach DüV, Saat-/Erntedatum (nur ergänzt), Zwischenfrucht und alle Maßnahmen
(`FieldMeasure`: Aussaat, Düngung mit Nährstoffen in kg/ha, Pflanzenschutz, Arbeiten/Maschinen, Personen).
Das Erntejahr steht in der Datei. Abgleich: USchlag-ID → Nummer + Name → Name (wie beim KML-Import); neue
Schläge (in Myfarm24 ohne Umriss in der KML, z. B. Teilschläge, Grünland) werden auf Wunsch angelegt. Beim
erneuten Import werden die importierten Maßnahmen des Jahres ersetzt, in der App erfasste bleiben.
Schlagseite: Boden-Karte, je Anbaujahr Maßnahmen aufklappbar, Nährstoffsumme und N anrechenbar gegen den Bedarf
(Myfarm24 summiert ungerundet, daher ±1 kg/ha Abweichung möglich).
Später möglich: Maschinen aus „Arbeiten / Maschinen“ den Maschinen der App zuordnen.

### Luftbild: Koordinatensystem aus den Capabilities (0.27.3)
Der LGLN-Dienst liefert laut Portal in EPSG:25832 (UTM 32N); die App fragte bisher fest in EPSG:4326 ab. Jetzt
liest der Server einmal am Tag die GetCapabilities des WMS (Layer, Koordinatensysteme, Formate) und wählt:
EPSG:4326/4258/CRS:84 direkt, sonst EPSG:3857 oder UTM 32/33 – dann holt er den umschließenden Ausschnitt und
entzerrt ihn auf Länge/Breite (Pillow QUAD, Projektion selbst gerechnet, gegen pyproj < 1 mm geprüft). Ohne lesbare
Capabilities wird wie bisher EPSG:4326 versucht. Verwaltung → Hof → „Dienst prüfen“ zeigt Layer, Systeme, Formate
und die gewählte Abfrage; Fehlermeldungen des Dienstes enthalten die abgefragte Adresse.
0.27.4: Capabilities in WMS 1.1.1 (mit DOCTYPE-Zeile) werden gelesen – vorher pauschal abgelehnt –, GetMap dann
mit `SRS` und Länge/Breite-Reihenfolge. „Dienst prüfen“ nennt den genauen Grund, wenn die Capabilities scheitern,
und macht eine Testabfrage am Hof-Standort.
0.27.5: Wartezeit je Abruf 10 s; nach einem Netzfehler wird der Dienst 2 Minuten nicht erneut gefragt (die Threads
blieben sonst hängen, und „Dienst prüfen“ lief in die 60-s-Grenze von Gunicorn → 502 über Cloudflare). Fehler
liefern 424 bzw. 200 (Testseite) statt 502 – 502/504 ersetzt Cloudflare durch die eigene Fehlerseite. „Dienst
prüfen“ misst DNS, Verbindung, Capabilities und Testbild einzeln mit Dauer und bricht beim ersten Netzfehler ab.
0.27.6: Antwortet der Dienst mit einer Webseite (z. B. Wartungsseite des LGLN), meldet die App deren Titel
(„Der Dienst antwortet mit einer Webseite statt mit Daten: „Wartungsarbeiten““) und fragt 2 Minuten nicht erneut.

### Rubrik Lager neu gegliedert (0.29.2)
„Lager“ öffnet (Handy und PC) eine **Übersicht** (`/lager/uebersicht`, `main.storage_home`) mit zwei Teilen:
**Kartoffellager** (jede Halle mit Belegung, Links Kisten/Verlauf/Etiketten, Bestand und Bestand je Sorte) und
**Schüttlager** (jeder Lagerort mit Ware und Bestand, „Neuer Lagerort“), dazu die letzten Bewegungen. Am Handy steht
das Schüttlager direkt unter den Hallen. Unterleiste am PC: Übersicht · Lagerkarte · Kisten · Verlauf · Etiketten ·
Schüttlager · Scannen. In der Verwaltung heißt der Punkt jetzt „Kartoffellager“.

### Schüttgut-Lager (0.29.0)
Für Getreide, Raps u. Ä. (Lager → Schüttgut). Entscheidungen: Lagerarten **Flachlager-Box**, **Haufen in der Halle**
und **Big Bags** (nur als Anzahl und Menge, ohne einzelne Codes); **eine Ware je Lagerort** (Kultur, optional Sorte –
wird beim ersten Einlagern in den leeren Lagerort gesetzt, danach passt nur dieselbe Ware). Lagerorte legt der Admin
an (Name, Standort, Kapazität t, bei Big Bags kg je Bag), Mitarbeiter buchen.
- **Einlagern** je Fuhre: Datum, Ware, Schlag, Waage (brutto/tara oder netto, kg; „38.000“ = 38 000), bei Big Bags
  die Anzahl, freiwillig Feuchte, Protein, Hektolitergewicht, Fallzahl, Besatz, Fahrzeug, Notiz.
- **Auslagern**: Eigenverbrauch (Fütterung, Biogas, Saatgut eigene Flächen, Sonstiges) am Lagerort; **Verkauf
  über den bestehenden Lieferschein** („Schüttgut verladen“, Brutto/Tara aus der Waage des Lieferscheins vorbelegt).
  Die Position bucht die Auslagerung, Entfernen nimmt sie zurück; PDF-Zeile „Schüttgut, gewogen“.
- **Anzeige**: Bestand, Füllgrad, Qualität der aktuellen Füllung (Mittel nach Menge, seit der Lagerort zuletzt
  leer war), Herkunft je Schlag in %, Buchungen. Der eingelagerte Getreide-Ertrag zählt beim Schlag (t/ha).
  Lager-Übersicht und Bereichsseite zeigen die Summe je Ware.
- Nicht gewählt (später möglich): Silos, Umlagern zwischen Lagerorten, Bestandskorrektur/Inventur.
- Technik: `BulkStore`, `BulkEntry` (ein/aus), `app/bulk.py`, `app/bulk_views.py`, `DeliveryItem.bulk_entry_id`/`crop`.

### Handy-Ansicht aufgeräumt (0.28.0)
Ausgangslage: Die Startseite war am Handy über 6 Bildschirmhöhen lang, vieles stand doppelt (Kachel + Karte), dazu
drei Navigationswege (Kacheln, Reiterleiste oben, Leiste unten). Entscheidungen (Entwürfe A–D besprochen):
- **Untere Leiste**: Heute · Lager · Scannen · Feld & Hof · Mehr. Lager, Feld & Hof und Mehr öffnen eine
  **Bereichsseite** (`/bereich/<key>`, `navigation.HUBS`/`hub()`) mit großen Zeilen und dem Stand je Punkt
  (`views._status`: „2 Störungen“, „3682 l im Tank“ …). Die Reiterleiste oben gibt es nur noch am PC; am Handy
  steht über dem Inhalt ein Zurück-Link zum Bereich (`navigation.crumb`).
- **Startseite am Handy** („Heute“): Gruß und Datum, Info-Chips (Wetter, Frost, Krautfäule bei hohem Risiko, Tank),
  **Schnellzugriff** (die ersten 4 der einstellbaren Kacheln, ohne Scannen – `tiles.quick`), **„Jetzt wichtig“**
  (eine Liste statt einzelner Karten: Datensicherung, Störungen, fällige Aufgaben, überfällige Wartungen, offene
  Lieferscheine, gelieferte Bestellungen – `views._important`, 4 sichtbar, Rest aufklappbar) und die Termine.
  Lagerzahlen (Kennzahlen, Bestand je Sorte, Belegung, letzte Bewegungen) stehen am Handy auf der Lager-Seite
  (`partials/stock_overview.html`, `views._stock_overview`). Am PC bleibt die Startseite wie bisher.
- **Listen kompakt**: Suche + „Filter“-Knopf, aktive Filter als Chips zum Wegtippen (`macros.filter_toggle`,
  `filter_chips`, Jinja-Global `url_without`), eine Zeile je Kiste/Schlag; Aufgaben mit einzeiliger Zusatzzeile.
  Nebenbei behoben: In der Kistenliste steckte der Platz-Link in einem Link (ungültiges HTML, brach die Zeile).
- 0.28.1, **PC-Startseite** nach demselben Prinzip: links „Jetzt wichtig“ (8 sichtbar), rechts Termine, Frost-Hinweis
  und Wetter, darunter die Lagerzahlen. Die Einzelkarten (Aufgaben heute, Bestellungen, Maschinen, Lieferscheine,
  Datensicherung) sind weg – ihr Inhalt steht in „Jetzt wichtig“ (für Besteller zusätzlich Bestellwünsche, die noch
  niemand übernommen hat). Tablet hoch nutzt die Handy-Ansicht, ab 992 px die PC-Ansicht.
- 0.29.1: **Aufgaben als eigener Block** unter „Jetzt wichtig“ (überfällig, heute, ohne Termin; mit Haken zum
  Abhaken, „Neue Aufgabe“, „Alle Aufgaben“; 5 sichtbar am Handy, 8 am PC). „Jetzt wichtig“ enthält keine Aufgaben mehr;
  Störungen mit fälliger Aufgabe stehen nur bei den Aufgaben.

### Wochenübersicht: Alle / Meine (0.27.2)
Die Karte „Diese Woche“ auf der Startseite hat einen Umschalter „Alle“ (Standard) / „Meine“ – wie „Nur meine“ im
Kalender (eigene Termine, Termine für alle, eigene Aufgaben/Wartungen; Feiertage bleiben). Die Wahl merkt sich der
Browser (Cookie `woche`, ein Jahr).

### Niederschlag seit Jahresbeginn (0.27.1)
Die Wetterseite zeigt unter „Niederschlag“ die Summe seit 1. Januar und ein Balkendiagramm je Monat.
Abgeschlossene Monate werden einzeln bei Bright Sky abgefragt und im Speicher gehalten (endgültig 3 Tage nach
Monatsende, vorher 30 Minuten); der laufende Monat kommt aus den 30-Tage-Messwerten (bis gestern). Nur die
Wetterseite lädt die Monatswerte, die Übersicht bleibt schnell. Fehlt ein Monat, steht ein Hinweis da.

### Fruchtfolgeplan (0.27.0)
Dritter Import unter Verwaltung → Schläge: der Myfarm24-„Fruchtfolgeplan“ (CSV, „Schlag;Nr.;ha;2026;…“, Kürzel
K/M/RW/… mit Legende darunter). Er setzt je Schlag und Jahr `PlotYear.planned_crop` (Kulturname aus der Legende,
„Sonst,“ → „Sonstige“, „-“ = Schlag in dem Jahr nicht im Plan, leer = Planung gelöscht); die tatsächliche Kultur
aus Schlagdokumentation/KML bleibt unberührt und geht in der Anzeige vor (`PlotYear.shown_crop`). Neue Schläge aus
dem Plan werden auf Wunsch angelegt (Nummer, Fläche). Feld & Hof → Fruchtfolge: Tabelle Schlag × Jahr (tatsächlich
ausgefüllt, geplant gestrichelt), Hinweis bei unterschrittener Anbaupause (Kartoffel, Rübe; einstellbar
auf der Seite – Admin, `fielddoc.rotation_breaks()`, `settings` `rotation_break_*`, von sich aus 3 Jahre dazwischen, 0.40.5) und
Anbauumfang in ha je Kultur und Jahr. Schlagkarte und Schlagseite zeigen für Planjahre die geplante Kultur.
Farben je Kultur jetzt auch über den Namen (Mais = Silomais, Winterweichweizen = Winterweizen, Spelz = Dinkel).
Echte Hofdaten (Schlagnamen, Flächen, Personen, Hofname) gehören nicht ins Repo – Tests nutzen erfundene Daten.

### Pflanzenschutz-Übersicht und Lage ohne Umriss (0.26.0)
Feld & Hof → Pflanzenschutz: je Erntejahr alle Schläge mit Anwendungen, letzte Anwendung, Ende der Wartezeit
(laufende zuerst, „noch n Tage“; nach dem Erntedatum „geerntet“) und Mittel ohne bekannte Wartezeit. Die Wartezeit
kommt aus der Myfarm24-Bemerkung („Wartezeit 14 Tage“, „Wartezeit F“ = durch den Anwendungszeitpunkt abgedeckt);
in der Datei steht sie nur bei einem Teil der Anwendungen, deshalb pflegt der Admin in der Liste „Eingesetzte
Mittel“ eine Wartezeit je Mittel (`PesticideProduct`, gilt für alle Kulturen – Hinweis auf der Seite). Die Liste
zeigt auch Anwendungen, Fläche und Gesamtmenge je Mittel. Auf der Schlagseite steht „Wartezeit bis …“ beim
Pflanzenschutz, auf der Übersicht gibt es die Kachel „Pflanzenschutz“ (Anzahl laufender Wartezeiten).
Schläge ohne Umriss erscheinen als Punkt auf der Schlagkarte (Lage aus der Schlagdokumentation, „Geografische
Lage des Schlages“, `Plot.lat/lon`); kommt der Umriss später per KML, gilt der Umriss.

### Luftbild unter der Schlagkarte (0.24.0)
Die Schlagkarte (und die kleine Karte auf der Schlagseite) zeigt ein Luftbild unter den Umrissen; Schalter rechts
an der Karte, die Wahl merkt sich der Browser. Der Server holt das Bild für den sichtbaren Ausschnitt (EPSG:4326,
passt ohne Umrechnung zu den Umrissen), speichert es zwischen (`luftbild-cache` neben den Uploads, max. 500 MB,
nicht in der Datensicherung) und liefert es aus – die CSP bleibt bei 'self'. Quelle unter Verwaltung → Hof:
Niedersachsen (LGLN DOP20, Open Data, Voreinstellung), Esri World Imagery oder ein eigener WMS; „Luftbild am
Hof-Standort testen“ zeigt die Antwort des Dienstes.

### Tankbuch-Export (0.23.2)
Admins exportieren das Tankbuch (Tankbuch → Export) für ein Jahr oder einen Zeitraum, optional für eine Maschine,
als PDF (Hofname/Anschrift, Bestand am Anfang/Ende, Lieferungen mit Kosten, Liter/Tankungen/Zählerstände/Verbrauch/
Kosten je Maschine, alle Einträge, Unterschriftszeile) oder als Excel (Übersicht, Je Maschine, Einträge).
Bewusst immer Deutsch (Dokument für Dritte, z. B. Agrardiesel-Erstattung).

### Wochenübersicht und Bundesland (0.23.1)
Auf der Startseite (PC und Handy) die Karte „Diese Woche“: heute (hervorgehoben) und die nächsten 6 Tage mit
eigenen Terminen, Feiertagen, Wartungen und Lieferungen – für alle gleich; Aufgaben stehen in ihrer eigenen Karte.
Mehrtägige Termine erscheinen am ersten Tag und bei „Heute“, solange sie laufen. Bundesland für die Feiertage unter
Verwaltung → Hof (alle 16 Länder, Voreinstellung Niedersachsen, „keine Feiertage“ möglich; Feiertage nur
einzelner Gemeinden fehlen bewusst).

### Kalender mit eigenen Terminen (0.23.0)
Im Kalender stehen jetzt auch Aufgaben mit Termin (Admins alle, sonst eigene und die für alle) und die Feiertage
(Bundesland einstellbar, siehe 0.23.1). Eigene Termine (Mitarbeiter und Admins tragen ein, alle sehen sie): ganztägig oder mit Uhrzeit,
mehrtägig, Wiederholung wöchentlich/monatlich/jährlich, Ort, Notiz, Beteiligte (leer = alle) und feste Arten mit
Farbe (Termin, Frist, Abwesenheit/Urlaub, Prüfung/Kontrolle, Sonstiges). Ändern/löschen: wer eingetragen hat und
Admins. Filter „Nur meine“. Im Abo-Link erscheinen alle Termine (Uhrzeiten in UTC, mehrtägige als Datum).

### Aufgabe mit Schlag (0.22.1)
Aufgaben können sich auf einen Schlag beziehen (neben Maschine, Lieferschein, Lagerplatz). Auf der Schlagseite
stehen die offenen Aufgaben und ein Knopf „Aufgabe“, der den Schlag vorbelegt; Wiederholungen behalten den Schlag.
**Mehrere Schläge** (0.40.4): Mehrfachauswahl mit Suchfeld (`macros.plot_pick`, `Task.plots`, Tabelle `task_plots`); die Aufgabe
erscheint auf jeder ihrer Schlagseiten.

### Kacheln auf der Übersicht (0.22.0)
Die Kacheln am Handy sind einstellbar: Der Admin legt unter Verwaltung → Startseite fest, welche Kacheln jede Rolle
sieht (Mitarbeiter z. B. Scannen und Tanken vorne). Jeder kann über „Anpassen“ Kacheln dazunehmen oder ausblenden;
gespeichert wird nur die Abweichung, Änderungen der Voreinstellung kommen also trotzdem bei allen an.
Reihenfolge = Katalog in `app/tiles.py`. Neue Kacheln: Scannen, Tanken (Füllstand), Kalender, Tankbuch,
Lieferanten, Kunden, Etiketten.

### Logo (0.20.1 / 0.21.0)
Standard-Logo: zwei Biogas-Fermenter mit Gaskuppel, Sonne, Kartoffelacker mit Pflanzenreihen (Entwurf A2).
Eigenes Logo unter Verwaltung → Hof hochladen (PNG/JPEG/WebP): daraus entstehen Kopfzeile, Browser-Tab,
Login-Seite, App-Icons (mit weißem Grund und Rand) und das Bild in Push-Nachrichten; „Standard-Logo verwenden“
setzt zurück. Dateien in `UPLOAD_DIR/branding/` (mitgesichert).

## Noch offen

1. Mehrere Kistengrößen? Das System unterstützt beliebig viele Kistentypen.
2. Lieferschein: Muss ein Pflanzenpass-Etikett mitgedruckt werden oder reicht die Angabe auf dem Lieferschein?
   Gibt es eine Vorlage, wie der Lieferschein aussehen soll?
3. Werden je Fahrzeug mehrere Kunden beliefert (ein Wiegevorgang, mehrere Lieferscheine)?
4. Welche Sensoren genau (Vorschlag folgt mit Baustein B).

Geklärt: WLAN im Lager ist gut (kein Offline-Modus nötig); Zugriff von unterwegs läuft über die eigene Domain mit HTTPS.
