"""Oberfläche: Rückgängig im Hinweis, „Mach ich“ zurücknehmen, Wochenleiste, Befehlssuche."""
import json
from datetime import date, timedelta

from app import navigation, team
from app.models import Task
from app.utils import undo_data
from tests.conftest import make_user


def _undo(html):
    """Formular des Rückgängig-Knopfs aus der Seite holen."""
    start = html.index('class="app-toast-undo"')
    form = html.rfind("<form", 0, start)
    return html[form:start]


def test_check_with_undo(app, db, seed, as_worker):
    worker = seed["worker"]
    task = team.add_plan_entry(date.today(), worker, "Hoftor ölen", seed["admin"])
    db.session.commit()
    page = as_worker.post(f"/tagesplan/eintrag/{task.id}/erledigt", data={"back": "/"}, follow_redirects=True).get_data(as_text=True)
    assert db.session.get(Task, task.id).done_at is not None
    assert "Erledigt: Hoftor ölen" in page and "Rückgängig" in page
    form = _undo(page)
    assert f'action="/tagesplan/eintrag/{task.id}/erledigt"' in form and 'name="back" value="/"' in form
    as_worker.post(f"/tagesplan/eintrag/{task.id}/erledigt", data={"back": "/"})
    assert db.session.get(Task, task.id).done_at is None


def test_take_and_give_back(app, db, seed, as_worker, as_admin):
    worker = seed["worker"]
    task = team.add_open_task("Rinne säubern", seed["admin"])
    db.session.commit()
    page = as_worker.post(f"/aufgabe/{task.id}/einplanen", data={"datum": date.today().isoformat(), "back": "/"},
                          follow_redirects=True).get_data(as_text=True)
    assert "Übernommen: Rinne säubern" in page and f"/aufgabe/{task.id}/zuruecklegen" in _undo(page)
    as_worker.post(f"/aufgabe/{task.id}/zuruecklegen", data={"back": "/"})
    task = db.session.get(Task, task.id)
    assert task.plan_day is None and not task.assignees
    # nur die eigene, offene Aufgabe
    team.plan_task(task, date.today(), seed["admin"], seed["admin"])
    db.session.commit()
    assert as_worker.post(f"/aufgabe/{task.id}/zuruecklegen").status_code == 403


def test_undo_data_only_local():
    assert undo_data(json.dumps({"text": "x", "url": "//fremd.de", "fields": {}})) is None
    assert undo_data("kein json") is None
    assert undo_data(json.dumps({"text": "x", "url": "/a", "fields": {}}))["url"] == "/a"


def test_week_strip(app, db, seed, as_worker):
    worker = seed["worker"]
    monday = date.today() - timedelta(days=date.today().weekday())
    done = team.add_plan_entry(monday, worker, "Erledigtes", seed["admin"])
    team.add_plan_entry(monday, worker, "Offenes", seed["admin"])
    done.done_at = done.created_at
    db.session.commit()
    week = team.week_summary(monday + timedelta(days=3), worker)
    assert next(w["day"] for w in week) == monday and len(week) == 7
    assert week[0]["done"] == 1 and week[0]["open"] == 1 and week[0]["mine"]
    html = as_worker.get(f"/tagesplan?datum={monday.isoformat()}").get_data(as_text=True)
    assert 'class="week-strip"' in html and "ws-dot ok" in html


def test_commands(app, db, seed, as_admin, as_worker):
    hen = make_user("henne", "mitarbeiter")
    hen.chickens_only = True
    db.session.commit()
    with app.test_request_context():
        admin = {c["url"] for c in navigation.commands(seed["admin"])}
        worker = {c["url"] for c in navigation.commands(seed["worker"])}
        only = {c["url"] for c in navigation.commands(hen)}
    assert "/admin/benutzer" in admin and "/admin/benutzer" not in worker
    assert "/" in worker and only
    assert not any(u.startswith("/admin") for u in only)
    r = as_worker.get("/befehle.json")
    assert r.status_code == 200 and {"label", "url", "icon", "group"} <= set(r.get_json()[0])
    assert 'id="cmdk"' in as_worker.get("/").get_data(as_text=True)
