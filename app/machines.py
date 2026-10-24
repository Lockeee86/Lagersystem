"""Maschinen: Zählerstände, Wartungsintervalle, Logbuch (Wartung, Reparatur, Störung).

Wie `storage.py` verändern die Funktionen nur die Session, committen muss der Aufrufer.
"""
import calendar
import re
from datetime import date
from decimal import Decimal

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
    # Übliche Werte für Gas-Otto-Motoren – an den Wartungsplan des Herstellers anpassen
    "bhkw": [("Ölwechsel", 2000, None), ("Zündkerzen", 2000, None), ("Ventilspiel einstellen", 2000, None),
             ("Luftfilter", 4000, None), ("Ölanalyse", 500, None), ("Kühlwasser / Frostschutz prüfen", None, 12),
             ("Zylinderköpfe (Revision)", 30000, None), ("Generalüberholung", 60000, None),
             ("Elektroprüfung (DGUV V3)", None, 48)],
    "biogas": [("Wartung", None, 12), ("Elektroprüfung (DGUV V3)", None, 48)],
    "sonstiges": [],
}
# Fahrzeuge zählen Kilometer, alles mit Motor Betriebsstunden, der Rest nichts
DEFAULT_UNITS = {"schlepper": "h", "fahrzeug": "km", "roder": "h", "stapler": "h", "maehdrescher": "h", "bhkw": "h"}

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
    if machine.skve_chp_id:                      # BHKW: ab hier rechnet biogas.sync mit den SKVE-Stunden weiter
        machine.skve_base_counter, machine.skve_base_day = counter, day or date.today()
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


def copy_tasks(source, targets, task_ids=None, update=True):
    """Wartungsintervalle von `source` auf andere Maschinen übertragen (z. B. gleiche BHKW oder Schlepper).
    Gleichnamige Wartungen beim Ziel bekommen mit `update` die Intervalle und die Notiz, fehlende werden angelegt –
    ohne „zuletzt erledigt“, das ist je Maschine verschieden. Nur nach Zähler laufende Wartungen werden bei Zielen
    ohne Zähler übersprungen. Gibt {"created", "updated", "skipped"} zurück."""
    tasks = [t for t in source.tasks if task_ids is None or t.id in task_ids]
    stats = {"created": 0, "updated": 0, "skipped": 0}
    for target in targets:
        if target.id == source.id:
            continue
        existing = {t.name.casefold(): t for t in target.tasks}
        for task in tasks:
            hours = task.interval_hours if target.counter_unit else None
            if not hours and not task.interval_months:
                stats["skipped"] += 1
                continue
            match = existing.get(task.name.casefold())
            if match is None:
                target.tasks.append(MaintenanceTask(name=task.name, interval_hours=hours,
                                                    interval_months=task.interval_months, note=task.note))
                stats["created"] += 1
            elif update:
                match.interval_hours, match.interval_months, match.note = hours, task.interval_months, task.note
                stats["updated"] += 1
    db.session.flush()
    return stats


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



# --- Kosten je Maschine und Jahr ---------------------------------------------------

def diesel_price(year):
    """Dieselpreis je Liter für das Jahr: Mittel der Lieferungen im Jahr (nach Menge), sonst der letzte bekannte
    Preis davor. None, wenn nie ein Preis eingetragen wurde."""
    from .models import FuelEntry
    rows = (FuelEntry.query.filter(FuelEntry.kind == "lieferung", FuelEntry.price_per_liter.isnot(None),
                                   FuelEntry.date >= date(year, 1, 1), FuelEntry.date <= date(year, 12, 31)).all())
    liters = sum(r.liters for r in rows)
    if liters:
        return (sum(r.liters * r.price_per_liter for r in rows) / liters).quantize(Decimal("0.001"))
    last = (FuelEntry.query.filter(FuelEntry.kind == "lieferung", FuelEntry.price_per_liter.isnot(None),
                                   FuelEntry.date <= date(year, 12, 31))
            .order_by(FuelEntry.date.desc(), FuelEntry.id.desc()).first())
    return last.price_per_liter if last else None


def _usage(machine, year):
    """Betriebsstunden bzw. km im Jahr aus den Zählerständen (Logbuch, Tankungen): letzter Stand im Jahr minus
    letzter Stand davor (oder erster im Jahr). None ohne genug Stände."""
    from .models import FuelEntry
    start, end = date(year, 1, 1), date(year, 12, 31)
    readings = [(e.date, e.counter) for e in machine.events.filter(MachineEvent.counter.isnot(None))]
    readings += [(f.date, f.counter) for f in machine.fuel_entries.filter(FuelEntry.counter.isnot(None))]
    before = [c for d, c in readings if d < start]
    during = sorted(c for d, c in readings if start <= d <= end)
    if not during:
        return None
    base = max(before) if before else during[0]
    used = during[-1] - base
    return used if used > 0 else None


def costs(machine, year, price=None):
    """Kosten im Jahr: Diesel (getankte Liter × Preis), Reparaturen/Wartungen (Beträge im Logbuch), Bestellungen für
    die Maschine (mit Preis, nicht abgelehnt), Summe und je Betriebsstunde bzw. 100 km."""
    from .models import FuelEntry, PurchaseRequest
    start, end = date(year, 1, 1), date(year, 12, 31)
    price = diesel_price(year) if price is None else price
    liters = sum(f.liters for f in machine.fuel_entries.filter(FuelEntry.kind == "tanken", FuelEntry.date >= start,
                                                               FuelEntry.date <= end))
    diesel = (liters * price).quantize(Decimal("0.01")) if price is not None and liters else Decimal(0)
    repairs = sum((e.cost for e in machine.events.filter(MachineEvent.cost.isnot(None), MachineEvent.date >= start,
                                                         MachineEvent.date <= end)), Decimal(0))
    purchases = Decimal(0)
    for p in PurchaseRequest.query.filter(PurchaseRequest.machine_id == machine.id, PurchaseRequest.price.isnot(None),
                                          PurchaseRequest.status != "abgelehnt"):
        day = p.delivered_on or p.ordered_on or p.created_at.date()
        if start <= day <= end:
            purchases += p.price
    total = diesel + repairs + purchases
    used = _usage(machine, year) if machine.counter_unit else None
    per_unit = None
    if used and total:
        per_unit = (total / used * (100 if machine.counter_unit == "km" else 1)).quantize(Decimal("0.01"))
    return {"liters": liters, "price": price, "diesel": diesel, "repairs": repairs, "purchases": purchases,
            "total": total, "used": used, "per_unit": per_unit,
            "unit": ("€/100 km" if machine.counter_unit == "km" else "€/h") if machine.counter_unit else None}


def cost_overview(year, machines_list):
    """Kosten aller Maschinen im Jahr (teuerste zuerst) und die Summe."""
    price = diesel_price(year)
    rows = [{"machine": m, **costs(m, year, price)} for m in machines_list]
    rows.sort(key=lambda r: -r["total"])
    return rows, sum((r["total"] for r in rows), Decimal(0)), price
