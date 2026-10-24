"""Tankbuch: Füllstand des Hoftanks, Tanken, Lieferungen, Peilung, Liter je Maschine."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import fuel, fuel_report, purchases, push, sites
from .extensions import db
from .i18n import gettext as _
from .models import FUEL_KINDS, FuelEntry, Machine, Setting
from .storage import StorageError
from .utils import admin_required, editor_required, flash_error, form_date, form_int, form_str

bp = Blueprint("fuel", __name__)


def _buyer_only():
    if not current_user.is_buyer:
        abort(403)


def _price(text):
    """„1,529“ → Decimal (Preis je Liter) oder None."""
    text = (text or "").strip().replace("€", "").replace(" ", "")
    if not text:
        return None
    try:
        value = Decimal(text.replace(".", "").replace(",", ".") if "," in text else text).quantize(Decimal("0.001"))
    except InvalidOperation:
        raise StorageError(_("Bitte den Preis je Liter als Zahl angeben, z. B. 1,529."))
    if value <= 0 or value >= 100:
        raise StorageError(_("Bitte den Preis je Liter als Zahl angeben, z. B. 1,529."))
    return value


@bp.route("/tankbuch")
@login_required
def tank():
    machine_id = request.args.get("maschine", type=int)
    kind = request.args.get("art")
    query = FuelEntry.query
    if machine_id:
        query = query.filter(FuelEntry.machine_id == machine_id)
    if kind in FUEL_KINDS:
        query = query.filter(FuelEntry.kind == kind)
    page = query.order_by(FuelEntry.date.desc(), FuelEntry.id.desc()).paginate(per_page=40, error_out=False)
    year = request.args.get("jahr", type=int) or date.today().year
    return render_template(
        "fuel/tank.html", status=fuel.status(), page=page, tank_site_id=sites.tank_site_id(), kinds=FUEL_KINDS, kind=kind, machine_id=machine_id,
        machines=Machine.query.filter_by(active=True).order_by(Machine.name).all(),
        per_machine=fuel.per_machine(year), year=year, year_total=fuel.liters_in_year(year=year),
        last_price=fuel.last_price(), suppliers=purchases.suppliers(), today=date.today(),
        filter_machine=db.session.get(Machine, machine_id) if machine_id else None,
    )


@bp.route("/tankbuch/tanken", methods=["POST"])
@editor_required
def refuel():
    machine = db.session.get(Machine, form_int("machine_id") or 0)
    if machine is None:
        flash_error(_("Bitte die Maschine auswählen."))
        return redirect(url_for("fuel.tank"))
    try:
        before = fuel.level()[0]
        entry = fuel.refuel(machine, current_user, fuel.parse_liters(form_str("liters", 20)),
                            form_int("counter", minimum=0, maximum=10_000_000), form_date("date"),
                            form_str("note", 200))
        db.session.commit()
        push.tank_low(before, before - entry.liters)
        flash(_("{liters} l für {name} eingetragen.", liters=f"{entry.liters:.1f}".replace(".", ","),
                name=machine.name), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("fuel.tank"))


@bp.route("/tankbuch/lieferung", methods=["POST"])
@login_required
def delivery():
    _buyer_only()
    try:
        supplier = purchases.supplier_by_name(form_str("supplier", 150))
        entry = fuel.delivery(current_user, fuel.parse_liters(form_str("liters", 20)), _price(form_str("price", 20)),
                              supplier.id if supplier else None, form_date("date"), form_str("note", 200))
        db.session.commit()
        flash(_("Lieferung über {liters} l eingetragen.", liters=f"{entry.liters:.0f}"), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("fuel.tank"))


@bp.route("/tankbuch/peilung", methods=["POST"])
@login_required
def dip():
    _buyer_only()
    try:
        text = form_str("liters", 20).replace(" ", "")
        value = text.replace(".", "").replace(",", ".") if "," in text else text
        fuel.dip(current_user, value, form_date("date"), form_str("note", 200))
        db.session.commit()
        flash(_("Füllstand gespeichert."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("fuel.tank"))


@bp.route("/tankbuch/einstellungen", methods=["POST"])
@admin_required
def tank_settings():
    fuel.save_settings(form_int("capacity", minimum=1, maximum=1_000_000),
                       form_int("warn", minimum=1, maximum=1_000_000))
    if "site_id" in request.form:
        sites.set_tank_site(sites.from_form(request.form.get("site_id")))
    db.session.commit()
    flash(_("Tank-Einstellungen gespeichert."), "success")
    return redirect(url_for("fuel.tank"))


@bp.route("/tankbuch/<int:entry_id>/loeschen", methods=["POST"])
@admin_required
def entry_delete(entry_id):
    entry = db.session.get(FuelEntry, entry_id)
    if entry is None:
        abort(404)
    db.session.delete(entry)
    db.session.commit()
    flash(_("Eintrag gelöscht."), "success")
    return redirect(request.referrer if (request.referrer or "").startswith(request.host_url) else url_for("fuel.tank"))


@bp.route("/tankbuch/export")
@admin_required
def export():
    """Tankbuch als Excel oder PDF – Jahr oder Zeitraum, optional nur eine Maschine."""
    today = date.today()
    year = request.args.get("jahr", type=int)
    start, end = _arg_date("von"), _arg_date("bis")
    if not (start and end):
        year = year or today.year
        start, end = date(year, 1, 1), date(year, 12, 31)
    if end < start:
        start, end = end, start
    machine = db.session.get(Machine, request.args.get("maschine", type=int) or 0)
    report = fuel_report.build(start, end, machine)
    farm = Setting.all()
    if request.args.get("format") == "xlsx":
        return Response(fuel_report.build_xlsx(report, farm),
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{fuel_report.filename(report, "xlsx")}"'})
    return Response(fuel_report.build_pdf(report, farm), mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{fuel_report.filename(report, "pdf")}"'})


def _arg_date(name):
    value = request.args.get(name, "")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except ValueError:
        return None
