from datetime import date, timedelta

from app import agenda, machines, purchases
from app.models import MaintenanceTask, User


def setup_data(db, seed, today):
    admin = seed["admin"]
    m = machines.create_machine("Fendt 724", "schlepper", admin)
    m.counter_unit, m.counter = "h", 4210
    db.session.add_all([
        MaintenanceTask(machine=m, name="TÜV", interval_months=24, last_done_on=today - timedelta(days=720)),  # bald
        MaintenanceTask(machine=m, name="UVV", interval_months=12, last_done_on=today - timedelta(days=400)),  # überfällig
        MaintenanceTask(machine=m, name="Ölwechsel", interval_hours=500, last_done_counter=3700),              # Zähler
    ])
    req = purchases.create_request(admin, "Keilriemen; 8PK1500, Lüfter", quantity="2 Stück")
    purchases.update_request(req, admin, "bestellt", supplier="Landtechnik Meyer", expected_on=today + timedelta(days=3))
    other = purchases.create_request(admin, "Netze")                     # ohne Liefertermin → kein Termin
    db.session.commit()
    return m, req, other


def test_events_and_month(app, db, seed):
    today = date.today()
    with app.test_request_context("/"):
        m, req, _other = setup_data(db, seed, today)
        items, undated = agenda.events(today - timedelta(days=60), today + timedelta(days=60), today)
        titles = {e["title"]: e for e in items}
        assert titles["Fendt 724: TÜV"]["color"] == "warning" and titles["Fendt 724: UVV"]["color"] == "danger"
        delivery = titles["Lieferung: Keilriemen; 8PK1500, Lüfter"]
        assert delivery["date"] == today + timedelta(days=3) and delivery["note"] == "2 Stück · Landtechnik Meyer"
        assert [u["task"].name for u in undated] == ["Ölwechsel"]      # nach Zähler, ohne Datum
        assert len(items) == 3

        data = agenda.month(today.year, today.month, today)
        assert len(data["weeks"][0]) == 7 and data["weeks"][0][0]["date"].weekday() == 0
        assert any(day["today"] for week in data["weeks"] for day in week)
        assert [o["task"].name for o in data["overdue"]] == ["UVV"]          # auch im aktuellen Monat sichtbar


def test_page_and_feed(app, as_admin, as_worker, as_viewer, db, seed):
    today = date.today()
    with app.test_request_context("/"):
        setup_data(db, seed, today)
    html = as_viewer.get("/kalender").get_data(as_text=True)
    assert "Kalender" in html and "Ölwechsel" in html and "Abo-Link erstellen" in html
    other_month = (today.replace(day=1) + timedelta(days=40))
    assert as_viewer.get(f"/kalender?jahr={other_month.year}&monat={other_month.month}").status_code == 200
    assert as_viewer.get("/kalender?monat=13").status_code == 404

    # Abo-Link: persönlich, ohne Anmeldung abrufbar, in der Sprache des Benutzers
    as_worker.post("/kalender/abo")
    worker = User.query.filter_by(username="max").one()
    token = worker.calendar_token
    assert token and len(token) >= 30
    html = as_worker.get("/kalender").get_data(as_text=True)
    assert f"webcal://localhost/kalender/{token}.ics" in html

    r = app.test_client().get(f"/kalender/{token}.ics")
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    body = r.get_data(as_text=True)
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert body.count("BEGIN:VEVENT") == 3
    assert "SUMMARY:Lieferung: Keilriemen\\; 8PK1500\\, Lüfter" in body           # RFC 5545 escaping
    assert f"DTSTART;VALUE=DATE:{today + timedelta(days=3):%Y%m%d}" in body
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))      # gefaltet

    worker.language = "pl"
    db.session.commit()
    body = app.test_client().get(f"/kalender/{token}.ics").get_data(as_text=True)
    assert "SUMMARY:Dostawa: Keilriemen" in body

    # neuer Link → alter ungültig; Unsinn → 404; gesperrter Benutzer → 404
    as_worker.post("/kalender/abo", data={"neu": "1"})
    assert app.test_client().get(f"/kalender/{token}.ics").status_code == 404
    assert app.test_client().get("/kalender/kurz.ics").status_code == 404
    worker.active = False
    db.session.commit()
    assert app.test_client().get(f"/kalender/{worker.calendar_token}.ics").status_code == 404


def test_fold():
    line = "SUMMARY:" + "Ölwechsel " * 20
    folded = agenda._fold(line)
    parts = folded.split("\r\n ")
    assert all(len(p.encode()) <= 75 for p in parts) and "".join(parts) == line
