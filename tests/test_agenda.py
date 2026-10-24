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
        assert len([e for e in items if e["kind"] != "feiertag"]) == 3

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
    assert body.count("BEGIN:VEVENT") - body.count("UID:feiertag-") == 3
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


def test_holidays():
    assert agenda.easter(2026) == date(2026, 4, 5) and agenda.easter(2027) == date(2027, 3, 28)
    h = agenda.holidays(2026, "NI")
    assert h[date(2026, 4, 3)] == "Karfreitag" and h[date(2026, 5, 14)] == "Christi Himmelfahrt"
    assert h[date(2026, 5, 25)] == "Pfingstmontag" and h[date(2026, 10, 31)] == "Reformationstag" and len(h) == 10
    by = agenda.holidays(2026, "BY")
    assert by[date(2026, 6, 4)] == "Fronleichnam" and by[date(2026, 11, 1)] == "Allerheiligen" and date(2026, 10, 31) not in by
    assert agenda.holidays(2026, "SN")[date(2026, 11, 18)] == "Buß- und Bettag"
    assert agenda.holidays(2022, "SN")[date(2022, 11, 16)] == "Buß- und Bettag"      # 23.11. war ein Mittwoch
    assert date(2026, 3, 8) in agenda.holidays(2026, "BE") and date(2026, 9, 20) in agenda.holidays(2026, "TH")
    assert agenda.holidays(2026, "-") == {}
    assert all(len(agenda.holidays(2026, s)) >= 9 for s in agenda.STATES)


def test_state_setting_and_week(app, as_admin, as_worker, db, seed):
    from app.models import Setting
    as_admin.post("/admin/einstellungen", data={"farm_name": "Hof", "holiday_state": "BY", "blight_from_month": "5",
                                                "blight_to_month": "9"})
    assert Setting.all()["holiday_state"] == "BY"
    with app.test_request_context("/"):
        names = [e["title"] for e in agenda.events(date(2026, 11, 1), date(2026, 11, 1))[0]]
    assert names == ["Allerheiligen"]
    as_admin.post("/admin/einstellungen", data={"farm_name": "Hof", "holiday_state": "XX", "blight_from_month": "5",
                                                "blight_to_month": "9"})
    assert Setting.all()["holiday_state"] == "NI"                         # Unsinn → Voreinstellung
    # Startseite: Wochenübersicht
    as_worker.post("/kalender/termin", data={"title": "Tierarzt im Stall", "all_day": "1",
                                             "start_date": date.today().isoformat()})
    html = as_worker.get("/").get_data(as_text=True)
    assert "Diese Woche" in html and "Tierarzt im Stall" in html and "week-today" in html

    # Auswahl „Alle“ / „Nur meine“ (Standard: alle), wird im Cookie gemerkt
    as_admin.post("/kalender/termin", data={"title": "Bankgespräch", "all_day": "1", "start_date": date.today().isoformat(),
                                            "participant_ids": [str(seed["admin"].id)]})
    html = as_worker.get("/").get_data(as_text=True)
    assert "Bankgespräch" in html and "Tierarzt im Stall" in html
    html = as_worker.get("/?woche=meine").get_data(as_text=True)
    assert "Bankgespräch" not in html and "Tierarzt im Stall" in html
    assert "Bankgespräch" not in as_worker.get("/").get_data(as_text=True)          # gemerkt
    assert "Bankgespräch" in as_admin.get("/?woche=meine").get_data(as_text=True)    # für den Admin ist es seiner
    assert "Bankgespräch" in as_worker.get("/?woche=alle").get_data(as_text=True)
    assert "Bankgespräch" in as_worker.get("/?woche=quatsch").get_data(as_text=True)


def test_entries_and_tasks(app, as_admin, as_worker, as_viewer, db, seed):
    from app import tasks
    from app.models import CalendarEntry
    worker, admin = seed["worker"], seed["admin"]
    # Mitarbeiter trägt einen mehrtägigen Termin für sich ein, Betrachter darf nicht
    as_worker.post("/kalender/termin", data={"title": "Urlaub Max", "kind": "abwesenheit", "all_day": "1",
                                             "start_date": "2026-11-09", "end_date": "2026-11-13",
                                             "participant_ids": [str(worker.id)]})
    assert as_viewer.post("/kalender/termin", data={"title": "x", "start_date": "2026-11-09"}).status_code == 403
    urlaub = CalendarEntry.query.one()
    assert urlaub.days == 5 and urlaub.participants == [worker] and urlaub.created_by == worker
    # Admin: jährliche Frist mit Uhrzeit, für alle
    as_admin.post("/kalender/termin", data={"title": "Anerkennung Pflanzgut, Antrag", "kind": "frist",
                                            "start_date": "2026-05-15", "start_time": "10:00", "end_time": "11:30",
                                            "repeat": "jaehrlich"})
    frist = CalendarEntry.query.filter_by(kind="frist").one()
    assert frist.start_time.hour == 10 and frist.repeat == "jaehrlich"
    # Fehler: Ende vor Anfang
    as_admin.post("/kalender/termin", data={"title": "Falsch", "start_date": "2026-05-15", "end_date": "2026-05-10", "all_day": "1"})
    assert CalendarEntry.query.count() == 2

    with app.test_request_context("/"):
        task = tasks.create_task(admin, title="Lager ausfegen", due_on=date(2026, 11, 10), assignees=[admin])
        db.session.commit()
        items, _ = agenda.events(date(2026, 11, 1), date(2026, 11, 30), date(2026, 11, 1), worker)
        kinds = {e["title"]: e for e in items}
        assert kinds["Urlaub Max"]["end"] == date(2026, 11, 13) and kinds["Urlaub Max"]["mine"]
        assert "Lager ausfegen" not in kinds                               # fremde Aufgabe sieht Max nicht
        assert "Lager ausfegen" in {e["title"] for e in agenda.events(date(2026, 11, 1), date(2026, 11, 30),
                                                                      date(2026, 11, 1), admin)[0]}
        # Wiederholung: jährliche Frist taucht 2027 wieder auf
        items27, _ = agenda.events(date(2027, 5, 1), date(2027, 5, 31), date(2027, 5, 1), worker)
        assert [e["date"] for e in items27 if e["kind"] == "eintrag"] == [date(2027, 5, 15)]
        # Mehrtägig im Raster an jedem Tag; Filter „nur meine“
        data = agenda.month(2026, 11, date(2026, 11, 1), admin, mine_only=True)
        days = [d["date"] for w in data["weeks"] for d in w if any(e["title"] == "Urlaub Max" for e in d["events"])]
        assert days == []                                                  # Admin ist nicht beteiligt
        data = agenda.month(2026, 11, date(2026, 11, 1), worker)
        days = [d["date"] for w in data["weeks"] for d in w if any(e["title"] == "Urlaub Max" for e in d["events"])]
        assert days == [date(2026, 11, d) for d in range(9, 14)]

    html = as_worker.get("/kalender?jahr=2026&monat=11").get_data(as_text=True)
    assert "Urlaub Max" in html and "Termin" in html
    html = as_worker.get("/kalender?jahr=2026&monat=5").get_data(as_text=True)
    assert "10:00–11:30" in html and "Christi Himmelfahrt" in html

    # iCal: Uhrzeit in UTC (Sommerzeit: 10:00 Berlin = 08:00 UTC), mehrtägig als DATE (Ende exklusiv)
    with app.test_request_context("/"):
        body = agenda.ics("Test", "https://hof.example/", worker, today=date(2027, 1, 1))
    assert "DTSTART:20270515T080000Z" in body and "DTEND:20270515T093000Z" in body
    assert "DTSTART;VALUE=DATE:20261109" in body and "DTEND;VALUE=DATE:20261114" in body
    assert "SUMMARY:Anerkennung Pflanzgut\\, Antrag" in body

    # Bearbeiten/Löschen: der Ersteller und Admins
    other = app.test_client()
    from .conftest import login, make_user
    make_user("jan", "mitarbeiter")
    login(other, "jan")
    assert other.post(f"/kalender/termin/{urlaub.id}", data={"title": "x", "start_date": "2026-11-09"}).status_code == 403
    as_worker.post(f"/kalender/termin/{urlaub.id}", data={"title": "Urlaub Max (verlängert)", "kind": "abwesenheit",
                                                          "all_day": "1", "start_date": "2026-11-09", "end_date": "2026-11-16"})
    assert urlaub.title == "Urlaub Max (verlängert)" and urlaub.days == 8 and urlaub.participants == []
    assert "Urlaub Max" in as_viewer.get(f"/kalender/termin/{urlaub.id}").get_data(as_text=True)
    as_admin.post(f"/kalender/termin/{frist.id}/loeschen")
    assert CalendarEntry.query.count() == 1


def test_waiting_periods(app, as_worker, db, seed):
    from app.models import FieldMeasure, PlotYear
    today = date.today()
    plot = seed["plot"]
    entry = PlotYear(plot=plot, year=today.year, crop="Kartoffel")
    db.session.add(entry)
    db.session.flush()
    db.session.add_all([
        FieldMeasure(plot_year_id=entry.id, kind="pflanzenschutz", name="Mittel A", done_on=today - timedelta(days=3),
                     note="Wartezeit 14 Tage"),
        FieldMeasure(plot_year_id=entry.id, kind="pflanzenschutz", name="Mittel B", done_on=today - timedelta(days=10),
                     note="Wartezeit 7 Tage"),                             # schon vorbei
    ])
    db.session.commit()
    with app.test_request_context():
        items, _undated = agenda.events(today, today + timedelta(days=30), today, seed["viewer"], mine_only=True)
        (event,) = [e for e in items if e["kind"] == "wartezeit"]
        assert event["date"] == today + timedelta(days=11) and event["title"] == "Acker Nord erntefähig"
        assert "Mittel A" in event["note"] and "Kartoffel" in event["note"]
        # außerhalb des Zeitraums nicht
        assert not [e for e in agenda.events(today, today + timedelta(days=5), today)[0] if e["kind"] == "wartezeit"]
        # geerntet → verschwindet
        entry.harvested_on = today
        db.session.commit()
        assert not [e for e in agenda.events(today, today + timedelta(days=30), today)[0] if e["kind"] == "wartezeit"]
    entry.harvested_on = None
    db.session.commit()
    day = today + timedelta(days=11)
    html = as_worker.get(f"/kalender?jahr={day.year}&monat={day.month}").get_data(as_text=True)
    assert "Acker Nord erntefähig" in html and "Ende der Wartezeit" in html
