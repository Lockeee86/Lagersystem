import io
import math
import re
from decimal import Decimal

import segno
from flask import (Blueprint, Response, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from . import storage
from .extensions import db
from .i18n import N_, gettext as _
from .labels import LABEL_FORMATS, render_labels
from .models import (MAP_VIEWS, SORT_USAGES, Box, BoxType, Lot, Movement, Plot, SeedClass, User, Variety,
                     Warehouse, format_quantity)
from .storage import StorageError
from .utils import (admin_required, back, editor_required, flash_error, form_date, form_int,
                    form_str)

bp = Blueprint("main", __name__)


def get_box_or_404(code):
    normalized = storage.normalize_code(code)
    box = Box.query.filter_by(code=normalized).first() if normalized else None
    if box is None:
        abort(404)
    return box


def get_warehouse_or_404(warehouse_id):
    warehouse = db.session.get(Warehouse, warehouse_id)
    if warehouse is None:
        abort(404)
    return warehouse


def commit_or_rollback():
    """Committet und meldet Fehler (z. B. gleichzeitige Buchung auf denselben Platz)."""
    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        flash_error(_("Der Platz wurde gerade von jemand anderem belegt. Bitte erneut versuchen."))
        return False


# --- Dashboard --------------------------------------------------------------

@bp.route("/")
@login_required
def dashboard():
    warehouses = Warehouse.query.order_by(Warehouse.name).all()
    boxes = Box.query.all()
    lots = Lot.query.filter_by(closed=False).all()

    occupancy = []
    for wh in warehouses:
        stored = [b for b in boxes if b.warehouse_id == wh.id]
        in_lots = sum((lot.quantity for lot in lots if lot.warehouse_id == wh.id), Decimal(0))
        total = len(stored) + float(in_lots)
        stacks = {(b.row, b.slot) for b in stored}
        occupancy.append({
            "warehouse": wh,
            "stored": format_quantity(total),
            "capacity": wh.capacity,
            "percent": round(100 * total / wh.capacity) if wh.capacity else 0,
            "has_boxes": bool(stored),
            "stacks": len(stacks),
            "stack_capacity": wh.rows * wh.slots,
            "full_stacks": sum(1 for s in stacks
                               if sum(1 for b in stored if (b.row, b.slot) == s) >= wh.max_levels),
        })

    per_variety = {}

    def add(variety, usage, boxes_count, kg, stored, harvest_date):
        entry = per_variety.setdefault((variety.id, usage), {
            "variety": variety, "usage": _(SORT_USAGES.get(usage, usage)), "boxes": 0, "kg": 0,
            "stored": 0, "oldest": None,
        })
        entry["boxes"] += boxes_count
        entry["kg"] += kg
        entry["stored"] += stored
        if harvest_date and (entry["oldest"] is None or harvest_date < entry["oldest"]):
            entry["oldest"] = harvest_date

    for box in boxes:
        if not box.is_empty and box.variety:
            add(box.variety, box.variety.usage, 1, box.weight_kg, 1 if box.is_stored else 0, box.harvest_date)
    for lot in lots:
        add(lot.variety, lot.usage, float(lot.quantity), lot.weight_kg, float(lot.quantity), lot.harvest_date)
    varieties = sorted(per_variety.values(), key=lambda e: -e["kg"])
    for v in varieties:
        v["not_stored"] = format_quantity(v["boxes"] - v["stored"]) if v["boxes"] > v["stored"] else None
        v["boxes_label"] = format_quantity(v["boxes"])
        v["boxes_count"] = math.ceil(v["boxes"])
    max_kg = max((v["kg"] for v in varieties), default=0)

    lot_boxes = float(sum((lot.quantity for lot in lots), Decimal(0)))
    stats = {
        "total": len(boxes),
        "filled": format_quantity(sum(1 for b in boxes if not b.is_empty) + lot_boxes),
        "filled_count": math.ceil(sum(1 for b in boxes if not b.is_empty) + lot_boxes),
        "empty": sum(1 for b in boxes if b.is_empty),
        "kg": sum(b.weight_kg for b in boxes) + sum(lot.weight_kg for lot in lots),
        "filled_not_stored": sum(1 for b in boxes if not b.is_empty and not b.is_stored),
        "lots": len(lots),
    }
    recent = Movement.query.order_by(Movement.created_at.desc()).limit(12).all()
    return render_template("dashboard.html", occupancy=occupancy, varieties=varieties,
                           max_kg=max_kg, stats=stats, recent=recent,
                           has_varieties=Variety.query.first() is not None,
                           has_plots=Plot.query.first() is not None)


# --- Lagerkarte -------------------------------------------------------------

def variety_shorts(varieties):
    """Eindeutige Kürzel je Sorte (2–3 Buchstaben), z. B. Belana → BE, Bellarosa → BL."""
    shorts, taken = {}, set()
    for v in sorted(varieties, key=lambda v: v.id):
        letters = [c for c in v.name.upper() if c.isalnum()] or ["?"]
        candidates = ["".join(letters[:2])]
        candidates += [letters[0] + c for c in letters[2:]]
        candidates += ["".join(letters[:3]), f"{letters[0]}{v.id}"]
        short = next((c for c in candidates if c not in taken), f"S{v.id}")
        taken.add(short)
        shorts[v.id] = short
    return shorts


@bp.route("/lager")
@bp.route("/lager/<int:warehouse_id>")
@login_required
def warehouse_map(warehouse_id=None):
    warehouses = Warehouse.query.order_by(Warehouse.name).all()
    if not warehouses:
        flash(_("Es ist noch kein Lager angelegt."), "warning")
        return redirect(url_for("admin.warehouses") if current_user.is_admin else url_for("main.dashboard"))
    warehouse = get_warehouse_or_404(warehouse_id) if warehouse_id else warehouses[0]

    # Gewählte Ansicht merken (pro Benutzer, auf allen Geräten)
    view = request.args.get("ansicht")
    if view in MAP_VIEWS and view != current_user.map_view:
        current_user.map_view = view
        db.session.commit()
    view = current_user.map_view if current_user.map_view in MAP_VIEWS else "karte"

    stacks = storage.stacks_overview(warehouse)
    varieties = Variety.query.order_by(Variety.name).all()
    used = {b.variety_id for boxes in stacks.values() for b in boxes if b.variety_id and not b.is_empty}
    used |= {vid for (vid,) in db.session.query(Lot.variety_id)
             .filter_by(warehouse_id=warehouse.id, closed=False).distinct()}
    shorts = variety_shorts(varieties)

    rows = []
    for r, label in warehouse.row_labels():
        row_stacks = [stacks.get((r, s), []) for s in range(1, warehouse.slots + 1)]
        mix = {}
        for boxes in row_stacks:
            for b in boxes:
                key = "—" if b.is_empty or not b.variety_id else shorts[b.variety_id]
                entry = mix.setdefault(key, {"short": key, "n": 0, "color": b.color})
                entry["n"] += 1
        count = sum(len(b) for b in row_stacks)
        rows.append({
            "row": r, "label": label, "stacks": row_stacks, "count": count,
            "free": warehouse.slots * warehouse.max_levels - count,
            "mix": sorted(mix.values(), key=lambda e: -e["n"]),
        })

    tafel = None
    if view == "tafel":
        lots = (Lot.query.filter_by(warehouse_id=warehouse.id, closed=False)
                .order_by(Lot.row, Lot.position, Lot.id).all())
        tafel = []
        for r, label in reversed(warehouse.row_labels()):   # wie an der Tafel: höchste Reihe oben
            in_row = [lot for lot in lots if lot.row == r]
            total = float(sum((lot.quantity for lot in in_row), Decimal(0)))
            tafel.append({
                "row": r, "label": label, "lots": in_row, "total": format_quantity(total),
                "free": format_quantity(max(warehouse.row_capacity - total, 0)),
                "over": total > warehouse.row_capacity,
                "percent": min(100, round(100 * total / warehouse.row_capacity)) if warehouse.row_capacity else 0,
            })

    data3d = None
    if view == "3d":
        data3d = {
            "rows": warehouse.rows, "slots": warehouse.slots, "levels": warehouse.max_levels,
            "rowLabels": [label for _r, label in warehouse.row_labels()],
            "stackUrl": url_for("main.stack_view", warehouse_id=warehouse.id, row=0, slot=0, partial=1),
            "stacks": [[r, s, [[b.color or "", b.variety_id if b.variety_id and not b.is_empty else 0] for b in boxes]]
                       for (r, s), boxes in stacks.items()],
        }

    return render_template("warehouse_map.html", warehouse=warehouse, warehouses=warehouses,
                           stacks=stacks, rows=rows, view=view, views=MAP_VIEWS, data3d=data3d, tafel=tafel,
                           shorts=shorts, varieties=[v for v in varieties if v.id in used])


@bp.route("/lager/<int:warehouse_id>/stapel/<int:row>/<int:slot>")
@login_required
def stack_view(warehouse_id, row, slot):
    warehouse = get_warehouse_or_404(warehouse_id)
    if not (1 <= row <= warehouse.rows and 1 <= slot <= warehouse.slots):
        abort(404)
    boxes = storage.stack(warehouse.id, row, slot)
    template = "partials/stack.html" if request.args.get("partial") else "stack.html"
    return render_template(template, warehouse=warehouse, row=row, slot=slot, boxes=boxes)


@bp.route("/lager/<int:warehouse_id>/stapel/<int:row>/<int:slot>/einlagern", methods=["POST"])
@editor_required
def stack_add(warehouse_id, row, slot):
    """Kiste per Code direkt auf diesen Stapel setzen (einlagern oder umlagern)."""
    warehouse = get_warehouse_or_404(warehouse_id)
    code = storage.normalize_code(request.form.get("code"))
    box = Box.query.filter_by(code=code).first() if code else None
    if box is None:
        flash_error(_("Kiste nicht gefunden."))
        return back("main.stack_view", warehouse_id=warehouse.id, row=row, slot=slot)
    try:
        if box.is_stored:
            storage.move_box(box, warehouse.id, row, slot, current_user)
        else:
            storage.store_box(box, warehouse.id, row, slot, current_user)
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return back("main.stack_view", warehouse_id=warehouse.id, row=row, slot=slot)
    if commit_or_rollback():
        flash(_("{code} steht jetzt auf {location}.", code=box.code, location=box.location), "success")
    return back("main.stack_view", warehouse_id=warehouse.id, row=row, slot=slot)


# --- Kisten -----------------------------------------------------------------

BOX_FILTERS = {
    "eingelagert": (N_("Eingelagert"), Box.warehouse_id.isnot(None)),
    "nicht_eingelagert": (N_("Nicht eingelagert"), Box.warehouse_id.is_(None)),
    "befuellt": (N_("Befüllt"), Box.fill > 0),
    "leer": (N_("Leer"), Box.fill == 0),
    "befuellt_nicht_eingelagert": (N_("Befüllt, nicht eingelagert"),
                                   (Box.fill > 0) & Box.warehouse_id.is_(None)),
}


@bp.route("/kisten")
@login_required
def boxes():
    query = Box.query
    q = request.args.get("q", "").strip()
    if q:
        like = f"%{q}%"
        query = query.outerjoin(Plot).filter(or_(Box.code.ilike(like), Plot.name.ilike(like),
                                                 Box.note.ilike(like), Box.grade.ilike(like)))
    variety_id = request.args.get("sorte", type=int)
    if variety_id:
        query = query.filter(Box.variety_id == variety_id)
    plot_id = request.args.get("schlag", type=int)
    if plot_id:
        query = query.filter(Box.plot_id == plot_id)
    status = request.args.get("status", "")
    if status in BOX_FILTERS:
        query = query.filter(BOX_FILTERS[status][1])
    warehouse_id = request.args.get("lager", type=int)
    if warehouse_id:
        query = query.filter(Box.warehouse_id == warehouse_id)

    sort = request.args.get("sort", "code")
    order = {
        "code": [Box.code],
        "ernte": [Box.harvest_date.asc().nulls_last(), Box.code],
        "platz": [Box.warehouse_id, Box.row, Box.slot, Box.level],
        "geaendert": [Box.updated_at.desc()],
    }.get(sort, [Box.code])
    page = query.order_by(*order).paginate(per_page=100, error_out=False)
    return render_template(
        "boxes.html", page=page, filters=BOX_FILTERS,
        varieties=Variety.query.order_by(Variety.name).all(),
        plots=Plot.query.order_by(Plot.name).all(),
        warehouses=Warehouse.query.order_by(Warehouse.name).all(),
        box_types=BoxType.query.order_by(BoxType.name).all(),
        args=request.args,
    )


@bp.route("/kisten/neu", methods=["POST"])
@admin_required
def boxes_create():
    count = form_int("count", minimum=1, maximum=1000)
    box_type = db.session.get(BoxType, form_int("box_type_id", 0))
    if not count or not box_type:
        flash_error(_("Bitte Anzahl (1–1000) und Kistentyp angeben."))
        return redirect(url_for("main.boxes"))
    try:
        created = storage.create_boxes(count, box_type.id, current_user)
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("main.boxes"))
    db.session.commit()
    first, last = created[0].code, created[-1].code
    flash(_("{n} Kisten angelegt ({first} bis {last}).", n=count, first=first, last=last), "success")
    return redirect(url_for("main.labels", von=first, bis=last))


@bp.route("/kiste/<code>")
@login_required
def box_detail(code):
    box = get_box_or_404(code)
    if box.code != code:
        return redirect(url_for("main.box_detail", code=box.code))
    return render_template(
        "box_detail.html", box=box,
        above=storage.boxes_above(box),
        movements=box.movements.limit(50).all(),
        varieties=Variety.query.filter(or_(Variety.active.is_(True), Variety.id == box.variety_id))
        .order_by(Variety.name).all(),
        warehouses=Warehouse.query.order_by(Warehouse.name).all(),
        box_types=BoxType.query.order_by(BoxType.name).all(),
        plots=Plot.query.filter(or_(Plot.active.is_(True), Plot.id == box.plot_id))
        .order_by(Plot.name).all(),
    )


@bp.route("/kiste/<code>/inhalt", methods=["POST"])
@editor_required
def box_contents(code):
    box = get_box_or_404(code)
    variety_id = form_int("variety_id")
    if variety_id and not db.session.get(Variety, variety_id):
        variety_id = None
    box_type_id = form_int("box_type_id")
    if box_type_id and not db.session.get(BoxType, box_type_id):
        box_type_id = None
    plot_id = form_int("plot_id")
    if plot_id and not db.session.get(Plot, plot_id):
        plot_id = None
    try:
        changed = storage.update_contents(
            box, current_user,
            variety_id=variety_id,
            harvest_date=form_date("harvest_date"),
            plot_id=plot_id,
            grade=form_str("grade", 50),
            fill=form_int("fill", box.fill),
            note=form_str("note", 2000),
            box_type_id=box_type_id,
        )
    except StorageError as e:
        flash_error(str(e))
        return redirect(url_for("main.box_detail", code=box.code))
    db.session.commit()
    if changed:
        flash(_("{code} gespeichert.", code=box.code), "success")
    return redirect(url_for("main.box_detail", code=box.code))


@bp.route("/kiste/<code>/fuellstand", methods=["POST"])
@editor_required
def box_fill(code):
    box = get_box_or_404(code)
    try:
        if storage.set_fill(box, form_int("fill", -1), current_user):
            db.session.commit()
            flash(_("{code}: Füllstand {fill}.", code=box.code, fill=box.fill_label), "success")
    except StorageError as e:
        flash_error(str(e))
    return redirect(url_for("main.box_detail", code=box.code))


def _target_from_form():
    warehouse_id = form_int("warehouse_id")
    row = form_int("row")
    slot = form_int("slot")
    if not (warehouse_id and row and slot):
        raise StorageError("Bitte Lager, Reihe und Stellplatz angeben.")
    return warehouse_id, row, slot


@bp.route("/kiste/<code>/einlagern", methods=["POST"])
@editor_required
def box_store(code):
    box = get_box_or_404(code)
    try:
        target = _target_from_form()
        if box.is_stored:
            storage.move_box(box, *target, current_user, note=form_str("note", 500) or None)
        else:
            storage.store_box(box, *target, current_user, note=form_str("note", 500) or None)
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("main.box_detail", code=box.code))
    if commit_or_rollback():
        flash(_("{code} steht jetzt auf {location}.", code=box.code, location=box.location), "success")
    return redirect(url_for("main.box_detail", code=box.code))


@bp.route("/kiste/<code>/auslagern", methods=["POST"])
@editor_required
def box_remove(code):
    box = get_box_or_404(code)
    try:
        shifted = storage.remove_box(box, current_user, empty=bool(request.form.get("empty")),
                                     note=form_str("note", 500) or None)
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("main.box_detail", code=box.code))
    if commit_or_rollback():
        msg = _("{code} ausgelagert.", code=box.code)
        if shifted:
            msg += " " + _("Nachgerückt: {codes}.", codes=", ".join(b.code for b in shifted))
        flash(msg, "success")
    return redirect(url_for("main.box_detail", code=box.code))


@bp.route("/kiste/<code>/loeschen", methods=["POST"])
@admin_required
def box_delete(code):
    box = get_box_or_404(code)
    if box.is_stored:
        flash_error(_("Eingelagerte Kisten können nicht gelöscht werden. Bitte zuerst auslagern."))
        return redirect(url_for("main.box_detail", code=box.code))
    db.session.delete(box)
    db.session.commit()
    flash(_("{code} wurde gelöscht.", code=box.code), "success")
    return redirect(url_for("main.boxes"))


@bp.route("/kiste/<code>/qr.svg")
@login_required
def box_qr(code):
    box = get_box_or_404(code)
    buf = io.BytesIO()
    segno.make(box.code, error="m").save(buf, kind="svg", scale=8, border=2, dark="#000", light="#fff")
    return Response(buf.getvalue(), mimetype="image/svg+xml",
                    headers={"Cache-Control": "private, max-age=86400"})


# --- Scan -------------------------------------------------------------------

@bp.route("/scan")
@login_required
def scan():
    raw = request.args.get("code")
    if raw:
        code = storage.normalize_code(raw)
        if code and Box.query.filter_by(code=code).first():
            return redirect(url_for("main.box_detail", code=code))
        flash_error(_("Keine Kiste mit dem Code „{code}“ gefunden.", code=raw[:40]))
    return render_template("scan.html")


# --- Partien ----------------------------------------------------------------

def get_lot_or_404(lot_id):
    lot = db.session.get(Lot, lot_id)
    if lot is None:
        abort(404)
    return lot


def _lot_form_choices(lot=None):
    """Auswahllisten fürs Partie-Formular (inaktive Einträge nur, wenn die Partie sie schon nutzt)."""
    def active_or_used(model, used_id):
        return (model.query.filter(or_(model.active.is_(True), model.id == used_id))
                .order_by(*([model.position, model.name] if model is SeedClass else [model.name])).all())
    return {
        "varieties": active_or_used(Variety, lot.variety_id if lot else None),
        "plots": active_or_used(Plot, lot.plot_id if lot else None),
        "seed_classes": active_or_used(SeedClass, lot.seed_class_id if lot else None),
        "box_types": BoxType.query.order_by(BoxType.name).all(),
        "warehouses": Warehouse.query.order_by(Warehouse.name).all(),
        "usages": SORT_USAGES,
    }


def _lot_fields_from_form():
    """Gemeinsame Felder für Anlegen und Bearbeiten; wirft StorageError bei ungültiger Eingabe."""
    variety = db.session.get(Variety, form_int("variety_id", 0))
    if variety is None:
        raise StorageError(_("Bitte eine Sorte wählen."))
    usage = form_str("usage", 20)
    seed_class_id = form_int("seed_class_id")
    if seed_class_id and not db.session.get(SeedClass, seed_class_id):
        seed_class_id = None
    plot_id = form_int("plot_id")
    if plot_id and not db.session.get(Plot, plot_id):
        plot_id = None
    box_type = db.session.get(BoxType, form_int("box_type_id", 0)) or BoxType.query.order_by(BoxType.id).first()
    if box_type is None:
        raise StorageError(_("Es ist noch kein Kistentyp angelegt."))
    return {
        "variety_id": variety.id,
        "usage": usage if usage in SORT_USAGES else variety.usage,
        "seed_class_id": seed_class_id,
        "grade": form_str("grade", 50) or None,
        "plot_id": plot_id,
        "harvest_date": form_date("harvest_date"),
        "note": form_str("note", 2000) or None,
        "box_type_id": box_type.id,
    }


@bp.route("/partie/<int:lot_id>")
@login_required
def lot_detail(lot_id):
    lot = get_lot_or_404(lot_id)
    neighbours = storage.row_lots(lot.warehouse_id, lot.row)
    index = next((i for i, other in enumerate(neighbours) if other.id == lot.id), None)
    return render_template(
        "lot_detail.html", lot=lot, movements=lot.movements.limit(50).all(),
        can_left=index is not None and index > 0,
        can_right=index is not None and index < len(neighbours) - 1,
        **_lot_form_choices(lot),
    )


@bp.route("/partie/neu", methods=["GET", "POST"])
@editor_required
def lot_create():
    choices = _lot_form_choices()
    if not choices["warehouses"]:
        flash(_("Es ist noch kein Lager angelegt."), "warning")
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        try:
            fields = _lot_fields_from_form()
            warehouse = db.session.get(Warehouse, form_int("warehouse_id", 0)) or choices["warehouses"][0]
            row = form_int("row")
            if row is None:
                raise StorageError(_("Bitte eine Reihe wählen."))
            lot = storage.create_lot(current_user, warehouse.id, row,
                                     quantity=storage.parse_quantity(request.form.get("quantity")), **fields)
        except StorageError as e:
            db.session.rollback()
            flash_error(str(e))
            return render_template("lot_form.html", lot=None, form=request.form, **choices)
        db.session.commit()
        flash(_("Partie {title} in {location} angelegt.", title=lot.title, location=lot.location), "success")
        return redirect(url_for("main.warehouse_map", warehouse_id=lot.warehouse_id, ansicht="tafel"))
    form = {"warehouse_id": request.args.get("lager", ""), "row": request.args.get("reihe", "")}
    return render_template("lot_form.html", lot=None, form=form, **choices)


@bp.route("/partie/<int:lot_id>/bearbeiten", methods=["GET", "POST"])
@editor_required
def lot_edit(lot_id):
    lot = get_lot_or_404(lot_id)
    choices = _lot_form_choices(lot)
    if request.method == "POST":
        try:
            changed = storage.update_lot(lot, current_user, **_lot_fields_from_form())
        except StorageError as e:
            db.session.rollback()
            flash_error(str(e))
            return render_template("lot_form.html", lot=lot, form=request.form, **choices)
        db.session.commit()
        if changed:
            flash(_("Partie {title} gespeichert.", title=lot.title), "success")
        return redirect(url_for("main.lot_detail", lot_id=lot.id))
    form = {
        "variety_id": lot.variety_id, "usage": lot.usage, "seed_class_id": lot.seed_class_id or "",
        "grade": lot.grade or "", "plot_id": lot.plot_id or "",
        "harvest_date": lot.harvest_date.isoformat() if lot.harvest_date else "",
        "note": lot.note or "", "box_type_id": lot.box_type_id,
    }
    return render_template("lot_form.html", lot=lot, form=form, **choices)


def _lot_action(lot_id, action):
    lot = get_lot_or_404(lot_id)
    quantity = storage.parse_quantity(request.form.get("quantity"))
    note = form_str("note", 500) or None
    try:
        message = action(lot, quantity, note)
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("main.lot_detail", lot_id=lot.id))
    db.session.commit()
    if message:
        flash(message, "success")
    return redirect(url_for("main.lot_detail", lot_id=lot.id))


@bp.route("/partie/<int:lot_id>/entnehmen", methods=["POST"])
@editor_required
def lot_take(lot_id):
    def action(lot, quantity, note):
        storage.take_from_lot(lot, quantity, current_user, note)
        return _("{n} Kisten entnommen. Bestand: {left}.", n=format_quantity(quantity), left=lot.quantity_label)
    return _lot_action(lot_id, action)


@bp.route("/partie/<int:lot_id>/zugang", methods=["POST"])
@editor_required
def lot_add(lot_id):
    def action(lot, quantity, note):
        storage.add_to_lot(lot, quantity, current_user, note)
        return _("{n} Kisten hinzugefügt. Bestand: {left}.", n=format_quantity(quantity), left=lot.quantity_label)
    return _lot_action(lot_id, action)


@bp.route("/partie/<int:lot_id>/korrektur", methods=["POST"])
@editor_required
def lot_correct(lot_id):
    def action(lot, quantity, note):
        if storage.correct_lot(lot, quantity, current_user, note):
            return _("Bestand auf {n} Kisten gesetzt.", n=lot.quantity_label)
        return None
    return _lot_action(lot_id, action)


@bp.route("/partie/<int:lot_id>/umsetzen", methods=["POST"])
@editor_required
def lot_move(lot_id):
    def action(lot, quantity, note):
        row = form_int("row")
        if row is None:
            raise StorageError(_("Bitte eine Reihe wählen."))
        target = storage.move_lot(lot, form_int("warehouse_id", lot.warehouse_id), row, current_user,
                                  quantity=quantity, note=note)
        return _("{n} Kisten stehen jetzt in {location}.", n=target.quantity_label, location=target.location)
    return _lot_action(lot_id, action)


@bp.route("/partie/<int:lot_id>/verschieben", methods=["POST"])
@editor_required
def lot_shift(lot_id):
    lot = get_lot_or_404(lot_id)
    if storage.shift_lot(lot, -1 if request.form.get("richtung") == "links" else 1):
        db.session.commit()
    return back("main.lot_detail", lot_id=lot.id)


# --- Verlauf ----------------------------------------------------------------

@bp.route("/verlauf")
@login_required
def history():
    query = Movement.query.outerjoin(Box, Movement.box_id == Box.id).outerjoin(Variety, Movement.variety_id == Variety.id)
    q = request.args.get("q", "").strip()
    if q:
        code = storage.normalize_code(q) if re.match(r"^[Kk]-?\d+$", q) else None
        query = query.filter(Box.code == code) if code else query.filter(
            or_(Movement.from_location.ilike(f"%{q}%"), Movement.to_location.ilike(f"%{q}%"),
                Variety.name.ilike(f"%{q}%")))
    lot_id = request.args.get("partie", type=int)
    if lot_id:
        query = query.filter(Movement.lot_id == lot_id)
    action = request.args.get("aktion")
    if action:
        query = query.filter(Movement.action == action)
    user_id = request.args.get("benutzer", type=int)
    if user_id:
        query = query.filter(Movement.user_id == user_id)
    page = query.order_by(Movement.created_at.desc(), Movement.id.desc()).paginate(
        per_page=100, error_out=False)
    from .models import MOVEMENT_ACTIONS
    return render_template("history.html", page=page, actions=MOVEMENT_ACTIONS,
                           users=User.query.order_by(User.username).all(), args=request.args)


# --- Etiketten --------------------------------------------------------------

@bp.route("/etiketten")
@admin_required
def labels():
    return render_template("labels.html", formats=LABEL_FORMATS,
                           von=request.args.get("von", ""), bis=request.args.get("bis", ""),
                           total=Box.query.count())


@bp.route("/etiketten.pdf", methods=["POST"])
@admin_required
def labels_pdf():
    start = storage.normalize_code(request.form.get("von"))
    end = storage.normalize_code(request.form.get("bis"))
    query = Box.query
    if start:
        query = query.filter(Box.code >= start)
    if end:
        query = query.filter(Box.code <= end)
    codes = [b.code for b in query.order_by(Box.code).limit(2000)]
    if not codes:
        flash_error(_("Im gewählten Bereich gibt es keine Kisten."))
        return redirect(url_for("main.labels"))
    fmt = request.form.get("format", "3x8")
    if fmt not in LABEL_FORMATS:
        fmt = "3x8"
    pdf = render_labels(codes, fmt, skip=form_int("skip", 0, minimum=0, maximum=100))
    filename = f"etiketten_{codes[0]}_{codes[-1]}.pdf"
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})
