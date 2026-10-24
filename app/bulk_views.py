"""Schüttgut-Lager: Übersicht der Lagerorte, Lagerort mit Bestand, Qualität, Herkunft und Buchungen."""
from datetime import date

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import bulk, sites
from .extensions import db
from .i18n import gettext as _
from .labels import LABEL_FORMATS, render_labels
from .models import BULK_KINDS, BULK_PURPOSES, BULK_QUALITY, BulkEntry, BulkStore, Plot
from .storage import StorageError
from .utils import admin_required, editor_required, flash_error, form_date, form_int, form_str

bp = Blueprint("bulk", __name__)


def _store(store_id):
    store = db.session.get(BulkStore, store_id)
    if store is None:
        abort(404)
    return store


def _kg(name):
    return bulk.parse_number(request.form.get(name), 1, 10_000_000, integer=True)


def _quality():
    values = {}
    for field in BULK_QUALITY:
        low, high = bulk.QUALITY_RANGES[field]
        values[field] = bulk.parse_number(request.form.get(field), low, high, integer=field == "falling_number")
    return values


@bp.route("/schuettgut")
@login_required
def store_list():
    rows, per_ware = bulk.overview(sites.chosen())
    inactive = BulkStore.query.filter_by(active=False).order_by(BulkStore.name).all() if current_user.is_admin else []
    return render_template("bulk/list.html", rows=rows, per_ware=per_ware, kinds=BULK_KINDS, inactive=inactive)


@bp.route("/schuettgut/<int:store_id>")
@login_required
def store_detail(store_id):
    store = _store(store_id)
    plots = Plot.query.filter_by(active=True).order_by(Plot.name).all()
    return render_template(
        "bulk/detail.html", store=store, stock=bulk.stock(store), quality=bulk.quality(store),
        shares=bulk.plot_shares(store), entries=store.entries[:50], plots=plots, purposes=BULK_PURPOSES,
        kinds=BULK_KINDS, crops=bulk.known_crops(), today=date.today(), label_formats=LABEL_FORMATS,
        open_in=request.args.get("einlagern") == "1",
    )


@bp.route("/schuettgut/speichern", methods=["POST"])
@admin_required
def store_save():
    store_id = form_int("id")
    store = _store(store_id) if store_id else BulkStore()
    try:
        bulk.save_store(store, form_str("name", 100), request.form.get("kind", ""), form_str("location", 100),
                        bulk.parse_number(request.form.get("capacity_t"), 0.01, 100000),
                        bulk.parse_number(request.form.get("bag_kg"), 1, 5000, integer=True),
                        form_str("note", 2000), bool(request.form.get("active")) if store_id else True,
                        sites.from_form(request.form.get("site_id")))
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("bulk.store_detail", store_id=store_id) if store_id else url_for("bulk.store_list"))
    db.session.commit()
    flash(_("Lagerort „{name}“ gespeichert.", name=store.name), "success")
    return redirect(url_for("bulk.store_detail", store_id=store.id))


@bp.route("/schuettgut/<int:store_id>/etikett.pdf", methods=["POST"])
@admin_required
def store_label(store_id):
    store = _store(store_id)
    if not store.code:
        store.code = bulk.next_code()
        db.session.commit()
    fmt = request.form.get("format", "2x2")
    if fmt not in LABEL_FORMATS:
        fmt = "2x2"
    count = form_int("count", 1, minimum=1, maximum=24)
    pdf = render_labels([store.code] * count, fmt, skip=form_int("skip", 0, minimum=0, maximum=100))
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="etikett_{store.code}.pdf"'})


@bp.route("/schuettgut/<int:store_id>/einlagern", methods=["POST"])
@editor_required
def take_in(store_id):
    store = _store(store_id)
    plot_id = form_int("plot_id")
    try:
        bulk.take_in(store, current_user, form_date("date") or date.today(), form_str("crop", 100),
                     net_kg=_kg("net_kg"), gross_kg=_kg("gross_kg"), tare_kg=_kg("tare_kg"),
                     variety_name=form_str("variety_name", 100),
                     plot=db.session.get(Plot, plot_id) if plot_id else None,
                     bags=form_int("bags", minimum=1, maximum=10000), vehicle=form_str("vehicle", 50),
                     note=form_str("note", 300), **_quality())
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("bulk.store_detail", store_id=store.id, einlagern=1))
    db.session.commit()
    flash(_("Eingelagert in „{name}“.", name=store.name), "success")
    return redirect(url_for("bulk.store_detail", store_id=store.id))


@bp.route("/schuettgut/<int:store_id>/auslagern", methods=["POST"])
@editor_required
def take_out(store_id):
    store = _store(store_id)
    purpose = request.form.get("purpose", "")
    if purpose == "verkauf":
        flash_error(_("Verkauf bitte über einen Lieferschein buchen."))
        return redirect(url_for("bulk.store_detail", store_id=store.id))
    try:
        bulk.take_out(store, current_user, form_date("date") or date.today(), purpose, net_kg=_kg("net_kg"),
                      gross_kg=_kg("gross_kg"), tare_kg=_kg("tare_kg"),
                      bags=form_int("bags", minimum=1, maximum=10000), note=form_str("note", 300))
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("bulk.store_detail", store_id=store.id))
    db.session.commit()
    flash(_("Ausgelagert aus „{name}“.", name=store.name), "success")
    return redirect(url_for("bulk.store_detail", store_id=store.id))


@bp.route("/schuettgut/buchung/<int:entry_id>/loeschen", methods=["POST"])
@admin_required
def entry_delete(entry_id):
    entry = db.session.get(BulkEntry, entry_id)
    if entry is None:
        abort(404)
    store_id = entry.store_id
    try:
        bulk.delete_entry(entry)
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("bulk.store_detail", store_id=store_id))
    db.session.commit()
    flash(_("Buchung gelöscht."), "success")
    return redirect(url_for("bulk.store_detail", store_id=store_id))
