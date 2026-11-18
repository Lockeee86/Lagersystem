"""Datensicherung: Datenbank-Abzug (pg_dump bzw. SQLite-Kopie) und Fotos/Dokumente nach `BACKUP_DIR`.

Aufbau im Sicherungsordner:
    db/lager-2026-10-23_0300.dump   Datenbank (PostgreSQL: pg_dump --format=custom)
    dateien/…                       Spiegel von UPLOAD_DIR (nur neue/geänderte Dateien werden kopiert)
    pakete/lager-paket-….zip        Komplettpaket zum Herunterladen (Datenbank + Fotos/Dokumente + manifest.json)
    hochgeladen/…                   zum Zurückspielen hochgeladene Datei (wird danach gelöscht)
    einstellungen.json              Einstellungen der Seite (Uhrzeit, Aufbewahrung, zweites Ziel) – liegen bewusst
                                    hier und nicht in der Datenbank, damit sie ein Zurückspielen überstehen
    status.json                     letzte Läufe (für die Verwaltungsseite)

Aufbewahrung: alle Stände der letzten 14 Tage, dazu der erste Stand jedes Monats für 12 Monate (einstellbar).
Zweites Ziel (optional): Netzlaufwerk über SMB (NAS, Windows-Freigabe – ohne Einbinden in den Container) oder ein
eingebundener Ordner. Nach jeder Sicherung wird es abgeglichen, mit derselben Aufbewahrung.
Der nächtliche Lauf startet aus dem Zeitplan (`scheduler.py`); längere Arbeiten laufen im Hintergrund (`start()`).
"""
import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import date, datetime, timedelta, UTC
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import make_url

from .i18n import N_, gettext as _

KEEP_DAYS = 14
KEEP_MONTHS = 12
STALE_AFTER = timedelta(hours=36)        # Warnung, wenn so lange keine erfolgreiche Sicherung
NAME_RE = re.compile(r"^lager-(\d{4})-(\d{2})-(\d{2})_(\d{4})(\d{2})?\.(dump|sqlite)$")   # Sekunden nur bei zwei Ständen in einer Minute
PACKAGE_RE = re.compile(r"^lager-paket-\d{4}-\d{2}-\d{2}_\d{4}\.zip$")
UPLOAD_RE = re.compile(r"^upload-\d{8}-\d{6}\.(dump|sqlite|zip)$")
CONFIG_FILE = "einstellungen.json"
TARGETS = {"": N_("Nur im Sicherungsordner"), "smb": N_("Netzlaufwerk (SMB – NAS, Windows-Freigabe)"),
           "folder": N_("Eingebundener Ordner")}
CONFIG_DEFAULTS = {"hour": None, "keep_days": KEEP_DAYS, "keep_months": KEEP_MONTHS, "target": "",
                   "smb_server": "", "smb_share": "", "smb_path": "lagersystem", "smb_user": "", "smb_domain": "",
                   "smb_password": "", "folder": ""}

_lock = threading.Lock()


class BackupError(Exception):
    pass


def backup_dir():
    return current_app.config.get("BACKUP_DIR") or None


def _tz():
    return ZoneInfo(current_app.config.get("TIMEZONE", "Europe/Berlin"))


def _stamp():
    return datetime.now(_tz()).strftime("%Y-%m-%d_%H%M")


def is_running():
    return _lock.locked()


# --- Einstellungen ------------------------------------------------------------

def _fernet():
    from cryptography.fernet import Fernet
    key = hashlib.sha256((current_app.config["SECRET_KEY"] + ":datensicherung").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _read_config():
    base = backup_dir()
    data = {}
    if base:
        try:
            with open(os.path.join(base, CONFIG_FILE)) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
    return {key: data.get(key, default) for key, default in CONFIG_DEFAULTS.items()}


def settings():
    """Einstellungen ohne Passwort (dafür `has_password`) – für die Seite."""
    cfg = _read_config()
    cfg["has_password"] = bool(cfg.pop("smb_password"))
    cfg["hour"] = hour()
    return cfg


def hour():
    value = _read_config().get("hour")
    return int(value) if value is not None else int(current_app.config.get("BACKUP_HOUR", 3))


def _password(cfg):
    if not cfg.get("smb_password"):
        return ""
    try:
        return _fernet().decrypt(cfg["smb_password"].encode()).decode()
    except Exception:                                 # SECRET_KEY geändert → Passwort neu eingeben
        return ""


def save_settings(values):
    """Einstellungen prüfen und speichern. `values`: hour, keep_days, keep_months, target, smb_*, folder;
    `smb_password` leer = bisheriges behalten, `clear_password` = löschen."""
    base = backup_dir()
    if not base:
        raise BackupError(_("Die Datensicherung ist nicht eingerichtet (BACKUP_DIR fehlt)."))
    cfg = _read_config()

    def number(key, low, high, message):
        try:
            value = int(str(values.get(key, "")).strip())
        except ValueError:
            raise BackupError(message) from None
        if not low <= value <= high:
            raise BackupError(message)
        return value

    cfg["hour"] = number("hour", 0, 23, _("Uhrzeit bitte zwischen 0 und 23."))
    cfg["keep_days"] = number("keep_days", 1, 365, _("Tage bitte zwischen 1 und 365."))
    cfg["keep_months"] = number("keep_months", 0, 120, _("Monate bitte zwischen 0 und 120."))
    target = values.get("target") or ""
    if target not in TARGETS:
        raise BackupError(_("Unbekanntes Ziel."))
    cfg["target"] = target
    for key in ("smb_server", "smb_share", "smb_path", "smb_user", "smb_domain", "folder"):
        cfg[key] = str(values.get(key) or "").strip()[:200]
    cfg["smb_server"] = cfg["smb_server"].strip("\\/")
    cfg["smb_share"] = cfg["smb_share"].strip("\\/")
    cfg["smb_path"] = cfg["smb_path"].replace("\\", "/").strip("/")
    if ".." in cfg["smb_path"].split("/"):
        raise BackupError(_("Der Unterordner darf kein „..“ enthalten."))
    if values.get("clear_password"):
        cfg["smb_password"] = ""
    elif values.get("smb_password"):
        cfg["smb_password"] = _fernet().encrypt(str(values["smb_password"]).encode()).decode()
    if target == "smb" and not (cfg["smb_server"] and cfg["smb_share"]):
        raise BackupError(_("Für das Netzlaufwerk bitte Server und Freigabe angeben."))
    if target == "folder":
        if not cfg["folder"].startswith("/"):
            raise BackupError(_("Bitte den Ordner als vollständigen Pfad im Container angeben, z. B. /nas."))
        real = os.path.realpath(cfg["folder"])
        if real in {os.path.realpath(base), os.path.realpath(current_app.config.get("UPLOAD_DIR") or "/nonexistent")}:
            raise BackupError(_("Der Ordner darf nicht der Sicherungs- oder Foto-Ordner selbst sein."))
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, CONFIG_FILE)
    with open(path + ".tmp", "w") as f:
        json.dump(cfg, f)
    os.replace(path + ".tmp", path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


# --- Zweites Ziel: Ordner oder Netzlaufwerk -----------------------------------
# Beide verstehen dieselben Befehle mit Pfaden relativ zum Zielordner („db/…“, „dateien/…“).

class FolderTarget:
    """Ein in den Container eingebundener Ordner (z. B. NAS per Docker-Volume unter /nas)."""

    def __init__(self, root):
        self.root = root
        self.label = root

    def _p(self, rel):
        return os.path.join(self.root, *[p for p in rel.split("/") if p])

    def check(self):
        if not os.path.isdir(self.root):
            raise BackupError(_("Der Ordner {path} ist im Container nicht vorhanden – im Stack als Volume einbinden.",
                                path=self.root))

    def ensure(self, rel=""):
        os.makedirs(self._p(rel), exist_ok=True)

    def listing(self, rel=""):
        try:
            return {e.name: (e.stat().st_size, e.is_dir()) for e in os.scandir(self._p(rel))}
        except FileNotFoundError:
            return {}

    def upload(self, local, rel):
        dst = self._p(rel)
        shutil.copyfile(local, dst + ".part")
        os.replace(dst + ".part", dst)

    def download(self, rel, local):
        shutil.copyfile(self._p(rel), local + ".part")
        os.replace(local + ".part", local)

    def remove(self, rel):
        os.remove(self._p(rel))

    def close(self):
        pass


class SmbTarget:
    """Netzlaufwerk über SMB 2/3 (smbprotocol) – der Container braucht dafür keine besonderen Rechte."""

    def __init__(self, cfg):
        import smbclient
        self.c = smbclient
        self.server = cfg["smb_server"]
        user = cfg["smb_user"]
        if user and cfg.get("smb_domain") and "\\" not in user and "@" not in user:
            user = f"{cfg['smb_domain']}\\{user}"
        try:
            smbclient.register_session(self.server, username=user or None, password=_password(cfg) or None,
                                       connection_timeout=15)
        except Exception as e:
            raise BackupError(_("Keine Verbindung zum Netzlaufwerk: {error}", error=_short(e))) from None
        sub = "\\".join(p for p in cfg["smb_path"].split("/") if p)
        self.root = f"\\\\{self.server}\\{cfg['smb_share']}" + (f"\\{sub}" if sub else "")
        self.label = self.root

    def _p(self, rel):
        parts = [p for p in rel.split("/") if p]
        return self.root + ("\\" + "\\".join(parts) if parts else "")

    def check(self):
        self.ensure()

    def ensure(self, rel=""):
        self.c.makedirs(self._p(rel), exist_ok=True)

    def listing(self, rel=""):
        try:
            return {e.name: (e.stat().st_size, e.is_dir()) for e in self.c.scandir(self._p(rel))}
        except OSError:
            return {}

    def upload(self, local, rel):
        dst = self._p(rel)
        with open(local, "rb") as src, self.c.open_file(dst + ".part", mode="wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)
        self.c.replace(dst + ".part", dst)

    def download(self, rel, local):
        with self.c.open_file(self._p(rel), mode="rb") as src, open(local + ".part", "wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)
        os.replace(local + ".part", local)

    def remove(self, rel):
        self.c.remove(self._p(rel))

    def close(self):
        with contextlib.suppress(Exception):
            self.c.delete_session(self.server)


def _short(error):
    return (str(error) or error.__class__.__name__).strip()[-300:]


def target(cfg=None):
    """Das eingestellte zweite Ziel oder None."""
    cfg = cfg or _read_config()
    if cfg["target"] == "folder" and cfg["folder"]:
        return FolderTarget(cfg["folder"])
    if cfg["target"] == "smb" and cfg["smb_server"] and cfg["smb_share"]:
        return SmbTarget(cfg)
    return None


def test_target():
    """Verbindung prüfen: Ordner anlegen, Probedatei schreiben, lesen und löschen. Gibt das Ziel als Text zurück."""
    t = target()
    if t is None:
        raise BackupError(_("Kein Netzlaufwerk und kein Ordner eingestellt."))
    try:
        t.check()
        t.ensure()
        with tempfile.TemporaryDirectory() as tmp:
            probe = os.path.join(tmp, "probe")
            with open(probe, "w") as f:
                f.write("Lagersystem")
            t.upload(probe, ".verbindungstest")
            t.download(".verbindungstest", probe + "2")
            t.remove(".verbindungstest")
        return t.label
    except BackupError:
        raise
    except Exception as e:
        raise BackupError(_("Schreiben auf {target} nicht möglich: {error}", target=t.label, error=_short(e))) from None
    finally:
        t.close()


def _push_tree(t, local_root, rel_root):
    """Neue oder geänderte Dateien hochladen (gleich = gleiche Größe). Gelöschte bleiben im Ziel."""
    count = 0
    if not os.path.isdir(local_root):
        return count
    for root, _dirs, files in os.walk(local_root):
        rel = os.path.relpath(root, local_root).replace(os.sep, "/")
        rel = rel_root if rel == "." else f"{rel_root}/{rel}"
        t.ensure(rel)
        remote = t.listing(rel)
        for name in files:
            if name.endswith(".part"):
                continue
            size = os.path.getsize(os.path.join(root, name))
            if remote.get(name, (None,))[0] != size:
                t.upload(os.path.join(root, name), f"{rel}/{name}")
                count += 1
    return count


def _safe_name(name):
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name


def _pull_tree(t, rel, local_root):
    count = 0
    for name, (size, is_dir) in t.listing(rel).items():
        if not _safe_name(name) or name.endswith(".part"):
            continue
        local = os.path.join(local_root, name)
        if is_dir:
            os.makedirs(local, exist_ok=True)
            count += _pull_tree(t, f"{rel}/{name}", local)
        elif not os.path.exists(local) or os.path.getsize(local) != size:
            t.download(f"{rel}/{name}", local)
            count += 1
    return count


def _sync_to_target(base, cfg):
    """Nach der Sicherung: Abzüge und Dateien ins zweite Ziel, dort dieselbe Aufbewahrung."""
    t = target(cfg)
    if t is None:
        return None
    try:
        t.check()
        t.ensure("db")
        remote = t.listing("db")
        local_db = os.path.join(base, "db")
        sent = 0
        for name in sorted(os.listdir(local_db)):
            if NAME_RE.match(name):
                size = os.path.getsize(os.path.join(local_db, name))
                if remote.get(name, (None,))[0] != size:
                    t.upload(os.path.join(local_db, name), f"db/{name}")
                    sent += 1
        names = set(remote) | {n for n in os.listdir(local_db) if NAME_RE.match(n)}
        for name in to_delete(names, datetime.now(_tz()).date(), cfg["keep_days"], cfg["keep_months"]):
            if name in remote or name in os.listdir(local_db):
                with contextlib.suppress(OSError):
                    t.remove(f"db/{name}")
        files = _push_tree(t, os.path.join(base, "dateien"), "dateien")
        return {"ok": True, "target": t.label, "dumps": sent, "files": files,
                "at": datetime.now(UTC).isoformat(), "error": None}
    except Exception as e:
        current_app.logger.exception("Abgleich mit dem Netzlaufwerk fehlgeschlagen")
        return {"ok": False, "target": t.label, "error": _short(e), "at": datetime.now(UTC).isoformat()}
    finally:
        t.close()


def _pull_from_target(base):
    """Stände und Dateien vom zweiten Ziel in den Sicherungsordner holen (z. B. nach einem neuen Server)."""
    t = target()
    if t is None:
        raise BackupError(_("Kein Netzlaufwerk und kein Ordner eingestellt."))
    try:
        t.check()
        db_dir = os.path.join(base, "db")
        os.makedirs(db_dir, exist_ok=True)
        dumps = 0
        for name, (size, is_dir) in t.listing("db").items():
            local = os.path.join(db_dir, name)
            if not is_dir and NAME_RE.match(name) and (not os.path.exists(local) or os.path.getsize(local) != size):
                t.download(f"db/{name}", local)
                dumps += 1
        os.makedirs(os.path.join(base, "dateien"), exist_ok=True)
        files = _pull_tree(t, "dateien", os.path.join(base, "dateien"))
        return {"dumps": dumps, "files": files}
    except BackupError:
        raise
    except Exception as e:
        raise BackupError(_("Holen von {target} fehlgeschlagen: {error}", target=t.label, error=_short(e))) from None
    finally:
        t.close()


# --- Sicherung ----------------------------------------------------------------

def _dump_database(target):
    """Schreibt den Datenbank-Abzug nach `target` (ohne Endung) und gibt den fertigen Pfad zurück."""
    url = make_url(current_app.config["SQLALCHEMY_DATABASE_URI"])
    if url.get_backend_name() == "sqlite":
        path = target + ".sqlite"
        src = sqlite3.connect(url.database)
        try:
            dst = sqlite3.connect(path)
            with dst:
                src.backup(dst)
            dst.close()
        finally:
            src.close()
        return path
    path = target + ".dump"
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password              # Passwort nicht in der Prozessliste
    conninfo = url.set(drivername="postgresql", password=None).render_as_string(hide_password=False)
    try:
        result = subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--file", path, "--dbname", conninfo],
                                env=env, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        raise BackupError("pg_dump fehlt im Container – Stack mit „Re-pull image and redeploy“ neu bauen.") from None
    if result.returncode != 0:
        raise BackupError((result.stderr or "pg_dump fehlgeschlagen").strip()[-500:])
    return path


def _mirror_files(src, dst):
    """Kopiert neue oder geänderte Dateien. Gelöschte bleiben in der Sicherung erhalten."""
    copied = 0
    if not src or not os.path.isdir(src):
        return copied
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        out_dir = os.path.normpath(os.path.join(dst, rel))
        os.makedirs(out_dir, exist_ok=True)
        for name in files:
            s, d = os.path.join(root, name), os.path.join(out_dir, name)
            st = os.stat(s)
            if os.path.exists(d) and os.path.getsize(d) == st.st_size and int(os.path.getmtime(d)) >= int(st.st_mtime):
                continue
            shutil.copy2(s, d)
            copied += 1
    return copied


def to_delete(names, today, keep_days=KEEP_DAYS, keep_months=KEEP_MONTHS):
    """Welche Abzüge nach der Aufbewahrungsregel weg können (reine Funktion, für Tests)."""
    dated = []
    for name in names:
        m = NAME_RE.match(name)
        if m:
            dated.append((date(int(m[1]), int(m[2]), int(m[3])), name))
    dated.sort()
    keep = {name for day, name in dated if (today - day).days < keep_days}
    first_of_month = {}
    for day, name in dated:
        first_of_month.setdefault((day.year, day.month), name)
    now = today.year * 12 + today.month              # erster Stand je Kalendermonat der letzten `keep_months`
    keep.update(name for (y, m), name in first_of_month.items() if 0 <= now - (y * 12 + m) < keep_months)
    if dated:
        keep.add(dated[-1][1])                       # der neueste bleibt immer
    return [name for _day, name in dated if name not in keep]


def _backup_locked(status):
    """Die eigentliche Sicherung (Sperre hält der Aufrufer). Ergänzt `status`, wirft bei Fehlern."""
    base = backup_dir()
    cfg = _read_config()
    started = time.monotonic()
    db_dir = os.path.join(base, "db")
    os.makedirs(db_dir, exist_ok=True)
    # erst in eine temporäre Datei, damit ein abgebrochener Lauf keinen halben Abzug hinterlässt
    tmp_dir = tempfile.mkdtemp(dir=db_dir, prefix=".tmp-")
    try:
        stamp = _stamp()
        if any(n.startswith(f"lager-{stamp}.") for n in os.listdir(db_dir)):        # nichts überschreiben
            stamp = datetime.now(_tz()).strftime("%Y-%m-%d_%H%M%S")
        tmp = _dump_database(os.path.join(tmp_dir, f"lager-{stamp}"))
        final = os.path.join(db_dir, os.path.basename(tmp))
        os.replace(tmp, final)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    copied = _mirror_files(current_app.config.get("UPLOAD_DIR"), os.path.join(base, "dateien"))
    for name in to_delete(os.listdir(db_dir), datetime.now(_tz()).date(), cfg["keep_days"], cfg["keep_months"]):
        os.remove(os.path.join(db_dir, name))
    status.update(ok=True, last_success=status["last_run"], file=os.path.basename(final),
                  size=os.path.getsize(final), files_copied=copied, seconds=round(time.monotonic() - started, 1))
    status["share"] = _sync_to_target(base, cfg)
    current_app.logger.info("Datensicherung %s (%d neue Dateien)", status["file"], copied)


def run_backup():
    """Eine Sicherung ausführen (in einem App-Kontext). Gibt den neuen Status zurück."""
    base = backup_dir()
    if not base:
        raise BackupError("BACKUP_DIR ist nicht gesetzt.")
    if not _lock.acquire(blocking=False):
        raise BackupError("Es läuft bereits eine Sicherung.")
    status = read_status()
    status.update(last_run=datetime.now(UTC).isoformat(), ok=False, error=None)
    try:
        _backup_locked(status)
    except Exception as e:
        status["error"] = _short(e)
        current_app.logger.exception("Datensicherung fehlgeschlagen")
    finally:
        _write_status(base, status)
        _lock.release()
    return status


def start(app, fn, *args):
    """Länger laufende Arbeit im Hintergrund (in Tests sofort)."""
    def work():
        with app.app_context(), contextlib.suppress(BackupError):
            fn(*args)
    if app.testing:
        work()
    else:
        threading.Thread(target=work, name=f"backup-{fn.__name__}", daemon=True).start()


def run_in_background(app):
    """„Jetzt sichern“ aus der Verwaltung: läuft im Hintergrund, die Seite zeigt den Fortschritt."""
    start(app, run_backup)


def _job(kind, fn):
    """Arbeit unter der Sperre mit Ergebnis in `status[kind]` (Paket, Holen, Zurückspielen)."""
    base = backup_dir()
    if not base:
        raise BackupError(_("Die Datensicherung ist nicht eingerichtet (BACKUP_DIR fehlt)."))
    if not _lock.acquire(blocking=False):
        raise BackupError(_("Es läuft bereits eine Sicherung."))
    started = time.monotonic()
    result = {"at": datetime.now(UTC).isoformat(), "ok": False, "error": None}
    try:
        result.update(fn(base) or {})
        result["ok"] = True
    except Exception as e:
        result["error"] = _short(e)
        current_app.logger.exception("Datensicherung: %s fehlgeschlagen", kind)
    finally:
        result["seconds"] = round(time.monotonic() - started, 1)
        status = read_status()
        status[kind] = result
        _write_status(base, status)
        _lock.release()
    return result


# --- Komplettpaket ------------------------------------------------------------

def _revision():
    from .extensions import db
    try:
        with db.engine.connect() as con:
            return con.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    except Exception:
        return None


def build_package():
    """ZIP mit frischem Datenbank-Abzug, allen Fotos/Dokumenten und manifest.json. Nur das neueste Paket bleibt."""
    def work(base):
        out_dir = os.path.join(base, "pakete")
        os.makedirs(out_dir, exist_ok=True)
        stamp = _stamp()
        name = f"lager-paket-{stamp}.zip"
        tmp_dir = tempfile.mkdtemp(dir=out_dir, prefix=".tmp-")
        try:
            dump = _dump_database(os.path.join(tmp_dir, f"lager-{stamp}"))
            uploads = current_app.config.get("UPLOAD_DIR")
            count = 0
            part = os.path.join(tmp_dir, name)
            with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                z.write(dump, f"db/{os.path.basename(dump)}")
                if uploads and os.path.isdir(uploads):
                    for root, _dirs, files in os.walk(uploads):
                        for fname in files:
                            full = os.path.join(root, fname)
                            z.write(full, "dateien/" + os.path.relpath(full, uploads).replace(os.sep, "/"))
                            count += 1
                manifest = {"app": "lagersystem", "version": current_app.config.get("APP_VERSION"),
                            "created": datetime.now(UTC).isoformat(), "database": os.path.basename(dump),
                            "revision": _revision(), "files": count}
                z.writestr("manifest.json", json.dumps(manifest, indent=1))
            os.replace(part, os.path.join(out_dir, name))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        for old in os.listdir(out_dir):
            if PACKAGE_RE.match(old) and old != name:
                os.remove(os.path.join(out_dir, old))
        return {"file": name, "size": os.path.getsize(os.path.join(out_dir, name)), "files": count}
    return _job("package", work)


def package_path(name):
    base = backup_dir()
    if not base or not PACKAGE_RE.match(name or ""):
        return None
    path = os.path.join(base, "pakete", name)
    return path if os.path.isfile(path) else None


def pull_from_target():
    return _job("pull", _pull_from_target)


# --- Zurückspielen ------------------------------------------------------------

def save_upload(stream_save, filename):
    """Hochgeladene Datei (Abzug .dump/.sqlite oder Komplettpaket .zip) im Sicherungsordner ablegen.
    `stream_save(path)` schreibt sie (z. B. `FileStorage.save`). Gibt den internen Namen zurück."""
    base = backup_dir()
    if not base:
        raise BackupError(_("Die Datensicherung ist nicht eingerichtet (BACKUP_DIR fehlt)."))
    up_dir = os.path.join(base, "hochgeladen")
    shutil.rmtree(up_dir, ignore_errors=True)              # immer nur eine hochgeladene Datei
    os.makedirs(up_dir, exist_ok=True)
    tmp = os.path.join(up_dir, ".upload")
    stream_save(tmp)
    with open(tmp, "rb") as f:
        head = f.read(16)
    kind = "zip" if head[:2] == b"PK" else "dump" if head[:5] == b"PGDMP" else \
        "sqlite" if head.startswith(b"SQLite format 3") else None
    if kind is None:
        os.remove(tmp)
        raise BackupError(_("Das ist weder ein Komplettpaket (.zip) noch ein Datenbank-Stand (.dump/.sqlite)."))
    name = f"upload-{datetime.now(_tz()):%Y%m%d-%H%M%S}.{kind}"
    os.replace(tmp, os.path.join(up_dir, name))
    return name


def _source_path(kind, name):
    base = backup_dir()
    if kind == "stand":
        path = backup_path(name)
    elif kind == "upload" and UPLOAD_RE.match(name or ""):
        path = os.path.join(base, "hochgeladen", name)
    elif kind == "paket":
        path = package_path(name)
    else:
        path = None
    if not path or not os.path.isfile(path):
        raise BackupError(_("Der gewählte Stand wurde nicht gefunden."))
    return path


def _unpack(path, tmp_dir):
    """Komplettpaket entpacken: → (Pfad des Abzugs, Ordner mit den Dateien oder None)."""
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise BackupError(_("Das Paket ist beschädigt.")) from None
    with archive:
        names = archive.namelist()
        dumps = [n for n in names if re.match(r"^db/[^/]+\.(dump|sqlite)$", n)]
        if len(dumps) != 1:
            raise BackupError(_("Im Paket wurde kein Datenbank-Stand gefunden."))
        root = os.path.realpath(tmp_dir)
        files_dir = None
        for member in archive.infolist():
            if member.is_dir() or not (member.filename == dumps[0] or member.filename.startswith("dateien/")):
                continue
            out = os.path.realpath(os.path.join(root, member.filename))
            if not out.startswith(root + os.sep):          # kein Ausbrechen aus dem Ordner („../“)
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with archive.open(member) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            if member.filename.startswith("dateien/"):
                files_dir = os.path.join(root, "dateien")
        return os.path.join(root, dumps[0]), files_dir


def _migrate():
    """Nach dem Zurückspielen eines älteren Stands die Datenbank auf den Stand der App bringen."""
    from flask_migrate import upgrade
    from .extensions import db
    if "alembic_version" not in sa_inspect(db.engine).get_table_names():
        return False
    upgrade(directory=os.path.join(os.path.dirname(current_app.root_path), "migrations"))
    return True


def run_restore(kind, name, with_files):
    """Zurückspielen: erst ein Sicherheitsstand, dann Datenbank ersetzen, Migrationen, auf Wunsch Fotos/Dokumente."""
    def work(base):
        from .extensions import db
        path = _source_path(kind, name)
        status = read_status()
        status.update(last_run=datetime.now(UTC).isoformat(), ok=False, error=None)
        try:
            _backup_locked(status)                          # Sicherheitsstand des jetzigen Zustands
        finally:
            _write_status(base, status)
        if not status.get("ok"):
            raise BackupError(_("Der Sicherheitsstand vorher ist fehlgeschlagen – nichts zurückgespielt."))
        tmp_dir = tempfile.mkdtemp(dir=base, prefix=".restore-")
        try:
            files_dir = None
            dump = path
            if path.endswith(".zip"):
                dump, files_dir = _unpack(path, tmp_dir)
            elif with_files:
                files_dir = os.path.join(base, "dateien")       # Spiegel im Sicherungsordner
            db.session.remove()
            db.engine.dispose()
            restore(dump)
            db.engine.dispose()
            migrated = _migrate()
            copied = 0
            if with_files and files_dir and os.path.isdir(files_dir):
                copied = _mirror_files(files_dir, current_app.config.get("UPLOAD_DIR"))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        if kind == "upload":
            shutil.rmtree(os.path.join(base, "hochgeladen"), ignore_errors=True)
        return {"source": name, "files": copied, "migrated": migrated, "safety": status.get("file")}
    return _job("restore", work)


# --- Status -------------------------------------------------------------------

def _status_path(base):
    return os.path.join(base, "status.json")


def read_status():
    base = backup_dir()
    if not base:
        return {}
    try:
        with open(_status_path(base)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_status(base, status):
    try:
        os.makedirs(base, exist_ok=True)
        tmp = _status_path(base) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(status, f)
        os.replace(tmp, _status_path(base))
    except OSError:
        current_app.logger.exception("Status der Datensicherung nicht schreibbar")


def _parse(ts):
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def list_backups():
    base = backup_dir()
    db_dir = os.path.join(base, "db") if base else None
    if not db_dir or not os.path.isdir(db_dir):
        return []
    items = []
    for name in os.listdir(db_dir):
        m = NAME_RE.match(name)
        if m:
            st = os.stat(os.path.join(db_dir, name))
            # Zeitpunkt aus dem Namen (Ortszeit) – nach dem Holen vom Netzlaufwerk stimmt das Dateidatum nicht
            local = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4][:2]), int(m[4][2:]), int(m[5] or 0), tzinfo=_tz())
            items.append({"name": name, "size": st.st_size, "created": local.astimezone(UTC)})
    return sorted(items, key=lambda i: i["name"], reverse=True)


def backup_path(name):
    """Pfad zu einem Abzug – nur gültige Dateinamen, kein Ausbrechen aus dem Ordner."""
    base = backup_dir()
    if not base or not NAME_RE.match(name or ""):
        return None
    path = os.path.join(base, "db", name)
    return path if os.path.isfile(path) else None


def _dir_size(path):
    total = count = 0
    if path and os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for name in files:
                with contextlib.suppress(OSError):
                    total += os.path.getsize(os.path.join(root, name))
                    count += 1
    return total, count


def overview():
    """Alles für die Verwaltungsseite und die Warnung auf der Startseite."""
    base = backup_dir()
    status = read_status()
    last_success = _parse(status.get("last_success"))
    now = datetime.now(UTC)
    info = {
        "configured": bool(base), "running": is_running(), "status": status,
        "last_run": _parse(status.get("last_run")), "last_success": last_success,
        "backups": list_backups() if base else [], "hour": hour() if base else current_app.config.get("BACKUP_HOUR", 3),
        "settings": settings() if base else None,
    }
    for kind in ("package", "pull", "restore", "share"):
        job = status.get(kind) or None
        if job:
            job = dict(job, at=_parse(job.get("at")))
        info[kind] = job
    pkg = info["package"]
    info["package_file"] = pkg["file"] if pkg and pkg.get("ok") and package_path(pkg.get("file")) else None
    up_dir = os.path.join(base, "hochgeladen") if base else None
    info["upload"] = next((n for n in sorted(os.listdir(up_dir)) if UPLOAD_RE.match(n)), None) \
        if up_dir and os.path.isdir(up_dir) else None
    info["stale"] = bool(base) and (last_success is None or now - last_success > STALE_AFTER)
    info["failed"] = bool(base) and status.get("ok") is False and bool(status.get("error"))
    info["share_failed"] = bool(base) and bool(info["share"]) and info["share"].get("ok") is False
    info["files_size"], info["files_count"] = _dir_size(current_app.config.get("UPLOAD_DIR"))
    try:
        info["disk"] = shutil.disk_usage(base) if base and os.path.isdir(base) else None
    except OSError:
        info["disk"] = None
    return info


def needs_attention():
    """Für die Startseite der Admins: Sicherung fehlgeschlagen oder zu alt (nicht vor der ersten Nacht)."""
    if not backup_dir():
        return None
    info = overview()
    if info["failed"] or info["share_failed"]:
        return "failed"
    if info["stale"] and info["last_run"] is not None:
        return "stale"
    return None


# --- Zeitplan -----------------------------------------------------------------

def is_due(now_local, last_success_local, last_run_local, hour):
    """Fällig ab `hour` Uhr, wenn heute noch nicht erfolgreich gesichert wurde.
    Nach einem Fehler frühestens eine Stunde später erneut versuchen."""
    if now_local.hour < hour:
        return False
    if last_success_local and last_success_local.date() >= now_local.date():
        return False
    if last_run_local and now_local - last_run_local < timedelta(hours=1):
        return False
    return True


def tick(app):
    with app.app_context():
        if not backup_dir():
            return
        status = read_status()
        tz = _tz()
        local = lambda ts: _parse(ts).astimezone(tz) if _parse(ts) else None   # noqa: E731
        if is_due(datetime.now(tz), local(status.get("last_success")), local(status.get("last_run")), hour()):
            with contextlib.suppress(BackupError):
                run_backup()


def restore(path):
    """Spielt einen Abzug zurück (Verwaltung → Datensicherung oder `flask backup-restore`)."""
    url = make_url(current_app.config["SQLALCHEMY_DATABASE_URI"])
    if path.endswith(".sqlite"):
        if url.get_backend_name() != "sqlite":
            raise BackupError(_("Ein SQLite-Stand passt nicht zu einer PostgreSQL-Datenbank."))
        shutil.copy2(path, url.database)
        return
    if url.get_backend_name() == "sqlite":
        raise BackupError(_("Ein PostgreSQL-Stand passt nicht zu einer SQLite-Datenbank."))
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    conninfo = url.set(drivername="postgresql", password=None).render_as_string(hide_password=False)
    result = subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--single-transaction",
                             "--exit-on-error", "--dbname", conninfo, path],
                            env=env, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise BackupError((result.stderr or "pg_restore fehlgeschlagen").strip()[-1000:])
