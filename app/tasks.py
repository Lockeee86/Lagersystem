"""Aufgaben: anlegen, zuteilen, abhaken, wiederkehrende Aufgaben fortschreiben.

Wie `storage.py` verändern die Funktionen nur die Session, committen muss der Aufrufer.
"""
from datetime import date, timedelta

from sqlalchemy import or_

from .extensions import db
from .i18n import gettext as _
from .machines import add_months
from .models import RECURRENCES, ROLE_MITARBEITER, Photo, Task, User, task_assignees, utcnow
from .storage import StorageError

UPCOMING_DAYS = 7


def assignable_users():
    """Wer Aufgaben bekommen kann: aktive Mitarbeiter und Admins."""
    return [u for u in User.query.filter_by(active=True).order_by(User.username) if u.has_role(ROLE_MITARBEITER)]


def _advance(day, recurrence):
    if recurrence == "taeglich":
        return day + timedelta(days=1)
    if recurrence == "werktags":
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day
    if recurrence == "woechentlich":
        return day + timedelta(days=7)
    if recurrence == "zweiwoechentlich":
        return day + timedelta(days=14)
    if recurrence == "monatlich":
        return add_months(day, 1)
    raise ValueError(recurrence)


def next_due(due_on, recurrence, today=None):
    """Nächster Termin nach dem bisherigen, mindestens morgen. Der Rhythmus (z. B. immer montags) bleibt,
    auch wenn die Aufgabe zu spät erledigt wurde."""
    today = today or date.today()
    day = _advance(due_on or today, recurrence)
    while day <= today:
        day = _advance(day, recurrence)
    return day


def apply_fields(task, title, description=None, due_on=None, important=False, recurrence=None, assignees=(),
                 machine_id=None, delivery_id=None, warehouse_id=None, row=None):
    title = (title or "").strip()
    if not title:
        raise StorageError(_("Bitte einen Titel angeben."))
    if recurrence and recurrence not in RECURRENCES:
        raise StorageError(_("Unbekannte Wiederholung."))
    task.title = title
    task.description = description or None
    task.important = bool(important)
    task.recurrence = recurrence or None
    task.due_on = due_on or (date.today() if recurrence else None)   # Serien brauchen einen Termin
    task.assignees = list(assignees)
    task.machine_id, task.delivery_id = machine_id, delivery_id
    task.warehouse_id = warehouse_id
    task.row = row if warehouse_id else None
    return task


def create_task(user, **fields):
    task = apply_fields(Task(created_by_id=user.id if user else None), **fields)
    db.session.add(task)
    db.session.flush()
    return task


def complete_task(task, user, note=None, photo_names=None):
    """Hakt die Aufgabe ab. Bei Wiederholung wird die nächste angelegt und zurückgegeben."""
    if task.is_done:
        raise StorageError(_("Diese Aufgabe ist schon erledigt."))
    task.done_at = utcnow()
    task.done_by_id = user.id if user else None
    task.done_note = note or None
    for name in photo_names or []:
        task.photos.append(Photo(filename=name, user_id=user.id if user else None))
    follow_up = None
    if task.recurrence:
        follow_up = Task(
            title=task.title, description=task.description, important=task.important,
            recurrence=task.recurrence, due_on=next_due(task.due_on, task.recurrence),
            assignees=list(task.assignees), machine_id=task.machine_id, delivery_id=task.delivery_id,
            warehouse_id=task.warehouse_id, row=task.row, previous_id=task.id, created_by_id=task.created_by_id,
        )
        db.session.add(follow_up)
    db.session.flush()
    return follow_up


def reopen_task(task):
    """Abhaken rückgängig machen; eine dabei angelegte, noch offene Folgeaufgabe fällt weg."""
    if not task.is_done:
        return
    Task.query.filter(Task.previous_id == task.id, Task.done_at.is_(None)).delete(synchronize_session="fetch")
    task.done_at = task.done_by_id = task.done_note = None
    db.session.flush()


# --- Abfragen ---------------------------------------------------------------

def visible_query(user=None):
    """Offene + erledigte Aufgaben; mit `user` nur seine und die für alle."""
    query = Task.query
    if user is not None:
        mine = db.session.query(task_assignees.c.task_id).filter(task_assignees.c.user_id == user.id)
        anyone = ~Task.id.in_(db.session.query(task_assignees.c.task_id))
        query = query.filter(or_(Task.id.in_(mine), anyone))
    return query


def _sort_key(task):
    return (not task.important, task.due_on or date.max, task.id)


def buckets(tasks, today=None):
    """Offene Aufgaben nach Fälligkeit gruppieren."""
    today = today or date.today()
    groups = {"overdue": [], "today": [], "upcoming": [], "later": [], "undated": []}
    for task in sorted(tasks, key=_sort_key):
        if task.due_on is None:
            groups["undated"].append(task)
        elif task.due_on < today:
            groups["overdue"].append(task)
        elif task.due_on == today:
            groups["today"].append(task)
        elif task.due_on <= today + timedelta(days=UPCOMING_DAYS):
            groups["upcoming"].append(task)
        else:
            groups["later"].append(task)
    return groups


def open_tasks(user=None):
    return visible_query(user).filter(Task.done_at.is_(None)).all()


def due_now(user=None, today=None):
    """Für die Übersicht: überfällig, heute fällig und ohne Datum (wichtige zuerst)."""
    today = today or date.today()
    tasks = [t for t in open_tasks(user) if t.due_on is None or t.due_on <= today]
    return sorted(tasks, key=_sort_key)


def recently_done(user=None, days=7):
    since = utcnow() - timedelta(days=days)
    return visible_query(user).filter(Task.done_at >= since).order_by(Task.done_at.desc()).all()


def for_machine(machine_id):
    return sorted(Task.query.filter_by(machine_id=machine_id, done_at=None), key=_sort_key)


def for_delivery(delivery_id):
    return sorted(Task.query.filter_by(delivery_id=delivery_id, done_at=None), key=_sort_key)
