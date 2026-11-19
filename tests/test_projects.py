"""Projekte: Anlegen, Schritte als Aufgaben, Fortschritt/Ampel, Tagebuch, Dateien, Kosten, Rechte, Startseite."""
import io
from datetime import date, timedelta
from decimal import Decimal

from app import projects, purchases, tasks
from app.models import Notice, Project, PurchaseRequest, Task


def _project(db, seed, **kw):
    fields = dict(name="Neue Waagensoftware", owner=seed["worker"], target_on=date.today() + timedelta(days=60))
    fields.update(kw)
    project = projects.save_project(None, seed["admin"], **fields)
    db.session.commit()
    return project


def test_create_and_steps(app, as_admin, db, seed):
    r = as_admin.post("/projekte/neu", data={"name": "Neuer Hühnerstall", "description": "Ziel: 200 Plätze",
                                             "status": "laeuft", "owner_id": seed["worker"].id,
                                             "member_ids": [seed["admin"].id], "budget": "10.000",
                                             "target_on": (date.today() + timedelta(days=90)).isoformat()})
    project = Project.query.one()
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/projekt/{project.id}")
    assert project.owner == seed["worker"] and project.budget == Decimal("10000") and project.members == [seed["admin"]]
    # Schritte = Aufgaben, hinten angehängt
    for title in ("Angebote einholen", "Bauantrag", "Eröffnung"):
        as_admin.post(f"/projekt/{project.id}/schritt", data={"title": title, "assignee_ids": [seed["worker"].id],
                                                             "milestone": "1" if title == "Eröffnung" else ""})
    steps = projects.steps(project)
    assert [t.title for t in steps] == ["Angebote einholen", "Bauantrag", "Eröffnung"]
    assert steps[2].milestone and steps[0].assignees == [seed["worker"]]
    assert Notice.query.filter_by(user_id=seed["worker"].id, topic="tasks_new").count() == 3
    # umsortieren
    as_admin.post(f"/projekt/schritt/{steps[2].id}/verschieben", data={"dir": "up"})
    assert [t.title for t in projects.steps(project)] == ["Angebote einholen", "Eröffnung", "Bauantrag"]
    # Schritt erscheint in den Aufgaben des Mitarbeiters und auf der Projektseite
    assert any(t.project_id == project.id for t in tasks.open_tasks(seed["worker"]))
    page = as_admin.get(f"/projekt/{project.id}").get_data(as_text=True)
    assert "Angebote einholen" in page and "0 von 3 Schritten erledigt" in page and "Kosten" in page
    # abhaken über die normale Aufgabe → Fortschritt
    tasks.complete_task(steps[0], seed["worker"])
    db.session.commit()
    assert projects.progress(project) == {"done": 1, "total": 3, "pct": 33}
    assert projects.next_step(project).title == "Eröffnung"
    assert "Neuer Hühnerstall" in as_admin.get("/projekte").get_data(as_text=True)


def test_health_and_mine(app, db, seed):
    today = date(2026, 6, 1)
    project = _project(db, seed, target_on=today + timedelta(days=30))
    late = projects.add_step(project, seed["admin"], "Angebote", due_on=today - timedelta(days=2),
                             assignees=[seed["viewer"]])
    db.session.commit()
    assert projects.health(project, today)[0] == "warn"
    tasks.complete_task(late, seed["admin"])
    assert projects.health(project, today) == ("ok", None)
    assert projects.health(project, today + timedelta(days=31))[0] == "bad"            # Ziel überschritten
    project.status = "pausiert"
    assert projects.health(project, today) == (None, None)
    project.status = "laeuft"
    db.session.commit()
    # „Meine Projekte“: verantwortlich, beteiligt oder offener Schritt
    assert [c["project"] for c in projects.mine(seed["worker"], today)] == [project]
    assert projects.mine(seed["admin"], today) == []
    projects.add_step(project, seed["admin"], "Schulung", assignees=[seed["admin"]])
    db.session.commit()
    assert [c["project"] for c in projects.mine(seed["admin"], today)] == [project]
    # Status „fertig“ setzt das Datum, zurück auf „läuft“ löscht es
    projects.save_project(project, seed["admin"], project.name, status="fertig", owner=project.owner, today=today)
    assert project.finished_on == today and projects.mine(seed["worker"], today) == []
    projects.save_project(project, seed["admin"], project.name, status="laeuft", owner=project.owner)
    assert project.finished_on is None


def test_validation(app, db, seed):
    import pytest
    from app.storage import StorageError
    with pytest.raises(StorageError):
        projects.save_project(None, seed["admin"], "  ")
    with pytest.raises(StorageError):
        projects.save_project(None, seed["admin"], "X", start_on=date(2026, 5, 1), target_on=date(2026, 4, 1))
    with pytest.raises(StorageError):
        projects.save_project(None, seed["admin"], "X", status="quatsch")


def test_rights(app, as_worker, as_viewer, db, seed):
    project = _project(db, seed, owner=seed["admin"])
    # Mitarbeiter ohne Tagesplan-Recht: sehen, Tagebuch schreiben, aber nicht anlegen oder Schritte anlegen
    assert as_worker.get("/projekte").status_code == 200
    page = as_worker.get(f"/projekt/{project.id}").get_data(as_text=True)
    assert "Neue Waagensoftware" in page and "Kosten" not in page and "Schritt anlegen" not in page
    assert as_worker.post("/projekte/neu", data={"name": "X"}).status_code == 403
    assert as_worker.post(f"/projekt/{project.id}/schritt", data={"title": "X"}).status_code == 403
    assert as_worker.post(f"/projekt/{project.id}/bearbeiten", data={"name": "X"}).status_code == 403
    as_worker.post(f"/projekt/{project.id}/tagebuch", data={"text": "Anbieter angerufen", "amount": "999"})
    entry = project.entries[0]
    assert entry.text == "Anbieter angerufen" and entry.amount is None                 # Beträge nur Admin
    # Verantwortlicher darf pflegen
    project.owner = seed["worker"]
    db.session.commit()
    assert as_worker.post(f"/projekt/{project.id}/schritt", data={"title": "Testlauf"}).status_code == 302
    assert "Schritt anlegen" in as_worker.get(f"/projekt/{project.id}").get_data(as_text=True)
    # Tagesplan-Pfleger darf anlegen
    seed["worker"].can_plan = True
    db.session.commit()
    assert as_worker.post("/projekte/neu", data={"name": "Hallenumbau"}).status_code == 302
    # Betrachter: nur lesen
    assert as_viewer.get(f"/projekt/{project.id}").status_code == 200
    assert as_viewer.post(f"/projekt/{project.id}/tagebuch", data={"text": "x"}).status_code == 403
    assert as_worker.post(f"/projekt/{project.id}/loeschen").status_code == 403


def test_journal_push_and_delete(app, as_admin, db, seed):
    project = _project(db, seed, members=[seed["viewer"]])
    as_admin.post(f"/projekt/{project.id}/tagebuch", data={"text": "Angebot über 4.200 € erhalten\nGilt 4 Wochen",
                                                          "amount": "4.200"})
    entry = project.entries[0]
    assert entry.amount == Decimal("4200")
    notes = Notice.query.filter_by(topic="project").all()
    # an den Verantwortlichen; nicht an sich selbst, nicht an Betrachter (Thema nur für Mitarbeiter)
    assert {n.user_id for n in notes} == {seed["worker"].id}
    assert notes[0].body == "Angebot über 4.200 € erhalten" and f"/projekt/{project.id}" in notes[0].url
    as_admin.post(f"/projekt/tagebuch/{entry.id}/loeschen")
    assert project.entries == []


def test_files_photos_and_delete_project(app, as_admin, db, seed):
    import os
    from app import uploads
    project = _project(db, seed)
    step = projects.add_step(project, seed["admin"], "Angebote einholen")
    db.session.commit()
    pdf = io.BytesIO(b"%PDF-1.4\n%erfunden\n")
    r = as_admin.post(f"/projekt/{project.id}/datei", data={"file": (pdf, "angebot.pdf"), "title": "Angebot"},
                      content_type="multipart/form-data")
    assert r.status_code == 302
    doc = project.documents[0]
    path = os.path.join(uploads.document_dir(), doc.filename)
    assert os.path.exists(path)
    got = as_admin.get(f"/projekt/datei/{doc.id}")
    assert got.status_code == 200 and got.headers["Content-Security-Policy"].startswith("sandbox")
    bad = io.BytesIO(b"nur Text")
    as_admin.post(f"/projekt/{project.id}/datei", data={"file": (bad, "x.txt")}, content_type="multipart/form-data")
    assert len(project.documents) == 1
    # Projekt löschen: Datei weg, Schritt bleibt als Aufgabe
    as_admin.post(f"/projekt/{project.id}/loeschen")
    assert Project.query.count() == 0 and not os.path.exists(path)
    assert db.session.get(Task, step.id).project_id is None


def test_costs(app, as_admin, db, seed):
    project = _project(db, seed, budget=Decimal("5000"))
    project.budget = Decimal("5000")
    step = projects.add_step(project, seed["admin"], "Software kaufen")
    db.session.commit()
    req = purchases.create_request(seed["admin"], "Lizenz", task=step)
    req.price = Decimal("3000")
    other = purchases.create_request(seed["admin"], "Waagenterminal", task=step)
    other.status = "abgelehnt"
    other.price = Decimal("900")
    purchases.create_request(seed["admin"], "Kabel", task=step)
    projects.add_entry(project, seed["admin"], "Schulung vor Ort", Decimal("500"))
    db.session.commit()
    c = projects.costs(project)
    assert c["total"] == Decimal("3500") and c["rest"] == Decimal("1500") and c["unpriced"] == 1 and c["pct"] == 70
    assert {p.title for p in c["purchases"]} == {"Lizenz", "Kabel"}
    assert PurchaseRequest.query.count() == 3
    page = as_admin.get(f"/projekt/{project.id}").get_data(as_text=True)
    assert "3.500,00" in page and "1.500,00" in page


def test_task_form_links_project(app, as_admin, db, seed):
    project = _project(db, seed)
    as_admin.post("/aufgaben/neu", data={"title": "Waage eichen", "project_id": project.id})
    task = Task.query.filter_by(title="Waage eichen").one()
    assert task.project_id == project.id and task.project_position == 1
    page = as_admin.get(f"/aufgabe/{task.id}").get_data(as_text=True)
    assert "Projekt: Neue Waagensoftware" in page
    as_admin.post(f"/aufgabe/{task.id}/bearbeiten", data={"title": "Waage eichen", "project_id": ""})
    assert db.session.get(Task, task.id).project_id is None
    # Wiederholung behält das Projekt
    rep = projects.add_step(project, seed["admin"], "Wochenbericht")
    rep.recurrence, rep.due_on = "woechentlich", date.today()
    db.session.commit()
    follow = tasks.complete_task(rep, seed["admin"])
    assert follow.project_id == project.id


def test_dashboard_and_calendar(app, as_worker, db, seed):
    from app import agenda
    project = _project(db, seed)
    projects.add_step(project, seed["admin"], "Anbieter vergleichen", assignees=[seed["worker"]])
    db.session.commit()
    page = as_worker.get("/").get_data(as_text=True)
    assert 'id="projekte"' in page and "Anbieter vergleichen" in page
    with app.test_request_context("/"):
        items, _undated = agenda.events(project.target_on, project.target_on, user=seed["worker"])
    assert any(e["kind"] == "projekt" and e["mine"] for e in items)
