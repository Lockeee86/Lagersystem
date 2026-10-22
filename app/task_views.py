"""Seiten für Aufgaben: Liste (meine / alle / je Person), Detail mit Abhaken, Anlegen und Bearbeiten."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import tasks, uploads
from .extensions import db
from .i18n import gettext as _
from .models import DELIVERY_OPEN, RECURRENCES, Delivery, Machine, Task, User, Warehouse
from .storage import StorageError
from .utils import admin_required, editor_required, flash_error, form_date, form_int, form_str

bp = Blueprint("tasks", __name__)


def _get_task(task_id):
    task = db.session.get(Task, task_id)
    if task is None:
        abort(404)
    return task


def _choices(task=None):
    """Auswahllisten für das Formular (aktive Maschinen, offene Lieferscheine + der bisherige Bezug)."""
    machines = Machine.query.filter((Machine.active.is_(True)) | (Machine.id == (task.machine_id if task else None)))
    deliveries = Delivery.query.filter((Delivery.status == DELIVERY_OPEN) |
                                       (Delivery.id == (task.delivery_id if task else None)))
    return dict(
        users=tasks.assignable_users(), recurrences=RECURRENCES,
        machines=machines.order_by(Machine.name).all(),
        deliveries=deliveries.order_by(Delivery.date.desc()).all(),
        warehouses=Warehouse.query.order_by(Warehouse.name).all(),
    )


def _fields_from_form():
    ids = {int(v) for v in request.form.getlist("assignee_ids") if v.isdigit()}
    machine_id = form_int("machine_id")
    delivery_id = form_int("delivery_id")
    # Lagerplatz als "lager_id:reihe" (Reihe leer = ganzes Lager)
    wh_part, _sep, row_part = form_str("place", 20).partition(":")
    warehouse = db.session.get(Warehouse, int(wh_part)) if wh_part.isdigit() else None
    row = int(row_part) if row_part.isdigit() else None
    recurrence = form_str("recurrence", 20)
    return dict(
        title=form_str("title", 200), description=form_str("description", 4000),
        due_on=form_date("due_on"), important=bool(request.form.get("important")),
        recurrence=recurrence if recurrence in RECURRENCES else None,
        assignees=User.query.filter(User.id.in_(ids)).all() if ids else [],
        machine_id=machine_id if machine_id and db.session.get(Machine, machine_id) else None,
        delivery_id=delivery_id if delivery_id and db.session.get(Delivery, delivery_id) else None,
        warehouse_id=warehouse.id if warehouse else None,
        row=row if warehouse and row and row <= warehouse.rows else None,
    )


def _back(default):
    target = request.form.get("back", "")
    return redirect(target if target.startswith("/") and not target.startswith("//") else default)


@bp.route("/aufgaben")
@login_required
def task_list():
    # Wessen Aufgaben? Mitarbeiter/Betrachter standardmäßig die eigenen, der Admin alle
    who = request.args.get("wer") or ("alle" if current_user.is_admin else "ich")
    person = None
    if who == "ich":
        person = current_user
    elif who.isdigit():
        person = db.session.get(User, int(who))
        if person is None:
            abort(404)
    prefill = {
        "machine_id": request.args.get("maschine", type=int),
        "delivery_id": request.args.get("lieferschein", type=int),
    }
    return render_template(
        "tasks/list.html", groups=tasks.buckets(tasks.open_tasks(person)), done=tasks.recently_done(person),
        who=who, person=person, today=date.today(), prefill=prefill,
        show_form=current_user.is_admin and request.args.get("neu") == "1", **_choices(),
    )


@bp.route("/aufgaben/neu", methods=["POST"])
@admin_required
def task_create():
    try:
        task = tasks.create_task(current_user, **_fields_from_form())
        db.session.commit()
        flash(_("Aufgabe „{title}“ angelegt.", title=task.title), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _back(url_for("tasks.task_list"))


@bp.route("/aufgabe/<int:task_id>")
@login_required
def task_detail(task_id):
    task = _get_task(task_id)
    follow_up = Task.query.filter_by(previous_id=task.id).first()
    return render_template("tasks/detail.html", task=task, follow_up=follow_up, **_choices(task))


@bp.route("/aufgabe/<int:task_id>/bearbeiten", methods=["POST"])
@admin_required
def task_edit(task_id):
    task = _get_task(task_id)
    try:
        tasks.apply_fields(task, **_fields_from_form())
        db.session.commit()
        flash(_("Aufgabe „{title}“ gespeichert.", title=task.title), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("tasks.task_detail", task_id=task.id))


@bp.route("/aufgabe/<int:task_id>/erledigt", methods=["POST"])
@editor_required
def task_done(task_id):
    task = _get_task(task_id)
    if not (current_user.is_admin or task.is_for(current_user)):
        abort(403)
    names = []
    try:
        names = uploads.save_photos(request.files.getlist("photos"))
        follow_up = tasks.complete_task(task, current_user, form_str("note", 4000), names)
        db.session.commit()
        message = _("„{title}“ erledigt.", title=task.title)
        if follow_up:
            message += " " + _("Nächster Termin: {date}.", date=follow_up.due_on.strftime("%d.%m.%Y"))
        flash(message, "success")
    except StorageError as e:
        db.session.rollback()
        uploads.delete_files(names)
        flash_error(str(e))
    return _back(url_for("tasks.task_list"))


@bp.route("/aufgabe/<int:task_id>/oeffnen", methods=["POST"])
@admin_required
def task_reopen(task_id):
    task = _get_task(task_id)
    tasks.reopen_task(task)
    db.session.commit()
    flash(_("„{title}“ ist wieder offen.", title=task.title), "success")
    return redirect(url_for("tasks.task_detail", task_id=task.id))


@bp.route("/aufgabe/<int:task_id>/loeschen", methods=["POST"])
@admin_required
def task_delete(task_id):
    task = _get_task(task_id)
    names = [p.filename for p in task.photos]
    Task.query.filter_by(previous_id=task.id).update({"previous_id": None})
    db.session.delete(task)
    db.session.commit()
    uploads.delete_files(names)
    flash(_("Aufgabe „{title}“ gelöscht.", title=task.title), "success")
    return redirect(url_for("tasks.task_list"))
