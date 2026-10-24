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
from .models import BOX_DEFECT, BOX_RETIRED, Box, Movement, Warehouse, utcnow

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
    if box.is_retired:
        raise StorageError(_("{code} ist ausgemustert.", code=box.code))
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


def clear_all(user, warehouse_id=None, note=None):
    """Nimmt alle Kisten aus einem Lager (oder aus allen) – z. B. vor dem Neubefüllen per Import.
    Inhalt und Verlauf bleiben erhalten. Liefert die Anzahl der ausgelagerten Kisten."""
    query = Box.query.filter(Box.warehouse_id.isnot(None))
    if warehouse_id:
        query = query.filter(Box.warehouse_id == warehouse_id)
    boxes = query.all()
    for box in boxes:
        old = location_text(box.warehouse, box.row, box.slot, box.level)
        box.warehouse_id = box.row = box.slot = box.level = None
        _log(box, "ausgelagert", user, from_location=old, fill_before=box.fill, fill_after=box.fill, note=note)
    db.session.flush()
    return len(boxes)


def ship_box(box, user, reference):
    """Verlädt eine volle oder angebrochene Kiste (Verkauf): vom Stapel nehmen, Inhalt geht an den Kunden,
    die Kiste ist danach leer. `reference` = Lieferscheinnummer. Liefert (alter Platz, nachgerückte Kisten)."""
    if box.is_empty:
        raise StorageError(_("{code} ist leer und kann nicht verladen werden.", code=box.code))
    old, shifted = None, []
    if box.is_stored:
        _lock_warehouse(box.warehouse_id)
        old = location_text(box.warehouse, box.row, box.slot, box.level)
        shifted = _take_out(box, user)
    fill_before = box.fill
    box.fill = 0
    _log(box, "verladen", user, from_location=old, fill_before=fill_before, fill_after=0, detail=reference)
    return old, shifted


def unship_box(box, fill, user, reference):
    """Nimmt das Verladen zurück (Kiste kam doch nicht mit): Füllstand wiederherstellen.
    Die Kiste ist danach nicht eingelagert und muss wieder auf einen Stapel gestellt werden."""
    if not box.is_empty:
        raise StorageError(_("{code} wurde inzwischen neu befüllt und kann nicht zurückgenommen werden.",
                             code=box.code))
    box.fill = fill
    _log(box, "zurueckgenommen", user, fill_before=0, fill_after=fill, detail=reference)


def set_fill(box, fill, user, note=None):
    if fill not in (0, 25, 50, 75, 100):
        raise StorageError(_("Ungültiger Füllstand."))
    if fill == box.fill:
        return False
    if box.is_retired and fill > 0:
        raise StorageError(_("{code} ist ausgemustert.", code=box.code))
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
    if box.is_retired and fill > 0:
        raise StorageError(_("{code} ist ausgemustert.", code=box.code))
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


# --- Ernte-Modus ---------------------------------------------------------------

def _same_contents(box, variety_id, usage, seed_class_id, grade=None):
    return (box.variety_id == variety_id and (box.effective_usage or None) == (usage or None)
            and box.seed_class_id == seed_class_id and (box.grade or None) == (grade or None))


def suggest_place(warehouse, variety_id, usage=None, seed_class_id=None, row=None, grade=None):
    """Platzvorschlag „gleiche Sorte zusammen“: ein Stapel, auf dem oben dieselbe Ware steht und noch Platz ist
    (der höchste zuerst, damit Stapel voll werden), sonst der freie Stellplatz, der am nächsten an dieser Ware
    liegt – bevorzugt in derselben Reihe (ohne solche Stapel: der erste freie). Mit `row` nur in dieser Reihe.
    (row, slot) oder None, wenn das Lager (bzw. die Reihe) voll ist."""
    stacks = stacks_overview(warehouse)
    if row is not None:
        stacks = {pos: boxes for pos, boxes in stacks.items() if pos[0] == row}
    same = [(pos, boxes) for pos, boxes in stacks.items()
            if _same_contents(boxes[-1], variety_id, usage, seed_class_id, grade)]
    room = [(pos, len(boxes)) for pos, boxes in same if len(boxes) < warehouse.max_levels]
    if room:
        return min(room, key=lambda r: (-r[1], r[0]))[0]
    rows = [row] if row is not None else range(1, warehouse.rows + 1)
    free = [(r, s) for r in rows for s in range(1, warehouse.slots + 1) if (r, s) not in stacks]
    if not free:
        return None
    if same:
        # in derselben Reihe zählt der Nachbarplatz näher als der gegenüber in der nächsten Reihe
        return min(free, key=lambda p: (min(2 * abs(p[0] - q[0]) + abs(p[1] - q[1]) for q, _b in same), p))
    return free[0]


def harvest_box(box, user, variety, plot_id, harvest_date, usage=None, seed_class_id=None, fill=100,
                warehouse=None, row=None, grade=None):
    """Ernte-Modus: leere Kiste befüllen und – mit `warehouse` – gleich auf den vorgeschlagenen Platz stellen.
    Gibt den Platz zurück (oder None, wenn nur befüllt wurde)."""
    if box.is_retired:
        raise StorageError(_("{code} ist ausgemustert.", code=box.code))
    if box.is_defect:
        raise StorageError(_("{code} ist defekt ({note}) – bitte eine andere Kiste nehmen.",
                             code=box.code, note=box.condition_note or "–"))
    if box.fill > 0:
        what = box.variety.name if box.variety else _("Inhalt")
        raise StorageError(_("{code} ist schon befüllt ({what}).", code=box.code, what=what))
    usage = usage if usage and usage != variety.usage else None      # wie bei der Sorte → leer lassen
    update_contents(box, user, variety_id=variety.id, harvest_date=harvest_date, plot_id=plot_id, grade=grade or None,
                    fill=fill, note=None, usage=usage, seed_class_id=seed_class_id)
    if warehouse is None:
        return None
    if box.is_stored:
        return box.location
    _lock_warehouse(warehouse.id)
    place = suggest_place(warehouse, variety.id, usage or variety.usage, seed_class_id, row=row, grade=grade)
    if place is None and row is not None:
        raise StorageError(_("Reihe {row} ist voll – bitte eine andere Reihe wählen.", row=warehouse.row_label(row)))
    if place is None:
        raise StorageError(_("{name} ist voll.", name=warehouse.name))
    store_box(box, warehouse.id, *place, user)
    return box.location


# --- Zustand der Kiste (defekt, ausgemustert) ----------------------------------

def report_defect(box, user, note):
    """Kiste als defekt melden – geht immer, auch befüllt und eingelagert. Im Ernte-Modus wird sie dann abgewiesen."""
    note = (note or "").strip()[:200]
    if not note:
        raise StorageError(_("Bitte kurz eintragen, was kaputt ist."))
    if box.is_retired:
        raise StorageError(_("{code} ist ausgemustert.", code=box.code))
    box.condition, box.condition_note, box.condition_at = BOX_DEFECT, note, utcnow()
    _log(box, "defekt", user, note=note)


def repair_box(box, user, note=None):
    """Wieder in Ordnung (auch eine ausgemusterte Kiste wieder in Betrieb nehmen)."""
    if not box.condition:
        raise StorageError(_("{code} ist in Ordnung.", code=box.code))
    box.condition = box.condition_note = box.condition_at = None
    _log(box, "repariert", user, note=(note or "").strip()[:200] or None)


def retire_box(box, user, note=None):
    """Ausmustern: nur leer und nicht eingelagert. Die Kiste bleibt mit ihrem Verlauf erhalten."""
    if box.is_retired:
        raise StorageError(_("{code} ist ausgemustert.", code=box.code))
    if not box.is_empty or box.is_stored:
        raise StorageError(_("Bitte {code} zuerst leeren und auslagern.", code=box.code))
    note = (note or "").strip()[:200] or box.condition_note
    box.condition, box.condition_note, box.condition_at = BOX_RETIRED, note, utcnow()
    _log(box, "ausgemustert", user, note=note)


def defect_boxes():
    """Kisten zur Reparatur, älteste Meldung zuerst."""
    return Box.query.filter(Box.condition == BOX_DEFECT).order_by(Box.condition_at, Box.code).all()


def retired_boxes():
    return Box.query.filter(Box.condition == BOX_RETIRED).order_by(Box.condition_at.desc(), Box.code).all()


# --- Kartoffelgrößen (Kaliber) ------------------------------------------------

SIZES_KEY = "potato_sizes"
MAX_SIZES = 40
_SIZE_RANGE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)\s*(mm)?$", re.IGNORECASE)
_SIZE_OPEN = re.compile(r"^([<>]|über|unter|ab|bis)\s*(\d+(?:[.,]\d+)?)\s*(mm)?$", re.IGNORECASE)


def normalize_size(text):
    """„25-28“, „25 - 28mm“ → „25–28 mm“; „>55“ → „> 55 mm“; sonst der Text, wie er ist (gekürzt)."""
    text = " ".join((text or "").split())[:50]
    m = _SIZE_RANGE.match(text)
    if m:
        return f"{m.group(1)}–{m.group(2)} mm"
    m = _SIZE_OPEN.match(text)
    if m:
        return f"{m.group(1)} {m.group(2)} mm"
    return text


def sizes():
    """Die in der Verwaltung hinterlegten Kartoffelgrößen in der eingestellten Reihenfolge."""
    from .models import Setting
    setting = db.session.get(Setting, SIZES_KEY)
    return [s for s in (setting.value or "").split("\n") if s] if setting else []


def save_sizes(text):
    """Eine Größe je Zeile (Reihenfolge bleibt, doppelte fallen weg). Committet nicht."""
    from .models import Setting
    result = []
    for line in (text or "").splitlines():
        size = normalize_size(line)
        if size and size.casefold() not in {r.casefold() for r in result}:
            result.append(size)
    if len(result) > MAX_SIZES:
        raise StorageError(_("Höchstens {n} Größen.", n=MAX_SIZES))
    Setting.put(SIZES_KEY, "\n".join(result))
    return result


# --- Lagerdauer (Ampel) ---------------------------------------------------------

AGE_DEFAULTS = {"age_warn_months": 6, "age_old_months": 9}


def age_limits():
    """Monate, ab denen eingelagerte Ware gelb bzw. rot markiert wird (Verwaltung → Kartoffellager)."""
    from .models import Setting
    result = {}
    for key, default in AGE_DEFAULTS.items():
        setting = db.session.get(Setting, key)
        try:
            result[key] = int(setting.value) if setting and setting.value else default
        except ValueError:
            result[key] = default
    return result["age_warn_months"], result["age_old_months"]


def save_age_limits(warn, old):
    from .models import Setting
    if not warn or not old or warn < 1 or old < warn or old > 60:
        raise StorageError(_("Bitte zwei Monatszahlen angeben, die zweite mindestens so groß wie die erste."))
    Setting.put("age_warn_months", str(warn))
    Setting.put("age_old_months", str(old))


def age_level(box, limits=None, today=None):
    """„old“ (rot), „warn“ (gelb) oder None – nach dem Erntedatum befüllter Kisten."""
    from datetime import date as _date
    from .machines import add_months
    if box.is_empty or not box.harvest_date:
        return None
    warn, old = limits or age_limits()
    today = today or _date.today()
    if box.harvest_date <= add_months(today, -old):
        return "old"
    if box.harvest_date <= add_months(today, -warn):
        return "warn"
    return None


def stack_age_level(boxes, limits=None, today=None):
    """Schlimmste Stufe eines Stapels (für die Karte)."""
    levels = {age_level(b, limits, today) for b in boxes}
    return "old" if "old" in levels else "warn" if "warn" in levels else None


def oldest_boxes(variety_id=None, warehouse_id=None, limit=200):
    """Eingelagerte, befüllte Kisten – älteste Ernte zuerst; dazu, ob sie oben liegen (gleich greifbar)."""
    query = Box.query.filter(Box.warehouse_id.isnot(None), Box.fill > 0, Box.harvest_date.isnot(None))
    if variety_id:
        query = query.filter(Box.variety_id == variety_id)
    if warehouse_id:
        query = query.filter(Box.warehouse_id == warehouse_id)
    boxes = query.order_by(Box.harvest_date, Box.warehouse_id, Box.row, Box.slot, Box.level.desc()).limit(limit).all()
    heights = dict(((w, r, s), n) for w, r, s, n in db.session.query(Box.warehouse_id, Box.row, Box.slot,
                                                                     func.count(Box.id))
                   .filter(Box.warehouse_id.isnot(None)).group_by(Box.warehouse_id, Box.row, Box.slot))
    limits = age_limits()
    return [{"box": b, "on_top": heights.get((b.warehouse_id, b.row, b.slot)) == b.level,
             "level": age_level(b, limits)} for b in boxes]


# --- Ernte-Tagesbericht -------------------------------------------------------------

def harvest_report(day, tz):
    """Was an einem Tag befüllt wurde (leere Kiste → befüllt; Ernte-Modus oder von Hand): Kisten und geschätzte kg
    je Sorte, je Schlag, je Reihe (wo die Kisten jetzt stehen) und je Benutzer."""
    from datetime import datetime, time, timedelta, timezone
    from .models import BoxType, Plot, User, Variety
    start = datetime.combine(day, time.min, tz).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    moves = (Movement.query.filter(Movement.action == "befuellt", Movement.created_at >= start,
                                   Movement.created_at < end, Movement.fill_after > 0)
             .filter((Movement.fill_before == 0) | Movement.fill_before.is_(None))
             .order_by(Movement.created_at).all())
    boxes = {b.id: b for b in Box.query.filter(Box.id.in_({m.box_id for m in moves}))} if moves else {}
    weights = {t.id: t.full_weight_kg for t in BoxType.query}

    def group(key_fn, label_fn):
        result = {}
        for m in moves:
            key = key_fn(m)
            entry = result.setdefault(key, {"label": label_fn(key), "boxes": 0, "kg": 0})
            entry["boxes"] += 1
            box = boxes.get(m.box_id)
            entry["kg"] += round(weights.get(box.box_type_id, 0) * (m.fill_after or 0) / 100) if box else 0
        return sorted(result.values(), key=lambda e: -e["boxes"])

    def name(model, attr="name"):
        return lambda key: (getattr(db.session.get(model, key), attr) if key else None)

    def row_of(m):
        box = boxes.get(m.box_id)
        if not box or not box.is_stored:
            return None
        return (box.warehouse.name, box.warehouse.row_label(box.row))

    varieties = group(lambda m: m.variety_id, name(Variety))
    return {
        "day": day, "count": len(moves), "kg": sum(e["kg"] for e in varieties),
        "varieties": varieties, "plots": group(lambda m: m.plot_id, name(Plot)),
        "rows": group(row_of, lambda key: f"{key[0]} · {key[1]}" if key else None),
        "users": group(lambda m: m.user_id, lambda key: db.session.get(User, key).name if key else None),
        "first": moves[0].created_at if moves else None, "last": moves[-1].created_at if moves else None,
    }
