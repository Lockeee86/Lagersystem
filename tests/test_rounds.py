import io
from datetime import timedelta

import pytest
from PIL import Image

from app import push, rounds
from app.models import CheckStation, PushSubscription, StationCheck
from app.storage import StorageError


@pytest.fixture(autouse=True)
def _clear_outbox():
    push.outbox.clear()
    yield
    push.outbox.clear()


def jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 50, 50)).save(buf, format="JPEG")
    buf.seek(0)
    return buf


def test_codes_and_status(db, seed):
    assert rounds.normalize_code("r1") == "R-01" and rounds.normalize_code("https://x/scan/R-07") == "R-07"
    assert rounds.normalize_code("K-0001") is None
    station = rounds.save_station(None, "Fermenter Test", "Rührwerk läuft\r\n\n  Keine   Leckage ", 1)
    other = rounds.save_station(None, "Pumpenhaus Test", "", 7)
    db.session.commit()
    assert (station.code, other.code) == ("R-01", "R-02")
    assert station.item_list == ["Rührwerk läuft", "Keine Leckage"]
    assert rounds.status(station)["overdue"]                                    # noch nie kontrolliert
    with pytest.raises(StorageError, match="beschreiben"):
        rounds.record_check(station, seed["worker"], [1])                     # Mangel ohne Notiz/Foto
    check = rounds.record_check(station, seed["worker"], [], None)
    db.session.commit()
    assert check.ok and check.results == [["Rührwerk läuft", True], ["Keine Leckage", True]]
    assert not rounds.status(station)["overdue"]
    check.created_at -= timedelta(days=2)
    db.session.commit()
    assert rounds.status(station)["overdue"]
    issue = rounds.record_check(station, seed["worker"], [1], "Tropft am Flansch")
    db.session.commit()
    assert issue.failed == ["Keine Leckage"] and rounds.overview()["issues"] == 1
    rounds.resolve(issue, seed["admin"])
    with pytest.raises(StorageError):
        rounds.resolve(issue, seed["admin"])
    db.session.commit()
    assert rounds.overview()["issues"] == 0


def test_pages_scan_and_push(app, as_admin, as_worker, as_viewer, db, seed):
    assert as_worker.post("/rundgang/neu", data={"name": "X"}).status_code == 403
    html = as_admin.get("/rundgang").get_data(as_text=True)
    assert "Vorschläge" in html and "Fermenter" in html
    r = as_admin.post("/rundgang/neu", data={"vorschlag": "0"})
    station = CheckStation.query.one()
    assert r.headers["Location"].endswith(f"/station/{station.code}") and "Rührwerke laufen" in station.items
    # Scannen öffnet die Checkliste
    assert as_worker.get(f"/scan?code={station.code.lower()}").headers["Location"].endswith(f"/station/{station.code}")
    html = as_worker.get(f"/station/{station.code}").get_data(as_text=True)
    assert 'id="kontrolle"' in html and "Rührwerke laufen" in html
    assert 'id="kontrolle"' not in as_viewer.get(f"/station/{station.code}").get_data(as_text=True)

    # alles in Ordnung → zurück zur Übersicht
    r = as_worker.post(f"/station/{station.code}/kontrolle", data={})
    assert r.headers["Location"].endswith("/rundgang")
    # Mangel mit Foto → Push an Admins (Thema von sich aus an)
    db.session.add(PushSubscription(user_id=seed["admin"].id, endpoint="https://push.example/a", p256dh="k", auth="a"))
    db.session.commit()
    as_worker.post(f"/station/{station.code}/kontrolle", data={"failed": ["0"], "note": "Rührwerk 2 steht",
                                                                 "photos": (jpeg(), "mangel.jpg")},
                   content_type="multipart/form-data")
    check = StationCheck.query.filter_by(ok=False).one()
    assert len(check.photos) == 1 and push.outbox[0]["title"] == "Rundgang: Fermenter"
    assert "Rührwerke laufen" in push.outbox[0]["body"]
    html = as_admin.get("/").get_data(as_text=True)
    assert "1 offener Mangel" in html                                          # Jetzt wichtig
    as_worker.post(f"/kontrolle/{check.id}/behoben")
    assert db.session.get(StationCheck, check.id).resolved_by_id == seed["worker"].id

    # Etiketten-PDF, Bearbeiten, Löschen nur ohne Kontrollen
    pdf = as_admin.post("/rundgang/etiketten.pdf", data={"codes": [station.code], "format": "2x2"})
    assert pdf.data[:4] == b"%PDF"
    as_admin.post(f"/station/{station.code}/bearbeiten", data={"name": "Fermenter 1", "items": "Temperatur",
                                                               "interval_days": "2", "active": "1"})
    assert (station.name, station.item_list, station.interval_days) == ("Fermenter 1", ["Temperatur"], 2)
    as_admin.post(f"/station/{station.code}/loeschen")
    assert db.session.get(CheckStation, station.id) is not None


def test_morning_push(app, db, seed):
    station = rounds.save_station(None, "Gasfackel Test", "Fackel bereit", 7)
    db.session.add(PushSubscription(user_id=seed["admin"].id, endpoint="https://push.example/a", p256dh="k", auth="a"))
    db.session.commit()
    with app.test_request_context("/"):
        from datetime import date
        assert rounds.morning_due(date.today()) == 1
    assert push.outbox[-1]["title"] == "Rundgang: 1 Station nicht kontrolliert"
    rounds.record_check(station, seed["worker"], [])
    db.session.commit()
    with app.test_request_context("/"):
        assert rounds.morning_due(date.today()) == 0
