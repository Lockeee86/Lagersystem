from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import machines, places
from app.models import Machine, MaintenanceTask, TechPlace, Warehouse
from app.storage import StorageError

from .conftest import login, make_user


def _tree(db, admin):
    plant = places.save_place(None, "Anlage Ost", biogas=True)
    chp_room = places.save_place(None, "Motorenraum", parent_id=plant.id)
    hall = places.save_place(None, "Halle Süd")
    cooling = places.save_place(None, "Kühlung", parent_id=hall.id)
    motor = machines.create_machine("Gasmotor A", "bhkw", admin, with_templates=False, counter_unit="h")
    stirrer = machines.create_machine("Rührwerk 1", "biogas", admin, with_templates=False)
    chiller = machines.create_machine("Kälteaggregat", "kuehlung", admin)
    for m, p in ((motor, chp_room), (stirrer, plant), (chiller, cooling)):
        places.install(m, p)
    db.session.commit()
    return plant, chp_room, hall, cooling, motor, stirrer, chiller


def test_tree_and_summary(db, seed):
    admin = seed["admin"]
    plant, chp_room, hall, cooling, motor, stirrer, chiller = _tree(db, admin)
    assert plant.code == "TP-001" and places.normalize_code("tp7") == "TP-007"
    assert [(p.name, d) for p, d in places.tree(admin)] == [("Anlage Ost", 0), ("Motorenraum", 1),
                                                             ("Halle Süd", 0), ("Kühlung", 1)]
    assert chp_room.in_biogas and not cooling.in_biogas and cooling.label == "Halle Süd › Kühlung"
    # Kühlung bringt ihre üblichen Wartungen mit
    assert any("Kältemittel" in t.name for t in chiller.tasks)
    # Stand: über den ganzen Unterbaum
    machines.add_event(motor, "stoerung", admin, text="Zündaussetzer")
    db.session.add(MaintenanceTask(machine=stirrer, name="Getriebeöl", interval_months=12,
                                   last_done_on=date.today() - timedelta(days=400)))
    machines.add_event(stirrer, "reparatur", admin, text="Lager", cost=Decimal("250"))
    db.session.commit()
    info = places.summary(plant, admin, year=date.today().year)
    assert {m.name for m in info["machines"]} == {"Gasmotor A", "Rührwerk 1"}
    assert len(info["faults"]) == 1 and info["overdue"] == 1 and info["state"] == "fault"
    assert info["cost_total"] == Decimal("250")
    # kein Kreis
    with pytest.raises(StorageError):
        places.save_place(plant, "Anlage Ost", parent_id=chp_room.id)
    # Biogas abgewählt: Anlage samt Unterplätzen unsichtbar
    worker = seed["worker"]
    worker.biogas = False
    assert [p.name for p, _d in places.tree(worker)] == ["Halle Süd", "Kühlung"]
    # löschen: erst Kinder; Maschinen bleiben
    with pytest.raises(StorageError):
        places.delete_place(hall)
    places.delete_place(cooling)
    db.session.commit()
    assert chiller.place_id is None and db.session.get(Machine, chiller.id)


def test_pages(app, as_admin, as_worker, db, seed):
    admin = seed["admin"]
    wh = Warehouse.query.first()
    r = as_admin.post("/plaetze/neu", data={"name": "Halle Nord", "warehouse_id": wh.id})
    hall = TechPlace.query.filter_by(name="Halle Nord").one()
    assert r.headers["Location"].endswith(f"/platz/{hall.id}") and hall.warehouse == wh
    as_admin.post("/plaetze/neu", data={"name": "Lüftung Nord", "parent_id": hall.id})
    vent = TechPlace.query.filter_by(name="Lüftung Nord").one()
    assert vent.parent == hall
    fan = machines.create_machine("Lüfter 1", "lueftung", admin)
    db.session.commit()
    assert as_worker.post(f"/platz/{vent.id}/einbauen", data={"machine_id": fan.id}).status_code == 403
    as_admin.post(f"/platz/{vent.id}/einbauen", data={"machine_id": fan.id})
    assert fan.place == vent
    # Seiten
    html = as_worker.get("/plaetze").get_data(as_text=True)
    assert "Halle Nord" in html and "Lüftung Nord" in html and "Neuer Platz" not in html
    html = as_worker.get(f"/platz/{hall.id}").get_data(as_text=True)
    assert "Lüfter 1" in html and "Lüftung Nord" in html
    assert "Halle Nord" in as_worker.get(f"/lager/{wh.id}").get_data(as_text=True)
    assert "Lüftung Nord" in as_worker.get(f"/maschine/{fan.id}").get_data(as_text=True)
    assert "Lüftung Nord" in as_worker.get("/maschinen").get_data(as_text=True)
    # Maschine bearbeiten: Platz wählen bzw. entfernen
    as_admin.post(f"/maschine/{fan.id}/bearbeiten", data={"name": "Lüfter 1", "category": "lueftung", "place_id": hall.id})
    assert fan.place == hall
    as_admin.post(f"/platz/{hall.id}/einbauen", data={"machine_id": fan.id, "remove": "1"})
    assert fan.place is None
    # Biogas-Platz für Benutzer ohne Biogas: 404
    as_admin.post(f"/platz/{vent.id}/bearbeiten", data={"name": "Lüftung Nord", "parent_id": hall.id, "biogas": "1"})
    seed["worker"].biogas = False
    db.session.commit()
    assert as_worker.get(f"/platz/{vent.id}").status_code == 404
    # löschen
    as_admin.post(f"/platz/{vent.id}/loeschen")
    assert db.session.get(TechPlace, vent.id) is None


def test_grouped_machine_selects(as_admin, db, seed):
    admin = seed["admin"]
    plant, chp_room, hall, cooling, motor, stirrer, chiller = _tree(db, admin)
    tractor = machines.create_machine("Schlepper 9", "schlepper", admin)
    db.session.commit()
    groups = places.group_machines(Machine.query.order_by(Machine.name))
    assert [label for label, _items in groups] == ["Anlage Ost", "Anlage Ost › Motorenraum", "Halle Süd › Kühlung",
                                                   "Ohne festen Platz"]
    assert groups[-1][1] == [tractor]
    for url in ("/bestellungen", "/aufgaben", "/tagesplan"):
        html = as_admin.get(url).get_data(as_text=True)
        assert '<optgroup label="Anlage Ost › Motorenraum">' in html, url
    # ohne Plätze: keine Gruppen
    assert places.group_machines([tractor]) == [(None, [tractor])]


def test_machine_list_by_place(as_admin, db, seed):
    admin = seed["admin"]
    _tree(db, admin)
    machines.create_machine("Schlepper 9", "schlepper", admin)
    db.session.commit()
    html = as_admin.get("/maschinen").get_data(as_text=True)
    assert "Anlage Ost › Motorenraum" in html and "Ohne festen Platz" in html and "nach Art" in html
    html = as_admin.get("/maschinen?gruppe=art").get_data(as_text=True)
    assert "Anlage Ost › Motorenraum" not in html and "Schlepper" in html
