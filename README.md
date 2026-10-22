# Lagersystem

Webbasiertes Lagersystem für Kartoffelkisten: QR-Codes an jeder Kiste, Lagerkarte mit
Stapelansicht, Füllstände, Bestände je Sorte und ein lückenloser Verlauf. Läuft als
Docker-Stack (z. B. in Portainer) und ist fürs Handy optimiert.

Konzept und geplante Ausbaustufen: [KONZEPT.md](KONZEPT.md)

## Funktionen (Phase 1)

- **Lagerkarte** mit drei umschaltbaren Ansichten (die Wahl wird je Benutzer gemerkt):
  **Karte** (Draufsicht mit Stapelhöhe und Sortenfarbe), **3D** (isometrisch, drehbar, zoombar) und
  **Liste** (Reihen mit Zahlen und Sortenkürzeln, auch ohne Farben lesbar). Filter nach Sorte oder
  freien Plätzen, Antippen öffnet die Stapelansicht
- **Kisten** mit fester Nummer (`K-0001` …) und QR-Code; Sorte, Erntedatum, Schlag (Auswahlliste), Sortierung,
  Füllstand (voll / ¾ / ½ / ¼ / leer), Notiz
- **Einlagern, Umlagern, Auslagern**. Kisten kommen immer oben auf den Stapel (max. Höhe einstellbar).
  Wird eine Kiste aus der Mitte genommen, zeigt das System „blockiert durch …“, und die
  Kisten darüber rutschen nach.
- **Scannen** mit der Handykamera direkt im Browser (benötigt HTTPS) oder Code von Hand eingeben
- **Übersicht**: geschätzter Bestand je Sorte (kg/t), Belegung, letzte Bewegungen
- **Verlauf** aller Buchungen mit Benutzer und Zeitpunkt
- **QR-Etiketten als PDF** (A4: 3 × 8, 2 × 4 oder 2 × 2 große Schilder)
- **Benutzer & Rollen**: Admin, Mitarbeiter, Betrachter
- **Sprachen: Deutsch und Polnisch**, je Benutzer einstellbar (Einstellungen, Benutzermenü oder Login-Seite)
- Hell/Dunkel-Modus, Seiten aktualisieren sich automatisch, wenn jemand anderes bucht

## Installation in Portainer

1. **Stacks → Add stack → Repository**
   - Repository URL: `https://github.com/Lockeee86/Lagersystem`
   - Reference: `refs/heads/main`
   - Compose path: `docker-compose.yml`
2. Unter **Environment variables** eintragen (siehe `.env.example`):

   | Variable | Beispiel | Hinweis |
   |---|---|---|
   | `POSTGRES_PASSWORD` | langes Zufallspasswort | **Pflicht** |
   | `SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_hex(32))"` | **Pflicht**, sonst werden alle bei jedem Neustart abgemeldet |
   | `WEB_PORT` | `5050` | Port auf dem Docker-Host. Muss frei sein, sonst bricht der Deploy mit „port is already allocated“ ab |
   | `COOKIE_SECURE` | `false` / `true` | `true`, sobald nur noch über HTTPS zugegriffen wird |
   | `TRUSTED_PROXIES` | `1` | Anzahl Reverse Proxies vor der App, `0` ohne Proxy |
   | `TZ` | `Europe/Berlin` | Zeitzone |

3. **Deploy the stack**, danach `http://<server>:5050` öffnen
4. Die **Ersteinrichtung** startet automatisch: erstes Admin-Konto und Lagermaße angeben
   (Vorgabe: 10 Reihen × 15 Plätze × 5 Ebenen) und das Gewicht einer vollen Kiste.
5. Danach: **Sorten und Schläge anlegen → Kisten anlegen → Etiketten drucken → loslegen**

Beim Start spielt der Container automatisch alle Datenbank-Migrationen ein. Updates gehen
deshalb einfach per **Pull and redeploy**, die Daten bleiben im Volume `postgres_data` erhalten.

### Zugriff von unterwegs / Kamera-Scan

Der Kamera-Scan im Browser funktioniert nur über **HTTPS**. Möglichkeiten:

- **Reverse Proxy** (z. B. Nginx Proxy Manager, Traefik, Caddy) mit Let's-Encrypt-Zertifikat,
  dann `COOKIE_SECURE=true` und `TRUSTED_PROXIES=1`
- **Cloudflare Tunnel** (ohne Portfreigabe), ebenfalls `COOKIE_SECURE=true`
- **VPN** (WireGuard, Tailscale, Fritzbox). Für die Kamera braucht es trotzdem HTTPS,
  z. B. über `tailscale serve`.

Ohne HTTPS funktioniert alles außer der Kamera: Den Code gibst du dann auf der Scan-Seite von Hand ein.

### Sicherheit

- Passwörter werden gehasht gespeichert, jedes Formular ist gegen CSRF geschützt
- Login-Sperre nach 5 Fehlversuchen (15 Minuten, je IP und Benutzername)
- Passwortänderung oder Sperren eines Benutzers meldet ihn auf allen Geräten ab
- Alle Frontend-Bibliotheken liegen lokal im Projekt, es werden keine externen CDNs geladen

### Notfall: Passwort vergessen

In Portainer beim Container `lagersystem-web` auf **Console** gehen (`/bin/sh`) und eingeben:

```sh
flask --app wsgi reset-password <benutzername>
# oder einen neuen Admin anlegen:
flask --app wsgi create-admin <benutzername>
```

### Backup

```sh
docker exec lagersystem-db pg_dump -U lager lager > lager-backup.sql
```

## Entwicklung

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
flask --app wsgi db upgrade      # legt lagersystem.db (SQLite) an
flask --app wsgi run --debug
pytest
```

Nach Änderungen an `app/models.py` eine Migration erzeugen und mit committen:

```sh
flask --app wsgi db migrate -m "Beschreibung"
```
