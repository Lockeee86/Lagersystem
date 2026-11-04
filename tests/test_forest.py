from datetime import date, datetime, timedelta, timezone

import pytest

from app import agenda, forest, push
from app.models import ForestBurial, ForestContract, ForestTree, PushSubscription, Task
from app.storage import StorageError


@pytest.fixture(autouse=True)
def _clear_outbox():
    push.outbox.clear()
    yield
    push.outbox.clear()


@pytest.fixture
def woods(db, seed):
    seed["admin"].forest = True
    seed["worker"].forest = True
    forest.create_trees(1, 5, places=12, species="Buche", section="Nordhang")
    db.session.commit()
    return seed


def _tree(n):
    return ForestTree.query.filter_by(number=n).one()


def test_trees_and_places(db, woods):
    assert ForestTree.query.count() == 5 and len(_tree(1).places) == 12
    assert forest.create_trees(4, 7) == 2                                  # vorhandene bleiben
    with pytest.raises(StorageError):
        forest.create_trees(5, 1)
    assert forest.normalize_code("w-7") == "W-007" and forest.tree_by_code("W-007").number == 7
    assert forest.normalize_code("K-0007") is None

    tree = _tree(2)
    forest.save_tree(tree, 2, "Eiche", "Südhang", "familie", 6, "52,4", "10.5", "Am Weg", True)
    assert len(tree.places) == 6 and tree.lat == 52.4 and tree.kind == "familie"
    db.session.commit()
    with pytest.raises(StorageError):
        forest.save_tree(tree, 1, None, None, "familie", 6, None, None, None)   # Nummer vergeben
    with pytest.raises(StorageError):
        forest.save_tree(tree, 2, None, None, "familie", 6, "200", "10", None)  # Breite unmöglich
    db.session.rollback()

    rows = forest.parse_trees_csv("nummer;baumart;abschnitt;art;plaetze;breite;laenge\n"
                                  "3;Ahorn;Senke;Familie;8;52.1;10.2\n20;Buche;;;;;\n21;Eiche;;Einzelbaum;1;;\n".encode())
    assert forest.apply_trees(rows) == (2, 1)
    assert _tree(21).kind == "einzel" and _tree(21).kind_label == "Einzelbaum" and len(_tree(21).places) == 1
    assert _tree(3).kind == "familie" and _tree(20).kind == "gemeinschaft"
    assert _tree(3).species == "Ahorn" and len(_tree(3).places) == 8 and _tree(20).places
    with pytest.raises(StorageError):
        forest.parse_trees_csv(b"baum;art\n1;x\n")
    db.session.commit()
    items = forest.map_data()
    assert [i["id"] for i in items] == [2, 3]


def test_contract_burial_tasks(app, db, woods):
    admin, worker = woods["admin"], woods["worker"]
    tree = _tree(1)
    contract = forest.save_contract(None, admin, "Erika Muster", date(2026, 3, 2), tree.places[:2], years=99,
                                    price="1.250,00")
    db.session.commit()
    assert contract.number == "V-2026-001" and contract.ends_on == date(2125, 3, 2)
    assert str(contract.price) == "1250.00" and tree.places[0].state == "reserviert"
    with pytest.raises(StorageError):                                    # Platz schon vergeben
        forest.save_contract(None, admin, "Max Beispiel", date(2026, 3, 3), tree.places[1:3])
    db.session.rollback()

    day = date(2030, 6, 10)                                               # ein Montag
    burial = forest.save_burial(None, worker, tree.places[0], "Hans Muster", day, "11", "Bestattungen Beispiel")
    db.session.commit()
    assert burial.time == "11:00" and burial.contract == contract and tree.places[0].state == "belegt"
    assert burial.dig_task.due_on == date(2030, 6, 7) and burial.close_task.due_on == day   # Freitag davor
    assert burial.dig_task.area == forest.AREA and burial.dig_task.important
    with pytest.raises(StorageError):
        forest.save_burial(None, worker, tree.places[0], "Noch jemand", day)
    db.session.rollback()

    forest.save_burial(burial, worker, tree.places[0], "Hans Muster", day + timedelta(days=1), "10:30")
    db.session.commit()
    assert burial.dig_task.due_on == day and burial.close_task.due_on == day + timedelta(days=1)

    with app.test_request_context():
        events = forest.calendar_events(day, day + timedelta(days=3), agenda._event)
    assert len(events) == 1 and events[0]["time"].hour == 10
    with pytest.raises(StorageError):
        forest.delete_contract(contract)                                  # hat eine Beisetzung
    db.session.rollback()

    forest.delete_burial(burial)
    db.session.commit()
    assert ForestBurial.query.count() == 0 and Task.query.count() == 0
    forest.delete_contract(contract)
    db.session.commit()
    assert ForestContract.query.count() == 0 and tree.places[0].state == "frei"
    o = forest.overview()
    assert o["trees"] == 5 and o["frei"] == 60 and o["belegt"] == 0


def test_storm_push(app, db, woods, monkeypatch):
    from app import weather
    db.session.add(PushSubscription(user=woods["worker"], endpoint="https://push.example/1", p256dh="a", auth="b"))
    db.session.commit()
    now = datetime(2030, 2, 3, 6, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(weather, "get_weather", lambda: {"days": [{"date": date(2030, 2, 2), "gust": 95.0}]})
    marks = set()
    with app.test_request_context():
        assert forest.storm_check(now, lambda job, day: (job, day) in marks, lambda job, day: marks.add((job, day))) == 1
        assert forest.storm_check(now, lambda job, day: (job, day) in marks, lambda job, day: marks.add((job, day))) == 0
    assert "95" in push.outbox[0]["title"]


def test_pages(app, db, woods, as_admin, as_worker, as_viewer):
    for url in ("/wald", "/wald/karte", "/wald/baeume", "/wald/baeume?frei=1&abschnitt=Nordhang", "/wald/baum/1",
                "/wald/beisetzungen", "/wald/vertraege", "/", "/kalender", "/wald/vertrag/neu?baum=1&platz=1&platz=2", "/bereich/wald"):
        assert as_admin.get(url).status_code == 200, url
    assert as_viewer.get("/wald").status_code == 404                     # Rubrik nicht eingeschaltet
    assert as_worker.get("/wald/vertraege").status_code == 403
    assert "Erika" not in as_worker.get("/wald/baum/1").get_data(as_text=True)

    place_ids = [p.id for p in _tree(1).places[:2]]
    r = as_admin.post("/wald/vertrag", data={"holder": "Erika Muster", "signed_on": "2026-03-02", "years": "99",
                                             "place_id": place_ids})
    assert r.status_code == 302
    contract = ForestContract.query.one()
    assert as_admin.get(f"/wald/vertrag/{contract.id}").status_code == 200
    assert "Erika Muster" in as_admin.get("/wald/baum/1").get_data(as_text=True)

    r = as_worker.post("/wald/beisetzung", data={"tree": "1", "place": "1", "deceased": "Hans Muster",
                                                 "day": (date.today() + timedelta(days=5)).isoformat(), "time": "11:00"})
    assert r.status_code == 302 and ForestBurial.query.count() == 1
    for url in ("/", "/kalender"):
        assert as_worker.get(url).status_code == 200, url
    page = as_worker.get("/wald/beisetzungen").get_data(as_text=True)
    assert "Hans Muster" in page and "Grab ausheben" in page
    assert as_worker.get("/scan?code=W-001", follow_redirects=False).headers["Location"].endswith("/wald/baum/1")
    assert as_admin.post("/wald/baeume/anlegen", data={"first": "6", "last": "8", "places": "12"}).status_code == 302
    assert ForestTree.query.count() == 8


def test_forest_only_user(app, db, woods, as_admin, as_worker):
    """Benutzer „nur Bestattungswald“: sieht ausschließlich den Wald, seine Aufgaben und die Baumkontrolle."""
    from app import rounds
    from app.models import CheckStation, PUSH_TOPICS
    worker = woods["worker"]
    as_admin.post("/admin/benutzer", data={"id": worker.id, "username": "max", "role": "mitarbeiter",
                                           "language": "de", "active": "1", "only": "wald"})
    assert worker.forest_only and worker.forest and worker.only_forest and not worker.only_chickens
    assert worker.sees_forest and not worker.sees_biogas and not worker.sees_chickens
    r = as_worker.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/wald")
    for url in ("/lager/uebersicht", "/schlaege", "/maschinen", "/biogas", "/huehner", "/sensoren", "/suche",
                "/kalender", "/bereich/feld", "/lieferscheine", "/wald/vertraege"):
        assert as_worker.get(url).status_code in (403, 404), url
    for url in ("/wald", "/wald/karte", "/wald/baeume", "/wald/baum/1", "/wald/beisetzungen", "/bereich/wald",
                "/bereich/mehr", "/aufgaben", "/konto"):
        assert as_worker.get(url).status_code == 200, url
    html = as_worker.get("/wald").get_data(as_text=True)
    tabs = html[html.index('class="tabbar'):html.index("</nav>", html.index('class="tabbar'))]
    assert 'href="/wald/beisetzungen"' in tabs and 'href="/aufgaben"' in tabs and "Lager" not in tabs
    assert 'action="/suche"' not in html

    # Beisetzung eintragen → die Aufgaben sieht er, fremde nicht
    other = Task(title="Ölwechsel Schlepper")
    hens = Task(title="Ausmisten", area="huehner")
    db.session.add_all([other, hens])
    db.session.commit()
    as_worker.post("/wald/beisetzung", data={"tree": "1", "place": "1", "deceased": "Hans Muster",
                                             "day": (date.today() + timedelta(days=5)).isoformat()})
    page = as_worker.get("/aufgaben?wer=alle").get_data(as_text=True)
    assert "Grab ausheben" in page and "Ausmisten" not in page and "Ölwechsel" not in page
    assert as_worker.get(f"/aufgabe/{other.id}").status_code == 404
    as_worker.post("/aufgaben/neu", data={"title": "Wege freischneiden"})
    assert Task.query.filter_by(title="Wege freischneiden").one().area == "wald"

    # Baumkontrolle ja, Stallkontrolle nein
    db.session.add_all([CheckStation(code="R-07", name="Abschnitt Nord", area="wald"),
                        CheckStation(code="R-08", name="Stall", area="huehner")])
    db.session.commit()
    assert as_worker.get("/station/R-07").status_code == 200
    assert as_worker.get("/station/R-08").status_code == 404
    assert set(worker.push_topics()) <= {k for k, t in PUSH_TOPICS.items() if t["group"] == "Bestattungswald"} | \
        {"tasks_new", "tasks_due"}
    assert "forest_storm" in worker.push_topics()

    # Admins sind nie „nur Bestattungswald“
    woods["admin"].forest_only = True
    assert not woods["admin"].restricted and woods["admin"].sees_biogas


def test_selection_flow(app, db, woods):
    """Baumauswahl → Baum aussuchen → Vertrag → Beisetzung."""
    from app.models import ForestSelection
    admin, worker = woods["admin"], woods["worker"]
    day = date(2030, 6, 5)
    sel = forest.save_selection(None, worker, "Erika Muster", day, "14", phone="0123 456", deceased="Hans Muster",
                                places_wanted=2, kind_wanted="familie", guide=worker)
    db.session.commit()
    assert sel.status == "geplant" and sel.time == "14:00" and sel.task.area == forest.AREA
    assert sel.task.assignees == [worker] and sel.task.due_on == day and "Erika Muster" in sel.task.title
    with pytest.raises(StorageError):
        forest.save_selection(None, worker, "", day)
    db.session.rollback()

    # zweite Auswahl will dieselben Plätze → geht nicht
    other = forest.save_selection(None, worker, "Max Beispiel", day, places_wanted=1)
    forest.choose_tree(sel, worker, _tree(1))                       # ohne Nummern: die ersten freien
    db.session.commit()
    assert sel.status == "ausgesucht" and sel.numbers == [1, 2] and sel.task.is_done
    assert [p.number for p in sel.places] == [1, 2] and sel.places_label == "Baum 1, Platz 1, 2"
    with pytest.raises(StorageError):
        forest.choose_tree(other, worker, _tree(1), [2])
    db.session.rollback()
    forest.choose_tree(other, worker, _tree(1))                     # nimmt den nächsten freien
    assert other.numbers == [3]
    forest.choose_tree(sel, worker, _tree(1), [1, 4])              # umentscheiden geht
    assert sel.numbers == [1, 4]
    db.session.commit()

    contract = forest.save_contract(None, admin, sel.contact, day, sel.places, selection=sel)
    db.session.commit()
    assert sel.contract == contract and _tree(1).places[3].state == "reserviert"
    assert forest.next_place(sel).number == 1
    with app.test_request_context():
        burial = forest.save_burial(None, worker, forest.next_place(sel), "Hans Muster", date(2030, 6, 20), selection=sel)
        db.session.commit()
        assert burial.selection == sel and burial.contract == contract
        assert forest.next_place(sel).number == 4
        data = forest.selections()
        assert sel in data["chosen"] and other in data["chosen"]
        events = forest.calendar_events(day, day, agenda._event)
        assert {e["kind"] for e in events} == {"baumauswahl"} and len(events) == 2

    with pytest.raises(StorageError):
        forest.cancel_selection(sel)                               # hat schon eine Beisetzung
    db.session.rollback()
    forest.cancel_selection(other)
    db.session.commit()
    assert other.status == "abgesagt" and other in forest.selections()["done"]
    forest.reopen_selection(other, worker)
    db.session.commit()
    assert other.status == "geplant" and other.task is not None and not other.task.is_done
    forest.delete_selection(other)
    db.session.commit()
    assert ForestSelection.query.count() == 1


def test_selection_pages(app, db, woods, as_admin, as_worker, as_viewer):
    worker = woods["worker"]
    day = (date.today() + timedelta(days=3)).isoformat()
    r = as_worker.post("/wald/baumauswahl", data={"contact": "Erika Muster", "day": day, "time": "10:00",
                                                  "places_wanted": "2", "guide_id": str(worker.id),
                                                  "deceased": "Hans Muster"})
    assert r.status_code == 302
    from app.models import ForestSelection
    sel = ForestSelection.query.one()
    for url in ("/wald/baumauswahl", f"/wald/baumauswahl/{sel.id}", f"/wald/baum/1?auswahl={sel.id}", "/wald",
                "/bereich/wald", "/aufgaben"):
        page = as_worker.get(url)
        assert page.status_code == 200, url
    assert "Erika Muster" in as_worker.get("/wald").get_data(as_text=True)
    assert "Baumauswahl: Erika Muster" in as_worker.get("/aufgaben").get_data(as_text=True)
    assert as_worker.get(f"/wald/suche?baum=1&auswahl={sel.id}").headers["Location"].endswith(f"/wald/baum/1?auswahl={sel.id}")

    # am Baum antippen
    r = as_worker.post(f"/wald/baumauswahl/{sel.id}/baum", data={"tree": "1", "platz": ["5", "6"]})
    assert r.status_code == 302 and sel.numbers == [5, 6]
    assert "vorgemerkt: Erika Muster" in as_worker.get("/wald/baum/1").get_data(as_text=True)
    page = as_admin.get(f"/wald/vertrag/neu?auswahl={sel.id}").get_data(as_text=True)
    assert 'value="Erika Muster"' in page and "Baum 1, Platz 5" in page
    place_ids = [p.id for p in sel.places]
    as_admin.post("/wald/vertrag", data={"holder": "Erika Muster", "signed_on": day, "years": "99",
                                         "place_id": place_ids, "selection_id": str(sel.id)})
    assert sel.contract is not None
    page = as_worker.get(f"/wald/beisetzungen?auswahl={sel.id}").get_data(as_text=True)
    assert 'value="Hans Muster"' in page and 'name="selection_id"' in page and 'value="5"' in page
    as_worker.post("/wald/beisetzung", data={"tree": "1", "place": "5", "deceased": "Hans Muster",
                                             "day": day, "selection_id": str(sel.id)})
    assert sel.burials and sel.burials[0].place.number == 5
    assert as_worker.post(f"/wald/baumauswahl/{sel.id}/loeschen").status_code == 403


def test_grave_team(app, db, woods, as_admin, as_worker):
    """Grabstelle vorbereiten: an die Person, bei Abwesenheit (Kalender) an die Vertretung."""
    from app.models import CalendarEntry
    from .conftest import make_user
    admin, worker = woods["admin"], woods["worker"]
    deputy = make_user("jana", "mitarbeiter")
    deputy.forest = True
    db.session.commit()
    assert forest.grave_team() == (None, None)
    with pytest.raises(StorageError):
        forest.save_grave_team(worker, worker)
    r = as_admin.post("/wald/grab-team", data={"grave_person": str(worker.id), "grave_deputy": str(deputy.id)})
    assert r.status_code == 302 and forest.grave_team() == (worker, deputy)
    assert as_worker.post("/wald/grab-team", data={"grave_person": ""}).status_code == 403
    page = as_worker.get("/wald/beisetzungen").get_data(as_text=True)
    assert f'<option value="{worker.id}" selected>' in page and f'<option value="{deputy.id}" selected>' in page

    monday = date.today() + timedelta(days=14 - date.today().weekday())       # Montag in zwei Wochen
    as_worker.post("/wald/beisetzung", data={"tree": "1", "place": "1", "deceased": "Hans Muster",
                                             "day": monday.isoformat(),
                                             "grave_person": str(worker.id), "grave_deputy": str(deputy.id)})
    burial = ForestBurial.query.one()
    assert (burial.grave_person, burial.grave_deputy) == (worker, deputy)
    assert burial.dig_task.assignees == [worker] and burial.close_task.assignees == [worker]
    assert burial.dig_task.due_on == monday - timedelta(days=3)                  # Freitag davor

    # Person trägt Urlaub für den Freitag ein → „Grab ausheben“ geht an die Vertretung, „schließen“ bleibt
    push.outbox.clear()
    as_worker.post("/kalender/termin", data={"title": "Urlaub", "kind": "abwesenheit", "all_day": "1",
                                             "start_date": burial.dig_task.due_on.isoformat(),
                                             "participant_ids": str(worker.id)})
    assert CalendarEntry.query.count() == 1
    assert burial.dig_task.assignees == [deputy] and burial.close_task.assignees == [worker]
    assert {m["user_id"] for m in push.outbox} <= {deputy.id}
    # beide weg → beide, damit nichts liegen bleibt
    db.session.add(CalendarEntry(title="Schule", kind="abwesenheit", start_date=burial.dig_task.due_on,
                                 participants=[deputy]))
    db.session.commit()
    forest.refresh_grave_tasks()
    assert set(burial.dig_task.assignees) == {worker, deputy}
    # Urlaub gelöscht → wieder die Person
    entry = CalendarEntry.query.filter_by(title="Urlaub").one()
    as_worker.post(f"/kalender/termin/{entry.id}/loeschen")
    db.session.delete(CalendarEntry.query.filter_by(title="Schule").one())
    db.session.commit()
    forest.refresh_grave_tasks()
    assert burial.dig_task.assignees == [worker]

    # beim Eintragen anders gewählt; ohne Formularfeld → Standard
    as_worker.post("/wald/beisetzung", data={"tree": "1", "place": "2", "deceased": "Gerda Muster",
                                             "day": monday.isoformat(), "grave_person": "",
                                             "grave_deputy": str(deputy.id)})
    other = ForestBurial.query.filter_by(deceased="Gerda Muster").one()
    assert other.dig_task.assignees == [deputy] and other.grave_person is None
    with app.test_request_context():
        third = forest.save_burial(None, admin, _tree(1).places[2], "Karl Beispiel", monday)
    assert (third.grave_person, third.grave_deputy) == (worker, deputy)
    assert third.close_task.assignees == [worker]
    page = as_worker.get("/wald/beisetzungen").get_data(as_text=True)
    assert f"Grab: {worker.name}" in page and f"Vertretung: {deputy.name}" in page


def test_absent(db, seed):
    from app import agenda
    from app.models import CalendarEntry
    worker = seed["worker"]
    day = date(2030, 3, 4)
    db.session.add(CalendarEntry(title="Urlaub", kind="abwesenheit", start_date=day, end_date=day + timedelta(days=4),
                                 participants=[worker]))
    db.session.add(CalendarEntry(title="Kurs", kind="termin", start_date=day + timedelta(days=10),
                                 participants=[worker]))
    db.session.add(CalendarEntry(title="Arzt", kind="abwesenheit", start_date=day + timedelta(days=20),
                                 created_by_id=worker.id))                     # ohne Teilnehmer → wer ihn anlegt
    db.session.commit()
    assert agenda.absent(worker, day + timedelta(days=2)) and not agenda.absent(worker, day + timedelta(days=5))
    assert not agenda.absent(worker, day + timedelta(days=10)) and agenda.absent(worker, day + timedelta(days=20))
    assert not agenda.absent(seed["admin"], day) and not agenda.absent(None, day)


def test_bells(app, db, woods, as_worker, as_viewer):
    """Kirchenglocken: Häkchen beim Eintragen, nachträglich umschaltbar, steht in Aufgabe und Kalender."""
    day = date.today() + timedelta(days=6)
    as_worker.post("/wald/beisetzung", data={"tree": "1", "place": "1", "deceased": "Hans Muster",
                                             "day": day.isoformat(), "bells": "1"})
    as_worker.post("/wald/beisetzung", data={"tree": "1", "place": "2", "deceased": "Gerda Muster",
                                             "day": day.isoformat()})
    with_bells = ForestBurial.query.filter_by(deceased="Hans Muster").one()
    without = ForestBurial.query.filter_by(deceased="Gerda Muster").one()
    assert with_bells.bells and not without.bells
    assert "mit Kirchenglocken" in with_bells.close_task.description
    assert "Kirchenglocken" not in without.close_task.description
    with app.test_request_context():
        notes = {e["note"] for e in forest.calendar_events(day, day, agenda._event)}
    assert "Hans Muster · mit Kirchenglocken" in notes and "Gerda Muster" in notes
    page = as_worker.get("/wald/beisetzungen").get_data(as_text=True)
    assert page.count("bi-bell-fill") == 1 and 'id="bBells"' in page

    as_worker.post(f"/wald/beisetzung/{without.id}/glocken", data={"bells": "1"})
    assert without.bells and "mit Kirchenglocken" in without.dig_task.description
    as_worker.post(f"/wald/beisetzung/{with_bells.id}/glocken", data={"bells": "0"})
    assert not with_bells.bells and "Kirchenglocken" not in with_bells.dig_task.description
    assert as_viewer.post(f"/wald/beisetzung/{without.id}/glocken", data={"bells": "0"}).status_code in (403, 404)
