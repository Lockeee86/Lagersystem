import os
import re
import secrets
import time
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user
from sqlalchemy import func, or_

from . import transfer
from .auth import validate_password
from .extensions import db
from .i18n import LANGUAGES, gettext as _, ngettext
from .models import (ROLE_ADMIN, ROLES, SORT_USAGES, Box, BoxType, Movement, Plot, SeedClass, User, Variety,
                     Warehouse)
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
    return value.quantize(Decimal("0.01")) if 0 < value < 100000 else None


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
            plot.active = bool(request.form.get("active")) if plot.id else True
            db.session.add(plot)
            db.session.commit()
            flash(_("Schlag „{name}“ gespeichert.", name=plot.name), "success")
        return redirect(url_for("admin.plots"))

    items = Plot.query.order_by(Plot.active.desc(), Plot.name).all()
    counts = dict(db.session.query(Box.plot_id, func.count(Box.id))
                  .filter(Box.fill > 0).group_by(Box.plot_id))
    return render_template("admin/plots.html", items=items, counts=counts)


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


def _pending_import():
    """Die hochgeladene Datei aus der Vorschau (oder None)."""
    info = session.get("import") or {}
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
        results = transfer.run_import(rows, current_user, info["create"], info.get("clear", False),
                                      info.get("warehouse"))
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
    db.session.commit()
    os.remove(path)
    session.pop("import", None)
    flash(ngettext("{n} Zeile importiert.", "{n} Zeilen importiert.", len(results)), "success")
    return redirect(url_for("admin.data"))
