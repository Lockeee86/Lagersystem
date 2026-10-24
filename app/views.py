import io
from datetime import date, timedelta

import segno
from flask import (Blueprint, Response, abort, flash, make_response, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from . import agenda, backup, bulk, fuel, machines, navigation, purchases, sales, search, storage, tasks, tiles, weather
from .extensions import db
from .i18n import N_, gettext as _, ngettext
from .labels import LABEL_FORMATS, render_labels
from .models import (MAP_VIEWS, SORT_USAGES, Box, BoxType, BulkStore, Machine, Movement, Plot, SeedClass, User, Variety,
                     Warehouse)
from .storage import StorageError
from .utils import (admin_required, back, editor_required, flash_error, form_date, form_int,
                    fmt_date, fmt_kg, fmt_num, form_str)

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

WEEK_COOKIE = "woche"


def _week(days=7, mine_only=False):
    """Wochenübersicht für die Startseite: Termine, Feiertage, Wartungen, Lieferungen (Aufgaben haben eine eigene Karte).
    `mine_only`: nur eigene Termine (wie „Nur meine“ im Kalender), Feiertage bleiben."""
    today = date.today()
    items, _undated = agenda.events(today, today + timedelta(days=days - 1), today, current_user, mine_only=mine_only)
    result = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        # Mehrtägiges nur am ersten Tag – und bei „Heute“, solange es läuft
        entries = [e for e in items if e["kind"] != "aufgabe"
                   and (e["date"] == day or (offset == 0 and e["date"] < day <= e["end"]))]
        result.append({"date": day, "offset": offset, "events": entries})
    return result


def _tile_info(key):
    """Zusatzangaben für Kacheln, die die Übersicht sonst nicht berechnet (nur bei Bedarf)."""
    if key == "tanken" or key == "tankbuch":
        return fuel.status()
    if key == "kalender":
        today = date.today()
        items = agenda.events(today, today + timedelta(days=14), today, current_user, mine_only=True)[0]
        return sum(1 for e in items if e["kind"] != "feiertag")
    if key == "lieferanten":
        return len(purchases.suppliers())
    if key == "pflanzenschutz":
        from . import fielddoc
        return sum(1 for r in fielddoc.protection_overview(date.today().year)[0] if r["running"])
    if key == "kunden":
        from .models import Customer
        return Customer.query.count()
    return None


def _important(due_now, faults, due_maintenance, deliveries, arrived, backup_alert, today, unclaimed=()):
    """„Jetzt wichtig“ auf der Startseite: eine Liste statt einzelner Karten, Dringendes zuerst.
    Je Eintrag {level: danger|warning|success, title, sub, url}. Aufgaben stehen in einem eigenen Block darunter;
    Störungen, zu denen es schon eine fällige Aufgabe gibt, erscheinen nur dort. `unclaimed`: Bestellwünsche, die
    noch niemand übernommen hat (Besteller)."""
    items = []
    if backup_alert:
        items.append({"level": "danger", "title": _("Datensicherung"),
                      "sub": _("fehlgeschlagen") if backup_alert == "failed" else _("seit über einem Tag keine"),
                      "url": url_for("admin.backups")})
    task_faults = {t.machine_event_id for t in due_now if t.machine_event_id}
    for e in faults:
        if e.id not in task_faults:
            items.append({"level": "danger", "title": e.machine.name, "sub": _("Störung") + (f" · {e.text}" if e.text else ""),
                          "url": url_for("machines.machine_detail", machine_id=e.machine_id) + "#logbuch"})
    for task, status in due_maintenance:
        if status["state"] == "overdue":
            items.append({"level": "warning", "title": f"{task.machine.name}: {task.name}", "sub": _("Wartung überfällig"),
                          "url": url_for("machines.machine_detail", machine_id=task.machine_id) + "#wartungen"})
    for d in deliveries:
        items.append({"level": "warning", "title": _("{number} verladen", number=d.number),
                      "sub": f"{d.customer.name} · " + ngettext("{n} Kiste", "{n} Kisten", d.box_count),
                      "url": url_for("sales.delivery_detail", delivery_id=d.id)})
    for r in unclaimed:
        items.append({"level": "warning", "title": r.title, "sub": _("Bestellwunsch · noch frei"),
                      "url": url_for("purchases.purchase_detail", req_id=r.id)})
    for r in arrived:
        items.append({"level": "success", "title": _("{title} ist da", title=r.title), "sub": _("Bestellung"),
                      "url": url_for("purchases.purchase_detail", req_id=r.id)})
    order = {"danger": 0, "warning": 1, "success": 2}
    return sorted(items, key=lambda i: order[i["level"]])


def _stock_overview(recent_limit=12):
    """Lagerzahlen für Startseite (PC) und Lager-Bereichsseite (Handy): Belegung je Lager, Bestand je Sorte,
    Kennzahlen, letzte Bewegungen."""
    warehouses = Warehouse.query.order_by(Warehouse.name).all()
    boxes = Box.query.all()

    occupancy = []
    for wh in warehouses:
        stored = [b for b in boxes if b.warehouse_id == wh.id]
        stacks = {(b.row, b.slot) for b in stored}
        occupancy.append({
            "warehouse": wh,
            "stored": len(stored),
            "capacity": wh.capacity,
            "percent": round(100 * len(stored) / wh.capacity) if wh.capacity else 0,
            "stacks": len(stacks),
            "stack_capacity": wh.rows * wh.slots,
            "full_stacks": sum(1 for s in stacks
                               if sum(1 for b in stored if (b.row, b.slot) == s) >= wh.max_levels),
        })

    per_variety = {}
    for box in boxes:
        if box.is_empty or not box.variety:
            continue
        entry = per_variety.setdefault(box.variety_id, {
            "variety": box.variety, "boxes": 0, "kg": 0, "stored": 0, "oldest": None,
        })
        entry["boxes"] += 1
        entry["kg"] += box.weight_kg
        entry["stored"] += 1 if box.is_stored else 0
        if box.harvest_date and (entry["oldest"] is None or box.harvest_date < entry["oldest"]):
            entry["oldest"] = box.harvest_date
    varieties = sorted(per_variety.values(), key=lambda e: -e["kg"])
    max_kg = max((v["kg"] for v in varieties), default=0)

    stats = {
        "total": len(boxes),
        "filled": sum(1 for b in boxes if not b.is_empty),
        "empty": sum(1 for b in boxes if b.is_empty),
        "kg": sum(b.weight_kg for b in boxes),
        "filled_not_stored": sum(1 for b in boxes if not b.is_empty and not b.is_stored),
    }
    recent = Movement.query.order_by(Movement.created_at.desc()).limit(recent_limit).all()
    return {"occupancy": occupancy, "varieties": varieties, "max_kg": max_kg, "stats": stats, "recent": recent,
            "bulk_wares": bulk.overview()[1]}


@bp.route("/")
@login_required
def dashboard():
    stock = _stock_overview()
    choice = request.args.get(WEEK_COOKIE)
    if choice not in ("alle", "meine"):
        choice = None
    week_mine = (choice or request.cookies.get(WEEK_COOKIE)) == "meine"
    today = date.today()
    due_now = tasks.due_now(None if current_user.is_admin else current_user)
    open_deliveries, due_maintenance, faults = sales.open_deliveries(), machines.due_tasks(), machines.open_faults()
    arrived = purchases.arrived_for(current_user)
    backup_alert = backup.needs_attention() if current_user.is_admin else None
    unclaimed = [r for r in purchases.open_requests(current_user) if not r.assignee] if current_user.is_buyer else []
    important = _important(due_now, faults, due_maintenance, open_deliveries if current_user.can_edit else [],
                           arrived, backup_alert, today, unclaimed)
    response = make_response(render_template(
        "dashboard.html", week_mine=week_mine, **stock, important=important, open_faults=faults, due_tasks_now=due_now,
        weather=weather.get_weather(), show_blight=weather.blight_in_season(), today=today,
        tank=fuel.status() if current_user.can_edit else None,
        quick_tiles=tiles.quick(tiles.for_user(current_user)), tile_catalog=tiles.CATALOG,
        week=_week(mine_only=week_mine),
        has_varieties=Variety.query.first() is not None,
        has_plots=Plot.query.first() is not None))
    if choice:
        response.set_cookie(WEEK_COOKIE, choice, max_age=365 * 24 * 3600, samesite="Lax", httponly=True,
                            secure=request.is_secure)
    return response


def _status(endpoint):
    """Kurzer Stand je Unterpunkt für die Bereichsseiten am Handy: (Text, Warnung?) oder (None, False)."""
    if endpoint == "main.warehouse_map":
        occ = _stock_overview(0)["occupancy"]
        return (_("{n} % belegt", n=occ[0]["percent"]) if occ else None), False
    if endpoint == "main.boxes":
        n = Box.query.filter(Box.fill > 0).count()
        return ngettext("{n} befüllte Kiste", "{n} befüllte Kisten", n), False
    if endpoint == "bulk.store_list":
        rows, per_ware = bulk.overview()
        if not rows:
            return _("Getreide, Raps … in Boxen und Big Bags"), False
        return (" · ".join(f"{ware} {fmt_kg(kg)}" for ware, kg in list(per_ware.items())[:2]) or _("leer")), False
    if endpoint == "main.history":
        return _("Ein- und Auslagern, Änderungen"), False
    if endpoint == "main.labels":
        return _("QR-Etiketten drucken"), False
    if endpoint == "fields.field_map":
        p = _plot_stats()
        return (f"{p['count']} · {fmt_num(p['ha'], 0)} ha" if p["ha"] else str(p["count"])), False
    if endpoint == "fields.rotation_page":
        return _("Plan und Anbaupausen"), False
    if endpoint == "fields.protection":
        n = _tile_info("pflanzenschutz")
        return (ngettext("{n} Wartezeit läuft", "{n} Wartezeiten laufen", n) if n else _("keine Wartezeit")), bool(n)
    if endpoint == "machines.machine_list":
        faults, due = machines.open_faults(), machines.due_tasks()
        if faults:
            return ngettext("{n} Störung", "{n} Störungen", len(faults)), True
        if due:
            return ngettext("{n} Wartung fällig", "{n} Wartungen fällig", len(due)), True
        return _("alles in Ordnung"), False
    if endpoint == "fuel.tank":
        tank = fuel.status()
        return ((_("{liters} l im Tank", liters=fmt_num(tank["liters"], 0)) if tank["has_entries"]
                 else _("Liter eintragen")), bool(tank.get("low")))
    if endpoint == "main.weather_page":
        w = weather.get_weather()
        if not w or w.get("error") or not w.get("current"):
            return None, False
        text = f"{fmt_num(w['current']['temp'], 0)} °C"
        if w.get("frost"):
            text += " · " + (_("Frost") if w["frost"]["level"] == "frost" else _("Bodenfrost"))
        return text, bool(w.get("frost"))
    if endpoint == "tasks.task_list":
        n = len(tasks.due_now(current_user)) if current_user.can_edit else 0
        return (ngettext("{n} für heute", "{n} für heute", n) if n else _("nichts fällig")), bool(n)
    if endpoint == "purchases.purchase_list":
        arrived = purchases.arrived_for(current_user)
        if arrived:
            return ngettext("{n} ist da", "{n} sind da", len(arrived)), True
        if current_user.is_buyer:
            return ngettext("{n} offen", "{n} offen", len(purchases.open_requests(current_user))), False
        return _("Teil anfragen"), False
    if endpoint == "agenda.month":
        n = _tile_info("kalender")
        return ngettext("{n} Termin in 14 Tagen", "{n} Termine in 14 Tagen", n), False
    if endpoint == "main.search":
        return _("Kisten, Schläge, Maschinen …"), False
    if endpoint == "sales.deliveries":
        n = len(sales.open_deliveries())
        return (ngettext("{n} Lieferschein offen", "{n} Lieferscheine offen", n) if n else None), bool(n)
    return None, False


@bp.route("/lager/uebersicht")
@login_required
def storage_home():
    """Lager-Übersicht (Handy und PC): Kartoffellager (Hallen, Kisten, Bestand je Sorte) und Schüttlager (Lagerorte)."""
    stock = _stock_overview(8)
    bulk_rows, bulk_wares = bulk.overview()
    return render_template("storage_home.html", **stock, bulk_rows=bulk_rows, bulk_total=sum(bulk_wares.values()))


@bp.route("/bereich/<key>")
@login_required
def area(key):
    """Bereichsseite fürs Handy (untere Leiste): Unterpunkte als große Zeilen mit Stand."""
    if key == "lager":
        return redirect(url_for("main.storage_home"))
    if key not in navigation.HUBS:
        abort(404)
    groups = []
    for group in navigation.hub(key, current_user):
        rows = []
        for it in group["items"]:
            text, warn = _status(it["endpoint"])
            rows.append({**it, "url": url_for(it["endpoint"]), "status": text, "warn": warn})
        groups.append({"label": group["label"], "rows": rows})
    return render_template("area.html", key=key, title=navigation.HUBS[key], groups=groups,
                           tank=fuel.status() if key == "feld" and current_user.can_edit else None)


@bp.route("/startseite/kacheln", methods=["GET", "POST"])
@login_required
def tile_settings():
    """Eigene Kachel-Auswahl für die Übersicht (Abweichung von der Voreinstellung der Rolle)."""
    if request.method == "POST":
        if request.form.get("reset"):
            tiles.reset_user(current_user)
            flash(_("Kacheln auf die Voreinstellung zurückgesetzt."), "success")
        else:
            tiles.save_user(current_user, request.form.getlist("tile"))
            flash(_("Kacheln gespeichert."), "success")
        db.session.commit()
        return redirect(url_for("main.dashboard"))
    return render_template("tiles.html", catalog=tiles.CATALOG, allowed=tiles.allowed(current_user),
                           chosen=tiles.for_user(current_user), defaults=tiles.role_default(current_user.role),
                           customized=bool(current_user.tiles_add or current_user.tiles_hide))


# --- Wetter -----------------------------------------------------------------

@bp.route("/wetter")
@login_required
def weather_page():
    data = weather.get_weather()
    return render_template("weather.html", weather=data, icons=weather.ICONS, rain_year=weather.rain_year(data),
                           months=weather.MONTHS)


# --- Lagerkarte -------------------------------------------------------------

def _plot_stats():
    from sqlalchemy import func
    count, area = db.session.query(func.count(Plot.id), func.sum(Plot.area_ha)).filter(Plot.active.is_(True)).one()
    return {"count": count, "ha": area}


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
    shorts = variety_shorts(varieties)

    rows = []
    for r, label in warehouse.display_rows():
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
                           stacks=stacks, rows=rows, view=view, views=MAP_VIEWS, data3d=data3d,
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
        seed_classes=SeedClass.query.filter(or_(SeedClass.active.is_(True), SeedClass.id == box.seed_class_id))
        .order_by(SeedClass.position, SeedClass.name).all(),
        usages=SORT_USAGES,
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
    seed_class_id = form_int("seed_class_id")
    if seed_class_id and not db.session.get(SeedClass, seed_class_id):
        seed_class_id = None
    usage = form_str("usage", 20)
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
            usage=usage if usage in SORT_USAGES else None,
            seed_class_id=seed_class_id,
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
    if box.delivery_items.first():
        flash_error(_("{code} steht auf einem Lieferschein und kann nicht gelöscht werden.", code=box.code))
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
        machine_code = machines.normalize_code(raw)
        machine = Machine.query.filter_by(code=machine_code).first() if machine_code else None
        if machine:
            return redirect(url_for("machines.machine_detail", machine_id=machine.id))
        store_code = bulk.normalize_code(raw)
        store = BulkStore.query.filter_by(code=store_code).first() if store_code else None
        if store:
            # am Lagerort gescannt: gleich das Einlagern öffnen (Betrachter sehen nur den Bestand)
            return redirect(url_for("bulk.store_detail", store_id=store.id,
                                    einlagern=1 if current_user.can_edit else None))
        code = storage.normalize_code(raw)
        if code and Box.query.filter_by(code=code).first():
            return redirect(url_for("main.box_detail", code=code))
        flash_error(_("Keine Kiste mit dem Code „{code}“ gefunden.", code=raw[:40]))
    return render_template("scan.html")


# --- Suche ------------------------------------------------------------------

@bp.route("/suche", endpoint="search")
@login_required
def search_page():
    q = request.args.get("q", "").strip()[:100]
    if q:
        hit = search.direct_hit(q)
        if hit:
            kind, obj = hit
            if kind == "box":
                return redirect(url_for("main.box_detail", code=obj.code))
            if kind == "machine":
                return redirect(url_for("machines.machine_detail", machine_id=obj.id))
            return redirect(url_for("sales.delivery_detail", delivery_id=obj.id))
    results = search.search(q, current_user) if q else None
    return render_template("search.html", q=q, results=results,
                           total=sum(len(v) for v in results.values()) if results else 0)


# --- Verlauf ----------------------------------------------------------------

@bp.route("/verlauf")
@login_required
def history():
    query = Movement.query.join(Box)
    q = request.args.get("q", "").strip()
    if q:
        code = storage.normalize_code(q)
        query = query.filter(Box.code == code) if code else query.filter(
            or_(Movement.from_location.ilike(f"%{q}%"), Movement.to_location.ilike(f"%{q}%")))
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
