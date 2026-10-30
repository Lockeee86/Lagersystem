"""Jahresbudget je Einspeisestelle (Höchstbemessungsleistung) – nur erfundene Zahlen."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import feedin, machines, push
from app.models import ChpDay, FeedPoint, PushSubscription


def bhkw(db, seed, chp_id, name, kw):
    m = machines.create_machine(name, "bhkw", seed["admin"], counter_unit="h", skve_chp_id=chp_id, power_kw=kw)
    db.session.commit()
    return m


@pytest.fixture
def plant(db, seed):
    """Zwei Motoren (250 + 500 kW) an einer Einspeisestelle mit 300 kW; erstes Halbjahr 2025 je Tag 8000 kWh."""
    a, b = bhkw(db, seed, 21, "Motor A", 250), bhkw(db, seed, 22, "Motor B", 500)
    point = feedin.save_point(None, "Einspeisung Test", "300", [a.id, b.id])
    day = date(2025, 1, 1)
    while day < date(2025, 7, 1):
        db.session.add(ChpDay(machine_id=a.id, day=day, hours=Decimal("24"), kwh=Decimal("6000")))
        db.session.add(ChpDay(machine_id=b.id, day=day, hours=Decimal("4"), kwh=Decimal("2000")))
        day += timedelta(days=1)
    db.session.commit()
    return point


def test_status_from_schedule(plant):
    s = feedin.status(plant, 2025, today=date(2025, 7, 1))
    assert s["budget"] == 300 * 8760 and s["used"] == pytest.approx(181 * 8000)
    assert s["elapsed"] == 181 and s["left_days"] == 184 and s["capacity"] == 750
    assert s["projected"] == pytest.approx(181 * 8000 / 181 * 365)
    assert s["level"] == "over" and s["diff"] > 0
    remaining = 300 * 8760 - 181 * 8000
    assert s["avg_kw_left"] == pytest.approx(remaining / (184 * 24))
    assert s["full_hours_left"] == pytest.approx(remaining / 750)
    assert s["months"][0]["source"] == "fahrplan" and s["months"][6]["kwh"] is None


def test_billed_months_replace_schedule(plant):
    feedin.save_months(plant, 2025, {1: "200.000", 2: ""})
    s = feedin.status(plant, 2025, today=date(2025, 7, 1))
    assert (s["months"][0]["kwh"], s["months"][0]["source"]) == (200000.0, "abrechnung") and s["billed"] == 1
    assert s["used"] == pytest.approx(181 * 8000 - 31 * 8000 + 200000)
    feedin.save_months(plant, 2025, {1: ""})                      # leer = wieder Fahrplan
    assert feedin.status(plant, 2025, today=date(2025, 7, 1))["billed"] == 0
    from app.storage import StorageError
    with pytest.raises(StorageError):
        feedin.save_months(plant, 2025, {3: "viel"})
    s = feedin.status(plant, 2025, today=date(2026, 3, 1))         # Vorjahr: ganzes Jahr vorbei
    assert s["elapsed"] == 365 and s["left_days"] == 0


def test_tight_and_ok(db, seed):
    m = bhkw(db, seed, 31, "Motor C", 400)
    point = feedin.save_point(None, "Einspeisung Klein", "400", [m.id])
    for i in range(10):
        db.session.add(ChpDay(machine_id=m.id, day=date(2025, 1, 1) + timedelta(days=i), hours=Decimal("20"),
                              kwh=Decimal("8000")))
    db.session.commit()
    assert feedin.status(point, 2025, today=date(2025, 1, 11))["level"] == "ok"          # 333 kW im Schnitt
    point.max_kw = Decimal("338")
    assert feedin.status(point, 2025, today=date(2025, 1, 11))["level"] == "tight"


def test_price_threshold():
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    outlook = [(start + timedelta(hours=h), float(h), False) for h in range(48)]
    assert feedin.price_threshold(6, outlook) == 36.0               # teuerste 12 von 48 Stunden
    assert feedin.price_threshold(24, outlook) is None and feedin.price_threshold(6, []) is None


def test_pages_and_push(app, as_admin, as_worker, db, seed, plant):
    seed["worker"].biogas = True
    db.session.commit()
    html = as_admin.get("/biogas?jahr=2025").get_data(as_text=True)
    assert "Jahresbudget 2025" in html and "Einspeisung Test" in html and "über dem Budget" in html
    assert "Monatsmengen aus der Abrechnung 2025" in html
    assert "Monatsmengen aus der Abrechnung" not in as_worker.get("/biogas?jahr=2025").get_data(as_text=True)
    assert as_worker.post(f"/biogas/einspeisestelle/{plant.id}/monate", data={"jahr": "2025"}).status_code == 403
    as_admin.post(f"/biogas/einspeisestelle/{plant.id}/monate", data={"jahr": "2025", "m2": "123456"})
    assert feedin.months(plant, 2025, date(2025, 7, 1))[1]["kwh"] == 123456.0
    r = as_admin.post("/biogas/einspeisestelle", data={"name": "", "max_kw": "100"}, follow_redirects=True)
    assert "Namen für die Einspeisestelle" in r.get_data(as_text=True)
    as_admin.post(f"/biogas/einspeisestelle/{plant.id}", data={"name": "Einspeisung Neu", "max_kw": "1.000,5"})
    assert plant.name == "Einspeisung Neu" and plant.max_kw == Decimal("1000.5") and plant.machines.count() == 0

    plant.max_kw = Decimal("300")
    for m in feedin.candidates():
        m.feed_point_id = plant.id
    db.session.add(PushSubscription(user_id=seed["admin"].id, endpoint="https://push.example/f", p256dh="k", auth="a"))
    db.session.commit()
    push.outbox.clear()
    marks = set()
    with app.test_request_context("/"):
        feedin.check(datetime(2025, 7, 7, 8, 0), lambda k, d: (k, d) in marks, lambda k, d: marks.add((k, d)))
        feedin.check(datetime(2025, 7, 7, 9, 0), lambda k, d: (k, d) in marks, lambda k, d: marks.add((k, d)))
    sent = [m for m in push.outbox if m["tag"].startswith("feed-budget")]
    assert len(sent) == 1 and "Jahresbudget wird überschritten" in sent[0]["title"]
    as_admin.post(f"/biogas/einspeisestelle/{plant.id}/loeschen")
    assert FeedPoint.query.count() == 0


def test_missing_months_are_extrapolated(db, seed):
    """Fahrplan erst ab Mai (wie nach dem ersten SKVE-Abruf): Jan–Apr fehlen, hochgerechnet wird aus den Tagen mit Werten."""
    m = bhkw(db, seed, 41, "Motor D", 500)
    point = feedin.save_point(None, "Einspeisung Lücke", "300", [m.id])
    day = date(2025, 5, 1)
    while day < date(2025, 7, 1):
        db.session.add(ChpDay(machine_id=m.id, day=day, hours=Decimal("14"), kwh=Decimal("6800")))
        day += timedelta(days=1)
    db.session.commit()
    s = feedin.status(point, 2025, today=date(2025, 7, 1))
    assert s["missing"] == [1, 2, 3, 4] and s["gap_days"] == 120
    assert s["estimated"] == pytest.approx(181 * 6800) and s["projected"] == pytest.approx(365 * 6800)
    assert s["level"] == "ok"                                      # 300 kW Schnitt wären 7200 kWh am Tag
    feedin.save_months(point, 2025, {m_: "223200" for m_ in (1, 2, 3, 4)})
    assert feedin.status(point, 2025, today=date(2025, 7, 1))["missing"] == []
