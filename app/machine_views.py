"""Seiten für Maschinen: Übersicht, Maschinenseite mit Wartungen und Logbuch, Fotos, QR-Etikett."""
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import (Blueprint, Response, abort, flash, redirect, render_template, request, send_from_directory,
                   url_for)
from flask_login import current_user, login_required
from werkzeug.datastructures import MultiDict

from . import machines, tasks, uploads
from .extensions import db
from .i18n import gettext as _
from .labels import LABEL_FORMATS, render_labels
from .models import (COUNTER_UNITS, MACHINE_CATEGORIES, MACHINE_EVENT_KINDS, Machine, MachineEvent,
                     MaintenanceTask, Photo)
from .storage import StorageError
from .utils import admin_required, editor_required, flash_error, form_date, form_int, form_str

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
    raw = form_str("cost", 20).replace("€", "").replace(" ", "")
    if not raw:
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        value = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise StorageError(_("Bitte die Kosten als Zahl angeben, z. B. 125,50."))
    if value < 0 or value > Decimal("9999999"):
        raise StorageError(_("Bitte die Kosten als Zahl angeben, z. B. 125,50."))
    return value


def _run(machine, action, anchor=None):
    """Führt `action()` aus (speichert hochgeladene Fotos vorher), committet oder meldet den Fehler."""
    names = []
    try:
        names = uploads.save_photos(request.files.getlist("photos"))
        message = action(names)
        db.session.commit()
        flash(message, "success")
    except StorageError as e:
        db.session.rollback()
        uploads.delete_files(names)
        flash_error(str(e))
    return _to_machine(machine, anchor)


# --- Übersicht --------------------------------------------------------------

@bp.route("/maschinen")
@login_required
def machine_list():
    show_all = request.args.get("alle") == "1"
    query = Machine.query if show_all else Machine.query.filter_by(active=True)
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
        note=form_str("note", 2000) or None,
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
    maintenance = sorted(((t, machines.task_status(t)) for t in machine.tasks),
                   key=lambda r: ({"overdue": 0, "soon": 1, "unknown": 2, "ok": 3}[r[1]["state"]], r[0].name))
    kind = request.args.get("art")
    events = machine.events
    if kind in MACHINE_EVENT_KINDS:
        events = events.filter(MachineEvent.kind == kind)
    page = events.paginate(per_page=30, error_out=False)
    total_cost = sum(e.cost or 0 for e in machine.events.filter(MachineEvent.cost.isnot(None)))
    page_args = MultiDict(request.args)
    page_args["machine_id"] = machine.id
    return render_template("machines/detail.html", machine=machine, tasks=maintenance, page=page, kind=kind,
                           page_args=page_args, open_tasks=tasks.for_machine(machine.id), today=date.today(),
                           kinds=MACHINE_EVENT_KINDS, categories=MACHINE_CATEGORIES, units=COUNTER_UNITS,
                           faults=[e for e in machines.open_faults() if e.machine_id == machine.id],
                           total_cost=total_cost, label_formats=LABEL_FORMATS)


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

    def action(names):
        machines.add_event(machine, kind, current_user, form_date("date"), form_str("text", 4000),
                           _form_cost() if current_user.is_admin else None,
                           form_int("counter", minimum=0, maximum=10_000_000), names)
        if kind == "stoerung":
            return _("Störung gemeldet.")
        return _("Eintrag gespeichert.")
    return _run(machine, action, "logbuch")


@bp.route("/maschine/<int:machine_id>/eintrag/<int:event_id>/erledigt", methods=["POST"])
@editor_required
def fault_resolved(machine_id, event_id):
    machine = _get(Machine, machine_id)
    event = _get(MachineEvent, event_id)
    if event.machine_id != machine.id:
        abort(404)
    try:
        machines.resolve_fault(event, current_user)
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


# --- Fotos und QR -------------------------------------------------------------

@bp.route("/foto/<int:photo_id>")
@login_required
def photo(photo_id):
    item = _get(Photo, photo_id)
    response = send_from_directory(uploads.upload_dir(), item.filename, mimetype="image/jpeg", max_age=86400)
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


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
