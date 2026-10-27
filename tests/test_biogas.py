"""Biogas/SKVE: die API wird durch `fake_api` ersetzt – erfundene Motoren und Werte, kein Netz."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import biogas, machines
from app.models import ChpDay, Machine, Setting

TZ = ZoneInfo("Europe/Berlin")


def run_window(day):
    """Motor 11 läuft jeden Tag 06–14 Uhr Ortszeit mit 500 kW (8 h, 4.000 kWh)."""
    start = datetime(day.year, day.month, day.day, 6, tzinfo=TZ).astimezone(timezone.utc)
    return start, start + timedelta(hours=8)


@pytest.fixture
def fake_api(app, monkeypatch):
    app.config.update(SKVE_API_KEY="test-key", SKVE_GAS_STORAGE_ID="7")
    calls = []

    def fake_get(path, start, end):
        calls.append(path)
        times, values = [], []
        t = start.replace(minute=start.minute - start.minute % 15, second=0, microsecond=0)
        while t < end:
            local_day = t.astimezone(TZ).date()
            on_from, on_to = run_window(local_day)
            if path.endswith("/chp/11/schedule"):
                values.append(500.0 if on_from <= t < on_to else 0.0)
            elif path.endswith("/chp/12/schedule"):
                values.append(0.0)
            elif "prices/DAA" in path:
                values.append(100.0)                                   # 100 €/MWh
            else:
                values.append(55.0)                                    # Gasspeicher %
            times.append(int(t.timestamp()))
            t += timedelta(minutes=15)
        if "gas-storage" in path:
            return {"timeSeries": {"customer_view": {"timestamps": times, "values": values}}}
        if "prices" in path:
            return {"timeSeries": {"price": {"timestamps": times, "values": values}}}
        return {"times": times, "values": values}

    monkeypatch.setattr(biogas, "_get", fake_get)
    biogas.clear_cache()
    yield calls
    biogas.clear_cache()


def make_bhkw(db, seed, chp_id, name):
    m = machines.create_machine(name, "bhkw", seed["admin"], counter_unit="h", skve_chp_id=chp_id, power_kw=500)
    db.session.commit()
    return m


def test_aggregate(app):
    day = date(2026, 7, 1)
    start, end = run_window(day)
    points = [(start + timedelta(minutes=15 * i), 400.0) for i in range(8)]       # 2 h
    points.append((start + timedelta(hours=2), 0.0))
    points += [(start + timedelta(hours=5, minutes=15 * i), 200.0) for i in range(4)]  # 1 h, zweiter Start
    price = {ts: 50.0 for ts, _kw in points}
    row = biogas.aggregate(points, price)[day]
    assert row["hours"] == pytest.approx(3.0) and row["kwh"] == pytest.approx(1000.0) and row["starts"] == 2
    assert row["revenue"] == pytest.approx(50.0)                                 # 1 MWh × 50 €


def test_sync_counts_hours_and_drives_maintenance(app, db, seed, fake_api):
    motor = make_bhkw(db, seed, 11, "BHKW Test 1")
    idle = make_bhkw(db, seed, 12, "BHKW Test 2")
    oil = next(t for t in motor.tasks if t.name == "Ölwechsel")
    assert oil.interval_hours == 2000 and motor.counter_unit == "h"
    # Stand am Motor abgelesen: 10.000 h vor 10 Tagen, Ölwechsel bei 8.100 h
    today = date(2026, 7, 20)
    machines.set_counter(motor, 10000, seed["admin"], day=today - timedelta(days=10))
    oil.last_done_counter, oil.last_done_on = 8100, today - timedelta(days=100)
    db.session.commit()

    now = datetime(2026, 7, 20, 3, 0, tzinfo=TZ)
    biogas.sync(app, now=now)
    assert ChpDay.query.filter_by(machine_id=motor.id).count() == biogas.FIRST_SYNC_DAYS
    yesterday = ChpDay.query.filter_by(machine_id=motor.id, day=today - timedelta(days=1)).one()
    assert float(yesterday.hours) == 8.0 and float(yesterday.kwh) == 4000.0 and float(yesterday.revenue_eur) == 400.0
    # 9 Tage nach der Ablesung × 8 h
    assert db.session.get(Machine, motor.id).counter == 10072
    # Motor ohne Ablesung: zählt ab jetzt vom bisherigen Stand weiter
    assert db.session.get(Machine, idle.id).skve_base_day == today - timedelta(days=1)
    assert biogas.status()["ok"] and db.session.get(Setting, biogas.SYNC_KEY).value == today.isoformat()

    # einmal am Tag: zweiter Lauf ruft nichts ab; vor 2 Uhr auch nicht
    fake_api.clear()
    assert biogas.sync(app, now=now + timedelta(hours=3)) is None and not fake_api
    # nächster Tag: nur die letzten Tage neu, Zähler +8
    biogas.sync(app, now=now + timedelta(days=1))
    assert db.session.get(Machine, motor.id).counter == 10080
    assert ChpDay.query.filter_by(machine_id=motor.id).count() == biogas.FIRST_SYNC_DAYS + 1

    # Prognose: 8 h/Tag, Ölwechsel fällig bei 10.100 h → noch 20 h → 3 Tage
    forecast = {f["task"].name: f for f in biogas.maintenance_forecast(db.session.get(Machine, motor.id),
                                                                       today + timedelta(days=1))}
    assert forecast["Ölwechsel"]["left"] == 20 and forecast["Ölwechsel"]["due_on"] == today + timedelta(days=4)
    assert forecast["Zündkerzen"]["left"] is None                      # noch nie erledigt → Hinweis
    assert machines.task_status(oil)["state"] == "soon"                # greift in die normale Wartung


def test_errors_are_kept_and_reported(app, db, seed, monkeypatch):
    app.config["SKVE_API_KEY"] = "falsch"
    make_bhkw(db, seed, 11, "BHKW Test 1")

    def broken(path, start, end):
        raise biogas.SkveError("401 – API-Schlüssel ungültig")
    monkeypatch.setattr(biogas, "_get", broken)
    biogas.sync(app, now=datetime(2026, 7, 20, 3, 0, tzinfo=TZ))
    assert biogas.status() == {"at": biogas.status()["at"], "ok": False, "message": "401 – API-Schlüssel ungültig"}
    assert db.session.get(Setting, biogas.SYNC_KEY) is None            # nächster Versuch beim nächsten Lauf
    biogas.clear_cache()
    assert biogas.live()["error"].startswith("401")


def test_not_configured(app, as_admin, db, seed):
    app.config["SKVE_API_KEY"] = ""
    assert biogas.sync(app) is None and biogas.live() == {"configured": False}
    html = as_admin.get("/biogas").get_data(as_text=True)
    assert "Speicher-Kraftwerk verbinden" in html and "SKVE_API_KEY" in html


def test_pages(app, as_admin, as_worker, as_viewer, db, seed, fake_api):
    motor = make_bhkw(db, seed, 11, "BHKW Test 1")
    machines.set_counter(motor, 5000, seed["admin"])
    db.session.commit()
    as_worker.post("/biogas/abrufen")                                    # nur Admin
    assert ChpDay.query.count() == 0
    r = as_admin.post("/biogas/abrufen", follow_redirects=True)
    assert "SKVE-Daten abgerufen" in r.get_data(as_text=True) and ChpDay.query.count() > 0
    html = as_viewer.get("/biogas").get_data(as_text=True)
    assert "BHKW Test 1" in html and "Fahrplan nächste 24 Stunden" in html and "Gasspeicher" in html
    assert "Nächste Wartungen nach Laufzeit" in html and "Ölanalyse" in html and "Erlös" not in html
    assert "Erlös (Day-Ahead)" in as_admin.get("/biogas").get_data(as_text=True)
    html = as_viewer.get(f"/maschine/{motor.id}").get_data(as_text=True)
    assert 'id="laufzeit"' in html and "Laufzeit (SKVE)" in html
    # Biogas erscheint unter „Mehr“
    assert 'href="/biogas"' in as_viewer.get("/bereich/mehr").get_data(as_text=True)

    # Motor-Nr. an der Maschine: doppelt geht nicht
    other = machines.create_machine("BHKW Test 2", "bhkw", seed["admin"])
    db.session.commit()
    as_admin.post(f"/maschine/{other.id}/bearbeiten", data={"name": "BHKW Test 2", "category": "bhkw",
                                                           "bhkw_form": "1", "skve_chp_id": "11", "power_kw": "250"})
    assert db.session.get(Machine, other.id).skve_chp_id is None
    as_admin.post(f"/maschine/{other.id}/bearbeiten", data={"name": "BHKW Test 2", "category": "bhkw",
                                                           "bhkw_form": "1", "skve_chp_id": "12", "power_kw": "250"})
    assert (db.session.get(Machine, other.id).skve_chp_id, db.session.get(Machine, other.id).power_kw) == (12, 250)


def test_dashboard_card(app, as_viewer, db, seed, fake_api):
    # Betrachter haben die BHKW-Karte von sich aus nicht – selbst einschalten
    seed["viewer"].home_add = "bhkw"
    motor = make_bhkw(db, seed, 11, "BHKW Test 1")
    machines.set_counter(motor, 9000, seed["admin"])
    oil = next(t for t in motor.tasks if t.name == "Ölwechsel")
    oil.last_done_counter, oil.last_done_on = 7100, date.today() - timedelta(days=90)
    db.session.commit()
    # ohne Zwischenspeicher: keine API-Abfrage beim Laden der Startseite
    html = as_viewer.get("/").get_data(as_text=True)
    assert 'id="bhkw"' in html and "BHKW Test 1" in html and "Ölwechsel: in 100 h" in html and not fake_api
    biogas.refresh_live(app)                                   # wie der Zeitplan
    assert fake_api
    html = as_viewer.get("/").get_data(as_text=True)
    # aktueller Wert aus dem Zwischenspeicher: läuft der Motor (Fahrplan 6–14 Uhr), die Leistung, sonst der nächste Start
    running = biogas.live(cached_only=True)["motors"][0]["running"]
    assert ("/ 500 kW" if running else "Nächster geplanter Start") in html
    # ohne BHKW keine Karte
    motor.active = False
    db.session.commit()
    assert 'id="bhkw"' not in as_viewer.get("/").get_data(as_text=True)


def test_best_windows(app):
    now = datetime(2026, 10, 24, 20, 0, tzinfo=TZ).astimezone(timezone.utc)          # Samstagabend
    outlook = []
    t = now.replace(minute=0)
    while t < now + timedelta(days=3):
        local = t.astimezone(TZ)
        price = 120.0
        if local.date() == date(2026, 10, 25) and 10 <= local.hour < 14:
            price = 20.0                                                               # Sonntag Mittag günstig
        if local.date() == date(2026, 10, 25) and 2 <= local.hour < 6:
            price = -30.0                                                              # nachts negativ, aber außerhalb
        if local.date() == date(2026, 10, 26) and 8 <= local.hour < 12:
            price = 60.0
        outlook.append((t, price, local.date() > date(2026, 10, 25)))
        t += timedelta(minutes=15)
    windows = biogas.best_windows(548, 4, outlook, now, work=(6, 18))
    first = windows[0]
    assert (first["start"].hour, first["end"].hour, first["start"].date()) == (10, 14, date(2026, 10, 25))
    assert first["cost"] == pytest.approx(548 * 4 / 1000 * 20) and not first["forecast"]
    assert windows[1]["start"].date() == date(2026, 10, 26) and windows[1]["forecast"]       # je Tag einer
    assert len({w["start"].date() for w in windows}) == len(windows)
    # bis zum Fälligkeitstag: nur Samstag/Sonntag möglich
    until = datetime(2026, 10, 26, 0, 0, tzinfo=TZ).astimezone(timezone.utc)
    assert {w["start"].date() for w in biogas.best_windows(548, 4, outlook, now, until=until, work=(6, 18))} \
        == {date(2026, 10, 25)}
    # rund um die Uhr erlaubt → die negativen Preise in der Nacht gewinnen, Stillstand spart Geld
    night = biogas.best_windows(548, 4, outlook, now, work=(0, 24))[0]
    assert night["start"].hour == 2 and night["cost"] < 0


def test_maintenance_windows_page(app, as_admin, as_viewer, db, seed, fake_api):
    motor = make_bhkw(db, seed, 11, "BHKW Test 1")
    machines.set_counter(motor, 9000, seed["admin"], day=date.today() - timedelta(days=30))
    oil = next(t for t in motor.tasks if t.name == "Ölwechsel")
    assert oil.downtime_hours == 4
    oil.last_done_counter, oil.last_done_on = 7000, date.today() - timedelta(days=200)       # schon fällig
    db.session.commit()
    rows, error = biogas.maintenance_windows()
    assert error is None and [r["task"].name for r in rows] == ["Ölwechsel"] and rows[0]["windows"]
    assert all(6 <= w["start"].hour and w["end"].hour <= 18 for w in rows[0]["windows"])
    html = as_viewer.get("/biogas").get_data(as_text=True)
    assert 'id="fenster"' in html and "BHKW Test 1: Ölwechsel" in html and "€/MWh" in html
    assert "Als Aufgabe" not in html and "Erlös" not in html                     # Geld nur für Admins
    html = as_admin.get("/biogas").get_data(as_text=True)
    assert "Als Aufgabe" in html and "datum=" in html and "Erlös fehlen" in html
    # Arbeitszeit einstellen
    as_admin.post("/biogas/arbeitszeit", data={"start": "7", "end": "16"})
    assert biogas.window_hours() == (7, 16)
    r = as_admin.post("/biogas/arbeitszeit", data={"start": "18", "end": "6"}, follow_redirects=True)
    assert "gültige Arbeitszeit" in r.get_data(as_text=True) and biogas.window_hours() == (7, 16)
    # Aufgabe mit dem Datum des Fensters vorbelegt
    day = (date.today() + timedelta(days=2)).isoformat()
    html = as_admin.get(f"/aufgaben?neu=1&wartung={oil.id}&datum={day}").get_data(as_text=True)
    assert f'value="{day}"' in html


def test_week_report_and_push(app, db, seed, fake_api):
    from app import push
    from app.models import PushSubscription
    motor = make_bhkw(db, seed, 11, "BHKW Test 1")
    idle = make_bhkw(db, seed, 12, "BHKW Test 2")
    db.session.add(PushSubscription(user_id=seed["admin"].id, endpoint="https://push.example/a", p256dh="k", auth="a"))
    db.session.commit()
    monday = date(2026, 10, 26)
    biogas.sync(app, now=datetime(2026, 10, 26, 3, 0, tzinfo=TZ))
    report = biogas.week_report(monday)
    assert (report["monday"], report["sunday"]) == (date(2026, 10, 19), date(2026, 10, 25))
    rows = {r["machine"].name: r for r in report["rows"]}
    assert rows["BHKW Test 1"]["hours"] == pytest.approx(56.0) and rows["BHKW Test 2"]["idle_days"] == 7
    assert report["kwh"] == pytest.approx(28000.0) and report["avg_price"] == pytest.approx(100.0)
    push.outbox.clear()
    with app.test_request_context("/"):
        assert biogas.week_push(monday) == 1
    message = push.outbox[-1]
    assert message["title"] == "Biogas letzte Woche: 28,0 MWh"
    assert "56 Betriebsstunden" in message["body"] and "BHKW Test 2: 7 Tage Stillstand" in message["body"]
    assert "Ø 100 €/MWh" in message["body"]


def test_next_start(app, db, seed, fake_api):
    make_bhkw(db, seed, 11, "BHKW Test")
    day = date(2026, 7, 1)
    on_from, on_to = run_window(day)
    stopped = biogas.live(now=on_from - timedelta(hours=2))["motors"][0]
    assert not stopped["running"] and stopped["next_start"] == on_from
    label = biogas.start_label(stopped, day, now=on_from - timedelta(hours=2))
    assert label == {"day": "today", "time": "06:00"}
    evening = on_to + timedelta(hours=1)                           # heute fertig → morgen 6 Uhr
    motor = biogas.live(now=evening)["motors"][0]
    assert biogas.start_label(motor, day, now=evening) == {"day": "tomorrow", "time": "06:00"}
    running = biogas.live(now=on_from + timedelta(hours=1))["motors"][0]
    assert running["running"] and biogas.start_label(running, day) is None
    assert biogas.start_label(stopped, day, now=on_from + timedelta(minutes=1)) is None    # Start schon vorbei


def test_flex_quality(app, db, seed):
    """Fahrplan-Güte: Erlös gegenüber denselben kWh in den teuersten Viertelstunden des Tages."""
    day = date(2026, 7, 2)
    midnight = datetime(2026, 7, 2, tzinfo=TZ).astimezone(timezone.utc)
    prices = {midnight + timedelta(minutes=15 * i): (200.0 if 68 <= i < 76 else 50.0) for i in range(96)}  # 17–19 Uhr teuer
    morning = [(midnight + timedelta(hours=6, minutes=15 * i), 500.0) for i in range(8)] + \
        [(midnight + timedelta(hours=8), 0.0)]                                    # 2 h morgens zu 50 €
    row = biogas.aggregate(morning, prices)[day]
    assert row["revenue"] == pytest.approx(50.0) and row["ideal"] == pytest.approx(200.0)    # 1 MWh × 200 €
    motor = make_bhkw(db, seed, 21, "BHKW Güte")
    db.session.add(ChpDay(machine=motor, day=day, hours=2, kwh=1000, revenue_eur=50, ideal_revenue_eur=200))
    db.session.add(ChpDay(machine=motor, day=day + timedelta(days=1), hours=2, kwh=1000, revenue_eur=150,
                          ideal_revenue_eur=200))
    db.session.commit()
    info = biogas.flex_quality(day, day + timedelta(days=1), [motor])
    assert info["total"]["percent"] == 50 and info["total"]["missed"] == pytest.approx(200.0)


def test_availability(app, as_admin, as_viewer, db, seed):
    """Verfügbarkeit laut Logbuch: Störung bis „behoben“ und Wartung mit ihrem Stillstand."""
    from app.models import MachineEvent
    motor = make_bhkw(db, seed, 31, "BHKW Logbuch")
    motor.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    start = datetime(2026, 9, 10, 8, tzinfo=TZ)
    db.session.add(MachineEvent(machine=motor, kind="stoerung", date=start.date(), user_id=seed["admin"].id,
                                created_at=start.astimezone(timezone.utc),
                                resolved_at=(start + timedelta(hours=12)).astimezone(timezone.utc)))
    task = motor.tasks[0]
    task.downtime_hours = 6
    db.session.add(MachineEvent(machine=motor, kind="wartung", date=date(2026, 9, 20), task=task,
                                user_id=seed["admin"].id))
    db.session.commit()
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    sept = biogas.availability(motor, date(2026, 9, 1), date(2026, 9, 30), now)
    assert sept["fault_h"] == pytest.approx(12) and sept["service_h"] == pytest.approx(6) and sept["faults"] == 1
    assert sept["percent"] == pytest.approx(round(100 * (1 - 18 / 720), 1))
    assert biogas.availability(motor, date(2025, 1, 1), date(2025, 1, 31), now)["percent"] is None   # vor dem Anlegen
    MachineEvent.query.filter_by(kind="stoerung").one().still_running = True             # lief eingeschränkt weiter
    db.session.commit()
    assert biogas.availability(motor, date(2026, 9, 1), date(2026, 9, 30), now)["fault_h"] == 0
    html = as_viewer.get("/biogas").get_data(as_text=True)
    assert "Verfügbarkeit laut Logbuch" in html and "BHKW Logbuch" in html
