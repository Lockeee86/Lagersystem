import io
import os
from datetime import date, timedelta

import pytest
from PIL import Image

from app import machines
from app.models import Machine, MachineEvent, MaintenanceTask, Photo
from app.storage import StorageError


def png(size=(2400, 1200), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    buf.seek(0)
    return buf


@pytest.fixture
def tractor(db, seed):
    m = machines.create_machine("Fendt 724", "schlepper", seed["admin"], counter_unit="h", plate="UE-F 724")
    machines.set_counter(m, 1200, seed["admin"])
    db.session.commit()
    return m


def test_codes_and_templates(db, seed, tractor):
    trailer = machines.create_machine("Kipper", "anhaenger", seed["admin"])
    db.session.commit()
    assert (tractor.code, trailer.code) == ("M-001", "M-002")
    names = {t.name for t in tractor.tasks}
    assert "Motoröl und Filter" in names and "UVV-Prüfung" in names
    # ohne Zähler: nur Intervalle nach Monaten
    assert trailer.tasks and all(t.interval_hours is None and t.interval_months for t in trailer.tasks)
    assert machines.normalize_code("m-1") == "M-001" and machines.normalize_code("K-0001") is None


def test_counter_must_not_go_down(db, seed, tractor):
    with pytest.raises(StorageError):
        machines.set_counter(tractor, 1100, seed["worker"])
    machines.set_counter(tractor, 1250, seed["worker"])
    assert tractor.counter == 1250


def test_task_status(db, seed, tractor):
    oil = next(t for t in tractor.tasks if t.name == "Motoröl und Filter")   # 500 h / 12 Monate
    assert machines.task_status(oil)["state"] == "unknown"
    today = date(2026, 10, 22)
    oil.last_done_on, oil.last_done_counter = date(2026, 3, 1), 750
    s = machines.task_status(oil, today)
    assert s["due_counter"] == 1250 and s["left_counter"] == 50 and s["state"] == "soon"
    tractor.counter = 1300
    assert machines.task_status(oil, today)["state"] == "overdue"
    tractor.counter = 800
    s = machines.task_status(oil, today)
    assert s["state"] == "ok" and s["due_on"] == date(2027, 3, 1)
    assert machines.task_status(oil, date(2027, 2, 15))["state"] == "soon"
    assert machines.task_status(oil, date(2027, 3, 1))["state"] == "overdue"
    assert machines.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_complete_task(db, seed, tractor):
    oil = next(t for t in tractor.tasks if t.name == "Motoröl und Filter")
    machines.complete_task(oil, seed["worker"], date(2026, 10, 1), 1260, "15W-40")
    db.session.commit()
    assert oil.last_done_counter == 1260 and tractor.counter == 1260
    e = tractor.events.filter_by(kind="wartung").one()
    assert e.kind == "wartung" and e.task_name == "Motoröl und Filter" and e.text == "15W-40"
    assert machines.task_status(oil)["state"] == "ok"


def test_pages_and_rights(app, as_admin, as_worker, as_viewer, db, seed):
    r = as_admin.post("/maschinen/neu", data={"name": "Grimme SE 150", "category": "roder", "counter_unit": "h",
                                              "counter": "300", "templates": "1"})
    m = Machine.query.one()
    assert r.headers["Location"].endswith(f"/maschine/{m.id}") and m.counter == 300 and m.tasks
    assert as_worker.post("/maschinen/neu", data={"name": "X", "category": "roder"}).status_code == 403

    # Mitarbeiter: Zähler, Störung mit Foto, Wartung erledigt
    as_worker.post(f"/maschine/{m.id}/zaehler", data={"counter": "320"})
    assert m.counter == 320
    as_worker.post(f"/maschine/{m.id}/eintrag", data={"kind": "stoerung", "text": "Siebband reißt",
                                                     "photos": [(png(), "band.png")]},
                   content_type="multipart/form-data")
    fault = MachineEvent.query.filter_by(kind="stoerung").one()
    (photo,) = fault.photos
    path = os.path.join(app.config["UPLOAD_DIR"], photo.filename)
    with Image.open(path) as img:
        assert img.format == "JPEG" and max(img.size) == 1600
    assert as_viewer.get(f"/foto/{photo.id}").status_code == 200
    task = m.tasks[0]
    as_worker.post(f"/maschine/{m.id}/wartung/{task.id}", data={"date": "2026-10-20", "counter": "320",
                                                                "cost": "999"})
    assert task.last_done_on == date(2026, 10, 20)
    assert MachineEvent.query.filter_by(kind="wartung").one().cost is None   # Kosten nur Admin

    # Übersicht und Dashboard zeigen die Störung
    assert "Siebband" in as_viewer.get("/").get_data(as_text=True)
    assert "Siebband" in as_viewer.get("/maschinen").get_data(as_text=True)
    assert as_viewer.get(f"/maschine/{m.id}").status_code == 200
    assert as_viewer.post(f"/maschine/{m.id}/zaehler", data={"counter": "400"}).status_code == 403

    as_worker.post(f"/maschine/{m.id}/eintrag/{fault.id}/erledigt")
    assert fault.resolved_at is not None and not machines.open_faults()

    # Admin: Reparatur mit Kosten, Intervall anlegen/löschen, Eintrag mit Foto löschen
    as_admin.post(f"/maschine/{m.id}/eintrag", data={"kind": "reparatur", "text": "Lager getauscht",
                                                    "cost": "1.234,50"})
    rep = MachineEvent.query.filter_by(kind="reparatur").one()
    assert str(rep.cost) == "1234.50"
    assert "1.234,50 €" in as_admin.get(f"/maschine/{m.id}").get_data(as_text=True)
    as_admin.post(f"/maschine/{m.id}/intervall", data={"name": "Ketten spannen", "interval_hours": "50"})
    new = MaintenanceTask.query.filter_by(name="Ketten spannen").one()
    as_admin.post(f"/maschine/{m.id}/intervall/{new.id}/loeschen")
    assert db.session.get(MaintenanceTask, new.id) is None
    as_admin.post(f"/maschine/{m.id}/eintrag/{fault.id}/loeschen")
    assert Photo.query.count() == 0 and not os.path.exists(path)

    # QR: Etikett und Scan
    pdf = as_admin.post(f"/maschine/{m.id}/etikett.pdf", data={"format": "2x4", "count": "2"})
    assert pdf.data[:4] == b"%PDF"
    assert as_worker.get("/scan?code=M-001").headers["Location"].endswith(f"/maschine/{m.id}")


def test_invalid_photo_is_rejected(app, as_worker, db, seed, tractor):
    as_worker.post(f"/maschine/{tractor.id}/eintrag",
                   data={"kind": "stoerung", "text": "x", "photos": [(png(), "ok.png"),
                                                                    (io.BytesIO(b"kein bild"), "x.jpg")]},
                   content_type="multipart/form-data")
    assert MachineEvent.query.filter_by(kind="stoerung").count() == 0
    upload_dir = app.config["UPLOAD_DIR"]
    assert not os.path.isdir(upload_dir) or not os.listdir(upload_dir)   # bereits gespeichertes Foto wieder weg


def test_due_overview(db, seed, tractor):
    uvv = next(t for t in tractor.tasks if t.name == "UVV-Prüfung")
    uvv.last_done_on = date.today() - timedelta(days=400)
    db.session.commit()
    due = machines.due_tasks()
    assert [(t.name, s["state"]) for t, s in due] == [("UVV-Prüfung", "overdue")]
    tractor.active = False
    assert machines.due_tasks() == []


def test_fault_still_running(as_admin, as_worker, db, seed, tractor):
    """Störung: Maschine steht oder kann eingeschränkt weiterfahren; Aufgabe auch nachträglich für Mitarbeiter."""
    from app.models import MachineEvent
    as_worker.post(f"/maschine/{tractor.id}/eintrag", data={"kind": "stoerung", "text": "Hydraulik leckt leicht",
                                                           "state": "laeuft"})
    event = MachineEvent.query.filter_by(kind="stoerung").one()
    assert event.still_running
    html = as_worker.get(f"/maschine/{tractor.id}").get_data(as_text=True)
    assert "Kann eingeschränkt weiterfahren" in html and "Aufgabe daraus" in html and "alert-warning" in html
    assert "Störung, läuft weiter" in as_worker.get("/").get_data(as_text=True)
    as_worker.post(f"/maschine/{tractor.id}/eintrag/{event.id}/zustand", data={"state": "steht"})
    assert not event.still_running
    assert "Maschine steht" in as_worker.get(f"/maschine/{tractor.id}").get_data(as_text=True)
    page = as_worker.get(f"/aufgaben?neu=1&stoerung={event.id}").get_data(as_text=True)
    assert "Hydraulik leckt leicht" in page                                          # Aufgabe vorbelegt
    as_worker.post(f"/maschine/{tractor.id}/eintrag", data={"kind": "notiz", "text": "Reifen ok", "state": "laeuft"})
    assert not MachineEvent.query.filter_by(kind="notiz").one().still_running        # nur bei Störungen


def test_fault_resolved_task_stays(as_admin, db, seed, tractor):
    """Starter hängt → Aufgabe „Starter wechseln“ → Störung behoben (lief beim nächsten Start), Aufgabe bleibt offen."""
    from app.models import Task
    as_admin.post(f"/maschine/{tractor.id}/eintrag", data={"kind": "stoerung", "text": "Starter hängt beim Starten",
                                                          "as_task": "1", "task_title": "Starter wechseln"})
    event = MachineEvent.query.filter_by(kind="stoerung").one()
    task = Task.query.one()
    assert task.title == "Fendt 724: Starter wechseln" and task.machine_event_id == event.id
    html = as_admin.get(f"/maschine/{tractor.id}").get_data(as_text=True)
    assert "Behoben, Aufgabe bleibt" in html and "Behoben, Aufgabe erledigt" in html
    r = as_admin.post(f"/maschine/{tractor.id}/eintrag/{event.id}/erledigt", follow_redirects=True)
    assert "Aufgabe dazu bleibt offen" in r.get_data(as_text=True)
    assert event.resolved_at is not None and task.done_at is None
    assert "behoben" in as_admin.get(f"/aufgabe/{task.id}").get_data(as_text=True)
    # die Aufgabe später erledigen geht ohne Fehler, obwohl die Störung schon behoben ist
    as_admin.post(f"/aufgabe/{task.id}/erledigt", data={"note": "Starter getauscht"})
    assert task.done_at is not None
    # zweite Störung: beides auf einmal erledigen
    as_admin.post(f"/maschine/{tractor.id}/eintrag", data={"kind": "stoerung", "text": "Licht flackert", "as_task": "1"})
    second = MachineEvent.query.filter_by(text="Licht flackert").one()
    linked = Task.query.filter_by(machine_event_id=second.id).one()
    assert linked.title == "Fendt 724: Licht flackert"
    as_admin.post(f"/maschine/{tractor.id}/eintrag/{second.id}/erledigt", data={"close_task": "1"})
    assert second.resolved_at is not None and linked.done_at is not None
