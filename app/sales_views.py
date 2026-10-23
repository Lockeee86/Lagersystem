"""Seiten für Verkauf: Lieferscheine, Verladen per Scan, Kunden, Anerkennungsnummern."""
from datetime import date

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import extract, func, or_
from sqlalchemy.exc import IntegrityError

from . import bulk, sales, storage, tasks
from .delivery_note import render_delivery_note
from .extensions import db
from .i18n import gettext as _, ngettext
from .models import (DELIVERY_STATUSES, SORT_USAGES, Box, BulkStore, Certification, Customer, Delivery,
                     DeliveryItem, SeedClass, Setting, Variety)
from .storage import StorageError
from .utils import admin_required, editor_required, flash_error, form_date, form_int, form_str

bp = Blueprint("sales", __name__)


def _get(model, obj_id):
    obj = db.session.get(model, obj_id)
    if obj is None:
        abort(404)
    return obj


def _to_delivery(delivery, anchor=None):
    url = url_for("sales.delivery_detail", delivery_id=delivery.id)
    return redirect(url + (f"#{anchor}" if anchor else ""))


def _active_customers(include_id=None):
    return (Customer.query.filter(or_(Customer.active.is_(True), Customer.id == include_id))
            .order_by(Customer.name).all())


# --- Lieferscheine ------------------------------------------------------------

@bp.route("/lieferscheine")
@login_required
def deliveries():
    query = Delivery.query
    status = request.args.get("status", "")
    if status in DELIVERY_STATUSES:
        query = query.filter(Delivery.status == status)
    customer_id = request.args.get("kunde", type=int)
    if customer_id:
        query = query.filter(Delivery.customer_id == customer_id)
    year = request.args.get("jahr", type=int)
    if year:
        query = query.filter(extract("year", Delivery.date) == year)
    page = query.order_by(Delivery.date.desc(), Delivery.id.desc()).paginate(per_page=50, error_out=False)
    return render_template("sales/deliveries.html", page=page, statuses=DELIVERY_STATUSES,
                           customers=Customer.query.order_by(Customer.name).all(),
                           active_customers=_active_customers(), years=sales.delivery_years(),
                           today=date.today().isoformat(), args=request.args)


@bp.route("/lieferscheine/neu", methods=["POST"])
@admin_required
def delivery_create():
    customer = db.session.get(Customer, form_int("customer_id", 0))
    if customer is None:
        flash_error(_("Bitte einen Kunden wählen."))
        return redirect(url_for("sales.deliveries"))
    delivery = sales.create_delivery(customer, form_date("date") or date.today(), current_user,
                                     vehicle=form_str("vehicle", 50), note=form_str("note", 2000))
    db.session.commit()
    flash(_("Lieferschein {number} angelegt.", number=delivery.number), "success")
    return _to_delivery(delivery)


@bp.route("/lieferschein/<int:delivery_id>")
@login_required
def delivery_detail(delivery_id):
    delivery = _get(Delivery, delivery_id)
    return render_template(
        "sales/delivery.html", delivery=delivery, lines=sales.summarize(delivery),
        open_tasks=tasks.for_delivery(delivery.id), today=date.today(),
        customers=_active_customers(delivery.customer_id),
        varieties=Variety.query.filter_by(active=True).order_by(Variety.name).all(),
        seed_classes=SeedClass.query.filter_by(active=True).order_by(SeedClass.position, SeedClass.name).all(),
        usages=SORT_USAGES,
        bulk_stores=[r for r in bulk.overview()[0] if r["stock"]["kg"] > 0] if delivery.is_open else [],
    )


@bp.route("/lieferschein/<int:delivery_id>/schuettgut", methods=["POST"])
@editor_required
def delivery_add_bulk(delivery_id):
    """Schüttgut auf den Lieferschein (bucht die Auslagerung am Lagerort)."""
    delivery = _get(Delivery, delivery_id)
    store = db.session.get(BulkStore, form_int("store_id", 0))
    try:
        if store is None:
            raise StorageError(_("Bitte einen Lagerort wählen."))
        sales.add_bulk(delivery, current_user, store,
                       net_kg=bulk.parse_number(request.form.get("net_kg"), 1, 10_000_000, integer=True),
                       gross_kg=bulk.parse_number(request.form.get("gross_kg"), 1, 10_000_000, integer=True),
                       tare_kg=bulk.parse_number(request.form.get("tare_kg"), 1, 10_000_000, integer=True),
                       bags=form_int("bags", minimum=1, maximum=10000))
        db.session.commit()
        flash(_("Schüttgut aus „{name}“ verladen.", name=store.name), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _to_delivery(delivery, "positionen")


@bp.route("/lieferschein/<int:delivery_id>/kopf", methods=["POST"])
@admin_required
def delivery_header(delivery_id):
    delivery = _get(Delivery, delivery_id)
    customer = db.session.get(Customer, form_int("customer_id", 0))
    if not delivery.is_open:
        flash_error(_("Lieferschein {number} ist abgeschlossen.", number=delivery.number))
    elif customer is None:
        flash_error(_("Bitte einen Kunden wählen."))
    else:
        delivery.customer = customer
        delivery.date = form_date("date") or delivery.date
        delivery.vehicle = form_str("vehicle", 50) or None
        delivery.note = form_str("note", 2000) or None
        db.session.commit()
        flash(_("{number} gespeichert.", number=delivery.number), "success")
    return _to_delivery(delivery)


@bp.route("/lieferschein/<int:delivery_id>/waage", methods=["POST"])
@admin_required
def delivery_weights(delivery_id):
    delivery = _get(Delivery, delivery_id)
    try:
        sales.set_weights(delivery, form_int("gross_kg", minimum=0), form_int("tare_kg", minimum=0))
        db.session.commit()
        flash(_("{number} gespeichert.", number=delivery.number), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _to_delivery(delivery, "waage")


@bp.route("/lieferschein/<int:delivery_id>/menge", methods=["POST"])
@admin_required
def delivery_add_quantity(delivery_id):
    delivery = _get(Delivery, delivery_id)
    variety_id = form_int("variety_id")
    seed_class_id = form_int("seed_class_id")
    usage = form_str("usage", 20)
    try:
        sales.add_quantity(
            delivery, current_user,
            variety_id=variety_id if variety_id and db.session.get(Variety, variety_id) else None,
            usage=usage if usage in SORT_USAGES else None,
            seed_class_id=seed_class_id if seed_class_id and db.session.get(SeedClass, seed_class_id) else None,
            grade=form_str("grade", 50), packaging=form_str("packaging", 60),
            quantity=form_int("quantity", minimum=1, maximum=100000),
            unit_kg=form_int("unit_kg", minimum=1, maximum=100000),
        )
        db.session.commit()
        flash(_("Position hinzugefügt."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _to_delivery(delivery, "positionen")


def _wants_json():
    return request.accept_mimetypes.best == "application/json"


@bp.route("/lieferschein/<int:delivery_id>/kiste", methods=["POST"])
@editor_required
def delivery_add_box(delivery_id):
    """Kiste verladen – vom Scanner per fetch (JSON) oder per Formular."""
    delivery = _get(Delivery, delivery_id)
    raw = request.form.get("code", "")
    code = storage.normalize_code(raw)
    box = Box.query.filter_by(code=code).first() if code else None
    try:
        if box is None:
            raise StorageError(_("Keine Kiste mit dem Code „{code}“ gefunden.", code=raw[:40]))
        if box.is_empty and (other := sales.find_open_item(box)):
            raise StorageError(_("{code} ist schon auf Lieferschein {number} verladen.",
                                 code=box.code, number=other.delivery.number))
        item, shifted = sales.add_box(delivery, box, current_user)
        db.session.commit()
    except (StorageError, IntegrityError) as e:
        db.session.rollback()
        message = str(e) if isinstance(e, StorageError) else _(
            "Der Platz wurde gerade von jemand anderem belegt. Bitte erneut versuchen.")
        if _wants_json():
            return jsonify(ok=False, message=message)
        flash_error(message)
        return redirect(url_for("sales.delivery_load", delivery_id=delivery.id))

    parts = [item.variety.name if item.variety else "", item.seed_class.name if item.seed_class else "",
             item.fill_label]
    message = _("{code} verladen.", code=box.code)
    if shifted:
        message += " " + _("Nachgerückt: {codes}.", codes=", ".join(b.code for b in shifted))
    if _wants_json():
        return jsonify(ok=True, message=message, item_id=item.id, code=box.code,
                       text=" · ".join(p for p in parts if p),
                       count_text=ngettext("{n} Kiste", "{n} Kisten", delivery.box_count))
    flash(message, "success")
    return redirect(url_for("sales.delivery_load", delivery_id=delivery.id))


@bp.route("/lieferschein/<int:delivery_id>/verladen")
@editor_required
def delivery_load(delivery_id):
    delivery = _get(Delivery, delivery_id)
    if not delivery.is_open:
        flash_error(_("Lieferschein {number} ist abgeschlossen.", number=delivery.number))
        return _to_delivery(delivery)
    boxes = [item for item in reversed(delivery.items) if item.box_id]
    return render_template("sales/load.html", delivery=delivery, boxes=boxes)


@bp.route("/lieferschein/<int:delivery_id>/position/<int:item_id>/entfernen", methods=["POST"])
@editor_required
def delivery_remove_item(delivery_id, item_id):
    delivery = _get(Delivery, delivery_id)
    item = _get(DeliveryItem, item_id)
    if item.delivery_id != delivery.id:
        abort(404)
    label = item.box.code if item.box else (item.crop or item.packaging or _("Position"))
    try:
        sales.remove_item(item, current_user)
        db.session.commit()
        if item.box_id:
            flash(_("{code} zurückgenommen. Bitte wieder einlagern.", code=label), "success")
        else:
            flash(_("Position entfernt."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    if request.form.get("back") == "verladen":
        return redirect(url_for("sales.delivery_load", delivery_id=delivery.id))
    return _to_delivery(delivery, "positionen")


@bp.route("/lieferschein/<int:delivery_id>/abschliessen", methods=["POST"])
@admin_required
def delivery_close(delivery_id):
    delivery = _get(Delivery, delivery_id)
    try:
        sales.close_delivery(delivery)
        db.session.commit()
        flash(_("Lieferschein {number} abgeschlossen.", number=delivery.number), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _to_delivery(delivery)


@bp.route("/lieferschein/<int:delivery_id>/oeffnen", methods=["POST"])
@admin_required
def delivery_reopen(delivery_id):
    delivery = _get(Delivery, delivery_id)
    sales.reopen_delivery(delivery)
    db.session.commit()
    flash(_("Lieferschein {number} ist wieder offen.", number=delivery.number), "success")
    return _to_delivery(delivery)


@bp.route("/lieferschein/<int:delivery_id>/loeschen", methods=["POST"])
@admin_required
def delivery_delete(delivery_id):
    delivery = _get(Delivery, delivery_id)
    try:
        sales.delete_delivery(delivery)
        db.session.commit()
        flash(_("Lieferschein {number} gelöscht.", number=delivery.number), "success")
        return redirect(url_for("sales.deliveries"))
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return _to_delivery(delivery)


@bp.route("/lieferschein/<int:delivery_id>.pdf")
@login_required
def delivery_pdf(delivery_id):
    delivery = _get(Delivery, delivery_id)
    pdf = render_delivery_note(delivery, sales.summarize(delivery), Setting.all())
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{delivery.number}.pdf"'})


# --- Kunden -----------------------------------------------------------------

@bp.route("/kunden", methods=["GET", "POST"])
@admin_required
def customers():
    if request.method == "POST":
        c_id = form_int("id")
        customer = _get(Customer, c_id) if c_id else Customer()
        name = form_str("name", 150)
        taken = Customer.query.filter(func.lower(Customer.name) == name.lower(),
                                      Customer.id != (customer.id or 0)).first()
        if not name:
            flash_error(_("Bitte einen Namen angeben."))
        elif taken:
            flash_error(_("Einen Kunden „{name}“ gibt es schon.", name=name))
        else:
            customer.name = name
            customer.number = form_str("number", 30) or None
            customer.address = form_str("address", 500) or None
            customer.phone = form_str("phone", 50) or None
            customer.email = form_str("email", 120) or None
            customer.note = form_str("note", 2000) or None
            customer.active = True
            db.session.add(customer)
            db.session.commit()
            flash(_("Kunde „{name}“ gespeichert.", name=customer.name), "success")
        return redirect(url_for("sales.customers"))
    return render_template("sales/customers.html", items=Customer.query.order_by(Customer.name).all(),
                           counts=sales.count_by_customer())


@bp.route("/kunden/<int:c_id>/loeschen", methods=["POST"])
@admin_required
def customer_delete(c_id):
    customer = _get(Customer, c_id)
    if customer.deliveries.first():
        customer.active = False
        flash(_("Kunde „{name}“ hat Lieferscheine und wurde deshalb nur deaktiviert.", name=customer.name),
              "success")
    else:
        db.session.delete(customer)
        flash(_("Kunde „{name}“ gelöscht.", name=customer.name), "success")
    db.session.commit()
    return redirect(url_for("sales.customers"))


# --- Anerkennungsnummern -------------------------------------------------------

@bp.route("/anerkennungen", methods=["GET", "POST"])
@admin_required
def certifications():
    if request.method == "POST":
        c_id = form_int("id")
        cert = _get(Certification, c_id) if c_id else Certification()
        variety = db.session.get(Variety, form_int("variety_id", 0))
        seed_class = db.session.get(SeedClass, form_int("seed_class_id", 0))
        year = form_int("year", minimum=2000, maximum=2100)
        number = form_str("number", 60)
        if not (variety and seed_class and year and number):
            flash_error(_("Bitte Sorte, Stufe, Erntejahr und Anerkennungsnummer angeben."))
            return redirect(url_for("sales.certifications"))
        cert.variety, cert.seed_class, cert.year, cert.number = variety, seed_class, year, number
        cert.note = form_str("note", 200) or None
        db.session.add(cert)
        try:
            db.session.commit()
            flash(_("Anerkennungsnummer gespeichert."), "success")
        except IntegrityError:
            db.session.rollback()
            flash_error(_("Für {variety} {seed_class} {year} gibt es schon eine Anerkennungsnummer.",
                          variety=variety.name, seed_class=seed_class.name, year=year))
        return redirect(url_for("sales.certifications"))
    items = (Certification.query.join(Variety).join(SeedClass)
             .order_by(Certification.year.desc(), Variety.name, SeedClass.position).all())
    return render_template(
        "sales/certifications.html", items=items, this_year=date.today().year,
        varieties=Variety.query.filter_by(active=True).order_by(Variety.name).all(),
        seed_classes=SeedClass.query.filter_by(active=True).order_by(SeedClass.position, SeedClass.name).all(),
    )


@bp.route("/anerkennungen/<int:c_id>/loeschen", methods=["POST"])
@admin_required
def certification_delete(c_id):
    db.session.delete(_get(Certification, c_id))
    db.session.commit()
    flash(_("Anerkennungsnummer gelöscht."), "success")
    return redirect(url_for("sales.certifications"))
