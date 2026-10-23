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

### Baustein D – Schlagkartei & Ertrag (umgesetzt in 0.13.0)
Umsetzung: Import der Schlagkartei aus Myfarm24 als KML/KMZ (Verwaltung → Schläge) mit Vorschau. Übernommen
werden Name, Schlagnummer, Fläche (SchlagHa, 4 Nachkommastellen), Bodenart, Rotes Gebiet, Umriss, Kultur
(Anbaujahr wählbar) und Vorfrucht (→ Kultur des Vorjahres, nur wenn dort leer). Abgleich beim erneuten
Import: Nummer + Name → Name (Schlag ohne Nummer) → gleicher Umriss (umbenannt) → eindeutige Nummer.
Schlagnummern und -namen sind in Myfarm24 nicht eindeutig (z. B. zweimal Nr. 7, zweimal „Obere Wiese“);
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
