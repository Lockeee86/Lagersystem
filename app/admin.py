import os
import re
import secrets
import time
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   send_file, session, url_for)
from flask_login import current_user
from sqlalchemy import func, or_

from . import aerial, agenda, audit, backup, branding, fielddoc, fields, sites, tiles, transfer, weather
from .auth import validate_password
from .extensions import db
from .i18n import LANGUAGES, gettext as _, ngettext
from .models import (FIELD_MEASURE_KINDS, ROLE_ADMIN, ROLES, SORT_USAGES, Box, BoxType, Movement, Plot, SeedClass,
                     Setting, User, Variety, Warehouse)
from .storage import StorageError
from .utils import admin_required, flash_error, form_int, form_str

bp = Blueprint("admin", __name__)

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@bp.before_request
@admin_required
def _only_admins():
    return None


def _get(model, obj_id):
    obj = db.session.get(model, obj_id)
    if obj is None:
        abort(404)
    return obj


def _name_taken(model, name, own_id=None):
    query = model.query.filter(func.lower(model.name) == name.lower())
    if own_id:
        query = query.filter(model.id != own_id)
    return query.first() is not None


# --- Startseite der Verwaltung ------------------------------------------------

@bp.route("/")
def index():
    from .navigation import admin_tiles
    counts = {
        "admin.warehouses": Warehouse.query.count(), "admin.varieties": Variety.query.filter_by(active=True).count(),
        "admin.plots": Plot.query.filter_by(active=True).count(), "admin.seed_classes": SeedClass.query.count(),
        "admin.box_types": BoxType.query.count(), "admin.users": User.query.filter_by(active=True).count(),
    }
    return render_template("admin/index.html", tiles=admin_tiles(), counts=counts)


# --- Einstellungen ----------------------------------------------------------

@bp.route("/einstellungen", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        values = {key: form_str(key, max_len) for key, (_label, max_len, _multiline) in Setting.FIELDS.items()}
        location = weather.parse_location(values["farm_location"])
        for key in ("blight_from_month", "blight_to_month"):
            values[key] = str(form_int(key, int(Setting.DEFAULTS[key]), minimum=1, maximum=12))
        if values["aerial_source"] not in aerial.SOURCES and values["aerial_source"] != "-":
            values["aerial_source"] = Setting.DEFAULTS["aerial_source"]
        if values["aerial_wms_url"] and not values["aerial_wms_url"].startswith(("https://", "http://")):
            values["aerial_wms_url"] = ""
        if values["holiday_state"] not in agenda.STATES and values["holiday_state"] != "-":
            values["holiday_state"] = Setting.DEFAULTS["holiday_state"]
        if values["farm_location"] and location is None:
            flash_error(_("Den Standort bitte als Breite, Länge angeben, z. B. 50.11, 8.68."))
        else:
            if location:
                values["farm_location"] = f"{location[0]:.4f}, {location[1]:.4f}"
            for key, value in values.items():
                Setting.put(key, value)
            db.session.commit()
            weather.clear_cache()
            flash(_("Einstellungen gespeichert."), "success")
            return redirect(url_for("admin.settings"))
        return render_template("admin/settings.html", values=values, fields=Setting.FIELDS, months=weather.MONTHS,
                               states=agenda.STATES, aerial_sources=aerial.SOURCES)
    return render_template("admin/settings.html", values=Setting.all(), fields=Setting.FIELDS, months=weather.MONTHS,
                           states=agenda.STATES, aerial_sources=aerial.SOURCES)


@bp.route("/logo", methods=["POST"])
def logo_upload():
    upload = request.files.get("logo")
    if not upload or not upload.filename:
        flash_error(_("Bitte eine Datei auswählen."))
    else:
        try:
            branding.save(upload)
            db.session.commit()
            flash(_("Logo gespeichert."), "success")
        except StorageError as e:
            db.session.rollback()
            flash_error(str(e))
    return redirect(url_for("admin.settings") + "#logo")


@bp.route("/logo/standard", methods=["POST"])
def logo_reset():
    branding.reset()
    db.session.commit()
    flash(_("Standard-Logo wird wieder verwendet."), "success")
    return redirect(url_for("admin.settings") + "#logo")


@bp.route("/startseite", methods=["GET", "POST"])
def tile_defaults():
    """Voreinstellung der Kacheln je Rolle."""
    if request.method == "POST":
        for role in tiles.ROLE_ORDER:
            tiles.save_role_default(role, request.form.getlist(f"tiles_{role}"))
        db.session.commit()
        flash(_("Voreinstellung gespeichert."), "success")
        return redirect(url_for("admin.tile_defaults"))
    from .models import ROLE_LEVEL
    return render_template("admin/tiles.html", catalog=tiles.CATALOG, roles=tiles.ROLE_ORDER, role_labels=ROLES,
                           defaults={r: tiles.role_default(r) for r in tiles.ROLE_ORDER},
                           can_see=lambda role, key: ROLE_LEVEL[role] >= ROLE_LEVEL[tiles.CATALOG[key]["role"]])


# --- Lager ------------------------------------------------------------------

@bp.route("/lager", methods=["GET", "POST"])
def warehouses():
    if request.method == "POST":
        wh_id = form_int("id")
        wh = _get(Warehouse, wh_id) if wh_id else Warehouse()
        name = form_str("name", 100)
        rows = form_int("rows", minimum=1, maximum=100)
        slots = form_int("slots", minimum=1, maximum=100)
        levels = form_int("max_levels", minimum=1, maximum=20)
        if not name or None in (rows, slots, levels):
            flash_error(_("Bitte Name, Reihen (1–100), Stellplätze (1–100) und Ebenen (1–20) angeben."))
        elif _name_taken(Warehouse, name, wh.id):
            flash_error(_("Ein Lager „{name}“ gibt es schon.", name=name))
        elif wh.id and wh.boxes.filter(or_(Box.row > rows, Box.slot > slots, Box.level > levels)).first():
            flash_error(_("Es stehen noch Kisten außerhalb der neuen Maße. Bitte zuerst umlagern."))
        else:
            wh.name, wh.rows, wh.slots, wh.max_levels = name, rows, slots, levels
            wh.note = form_str("note", 2000) or None
            wh.numeric_rows = bool(request.form.get("numeric_rows"))
            wh.rows_bottom_up = bool(request.form.get("rows_bottom_up"))
            wh.site_id = sites.from_form(request.form.get("site_id"))
            db.session.add(wh)
            db.session.commit()
            flash(_("Lager „{name}“ gespeichert.", name=wh.name), "success")
        return redirect(url_for("admin.warehouses"))

    items = Warehouse.query.order_by(Warehouse.name).all()
    counts = dict(db.session.query(Box.warehouse_id, func.count(Box.id)).group_by(Box.warehouse_id))
    return render_template("admin/warehouses.html", items=items, counts=counts)


@bp.route("/lager/<int:wh_id>/loeschen", methods=["POST"])
def warehouse_delete(wh_id):
    wh = _get(Warehouse, wh_id)
    if wh.boxes.first():
        flash_error(_("Im Lager stehen noch Kisten. Bitte zuerst auslagern."))
    else:
        db.session.delete(wh)
        db.session.commit()
        flash(_("Lager „{name}“ gelöscht.", name=wh.name), "success")
    return redirect(url_for("admin.warehouses"))


# --- Sorten -----------------------------------------------------------------

@bp.route("/sorten", methods=["GET", "POST"])
def varieties():
    if request.method == "POST":
        v_id = form_int("id")
        variety = _get(Variety, v_id) if v_id else Variety()
        name = form_str("name", 100)
        color = form_str("color", 7)
        usage = form_str("usage", 20)
        if not name:
            flash_error(_("Bitte einen Namen angeben."))
        elif _name_taken(Variety, name, variety.id):
            flash_error(_("Die Sorte „{name}“ gibt es schon.", name=name))
        else:
            variety.name = name
            variety.color = color if COLOR_RE.match(color) else "#c8a24a"
            variety.usage = usage if usage in SORT_USAGES else "speise"
            variety.note = form_str("note", 2000) or None
            variety.active = bool(request.form.get("active")) if variety.id else True
            db.session.add(variety)
            db.session.commit()
            flash(_("Sorte „{name}“ gespeichert.", name=variety.name), "success")
        return redirect(url_for("admin.varieties"))

    items = Variety.query.order_by(Variety.active.desc(), Variety.name).all()
    counts = dict(db.session.query(Box.variety_id, func.count(Box.id))
                  .filter(Box.fill > 0).group_by(Box.variety_id))
    return render_template("admin/varieties.html", items=items, counts=counts)


@bp.route("/sorten/<int:v_id>/loeschen", methods=["POST"])
def variety_delete(v_id):
    variety = _get(Variety, v_id)
    if Box.query.filter_by(variety_id=variety.id).first() or Movement.query.filter_by(variety_id=variety.id).first():
        variety.active = False
        db.session.commit()
        flash(_("„{name}“ wird in Kisten oder im Verlauf verwendet und wurde deshalb nur deaktiviert.", name=variety.name), "warning")
    else:
        db.session.delete(variety)
        db.session.commit()
        flash(_("Sorte „{name}“ gelöscht.", name=variety.name), "success")
    return redirect(url_for("admin.varieties"))


# --- Schläge ---------------------------------------------------------------

def _form_area():
    """Fläche in ha; akzeptiert Komma oder Punkt. None = leer oder ungültig."""
    raw = form_str("area_ha", 20).replace(",", ".")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value.quantize(Decimal("0.0001")) if 0 < value < 100000 else None


@bp.route("/schlaege", methods=["GET", "POST"])
def plots():
    if request.method == "POST":
        p_id = form_int("id")
        plot = _get(Plot, p_id) if p_id else Plot()
        name = form_str("name", 100)
        if not name:
            flash_error(_("Bitte einen Namen angeben."))
        elif _name_taken(Plot, name, plot.id):
            flash_error(_("Den Schlag „{name}“ gibt es schon.", name=name))
        else:
            plot.name = name
            plot.area_ha = _form_area()
            plot.note = form_str("note", 2000) or None
            plot.site_id = sites.from_form(request.form.get("site_id"))
            plot.active = bool(request.form.get("active")) if plot.id else True
            db.session.add(plot)
            db.session.commit()
            flash(_("Schlag „{name}“ gespeichert.", name=plot.name), "success")
        return redirect(url_for("admin.plots"))

    items = sites.apply(Plot.query, Plot).order_by(Plot.active.desc(), Plot.name).all()
    counts = dict(db.session.query(Box.plot_id, func.count(Box.id))
                  .filter(Box.fill > 0).group_by(Box.plot_id))
    return render_template("admin/plots.html", items=items, counts=counts, this_year=date.today().year)


@bp.route("/schlaege/betrieb", methods=["POST"])
def plots_assign_site():
    """Mehrere Schläge auf einmal einer Betriebsstätte zuordnen (oder die Zuordnung entfernen)."""
    ids = [int(i) for i in request.form.getlist("plot_ids") if i.isdigit()]
    if not ids:
        flash_error(_("Bitte mindestens einen Schlag markieren."))
        return redirect(url_for("admin.plots"))
    site_id = sites.from_form(request.form.get("site_id"))
    for plot in Plot.query.filter(Plot.id.in_(ids)):       # einzeln, damit es im Änderungsprotokoll steht
        plot.site_id = site_id
    db.session.commit()
    flash(ngettext("{n} Schlag zugeordnet.", "{n} Schläge zugeordnet.", len(ids)), "success")
    return redirect(url_for("admin.plots"))


@bp.route("/schlaege/kml", methods=["POST"])
def plots_kml_preview():
    upload = request.files.get("file")
    year = form_int("year", date.today().year, minimum=2000, maximum=2100)
    if not upload or not upload.filename:
        flash_error(_("Bitte eine Datei auswählen."))
        return redirect(url_for("admin.plots"))
    data_bytes = upload.read()
    try:
        entries = fields.parse_kml(upload.filename, data_bytes)
    except fields.FieldImportError as e:
        flash_error(str(e))
        return redirect(url_for("admin.plots"))
    rows, missing = fields.plan_import(entries, year)
    _cleanup_imports()
    token = secrets.token_hex(16)
    with open(os.path.join(_import_dir(), token), "wb") as f:
        f.write(data_bytes)
    session["kml_import"] = {"token": token, "name": upload.filename[:200], "year": year}
    return render_template("admin/plots_kml_preview.html", rows=rows, missing=missing, year=year,
                           filename=upload.filename, total_ha=sum(r["entry"]["area_ha"] for r in rows))


@bp.route("/schlaege/kml/uebernehmen", methods=["POST"])
def plots_kml_apply():
    info, path = _pending_import("kml_import")
    if not info:
        flash_error(_("Die Vorschau ist abgelaufen. Bitte die Datei erneut hochladen."))
        return redirect(url_for("admin.plots"))
    with open(path, "rb") as f:
        data_bytes = f.read()
    try:
        with audit.paused():
            created, updated = fields.apply_import(fields.parse_kml(info["name"], data_bytes), info["year"])
            db.session.flush()
        audit.note("plots", info["name"], {"created": created, "updated": updated, "year": info["year"]})
        db.session.commit()
    except fields.FieldImportError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("admin.plots"))
    os.remove(path)
    session.pop("kml_import", None)
    flash(_("Schlagkartei übernommen: {created} neu, {updated} aktualisiert.", created=created, updated=updated),
          "success")
    return redirect(url_for("fields.field_map", jahr=info["year"]))


@bp.route("/schlaege/myfarm", methods=["POST"])
def plots_csv_preview():
    """Schlagdokumentation aus Myfarm24 (CSV) – erst Vorschau, dann übernehmen."""
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash_error(_("Bitte eine Datei auswählen."))
        return redirect(url_for("admin.plots"))
    data_bytes = upload.read()
    try:
        year, entries = fielddoc.parse_csv(data_bytes)
    except fields.FieldImportError as e:
        flash_error(str(e))
        return redirect(url_for("admin.plots"))
    rows, missing = fielddoc.plan_import(entries, year)
    _cleanup_imports()
    token = secrets.token_hex(16)
    with open(os.path.join(_import_dir(), token), "wb") as f:
        f.write(data_bytes)
    session["fielddoc_import"] = {"token": token}
    totals = {}
    for row in rows:
        for kind, n in row["counts"].items():
            totals[kind] = totals.get(kind, 0) + n
    return render_template("admin/plots_csv_preview.html", rows=rows, missing=missing, year=year,
                           filename=upload.filename, totals=totals, kinds=FIELD_MEASURE_KINDS)


@bp.route("/schlaege/myfarm/uebernehmen", methods=["POST"])
def plots_csv_apply():
    info, path = _pending_import("fielddoc_import")
    if not info:
        flash_error(_("Die Vorschau ist abgelaufen. Bitte die Datei erneut hochladen."))
        return redirect(url_for("admin.plots"))
    with open(path, "rb") as f:
        data_bytes = f.read()
    try:
        year, entries = fielddoc.parse_csv(data_bytes)
        with audit.paused():
            stats = fielddoc.apply_import(entries, year, create=bool(request.form.get("create")))
            db.session.flush()
        audit.note("field_measures", f"Myfarm24 {year}", {k: stats[k] for k in ("created", "updated", "measures")})
        db.session.commit()
    except fields.FieldImportError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("admin.plots"))
    os.remove(path)
    session.pop("fielddoc_import", None)
    flash(_("Schlagdokumentation {year} übernommen: {created} Schläge neu, {updated} aktualisiert, {measures} Maßnahmen.",
            year=year, created=stats["created"], updated=stats["updated"],
            measures=stats["measures"]), "success")
    return redirect(url_for("admin.plots"))


@bp.route("/schlaege/fruchtfolge", methods=["POST"])
def plots_plan_preview():
    """Fruchtfolgeplan aus Myfarm24 (CSV) – erst Vorschau, dann übernehmen."""
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash_error(_("Bitte eine Datei auswählen."))
        return redirect(url_for("admin.plots"))
    data_bytes = upload.read()
    try:
        years, entries = fielddoc.parse_plan(data_bytes)
    except fields.FieldImportError as e:
        flash_error(str(e))
        return redirect(url_for("admin.plots"))
    rows, missing = fielddoc.plan_rotation(entries, years)
    _cleanup_imports()
    token = secrets.token_hex(16)
    with open(os.path.join(_import_dir(), token), "wb") as f:
        f.write(data_bytes)
    session["plan_import"] = {"token": token}
    return render_template("admin/plots_plan_preview.html", rows=rows, missing=missing, years=years,
                           filename=upload.filename)


@bp.route("/schlaege/fruchtfolge/uebernehmen", methods=["POST"])
def plots_plan_apply():
    info, path = _pending_import("plan_import")
    if not info:
        flash_error(_("Die Vorschau ist abgelaufen. Bitte die Datei erneut hochladen."))
        return redirect(url_for("admin.plots"))
    with open(path, "rb") as f:
        data_bytes = f.read()
    try:
        years, entries = fielddoc.parse_plan(data_bytes)
        with audit.paused():
            stats = fielddoc.apply_rotation(entries, years, create=bool(request.form.get("create")))
            db.session.flush()
        audit.note("plot_years", f"{years[0]}–{years[-1]}", {k: stats[k] for k in ("created", "updated")})
        db.session.commit()
    except fields.FieldImportError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("admin.plots"))
    os.remove(path)
    session.pop("plan_import", None)
    flash(_("Fruchtfolgeplan {first}–{last} übernommen: {created} Schläge neu, {updated} geändert.",
            first=years[0], last=years[-1], created=stats["created"], updated=stats["updated"]), "success")
    return redirect(url_for("fields.rotation_page"))


@bp.route("/schlaege/<int:p_id>/loeschen", methods=["POST"])
def plot_delete(p_id):
    plot = _get(Plot, p_id)
    if Box.query.filter_by(plot_id=plot.id).first() or Movement.query.filter_by(plot_id=plot.id).first():
        plot.active = False
        db.session.commit()
        flash(_("„{name}“ wird in Kisten oder im Verlauf verwendet und wurde deshalb nur deaktiviert.", name=plot.name), "warning")
    else:
        db.session.delete(plot)
        db.session.commit()
        flash(_("Schlag „{name}“ gelöscht.", name=plot.name), "success")
    return redirect(url_for("admin.plots"))


# --- Betriebsstätten ---------------------------------------------------------

@bp.route("/betriebsstaetten", methods=["GET", "POST"])
def site_list():
    from .models import Site
    if request.method == "POST":
        s_id = form_int("id")
        site = _get(Site, s_id) if s_id else Site()
        try:
            sites.save_site(site, form_str("name", 100), form_str("address", 500), form_str("phone", 50),
                            form_str("email", 120), form_str("passport_reg", 40), form_str("note", 2000),
                            bool(request.form.get("active")) if s_id else True)
        except StorageError as e:
            db.session.rollback()
            flash_error(str(e))
            return redirect(url_for("admin.site_list"))
        db.session.commit()
        flash(_("Betriebsstätte „{name}“ gespeichert.", name=site.name), "success")
        return redirect(url_for("admin.site_list"))
    items = Site.query.order_by(Site.active.desc(), Site.name).all()
    return render_template("admin/sites.html", items=items, usage={s.id: sites.usage(s) for s in items})


@bp.route("/betriebsstaetten/<int:s_id>/loeschen", methods=["POST"])
def site_delete(s_id):
    from .models import Site
    site = _get(Site, s_id)
    if sites.delete_site(site):
        flash(_("Betriebsstätte „{name}“ gelöscht.", name=site.name), "success")
    else:
        flash(_("„{name}“ ist noch zugeordnet und wurde deshalb nur deaktiviert.", name=site.name), "warning")
    db.session.commit()
    return redirect(url_for("admin.site_list"))


# --- Änderungsprotokoll --------------------------------------------------------

@bp.route("/protokoll")
def audit_log():
    from .models import AuditEntry, User
    query = AuditEntry.query
    area = request.args.get("bereich", "")
    if area in audit.AREAS:
        query = query.filter(AuditEntry.table == area)
    action = request.args.get("aktion", "")
    if action in audit.ACTIONS:
        query = query.filter(AuditEntry.action == action)
    user_id = request.args.get("benutzer", type=int)
    if user_id:
        query = query.filter(AuditEntry.user_id == user_id)
    q = request.args.get("q", "").strip()[:100]
    if q:
        query = query.filter(AuditEntry.label.ilike(f"%{q}%"))
    page = query.order_by(AuditEntry.created_at.desc(), AuditEntry.id.desc()).paginate(per_page=50, error_out=False)
    return render_template("admin/audit.html", page=page, areas=audit.AREAS, actions=audit.ACTIONS,
                           details=audit.describe(page.items), users=User.query.order_by(User.username).all(),
                           args=request.args, keep_days=audit.KEEP_DAYS)


# --- Saatgut-Stufen ---------------------------------------------------------

@bp.route("/stufen", methods=["GET", "POST"])
def seed_classes():
    if request.method == "POST":
        c_id = form_int("id")
        seed_class = _get(SeedClass, c_id) if c_id else SeedClass()
        name = form_str("name", 20)
        if not name:
            flash_error(_("Bitte einen Namen angeben."))
        elif _name_taken(SeedClass, name, seed_class.id):
            flash_error(_("Die Stufe „{name}“ gibt es schon.", name=name))
        else:
            seed_class.name = name
            seed_class.description = form_str("description", 200) or None
            seed_class.position = form_int("position", 0, minimum=0, maximum=999)
            seed_class.active = bool(request.form.get("active")) if seed_class.id else True
            db.session.add(seed_class)
            db.session.commit()
            flash(_("Stufe „{name}“ gespeichert.", name=seed_class.name), "success")
        return redirect(url_for("admin.seed_classes"))

    items = SeedClass.query.order_by(SeedClass.active.desc(), SeedClass.position, SeedClass.name).all()
    counts = dict(db.session.query(Box.seed_class_id, func.count(Box.id))
                  .filter(Box.fill > 0).group_by(Box.seed_class_id))
    return render_template("admin/seed_classes.html", items=items, counts=counts)


@bp.route("/stufen/<int:c_id>/loeschen", methods=["POST"])
def seed_class_delete(c_id):
    seed_class = _get(SeedClass, c_id)
    if Box.query.filter_by(seed_class_id=seed_class.id).first() \
            or Movement.query.filter_by(seed_class_id=seed_class.id).first():
        seed_class.active = False
        db.session.commit()
        flash(_("„{name}“ wird in Kisten oder im Verlauf verwendet und wurde deshalb nur deaktiviert.",
                name=seed_class.name), "warning")
    else:
        db.session.delete(seed_class)
        db.session.commit()
        flash(_("Stufe „{name}“ gelöscht.", name=seed_class.name), "success")
    return redirect(url_for("admin.seed_classes"))


# --- Kistentypen ------------------------------------------------------------

@bp.route("/kistentypen", methods=["GET", "POST"])
def box_types():
    if request.method == "POST":
        t_id = form_int("id")
        box_type = _get(BoxType, t_id) if t_id else BoxType()
        name = form_str("name", 100)
        weight = form_int("full_weight_kg", minimum=1, maximum=5000)
        if not name or weight is None:
            flash_error(_("Bitte Name und Gewicht (1–5000 kg) angeben."))
        elif _name_taken(BoxType, name, box_type.id):
            flash_error(_("Den Kistentyp „{name}“ gibt es schon.", name=name))
        else:
            box_type.name, box_type.full_weight_kg = name, weight
            box_type.note = form_str("note", 2000) or None
            db.session.add(box_type)
            db.session.commit()
            flash(_("Kistentyp „{name}“ gespeichert.", name=box_type.name), "success")
        return redirect(url_for("admin.box_types"))

    items = BoxType.query.order_by(BoxType.name).all()
    counts = dict(db.session.query(Box.box_type_id, func.count(Box.id)).group_by(Box.box_type_id))
    return render_template("admin/box_types.html", items=items, counts=counts)


@bp.route("/kistentypen/<int:t_id>/loeschen", methods=["POST"])
def box_type_delete(t_id):
    box_type = _get(BoxType, t_id)
    if Box.query.filter_by(box_type_id=box_type.id).first():
        flash_error(_("Dieser Kistentyp wird noch verwendet."))
    else:
        db.session.delete(box_type)
        db.session.commit()
        flash(_("Kistentyp „{name}“ gelöscht.", name=box_type.name), "success")
    return redirect(url_for("admin.box_types"))


# --- Benutzer ---------------------------------------------------------------

def _active_admins(exclude_id=None):
    query = User.query.filter_by(role=ROLE_ADMIN, active=True)
    if exclude_id:
        query = query.filter(User.id != exclude_id)
    return query.count()


@bp.route("/benutzer", methods=["GET", "POST"])
def users():
    if request.method == "POST":
        u_id = form_int("id")
        user = _get(User, u_id) if u_id else User()
        username = form_str("username", 64).lower()
        role = form_str("role", 20)
        active = bool(request.form.get("active")) if user.id else True
        password = request.form.get("password", "")

        error = None
        if not re.match(r"^[a-z0-9._-]{2,64}$", username):
            error = _("Benutzername: 2–64 Zeichen, nur a–z, 0–9, Punkt, Minus, Unterstrich.")
        elif User.query.filter(User.username == username, User.id != (user.id or 0)).first():
            error = _("Den Benutzer „{name}“ gibt es schon.", name=username)
        elif role not in ROLES:
            error = _("Ungültige Rolle.")
        elif (not user.id or password) and (msg := validate_password(password, request.form.get("password2", ""))):
            error = msg
        elif user.id and user.role == ROLE_ADMIN and user.active and (role != ROLE_ADMIN or not active) \
                and _active_admins(exclude_id=user.id) == 0:
            error = _("Es muss mindestens ein aktiver Admin bleiben.")
        if error:
            flash_error(error)
            return redirect(url_for("admin.users"))

        was_active = user.active
        user.username = username
        user.display_name = form_str("display_name", 100) or None
        user.role = role
        user.active = active
        user.can_order = bool(request.form.get("can_order"))
        language = form_str("language", 5)
        if language in LANGUAGES:
            user.language = language
        if password:
            user.set_password(password)
        elif user.id and was_active and not active:
            user.rotate_session()
        db.session.add(user)
        db.session.commit()
        flash(_("Benutzer „{name}“ gespeichert.", name=user.username), "success")
        if user.id == current_user.id and (not user.active or password):
            return redirect(url_for("auth.login"))
        return redirect(url_for("admin.users"))

    items = User.query.order_by(User.active.desc(), User.username).all()
    return render_template("admin/users.html", items=items, languages=LANGUAGES)


# --- Import / Export --------------------------------------------------------

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
IMPORT_MAX_AGE = 24 * 3600


def _import_dir():
    path = os.path.join(current_app.instance_path, "imports")
    os.makedirs(path, exist_ok=True)
    return path


def _cleanup_imports():
    now = time.time()
    for name in os.listdir(_import_dir()):
        path = os.path.join(_import_dir(), name)
        if now - os.path.getmtime(path) > IMPORT_MAX_AGE:
            os.remove(path)


def _pending_import(key="import"):
    """Die hochgeladene Datei aus der Vorschau (oder None)."""
    info = session.get(key) or {}
    token = info.get("token", "")
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        return None, None
    path = os.path.join(_import_dir(), token)
    if not os.path.exists(path):
        return None, None
    return info, path


@bp.route("/daten")
def data():
    years = transfer.season_years()
    return render_template("admin/data.html", years=years, current_year=date.today().year,
                           warehouses=Warehouse.query.order_by(Warehouse.name).all())


@bp.route("/daten/vorlage.xlsx")
def import_template():
    return Response(transfer.build_template(), mimetype=XLSX, headers={
        "Content-Disposition": 'attachment; filename="lager_import_vorlage.xlsx"'})


@bp.route("/daten/export.xlsx")
def export():
    year = request.args.get("jahr", type=int)
    return Response(transfer.build_export(year), mimetype=XLSX, headers={
        "Content-Disposition": f'attachment; filename="{transfer.export_filename(year)}"'})


@bp.route("/daten/import", methods=["POST"])
def import_preview():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash_error(_("Bitte eine Datei auswählen."))
        return redirect(url_for("admin.data"))
    data_bytes = upload.read()
    try:
        rows = transfer.read_file(upload.filename, data_bytes)
    except transfer.TransferError as e:
        flash_error(str(e))
        return redirect(url_for("admin.data"))
    if not rows:
        flash_error(_("Die Datei enthält keine Zeilen."))
        return redirect(url_for("admin.data"))

    create_missing = bool(request.form.get("create_missing"))
    clear_first = bool(request.form.get("clear_first"))
    warehouse = db.session.get(Warehouse, form_int("warehouse_id", 0))
    warehouse_id = warehouse.id if warehouse else None
    results = transfer.run_import(rows, current_user, create_missing, clear_first, warehouse_id)
    db.session.rollback()   # nur Vorschau

    _cleanup_imports()
    token = secrets.token_hex(16)
    with open(os.path.join(_import_dir(), token), "wb") as f:
        f.write(data_bytes)
    session["import"] = {"token": token, "name": upload.filename[:200], "create": create_missing,
                         "clear": clear_first, "warehouse": warehouse_id}
    errors = sum(1 for r in results if r["errors"])
    return render_template("admin/import_preview.html", results=results, errors=errors,
                           filename=upload.filename, create_missing=create_missing, warehouse=warehouse)


@bp.route("/daten/import/uebernehmen", methods=["POST"])
def import_apply():
    info, path = _pending_import()
    if not info:
        flash_error(_("Die Vorschau ist abgelaufen. Bitte die Datei erneut hochladen."))
        return redirect(url_for("admin.data"))
    with open(path, "rb") as f:
        data_bytes = f.read()
    try:
        rows = transfer.read_file(info["name"], data_bytes)
        with audit.paused():
            results = transfer.run_import(rows, current_user, info["create"], info.get("clear", False),
                                          info.get("warehouse"))
            db.session.flush()
    except transfer.TransferError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("admin.data"))
    errors = sum(1 for r in results if r["errors"])
    if errors:
        # Zwischen Vorschau und Übernehmen hat sich etwas geändert (z. B. jemand hat gebucht)
        db.session.rollback()
        return render_template("admin/import_preview.html", results=results, errors=errors,
                               filename=info["name"], create_missing=info["create"],
                               warehouse=db.session.get(Warehouse, info.get("warehouse") or 0))
    audit.note("boxes", info["name"], {"rows": len(results)})
    db.session.commit()
    os.remove(path)
    session.pop("import", None)
    flash(ngettext("{n} Zeile importiert.", "{n} Zeilen importiert.", len(results)), "success")
    return redirect(url_for("admin.data"))


# --- Datensicherung -----------------------------------------------------------

@bp.route("/sicherung")
def backups():
    return render_template("admin/backup.html", info=backup.overview())


@bp.route("/sicherung/jetzt", methods=["POST"])
def backup_now():
    if not backup.backup_dir():
        flash_error(_("Die Datensicherung ist nicht eingerichtet (BACKUP_DIR fehlt)."))
    elif backup.is_running():
        flash_error(_("Es läuft bereits eine Sicherung."))
    else:
        backup.run_in_background(current_app._get_current_object())
        flash(_("Sicherung gestartet."), "success")
    return redirect(url_for("admin.backups"))


@bp.route("/sicherung/<name>")
def backup_download(name):
    path = backup.backup_path(name)
    if path is None:
        abort(404)
    return send_file(path, as_attachment=True, download_name=name, mimetype="application/octet-stream")
