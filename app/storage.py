"""Lagerlogik: Stapeln, Einlagern, Umlagern, Auslagern.

Alle Funktionen verändern nur die Session. Committen muss der Aufrufer.
Regeln:
- Eine Kiste kommt immer oben auf den Stapel (nächste freie Ebene).
- Wird eine Kiste aus der Mitte genommen, rutschen die darüber liegenden nach.
"""
import re
from decimal import Decimal, InvalidOperation

from sqlalchemy import func

from .extensions import db
from .i18n import gettext as _
from .models import Box, Lot, Movement, Warehouse, format_quantity

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


def update_contents(box, user, variety_id, harvest_date, plot_id, grade, fill, note, box_type_id=None):
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


# --- Partien ----------------------------------------------------------------
# Eine Partie ist ein Block gleicher Kartoffeln in einer Reihe (wie ein Eintrag an der Tafel).
# Mengen sind Kisten mit Nachkommastellen (41,75 = 41 volle + eine ¾ Kiste).

QUANTITY_MAX = Decimal("100000")


def parse_quantity(value):
    """'41,75' / '41.75' / 41.75 → Decimal('41.75'); None bei leer/ungültig."""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
    else:
        text = str(value).strip().replace(" ", "").replace(",", ".")
        text = text.rstrip("xX×")
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    if not number.is_finite():
        return None
    return number.quantize(Decimal("0.01"))


def _check_quantity(qty, allow_zero=False):
    if qty is None or qty < 0 or (qty == 0 and not allow_zero) or qty >= QUANTITY_MAX:
        raise StorageError(_("Bitte eine gültige Menge angeben (z. B. 12 oder 41,75)."))
    return qty


def _log_lot(lot, action, user, quantity_delta=None, **kwargs):
    """Verlaufseintrag für eine Partie mit Momentaufnahme *nach* der Änderung."""
    db.session.add(Movement(
        lot=lot, action=action, user_id=user.id if user else None,
        variety_id=lot.variety_id, plot_id=lot.plot_id, harvest_date=lot.harvest_date,
        usage=lot.usage, seed_class_id=lot.seed_class_id,
        quantity_delta=quantity_delta, quantity_after=lot.quantity, **kwargs))


def lot_location_text(warehouse, row):
    return f"{warehouse.name} {_('Reihe {row}', row=warehouse.row_label(row))}"


def row_lots(warehouse_id, row):
    return (Lot.query.filter_by(warehouse_id=warehouse_id, row=row, closed=False)
            .order_by(Lot.position, Lot.id).all())


def row_quantity(warehouse_id, row):
    total = (db.session.query(func.coalesce(func.sum(Lot.quantity), 0))
             .filter_by(warehouse_id=warehouse_id, row=row, closed=False).scalar())
    return Decimal(str(total))


def _next_position(warehouse_id, row):
    current = (db.session.query(func.max(Lot.position))
               .filter_by(warehouse_id=warehouse_id, row=row, closed=False).scalar())
    return (current or 0) + 1


def _check_row(warehouse, row):
    if not (1 <= row <= warehouse.rows):
        raise StorageError(_("Reihe muss zwischen {first} und {last} liegen.",
                             first=warehouse.row_label(1), last=warehouse.row_label(warehouse.rows)))


def create_lot(user, warehouse_id, row, variety_id, quantity, box_type_id, usage="pflanzgut",
               seed_class_id=None, grade=None, plot_id=None, harvest_date=None, note=None):
    warehouse = _lock_warehouse(warehouse_id)
    _check_row(warehouse, row)
    _check_quantity(quantity)
    lot = Lot(warehouse_id=warehouse.id, row=row, position=_next_position(warehouse.id, row),
              variety_id=variety_id, usage=usage, seed_class_id=seed_class_id, grade=grade or None,
              plot_id=plot_id, harvest_date=harvest_date, quantity=quantity, box_type_id=box_type_id,
              note=note or None)
    db.session.add(lot)
    db.session.flush()
    _log_lot(lot, "partie_angelegt", user, quantity_delta=quantity,
             to_location=lot_location_text(warehouse, row))
    return lot


def _close_if_empty(lot):
    if lot.quantity <= 0:
        lot.quantity = Decimal("0")
        lot.closed = True


def take_from_lot(lot, quantity, user, note=None):
    _check_quantity(quantity)
    if lot.closed or quantity > lot.quantity:
        raise StorageError(_("Es sind nur {n} Kisten vorhanden.", n=format_quantity(lot.quantity)))
    _lock_warehouse(lot.warehouse_id)
    lot.quantity -= quantity
    _close_if_empty(lot)
    _log_lot(lot, "entnommen", user, quantity_delta=-quantity,
             from_location=lot_location_text(lot.warehouse, lot.row), note=note)


def add_to_lot(lot, quantity, user, note=None):
    _check_quantity(quantity)
    if lot.closed:
        raise StorageError(_("Diese Partie ist abgeschlossen."))
    lot.quantity += quantity
    _log_lot(lot, "zugang", user, quantity_delta=quantity,
             to_location=lot_location_text(lot.warehouse, lot.row), note=note)


def correct_lot(lot, quantity, user, note=None):
    """Bestand auf einen gezählten Wert setzen (Inventur)."""
    _check_quantity(quantity, allow_zero=True)
    if quantity == lot.quantity:
        return False
    delta = quantity - lot.quantity
    lot.quantity = quantity
    lot.closed = False
    _close_if_empty(lot)
    _log_lot(lot, "korrektur", user, quantity_delta=delta, note=note)
    return True


def move_lot(lot, warehouse_id, row, user, quantity=None, note=None):
    """Partie (oder einen Teil davon) in eine andere Reihe setzen.
    Mit Teilmenge entsteht dort eine neue Partie mit denselben Angaben."""
    if lot.closed:
        raise StorageError(_("Diese Partie ist abgeschlossen."))
    target = _lock_warehouse(warehouse_id)
    _check_row(target, row)
    if quantity is not None:
        _check_quantity(quantity)
        if quantity > lot.quantity:
            raise StorageError(_("Es sind nur {n} Kisten vorhanden.", n=format_quantity(lot.quantity)))
    old = lot_location_text(lot.warehouse, lot.row)
    new = lot_location_text(target, row)
    if quantity is None or quantity == lot.quantity:
        if (lot.warehouse_id, lot.row) == (target.id, row):
            raise StorageError(_("Die Partie steht bereits in dieser Reihe."))
        lot.warehouse_id, lot.row = target.id, row
        lot.position = _next_position(target.id, row)
        db.session.flush()
        _log_lot(lot, "umgesetzt", user, from_location=old, to_location=new, note=note)
        return lot
    part = Lot(warehouse_id=target.id, row=row, position=_next_position(target.id, row),
               variety_id=lot.variety_id, usage=lot.usage, seed_class_id=lot.seed_class_id, grade=lot.grade,
               plot_id=lot.plot_id, harvest_date=lot.harvest_date, quantity=quantity,
               box_type_id=lot.box_type_id, note=lot.note)
    db.session.add(part)
    lot.quantity -= quantity
    db.session.flush()
    _log_lot(lot, "geteilt", user, quantity_delta=-quantity, from_location=old, to_location=new,
             detail=str(part.id), note=note)
    _log_lot(part, "partie_angelegt", user, quantity_delta=quantity, from_location=old, to_location=new,
             note=note)
    return part


def shift_lot(lot, direction):
    """Innerhalb der Reihe nach links (-1) oder rechts (+1) tauschen. Kein Verlaufseintrag."""
    lots = row_lots(lot.warehouse_id, lot.row)
    idx = next((i for i, other in enumerate(lots) if other.id == lot.id), None)
    if idx is None:
        return False
    j = idx + direction
    if not 0 <= j < len(lots):
        return False
    lots[idx], lots[j] = lots[j], lots[idx]
    for pos, other in enumerate(lots, start=1):
        other.position = pos
    return True


def update_lot(lot, user, variety_id, usage, seed_class_id, grade, plot_id, harvest_date, note, box_type_id):
    fields = {
        "variety": ("variety_id", variety_id), "usage": ("usage", usage),
        "seed_class": ("seed_class_id", seed_class_id), "grade": ("grade", grade or None),
        "field": ("plot_id", plot_id), "harvest_date": ("harvest_date", harvest_date),
        "note": ("note", note or None), "box_type": ("box_type_id", box_type_id),
    }
    changes = [key for key, (attr, value) in fields.items() if getattr(lot, attr) != value]
    if not changes:
        return False
    for key in changes:
        attr, value = fields[key]
        setattr(lot, attr, value)
    _log_lot(lot, "partie_geaendert", user, detail=",".join(changes))
    return True
