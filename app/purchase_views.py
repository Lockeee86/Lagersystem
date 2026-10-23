"""Seiten für Bestellungen: Liste, Bestellwunsch anlegen (mit Fotos), Bearbeiten durch den Besteller."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import purchases, push, uploads
from .extensions import db
from .i18n import gettext as _
from .models import PURCHASE_OPEN, PURCHASE_STATUSES, Machine, MachineEvent, PurchaseRequest, User
from .storage import StorageError
from .utils import editor_required, flash_error, form_date, form_int, form_money, form_str

bp = Blueprint("purchases", __name__)

FILTERS = {"offen": PURCHASE_OPEN, "alle": tuple(PURCHASE_STATUSES)}


def _get(req_id):
    req = db.session.get(PurchaseRequest, req_id)
    if req is None:
        abort(404)
    return req


@bp.route("/bestellungen")
@login_required
def purchase_list():
    status = request.args.get("status", "offen")
    statuses = FILTERS.get(status) or ((status,) if status in PURCHASE_STATUSES else PURCHASE_OPEN)
    query = PurchaseRequest.query.filter(PurchaseRequest.status.in_(statuses))
    # wer=ich: selbst gemeldet · wer=zustaendig: vom Besteller übernommen · wer=frei: noch niemand zuständig
    who = request.args.get("wer") if request.args.get("wer") in ("ich", "zustaendig", "frei") else None
    if who == "ich":
        query = query.filter(PurchaseRequest.requested_by_id == current_user.id)
    elif who == "zustaendig":
        query = query.filter(PurchaseRequest.assignee_id == current_user.id)
    elif who == "frei":
        query = query.filter(PurchaseRequest.assignee_id.is_(None))
    items = query.order_by(PurchaseRequest.urgent.desc(), PurchaseRequest.created_at.desc()).limit(300).all()
    order = {s: i for i, s in enumerate(PURCHASE_STATUSES)}
    items.sort(key=lambda r: (order.get(r.status, 9), not r.urgent))
    event = db.session.get(MachineEvent, request.args.get("stoerung", type=int) or 0)
    prefill = {
        "machine_id": request.args.get("maschine", type=int) or (event.machine_id if event else None),
        "machine_event_id": event.id if event else None,
    }
    return render_template(
        "purchases/list.html", items=items, status=status, who=who, statuses=PURCHASE_STATUSES,
        machines=Machine.query.filter_by(active=True).order_by(Machine.name).all(), prefill=prefill,
        show_form=request.args.get("neu") == "1", buyers=purchases.buyers(), today=date.today(),
    )


@bp.route("/bestellungen/neu", methods=["POST"])
@editor_required
def purchase_create():
    names = []
    try:
        names = uploads.save_photos(request.files.getlist("photos"))
        machine_id = form_int("machine_id")
        event_id = form_int("machine_event_id")
        req = purchases.create_request(
            current_user, form_str("title", 200), form_str("details", 4000), form_str("quantity", 50),
            bool(request.form.get("urgent")),
            machine_id=machine_id if machine_id and db.session.get(Machine, machine_id) else None,
            machine_event_id=event_id if event_id and db.session.get(MachineEvent, event_id) else None,
            photo_names=names,
        )
        db.session.commit()
        push.purchase_requested(req, current_user)
        flash(_("Bestellwunsch „{title}“ abgeschickt.", title=req.title), "success")
        return redirect(url_for("purchases.purchase_detail", req_id=req.id))
    except StorageError as e:
        db.session.rollback()
        uploads.delete_files(names)
        flash_error(str(e))
        return redirect(url_for("purchases.purchase_list", neu=1))


@bp.route("/bestellung/<int:req_id>")
@login_required
def purchase_detail(req_id):
    return render_template("purchases/detail.html", req=_get(req_id), statuses=PURCHASE_STATUSES,
                           buyers=purchases.buyers())


@bp.route("/bestellung/<int:req_id>/uebernehmen", methods=["POST"])
@login_required
def purchase_assign(req_id):
    """Selbst übernehmen (jeder Besteller) oder weitergeben (wer zuständig ist und der Admin)."""
    req = _get(req_id)
    if not current_user.is_buyer:
        abort(403)
    target_id = form_int("assignee_id")
    target = db.session.get(User, target_id) if target_id else current_user
    if target != current_user and not req.can_handle(current_user):
        abort(403)
    try:
        purchases.assign(req, current_user, target)
        db.session.commit()
        if target != current_user:
            push.purchase_handed_over(req, current_user, target)
        if target == current_user:
            flash(_("Du kümmerst dich um „{title}“.", title=req.title), "success")
        else:
            flash(_("„{title}“ an {name} weitergegeben.", title=req.title, name=target.name), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("purchases.purchase_detail", req_id=req.id))


@bp.route("/bestellung/<int:req_id>/bearbeiten", methods=["POST"])
@login_required
def purchase_update(req_id):
    req = _get(req_id)
    if not req.can_handle(current_user):
        abort(403)
    old_status = req.status
    try:
        purchases.update_request(
            req, current_user, form_str("status", 20), supplier=form_str("supplier", 150),
            order_number=form_str("order_number", 80), price=form_money("price"),
            ordered_on=form_date("ordered_on"), expected_on=form_date("expected_on"),
            delivered_on=form_date("delivered_on"), buyer_note=form_str("buyer_note", 4000),
            inquired_on=form_date("inquired_on"),
        )
        db.session.commit()
        if req.status != old_status:
            push.purchase_status(req, current_user)
        flash(_("Bestellung „{title}“: {status}.", title=req.title, status=req.status_label), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("purchases.purchase_detail", req_id=req.id))


@bp.route("/bestellung/<int:req_id>/erledigt", methods=["POST"])
@editor_required
def purchase_done(req_id):
    req = _get(req_id)
    if not (req.can_handle(current_user) or req.requested_by_id == current_user.id):
        abort(403)
    try:
        purchases.mark_done(req, current_user)
        db.session.commit()
        flash(_("Bestellung „{title}“: {status}.", title=req.title, status=req.status_label), "success")
    except StorageError as e:
        flash_error(str(e))
    return redirect(url_for("purchases.purchase_detail", req_id=req.id))


@bp.route("/bestellung/<int:req_id>/loeschen", methods=["POST"])
@editor_required
def purchase_delete(req_id):
    """Zurückziehen, solange noch niemand etwas unternommen hat (Anfragender) – Admins dürfen immer."""
    req = _get(req_id)
    if not (current_user.is_admin or (req.requested_by_id == current_user.id and req.status == "neu")):
        abort(403)
    names = [p.filename for p in req.photos]
    db.session.delete(req)
    db.session.commit()
    uploads.delete_files(names)
    flash(_("Bestellwunsch „{title}“ gelöscht.", title=req.title), "success")
    return redirect(url_for("purchases.purchase_list"))
