"""Team-Alltag: Übergabe-Notizen, Tagesplan, Unterweisungen mit Unterschrift.

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
from .models import (Handover, InstructionRecord, InstructionTopic, Machine, PlanEntry, Plot, User, utcnow)
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


def plan_for(day):
    """{user_id: [Einträge]} für einen Tag."""
    plan = {}
    for entry in PlanEntry.query.filter_by(day=day).order_by(PlanEntry.position, PlanEntry.id):
        plan.setdefault(entry.user_id, []).append(entry)
    return plan


def add_plan_entry(day, user, text, actor, machine_id=None, plot_ids=()):
    text = _text(text, 200)
    if not text:
        raise StorageError(_("Bitte eintragen, was zu tun ist."))
    if user is None or not user.active or not user.can_edit:
        raise StorageError(_("Bitte einen Mitarbeiter wählen."))
    machine_id = machine_id if machine_id and db.session.get(Machine, machine_id) else None
    plots = Plot.query.filter(Plot.id.in_(set(plot_ids))).order_by(Plot.name).all() if plot_ids else []
    position = (db.session.query(func.max(PlanEntry.position)).filter_by(day=day, user_id=user.id).scalar() or 0) + 1
    entry = PlanEntry(day=day, user_id=user.id, text=text, machine_id=machine_id, plots=plots,
                      position=position, created_by_id=actor.id if actor else None)
    db.session.add(entry)
    db.session.flush()
    return entry


def last_planned_day(before):
    return db.session.query(func.max(PlanEntry.day)).filter(PlanEntry.day < before).scalar()


def copy_plan(source_day, target_day, actor):
    """Einträge eines Tages übernehmen – nur für Leute, die am Zieltag noch nichts haben. Gibt die Anzahl zurück."""
    if source_day is None:
        raise StorageError(_("Es gibt noch keinen früheren Tagesplan."))
    planned = set(plan_for(target_day))
    members = {u.id for u in team_members()}
    copied = 0
    for user_id, entries in plan_for(source_day).items():
        if user_id in planned or user_id not in members:
            continue
        for e in entries:
            db.session.add(PlanEntry(day=target_day, user_id=user_id, text=e.text, machine_id=e.machine_id,
                                     plots=list(e.plots), position=e.position,
                                     created_by_id=actor.id if actor else None))
            copied += 1
    db.session.flush()
    return copied


def toggle_plan_entry(entry, user):
    """Abhaken bzw. wieder öffnen (eingeteilte Person oder Admin). Gibt True zurück, wenn jetzt erledigt."""
    if not entry.can_check(user):
        raise StorageError(_("Abhaken kann nur, wer eingeteilt ist."))
    if entry.done_at:
        entry.done_at, entry.done_by_id = None, None
    else:
        entry.done_at, entry.done_by_id = utcnow(), user.id
    return entry.done_at is not None


def open_entries(day):
    """Nicht erledigte Einträge eines Tages."""
    return [e for entries in plan_for(day).values() for e in entries if not e.done_at]


def last_open_day(before):
    """Letzter Tag vor `before` mit unerledigten Einträgen – höchstens eine Woche zurück."""
    return (db.session.query(func.max(PlanEntry.day))
            .filter(PlanEntry.day < before, PlanEntry.day >= before - timedelta(days=7), PlanEntry.done_at.is_(None))
            .scalar())


def carry_candidates(source_day, target_day):
    """Unerledigtes von `source_day`, das am `target_day` noch fehlt (gleiche Person, gleicher Text nur einmal)."""
    if source_day is None:
        return []
    members = {u.id for u in team_members()}
    target = {(e.user_id, e.text) for entries in plan_for(target_day).values() for e in entries}
    return [e for e in open_entries(source_day) if e.user_id in members and (e.user_id, e.text) not in target]


def carry_over(source_day, target_day, actor):
    """Unerledigtes von `source_day` an `target_day` hinten anhängen. Gibt die Anzahl zurück."""
    if source_day is None:
        raise StorageError(_("Es gibt nichts Unerledigtes zu übernehmen."))
    candidates = carry_candidates(source_day, target_day)
    for e in candidates:
        position = (db.session.query(func.max(PlanEntry.position))
                    .filter_by(day=target_day, user_id=e.user_id).scalar() or 0) + 1
        db.session.add(PlanEntry(day=target_day, user_id=e.user_id, text=e.text, machine_id=e.machine_id,
                                 plots=list(e.plots), position=position, created_by_id=actor.id if actor else None))
        db.session.flush()
    return len(candidates)


def send_plan(day, actor):
    """Jedem Eingeteilten seinen Plan aufs Handy schicken (nach dem Commit). Gibt die Zahl der Empfänger zurück."""
    sent = 0
    for user_id, entries in plan_for(day).items():
        user = entries[0].user
        lines = [e.text + (f" ({e.machine.name})" if e.machine else "") for e in entries]
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
