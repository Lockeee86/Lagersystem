"""Lagerlogik: Stapeln, Einlagern, Umlagern, Auslagern.

Alle Funktionen verändern nur die Session. Committen muss der Aufrufer.
Regeln:
- Eine Kiste kommt immer oben auf den Stapel (nächste freie Ebene).
- Wird eine Kiste aus der Mitte genommen, rutschen die darüber liegenden nach.
"""
import re

from sqlalchemy import func

from .extensions import db
from .i18n import gettext as _
from .models import Box, Movement, Warehouse

CODE_PREFIX = "K-"
CODE_RE = re.compile(r"^K-(\d+)$")


class StorageError(Exception):
    """Fachlicher Fehler, der dem Benutzer angezeigt wird."""


# --- Abfragen ---------------------------------------------------------------

def stack(warehouse_id, row, slot):
    """Alle Kisten eines Stapels, von unten nach oben."""
    return (
        Box.query.filter_by(warehouse_id=warehouse_id, row=row, slot=slot)
        .order_by(Box.level)
        .all()
    )


def stack_height(warehouse_id, row, slot):
    return (
        db.session.query(func.count(Box.id))
        .filter_by(warehouse_id=warehouse_id, row=row, slot=slot)
        .scalar()
    )


def boxes_above(box):
    """Kisten, die über `box` liegen (blockieren den Zugriff)."""
    if not box.is_stored:
        return []
    return (
        Box.query.filter(
            Box.warehouse_id == box.warehouse_id,
            Box.row == box.row,
            Box.slot == box.slot,
            Box.level > box.level,
        )
        .order_by(Box.level.desc())
        .all()
    )


def stacks_overview(warehouse):
    """{(row, slot): [box, ...]} für die Lagerkarte."""
    result = {}
    boxes = (
        Box.query.filter_by(warehouse_id=warehouse.id)
        .order_by(Box.row, Box.slot, Box.level)
        .all()
    )
    for box in boxes:
        result.setdefault((box.row, box.slot), []).append(box)
    return result


def normalize_code(raw):
    """Akzeptiert 'K-0012', 'k12', '12' oder eine URL, die auf /kiste/K-0012 endet."""
    if not raw:
        return None
    value = raw.strip().upper()
    value = value.rstrip("/").rsplit("/", 1)[-1]
    m = re.match(r"^K-?(\d+)$", value) or re.match(r"^(\d+)$", value)
    if not m:
        return None
    return format_code(int(m.group(1)))


def format_code(number):
    return f"{CODE_PREFIX}{number:04d}"


def location_text(warehouse, row, slot, level):
    return f"{warehouse.name} {warehouse.location_label(row, slot, level)}"


# --- Änderungen -------------------------------------------------------------

def _log(box, action, user, **kwargs):
    """Protokolliert eine Bewegung samt Momentaufnahme des Kisteninhalts *nach* der Änderung.
    Die Momentaufnahme macht frühere Kartoffeljahre auswertbar, auch wenn die Kiste später neu befüllt wird."""
    db.session.add(Movement(
        box=box, action=action, user_id=user.id if user else None,
        variety_id=box.variety_id, plot_id=box.plot_id, harvest_date=box.harvest_date, fill=box.fill,
        usage=box.effective_usage, seed_class_id=box.seed_class_id,
        **kwargs))


def _lock_warehouse(warehouse_id):
    """Sperrt das Lager für die Dauer der Transaktion (PostgreSQL), damit zwei
    gleichzeitige Buchungen nicht dieselbe Ebene belegen."""
    wh = db.session.get(Warehouse, warehouse_id, with_for_update=True)
    if wh is None:
        raise StorageError(_("Lager nicht gefunden."))
    return wh


def _check_position(warehouse, row, slot):
    if not (1 <= row <= warehouse.rows):
        raise StorageError(_("Reihe muss zwischen {first} und {last} liegen.",
                             first=warehouse.row_label(1), last=warehouse.row_label(warehouse.rows)))
    if not (1 <= slot <= warehouse.slots):
        raise StorageError(_("Stellplatz muss zwischen 1 und {n} liegen.", n=warehouse.slots))


def _place_on_top(box, warehouse, row, slot):
    _check_position(warehouse, row, slot)
    height = stack_height(warehouse.id, row, slot)
    if height >= warehouse.max_levels:
        raise StorageError(_("Stapel {stack} ist voll ({n} von {n} Kisten).",
                             stack=warehouse.location_label(row, slot), n=warehouse.max_levels))
    box.warehouse_id = warehouse.id
    box.row = row
    box.slot = slot
    box.level = height + 1
    db.session.flush()


def _take_out(box, user):
    """Nimmt die Kiste vom Stapel. Darüber liegende Kisten rutschen nach."""
    above = list(reversed(boxes_above(box)))  # von unten nach oben
    warehouse, row, slot = box.warehouse, box.row, box.slot
    box.warehouse_id = box.row = box.slot = box.level = None
    db.session.flush()
    for other in above:
        old = location_text(warehouse, row, slot, other.level)
        other.level -= 1
        db.session.flush()
        _log(other, "nachgerueckt", user,
             from_location=old,
             to_location=location_text(warehouse, row, slot, other.level),
             detail=box.code)
    return above


def store_box(box, warehouse_id, row, slot, user, note=None):
    if box.is_stored:
        raise StorageError(_("{code} ist bereits eingelagert ({location}).",
                             code=box.code, location=box.location))
    warehouse = _lock_warehouse(warehouse_id)
    _place_on_top(box, warehouse, row, slot)
    _log(box, "eingelagert", user,
         to_location=location_text(warehouse, row, slot, box.level), note=note)


def move_box(box, warehouse_id, row, slot, user, note=None):
    if not box.is_stored:
        raise StorageError(_("{code} ist nicht eingelagert.", code=box.code))
    if (box.warehouse_id, box.row, box.slot) == (warehouse_id, row, slot):
        raise StorageError(_("Die Kiste steht bereits auf diesem Stapel."))
    target = _lock_warehouse(warehouse_id)
    if box.warehouse_id != warehouse_id:
        _lock_warehouse(box.warehouse_id)
    _check_position(target, row, slot)
    if stack_height(target.id, row, slot) >= target.max_levels:
        raise StorageError(_("Stapel {stack} ist voll.", stack=target.location_label(row, slot)))
    old = box.location and location_text(box.warehouse, box.row, box.slot, box.level)
    shifted = _take_out(box, user)
    _place_on_top(box, target, row, slot)
    _log(box, "umgelagert", user, from_location=old,
         to_location=location_text(target, row, slot, box.level), note=note)
    return shifted


def remove_box(box, user, empty=False, note=None):
    if not box.is_stored:
        raise StorageError(_("{code} ist nicht eingelagert.", code=box.code))
    _lock_warehouse(box.warehouse_id)
    old = location_text(box.warehouse, box.row, box.slot, box.level)
    fill_before = box.fill
    shifted = _take_out(box, user)
    if empty:
        box.fill = 0
    _log(box, "ausgelagert", user, from_location=old,
         fill_before=fill_before, fill_after=box.fill, note=note)
    return shifted


def set_fill(box, fill, user, note=None):
    if fill not in (0, 25, 50, 75, 100):
        raise StorageError(_("Ungültiger Füllstand."))
    if fill == box.fill:
        return False
    fill_before = box.fill
    box.fill = fill
    _log(box, "fuellstand", user, fill_before=fill_before, fill_after=fill, note=note)
    return True


def update_contents(box, user, variety_id, harvest_date, plot_id, grade, fill, note, box_type_id=None,
                    usage=None, seed_class_id=None):
    if fill not in (0, 25, 50, 75, 100):
        raise StorageError(_("Ungültiger Füllstand."))
    box_type_id = box_type_id or box.box_type_id
    changes = []
    if box.box_type_id != box_type_id:
        changes.append("box_type")
    if box.variety_id != variety_id:
        changes.append("variety")
    if box.harvest_date != harvest_date:
        changes.append("harvest_date")
    if box.plot_id != plot_id:
        changes.append("field")
    if (box.usage or None) != (usage or None):
        changes.append("usage")
    if box.seed_class_id != seed_class_id:
        changes.append("seed_class")
    if (box.grade or None) != (grade or None):
        changes.append("grade")
    if (box.note or None) != (note or None):
        changes.append("note")
    if not changes and box.fill == fill:
        return False
    fill_before = box.fill
    box.box_type_id = box_type_id
    box.variety_id = variety_id
    box.harvest_date = harvest_date
    box.plot_id = plot_id
    box.usage = usage or None
    box.seed_class_id = seed_class_id
    box.grade = grade or None
    box.fill = fill
    box.note = note or None
    _log(box, "befuellt", user, fill_before=fill_before, fill_after=fill,
         detail=",".join(changes) or None)
    return True


def next_code_number():
    numbers = [int(m.group(1)) for (code,) in db.session.query(Box.code) if (m := CODE_RE.match(code))]
    return max(numbers, default=0) + 1


def create_box(code, box_type_id, user):
    """Legt eine Kiste mit vorgegebener Nummer an (z. B. beim Import)."""
    code = normalize_code(code)
    if not code:
        raise StorageError(_("Ungültige Kistennummer."))
    if Box.query.filter_by(code=code).first():
        raise StorageError(_("{code} gibt es schon.", code=code))
    box = Box(code=code, box_type_id=box_type_id, fill=0)
    db.session.add(box)
    db.session.flush()
    _log(box, "angelegt", user)
    return box


def create_boxes(count, box_type_id, user):
    if not (1 <= count <= 1000):
        raise StorageError(_("Es können 1 bis 1000 Kisten auf einmal angelegt werden."))
    start = next_code_number()
    boxes = []
    for n in range(start, start + count):
        box = Box(code=format_code(n), box_type_id=box_type_id, fill=0)
        db.session.add(box)
        boxes.append(box)
    db.session.flush()
    for box in boxes:
        _log(box, "angelegt", user)
    return boxes
