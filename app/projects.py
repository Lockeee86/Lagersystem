"""Projekte: Vorhaben über Wochen oder Monate (neue Waagensoftware, neuer Betriebszweig …).

Die Schritte sind normale Aufgaben (`Task.project_id`, Reihenfolge `project_position`, `milestone`) – sie
erscheinen wie gewohnt im Tagesplan, unter „Meine Aufgaben“, im Kalender und im Widget. Dazu ein Tagebuch
(`ProjectEntry`, optional mit Betrag), Dateien (`ProjectDocument`) und Fotos (`Photo.project_id`).
Kosten = Bestellwünsche der Schritte + Beträge im Tagebuch; Euro sehen nur Admins.
Anlegen: Admin oder wer den Tagesplan pflegt (`User.is_planner`); pflegen außerdem der Verantwortliche.
Committet nicht.
"""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from . import tasks
from .extensions import db
from .i18n import gettext as _, ngettext
from .models import (PROJECT_ACTIVE, PROJECT_STATUSES, Project, ProjectEntry, PurchaseRequest, Task, User,
                     project_members, task_assignees)
from .storage import StorageError

SOON_DAYS = 14          # Zieldatum in Sicht → gelb, wenn noch viel offen ist


# --- Rechte -------------------------------------------------------------------------

def can_create(user):
    return bool(user and user.is_authenticated and user.can_edit and user.is_planner and not user.restricted)


def can_manage(project, user):
    """Bearbeiten, Schritte anlegen/ordnen: Admin, Tagesplan-Pfleger und der Verantwortliche."""
    if not (user and user.is_authenticated and user.can_edit) or user.restricted:
        return False
    return bool(user.is_admin or user.is_planner or project.owner_id == user.id)


def can_write(project, user):
    """Tagebuch und Dateien: jeder Mitarbeiter (nicht Betrachter, nicht „nur eine Rubrik“)."""
    return bool(user and user.is_authenticated and user.can_edit and not user.restricted)


# --- Anlegen und ändern -------------------------------------------------------------

def save_project(project, user, name, description=None, status="laeuft", owner=None, members=(), start_on=None,
                 target_on=None, budget=None, today=None):
    """Neues Projekt (project=None) oder Änderung. `budget` nur von Admins (sonst bleibt es)."""
    name = (name or "").strip()
    if not name:
        raise StorageError(_("Bitte einen Namen angeben."))
    if status not in PROJECT_STATUSES:
        raise StorageError(_("Unbekannter Status."))
    if start_on and target_on and target_on < start_on:
        raise StorageError(_("Das Zieldatum liegt vor dem Start."))
    if project is None:
        project = Project(created_by_id=user.id if user else None)
        db.session.add(project)
    project.name = name[:150]
    project.description = (description or "").strip() or None
    if status == "fertig" and project.status != "fertig":
        project.finished_on = today or date.today()
    elif status != "fertig":
        project.finished_on = None
    project.status = status
    project.owner = owner
    project.members = [u for u in members if u is not None and u != owner]
    project.start_on = start_on
    project.target_on = target_on
    if user is not None and user.is_admin:
        project.budget = budget
    db.session.flush()
    return project


def add_step(project, user, title, due_on=None, assignees=(), milestone=False, description=None):
    """Neuer Schritt = Aufgabe mit Bezug zum Projekt, hinten angehängt."""
    task = tasks.create_task(user, title=title, description=description, due_on=due_on, assignees=list(assignees))
    last = db.session.query(db.func.max(Task.project_position)).filter(Task.project_id == project.id).scalar()
    task.project_id = project.id
    task.project_position = (last or 0) + 1
    task.milestone = bool(milestone)
    db.session.flush()
    return task


def _renumber(project):
    for i, step in enumerate(steps(project), start=1):
        step.project_position = i


def move_step(task, direction):
    """Schritt eine Stelle nach oben (-1) oder unten (+1)."""
    if task.project is None:
        raise StorageError(_("Diese Aufgabe gehört zu keinem Projekt."))
    _renumber(task.project)
    ordered = steps(task.project)
    i = ordered.index(task)
    j = i + (-1 if direction < 0 else 1)
    if 0 <= j < len(ordered):
        ordered[i].project_position, ordered[j].project_position = ordered[j].project_position, ordered[i].project_position
    db.session.flush()


def link_task(task, project):
    """Bestehende Aufgabe einem Projekt zuordnen (oder lösen mit project=None)."""
    if project is None:
        task.project_id = None
        task.project_position = 0
        return
    if task.project_id != project.id:
        last = db.session.query(db.func.max(Task.project_position)).filter(Task.project_id == project.id).scalar()
        task.project_id = project.id
        task.project_position = (last or 0) + 1


def add_entry(project, user, text, amount=None):
    text = (text or "").strip()
    if not text:
        raise StorageError(_("Bitte etwas eintragen."))
    if amount is not None and not (user and user.is_admin):
        amount = None                                   # Beträge nur von Admins
    entry = ProjectEntry(project=project, text=text[:4000], amount=amount, user_id=user.id if user else None)
    db.session.add(entry)
    db.session.flush()
    return entry


def delete_project(project):
    """Löscht Projekt, Tagebuch, Fotos und Dateien; die Schritte bleiben als Aufgaben ohne Projekt.
    Gibt die Dateinamen der Dokumente zurück – die löscht der Aufrufer nach dem Commit."""
    names = [d.filename for d in project.documents]
    for step in list(project.steps):
        step.project_id = None
        step.project_position = 0
    db.session.delete(project)
    db.session.flush()
    return names


# --- Abfragen -----------------------------------------------------------------------

def steps(project):
    return sorted(project.steps, key=lambda t: (t.project_position, t.id))


def progress(project):
    all_steps = project.steps
    done = sum(1 for t in all_steps if t.is_done)
    total = len(all_steps)
    return {"done": done, "total": total, "pct": round(100 * done / total) if total else 0}


def next_step(project):
    return next((t for t in steps(project) if not t.is_done), None)


def overdue_steps(project, today=None):
    today = today or date.today()
    return [t for t in project.steps if not t.is_done and t.due_on and t.due_on < today]


def health(project, today=None):
    """Ampel: ("ok"|"warn"|"bad"|None, Grund). Nur für laufende Projekte."""
    today = today or date.today()
    if project.status != "laeuft":
        return None, None
    prog = progress(project)
    if project.target_on and project.target_on < today:
        return "bad", _("Zieldatum überschritten")
    late = overdue_steps(project, today)
    if late:
        return "warn", ngettext("{n} Schritt überfällig", "{n} Schritte überfällig", len(late))
    if project.target_on and project.target_on <= today + timedelta(days=SOON_DAYS) and prog["total"] \
            and prog["pct"] < 75:
        return "warn", _("Zieldatum bald, noch viel offen")
    return "ok", None


def card(project, today=None):
    """Alles, was eine Kachel in der Liste oder auf der Startseite braucht."""
    today = today or date.today()
    level, reason = health(project, today)
    return {"project": project, "progress": progress(project), "next": next_step(project),
            "health": level, "reason": reason}


def _base_query():
    return Project.query.options(selectinload(Project.steps).selectinload(Task.assignees),
                                 selectinload(Project.members), selectinload(Project.owner))


def overview(finished=False, today=None):
    query = _base_query()
    if finished:
        query = query.filter(Project.status == "fertig").order_by(Project.finished_on.desc().nullslast(),
                                                                  Project.id.desc())
    else:
        query = query.filter(Project.status.in_(PROJECT_ACTIVE))
    projects = query.all()
    if not finished:
        order = {"laeuft": 0, "idee": 1, "pausiert": 2}
        projects.sort(key=lambda p: (order.get(p.status, 3), p.target_on or date.max, p.name.lower()))
    return [card(p, today) for p in projects]


def counts():
    rows = db.session.query(Project.status, db.func.count(Project.id)).group_by(Project.status).all()
    data = dict(rows)
    return {"active": sum(data.get(s, 0) for s in PROJECT_ACTIVE), "finished": data.get("fertig", 0)}


def mine(user, today=None, limit=4):
    """Laufende Projekte, in denen der Benutzer verantwortlich, beteiligt oder für einen offenen Schritt
    eingeteilt ist (Startseite „Meine Projekte“)."""
    if not (user and user.is_authenticated) or user.restricted:
        return []
    member = db.session.query(project_members.c.project_id).filter(project_members.c.user_id == user.id)
    step = (db.session.query(Task.project_id).join(task_assignees, task_assignees.c.task_id == Task.id)
            .filter(task_assignees.c.user_id == user.id, Task.project_id.isnot(None), Task.done_at.is_(None)))
    projects = (_base_query().filter(Project.status == "laeuft",
                                     or_(Project.owner_id == user.id, Project.id.in_(member), Project.id.in_(step)))
                .all())
    projects.sort(key=lambda p: (p.target_on or date.max, p.name.lower()))
    return [card(p, today) for p in projects[:limit]]


def choices(task=None):
    """Projekte für die Auswahl im Aufgaben-Formular: laufende + das bisherige."""
    current = task.project_id if task else None
    return (Project.query.filter(or_(Project.status.in_(PROJECT_ACTIVE), Project.id == current))
            .order_by(Project.name).all())


def costs(project):
    """Kosten: Bestellwünsche der Schritte (ohne abgelehnte) + Beträge aus dem Tagebuch."""
    step_ids = [t.id for t in project.steps]
    purchases = (PurchaseRequest.query.filter(PurchaseRequest.task_id.in_(step_ids),
                                              PurchaseRequest.status != "abgelehnt")
                 .order_by(PurchaseRequest.created_at).all() if step_ids else [])
    manual = [e for e in project.entries if e.amount is not None]
    total = sum((p.price for p in purchases if p.price is not None), Decimal(0)) \
        + sum((e.amount for e in manual), Decimal(0))
    budget = project.budget
    return {"purchases": purchases, "manual": sorted(manual, key=lambda e: e.created_at), "total": total,
            "unpriced": sum(1 for p in purchases if p.price is None), "budget": budget,
            "rest": budget - total if budget is not None else None,
            "pct": min(100, round(100 * total / budget)) if budget else None}


def calendar_events(start, end, event, user=None):
    """Zieldaten laufender Projekte für den Kalender (die Schritte stehen dort schon als Aufgaben).
    `event` = agenda._event; „meine“ = verantwortlich oder beteiligt."""
    from flask import url_for
    rows = (Project.query.filter(Project.status.in_(PROJECT_ACTIVE), Project.target_on >= start,
                                 Project.target_on <= end).order_by(Project.target_on).all())
    return [event(p.target_on, "projekt", f"projekt-{p.id}", _("Projektziel: {name}", name=p.name), "bi-flag",
                  "primary", url_for("projects.project_detail", project_id=p.id),
                  p.owner.name if p.owner else None, mine=user is None or user in p.people) for p in rows]


def assignable():
    return tasks.assignable_users()


def users_by_ids(ids):
    ids = {int(v) for v in ids if str(v).isdigit()}
    return User.query.filter(User.id.in_(ids), User.active.is_(True)).order_by(User.username).all() if ids else []
