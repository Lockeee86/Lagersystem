# CLAUDE.md

Hinweise für Claude Code bei der Arbeit an diesem Repository.

## Projekt

Lagersystem für Kartoffelkisten (Flask + PostgreSQL, Docker-Stack für Portainer).
Konzept, Entscheidungen und Phasenplan stehen in `KONZEPT.md`. Bei neuen Features dort nachsehen
und den Phasenplan aktuell halten. Die Oberfläche und alle Texte sind auf Deutsch.

## Befehle

```sh
pip install -r requirements-dev.txt
pytest                                   # Tests (SQLite in-memory)
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
  `Box.condition`, Seite `main.repairs`). Views rufen nur diese Funktionen auf,
  die Funktionen committen nicht selbst. Jede Änderung an einer Kiste erzeugt eine `Movement`.
- `app/sales.py`: Lieferscheinlogik (anlegen, Kisten verladen über `storage.ship_box`, Mengen, Waage,
  Unterschriften `sign()` / `remove_signature()` / `sign_required()` – unterschrieben = gesperrt,
  abschließen, Zusammenfassung für Anzeige/PDF); committet wie `storage.py` nicht selbst.
  `app/sales_views.py` (Blueprint `sales`: Lieferscheine, Verladen per Dauer-Scanner, Kunden, Anerkennungen),
  `app/delivery_note.py` (Lieferschein-PDF, bewusst immer Deutsch)
- `app/team.py`: Team-Alltag – Übergabe-Notizen (`Handover`, `open_handovers()`), Tagesplan (`PlanEntry`, `plan_for()`,
  `copy_plan()`, Abhaken `toggle_plan_entry()`, Unerledigtes `carry_over()`, Pflegen: Admin oder `User.can_plan` → `is_planner`, `send_plan()` nach dem Commit), Unterweisungen (`InstructionTopic`/`InstructionRecord`, `status()`,
  `overview()`, `sign_instruction()`), committet nicht; `app/team_views.py` (Blueprint `team`), `app/instruction_pdf.py`
  (Nachweis, immer Deutsch). Unterschriften allgemein: `app/signatures.py` (`to_png`, `pdf_image`),
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
  Rechte über `PurchaseRequest.can_handle`) und Lieferanten (`Supplier`, Zuordnung über den Namen), committet nicht;
  `app/purchase_views.py` (Blueprint `purchases`). Geldbeträge aus Formularen über `utils.form_money`
- `app/tasks.py`: Aufgaben (Zuteilung, Fälligkeit, Wiederholung `next_due`), committet nicht;
  `app/task_views.py` (Blueprint `tasks`). Makros aus `tasks/_macros.html` mit `with context` importieren.
- `app/fields.py`: Schlagkartei (KML-Import mit Abgleich, Anbaujahre `PlotYear`, Ertrag t/ha), committet nicht;
  `app/fields_views.py` (Blueprint `fields`), `app/static/js/schlagkarte.js` (Umrisse als SVG).
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
  `MaintenanceTask.downtime_hours`). `app/biogas_views.py` (Blueprint `biogas`). Wartungen kopieren: `machines.copy_tasks`
- `app/rounds.py`: Rundgang – `CheckStation` (Code `R-01`, `normalize_code`, `/scan` erkennt ihn) und `StationCheck`
  (Ergebnis je Prüfpunkt, Fotos über `Photo.check_id`), `record_check()` / `resolve()` / `status()` / `overview()`,
  Push `issue_reported()` und `morning_due()` (aus `push.daily`), committet nicht; `app/rounds_views.py` (Blueprint
  `rounds`). Wochenbericht `biogas.week_report()` / `week_push()` montags aus `push.daily`
- `app/chickens.py`: Rubrik Hühner (nur `User.chickens`; eintragen `keeps_chickens`) – Eier `EggEntry`, Haushalte
  `Household` + Verteilung `EggShare` (Reihenfolge `fair_order()`), Vorrat `stock()`, Herde `FlockEvent` (`flock_size()`), Futter `FeedEntry`,
  Stallbuch `FlockTreatment` (Wartezeit `blocked()`), Monate `months()`, `summary()` für Startseite; Stallkontrolle =
  Rundgang mit `CheckStation.area == "huehner"`, Aufgaben `Task.area`; Push `evening_check()` aus `push.daily`
  (Sonnenuntergang `sunset()`), committet nicht; `app/chickens_views.py` (Blueprint `chickens`). Rubriken mit
  `"show"` in `navigation.AREAS` bzw. `tiles.SECTIONS` erscheinen nur für passende Benutzer
- `app/coop.py`: Stall-Sensor (Meshtastic) – `record()` aus `api.coop_sensor` (`/api/huehner/sensor`, Schlüssel
  `new_token()`/`check_token()`), Knoten-IDs `nodes()`/`save_nodes()` (die Brücke holt sie mit Lager-Knoten über
  `/api/meshtastic/knoten`), `status()`, `history()`, Warnungen `alerts()` aus `chickens.evening_check`;
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
- Handy: untere Leiste in `base.html` (`mobile_tab`, Zurück-Link `mobile_crumb`), Startseite mit „Jetzt wichtig“
  (`views._important`, ohne Aufgaben – die haben einen eigenen Block) und Schnellzugriff (`tiles.quick`). Listen-Filter am Handy über `macros.filter_toggle` /
  `filter_chips` (Auswahlfelder in `<div class="collapse filter-more" id="…"><div class="row g-2">`).
  Keine Links in Links (z. B. `location_badge(box, link=false)` in Listenzeilen)
- `app/views.py` (Seiten), `app/admin.py` (Verwaltung, nur Admin), `app/auth.py` (Login, Setup, Passwort),
  `app/api.py` (`/api/health`, `/api/version` für Auto-Reload), `app/labels.py` (QR-Etiketten-PDF)
- `app/transfer.py`: Excel-Vorlage, Import (`run_import` committet nicht – Aufrufer entscheidet: Vorschau = Rollback,
  Übernehmen = Commit nur ohne Fehler) und Export je Kartoffeljahr
- `app/static/js/mapzoom.js`: Lagerkarte am Handy/Tablet zoomen und verschieben (zwei Finger); ein Stapel klappt dort
  unten als Blatt auf (`#stackSheet`, Offcanvas), am PC als Fenster
- `app/static/js/lager3d.js`: 3D-Ansicht der Lagerkarte (isometrisches SVG aus JSON, das `views.warehouse_map` liefert)
- `app/templates/`: Jinja2, Bootstrap 5. Gemeinsame Makros in `macros.html` (Mehrfachauswahl Schläge: `plot_pick`)
- `app/static/vendor/`: lokal abgelegte Bibliotheken (Bootstrap, Bootstrap Icons, html5-qrcode), **kein CDN**
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
