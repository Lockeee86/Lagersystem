from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app import chickens, push, rounds
from app.models import CheckStation, EggEntry, Household, PushSubscription, Task
from app.storage import StorageError

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture(autouse=True)
def _clear_outbox():
    push.outbox.clear()
    yield
    push.outbox.clear()


@pytest.fixture
def keeper(db, seed):
    seed["worker"].chickens = True
    db.session.commit()
    return seed["worker"]


def test_logic(db, seed, keeper):
    today = date.today()
    start = today - timedelta(days=10)
    chickens.add_flock_event(start, "gekauft", 30, keeper, breed="Lohmann Braun", hatched_on=start - timedelta(weeks=20))
    chickens.add_flock_event(today, "raubtier", 2, keeper)
    with pytest.raises(StorageError):
        chickens.add_flock_event(today, "verendet", 100, keeper)                # mehr als im Bestand
    with pytest.raises(StorageError):
        chickens.add_flock_event(today, "unbekannt", 1, keeper)
    assert chickens.flock_size(today) == 28 and chickens.flock_size(today - timedelta(days=1)) == 30

    chickens.add_eggs(today, 20, 1, None, keeper)
    chickens.add_eggs(today, 4, 0, "Nest 3", keeper)
    with pytest.raises(StorageError):
        chickens.add_eggs(today, 3, 5, None, keeper)                            # mehr kaputt als gesammelt
    with pytest.raises(StorageError):
        chickens.add_eggs(today + timedelta(days=1), 3, 0, None, keeper)        # Zukunft
    assert chickens.eggs_on(today) == {"eggs": 24, "cracked": 1}

    north = chickens.save_household(None, "Haus Nord")
    with pytest.raises(StorageError):
        chickens.save_household(None, "haus nord")                               # gibt es schon
    chickens.give_eggs(today, north, 10, None, keeper)
    assert chickens.stock() == 13                                                # 24 − 1 kaputt − 10
    assert chickens.share_summary(today, today) == [(north, 10)]

    chickens.add_feed(today, Decimal("25"), Decimal("18.50"), "Legemehl", None, keeper)
    month = chickens.months(today)[0]
    assert month["eggs"] == 24 and month["given"] == 10 and month["feed_cost"] == Decimal("18.50")
    assert month["per_egg"] == Decimal("18.50") / 24
    assert chickens.feed_per_hen(today) > 0

    treatment = chickens.add_treatment(today - timedelta(days=2), "Wurmkur", "Vorbeugung", 7, None, keeper)
    assert chickens.blocked(today) == treatment and treatment.blocked_until == today + timedelta(days=5)
    assert chickens.blocked(today + timedelta(days=6)) is None
    db.session.commit()

    info = chickens.summary(today)
    assert info["today"]["eggs"] == 24 and info["flock"] == 28 and info["stock"] == 13 and info["blocked"]


def test_sunset():
    # Mitte Juni in Norddeutschland nach 21 Uhr, Ende Dezember vor 16:30 (Ortszeit)
    summer = chickens.sunset(date(2026, 6, 21), 52.4, 10.5).astimezone(BERLIN)
    winter = chickens.sunset(date(2026, 12, 21), 52.4, 10.5).astimezone(BERLIN)
    assert (21, 0) <= (summer.hour, summer.minute) <= (21, 45)
    assert (15, 50) <= (winter.hour, winter.minute) <= (16, 30)
    assert chickens.sunset(date(2026, 6, 21), 80, 0) is None                   # Polartag


def test_visibility(as_admin, as_worker, as_viewer, db, seed):
    """Rubrik nur für freigeschaltete Benutzer; Freischalten in der Benutzerverwaltung."""
    assert as_worker.get("/huehner").status_code == 403
    assert "/huehner" not in as_worker.get("/").get_data(as_text=True)
    as_admin.post("/admin/benutzer", data={"id": seed["worker"].id, "username": "max", "role": "mitarbeiter",
                                           "active": "1", "chickens": "1"})
    assert seed["worker"].chickens
    html = as_worker.get("/").get_data(as_text=True)
    assert "/huehner" in html and 'id="huehner"' in html                       # Rubrik und Karte auf der Startseite
    assert as_worker.get("/huehner").status_code == 200
    assert "Hühner" in as_worker.get("/bereich/mehr").get_data(as_text=True)
    # Betrachter mit Freischaltung sieht, trägt aber nicht ein
    seed["viewer"].chickens = True
    db.session.commit()
    assert as_viewer.get("/huehner").status_code == 200
    assert as_viewer.post("/huehner/eier", data={"eggs": "5"}).status_code == 403


def test_pages(as_worker, db, seed, keeper):
    today = date.today()
    assert as_worker.post("/huehner/herde", data={"reason": "gekauft", "count": "30", "breed": "Sussex"}).status_code == 302
    r = as_worker.post("/huehner/eier", data={"eggs": "22", "cracked": "2", "back": "/#huehner"})
    assert r.headers["Location"].endswith("/#huehner") and chickens.eggs_on(today)["eggs"] == 22
    as_worker.post("/huehner/haushalt", data={"name": "Haus Süd"})
    south = Household.query.filter_by(name="Haus Süd").one()
    as_worker.post("/huehner/verteilen", data={"household_id": south.id, "eggs": "12"})
    as_worker.post("/huehner/futter", data={"kg": "25,0", "cost": "17,90", "product": "Legemehl"})
    as_worker.post("/huehner/stallbuch", data={"product": "Wurmkur", "withdrawal_days": "7"})
    for url in ("/huehner", "/huehner/eier-haushalte", "/huehner/herde", "/huehner/futter", "/huehner/stallbuch"):
        html = as_worker.get(url).get_data(as_text=True)
        assert as_worker.get(url).status_code == 200, url
    assert "Haus Süd" in as_worker.get("/huehner/eier-haushalte").get_data(as_text=True)
    assert "Eier gesperrt bis einschließlich" in as_worker.get("/huehner").get_data(as_text=True)
    assert chickens.stock() == 8
    # Haushalt deaktivieren: nicht mehr in der Auswahl, Verlauf bleibt
    as_worker.post("/huehner/haushalt", data={"id": south.id, "name": "Haus Süd"})
    assert not south.active
    entry = EggEntry.query.first()
    as_worker.post(f"/huehner/eier/{entry.id}/loeschen")
    assert chickens.eggs_on(today)["eggs"] == 0
    assert html


def test_tasks_and_station(as_admin, as_worker, db, seed, keeper):
    as_worker.post("/huehner/aufgabe", data={"vorschlag": "0"})
    task = Task.query.one()
    assert task.area == "huehner" and task.recurrence == "woechentlich" and task in chickens.open_tasks()
    html = as_worker.get("/huehner").get_data(as_text=True)
    assert "Hühnerstall ausmisten" in html
    as_worker.post("/aufgaben/neu", data={"title": "Zaun flicken", "area": "huehner"})
    assert Task.query.filter_by(title="Zaun flicken").one().area == "huehner"

    # Station am Stall: gehört zur Rubrik Hühner, nicht zum Rundgang der Biogasanlage
    as_admin.post("/rundgang/neu", data={"area": "huehner", "vorschlag": "stall"})
    station = CheckStation.query.one()
    assert station.area == "huehner" and station.name == "Hühnerstall" and len(station.item_list) == 5
    assert not rounds.overview()["rows"] and not rounds.has_stations()
    page = as_worker.get(f"/station/{station.code}").get_data(as_text=True)
    assert 'href="/huehner"' in page
    subscribe(db, seed["admin"])
    subscribe(db, keeper)
    seed["admin"].chickens = True
    db.session.commit()
    r = as_admin.post(f"/station/{station.code}/kontrolle", data={"failed": "0", "note": "Tränke leer"})
    assert r.status_code == 302
    assert [m["user_id"] for m in push.outbox] == [keeper.id]                 # an die Hühner-Leute
    assert chickens.summary()["issues"] == 1


def subscribe(db, user):
    db.session.add(PushSubscription(user_id=user.id, endpoint=f"https://push.example/{user.username}", p256dh="k",
                                    auth="a"))
    db.session.commit()


def test_evening_push(app, db, seed, keeper, monkeypatch):
    subscribe(db, keeper)
    monkeypatch.setattr(chickens, "farm_sunset", lambda day: chickens.sunset(day, 52.4, 10.5))   # ohne Wetter-Abruf
    chickens.add_flock_event(date(2026, 10, 1), "gekauft", 20, keeper)
    db.session.commit()
    day = date(2026, 10, 23)
    push.daily(app, datetime(2026, 10, 23, 17, 0, tzinfo=BERLIN))
    assert not [m for m in push.outbox if m["tag"].startswith("eggs-")]
    push.daily(app, datetime(2026, 10, 23, 18, 5, tzinfo=BERLIN))
    assert [m["title"] for m in push.outbox if m["tag"].startswith("eggs-")] == ["Hühner: noch keine Eier eingetragen"]
    push.daily(app, datetime(2026, 10, 23, 18, 30, tzinfo=BERLIN))           # nur einmal am Tag
    assert len([m for m in push.outbox if m["tag"].startswith("eggs-")]) == 1
    # Klappe: von sich aus aus – eingeschaltet kommt sie nach Sonnenuntergang
    keeper.set_push_topics(keeper.push_topics())
    db.session.commit()
    set_at = chickens.farm_sunset(day)
    push.daily(app, (set_at + timedelta(minutes=20)).astimezone(BERLIN).replace(day=24))
    assert [m for m in push.outbox if m["tag"].startswith("coop-")]
    # Eier eingetragen → keine Erinnerung
    push.outbox.clear()
    chickens.add_eggs(date(2026, 10, 25), 10, 0, None, keeper)
    db.session.commit()
    push.daily(app, datetime(2026, 10, 25, 19, 0, tzinfo=BERLIN))
    assert not [m for m in push.outbox if m["tag"].startswith("eggs-")]
    assert set_at.tzinfo is not None and set_at.astimezone(timezone.utc)


def test_fair_order(as_worker, db, seed, keeper):
    """Wer am längsten nichts bekommen hat, steht vorn und ist im Formular vorgewählt."""
    today = date.today()
    a = chickens.save_household(None, "Haus A")
    b = chickens.save_household(None, "Haus B")
    c = chickens.save_household(None, "Haus C")
    chickens.give_eggs(today - timedelta(days=5), a, 10, None, keeper)
    chickens.give_eggs(today - timedelta(days=1), b, 10, None, keeper)
    db.session.commit()
    order = chickens.fair_order(today)
    assert [r["household"] for r in order] == [c, a, b]                       # C noch nie, dann A (vor 5 Tagen)
    assert order[1]["days_since"] == 5 and order[1]["last_eggs"] == 10
    chickens.give_eggs(today, c, 6, None, keeper)
    db.session.commit()
    assert chickens.fair_order(today)[0]["household"] == a
    assert chickens.summary(today)["next"]["household"] == a
    html = as_worker.get("/huehner/eier-haushalte").get_data(as_text=True)
    assert f'value="{a.id}" id="hh{a.id}" required checked' in html and "als Nächstes dran" in html
    assert "vor 5 Tagen" in html


def test_flock_age(as_worker, db, seed, keeper):
    """Alter beim Kauf in Monaten; Durchschnittsalter für Herde und Monatsauswertung."""
    day = date(2026, 3, 15)
    old = chickens.add_flock_event(day, "gekauft", 10, keeper, breed="Sussex", age_months=12)
    assert old.hatched_on == date(2025, 3, 15)
    chickens.add_flock_event(day, "gekauft", 10, keeper, age_months=4)
    chicks = chickens.add_flock_event(day, "geschluepft", 5, keeper)
    assert chicks.hatched_on == day
    with pytest.raises(StorageError):
        chickens.add_flock_event(day, "gekauft", 1, keeper, age_months=200)
    chickens.add_flock_event(day + timedelta(days=30), "geschlachtet", 4, keeper)    # vom ältesten abgezogen
    db.session.commit()
    later = date(2026, 6, 15)
    groups = {c["event"].id: c for c in chickens.cohorts(later)}
    assert groups[old.id]["left"] == 6 and groups[old.id]["age"] == 15 and groups[old.id]["start_age"] == 12
    assert chickens.age_months(date(2026, 1, 31), date(2026, 2, 28)) == 0
    # 6 × 15 + 10 × 7 + 5 × 3 Monate ≈ 8,2 (nach Tagen gerechnet)
    assert 7.8 <= chickens.flock_age(later) <= 8.4
    html = as_worker.get("/huehner/herde").get_data(as_text=True)
    assert "noch 6 von 10" in html and "beim Kauf 12 Monate" in html and "im Schnitt" in html
    r = as_worker.post("/huehner/herde", data={"reason": "gekauft", "count": "3", "age_months": "5"})
    assert r.status_code == 302
    assert "Ø Alter" in as_worker.get("/huehner/eier-haushalte").get_data(as_text=True)


def test_expected_laying(as_worker, db, seed, keeper):
    """Richtwert der Legeleistung für Legehybriden nach Alter, gewichtet nach Hühnern."""
    assert chickens.expected_rate(17 * 7) == 0 and chickens.expected_rate(30 * 7) == 95
    assert 88 < chickens.expected_rate(52 * 7) < 89
    assert chickens.is_hybrid("Lohmann Braun") and not chickens.is_hybrid("Sussex")
    day = date(2026, 3, 1)
    chickens.add_flock_event(day, "gekauft", 20, keeper, breed="Lohmann Braun", age_months=5)
    db.session.commit()
    later = date(2026, 6, 1)                                    # gut 8 Monate ≈ 35 Wochen
    assert chickens.expected_laying(later, later) == 94
    chickens.add_flock_event(day, "gekauft", 10, keeper, breed="Sussex", age_months=5)   # 1/3 Rassehühner
    db.session.commit()
    assert chickens.expected_laying(later, later) is None
    assert any(c["expected"] for c in chickens.cohorts(later))


def test_chickens_only_user(as_admin, as_worker, db, seed):
    """Benutzer „nur Hühner“: sieht ausschließlich Hühner, ihre Aufgaben und die Stallkontrolle."""
    from app.models import Task
    worker = seed["worker"]
    as_admin.post("/admin/benutzer", data={"id": worker.id, "username": "max", "role": "mitarbeiter",
                                           "language": "de", "active": "1", "chickens_only": "1"})
    assert worker.chickens_only and worker.chickens and worker.only_chickens
    r = as_worker.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/huehner")
    for url in ("/lager/uebersicht", "/schlaege", "/maschinen", "/biogas", "/sensoren", "/suche", "/kalender",
                "/bereich/feld", "/lieferscheine"):
        assert as_worker.get(url).status_code == 404, url
    html = as_worker.get("/huehner").get_data(as_text=True)
    tabs = html[html.index('class="tabbar'):html.index("</nav>", html.index('class="tabbar'))]
    assert 'href="/huehner/eier-haushalte"' in tabs and 'href="/aufgaben"' in tabs and "Lager" not in tabs
    assert 'action="/suche"' not in html
    other = Task(title="Ölwechsel Schlepper")
    hens = Task(title="Ausmisten", area="huehner")
    db.session.add_all([other, hens])
    db.session.commit()
    page = as_worker.get("/aufgaben?wer=alle").get_data(as_text=True)
    assert "Ausmisten" in page and "Ölwechsel" not in page and "– keine Maschine –" not in page
    assert as_worker.get(f"/aufgabe/{other.id}").status_code == 404
    assert as_worker.get(f"/aufgabe/{hens.id}").status_code == 200
    as_worker.post("/aufgaben/neu", data={"title": "Wasser auffüllen"})
    assert Task.query.filter_by(title="Wasser auffüllen").one().area == "huehner"
    assert set(worker.push_topics()) <= {k for k, t in __import__("app.models", fromlist=["PUSH_TOPICS"])
                                         .PUSH_TOPICS.items() if t["group"] == "Hühner"} | {"tasks_new", "tasks_due"}
    # Admins sind nie „nur Hühner“
    seed["admin"].chickens_only = True
    assert not seed["admin"].only_chickens
