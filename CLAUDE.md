# CLAUDE.md

Hinweise für Claude Code bei der Arbeit an diesem Repository.

## Projekt

Lagersystem für Kartoffelkisten (Flask + PostgreSQL, Docker-Stack für Portainer).
Konzept, Entscheidungen und Phasenplan stehen in `KONZEPT.md`. Bei neuen Features dort nachsehen
und den Phasenplan aktuell halten. Die Oberfläche und alle Texte sind auf Deutsch.

## Befehle

```sh
pip install -r requirements-dev.txt
pytest -n auto                           # Tests (SQLite in-memory, parallel; GitHub Actions `.github/workflows/tests.yml` bei jedem Push)
flask --app wsgi db upgrade              # Migrationen anwenden
flask --app wsgi db migrate -m "..."     # Migration nach Modelländerung erzeugen (immer mit committen)
flask --app wsgi run --debug
docker compose up --build -d
```

## Aufbau

- `app/__init__.py`: `create_app()`, Konfiguration aus Umgebungsvariablen, Security-Header, Setup-Umleitung
- `app/models.py`: Benutzer (Rollen admin/mitarbeiter/betrachter), Lager, Sorte, Schlag (`Plot`), Saatgut-Stufe
  (`SeedClass`), Kistentyp, Kiste (mit Verwendung Saat/Speise und Stufe), Bewegung,
  `Setting` (Schlüssel/Wert: Hofname, Anschrift …; in Templates als `farm` bzw. `app_name`)
- `app/storage.py`: **die gesamte Lagerlogik** (Einlagern oben auf den Stapel, Umlagern,
  Auslagern mit Nachrücken, Füllstand, Kisten anlegen, Ernte-Modus `harvest_box` mit Platzvorschlag
  `suggest_place` – gleiche Ware zusammen; Bedienung in `scan.html`: Schalter + Einstellungen über der Kamera,
  gemerkt im Cookie `ernte`, Routen `main.harvest_settings` / `main.harvest_box`; Kartoffelgrößen `sizes()` /
  `save_sizes()` in `settings` `potato_sizes`, Auswahl über `macros.size_select`; Nachsortieren `main.sort_box`;
  Lagerdauer `age_level()` / `oldest_boxes()` (Schwellen `age_warn_months`/`age_old_months`); Ernte-Tagesbericht
  `harvest_report()` + Push `harvest_report`; Kistenzustand `report_defect()` / `repair_box()` / `retire_box()`,
  `Box.condition`, Seite `main.repairs`; Partien ohne Etikett: `plan_batch()` (`avoid_partial`) / `plan_summary()` / `partial_conflicts()` / `store_batch()` – leere
  Kisten wiederverwenden, fehlende anlegen, Reihe + Platzvorschlag –, `lots()` = gleiche Ware je Reihe, `find_lot()`,
  `pick_from_lot()` (am leichtesten erreichbar zuerst), `take_from_lot()`; Seiten `main.batch_store` `/einlagern`,
  `main.lots_page` `/partien`, Lieferschein `sales.add_lot` / `sales.delivery_add_lot`; Lagerkarte zum Antippen
  `partials/pick_map.html` (Einlagern bzw. `load=true` im Lieferschein → `sales.add_from_stacks` / `sales.delivery_add_map`)). Views rufen nur diese Funktionen auf,
  die Funktionen committen nicht selbst. Jede Änderung an einer Kiste erzeugt eine `Movement`.
- `app/sales.py`: Lieferscheinlogik (anlegen, Kisten verladen über `storage.ship_box`, Mengen, Waage,
  Unterschriften `sign()` / `remove_signature()` / `sign_required()` – unterschrieben = gesperrt,
  abschließen, Zusammenfassung für Anzeige/PDF); committet wie `storage.py` nicht selbst.
  `app/sales_views.py` (Blueprint `sales`: Lieferscheine, Verladen per Dauer-Scanner, Kunden, Anerkennungen),
  `app/delivery_note.py` (Lieferschein-PDF, bewusst immer Deutsch)
- `app/team.py`: Team-Alltag – Übergabe-Notizen (`Handover`, `open_handovers()`), Tagesplan = Ansicht der Aufgaben (es gibt nur `Task`;
  eingeplant = `Task.plan_day` + eine Person, `plan_position`; `plan_for()` inkl. Liegengebliebenem, `open_tasks()` = „Offen“,
  `my_today()` = Startseite, `add_plan_entry()` / `add_open_task()` / `plan_task()` / `unplan_task()`, `copy_plan()` nur Erledigtes,
  Abhaken `toggle_plan_entry()` über `tasks.complete_task`/`reopen_task`, Vorschläge `suggestions()` / `add_suggestion()` =
  Wartungen und Störungen ohne Aufgabe; Reiter Heute/Offen/Alle `team/_macros.html` `task_tabs`), Pflegen: Admin oder `User.can_plan` → `is_planner`, `send_plan()` nach dem Commit), Unterweisungen (`InstructionTopic`/`InstructionRecord`, `status()`,
  `overview()`, `sign_instruction()`), committet nicht; `app/team_views.py` (Blueprint `team`), `app/instruction_pdf.py`
  (Nachweis, immer Deutsch). Werkstatt-Bildschirm (Fernseher, Full HD): `app/screen.py` (`data()` inkl. `biogas.dashboard()`, Schlüssel `settings`
  `display_workshop_token`), Route `team.screen` `/werkstatt/anzeige/<token>` ohne Anmeldung, Vorlage `team/screen.html`,
  Link unter Verwaltung → Hof (`admin.screen_link`). Unterschriften allgemein: `app/signatures.py` (`to_png`, `pdf_image`),
  `static/js/signpad.js` + `macros.sign_modal`
- `app/bulk.py`: Schüttgut-Lager (`BulkStore` = Box/Haufen/Big Bags, eine Ware je Lagerort; `BulkEntry` ein/aus mit
  Waage und Qualität; Bestand `stock()`, Qualität `quality()`, Herkunft `plot_shares()`), committet nicht;
  `app/bulk_views.py` (Blueprint `bulk`). Code `S-001` je Lagerort (`bulk.normalize_code`, QR-Etikett; `/scan` öffnet
  das Einlagern). Verkauf über `sales.add_bulk` (Lieferschein-Position mit `bulk_entry`)
- `app/machines.py`: Maschinenlogik (Zähler, Wartungsintervalle/Fälligkeit, Logbuch, Störungen, Kosten je Jahr
  `costs()` / `cost_overview()`), committet nicht.
  `app/machine_views.py` (Blueprint `machines`), `app/uploads.py` (Fotos verkleinert nach `UPLOAD_DIR`; gelöschte
  `Photo`-Zeilen verlieren ihre Datei nach dem Commit). Fotoleiste an Kiste, Anbaujahr, Schüttlager, Lieferschein:
  `macros.photo_strip` (mit `with context` importieren), Routen `main.photos_add` / `main.photo_delete`.
  Titelbild der Maschine `Machine.image` (+ Vorschau `uploads.thumb_name`), Routen `machines.machine_image(_save)`
- `app/weather.py`: Wetter über Bright Sky (DWD), Zwischenspeicher im Speicher, Frost und Krautfäule (Hutton),
  Niederschlag seit 1. Januar `rain_year()` (Monatswerte zwischengespeichert);
  in Tests `weather._fetch` ersetzen (kein Netz)
- `app/backup.py`: Datensicherung nach `BACKUP_DIR` (pg_dump bzw. SQLite-Kopie, Fotos spiegeln, Aufbewahrung
  `to_delete`, Status in `status.json`). Konsole: `flask backup-now`, `flask backup-restore`
- `app/scheduler.py`: Hintergrund-Thread (aus `gunicorn.conf.py`, `post_worker_init`) für `backup.tick`, `push.daily`
  und `audit.cleanup`
- `app/push.py`: Web Push (VAPID-Schlüssel in `settings`, Themen `models.PUSH_TOPICS`, abgewählt `User.push_off`,
  bei Themen mit `"default"` (z. B. Wetter-Warnungen nur Admins) eingeschaltet `User.push_on`).
  `push.notify()` / die Auslöser (`task_assigned`, `fault_reported`, `purchase_*`) **nach dem Commit** aufrufen.
  Texte als `push.Msg("…", platzhalter=…)` – sie werden je Empfänger übersetzt (`i18n.force_locale`).
  In Tests landen Nachrichten in `push.outbox`. `app/push_views.py`: Abo, Themen, `/sw.js`, `/manifest.webmanifest`
- `app/fuel.py`: Tankbuch (Tanken/Lieferung/Peilung, Füllstand `level(tag)`, Verbrauch `consumption()`), committet nicht;
  `app/fuel_views.py` (Blueprint `fuel`). Tanken an der Maschine: `machine_views.machine_refuel`.
  `app/fuel_report.py`: Bericht für einen Zeitraum als Excel/PDF (immer Deutsch)
- `app/places.py`: Technische Plätze wie in SAP (`TechPlace`, Baum über `parent_id`, Code `TP-001`, optional
  Kartoffellager `warehouse_id` und Rubrik Biogas `biogas` – vererbt sich, `in_biogas`/`can_see()`); Maschinen = Equipments,
  eingebaut über `Machine.place_id` (`install()`). `tree()`, `summary()` (Störungen, fällige Wartungen, Kosten über den
  Unterbaum), `for_warehouse()` (Lagerkarte), `choices()`; committet nicht. Routen in `machine_views.py`
  (`place_list` `/plaetze`, `place_detail` `/platz/<id>`, `place_install`). Arten `kuehlung`/`lueftung` für Lagertechnik.
  Maschinen-Auswahlen immer über `macros.machine_options` (nach Platz gruppiert, `places.group_machines`).
  Anlagen-Standorte sind Plätze, keine Betriebsstätten: `places.place_from_site` (Route `admin.site_to_place`)
- Ersatzteile (`MachinePart`) und Dokumente (`MachineDocument`, `uploads.save_document` → `UPLOAD_DIR/dokumente/`)
  hängen an der Maschine, Routen in `machine_views.py`
- `app/aerial.py`: Luftbild-Proxy für die Schlagkarte (Quellen `SOURCES`, Zwischenspeicher, `_download` in Tests
  ersetzen), Routen `/luftbild` und `/luftbild/test` in `fields_views.py`
- `app/agenda.py`: Kalender – Wartungen, Lieferungen, Aufgaben, Ende laufender Wartezeiten („erntefähig“, `_waiting`),
  Feiertage (`holidays()`) werden berechnet, eigene
  Termine liegen in `CalendarEntry` (Wiederholungen über `occurrences()`), `save_entry()` committet nicht;
  iCal-Ausgabe `ics()`. `app/agenda_views.py` (Blueprint `agenda`, Abo über `User.calendar_token`)
- `app/branding.py`: Logo (Standard aus `static/img/` oder eigenes aus `UPLOAD_DIR/branding/`), in Templates
  `logo_url("logo-64" | "logo-512" | "icon-180" …)`, Auslieferung über `/logo/<name>.png`
- `app/tiles.py`: Schnellzugriff der Übersicht am Handy (die ersten 4 ohne Scannen, `quick()`; Katalog `CATALOG`,
  Voreinstellung je Rolle in `settings` `tiles_<rolle>`, persönliche Abweichung `User.tiles_add`/`tiles_hide`) und
  Bereiche der Startseite (`SECTIONS`, `sections_for()`, `settings` `home_<rolle>`, `User.home_add`/`home_hide`,
  Reihenfolge `User.home_order` / `section_order()`; `dashboard.html` sammelt je Bereich einen Block und gibt sie in
  `show_order` aus). Strompreis-Ampel `biogas.price_strip`
- `app/purchases.py`: Bestellwünsche (Status, Besteller = Admin oder `User.can_order`, zuständig `assignee`,
  Rechte über `PurchaseRequest.can_handle`; „erledigt“ erst mit `invoice_handed_on` = Rechnung übergeben an
  `settings` `invoice_recipient`; Dateien `PurchaseDocument` unter `dokumente/`) und Lieferanten (`Supplier`, Zuordnung über den Namen), committet nicht;
  `app/purchase_views.py` (Blueprint `purchases`). Geldbeträge aus Formularen über `utils.form_money`
- `app/tasks.py`: Aufgaben (Zuteilung, Fälligkeit, Wiederholung `next_due`; mehrere Maschinen `Task.machines` /
  `machine_list` / `set_machines()` – `machine_id` = die erste, für Maschinenseite/Wartung/Störung), committet nicht;
  `app/task_views.py` (Blueprint `tasks`). Makros aus `tasks/_macros.html` mit `with context` importieren.
- `app/fields.py`: Schlagkartei (KML-Import mit Abgleich, Anbaujahre `PlotYear`, Ertrag t/ha), committet nicht;
  `app/fields_views.py` (Blueprint `fields`), `app/static/js/schlagkarte.js` (Umrisse als SVG, Nullpunkt in der Kartenmitte `lon0`/`lat0`; eigener Standort
  `map.locate()` + Knopf `Schlagkarte.locateButton(map, btn, texte)` auf Schlag- und Waldkarte; Hinführen: Option `target`
  + Texte `target`/`dirs`/`here` → Linie, Kasten mit Pfeil/Entfernung/Richtung, Kompass wie auf der Baumseite).
  `app/fielddoc.py`: Myfarm24-Schlagdokumentation (CSV) → Stammdaten, `PlotYear`, Maßnahmen `FieldMeasure`
  (`source="myfarm"` wird beim Re-Import ersetzt), Abgleich über `fields._match`; Anzeige `fields/_doc.html`.
  Wartezeiten: `fielddoc.protection()` / `protection_overview()` (Bemerkung „Wartezeit n Tage/F“, sonst
  `PesticideProduct`), Seite `fields.protection` (`/pflanzenschutz`).
  Fruchtfolgeplan: `fielddoc.parse_plan/apply_rotation` → `PlotYear.planned_crop` (Anzeige über `shown_crop`),
  Seite `fields.rotation_page` (`/fruchtfolge`, `fielddoc.rotation()`, Anbaupause `rotation_breaks()`). Ertragsvergleich `fields.yield_history`
  (`/ertraege`); Feldnotizen `FieldNote` mit Fotos, Standort → Schlag `fields.locate` (`/schlag/ort`).
  Echte Myfarm24-Dateien nie committen – Tests mit erfundenen Daten
- `app/irrigation.py`: Beregnung – RainDancer-Wasserbuch (Excel) `parse_file()` → Vorschau `plan()` → `apply()` (ersetzt
  den Zeitraum der Datei, committet nicht; Routen `admin.irrigation_preview/_apply`), `Well` (genehmigte Jahresmenge),
  `Irrigation` (Einsatz), Auswertung `overview()` / `plot_summary()` / `mm_by_plot_year()` / `alerts()`, Seite
  `fields.irrigation_page`. Testdateien erzeugt `tests/test_irrigation.py` mit xlwt – echte RainDancer-Dateien nie
  committen
- `app/biogas.py`: Rubrik Biogas – Speicher-Kraftwerk-API (nur lesend, `SKVE_API_*` in der Konfiguration; in Tests
  `biogas._get` ersetzen), `sync()` im Zeitplan (einmal am Tag: `ChpDay` je BHKW-Maschine mit `Machine.skve_chp_id`,
  Zähler = `skve_base_counter` + Stunden nach `skve_base_day`; `machines.set_counter` setzt die Basis neu),
  `live()` (Übersicht, Zwischenspeicher, nächster Start `start_label()`; `refresh_live` im Zeitplan, Startseite nur `cached_only`), `dashboard()`,
  `maintenance_forecast()`, Wartungsfenster `maintenance_windows()` / `best_windows()` (Stillstand
  `MaintenanceTask.downtime_hours`), Fahrplan-Güte `flex_quality()` (`ChpDay.ideal_revenue_eur` = dieselben kWh in den
  teuersten Viertelstunden), Verfügbarkeit laut Logbuch `availability()`. Jahresbudget je Einspeisestelle (Höchstbemessungsleistung): `app/feedin.py`
  (`FeedPoint`, `FeedMonth` = Monatsmenge aus der Abrechnung, sonst Fahrplan; `status()`, `price_threshold()`, Push `check()`;
  CSV-Import `parse_csv()`/`apply_import()`, Erlöse `FeedRevenue`/`revenue()`, Budget-Fahrplan `plan()`; Mehrstrom-Rechner
  `extra()` = Kürzung über der HBL nach Clearingstelle (anteilig auf die Stufen `_stages`, Flexprämie, Wartung je kWh
  `maintenance_ct()` aus Logbuch/Bestellungen der BHKW, Substrat optional; Werte `FeedPoint.aw_stage1-3`, `flex_*`,
  `substrate_*` über `save_tariff()`)), Ausfallkosten
  `biogas.loss_per_hour()`, Prüffristen der Anlage = Maschinenart `anlage` (`biogas.plant_inspections()`, `machines.BIOGAS_HIDDEN`),
  Biogas-Seiten mit Reitern (`biogas/_layout.html`, am PC die Unterleiste): `biogas.overview` „Jetzt“ `/biogas`,
  `budget_page` `/biogas/budget`, `maintenance_page` `/biogas/wartung`, `analysis_page` `/biogas/auswertung`,
  `settings_page` `/biogas/einstellungen` (Admin: Einspeisestellen, Monatsmengen, Vergütung, CSV-Import, Display-Link –
  Formulare leiten dorthin zurück). Anzeige an der Anlage (800×480 wie das Stall-Display): `app/plant_display.py`
  (`data()`, `render()` → PNG für Farb-E-Paper mit `epaper`-Helfern, Schlüssel `settings` `biogas_display_token`), Routen
  `biogas.display` `/biogas/anzeige/<token>` (+ `/bild.png`) ohne Anmeldung, Vorlage `biogas/display.html`, Link für Admins
  unten auf der Biogas-Seite (`#anzeige`). Echte Abrechnungszahlen nie committen. SKVE liefert nur den Fahrplan (`PLAN_ONLY`). `app/biogas_views.py` (Blueprint `biogas`). Rubrik je Benutzer abwählbar
  (`User.biogas` → `sees_biogas`, auch für Admins abwählbar; Rundgang der Biogasanlage, BHKW/Strompreis auf der Startseite und
  Push-Gruppe „Biogas“ hängen daran; BHKW-Maschinen ebenso: `machines.visible(query, user)` / `can_see()` in Listen,
  Suche, Startseite, Kalender, Push – neue Maschinen-Abfragen für Benutzer damit filtern). Wartungen kopieren: `machines.copy_tasks`
- `app/rounds.py`: Rundgang – `CheckStation` (Code `R-01`, `normalize_code`, `/scan` erkennt ihn) und `StationCheck`
  (Ergebnis je Prüfpunkt, Fotos über `Photo.check_id`), `record_check()` / `resolve()` / `status()` / `overview()`,
  Push `issue_reported()` und `morning_due()` (aus `push.daily`), committet nicht; `app/rounds_views.py` (Blueprint
  `rounds`). Wochenbericht `biogas.week_report()` / `week_push()` montags aus `push.daily`
- `app/chickens.py`: Rubrik Hühner (nur `User.chickens`; eintragen `keeps_chickens`) – Eier `EggEntry`, Haushalte
  `Household` + Verteilung `EggShare` (Reihenfolge `fair_order()`), Vorrat `stock()`, Herde `FlockEvent` (`flock_size()`), Futter `FeedEntry`, tägliche Fütterung `Feeding` (`toggle_feeding()`, Auswertung `feeding_effect()`),
  Stallbuch `FlockTreatment` (Wartezeit `blocked()`), Stall-Display 800×480 `display()` (Route `chickens.display`
  `/huehner/anzeige/<token>` ohne Anmeldung, Schlüssel `settings` `coop_display_token`, Vorlage `chickens/display.html`; als PNG für Farb-E-Paper reTerminal E1002
  `chickens.display_image` `…/bild.png`, gezeichnet mit Pillow in `app/epaper.py` – nur die 6 Panel-Farben, Schrift DejaVu
  aus `fonts-dejavu-core` im Dockerfile, ESPHome-Anleitung `docs/reterminal-e1002.md`), Monate `months()`, Auswertung aller Jahre `analysis()`, `summary()` für Startseite; Stallkontrolle =
  Rundgang mit `CheckStation.area == "huehner"`, Aufgaben `Task.area`; Push `evening_check()` aus `push.daily`
  (Sonnenuntergang `sunset()`), committet nicht; `app/chickens_views.py` (Blueprint `chickens`). Alte Legedaten aus Excel:
  `app/egg_import.py` (`parse_file` → `plan` → `apply`, `EggEntry.imported` zählt nicht zum Vorrat; echte Listen nie committen). Rubriken mit
  `"show"` in `navigation.AREAS` bzw. `tiles.SECTIONS` erscheinen nur für passende Benutzer.
  Benutzer „nur eine Rubrik“ (`User.chickens_only` / `forest_only` → `only_area` "huehner"/"wald", `restricted`,
  `only_chickens`/`only_forest`; im Formular Auswahl `only`): `app/area_only.py` (`AREAS` je Rubrik mit Startseite,
  Blueprint-Präfix, Leisten-Platz, Push-Gruppe; `allowed()` im `before_request`, `filter_tasks()`); Seiten des
  Rubrik-Blueprints gehen automatisch, andere dort freigeben. In Templates `current_user.restricted` / `only_area`
- `app/forest.py`: Rubrik Bestattungswald (nur `User.forest` → `sees_forest`; Name `settings` `forest_name`, `name()`) –
  Bäume `ForestTree` (Feld + Nummer „A 147“, `key` „A147“ für Adressen `/wald/baum/<key>`, `name`, Code `W-A147`,
  `find_tree()` für jede Eingabe, `/scan` erkennt den Code; Art `flaeche` = Waldruhefläche mit Nummer 0; `category`,
  `trunk_cm`) mit Plätzen `ForestPlace` (Stand frei/reserviert/belegt, `person` = vorgesehen bei Vorsorge), `create_trees()` / `save_tree()` / CSV `parse_trees_csv()` + `apply_trees()`,
  `tree_rows(section, only_free, species, kinds, q)` (Suche `search_trees()` – „27“ ohne Feld findet alle Felder,
  Filterwerte `species_list()` / `kinds_in_use()`; `find()` öffnet bei einem Treffer den Baum, sonst die Liste), `overview()`, Plätze in Ringen um den Baum `place_wheel()` / `wheel_position()` (innen wie eine
  analoge Uhr, 12 = Norden, 3 = Osten; 13–24 im äußeren Ring um 15° versetzt – 13 über der Lücke zwischen 12 und 1;
  unter 12 Plätzen ein Ring ab Norden; maßstäblich `WHEEL_FIRST_M`/`WHEEL_WIDTH_M`: innen 2–2,5 m, außen 2,5–3 m,
  Stamm aus `trunk_cm`; SVG in `forest/tree.html`, Antippen kreuzt `#pl<n>` an; Knopf „Kompass“ am Handy dreht
  `#wheelRot` nach dem Kompass – `deviceorientationabsolute` bzw. iPhone `webkitCompassHeading` nach
  `requestPermission`, Sensoren in der `Permissions-Policy` erlaubt), `map_data(species, kinds, only_free, tree_id)` (Schlagkarte, id = Baum-id → `forest.tree_by_id`; Karte `/wald/karte` mit
  Filter Art/Baumart/frei, `?baum=<id>` = nur dieser Baum – „Lage öffnen“ auf der Baumseite, Option `fitItems`; `&hin=1` = „Hinführen“ startet den
  Standort gleich); Fotos am Baum `ForestTree.photos` (`Photo.forest_tree_id`, Fotoleiste `kind="baum"`, für „nur Wald“ in
  `area_only.allowed` freigegeben); Baumauswahl-Termin `ForestSelection` = Anfang
  von allem (`save_selection()` → Aufgabe „Baumauswahl“ für den Begleiter `guide`, `choose_tree()` merkt Baum + Plätze vor
  (`free_for()`, ohne Nummern die ersten freien), `cancel_selection()` / `reopen_selection()`, `selections()`,
  `next_place()`; Vertrag/Beisetzung aus der Auswahl über `?auswahl=` bzw. Formularfeld `selection_id`); Verträge `ForestContract` (nur Admin,
  `save_contract()` mit `ends_on`/`holder_birthday`, Vorgabe `default_end()` aus `settings` `forest_end` /
  `forest_single_years`, `next_contract_number()`). Übernahme aus dem Altsystem: `app/forest_import.py` (Tabellenexport
  Excel-XML/.xlsx oder ZIP mit Tabellen + Shapefiles (Punkte = Bäume, Polygone = Flächen → `ForestTree.outline`,
  `outline_points`, Karte und kleine Karte auf der Baumseite) + DXF: `read_upload()` → `apply()` / `apply_points()` /
  `apply_shapes()`, UTM → `utm_to_latlon()`; Grundkarte aus CAD `parse_dxf()` → `ForestShape` (Art am Ebenen-Namen
  `shape_kind()`, Farben `models.FOREST_SHAPE_KINDS`), auf der Karte über `forest.map_background()` →
  Schlagkarte-Option `background` (nicht klickbar, `minSpan` für kleine Karten), Route `forest.legacy_import`; echte Exporte nie committen – Tests erzeugen erfundene Dateien), Beisetzungen `ForestBurial` (`save_burial()` legt die Aufgaben
  „Grab ausheben“/„Grab schließen“ mit `Task.area = "wald"` an; Kirchenglocken `ForestBurial.bells`, nachträglich
  `set_bells()` – steht in Aufgaben und Kalender; zuständig `ForestBurial.grave_person` – bei Abwesenheit laut Kalender
  (`agenda.absent()`) `grave_deputy` (`_grave_assignees()`); Standard `grave_team()` / `save_grave_team()` in `settings`
  `forest_grave_person`/`forest_grave_deputy`, im Formular änderbar → `team=`; nach Kalender-Änderungen und morgens
  `push.grave_refresh()` → `refresh_grave_tasks()`), Kalender `calendar_events()`, Push `storm_check()` aus
  `push.daily`; committet nicht. `app/forest_views.py` (Blueprint `forest`, `/wald…`, Vorlagen `forest/` mit
  `_layout.html`), Baumkontrolle = Rundgang mit `CheckStation.area == "wald"` (`STATION_SUGGESTIONS`)
- `app/assistant.py`: Daten-Agent „Fragen“ (nur Admin, `/fragen`, Blueprint `assistant` in `app/assistant_views.py`) – Claude
  (`MODEL`, Anthropic-SDK, Schlüssel `ANTHROPIC_API_KEY` aus der Umgebung, ohne Schlüssel aus) mit Werkzeugen **nur zum Lesen**
  (`TOOLS`: lager, ertraege, biogas, maschinen, wald, huehner – nur Zahlen, nie Namen/Anschriften/Kunden/Verträge/Haushalte;
  `call_tool()` rollt danach zurück), eigene Schleife `run()` im Hintergrund-Thread (`start()`, in Tests sofort), Verlauf
  `AssistantQuery` (Werkzeuge, Tokens, `cost_usd`, Fehler als Kennung `ERRORS`), Monatslimit `settings` `assistant_limit_usd`,
  Antwort-Markdown `render_markdown()`. In Tests `assistant._client` ersetzen (kein Netz). Neue Werkzeuge: Personen weglassen und
  `tests/test_assistant.py` (Datenschutz-Test) erweitern
- `app/sensors.py`: Rubrik Sensoren – alle Meshtastic-Knoten (`MeshNode`, `purpose` coop/coop_top/climate/ignore,
  leer = neu), `record()` aus `api.meshtastic` (`/api/meshtastic`, jedes Paket der Brücke) reicht an `coop.record` /
  `climate.record` weiter, `assign()` / `add()` / `delete()`, Klappe verkehrt herum `door_settings()`, Hof-Kanal `channel()` / `save_channel()`
  (`settings` `mesh_*`, nicht im Protokoll) → Kanal-Link/QR der Meshtastic-App `channel_url()` (ChannelSet als Protobuf
  von Hand), committet nicht;
  `app/sensors_views.py` (Blueprint `sensors`, `/sensoren`, auch Schlüssel für die Brücke)
- `app/coop.py`: Stall-Sensor (Meshtastic) – `record()` (auch direkt `/api/huehner/sensor` für eigenes JSON), Schlüssel
  `new_token()`/`check_token()`, `status()`, `history()`, CO₂ `co2_info()`/`co2_history()` (Grenze `CO2_HIGH`), Warnungen `alerts()` aus `chickens.evening_check`;
  Brücke MQTT → App in `bridge/` (eigener Container, Compose-Profil `huehner`, Anleitung `bridge/README.md`)
- `app/climate.py`: Lagerklima (Meshtastic-Knoten mit SCD41) – `ClimateSensor` (Knoten → Lager/Reihe), `ClimateReading`,
  `record()` aus `api.climate_sensor` (`/api/lager/klima`), `status()` mit Grenzwerten `limits()` (`Warehouse.climate_*`),
  `series()` (24h/7d/30d/Saison), `important()` für „Jetzt wichtig“, Push `check()` aus `push.daily`, committet nicht;
  `app/climate_views.py` (Blueprint `climate`, Seite Lager → Klima), Makros `climate/_macros.html` (auch Lagerkarte)
- `app/sites.py`: Betriebsstätten (`Site`; `site_id` an Schlag, Lager, Schüttlager, Maschine, Lieferschein, Tank in
  `settings` `tank_site`), Absender fürs PDF `sender()`, Filter „Betrieb“ in Listen `chosen()`/`apply()`/`keep()`
  (`?betrieb=`, gemerkt im Cookie). Makros `macros.site_filter()` / `site_select()` erscheinen nur, wenn es
  Betriebsstätten gibt; committet nicht
- `app/audit.py`: Änderungsprotokoll `AuditEntry` – Session-Hook `before_flush` erfasst jedes Ändern/Löschen mit
  Benutzer und vorher/nachher (nicht: Neuanlagen, Kisten-Platz/-Inhalt = `Movement`, persönliche Einstellungen,
  interne `settings`). Massenimporte in `with audit.paused():` und danach `audit.note(...)`. Massen-`query.update()`
  läuft am Protokoll vorbei – lieber einzeln ändern. Seite `admin.audit_log` (`/admin/protokoll`), Aufräumen
  `audit.cleanup` im Zeitplan
- `app/navigation.py`: Bereiche (Übersicht, Lager, Feld & Hof, Verkauf) mit Unterpunkten und die Verwaltung –
  neue Seiten dort eintragen, dann erscheinen sie in Leiste, Unterleiste (PC) und auf den Bereichsseiten (Handy,
  `HUBS`, `hub()`, Route `main.area` = `/bereich/<key>`; Stand je Unterpunkt in `views._status`; „Lager“ ist die
  eigene Übersicht `main.storage_home` mit Kartoffellager und Schüttlager). `app/search.py`: Suche
- Handy: untere Leiste in `base.html` (`mobile_tab`, Zurück-Link `mobile_crumb`; die zwei freien Plätze aus
  `app/tabbar.py`: `CHOICES`, Rolle `settings` `tabbar_<rolle>`, eigene Wahl `User.tabbar`; runder Knopf in der Mitte
  `CENTER` Einlagern (Standard) / Scannen, `center()`, eigene Wahl `User.tab_center`, Betrachter/nur Hühner immer Scannen, nur Bestattungswald Waldkarte (Standard) oder Scannen – `default_center()`), Startseite mit „Jetzt wichtig“
  (`views._important`, ohne Aufgaben – die haben einen eigenen Block) und Schnellzugriff (`tiles.quick`). Listen-Filter am Handy über `macros.filter_toggle` /
  `filter_chips` (Auswahlfelder in `<div class="collapse filter-more" id="…"><div class="row g-2">`).
  Keine Links in Links (z. B. `location_badge(box, link=false)` in Listenzeilen)
  „Neu …“-Formulare unter langen Listen über `macros.fold_card(id, icon, titel, open, pc_open)` (am Handy oben eingeklappt,
  rechte Spalte `order-first order-lg-last`; `#id` in der Adresse klappt auf – `app.js`). Untere Leiste: `overscroll-behavior-y: none`,
  `--vv-shift` wird beim Nachfedern nicht neu berechnet
- `app/speed.py`: statische Dateien mit `?v=` (automatisch über `url_for`) ein Jahr zwischenspeichern, Text mit gzip packen.
  Zwischenspeicher je Seitenaufruf `utils.request_cache()` (nur GET). Neue Listen mit Beziehungen gleich mit
  `selectinload`/`joinedload` laden statt in Schleifen nachzufragen
- `app/views.py` (Seiten), `app/admin.py` (Verwaltung, nur Admin), `app/auth.py` (Login, Setup, Passwort),
  `app/api.py` (`/api/health`, `/api/version` für Auto-Reload), `app/labels.py` (QR-Etiketten-PDF)
- `app/transfer.py`: Excel-Vorlage, Import (`run_import` committet nicht – Aufrufer entscheidet: Vorschau = Rollback,
  Übernehmen = Commit nur ohne Fehler) und Export je Kartoffeljahr
- `app/static/js/mapzoom.js`: Lagerkarte am Handy/Tablet zoomen und verschieben (zwei Finger); ein Stapel klappt dort
  unten als Blatt auf (`#stackSheet`, Offcanvas), am PC als Fenster
- `app/static/js/lager3d.js`: 3D-Ansicht der Lagerkarte (isometrisches SVG aus JSON, das `views.warehouse_map` liefert)
- `app/templates/`: Jinja2, Bootstrap 5. Gemeinsame Makros in `macros.html` (Mehrfachauswahl `multi_pick`: Schläge `plot_pick`,
  Maschinen `machine_pick` – Einträge brauchen `pick_label`)
- Glas-Design: `app/backgrounds.py` (Hintergrund je Benutzer `User.background` / `bg_strength`, „auto“ nach Tageszeit,
  „klassisch“ = Standard für alle, hell/dunkel über `User.theme_mode` – „auto“ nach Sonnenstand `theme()`, im Template
  `classic_theme`, Route `auth.theme_save`), `static/css/glass.css` (nur geladen bei `html[data-bg]`, überschreibt die
  Farbvariablen aus `style.css`; Glasflächen über `--glass*`), Hintergrund-Ebene `.app-backdrop` in `base.html`,
  Auswahl unter Mein Konto (`auth.background_save`). Neue Bausteine mit den Variablen (`--surface`, `--line` …) bauen,
  dann passen sie in beiden Designs
- `app/static/vendor/`: lokal abgelegte Bibliotheken (Bootstrap, Bootstrap Icons, html5-qrcode, Schrift Figtree), **kein CDN**
  (die CSP erlaubt nur `'self'`)
- `migrations/`: Alembic. Der Container führt beim Start `flask db upgrade` aus (`entrypoint.sh`).

## Konventionen

- **Keine echten Hofdaten im Repo** (Tests, Doku, Kommentare, Beispiele): keine echten Schlagnamen, Schlagnummern,
  Flächen, FLIK/USchlag-IDs, Koordinaten, Personen, Hof-/Betriebsnamen oder Adressen aus Dateien des Nutzers.
  Immer erfundene Daten verwenden. Vor jedem Commit danach suchen und gefundene echte Daten wieder entfernen.

- Positionen: `row`, `slot`, `level` sind 1-basiert. Anzeige über `Warehouse.location_label()` → `C-07-3`
- Kisten-Codes `K-0001`; Benutzereingaben immer über `storage.normalize_code()`
- Zeitstempel in UTC speichern, Anzeige über die Jinja-Filter `|datetime` / `|date` (Europe/Berlin)
- Rechte über `@editor_required` / `@admin_required` aus `app/utils.py`
- Alle POST-Formulare brauchen `csrf_token`
- Login-Sperre liegt im Speicher, daher Gunicorn mit **einem** Worker (Threads sind ok)
- **Mehrsprachig (de/pl)** über `app/i18n.py`: jeder sichtbare Text als `_("Deutscher Text", platzhalter=...)`
  (in Python und Jinja), Plural mit `ngettext(...)`, Konstanten mit `N_()` markieren und bei der Anzeige
  mit `_()` übersetzen. Polnisch in `app/translations/pl.py` ergänzen; `tests/test_i18n.py` schlägt fehl,
  wenn eine Übersetzung fehlt. Keine fertigen Sätze in der DB speichern (siehe `Movement.detail`).
  In Jinja nie `_` als Variablennamen verwenden (überdeckt die Übersetzungsfunktion).
- `Movement` speichert eine Momentaufnahme des Kisteninhalts (`storage._log` erst **nach** der Änderung aufrufen).
  Sorten/Schläge, die im Verlauf vorkommen, nur deaktivieren, nie löschen.
