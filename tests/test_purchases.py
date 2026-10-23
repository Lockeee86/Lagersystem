import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from PIL import Image

from app import machines, purchases
from app.models import MachineEvent, PurchaseRequest, Task, User
from app.storage import StorageError

from .conftest import login, make_user


def png():
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (90, 90, 90)).save(buf, "PNG")
    buf.seek(0)
    return buf


def test_buyer_flag(db, seed):
    assert seed["admin"].is_buyer and not seed["worker"].is_buyer
    seed["worker"].can_order = True
    seed["viewer"].can_order = True
    assert seed["worker"].is_buyer and not seed["viewer"].is_buyer     # Betrachter bestellen nie
    assert purchases.buyers() == [seed["admin"], seed["worker"]]


def test_status_flow(db, seed):
    req = purchases.create_request(seed["worker"], "Hydraulikschlauch", quantity="1 Stück")
    with pytest.raises(StorageError):
        purchases.create_request(seed["worker"], "  ")
    with pytest.raises(StorageError):
        purchases.update_request(req, seed["admin"], "abgelehnt")          # ohne Begründung
    with pytest.raises(StorageError):
        purchases.mark_done(req, seed["worker"])                            # noch nicht geliefert
    purchases.update_request(req, seed["admin"], "bestellt", supplier="Landtechnik Meyer",
                             expected_on=date.today() - timedelta(days=1))
    assert req.ordered_on == date.today() and req.handled_by == seed["admin"]
    assert purchases.counts() == {"new": 0, "late": 1}
    purchases.update_request(req, seed["admin"], "geliefert", supplier="Landtechnik Meyer")
    assert req.delivered_on == date.today() and purchases.arrived_for(seed["worker"]) == [req]
    purchases.mark_done(req, seed["worker"])
    assert req.status == "erledigt" and not req.is_open


def test_pages(app, as_admin, as_worker, as_viewer, db, seed):
    m = machines.create_machine("Fendt 724", "schlepper", seed["admin"])
    fault = machines.add_event(m, "stoerung", seed["worker"], text="Schlauch geplatzt")
    db.session.commit()

    # Mitarbeiter fragt aus der Störung heraus ein Ersatzteil an (mit Foto)
    html = as_worker.get(f"/bestellungen?neu=1&stoerung={fault.id}").get_data(as_text=True)
    assert f'name="machine_event_id" value="{fault.id}"' in html
    r = as_worker.post("/bestellungen/neu", data={"title": "Hydraulikschlauch DN12, 1,2 m", "details": "Teile-Nr. 4711",
                                                  "quantity": "1 Stück", "machine_id": str(m.id),
                                                  "machine_event_id": str(fault.id), "urgent": "1",
                                                  "photos": [(png(), "schlauch.png")]},
                       content_type="multipart/form-data")
    req = PurchaseRequest.query.one()
    assert r.headers["Location"].endswith(f"/bestellung/{req.id}")
    assert req.requested_by == seed["worker"] and req.urgent and len(req.photos) == 1 and req.machine_event == fault
    assert as_viewer.post("/bestellungen/neu", data={"title": "x"}).status_code == 403

    # Besteller sieht den Wunsch: Zähler, Übersicht, Maschinenseite
    assert 'badge rounded-pill text-bg-danger" title="Bestellungen">1<' in as_admin.get("/schlagkarte").get_data(as_text=True)
    assert "Hydraulikschlauch" in as_admin.get("/").get_data(as_text=True)
    assert "Hydraulikschlauch" in as_worker.get(f"/maschine/{m.id}").get_data(as_text=True)

    # Nur Besteller bearbeiten; ein Mitarbeiter mit „darf bestellen“ ebenfalls
    data = {"status": "bestellt", "supplier": "Landtechnik Meyer", "order_number": "A-123", "price": "1.234,50",
            "expected_on": (date.today() + timedelta(days=3)).isoformat()}
    assert as_worker.post(f"/bestellung/{req.id}/bearbeiten", data=data).status_code == 403
    buyer = make_user("einkauf", "mitarbeiter")
    buyer.can_order = True
    db.session.commit()
    client = app.test_client()
    login(client, "einkauf")
    client.post(f"/bestellung/{req.id}/bearbeiten", data=data)
    assert req.status == "bestellt" and req.price == Decimal("1234.50") and req.handled_by == buyer
    html = as_worker.get(f"/bestellung/{req.id}").get_data(as_text=True)
    assert "Landtechnik Meyer" in html and "1.234,50 €" in html

    # Geliefert → Mitarbeiter sieht es auf der Übersicht und hakt ab
    client.post(f"/bestellung/{req.id}/bearbeiten", data=data | {"status": "geliefert"})
    assert "Geliefert:" in as_worker.get("/").get_data(as_text=True)
    as_worker.post(f"/bestellung/{req.id}/erledigt")
    assert req.status == "erledigt"

    # Zurückziehen nur, solange angefragt
    other = purchases.create_request(seed["worker"], "Netze 10 kg")
    db.session.commit()
    as_worker.post(f"/bestellung/{other.id}/loeschen")
    assert db.session.get(PurchaseRequest, other.id) is None
    assert as_worker.post(f"/bestellung/{req.id}/loeschen").status_code == 403
    assert "Hydraulikschlauch" in as_viewer.get("/bestellungen?status=alle").get_data(as_text=True)


def test_worker_tasks_go_to_admins(as_worker, db, seed):
    other = make_user("jan", "mitarbeiter")
    # Mitarbeiter schreibt eine Aufgabe – Zuteilung an Kollegen wird zu Admins/Bestellern
    as_worker.post("/aufgaben/neu", data={"title": "Reifendruck prüfen lassen", "assignee_ids": [str(other.id)],
                                          "recurrence": "taeglich"})
    t = Task.query.one()
    assert t.assignees == [seed["admin"]] and t.recurrence is None and t.created_by == seed["worker"]
    # Störung melden mit „als Aufgabe an Admin/Besteller“
    m = machines.create_machine("Kipper", "anhaenger", seed["admin"])
    db.session.commit()
    as_worker.post(f"/maschine/{m.id}/eintrag", data={"kind": "stoerung", "text": "Bremslicht kaputt", "as_task": "1"})
    t2 = Task.query.filter_by(machine_event_id=MachineEvent.query.filter_by(kind="stoerung").one().id).one()
    assert t2.assignees == [seed["admin"]] and t2.title == "Kipper: Bremslicht kaputt"
