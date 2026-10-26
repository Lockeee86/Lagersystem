"""Seite Lager → Klima: Werte je Lager und Sensor, Kurven, Sensoren zuordnen, Grenzwerte (Logik in climate.py)."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from . import climate, coop
from .extensions import db
from .i18n import gettext as _
from .models import ClimateSensor, Warehouse
from .storage import StorageError
from .utils import admin_required, flash_error, form_int, form_str

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
                           defaults=climate.DEFAULTS, target=climate.TARGET, has_token=coop.has_token(),
                           inactive=ClimateSensor.query.filter_by(active=False).all())


@bp.route("/lager/klima/sensor/<int:sensor_id>", methods=["POST"])
@admin_required
def sensor_save(sensor_id):
    sensor = db.session.get(ClimateSensor, sensor_id) or abort(404)
    warehouse = db.session.get(Warehouse, form_int("warehouse_id", 0)) if form_int("warehouse_id") else None
    try:
        climate.save_sensor(sensor, form_str("name", 100), warehouse, form_int("row"),
                            active="1" in request.form.getlist("active") if "active" in request.form else True)
        db.session.commit()
        flash(_("{name} gespeichert.", name=sensor.label), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("climate.overview_page") + f"#s{sensor.id}")


@bp.route("/lager/klima/sensor/<int:sensor_id>/loeschen", methods=["POST"])
@admin_required
def sensor_delete(sensor_id):
    sensor = db.session.get(ClimateSensor, sensor_id) or abort(404)
    db.session.delete(sensor)
    db.session.commit()
    flash(_("Sensor und seine Messwerte gelöscht."), "success")
    return redirect(url_for("climate.overview_page"))


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
