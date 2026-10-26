"""Seite Lager → Klima: Werte je Lager und Sensor, Kurven, Grenzwerte (Logik in climate.py; Sensoren zuordnen in
sensors_views.py)."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from . import climate
from .extensions import db
from .i18n import gettext as _
from .models import Warehouse
from .storage import StorageError
from .utils import admin_required, flash_error, form_str

bp = Blueprint("climate", __name__)


def _float(name):
    raw = form_str(name, 20).replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise StorageError(_("Bitte eine Zahl angeben."))


@bp.route("/lager/klima")
@login_required
def overview_page():
    key = request.args.get("zeitraum", "24h")
    key = key if key in climate.RANGES else "24h"
    data = climate.overview()
    charts = {s["sensor"].id: climate.series(s["sensor"], key) for _wh, rows in data["warehouses"] for s in rows}
    warehouses = climate.warehouses()
    return render_template("climate/overview.html", data=data, charts=charts, range_key=key, ranges=climate.RANGES,
                           warehouses=warehouses, limits={w.id: climate.limits(w) for w in warehouses},
                           defaults=climate.DEFAULTS, target=climate.TARGET)


@bp.route("/lager/klima/grenzwerte/<int:warehouse_id>", methods=["POST"])
@admin_required
def limits_save(warehouse_id):
    warehouse = db.session.get(Warehouse, warehouse_id) or abort(404)
    try:
        values = {key: _float(key) for key in climate.DEFAULTS}
        climate.save_limits(warehouse, values)
        db.session.commit()
        flash(_("Grenzwerte für {name} gespeichert.", name=warehouse.name), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("climate.overview_page") + f"#w{warehouse.id}")
