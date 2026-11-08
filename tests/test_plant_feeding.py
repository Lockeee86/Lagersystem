"""Fütterungsplan der Biogasanlage: Standard je Wochentag, eintragen, abgeben, 1×/2×, abhaken, Push, Seiten."""
from datetime import date, datetime, timedelta

import pytest

from app import agenda, plant_feeding as pf, push
from app.models import CalendarEntry, FeedingDay, FeedingShift, PushSubscription
from app.storage import StorageError
from tests.conftest import make_user

MONDAY = date(2026, 11, 9)          # Mo, KW 46 – erfunden, kein Feiertag in Niedersachsen


@pytest.fixture(autouse=True)
def _clear_outbox():
    push.outbox.clear()
    yield
    push.outbox.clear()


@pytest.fixture
def team(db, seed):
    anna = make_user("anna", "mitarbeiter")
    for u in (seed["admin"], seed["worker"], anna):
        u.biogas = True
        u.feeds_plant = True
        db.session.add(PushSubscription(user=u, endpoint=f"https://push.example/{u.username}", p256dh="a", auth="b"))
    seed["viewer"].biogas = True
    db.session.commit()
    return {**seed, "anna": anna}


def test_defaults_and_days(db, team):
    assert pf.defaults() == [1] * 8
    pf.save_defaults([1, 1, 1, 1, 1, 2, 2, 2])
    db.session.commit()
    assert pf.default_times(MONDAY) == 1 and pf.default_times(MONDAY + timedelta(days=5)) == 2
    assert pf.default_times(date(2026, 12, 25)) == 2                     # Feiertag
    days = pf.week(MONDAY, team["worker"], today=MONDAY)
    assert [d["times"] for d in days] == [1, 1, 1, 1, 1, 2, 2]
    assert [len(d["slots"]) for d in days] == [1, 1, 1, 1, 1, 2, 2] and days[0]["today"]
    assert days[5]["slots"][0]["label"] == "Früh" and days[0]["slots"][0]["label"] == "Fütterung"


def test_take_give_up_and_rights(db, team):
    worker, anna, admin, viewer = team["worker"], team["anna"], team["admin"], team["viewer"]
    pf.save_defaults([2] * 8)
    shift = pf.take(MONDAY, 1, worker, today=MONDAY)
    pf.take(MONDAY, 2, worker, today=MONDAY)                              # beide Fütterungen erlaubt
    db.session.commit()
    with pytest.raises(StorageError):
        pf.take(MONDAY, 1, anna, today=MONDAY)                            # schon vergeben
    with pytest.raises(StorageError):
        pf.take(MONDAY + timedelta(days=1), 1, anna, user=worker, today=MONDAY)   # andere nur als Planer
    with pytest.raises(StorageError):
        pf.take(MONDAY, 1, viewer, today=MONDAY)                          # Betrachter
    with pytest.raises(StorageError):
        pf.take(MONDAY - timedelta(days=1), 1, worker, today=MONDAY)      # Vergangenheit
    with pytest.raises(StorageError):
        pf.take(MONDAY + timedelta(weeks=pf.WEEKS_AHEAD, days=1), 1, worker, today=MONDAY)
    pf.take(MONDAY + timedelta(weeks=pf.WEEKS_AHEAD), 1, worker, today=MONDAY)   # genau 8 Wochen geht
    other = pf.take(MONDAY + timedelta(days=1), 1, admin, user=anna, today=MONDAY)  # Planer trägt ein
    assert other.user_id == anna.id
    with pytest.raises(StorageError):
        pf.give_up(shift, anna, today=MONDAY)                             # fremde Fütterung
    assert pf.give_up(shift, worker, today=MONDAY)[:2] == (MONDAY, 1)
    db.session.commit()
    assert pf.free_slots(MONDAY) == [1]


def test_switch_times(db, team):
    worker, anna, admin = team["worker"], team["anna"], team["admin"]
    tuesday = MONDAY + timedelta(days=1)
    with pytest.raises(StorageError):
        pf.set_times(tuesday, 2, worker, today=MONDAY)                    # im Voraus nur Planer
    pf.set_times(tuesday, 2, admin, today=MONDAY)
    db.session.commit()
    assert pf.times_on(tuesday) == 2 and db.session.get(FeedingDay, tuesday).times == 2
    pf.take(tuesday, 1, worker, today=MONDAY)
    pf.take(tuesday, 2, anna, today=MONDAY)
    db.session.commit()
    # am Tag selbst darf umstellen, wer dann füttert: 2 → 1, die Abend-Person fällt weg
    removed = pf.set_times(tuesday, 1, worker, today=tuesday)
    db.session.commit()
    assert removed == anna and pf.times_on(tuesday) == 1
    assert [s.user_id for s in FeedingShift.query.filter_by(day=tuesday)] == [worker.id]
    assert db.session.get(FeedingDay, tuesday) is None                     # wieder wie der Standard
    with pytest.raises(StorageError):
        pf.set_times(tuesday, 2, anna, today=tuesday)                      # nicht eingetragen
    # nur Abend belegt: beim Umstellen auf 1× rückt die Person auf die einzige Fütterung
    wed = MONDAY + timedelta(days=2)
    pf.set_times(wed, 2, admin, today=MONDAY)
    pf.take(wed, 2, anna, today=MONDAY)
    assert pf.set_times(wed, 1, admin, today=MONDAY) is None
    db.session.commit()
    assert FeedingShift.query.filter_by(day=wed).one().slot == 1


def test_done_push_and_calendar(app, db, team):
    worker, anna, admin = team["worker"], team["anna"], team["admin"]
    pf.save_defaults([2] * 8)
    tuesday = MONDAY + timedelta(days=1)
    shift = pf.take(MONDAY, 1, worker, today=MONDAY)
    pf.take(tuesday, 1, anna, today=MONDAY)
    db.session.commit()
    with pytest.raises(StorageError):
        pf.mark_done(pf.take(tuesday, 2, worker, today=MONDAY), worker, today=MONDAY)   # erst am Tag
    pf.mark_done(shift, worker, "Dosierer gereinigt", today=MONDAY)
    db.session.commit()
    assert shift.done_at and shift.done_by_id == worker.id and shift.note == "Dosierer gereinigt"
    pf.undo_done(shift, worker)
    assert shift.done_at is None

    ctx = app.test_request_context("/")             # wie push.daily: Links in den Nachrichten brauchen url_for
    ctx.push()
    jobs = set()
    done = lambda name, day: (name, day) in jobs            # noqa: E731
    mark = lambda name, day: jobs.add((name, day))          # noqa: E731
    # 12 Uhr: Früh nicht abgehakt → Planer (Admin); 16 Uhr: morgen Abend ist frei; 19 Uhr: Erinnerung für morgen
    pf.check(datetime(2026, 11, 9, 12, 5), done, mark)
    missed = [m for m in push.outbox if "nicht abgehakt" in m["title"]]
    assert {m["user_id"] for m in missed} == {admin.id} and len(missed) == 2   # heute Früh + gestern Abend (leer)
    assert any("niemand eingetragen" in m["body"] for m in missed) and any(worker.name in m["body"] for m in missed)
    push.outbox.clear()
    pf.check(datetime(2026, 11, 9, 19, 5), done, mark)
    titles = {(m["user_id"], m["title"]) for m in push.outbox}
    assert (anna.id, "Morgen fütterst du") in titles and (worker.id, "Morgen fütterst du") in titles
    assert all(t != "Fütterung morgen noch frei" for _u, t in titles)    # Di: Früh Anna, Abend Max
    pf.check(datetime(2026, 11, 9, 20, 0), done, mark)                     # nur einmal am Tag
    assert len(push.outbox) == len(titles)
    push.outbox.clear()
    pf.check(datetime(2026, 11, 10, 16, 1), done, mark)                    # Mi ist noch frei
    assert {m["title"] for m in push.outbox} >= {"Fütterung morgen noch frei"}

    # Kalender: eigene Fütterungen als „meine“
    events = pf.calendar_events(MONDAY, MONDAY + timedelta(days=6), agenda._event, anna)
    assert any(e["mine"] and anna.name in e["title"] for e in events)
    assert sum(1 for e in events if e["mine"]) == 1
    ctx.pop()


def test_pages(app, db, team, as_admin, as_worker):
    pf.save_defaults([1] * 8)
    db.session.commit()
    today = date.today()
    page = as_worker.get("/biogas/fuetterung").get_data(as_text=True)
    assert "Ich übernehme" in page and f"KW {pf.monday_of(today).isocalendar()[1]}" in page
    r = as_worker.post("/biogas/fuetterung/eintragen", data={"tag": today.isoformat(), "slot": "1"})
    assert r.status_code == 302
    shift = FeedingShift.query.one()
    assert shift.user_id == team["worker"].id
    # Startseite: „Fütterung Biogas“ mit Erledigt-Knopf
    home = as_worker.get("/").get_data(as_text=True)
    assert "Fütterung Biogas" in home and f"/biogas/fuetterung/{shift.id}/erledigt" in home
    as_worker.post(f"/biogas/fuetterung/{shift.id}/erledigt", data={"back": "/"})
    assert shift.done_at is not None
    # morgen frei → „Jetzt wichtig“
    assert "Fütterung noch frei" in as_worker.get("/").get_data(as_text=True)
    # Umstellen am Tag: wer füttert darf, Standard nur Admin
    as_worker.post("/biogas/fuetterung/tag", data={"tag": today.isoformat(), "times": "2"})
    assert pf.times_on(today) == 2
    assert as_worker.post("/biogas/fuetterung/standard", data={"d0": "2"}).status_code == 403
    as_admin.post("/biogas/fuetterung/standard", data={f"d{i}": "2" for i in range(8)})
    assert pf.defaults() == [2] * 8
    # Abgeben → Push an das Team (nicht an sich selbst)
    other = pf.take(today + timedelta(days=1), 1, team["worker"])
    db.session.commit()
    push.outbox.clear()
    as_worker.post(f"/biogas/fuetterung/{other.id}/abgeben")
    assert FeedingShift.query.filter_by(id=other.id).first() is None
    assert push.outbox and all(m["user_id"] != team["worker"].id for m in push.outbox)
    # Werkstatt-Bildschirm und Kalender laufen mit
    assert as_admin.get("/kalender").status_code == 200


def test_absence_hint(app, db, team, as_worker):
    tomorrow = date.today() + timedelta(days=1)
    entry = CalendarEntry(title="Urlaub", kind="abwesenheit", start_date=tomorrow, created_by=team["worker"])
    db.session.add(entry)
    db.session.commit()
    assert pf.absent_warning(team["worker"], tomorrow)
    r = as_worker.post("/biogas/fuetterung/eintragen", data={"tag": tomorrow.isoformat(), "slot": "1"},
                       follow_redirects=True)
    assert "laut Kalender abwesend" in r.get_data(as_text=True)


def test_feeds_plant_flag(app, db, team, as_admin, as_worker):
    """Nur wer den Haken „BGA füttern“ hat, trägt sich ein und bekommt Erinnerungen; Planer planen auch ohne."""
    worker, admin = team["worker"], team["admin"]
    worker.feeds_plant = False
    admin.feeds_plant = False
    db.session.commit()
    pf.save_defaults([1] * 8)
    db.session.commit()
    today = date.today()
    with pytest.raises(StorageError):
        pf.take(today, 1, worker)
    page = as_worker.get("/biogas/fuetterung").get_data(as_text=True)
    assert "Ich übernehme" not in page and "BGA füttern" in page
    assert "Fütterung noch frei" not in as_worker.get("/").get_data(as_text=True)
    assert "feeding_mine" not in worker.push_topics()
    # Admin ohne Haken plant trotzdem: trägt Anna ein, stellt um – sich selbst aber nicht
    shift = pf.take(today, 1, admin, user=team["anna"])
    assert pf.can_switch(admin, today + timedelta(days=3))
    with pytest.raises(StorageError):
        pf.take(today + timedelta(days=1), 1, admin)
    db.session.commit()
    assert shift.user_id == team["anna"].id
    # Haken in der Benutzerverwaltung
    as_admin.post("/admin/benutzer", data={"id": worker.id, "username": "max", "role": "mitarbeiter", "language": "de",
                                           "active": "1", "biogas": "1", "feeds_plant": "1"})
    assert worker.feeds_plant and pf.is_member(worker)
    assert "BGA füttern" in as_admin.get("/admin/benutzer").get_data(as_text=True)


def test_feeder_without_biogas(app, db, team, as_worker):
    """„BGA füttern“ ohne Rubrik Biogas: Biogas zeigt nur den Fütterungsplan, eintragen und Erinnerungen gehen."""
    worker = team["worker"]
    worker.biogas = False
    worker.feeds_plant = True
    db.session.commit()
    pf.save_defaults([1] * 8)
    db.session.commit()
    assert pf.is_member(worker) and worker in pf.members() and "feeding_mine" in worker.push_topics()
    page = as_worker.get("/biogas/fuetterung").get_data(as_text=True)
    assert "Ich übernehme" in page and "Noch niemand hat den Haken" not in page
    assert 'href="/biogas/budget"' not in page                         # keine anderen Biogas-Reiter
    for url in ("/biogas", "/biogas/budget", "/biogas/wartung", "/biogas/auswertung"):
        assert as_worker.get(url).status_code == 404, url
    as_worker.post("/biogas/fuetterung/eintragen", data={"tag": date.today().isoformat(), "slot": "1"})
    assert FeedingShift.query.filter_by(user_id=worker.id).count() == 1
    home = as_worker.get("/").get_data(as_text=True)
    assert "Fütterung Biogas" in home and 'href="/biogas/fuetterung"' in home
    # ohne „BGA füttern“ und ohne Biogas: gar nichts
    worker.feeds_plant = False
    db.session.commit()
    assert as_worker.get("/biogas/fuetterung").status_code == 404
