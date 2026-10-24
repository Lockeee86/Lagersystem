"""Seiten für Maschinen: Übersicht, Maschinenseite mit Wartungen und Logbuch, Fotos, QR-Etikett."""
from datetime import date

from flask import (Blueprint, Response, abort, flash, redirect, render_template, request, send_from_directory,
                   url_for)
from flask_login import current_user, login_required
from werkzeug.datastructures import MultiDict

from . import fuel, machines, purchases, push, sites, tasks, uploads
from .extensions import db
from .i18n import gettext as _
from .labels import LABEL_FORMATS, render_labels
from .models import (COUNTER_UNITS, DOCUMENT_KINDS, FUEL_CATEGORIES, MACHINE_CATEGORIES, MACHINE_EVENT_KINDS, FuelEntry, Machine,
                     MachineDocument, MachineEvent, MachinePart, MaintenanceTask, Photo, User)
from .storage import StorageError
from .utils import admin_required, editor_required, flash_error, form_date, form_int, form_money, form_str

bp = Blueprint("machines", __name__)


def _get(model, obj_id):
    obj = db.session.get(model, obj_id)
    if obj is None:
        abort(404)
    return obj


def _to_machine(machine, anchor=None):
    url = url_for("machines.machine_detail", machine_id=machine.id)
    return redirect(url + (f"#{anchor}" if anchor else ""))


def _form_cost():
    return form_money("cost")


def _run(machine, action, anchor=None, after=None):
    """Führt `action()` aus (speichert hochgeladene Fotos vorher), committet oder meldet den Fehler.
    `after()` läuft nach dem Commit (z. B. Push-Nachrichten)."""
    names = []
    try:
        names = uploads.save_photos(request.files.getlist("photos"))
        message = action(names)
        db.session.commit()
        if after:
            after()
        flash(message, "success")
    except StorageError as e:
        db.session.rollback()
        uploads.delete_files(names)
        flash_error(str(e))
    return _to_machine(machine, anchor)


# --- Übersicht --------------------------------------------------------------

@bp.route("/maschinen/kosten")
@admin_required
def machine_costs():
    """Kosten aller Maschinen in einem Jahr: Diesel, Reparaturen/Wartungen, Bestellungen, je Stunde/100 km."""
    year = request.args.get("jahr", type=int) or date.today().year
    items = sites.apply(Machine.query, Machine).order_by(Machine.name).all()
    rows, total, price = machines.cost_overview(year, items)
    years = list(range(date.today().year, date.today().year - 6, -1))
    return render_template("machines/costs.html", rows=rows, total=total, price=price, year=year, years=years)


@bp.route("/maschinen")
@login_required
def machine_list():
    show_all = request.args.get("alle") == "1"
    query = Machine.query if show_all else Machine.query.filter_by(active=True)
    query = sites.apply(query, Machine)
    items = query.order_by(Machine.name).all()
    groups = {}
    for m in items:
        groups.setdefault(m.category, []).append((m, *machines.machine_summary(m)))
    ordered = [(key, _(label), groups[key]) for key, label in MACHINE_CATEGORIES.items() if key in groups]
    return render_template("machines/list.html", groups=ordered, count=len(items), show_all=show_all,
                           categories=MACHINE_CATEGORIES, units=COUNTER_UNITS,
                           default_units=machines.DEFAULT_UNITS, due=machines.due_tasks(),
                           faults=machines.open_faults())


def _machine_fields():
    unit = form_str("counter_unit", 5)
    return dict(
        manufacturer=form_str("manufacturer", 100) or None, model=form_str("model", 100) or None,
        year=form_int("year", minimum=1900, maximum=2100), plate=form_str("plate", 30) or None,
        serial_no=form_str("serial_no", 100) or None, counter_unit=unit if unit in COUNTER_UNITS else None,
        note=form_str("note", 2000) or None, site_id=sites.from_form(request.form.get("site_id")),
    )


@bp.route("/maschinen/neu", methods=["POST"])
@admin_required
def machine_create():
    name = form_str("name", 100)
    category = form_str("category", 30)
    if not name or category not in MACHINE_CATEGORIES:
        flash_error(_("Bitte Name und Art der Maschine angeben."))
        return redirect(url_for("machines.machine_list"))
    fields = _machine_fields()
    machine = machines.create_machine(name, category, current_user,
                                      with_templates=bool(request.form.get("templates")), **fields)
    start = form_int("counter", minimum=0)
    if start is not None and machine.counter_unit:
        machines.set_counter(machine, start, current_user)
    db.session.commit()
    flash(_("{name} angelegt ({code}).", name=machine.name, code=machine.code), "success")
    return _to_machine(machine)


# --- Maschinenseite -----------------------------------------------------------

@bp.route("/maschine/<int:machine_id>")
@login_required
def machine_detail(machine_id):
    machine = _get(Machine, machine_id)
    cost_year = request.args.get("kosten", type=int) or date.today().year
    maintenance = sorted(((t, machines.task_status(t)) for t in machine.tasks),
                   key=lambda r: ({"overdue": 0, "soon": 1, "unknown": 2, "ok": 3}[r[1]["state"]], r[0].name))
    kind = request.args.get("art")
    events = machine.events
    if kind in MACHINE_EVENT_KINDS:
        events = events.filter(MachineEvent.kind == kind)
    page = events.paginate(per_page=30, error_out=False)
    total_cost = sum(e.cost or 0 for e in machine.events.filter(MachineEvent.cost.isnot(None)))
    open_tasks = tasks.for_machine(machine.id)
    page_args = MultiDict(request.args)
    page_args["machine_id"] = machine.id
    return render_template("machines/detail.html", machine=machine, tasks=maintenance, page=page, kind=kind,
                           page_args=page_args, open_tasks=open_tasks, today=date.today(),
                           assignable=tasks.assignable_users() if current_user.is_admin else tasks.request_recipients(),
                           machine_purchases=purchases.for_machine(machine.id),
                           counter_required=fuel.counter_required(),
                           cost_year=cost_year, cost=machines.costs(machine, cost_year) if current_user.is_admin else None,
                           fault_tasks={t.machine_event_id: t for t in open_tasks if t.machine_event_id},
                           maint_tasks={t.maintenance_task_id: t for t in open_tasks if t.maintenance_task_id},
                           kinds=MACHINE_EVENT_KINDS, categories=MACHINE_CATEGORIES, units=COUNTER_UNITS,
                           faults=[e for e in machines.open_faults() if e.machine_id == machine.id],
                           total_cost=total_cost, label_formats=LABEL_FORMATS,
                           doc_kinds=DOCUMENT_KINDS, suppliers=purchases.suppliers(),
                           fuel_recent=machine.fuel_entries.filter(FuelEntry.kind == "tanken")
                           .order_by(FuelEntry.date.desc(), FuelEntry.id.desc()).limit(5).all(),
                           fuel_consumption=fuel.consumption(machine), fuel_year=fuel.liters_in_year(machine.id))


@bp.route("/maschine/<int:machine_id>/bearbeiten", methods=["POST"])
@admin_required
def machine_edit(machine_id):
    machine = _get(Machine, machine_id)
    name = form_str("name", 100)
    category = form_str("category", 30)
    if not name or category not in MACHINE_CATEGORIES:
        flash_error(_("Bitte Name und Art der Maschine angeben."))
        return _to_machine(machine)
    machine.name, machine.category = name, category
    for key, value in _machine_fields().items():
        setattr(machine, key, value)
    machine.active = not request.form.get("inactive")
    # „Wird getankt“: nur speichern, wenn es von der Voreinstellung der Art abweicht
    if "refuels_form" in request.form:
        wanted = bool(request.form.get("refuels"))
        machine.refuels = None if wanted == (category in FUEL_CATEGORIES) else wanted
    db.session.commit()
    flash(_("{name} gespeichert.", name=machine.name), "success")
    return _to_machine(machine)


@bp.route("/maschine/<int:machine_id>/zaehler", methods=["POST"])
@editor_required
def machine_counter(machine_id):
    machine = _get(Machine, machine_id)

    def action(names):
        machines.set_counter(machine, form_int("counter", minimum=0, maximum=10_000_000), current_user,
                             form_date("date"), names)
        return _("Zählerstand {counter} {unit} gespeichert.", counter=machine.counter, unit=machine.unit_short)
    return _run(machine, action)


@bp.route("/maschine/<int:machine_id>/wartung/<int:task_id>", methods=["POST"])
@editor_required
def task_done(machine_id, task_id):
    machine = _get(Machine, machine_id)
    task = _get(MaintenanceTask, task_id)
    if task.machine_id != machine.id:
        abort(404)

    def action(names):
        tasks.close_linked(current_user, maintenance=task)
        machines.complete_task(task, current_user, form_date("date"),
                               form_int("counter", minimum=0, maximum=10_000_000), form_str("text", 2000),
                               _form_cost() if current_user.is_admin else None, names)
        return _("„{task}“ als erledigt eingetragen.", task=task.name)
    return _run(machine, action, "wartungen")


@bp.route("/maschine/<int:machine_id>/eintrag", methods=["POST"])
@editor_required
def machine_event(machine_id):
    machine = _get(Machine, machine_id)
    kind = form_str("kind", 20)
    created = {}

    def action(names):
        text = form_str("text", 4000)
        event = machines.add_event(machine, kind, current_user, form_date("date"), text,
                                   _form_cost() if current_user.is_admin else None,
                                   form_int("counter", minimum=0, maximum=10_000_000), names)
        created["event"] = event
        if kind == "stoerung" and request.form.get("as_task"):
            db.session.flush()
            ids = {int(v) for v in request.form.getlist("assignee_ids") if v.isdigit()}
            wanted = User.query.filter(User.id.in_(ids)).all() if ids else []
            created["task"] = tasks.create_task(
                current_user, title=tasks.title_for(machine, text), description=text,
                due_on=form_date("task_due") or date.today(), important=bool(request.form.get("task_important")),
                assignees=tasks.allowed_assignees(current_user, wanted),
                machine_id=machine.id, machine_event_id=event.id,
            )
            return _("Störung gemeldet und als Aufgabe angelegt.")
        if kind == "stoerung":
            return _("Störung gemeldet.")
        return _("Eintrag gespeichert.")

    def after():
        if kind != "stoerung":
            return
        task = created.get("task")
        if task is not None:
            push.task_assigned(task, current_user)
        # wer die Aufgabe schon bekommen hat, braucht die Störung nicht noch einmal
        push.fault_reported(created["event"], current_user, exclude=task.assignees if task else ())
    return _run(machine, action, "logbuch", after)


@bp.route("/maschine/<int:machine_id>/eintrag/<int:event_id>/erledigt", methods=["POST"])
@editor_required
def fault_resolved(machine_id, event_id):
    machine = _get(Machine, machine_id)
    event = _get(MachineEvent, event_id)
    if event.machine_id != machine.id:
        abort(404)
    try:
        machines.resolve_fault(event, current_user)
        tasks.close_linked(current_user, event=event)
        db.session.commit()
        flash(_("Störung als behoben markiert."), "success")
    except StorageError as e:
        flash_error(str(e))
    return _to_machine(machine, "logbuch")


@bp.route("/maschine/<int:machine_id>/eintrag/<int:event_id>/loeschen", methods=["POST"])
@admin_required
def event_delete(machine_id, event_id):
    machine = _get(Machine, machine_id)
    event = _get(MachineEvent, event_id)
    if event.machine_id != machine.id:
        abort(404)
    names = [p.filename for p in event.photos]
    db.session.delete(event)
    db.session.commit()
    uploads.delete_files(names)
    flash(_("Eintrag gelöscht."), "success")
    return _to_machine(machine, "logbuch")


# --- Wartungsintervalle -------------------------------------------------------

@bp.route("/maschine/<int:machine_id>/intervall", methods=["POST"])
@admin_required
def task_save(machine_id):
    machine = _get(Machine, machine_id)
    t_id = form_int("id")
    task = _get(MaintenanceTask, t_id) if t_id else MaintenanceTask(machine=machine)
    if task.machine_id not in (None, machine.id):
        abort(404)
    name = form_str("name", 100)
    hours = form_int("interval_hours", minimum=1, maximum=1_000_000)
    months = form_int("interval_months", minimum=1, maximum=240)
    if not name or not (hours or months):
        flash_error(_("Bitte Name und mindestens ein Intervall (Zähler oder Monate) angeben."))
        return _to_machine(machine, "wartungen")
    task.name, task.interval_hours, task.interval_months = name, hours, months
    task.last_done_on = form_date("last_done_on")
    task.last_done_counter = form_int("last_done_counter", minimum=0)
    task.note = form_str("note", 200) or None
    db.session.add(task)
    db.session.commit()
    flash(_("Wartung „{task}“ gespeichert.", task=task.name), "success")
    return _to_machine(machine, "wartungen")


@bp.route("/maschine/<int:machine_id>/intervall/<int:task_id>/loeschen", methods=["POST"])
@admin_required
def task_delete(machine_id, task_id):
    machine = _get(Machine, machine_id)
    task = _get(MaintenanceTask, task_id)
    if task.machine_id != machine.id:
        abort(404)
    MachineEvent.query.filter_by(task_id=task.id).update({"task_id": None})
    db.session.delete(task)
    db.session.commit()
    flash(_("Wartung „{task}“ gelöscht.", task=task.name), "success")
    return _to_machine(machine, "wartungen")


# --- Tanken an der Maschine ------------------------------------------------------

@bp.route("/maschine/<int:machine_id>/getankt", methods=["POST"])
@editor_required
def machine_refuel(machine_id):
    machine = _get(Machine, machine_id)
    before = None
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
    target = request.form.get("back", "")
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return _to_machine(machine, "diesel")


# --- Ersatzteile ----------------------------------------------------------------

@bp.route("/maschine/<int:machine_id>/teil", methods=["POST"])
@editor_required
def part_save(machine_id):
    machine = _get(Machine, machine_id)
    part_id = form_int("id")
    part = _get(MachinePart, part_id) if part_id else MachinePart()
    if part.machine_id not in (None, machine.id):
        abort(404)
    if part_id and request.form.get("delete"):
        if not current_user.is_admin:
            abort(403)
        db.session.delete(part)
        db.session.commit()
        flash(_("Teil „{name}“ gelöscht.", name=part.name), "success")
        return _to_machine(machine, "teile")
    name = form_str("name", 150)
    if not name:
        flash_error(_("Bitte angeben, was für ein Teil es ist."))
        return _to_machine(machine, "teile")
    supplier = purchases.supplier_by_name(form_str("supplier", 150))
    part.name, part.part_number = name, form_str("part_number", 80) or None
    part.manufacturer, part.quantity = form_str("manufacturer", 100) or None, form_str("quantity", 50) or None
    part.note, part.supplier_id = form_str("note", 1000) or None, supplier.id if supplier else None
    part.machine = machine
    db.session.add(part)
    db.session.commit()
    flash(_("Teil „{name}“ gespeichert.", name=part.name), "success")
    return _to_machine(machine, "teile")


# --- Dokumente ------------------------------------------------------------------

@bp.route("/maschine/<int:machine_id>/dokument", methods=["POST"])
@editor_required
def document_upload(machine_id):
    machine = _get(Machine, machine_id)
    upload = request.files.get("file")
    kind = form_str("kind", 20)
    if not upload or not upload.filename:
        flash_error(_("Bitte eine Datei auswählen."))
        return _to_machine(machine, "dokumente")
    name = None
    try:
        name, mimetype, size = uploads.save_document(upload)
        title = form_str("title", 150) or (upload.filename.rsplit(".", 1)[0][:150])
        db.session.add(MachineDocument(machine=machine, title=title, kind=kind if kind in DOCUMENT_KINDS else "sonstiges",
                                       filename=name, original_name=upload.filename[:200], mimetype=mimetype,
                                       size=size, user_id=current_user.id))
        db.session.commit()
        flash(_("„{title}“ hochgeladen.", title=title), "success")
    except StorageError as e:
        db.session.rollback()
        if name:
            uploads.delete_document(name)
        flash_error(str(e))
    return _to_machine(machine, "dokumente")


@bp.route("/dokument/<int:doc_id>")
@login_required
def document(doc_id):
    doc = _get(MachineDocument, doc_id)
    ext = doc.filename.rsplit(".", 1)[-1]
    download = f"{doc.title[:80]}.{ext}"
    response = send_from_directory(uploads.document_dir(), doc.filename, mimetype=doc.mimetype, max_age=86400,
                                   as_attachment=request.args.get("download") == "1", download_name=download)
    response.headers["Cache-Control"] = "private, max-age=86400"
    # Hochgeladene Dateien dürfen im Browser nichts ausführen
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    return response


@bp.route("/dokument/<int:doc_id>/loeschen", methods=["POST"])
@editor_required
def document_delete(doc_id):
    doc = _get(MachineDocument, doc_id)
    if not (current_user.is_admin or doc.user_id == current_user.id):
        abort(403)
    machine = doc.machine
    db.session.delete(doc)
    db.session.commit()
    uploads.delete_document(doc.filename)
    flash(_("„{title}“ gelöscht.", title=doc.title), "success")
    return _to_machine(machine, "dokumente")


# --- Fotos und QR -------------------------------------------------------------

@bp.route("/foto/<int:photo_id>")
@login_required
def photo(photo_id):
    item = _get(Photo, photo_id)
    response = send_from_directory(uploads.upload_dir(), item.filename, mimetype="image/jpeg", max_age=86400)
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


@bp.route("/maschine/<int:machine_id>/bild.jpg")
@login_required
def machine_image(machine_id):
    """Titelbild der Maschine; `?klein=1` = quadratische Vorschau für die Liste."""
    machine = _get(Machine, machine_id)
    if not machine.image:
        abort(404)
    name = uploads.thumb_name(machine.image) if request.args.get("klein") else machine.image
    response = send_from_directory(uploads.upload_dir(), name, mimetype="image/jpeg", max_age=86400)
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


@bp.route("/maschine/<int:machine_id>/bild", methods=["POST"])
@editor_required
def machine_image_save(machine_id):
    """Titelbild setzen, ersetzen (eine Datei) oder entfernen (`entfernen=1`)."""
    machine = _get(Machine, machine_id)
    old = machine.image
    if request.form.get("entfernen"):
        machine.image = None
        message = _("Bild entfernt.")
    else:
        upload = request.files.get("image")
        if not upload or not upload.filename:
            flash_error(_("Bitte ein Foto auswählen."))
            return redirect(url_for("machines.machine_detail", machine_id=machine.id))
        try:
            machine.image = uploads.save_with_thumb(upload)
        except StorageError as e:
            flash_error(str(e))
            return redirect(url_for("machines.machine_detail", machine_id=machine.id))
        message = _("Bild gespeichert.")
    db.session.commit()
    if old and old != machine.image:
        uploads.delete_files([old, uploads.thumb_name(old)])
    flash(message, "success")
    return redirect(url_for("machines.machine_detail", machine_id=machine.id))


@bp.route("/maschine/<int:machine_id>/etikett.pdf", methods=["POST"])
@admin_required
def machine_label(machine_id):
    machine = _get(Machine, machine_id)
    fmt = request.form.get("format", "2x4")
    if fmt not in LABEL_FORMATS:
        fmt = "2x4"
    count = form_int("count", 1, minimum=1, maximum=24)
    pdf = render_labels([machine.code] * count, fmt, skip=form_int("skip", 0, minimum=0, maximum=100))
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="etikett_{machine.code}.pdf"'})
