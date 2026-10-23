import os
import re
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from app import backup, create_app
from app.extensions import db as _db
from app.models import User

from .conftest import login


def test_retention():
    today = date(2026, 10, 23)
    names = [f"lager-{d:%Y-%m-%d}_0300.dump" for d in (today - timedelta(days=i) for i in range(400))]
    names.append("notizen.txt")                                     # fremde Dateien bleiben unberührt
    gone = set(backup.to_delete(names, today))
    kept = sorted(set(names) - gone - {"notizen.txt"})
    assert "notizen.txt" not in gone
    # 14 Tage + erster Stand jedes Monats (12 Monate, der aktuelle zählt mit)
    assert all(f"lager-{today - timedelta(days=i):%Y-%m-%d}_0300.dump" in kept for i in range(14))
    firsts = [n for n in kept if n[13:17] == "-01_"]
    assert len(firsts) == 12 and "lager-2025-11-01_0300.dump" in kept   # Nov. 2025 … Okt. 2026
    assert "lager-2025-10-01_0300.dump" not in kept and len(kept) == 14 + 12
    assert backup.to_delete(["lager-2026-10-23_0300.dump"], today) == []


def test_is_due():
    day = datetime(2026, 10, 23, 2, 59)
    assert not backup.is_due(day, None, None, 3)                    # vor 3 Uhr
    at3 = day.replace(hour=3, minute=5)
    assert backup.is_due(at3, None, None, 3)                        # noch nie gesichert
    assert backup.is_due(at3, at3 - timedelta(days=1), None, 3)
    assert not backup.is_due(at3, at3.replace(hour=3, minute=1), None, 3)          # heute schon
    assert not backup.is_due(at3, None, at3 - timedelta(minutes=20), 3)            # Fehler vor 20 Min.
    assert backup.is_due(at3 + timedelta(hours=2), None, at3, 3)                   # später erneut versuchen


@pytest.fixture
def file_app(tmp_path):
    """Eigene App mit SQLite-Datei (In-Memory lässt sich nicht kopieren)."""
    app = create_app({
        "TESTING": True, "SECRET_KEY": "test", "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'lager.db'}",
        "UPLOAD_DIR": str(tmp_path / "uploads"), "BACKUP_DIR": str(tmp_path / "backup"),
    })
    with app.app_context():
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()


def test_run_backup(file_app, tmp_path):
    admin = User(username="chef", role="admin")
    admin.set_password("geheim123")
    _db.session.add(admin)
    _db.session.commit()
    os.makedirs(tmp_path / "uploads" / "2026")
    (tmp_path / "uploads" / "a.jpg").write_bytes(b"x" * 10)
    (tmp_path / "uploads" / "2026" / "b.pdf").write_bytes(b"y" * 20)

    status = backup.run_backup()
    assert status["ok"] and status["files_copied"] == 2, status
    dump = tmp_path / "backup" / "db" / status["file"]
    assert dump.exists() and status["file"].endswith(".sqlite")
    with sqlite3.connect(dump) as con:
        assert con.execute("select username from users").fetchall() == [("chef",)]
    assert (tmp_path / "backup" / "dateien" / "2026" / "b.pdf").read_bytes() == b"y" * 20
    assert not [n for n in os.listdir(tmp_path / "backup" / "db") if n.startswith(".tmp")]

    # zweiter Lauf: nur neue Dateien, Übersicht stimmt
    (tmp_path / "uploads" / "c.jpg").write_bytes(b"z")
    assert backup.run_backup()["files_copied"] == 1
    info = backup.overview()
    assert info["configured"] and not info["stale"] and not info["failed"] and info["backups"]
    assert backup.needs_attention() is None

    # Seite, Download, Schutz vor fremden Pfaden
    client = file_app.test_client()
    login(client, "chef")
    html = client.get("/admin/sicherung").get_data(as_text=True)
    assert "Jetzt sichern" in html and "Gesichert" in html
    r = client.get(f"/admin/sicherung/{info['backups'][0]['name']}")
    assert r.status_code == 200 and r.headers["Content-Disposition"].startswith("attachment")
    assert client.get("/admin/sicherung/..%2Fstatus.json").status_code == 404
    assert client.get("/admin/sicherung/status.json").status_code == 404


def test_failure_is_reported(file_app, monkeypatch):
    def broken(target):
        raise backup.BackupError("pg_dump: Verbindung abgelehnt")
    monkeypatch.setattr(backup, "_dump_database", broken)
    status = backup.run_backup()
    assert not status["ok"] and "Verbindung abgelehnt" in status["error"]
    assert backup.needs_attention() == "failed"
    assert not backup.is_running()
    # Admins sehen den Hinweis oben auf der Übersicht (als eigene Meldung, nicht in einer Kachel)
    admin = User(username="chef", role="admin")
    admin.set_password("geheim123")
    _db.session.add(admin)
    _db.session.commit()
    client = file_app.test_client()
    login(client, "chef")
    html = client.get("/").get_data(as_text=True)
    assert '<div class="alert alert-danger d-flex gap-2 align-items-center">' in html
    assert "Die Datensicherung ist fehlgeschlagen." in html
    assert not re.search(r'<span class="s">\s*<div class="alert', html)


def test_not_configured(as_admin, as_worker):
    html = as_admin.get("/admin/sicherung").get_data(as_text=True)
    assert "Nicht eingerichtet" in html
    assert as_admin.post("/admin/sicherung/jetzt").status_code == 302
    assert as_worker.get("/admin/sicherung").status_code == 403
    assert backup.needs_attention() is None
