"""Datensicherung: Datenbank-Abzug (pg_dump bzw. SQLite-Kopie) und Fotos/Dokumente nach `BACKUP_DIR`.

Aufbau im Sicherungsordner:
    db/lager-2026-10-23_0300.dump   Datenbank (PostgreSQL: pg_dump --format=custom)
    dateien/…                       Spiegel von UPLOAD_DIR (nur neue/geänderte Dateien werden kopiert)
    status.json                     letzter Lauf (für die Verwaltungsseite)

Aufbewahrung: alle Stände der letzten 14 Tage, dazu der erste Stand jedes Monats für 12 Monate.
Der nächtliche Lauf startet aus dem Zeitplan (`scheduler.py`).
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy.engine import make_url

KEEP_DAYS = 14
KEEP_MONTHS = 12
STALE_AFTER = timedelta(hours=36)        # Warnung, wenn so lange keine erfolgreiche Sicherung
NAME_RE = re.compile(r"^lager-(\d{4})-(\d{2})-(\d{2})_(\d{4})\.(dump|sqlite)$")

_lock = threading.Lock()


class BackupError(Exception):
    pass


def backup_dir():
    return current_app.config.get("BACKUP_DIR") or None


def _tz():
    return ZoneInfo(current_app.config.get("TIMEZONE", "Europe/Berlin"))


def is_running():
    return _lock.locked()


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


def to_delete(names, today):
    """Welche Abzüge nach der Aufbewahrungsregel weg können (reine Funktion, für Tests)."""
    dated = []
    for name in names:
        m = NAME_RE.match(name)
        if m:
            dated.append((date(int(m[1]), int(m[2]), int(m[3])), name))
    dated.sort()
    keep = {name for day, name in dated if (today - day).days < KEEP_DAYS}
    first_of_month = {}
    for day, name in dated:
        first_of_month.setdefault((day.year, day.month), name)
    months = sorted(first_of_month, reverse=True)[:KEEP_MONTHS]
    keep.update(first_of_month[m] for m in months)
    return [name for _day, name in dated if name not in keep]


def run_backup():
    """Eine Sicherung ausführen (in einem App-Kontext). Gibt den neuen Status zurück."""
    base = backup_dir()
    if not base:
        raise BackupError("BACKUP_DIR ist nicht gesetzt.")
    if not _lock.acquire(blocking=False):
        raise BackupError("Es läuft bereits eine Sicherung.")
    started = time.monotonic()
    status = read_status()
    status.update(last_run=datetime.now(timezone.utc).isoformat(), ok=False, error=None)
    try:
        db_dir = os.path.join(base, "db")
        os.makedirs(db_dir, exist_ok=True)
        stamp = datetime.now(_tz()).strftime("%Y-%m-%d_%H%M")
        # erst in eine temporäre Datei, damit ein abgebrochener Lauf keinen halben Abzug hinterlässt
        tmp_dir = tempfile.mkdtemp(dir=db_dir, prefix=".tmp-")
        try:
            tmp = _dump_database(os.path.join(tmp_dir, f"lager-{stamp}"))
            final = os.path.join(db_dir, os.path.basename(tmp))
            os.replace(tmp, final)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        copied = _mirror_files(current_app.config.get("UPLOAD_DIR"), os.path.join(base, "dateien"))
        for name in to_delete(os.listdir(db_dir), datetime.now(_tz()).date()):
            os.remove(os.path.join(db_dir, name))
        status.update(ok=True, last_success=status["last_run"], file=os.path.basename(final),
                      size=os.path.getsize(final), files_copied=copied,
                      seconds=round(time.monotonic() - started, 1))
        current_app.logger.info("Datensicherung %s (%d neue Dateien)", status["file"], copied)
    except Exception as e:              # noqa: BLE001 – jeder Fehler soll in der Verwaltung sichtbar werden
        status["error"] = str(e)[-500:] or e.__class__.__name__
        current_app.logger.exception("Datensicherung fehlgeschlagen")
    finally:
        _write_status(base, status)
        _lock.release()
    return status


def run_in_background(app):
    """„Jetzt sichern“ aus der Verwaltung: läuft im Hintergrund, die Seite zeigt den Fortschritt."""
    def work():
        with app.app_context():
            try:
                run_backup()
            except BackupError:
                pass
    threading.Thread(target=work, name="backup-now", daemon=True).start()


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
        if NAME_RE.match(name):
            st = os.stat(os.path.join(db_dir, name))
            items.append({"name": name, "size": st.st_size,
                          "created": datetime.fromtimestamp(st.st_mtime, timezone.utc)})
    return sorted(items, key=lambda i: i["name"], reverse=True)


def backup_path(name):
    """Pfad zu einem Abzug – nur gültige Dateinamen, kein Ausbrechen aus dem Ordner."""
    base = backup_dir()
    if not base or not NAME_RE.match(name or ""):
        return None
    path = os.path.join(base, "db", name)
    return path if os.path.isfile(path) else None


def overview():
    """Alles für die Verwaltungsseite und die Warnung auf der Startseite."""
    base = backup_dir()
    status = read_status()
    last_success = _parse(status.get("last_success"))
    now = datetime.now(timezone.utc)
    info = {
        "configured": bool(base), "running": is_running(), "status": status,
        "last_run": _parse(status.get("last_run")), "last_success": last_success,
        "backups": list_backups() if base else [], "hour": current_app.config.get("BACKUP_HOUR", 3),
    }
    info["stale"] = bool(base) and (last_success is None or now - last_success > STALE_AFTER)
    info["failed"] = bool(base) and status.get("ok") is False and bool(status.get("error"))
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
    if info["failed"]:
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
        if is_due(datetime.now(tz), local(status.get("last_success")), local(status.get("last_run")),
                  app.config.get("BACKUP_HOUR", 3)):
            try:
                run_backup()
            except BackupError:
                pass


def restore(path):
    """Spielt einen Abzug zurück (nur über die Konsole, siehe `flask backup-restore`)."""
    url = make_url(current_app.config["SQLALCHEMY_DATABASE_URI"])
    if path.endswith(".sqlite"):
        if url.get_backend_name() != "sqlite":
            raise BackupError("SQLite-Abzug passt nicht zu einer PostgreSQL-Datenbank.")
        shutil.copy2(path, url.database)
        return
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    conninfo = url.set(drivername="postgresql", password=None).render_as_string(hide_password=False)
    result = subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--single-transaction",
                             "--exit-on-error", "--dbname", conninfo, path],
                            env=env, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise BackupError((result.stderr or "pg_restore fehlgeschlagen").strip()[-1000:])
