"""Team-Alltag: Übergabe-Notizen, Tagesplan (= Aufgaben mit geplantem Tag), Unterweisungen mit Unterschrift.

Wie `storage.py` ändern die Funktionen nur die Session, committen muss der Aufrufer. Push-Nachrichten
(`send_plan`, `handover_posted`) erst nach dem Commit aufrufen.
"""
from datetime import date, timedelta

from flask import url_for
from sqlalchemy import func

from . import push, signatures
from .extensions import db
from .i18n import N_, gettext as _
from .machines import add_months
from .models import (Handover, InstructionRecord, InstructionTopic, Machine, MachineEvent, MaintenanceTask, Plot, Task,
                     User, utcnow)
from .push import Msg
from .storage import StorageError

HANDOVER_DAYS = 3            # so lange bleibt eine offene Übergabe auf der Startseite
SOON_DAYS = 30               # Unterweisung „bald fällig“

# Vorschläge für die ersten Unterweisungen (Name, Wiederholung in Monaten, Inhalt)
INSTRUCTION_SUGGESTIONS = [
    (N_("Arbeitsschutz allgemein"), 12, N_("Gefahren auf dem Hof, Schutzkleidung, Verhalten bei Unfällen, Notrufnummern, Sammelplatz")),
    (N_("Stapler / Teleskoplader"), 12, N_("Sicht und Tragfähigkeit, Lasten aufnehmen und stapeln, Fahren mit Last, Abstellen und Laden")),
    (N_("Schlepper und Anhänger"), 12, N_("Abfahrtskontrolle, Anbaugeräte an- und abkuppeln, Zapfwelle, Straßenverkehr, Beleuchtung")),
    (N_("Kistenlager"), 12, N_("Stapelhöhe, beschädigte Kisten melden, Wege freihalten, Lüftung, Absturz- und Quetschgefahr")),
    (N_("Pflanzenschutz"), 12, N_("Schutzausrüstung, Ansetzen der Spritzbrühe, Reinigung, Lagerung der Mittel")),
    (N_("Brandschutz und Erste Hilfe"), 12, N_("Feuerlöscher und Verbandkasten (Ort), Fluchtwege, Erste Hilfe, Notruf 112")),
]


def _text(value, limit):
    return (value or "").replace("\r\n", "\n").strip()[:limit]


# --- Übergabe ----------------------------------------------------------------

def add_handover(user, text):
    text = _text(text, 2000)
    if not text:
        raise StorageError(_("Bitte eintragen, was der Nächste wissen muss."))
    note = Handover(text=text, user_id=user.id if user else None)
    db.session.add(note)
    db.session.flush()
    return note


def close_handover(note, user):
    if note.done_at:
        raise StorageError(_("Die Notiz ist schon erledigt."))
    note.done_at, note.done_by_id = utcnow(), user.id


def open_handovers(now=None):
    """Für die Startseite: offene Notizen der letzten HANDOVER_DAYS Tage, neueste zuerst."""
    since = (now or utcnow()) - timedelta(days=HANDOVER_DAYS)
    return (Handover.query.filter(Handover.done_at.is_(None), Handover.created_at >= since)
            .order_by(Handover.created_at.desc()).all())


def recent_handovers(days=60):
    since = utcnow() - timedelta(days=days)
    return Handover.query.filter(Handover.created_at >= since).order_by(Handover.created_at.desc()).all()


def handover_posted(note):
    """Push an alle Mitarbeiter, die das Thema eingeschaltet haben (standardmäßig aus)."""
    return push.notify(push.editors(), "handover", Msg("Übergabe von {name}", name=note.user.name if note.user else "–"),
                       note.text.splitlines()[0][:150], url_for("main.dashboard") + "#uebergabe",
                       tag=f"handover-{note.id}", exclude=note.user)


# --- Tagesplan ---------------------------------------------------------------

def team_members():
    """Wer im Tagesplan steht: aktive Mitarbeiter und Admins."""
    return [u for u in User.query.filter_by(active=True).order_by(User.display_name, User.username) if u.can_edit]


OPEN_AHEAD_DAYS = 7      # „Offen“ zeigt Aufgaben mit Termin bis so viele Tage voraus (spätere unter „Alle“)
SUGGEST_DAYS = 7         # Wartungen, die in so vielen Tagen fällig werden, schon vorschlagen


def _loaded(query):
    from sqlalchemy.orm import joinedload, selectinload
    return query.options(selectinload(Task.assignees), selectinload(Task.plots), joinedload(Task.machine))


def plan_for(day, today=None, exact=False):
    """{user_id: [Aufgaben]} eines Tages. Für heute auch, was an früheren Tagen eingeplant und liegen geblieben ist
    (`exact` = nur genau dieser Tag, z. B. zum Übernehmen)."""
    today = today or date.today()
    query = Task.query.filter(Task.plan_day.isnot(None))
    if day == today and not exact:
        query = query.filter(db.or_(Task.plan_day == day, db.and_(Task.plan_day < day, Task.done_at.is_(None))))
    else:
        query = query.filter(Task.plan_day == day)
    plan = {}
    for task in _loaded(query).order_by(Task.plan_day, Task.plan_position, Task.id):
        if task.plan_user:
            plan.setdefault(task.plan_user.id, []).append(task)
    return plan


def _next_position(day):
    return (db.session.query(func.max(Task.plan_position)).filter(Task.plan_day == day).scalar() or 0) + 1


def _check_member(user):
    if user is None or not user.active or not user.can_edit:
        raise StorageError(_("Bitte einen Mitarbeiter wählen."))


def add_plan_entry(day, user, text, actor, machine_id=None, plot_ids=()):
    """Neue Aufgabe direkt in den Tagesplan einer Person."""
    text = _text(text, 200)
    if not text:
        raise StorageError(_("Bitte eintragen, was zu tun ist."))
    _check_member(user)
    machine_id = machine_id if machine_id and db.session.get(Machine, machine_id) else None
    plots = Plot.query.filter(Plot.id.in_(set(plot_ids))).order_by(Plot.name).all() if plot_ids else []
    task = Task(title=text, machine_id=machine_id, plots=plots, assignees=[user], plan_day=day,
                plan_position=_next_position(day), created_by_id=actor.id if actor else None)
    db.session.add(task)
    db.session.flush()
    return task


def add_open_task(text, user, machine_id=None, important=False):
    """Kurz notieren: Aufgabe ohne Person und Tag („Offen“)."""
    text = _text(text, 200)
    if not text:
        raise StorageError(_("Bitte eintragen, was zu tun ist."))
    machine_id = machine_id if machine_id and db.session.get(Machine, machine_id) else None
    task = Task(title=text, machine_id=machine_id, important=bool(important), created_by_id=user.id if user else None)
    db.session.add(task)
    db.session.flush()
    return task


def plan_task(task, day, user, actor):
    """Aufgabe für `user` an `day` einplanen – Planer für alle, Mitarbeiter nur sich selbst („Mach ich“)."""
    if task.is_done:
        raise StorageError(_("Das ist schon erledigt."))
    _check_member(user)
    if not actor.is_planner:
        if actor.id != user.id:
            raise StorageError(_("Einplanen kann nur, wer den Tagesplan pflegt – oder du nimmst es dir selbst."))
        if task.assignees and actor not in task.assignees:
            raise StorageError(_("Das ist schon jemand anderem zugeteilt."))
    task.plan_day, task.assignees, task.plan_position = day, [user], _next_position(day)
    db.session.flush()
    return task


def unplan_task(task):
    """Zurück nach „Offen“ (ohne Tag und Person)."""
    task.plan_day, task.plan_position, task.assignees = None, 0, []
    db.session.flush()


def can_change(task, user):
    """Löschen bzw. „hat sich erledigt“: Planer oder wer es notiert hat."""
    return bool(user.is_planner or task.created_by_id == user.id)


def last_planned_day(before):
    return db.session.query(func.max(Task.plan_day)).filter(Task.plan_day < before).scalar()


def copy_plan(source_day, target_day, actor):
    """Den Plan eines Tages als neue Aufgaben übernehmen – nur für Leute, die am Zieltag noch nichts haben.

    Nur Erledigtes wird wiederholt: Unerledigtes wandert ohnehin mit („seit …“) und stünde sonst doppelt da."""
    if source_day is None:
        raise StorageError(_("Es gibt noch keinen früheren Tagesplan."))
    planned = set(plan_for(target_day, exact=True))
    members = {u.id for u in team_members()}
    copied = 0
    for user_id, entries in plan_for(source_day, exact=True).items():
        if user_id in planned or user_id not in members:
            continue
        for e in entries:
            if not e.is_done:
                continue
            db.session.add(Task(title=e.title, machine_id=e.machine_id, plots=list(e.plots), assignees=[e.plan_user],
                                plan_day=target_day, plan_position=e.plan_position,
                                created_by_id=actor.id if actor else None))
            copied += 1
    db.session.flush()
    return copied


def toggle_plan_entry(task, user):
    """Abhaken bzw. wieder öffnen. Gibt True zurück, wenn jetzt erledigt."""
    from . import tasks
    if not task.can_check(user):
        raise StorageError(_("Abhaken kann nur, wer eingeteilt ist."))
    if task.is_done:
        tasks.reopen_task(task)
    else:
        tasks.complete_task(task, user)
    return task.is_done


def open_tasks(day, user, today=None):
    """„Offen“: nicht erledigt und nicht eingeplant, ohne Termin oder bis eine Woche voraus (ohne Hühner).
    Mitarbeiter sehen nur, was für alle ist; überfällig und wichtig zuerst."""
    from . import machines
    query = Task.query.filter(Task.done_at.is_(None), Task.plan_day.is_(None),
                              db.or_(Task.area.is_(None), Task.area != "huehner"),
                              db.or_(Task.due_on.is_(None), Task.due_on <= day + timedelta(days=OPEN_AHEAD_DAYS)))
    rows = [t for t in _loaded(query) if (not t.machine or machines.can_see(t.machine, user))
            and (user.is_planner or not t.assignees)]
    return sorted(rows, key=lambda t: (not (t.due_on and t.due_on <= day), not t.important,
                                       t.due_on or date.max, t.created_at))


def my_today(user, today=None):
    """„Meine Aufgaben heute“: heute eingeplant (auch schon abgehakt), früher eingeplant und liegen geblieben, und
    mir zugeteilte Aufgaben ohne Tag, die heute fällig sind oder keinen Termin haben."""
    from .models import task_assignees
    today = today or date.today()
    mine = db.session.query(task_assignees.c.task_id).filter(task_assignees.c.user_id == user.id)
    query = Task.query.filter(Task.id.in_(mine), db.or_(
        Task.plan_day == today,
        db.and_(Task.plan_day < today, Task.done_at.is_(None)),
        db.and_(Task.plan_day.is_(None), Task.done_at.is_(None),
                db.or_(Task.due_on.is_(None), Task.due_on <= today))))
    return sorted(_loaded(query), key=lambda t: (t.is_done, t.plan_day or date.max, t.plan_position, t.id))


def suggestions(day, user=None):
    """Was sich für `day` anbietet, aber noch keine Aufgabe ist: fällige Wartungen und offene Störungen.

    Liste von Dicts {key, kind, text, item, machine, due_on, left_counter, late}; überfällig zuerst.
    `user` (wer plant) filtert Maschinen, die er nicht sehen darf (Biogas).
    """
    from . import machines
    linked = Task.query.filter(Task.done_at.is_(None))
    linked_maint = {t.maintenance_task_id for t in linked if t.maintenance_task_id}
    linked_events = {t.machine_event_id for t in linked if t.machine_event_id}
    rows = []
    for mt, status in machines.due_tasks(user=user):
        by_date = status["left_days"] is not None and status["left_days"] <= SUGGEST_DAYS
        by_counter = (status["left_counter"] is not None
                      and status["left_counter"] <= max(10, (mt.interval_hours or 0) // 10))
        if not (status["state"] == "overdue" or by_date or by_counter) or mt.id in linked_maint:
            continue
        rows.append({"key": f"wartung-{mt.id}", "kind": "wartung", "text": mt.name, "item": mt, "machine": mt.machine,
                     "due_on": status["due_on"] if by_date or not by_counter else None,
                     "left_counter": status["left_counter"] if by_counter else None,
                     "late": status["state"] == "overdue", "important": False})
    for ev in machines.open_faults(user):
        if ev.id in linked_events:
            continue
        rows.append({"key": f"stoerung-{ev.id}", "kind": "stoerung", "text": (ev.text or "").strip()[:200] or ev.machine.name,
                     "item": ev, "machine": ev.machine, "due_on": ev.date, "left_counter": None,
                     "late": not ev.still_running, "important": not ev.still_running})
    order = {"stoerung": 0, "wartung": 1}
    return sorted(rows, key=lambda r: (not r["late"], not r["important"], order[r["kind"]], r["due_on"] or date.max))


def add_suggestion(day, user, key, actor):
    """Wartung (`wartung-5`) bzw. Störung (`stoerung-7`) als Aufgabe für `user` einplanen – Abhaken erledigt sie mit."""
    match = {s["key"]: s for s in suggestions(day, actor)}.get(key)
    if match is None:
        raise StorageError(_("Dieser Vorschlag ist schon eingeplant oder erledigt."))
    task = add_plan_entry(day, user, match["text"], actor, machine_id=match["machine"].id if match["machine"] else None)
    if match["kind"] == "wartung":
        task.maintenance_task_id = match["item"].id
    else:
        task.machine_event_id, task.important = match["item"].id, match["important"]
    db.session.flush()
    return task


def send_plan(day, actor):
    """Jedem Eingeteilten seinen Plan aufs Handy schicken (nach dem Commit). Gibt die Zahl der Empfänger zurück."""
    sent = 0
    for user_id, entries in plan_for(day).items():
        user = entries[0].plan_user
        lines = [e.title + (f" ({e.machine.name})" if e.machine else "") for e in entries if not e.is_done]
        if not lines:
            continue
        sent += push.notify([user], "day_plan", Msg("Dein Plan für {day}", day=day.strftime("%d.%m.")),
                            " · ".join(lines)[:200], url_for("team.day_plan", datum=day.isoformat()),
                            tag=f"plan-{day.isoformat()}")
    return sent


# --- Unterweisungen ----------------------------------------------------------

def save_topic(topic, name, interval_months, content, for_all, user_ids, active=True):
    name = _text(name, 100)
    if not name:
        raise StorageError(_("Bitte einen Namen angeben."))
    other = InstructionTopic.query.filter(func.lower(InstructionTopic.name) == name.lower()).first()
    if other is not None and other is not topic:
        raise StorageError(_("{name} gibt es schon.", name=name))
    if not interval_months or not 1 <= interval_months <= 60:
        raise StorageError(_("Wiederholung bitte zwischen 1 und 60 Monaten."))
    if topic is None:
        topic = InstructionTopic()
        db.session.add(topic)
    topic.name, topic.interval_months, topic.content = name, interval_months, _text(content, 3000) or None
    topic.for_all, topic.active = bool(for_all), bool(active)
    topic.users = [] if for_all else [u for u in team_members() if u.id in set(user_ids or ())]
    db.session.flush()
    return topic


def participants(topic):
    return team_members() if topic.for_all else [u for u in topic.users if u.active]


def last_records(topic):
    """{user_id: letzte Unterweisung}."""
    last = {}
    for record in sorted(topic.records, key=lambda r: (r.day, r.id or 0), reverse=True):
        last.setdefault(record.user_id, record)
    return last


def status(topic, user, last=None, today=None):
    """{"record", "due_on", "level": ok|soon|due|never}."""
    today = today or date.today()
    record = (last if last is not None else last_records(topic)).get(user.id)
    if record is None:
        return {"record": None, "due_on": None, "level": "never"}
    due_on = add_months(record.day, topic.interval_months)
    level = "due" if due_on <= today else "soon" if due_on <= today + timedelta(days=SOON_DAYS) else "ok"
    return {"record": record, "due_on": due_on, "level": level}


def overview(today=None):
    """Je aktivem Thema: Personen mit Status und Zählern. Für die Übersichtsseite und „Jetzt wichtig“."""
    rows = []
    for topic in InstructionTopic.query.filter_by(active=True).order_by(InstructionTopic.name):
        last = last_records(topic)
        people = [(u, status(topic, u, last, today)) for u in participants(topic)]
        rows.append({"topic": topic, "people": people,
                     "due": sum(1 for _u, s in people if s["level"] in ("due", "never")),
                     "soon": sum(1 for _u, s in people if s["level"] == "soon")})
    return rows


def due_count(today=None):
    return sum(row["due"] for row in overview(today))


def sign_instruction(topic, user, day, data_url, instructor):
    if user is None or user not in participants(topic):
        raise StorageError(_("{name} gehört nicht zu dieser Unterweisung.", name=user.name if user else "–"))
    if day is None or day > date.today():
        raise StorageError(_("Bitte ein gültiges Datum angeben (nicht in der Zukunft)."))
    record = InstructionRecord(topic=topic, user_id=user.id, day=day, signature=signatures.to_png(data_url),
                               signed_name=user.name, instructor_id=instructor.id, instructor_name=instructor.name)
    db.session.add(record)
    db.session.flush()
    return record


def records_in_year(topic, year):
    return sorted((r for r in topic.records if r.day.year == year), key=lambda r: (r.day, r.signed_name.lower()))
