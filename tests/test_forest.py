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
                                  "3;Ahorn;Senke;Familie;8;52.1;10.2\n20;Buche;;;;;\n".encode())
    assert forest.apply_trees(rows) == (1, 1)
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
