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
    point = feedin.save_point(None, "Einspeisung Test", "300", [a.id, b.id], unit="kw")
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
    point = feedin.save_point(None, "Einspeisung Klein", "400", [m.id], unit="kw")
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
    r = as_admin.post("/biogas/einspeisestelle", data={"name": "", "amount": "100000"}, follow_redirects=True)
    assert "Namen für die Einspeisestelle" in r.get_data(as_text=True)
    as_admin.post(f"/biogas/einspeisestelle/{plant.id}", data={"name": "Einspeisung Neu", "amount": "1.000,5", "unit": "kw"})
    assert plant.name == "Einspeisung Neu" and plant.max_kw == Decimal("1000.5") and plant.machines.count() == 0
    as_admin.post(f"/biogas/einspeisestelle/{plant.id}", data={"name": "Einspeisung Neu", "amount": "2.628.000"})
    assert plant.annual_kwh == Decimal("2628000") and plant.max_kw is None
    assert plant.budget_kwh(2024) == 2628000 and plant.mean_kw == pytest.approx(300)       # kWh gelten jedes Jahr gleich
    r = as_admin.post(f"/biogas/einspeisestelle/{plant.id}", data={"name": "X", "amount": "12"}, follow_redirects=True)
    assert "Jahresmenge in kWh" in r.get_data(as_text=True)

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
    point = feedin.save_point(None, "Einspeisung Lücke", "2.628.000", [m.id])           # 300 kW × 8760 h
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


CSV = """\ufeffeinspeisestelle;monat;zeitraum_von;zeitraum_bis;rechnung_nr;anlage;kategorie;bik_code;bezeichnung_original;menge_kwh;preis_ct_kwh;betrag_eur;quelldatei
Nord;2026-01;2026-01-01;2026-01-31;1;A1;Marktwert steuerbare ET;X;Marktwert;10000.0;10.0;1000.0;a.txt
Nord;2026-01;2026-01-01;2026-01-31;1;A1;Verringerung (BiK1011);X;Verringerung;500.0;-0.2;-1.0;a.txt
Nord;2026-01;2026-01-01;2026-01-31;1;A1;Flexibilitätsprämie;X;Flex;10500.0;2.0;210.0;a.txt
Nord;2026-01;2026-01-01;2026-01-31;1;A2;Marktwert steuerbare ET;X;Marktwert;40000.0;10.0;4000.0;a.txt
Nord;2026-01;2026-01-01;2026-01-31;1;A2;Verringerung (BiK1011);X;Verringerung;1000.0;-0.2;-2.0;a.txt
Nord;2026-03;2026-03-01;2026-03-31;2;A1;Marktwert steuerbare ET;X;Marktwert;20000.0;9.0;1800.0;b.txt
Nord;2025-JAHR;2025-01-01;2025-12-31;3;A1;Flexibilitätsprämie;X;Flex;600000.0;2.0;1.0;c.txt
Süd;2026-01;2026-01-01;2026-01-31;4;A1;Flexibilitätsprämie;X;Flex;7000.0;2.0;1.0;d.txt
Süd;kaputt;;;;A1;Flexibilitätsprämie;X;Flex;7.0;2.0;1.0;d.txt
"""


def test_csv_import(app, as_admin, db, seed, plant):
    parsed = feedin.parse_csv(CSV.encode())
    nord = parsed["points"]["Nord"]
    assert nord[(2026, 1)] == {"kwh": 10500 + 41000, "reduced": 1500}      # A1 Flexprämie, A2 Marktwert + Verringerung
    assert nord[(2026, 3)]["kwh"] == 20000 and nord[(2025, 0)]["kwh"] == 600000
    assert parsed["points"]["Süd"][(2026, 1)]["kwh"] == 7000 and parsed["skipped"] == 1
    with pytest.raises(feedin.FeedImportError):
        feedin.parse_csv(b"a;b\n1;2\n")

    from io import BytesIO
    plant.name = "nord"
    db.session.commit()
    html = as_admin.post("/biogas/einspeisestelle/import", data={"file": (BytesIO(CSV.encode()), "positionen.csv")},
                         content_type="multipart/form-data").get_data(as_text=True)
    assert "In der Datei: Nord" in html and "Jahresabrechnung" in html
    assert f'<option value="{plant.id}" selected>' in html                   # Name passt (ohne Groß/Klein)
    r = as_admin.post("/biogas/einspeisestelle/import/uebernehmen", data={"point0": str(plant.id), "point1": ""},
                      follow_redirects=True)
    assert "3 Abrechnungswerte übernommen" in r.get_data(as_text=True)
    jan = plant.months.filter_by(year=2026, month=1).one()
    assert float(jan.kwh) == 51500 and float(jan.reduced_kwh) == 1500
    s = feedin.status(plant, 2025, today=date(2026, 3, 1))
    assert s["annual"] == 600000 and s["used"] == 600000 and s["missing"] == []
    s = feedin.status(plant, 2026, today=date(2026, 4, 1))
    assert s["reduced"] == 1500 and s["billed"] == 2
    r = as_admin.post("/biogas/einspeisestelle/import/uebernehmen", data={}, follow_redirects=True)
    assert "Vorschau ist abgelaufen" in r.get_data(as_text=True)


def test_csv_import_creates_points(as_admin, db, seed):
    """Ohne Einspeisestellen: Import ist trotzdem da und legt sie aus der Datei an."""
    from io import BytesIO
    assert "Abrechnungen importieren (CSV)" in as_admin.get("/biogas").get_data(as_text=True)
    html = as_admin.post("/biogas/einspeisestelle/import", data={"file": (BytesIO(CSV.encode()), "p.csv")},
                         content_type="multipart/form-data").get_data(as_text=True)
    assert "neu anlegen: Nord" in html
    r = as_admin.post("/biogas/einspeisestelle/import/uebernehmen",
                      data={"point0": "new", "amount0": "1.752.000", "point1": ""}, follow_redirects=True)
    assert "3 Abrechnungswerte übernommen" in r.get_data(as_text=True)
    point = FeedPoint.query.one()
    assert point.name == "Nord" and float(point.annual_kwh) == 1752000


REV_CSV = """einspeisestelle;monat;anlage;kategorie;menge_kwh;preis_ct_kwh;betrag_eur
Nord;2026-01;A1;Marktwert steuerbare ET;10000;10.0;1000.00
Nord;2026-01;A1;Marktprämie;10000;-11.0;-1100.00
Nord;2026-01;A1;Gülle-Bonus;10000;-8.0;-800.00
Nord;2026-01;A1;Verringerung (BiK1011);2000;-0.2;-4.00
Nord;2026-01;A1;Flexibilitätsprämie;12000;-2.0;-240.00
Nord;2026-01;A1;(B)(Z)   MP steuerbare     ET Korr;100;-0.2;-0.20
"""


def test_revenue(db, seed, plant):
    parsed = feedin.parse_csv(REV_CSV.encode())
    assert "MP steuerbare ET Korr" in parsed["revenues"][("Nord", 2026, 1)]
    feedin.apply_import(parsed, {"Nord": plant})
    db.session.commit()
    r = feedin.revenue(plant, 2026)
    m = r["months"][0]
    assert m["groups"]["grund"] == pytest.approx(1104.2) and m["groups"]["guelle"] == pytest.approx(800)
    assert m["groups"]["flex"] == pytest.approx(240) and m["total"] == pytest.approx(2144.2)
    assert m["netz"] == pytest.approx(1144.2) and m["market"] == pytest.approx(1000)
    assert m["ct"] == pytest.approx(100 * 2144.2 / 12000)
    assert m["lost"] == pytest.approx((1900 - 1000) / 10000 * 2000)             # (AW + Boni − Marktwert) × Verringerung
    assert feedin.rate_eur_per_kwh(plant, date(2026, 5, 1)) == pytest.approx(2144.2 / 12000)
    feedin.apply_import(parsed, {"Nord": plant})                                  # zweimal: nichts doppelt
    db.session.commit()
    assert feedin.revenue(plant, 2026)["year"]["total"] == pytest.approx(2144.2)
    assert feedin.revenue(plant, 2025) is None


def test_plan(plant):
    s = feedin.status(plant, 2025, today=date(2025, 7, 16))
    p = feedin.plan(s, today=date(2025, 7, 16))
    targets = {m["month"]: m["target"] for m in p["months"]}
    assert list(targets) == [7, 8, 9, 10, 11, 12]
    assert sum(targets.values()) == pytest.approx(s["budget"] - (s["estimated"] - s["months"][6]["kwh"] if s["months"][6]["kwh"] else s["estimated"]))
    assert targets[12] / targets[7] == pytest.approx(1.2 / 0.85)                 # Winter mehr
    assert p["now"]["month"] == 7 and p["now"]["expected"] == pytest.approx(targets[7] * 15 / 31)
    assert feedin.plan(feedin.status(plant, 2025, today=date(2026, 1, 5)), today=date(2026, 1, 5)) is None


def test_plan_caps_at_capacity(db, seed):
    m = bhkw(db, seed, 51, "Motor E", 100)
    point = feedin.save_point(None, "Einspeisung Eng", "2.000.000", [m.id])      # mehr, als 100 kW schaffen
    s = feedin.status(point, 2025, today=date(2025, 10, 1))
    p = feedin.plan(s, today=date(2025, 10, 1))
    assert all(x["target"] == pytest.approx(100 * 24 * {10: 31, 11: 30, 12: 31}[x["month"]]) for x in p["months"])
    assert all(x["hours_per_day"] == pytest.approx(24) for x in p["months"])


def test_loss_and_plant_checks(app, as_admin, as_worker, db, seed, plant):
    from app import biogas
    from app.models import Machine, MachineEvent
    motor = plant.machines.order_by(Machine.name).first()                         # Motor A, 6000 kWh am Tag
    today = date(2025, 7, 1)
    for d in range(60):
        row = ChpDay.query.filter_by(machine_id=motor.id, day=today - timedelta(days=60 - d)).one()
        row.revenue_eur = Decimal("600")
    db.session.commit()
    assert biogas.loss_per_hour(motor, today) == pytest.approx(6000 / 24 * 0.1)   # Ø 250 kW × 10 ct (Day-Ahead)
    assert biogas.loss_per_hour(motor, date(2020, 1, 1)) is None

    r = as_admin.post("/biogas/pruefliste", follow_redirects=True)
    assert "Prüfliste angelegt" in r.get_data(as_text=True)
    plant_machine = Machine.query.filter_by(category="anlage").one()
    assert len(plant_machine.tasks) >= 10
    html = as_admin.get("/biogas").get_data(as_text=True)
    assert "Prüffristen der Anlage" in html and "Emissionsmessung BHKW" in html and "noch nie eingetragen" in html
    seed["worker"].biogas = False
    db.session.commit()
    assert as_worker.get(f"/maschine/{plant_machine.id}").status_code in (403, 404)  # ohne Rubrik Biogas unsichtbar
