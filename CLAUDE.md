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
- `app/models.py`: Benutzer (Rollen admin/mitarbeiter/betrachter), Lager, Sorte, Kistentyp, Kiste, Bewegung
- `app/storage.py`: **die gesamte Lagerlogik** (Einlagern oben auf den Stapel, Umlagern,
  Auslagern mit Nachrücken, Füllstand, Kisten anlegen). Views rufen nur diese Funktionen auf,
  die Funktionen committen nicht selbst. Jede Änderung an einer Kiste erzeugt eine `Movement`.
- `app/views.py` (Seiten), `app/admin.py` (Verwaltung, nur Admin), `app/auth.py` (Login, Setup, Passwort),
  `app/api.py` (`/api/health`, `/api/version` für Auto-Reload), `app/labels.py` (QR-Etiketten-PDF)
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
