import os
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
    # Admins sehen den Hinweis auf der Übersicht ganz oben in „Jetzt wichtig“
    admin = User(username="chef", role="admin")
    admin.set_password("geheim123")
    _db.session.add(admin)
    _db.session.commit()
    client = file_app.test_client()
    login(client, "chef")
    html = client.get("/").get_data(as_text=True)
    first = html.index('class="hub-row important-row')
    assert "Datensicherung" in html[first:first + 400] and "fehlgeschlagen" in html[first:first + 400]


def test_not_configured(as_admin, as_worker):
    html = as_admin.get("/admin/sicherung").get_data(as_text=True)
    assert "Nicht eingerichtet" in html
    assert as_admin.post("/admin/sicherung/jetzt").status_code == 302
    assert as_worker.get("/admin/sicherung").status_code == 403
    assert backup.needs_attention() is None


def _admin_client(app):
    admin = User(username="chef", role="admin")
    admin.set_password("geheim123")
    _db.session.add(admin)
    _db.session.commit()
    client = app.test_client()
    login(client, "chef")
    return client


def test_settings_and_folder_target(file_app, tmp_path):
    """Einstellungen im Sicherungsordner (Passwort verschlüsselt), zweites Ziel als eingebundener Ordner."""
    client = _admin_client(file_app)
    nas = tmp_path / "nas"
    r = client.post("/admin/sicherung/einstellungen", data={"hour": "22", "keep_days": "3", "keep_months": "2",
                                                          "target": "folder", "folder": str(nas)},
                    follow_redirects=True)
    assert "gespeichert" in r.get_data(as_text=True)
    assert backup.hour() == 22 and backup.settings()["keep_days"] == 3
    # Ordner fehlt → Test meldet es verständlich, die lokale Sicherung klappt trotzdem
    r = client.post("/admin/sicherung/ziel/test", follow_redirects=True)
    assert "nicht vorhanden" in r.get_data(as_text=True)
    status = backup.run_backup()
    assert status["ok"] and status["share"]["ok"] is False and backup.needs_attention() == "failed"
    nas.mkdir()
    assert "Verbindung in Ordnung" in client.post("/admin/sicherung/ziel/test", follow_redirects=True).get_data(as_text=True)
    os.makedirs(tmp_path / "uploads" / "2026", exist_ok=True)
    (tmp_path / "uploads" / "2026" / "foto.jpg").write_bytes(b"f" * 30)
    status = backup.run_backup()
    assert status["share"]["ok"] and status["share"]["files"] == 1
    assert (nas / "lagersystem").exists() is False                      # Ordner-Ziel ohne Unterordner
    assert [n for n in os.listdir(nas / "db") if n.endswith(".sqlite")]
    assert (nas / "dateien" / "2026" / "foto.jpg").read_bytes() == b"f" * 30
    assert backup.needs_attention() is None
    # Aufbewahrung im Ziel: alte Stände fallen weg, der neueste bleibt
    (nas / "db" / "lager-2020-01-01_0300.sqlite").write_bytes(b"alt")
    backup.run_backup()
    assert not (nas / "db" / "lager-2020-01-01_0300.sqlite").exists()

    # Netzlaufwerk: Passwort nie im Klartext, leer lassen = behalten
    client.post("/admin/sicherung/einstellungen", data={"hour": "3", "keep_days": "14", "keep_months": "12",
                                                       "target": "smb", "smb_server": "nas", "smb_share": "backup",
                                                       "smb_path": "lager", "smb_user": "sicherung",
                                                       "smb_password": "Geheim!42"})
    raw = (tmp_path / "backup" / backup.CONFIG_FILE).read_text()
    assert "Geheim!42" not in raw and backup.settings()["has_password"]
    client.post("/admin/sicherung/einstellungen", data={"hour": "3", "keep_days": "14", "keep_months": "12",
                                                       "target": "smb", "smb_server": "nas", "smb_share": "backup"})
    assert backup._password(backup._read_config()) == "Geheim!42"
    r = client.post("/admin/sicherung/einstellungen", data={"hour": "3", "keep_days": "14", "keep_months": "12",
                                                           "target": "smb", "smb_server": "", "smb_share": ""},
                    follow_redirects=True)
    assert "Server und Freigabe" in r.get_data(as_text=True)
    page = client.get("/admin/sicherung").get_data(as_text=True)
    assert "Geheim" not in page and "gespeichert – leer lassen" in page


def test_package_and_restore(file_app, tmp_path):
    """Komplettpaket herunterladen, Daten ändern, Paket wieder hochladen und zurückspielen (mit Fotos)."""
    import io
    import json
    import zipfile
    client = _admin_client(file_app)
    os.makedirs(tmp_path / "uploads" / "2026")
    (tmp_path / "uploads" / "2026" / "a.jpg").write_bytes(b"a" * 5)
    client.post("/admin/sicherung/paket")
    info = backup.overview()
    assert info["package_file"] and info["package"]["files"] == 1
    data = client.get(f"/admin/sicherung/paket/{info['package_file']}").data
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        manifest = json.loads(z.read("manifest.json"))
    assert "dateien/2026/a.jpg" in names and any(n.startswith("db/lager-") for n in names)
    assert manifest["app"] == "lagersystem" and manifest["files"] == 1

    # danach geändert: neuer Benutzer, Foto weg
    extra = User(username="neu", role="mitarbeiter")
    extra.set_password("geheim123")
    _db.session.add(extra)
    _db.session.commit()
    os.remove(tmp_path / "uploads" / "2026" / "a.jpg")

    r = client.post("/admin/sicherung/hochladen", data={"file": (io.BytesIO(b"kein Backup"), "x.zip")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "weder ein Komplettpaket" in r.get_data(as_text=True)
    client.post("/admin/sicherung/hochladen", data={"file": (io.BytesIO(data), "paket.zip")},
                content_type="multipart/form-data")
    upload = backup.overview()["upload"]
    assert upload and upload.endswith(".zip")
    # ohne Bestätigungswort passiert nichts
    r = client.post("/admin/sicherung/zurueckspielen", data={"quelle": f"upload:{upload}", "bestaetigung": "ja"},
                    follow_redirects=True)
    assert "eintippen" in r.get_data(as_text=True)
    assert _db.session.query(User).filter_by(username="neu").count() == 1
    before = len(backup.list_backups())
    client.post("/admin/sicherung/zurueckspielen", data={"quelle": f"upload:{upload}", "bestaetigung": "zurückspielen",
                                                         "mit_dateien": "1"})
    result = backup.read_status()["restore"]
    assert result["ok"], result
    _db.session.remove()                                                            # Test-Sitzung neu öffnen
    assert _db.session.query(User).filter_by(username="neu").count() == 0          # Stand von vorher
    assert (tmp_path / "uploads" / "2026" / "a.jpg").read_bytes() == b"a" * 5      # Foto zurück
    assert len(backup.list_backups()) == before + 1                                 # Sicherheitsstand
    assert backup.overview()["upload"] is None                                      # hochgeladene Datei weg

    # aus einem Stand der Liste (ohne Fotos)
    stand = backup.list_backups()[0]["name"]
    client.post("/admin/sicherung/zurueckspielen", data={"quelle": f"stand:{stand}", "bestaetigung": "ZURÜCKSPIELEN"})
    assert backup.read_status()["restore"]["ok"]
    client.post("/admin/sicherung/zurueckspielen", data={"quelle": "stand:../status.json", "bestaetigung": "ZURÜCKSPIELEN"})
    assert not backup.read_status()["restore"]["ok"]


def test_pull_from_target(file_app, tmp_path):
    """Neuer Server: Stände und Fotos vom zweiten Ziel zurückholen."""
    client = _admin_client(file_app)
    nas = tmp_path / "nas"
    nas.mkdir()
    backup.save_settings({"hour": 3, "keep_days": 14, "keep_months": 12, "target": "folder", "folder": str(nas)})
    os.makedirs(tmp_path / "uploads")
    (tmp_path / "uploads" / "b.jpg").write_bytes(b"b")
    assert backup.run_backup()["share"]["ok"]
    import shutil
    shutil.rmtree(tmp_path / "backup" / "db")
    shutil.rmtree(tmp_path / "backup" / "dateien")
    assert backup.list_backups() == []
    client.post("/admin/sicherung/ziel/holen")
    pulled = backup.read_status()["pull"]
    assert pulled["ok"] and pulled["dumps"] == 1 and pulled["files"] == 1
    assert len(backup.list_backups()) == 1 and (tmp_path / "backup" / "dateien" / "b.jpg").exists()


def test_backup_pages_need_admin(as_worker):
    for url in ("/admin/sicherung/einstellungen", "/admin/sicherung/paket", "/admin/sicherung/zurueckspielen",
                "/admin/sicherung/hochladen", "/admin/sicherung/ziel/holen"):
        assert as_worker.post(url).status_code == 403


def test_same_minute_keeps_both(file_app, tmp_path):
    first = backup.run_backup()["file"]
    second = backup.run_backup()["file"]
    assert first != second and len(backup.list_backups()) == 2
    assert backup.NAME_RE.match(second) and backup.backup_path(second)
