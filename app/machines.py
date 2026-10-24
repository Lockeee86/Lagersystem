"""Maschinen: Zählerstände, Wartungsintervalle, Logbuch (Wartung, Reparatur, Störung).

Wie `storage.py` verändern die Funktionen nur die Session, committen muss der Aufrufer.
"""
import calendar
import re
from datetime import date

from .extensions import db
from .i18n import gettext as _
from .models import Machine, MachineEvent, MaintenanceTask, Photo, utcnow
from .storage import StorageError

CODE_RE = re.compile(r"^M-?(\d+)$")

# Vorschläge je Maschinentyp: (Name, Intervall Zähler, Intervall Monate). Werden beim Anlegen
# übernommen und lassen sich danach je Maschine ändern oder löschen.
TASK_TEMPLATES = {
    "schlepper": [("Motoröl und Filter", 500, 12), ("Hydrauliköl und Filter", 1000, 24),
                  ("Luftfilter", 500, None), ("Hauptuntersuchung (TÜV)", None, 24), ("UVV-Prüfung", None, 12)],
    "fahrzeug": [("Inspektion", 15000, 12), ("Hauptuntersuchung (TÜV)", None, 24)],
    "roder": [("Saisonkontrolle vor der Ernte", None, 12), ("Getriebeöl", 250, 12), ("UVV-Prüfung", None, 12)],
    "maehdrescher": [("Motoröl und Filter", 250, 12), ("Saisonkontrolle vor der Ernte", None, 12),
                     ("Hauptuntersuchung (TÜV)", None, 24), ("UVV-Prüfung", None, 12)],
    "saetechnik": [("Saisonkontrolle vor der Aussaat", None, 12), ("UVV-Prüfung", None, 12)],
    "bodenbearbeitung": [("Verschleißteile prüfen", None, 12), ("UVV-Prüfung", None, 12)],
    "pflanzenschutz": [("Pflanzenschutzgeräte-Kontrolle", None, 36), ("Düsen und Filter prüfen", None, 12),
                       ("UVV-Prüfung", None, 12)],
    "duengung": [("Saisonkontrolle", None, 12), ("UVV-Prüfung", None, 12)],
    "legemaschine": [("Saisonkontrolle vor dem Legen", None, 12), ("UVV-Prüfung", None, 12)],
    "sortieranlage": [("Saisonkontrolle", None, 12), ("Elektroprüfung (DGUV V3)", None, 48)],
    "anhaenger": [("Hauptuntersuchung (TÜV)", None, 24), ("Bremsen prüfen", None, 12), ("UVV-Prüfung", None, 12)],
    "stapler": [("Wartung", 500, 12), ("UVV-Prüfung", None, 12)],
    "lagertechnik": [("Wartung Lüftung", None, 12), ("Elektroprüfung (DGUV V3)", None, 48)],
    "sonstiges": [],
}
# Fahrzeuge zählen Kilometer, alles mit Motor Betriebsstunden, der Rest nichts
DEFAULT_UNITS = {"schlepper": "h", "fahrzeug": "km", "roder": "h", "stapler": "h", "maehdrescher": "h"}

SOON_DAYS = 30


def normalize_code(raw):
    if not raw:
        return None
    m = CODE_RE.match(raw.strip().upper().rstrip("/").rsplit("/", 1)[-1])
    return f"M-{int(m.group(1)):03d}" if m else None


def next_code():
    numbers = [int(m.group(1)) for (c,) in db.session.query(Machine.code) if (m := CODE_RE.match(c))]
    return f"M-{max(numbers, default=0) + 1:03d}"


def create_machine(name, category, user, with_templates=True, **fields):
    machine = Machine(code=next_code(), name=name, category=category, **fields)
    if with_templates:
        for task_name, hours, months in TASK_TEMPLATES.get(category, []):
            if hours and not machine.counter_unit:
                if not months:
                    continue
                hours = None
            machine.tasks.append(MaintenanceTask(name=task_name, interval_hours=hours, interval_months=months))
    db.session.add(machine)
    db.session.flush()
    return machine


def _event(machine, kind, user, day=None, **fields):
    event = MachineEvent(machine=machine, kind=kind, date=day or date.today(),
                         user_id=user.id if user else None, **fields)
    db.session.add(event)
    return event


def _attach(event, photo_names, user):
    for name in photo_names or []:
        event.photos.append(Photo(filename=name, user_id=user.id if user else None))


def _raise_counter(machine, counter):
    if counter is not None and (machine.counter is None or counter > machine.counter):
        machine.counter = counter
        machine.counter_at = utcnow()


def set_counter(machine, counter, user, day=None, photo_names=None):
    if not machine.counter_unit:
        raise StorageError(_("Diese Maschine hat keinen Zähler."))
    if counter is None or counter < 0:
        raise StorageError(_("Bitte einen gültigen Zählerstand angeben."))
    if machine.counter is not None and counter < machine.counter:
        raise StorageError(_("Der Zählerstand ist kleiner als der letzte ({counter} {unit}).",
                             counter=machine.counter, unit=machine.unit_short))
    _raise_counter(machine, counter)
    event = _event(machine, "zaehler", user, day, counter=counter)
    _attach(event, photo_names, user)
    return event


def complete_task(task, user, day=None, counter=None, text=None, cost=None, photo_names=None):
    machine = task.machine
    day = day or date.today()
    if counter is None and machine.counter_unit:
        counter = machine.counter
    task.last_done_on = day
    task.last_done_counter = counter if machine.counter_unit else None
    _raise_counter(machine, counter if machine.counter_unit else None)
    event = _event(machine, "wartung", user, day, counter=counter if machine.counter_unit else None,
                   task=task, task_name=task.name, text=text or None, cost=cost)
    _attach(event, photo_names, user)
    return event


def add_event(machine, kind, user, day=None, text=None, cost=None, counter=None, photo_names=None):
    if kind not in ("reparatur", "stoerung", "notiz"):
        raise StorageError(_("Unbekannte Art des Eintrags."))
    if not text and not photo_names:
        raise StorageError(_("Bitte beschreiben, was los ist."))
    if machine.counter_unit:
        _raise_counter(machine, counter)
    else:
        counter = None
    event = _event(machine, kind, user, day, text=text or None, cost=cost, counter=counter)
    _attach(event, photo_names, user)
    return event


def resolve_fault(event, user):
    if event.kind != "stoerung" or event.resolved_at:
        raise StorageError(_("Diese Störung ist schon erledigt."))
    event.resolved_at = utcnow()
    event.resolved_by_id = user.id if user else None


# --- Fälligkeiten ------------------------------------------------------------

def add_months(day, months):
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def task_status(task, today=None):
    """{state: overdue|soon|ok|unknown, due_on, due_counter, left_counter, left_days}"""
    today = today or date.today()
    machine = task.machine
    result = {"state": "ok", "due_on": None, "due_counter": None, "left_counter": None, "left_days": None}
    uses_counter = bool(task.interval_hours and machine.counter_unit)
    if task.last_done_on is None and (not uses_counter or task.last_done_counter is None):
        result["state"] = "unknown"
        return result
    states = []
    if task.interval_months and task.last_done_on:
        result["due_on"] = add_months(task.last_done_on, task.interval_months)
        result["left_days"] = (result["due_on"] - today).days
        states.append("overdue" if result["left_days"] <= 0 else "soon" if result["left_days"] <= SOON_DAYS else "ok")
    if uses_counter and task.last_done_counter is not None:
        result["due_counter"] = task.last_done_counter + task.interval_hours
        current = machine.counter or 0
        result["left_counter"] = result["due_counter"] - current
        margin = max(10, task.interval_hours // 10)
        states.append("overdue" if result["left_counter"] <= 0 else "soon" if result["left_counter"] <= margin else "ok")
    for state in ("overdue", "soon"):
        if state in states:
            result["state"] = state
            break
    return result


def due_tasks(states=("overdue", "soon")):
    """Fällige Wartungen aller aktiven Maschinen, überfällige zuerst."""
    rows = []
    for task in MaintenanceTask.query.join(Machine).filter(Machine.active.is_(True)):
        status = task_status(task)
        if status["state"] in states:
            rows.append((task, status))
    order = {"overdue": 0, "soon": 1, "unknown": 2, "ok": 3}
    return sorted(rows, key=lambda r: (order[r[1]["state"]], r[1]["left_days"] if r[1]["left_days"] is not None
                                       else 9999, r[0].machine.name))


def open_faults():
    return (MachineEvent.query.join(Machine)
            .filter(MachineEvent.kind == "stoerung", MachineEvent.resolved_at.is_(None), Machine.active.is_(True))
            .order_by(MachineEvent.date.desc(), MachineEvent.id.desc()).all())


def machine_summary(machine):
    """Ampel einer Maschine für Listen: (state, offene Störungen)."""
    states = {task_status(t)["state"] for t in machine.tasks}
    faults = machine.events.filter(MachineEvent.kind == "stoerung", MachineEvent.resolved_at.is_(None)).count()
    state = "overdue" if "overdue" in states else "soon" if "soon" in states else \
        "unknown" if "unknown" in states else "ok"
    return state, faults

