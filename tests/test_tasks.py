import io
from datetime import date, timedelta

import pytest
from PIL import Image

from app import machines, tasks
from app.models import Task
from app.storage import StorageError

MON = date(2026, 10, 19)   # Montag


@pytest.mark.parametrize("due,rec,today,expected", [
    (MON, "taeglich", MON, MON + timedelta(days=1)),
    (MON, "taeglich", MON + timedelta(days=3), MON + timedelta(days=4)),     # verspätet → ab morgen
    (date(2026, 10, 23), "werktags", date(2026, 10, 23), date(2026, 10, 26)),  # Fr → Mo
    (MON, "woechentlich", MON + timedelta(days=3), MON + timedelta(days=7)),   # bleibt montags
    (MON, "woechentlich", MON - timedelta(days=2), MON + timedelta(days=7)),   # vorzeitig erledigt
    (MON, "zweiwoechentlich", MON, MON + timedelta(days=14)),
    (date(2026, 1, 31), "monatlich", date(2026, 1, 31), date(2026, 2, 28)),
])
def test_next_due(due, rec, today, expected):
    assert tasks.next_due(due, rec, today) == expected


def test_complete_recurring_and_reopen(db, seed):
    t = tasks.create_task(seed["admin"], title="Lager kontrollieren", recurrence="taeglich",
                          assignees=[seed["worker"]])
    db.session.commit()
    assert t.due_on == date.today()            # Serie ohne Datum beginnt heute
    nxt = tasks.complete_task(t, seed["worker"], "alles trocken")
    db.session.commit()
    assert t.is_done and t.done_by == seed["worker"] and t.done_note == "alles trocken"
    assert nxt.due_on == date.today() + timedelta(days=1) and nxt.assignees == [seed["worker"]]
    assert nxt.previous_id == t.id and not nxt.is_done
    with pytest.raises(StorageError):
        tasks.complete_task(t, seed["worker"])
    tasks.reopen_task(t)
    db.session.commit()
    assert not t.is_done and Task.query.count() == 1
    with pytest.raises(StorageError):
        tasks.create_task(seed["admin"], title="  ")


def test_visibility_and_buckets(db, seed):
    today = date.today()
    mine = tasks.create_task(seed["admin"], title="Mir", assignees=[seed["worker"]], due_on=today)
    other = tasks.create_task(seed["admin"], title="Admin", assignees=[seed["admin"]], due_on=today - timedelta(days=2))
    everyone = tasks.create_task(seed["admin"], title="Alle", important=True)
    db.session.commit()
    assert {t.title for t in tasks.open_tasks(seed["worker"])} == {"Mir", "Alle"}
    assert len(tasks.open_tasks()) == 3
    groups = tasks.buckets(tasks.open_tasks())
    assert groups["overdue"] == [other] and groups["today"] == [mine] and groups["undated"] == [everyone]
    assert [t.title for t in tasks.due_now(seed["worker"])] == ["Alle", "Mir"]   # wichtige zuerst


def png():
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (30, 120, 30)).save(buf, "PNG")
    buf.seek(0)
    return buf


def test_pages_and_rights(as_admin, as_worker, as_viewer, db, seed):
    m = machines.create_machine("Fendt", "schlepper", seed["admin"], counter_unit="h")
    db.session.commit()
    assert as_worker.post("/aufgaben/neu", data={"title": "x"}).status_code == 403
    as_admin.post("/aufgaben/neu", data={"title": "Öl nachfüllen", "assignee_ids": [str(seed["worker"].id)],
                                         "due_on": date.today().isoformat(), "important": "1",
                                         "machine_id": str(m.id), "place": f"{seed['wh'].id}:3"})
    t = Task.query.one()
    assert t.assignees == [seed["worker"]] and t.machine_id == m.id and t.important
    assert t.warehouse_id == seed["wh"].id and t.row == 3 and t.location_label.endswith("Reihe C")
    foreign = tasks.create_task(seed["admin"], title="Nur Admin", assignees=[seed["admin"]])
    db.session.commit()

    # Mitarbeiter sieht seine Aufgabe auf Übersicht, Liste und Maschine; Zähler im Menü
    html = as_worker.get("/").get_data(as_text=True)
    assert "Öl nachfüllen" in html and "Nur Admin" not in html and 'tab-badge">1<' in html
    assert "Öl nachfüllen" in as_worker.get(f"/maschine/{m.id}").get_data(as_text=True)
    assert "Nur Admin" in as_worker.get("/aufgaben?wer=alle").get_data(as_text=True)
    assert as_worker.post(f"/aufgabe/{foreign.id}/erledigt").status_code == 403
    assert as_viewer.post(f"/aufgabe/{t.id}/erledigt").status_code == 403

    as_worker.post(f"/aufgabe/{t.id}/erledigt", data={"note": "2 Liter", "photos": [(png(), "oel.png")],
                                                      "back": "/"}, content_type="multipart/form-data")
    assert t.is_done and t.done_note == "2 Liter" and len(t.photos) == 1
    assert as_viewer.get(f"/foto/{t.photos[0].id}").status_code == 200
    assert as_viewer.get(f"/aufgabe/{t.id}").status_code == 200

    as_admin.post(f"/aufgabe/{foreign.id}/bearbeiten", data={"title": "Für alle", "recurrence": "woechentlich"})
    assert foreign.title == "Für alle" and foreign.assignees == [] and foreign.recurrence == "woechentlich"
    as_worker.post(f"/aufgabe/{foreign.id}/erledigt")
    follow = Task.query.filter_by(previous_id=foreign.id).one()
    as_admin.post(f"/aufgabe/{foreign.id}/loeschen")
    assert db.session.get(Task, foreign.id) is None and follow.previous_id is None
    assert as_admin.get("/aufgaben?neu=1&maschine=%d" % m.id).status_code == 200
