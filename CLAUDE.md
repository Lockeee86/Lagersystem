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
  Auslagern mit Nachrücken, Füllstand, Kisten anlegen). Views rufen nur diese Funktionen auf,
  die Funktionen committen nicht selbst. Jede Änderung an einer Kiste erzeugt eine `Movement`.
- `app/sales.py`: Lieferscheinlogik (anlegen, Kisten verladen über `storage.ship_box`, Mengen, Waage,
  abschließen, Zusammenfassung für Anzeige/PDF); committet wie `storage.py` nicht selbst.
  `app/sales_views.py` (Blueprint `sales`: Lieferscheine, Verladen per Dauer-Scanner, Kunden, Anerkennungen),
  `app/delivery_note.py` (Lieferschein-PDF, bewusst immer Deutsch)
- `app/machines.py`: Maschinenlogik (Zähler, Wartungsintervalle/Fälligkeit, Logbuch, Störungen), committet nicht.
  `app/machine_views.py` (Blueprint `machines`), `app/uploads.py` (Fotos verkleinert nach `UPLOAD_DIR`)
- `app/weather.py`: Wetter über Bright Sky (DWD), Zwischenspeicher im Speicher, Frost und Krautfäule (Hutton);
  in Tests `weather._fetch` ersetzen (kein Netz)
- `app/purchases.py`: Bestellwünsche (Status, Besteller = Admin oder `User.can_order`, zuständig `assignee`,
  Rechte über `PurchaseRequest.can_handle`), committet nicht;
  `app/purchase_views.py` (Blueprint `purchases`). Geldbeträge aus Formularen über `utils.form_money`
- `app/tasks.py`: Aufgaben (Zuteilung, Fälligkeit, Wiederholung `next_due`), committet nicht;
  `app/task_views.py` (Blueprint `tasks`). Makros aus `tasks/_macros.html` mit `with context` importieren.
- `app/fields.py`: Schlagkartei (KML-Import mit Abgleich, Anbaujahre `PlotYear`, Ertrag t/ha), committet nicht;
  `app/fields_views.py` (Blueprint `fields`), `app/static/js/schlagkarte.js` (Umrisse als SVG)
- `app/navigation.py`: Bereiche (Übersicht, Lager, Feld & Hof, Verkauf) mit Unterpunkten und die Verwaltung –
  neue Seiten dort eintragen, dann erscheinen sie in Leiste, Unterleiste und Kacheln. `app/search.py`: Suche
- `app/views.py` (Seiten), `app/admin.py` (Verwaltung, nur Admin), `app/auth.py` (Login, Setup, Passwort),
  `app/api.py` (`/api/health`, `/api/version` für Auto-Reload), `app/labels.py` (QR-Etiketten-PDF)
- `app/transfer.py`: Excel-Vorlage, Import (`run_import` committet nicht – Aufrufer entscheidet: Vorschau = Rollback,
  Übernehmen = Commit nur ohne Fehler) und Export je Kartoffeljahr
- `app/static/js/lager3d.js`: 3D-Ansicht der Lagerkarte (isometrisches SVG aus JSON, das `views.warehouse_map` liefert)
- `app/templates/`: Jinja2, Bootstrap 5. Gemeinsame Makros in `macros.html`
- `app/static/vendor/`: lokal abgelegte Bibliotheken (Bootstrap, Bootstrap Icons, html5-qrcode), **kein CDN**
  (die CSP erlaubt nur `'self'`)
- `migrations/`: Alembic. Der Container führt beim Start `flask db upgrade` aus (`entrypoint.sh`).

## Konventionen

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
