"""Rubrik Sensoren: alle Meshtastic-Knoten, Zuordnung, Schlüssel für die Brücke (Logik in sensors.py)."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import climate, coop, sensors
from .extensions import db
from .i18n import gettext as _
from .models import MeshNode, Warehouse
from .storage import StorageError
from .utils import admin_required, flash_error, form_int, form_str

bp = Blueprint("sensors", __name__)


def _place():
    warehouse = db.session.get(Warehouse, form_int("warehouse_id", 0)) if form_int("warehouse_id") else None
    return warehouse, form_int("row") if warehouse else None


@bp.route("/sensoren")
@login_required
def overview_page():
    if not (current_user.is_admin or sensors.any_in_use()):
        abort(404)
    data = sensors.overview()
    if not (current_user.is_admin or current_user.sees_chickens):
        data["coop"] = []
    return render_template("sensors/overview.html", data=data, purposes=sensors.PURPOSES,
                           warehouses=climate.warehouses(), coop_status=coop.status() if data["coop"] else None,
                           has_token=coop.has_token(), doors=sensors.door_settings())


@bp.route("/sensoren/neu", methods=["POST"])
@admin_required
def node_add():
    try:
        warehouse, row = _place()
        mesh = sensors.add(form_str("node", 20), form_str("name", 100), form_str("purpose", 20), warehouse, row)
        db.session.commit()
        flash(_("Knoten {node} angelegt – sobald er etwas schickt, erscheinen hier seine Werte.", node=mesh.node),
              "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(_(str(e)))
    return redirect(url_for("sensors.overview_page"))


@bp.route("/sensoren/<int:node_id>", methods=["POST"])
@admin_required
def node_save(node_id):
    mesh = db.session.get(MeshNode, node_id) or abort(404)
    try:
        warehouse, row = _place()
        sensors.assign(mesh, form_str("purpose", 20), form_str("name", 100), warehouse, row)
        db.session.commit()
        flash(_("{name} gespeichert.", name=mesh.label), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(_(str(e)))
    return redirect(url_for("sensors.overview_page") + f"#n{mesh.id}")


@bp.route("/sensoren/<int:node_id>/loeschen", methods=["POST"])
@admin_required
def node_delete(node_id):
    mesh = db.session.get(MeshNode, node_id) or abort(404)
    sensors.delete(mesh)
    db.session.commit()
    flash(_("Knoten entfernt."), "success")
    return redirect(url_for("sensors.overview_page"))


@bp.route("/sensoren/klappe", methods=["POST"])
@admin_required
def door_save():
    sensors.save_door_settings("door_inverted" in request.form, "top_inverted" in request.form)
    db.session.commit()
    flash(_("Gespeichert."), "success")
    return redirect(url_for("sensors.overview_page") + "#einrichtung")


@bp.route("/sensoren/schluessel", methods=["POST"])
@admin_required
def token():
    value = coop.new_token()
    db.session.commit()
    flash(_("Neuer Schlüssel: {token} – als COOP_API_TOKEN im Stack eintragen (wird nur jetzt angezeigt).",
            token=value), "warning")
    return redirect(url_for("sensors.overview_page") + "#einrichtung")
