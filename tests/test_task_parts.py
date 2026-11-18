"""Ersatzteil aus einer Aufgabe bestellen: Bestellwunsch mit Bezug, Aufgabe wartet, Nachricht bei Lieferung."""
from datetime import date, timedelta

from app import machines, push, tasks
from app.models import MachinePart, PurchaseRequest, PushSubscription, Supplier, Task

from .conftest import login, make_user


def test_order_part_from_task(app, db, seed, as_admin, as_worker):
    admin, worker = seed["admin"], seed["worker"]
    helper = make_user("lena", "mitarbeiter")
    for u in (admin, worker, helper):
        db.session.add(PushSubscription(user_id=u.id, endpoint=f"https://push.example/{u.id}", p256dh="k", auth="a"))
    motor = machines.create_machine("Motor 3", "bhkw", admin, with_templates=False)
    supplier = Supplier(name="Teilehandel Nord")
    db.session.add(supplier)
    part = MachinePart(machine=motor, name="Zündkerze", part_number="ZK-4711", manufacturer="Funkwerk",
                       supplier=supplier)
    db.session.add(part)
    fault = machines.add_event(motor, "stoerung", admin, text="Zündaussetzer, steht")
    task = tasks.create_task(admin, title="Motor 3 instand setzen", due_on=date.today() - timedelta(days=2),
                             assignees=[worker, helper])
    task.machine_id, task.machine_event_id = motor.id, fault.id
    db.session.commit()

    page = as_worker.get(f"/aufgabe/{task.id}").get_data(as_text=True)
    assert "Teil bestellen" in page and "ZK-4711" in page and 'id="tp_urgent" name="urgent" value="1" checked' in page
    assert "Teil bestellen" not in app.test_client().get(f"/aufgabe/{task.id}").get_data(as_text=True)

    push.outbox.clear()
    r = as_worker.post("/bestellungen/neu", data={"task_id": task.id, "part_id": part.id, "title": "", "quantity": "6 Stück",
                                                  "urgent": "1", "back": f"/aufgabe/{task.id}#teile"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/aufgabe/{task.id}#teile")
    req = PurchaseRequest.query.one()
    assert (req.task_id, req.machine_id, req.machine_event_id, req.part_id) == (task.id, motor.id, fault.id, part.id)
    assert req.title == "Zündkerze" and req.urgent and "ZK-4711" in req.details and req.supplier == "Teilehandel Nord"
    assert [m["title"] for m in push.outbox] == ["Dringender Bestellwunsch"]                 # an die Besteller

    task = db.session.get(Task, task.id)
    assert task.waiting_part and task.open_purchases == [req]
    groups = tasks.buckets(tasks.open_tasks())
    assert task in groups["waiting"] and task not in groups["overdue"]                    # nicht „überfällig“
    assert "Wartet auf Teil" in as_worker.get("/aufgaben").get_data(as_text=True)
    page = as_worker.get(f"/aufgabe/{task.id}").get_data(as_text=True)
    assert "wartet auf Teil" in page and "Trotzdem erledigt?" in page
    assert "Motor 3 instand setzen" in as_admin.get(f"/bestellung/{req.id}").get_data(as_text=True)

    # Besteller: bestellt → geliefert; wer die Aufgabe hat, bekommt „Teil ist da“
    as_admin.post(f"/bestellung/{req.id}/bearbeiten", data={"status": "bestellt", "expected_on": date.today().isoformat()})
    assert "kommt" in as_worker.get(f"/aufgabe/{task.id}").get_data(as_text=True)
    push.outbox.clear()
    as_admin.post(f"/bestellung/{req.id}/bearbeiten", data={"status": "geliefert"})
    titles = [(m["title"], m["user_id"]) for m in push.outbox]
    assert ("Teil ist da: Zündkerze", helper.id) in titles                                  # zweite Person der Aufgabe
    assert ("Bestellung: Geliefert", worker.id) in titles                                   # der Anfragende wie bisher
    task = db.session.get(Task, task.id)
    assert not task.waiting_part and task in tasks.buckets(tasks.open_tasks())["overdue"]


def test_order_part_rights(app, db, seed, as_worker):
    admin = seed["admin"]
    other = make_user("lena", "mitarbeiter")
    task = tasks.create_task(admin, title="Hoftor reparieren", assignees=[other])
    db.session.commit()
    assert "Teil bestellen" not in as_worker.get(f"/aufgabe/{task.id}").get_data(as_text=True)
    assert as_worker.post("/bestellungen/neu", data={"task_id": task.id, "title": "Scharnier"}).status_code == 403
    viewer = app.test_client()
    login(viewer, "oma")
    assert viewer.post("/bestellungen/neu", data={"task_id": task.id, "title": "Scharnier"}).status_code == 403
    assert PurchaseRequest.query.count() == 0
