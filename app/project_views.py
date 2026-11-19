"""Seiten für Projekte: Liste, Detail (Schritte, Tagebuch, Dateien, Kosten), Anlegen und Bearbeiten."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required

from . import projects, push, uploads
from .extensions import db
from .i18n import gettext as _
from .models import PROJECT_STATUSES, Project, ProjectDocument, ProjectEntry, Task, User
from .storage import StorageError
from .utils import admin_required, editor_required, flash_error, form_date, form_int, form_money, form_str

bp = Blueprint("projects", __name__)


def _get(project_id):
    return db.session.get(Project, project_id) or abort(404)


def _detail(project, anchor=""):
    return redirect(url_for("projects.project_detail", project_id=project.id) + anchor)


def _form_fields():
    owner_id = form_int("owner_id")
    owner = db.session.get(User, owner_id) if owner_id else None
    return dict(name=form_str("name", 150), description=form_str("description", 4000),
                status=form_str("status", 12) or "laeuft", owner=owner,
                members=projects.users_by_ids(request.form.getlist("member_ids")),
                start_on=form_date("start_on"), target_on=form_date("target_on"),
                budget=form_money("budget") if current_user.is_admin else None)


@bp.route("/projekte")
@login_required
def project_list():
    finished = request.args.get("fertig") == "1"
    return render_template("projects/list.html", cards=projects.overview(finished), finished=finished,
                           counts=projects.counts(), statuses=PROJECT_STATUSES, users=projects.assignable(),
                           can_create=projects.can_create(current_user), today=date.today())


@bp.route("/projekte/neu", methods=["POST"])
@editor_required
def project_create():
    if not projects.can_create(current_user):
        abort(403)
    try:
        project = projects.save_project(None, current_user, **_form_fields())
        db.session.commit()
        flash(_("Projekt „{name}“ angelegt.", name=project.name), "success")
        return _detail(project)
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("projects.project_list") + "#neu")


@bp.route("/projekt/<int:project_id>")
@login_required
def project_detail(project_id):
    project = _get(project_id)
    today = date.today()
    return render_template(
        "projects/detail.html", project=project, steps=projects.steps(project), card=projects.card(project, today),
        costs=projects.costs(project) if current_user.is_admin else None, statuses=PROJECT_STATUSES,
        users=projects.assignable(), can_manage=projects.can_manage(project, current_user),
        can_write=projects.can_write(project, current_user), today=today)


@bp.route("/projekt/<int:project_id>/bearbeiten", methods=["POST"])
@editor_required
def project_edit(project_id):
    project = _get(project_id)
    if not projects.can_manage(project, current_user):
        abort(403)
    try:
        projects.save_project(project, current_user, **_form_fields())
        db.session.commit()
        flash(_("Projekt „{name}“ gespeichert.", name=project.name), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _detail(project)


@bp.route("/projekt/<int:project_id>/loeschen", methods=["POST"])
@admin_required
def project_delete(project_id):
    project = _get(project_id)
    name = project.name
    names = projects.delete_project(project)
    db.session.commit()
    for filename in names:
        uploads.delete_document(filename)
    flash(_("Projekt „{name}“ gelöscht. Die Schritte bleiben als Aufgaben.", name=name), "success")
    return redirect(url_for("projects.project_list"))


# --- Schritte -----------------------------------------------------------------------

@bp.route("/projekt/<int:project_id>/schritt", methods=["POST"])
@editor_required
def step_add(project_id):
    project = _get(project_id)
    if not projects.can_manage(project, current_user):
        abort(403)
    try:
        task = projects.add_step(project, current_user, form_str("title", 200), due_on=form_date("due_on"),
                                 assignees=projects.users_by_ids(request.form.getlist("assignee_ids")),
                                 milestone=bool(request.form.get("milestone")),
                                 description=form_str("description", 4000))
        db.session.commit()
        if task.assignees:
            push.task_assigned(task, current_user)
        flash(_("Schritt „{title}“ angelegt.", title=task.title), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _detail(project, "#schritte")


@bp.route("/projekt/schritt/<int:task_id>/verschieben", methods=["POST"])
@editor_required
def step_move(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    if task.project is None or not projects.can_manage(task.project, current_user):
        abort(403)
    projects.move_step(task, -1 if request.form.get("dir") == "up" else 1)
    db.session.commit()
    return _detail(task.project, "#schritte")


# --- Tagebuch -----------------------------------------------------------------------

@bp.route("/projekt/<int:project_id>/tagebuch", methods=["POST"])
@editor_required
def entry_add(project_id):
    project = _get(project_id)
    if not projects.can_write(project, current_user):
        abort(403)
    try:
        entry = projects.add_entry(project, current_user, form_str("text", 4000),
                                   form_money("amount") if current_user.is_admin else None)
        db.session.commit()
        push.project_entry(entry, current_user)
        flash(_("Eintrag gespeichert."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _detail(project, "#tagebuch")


@bp.route("/projekt/tagebuch/<int:entry_id>/loeschen", methods=["POST"])
@editor_required
def entry_delete(entry_id):
    entry = db.session.get(ProjectEntry, entry_id) or abort(404)
    if not (current_user.is_admin or entry.user_id == current_user.id):
        abort(403)
    project = entry.project
    db.session.delete(entry)
    db.session.commit()
    flash(_("Eintrag gelöscht."), "success")
    return _detail(project, "#tagebuch")


# --- Dateien ------------------------------------------------------------------------

@bp.route("/projekt/<int:project_id>/datei", methods=["POST"])
@editor_required
def file_upload(project_id):
    project = _get(project_id)
    if not projects.can_write(project, current_user):
        abort(403)
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash_error(_("Bitte eine Datei auswählen."))
        return _detail(project, "#dateien")
    name = None
    try:
        name, mimetype, size = uploads.save_document(upload, office=True)
        title = form_str("title", 150) or upload.filename.rsplit(".", 1)[0][:150]
        db.session.add(ProjectDocument(project=project, title=title, filename=name, original_name=upload.filename[:200],
                                       mimetype=mimetype, size=size, user_id=current_user.id))
        db.session.commit()
        flash(_("„{title}“ hochgeladen.", title=title), "success")
    except StorageError as e:
        db.session.rollback()
        if name:
            uploads.delete_document(name)
        flash_error(str(e))
    return _detail(project, "#dateien")


@bp.route("/projekt/datei/<int:doc_id>")
@login_required
def file_get(doc_id):
    doc = db.session.get(ProjectDocument, doc_id) or abort(404)
    ext = doc.filename.rsplit(".", 1)[-1]
    response = send_from_directory(uploads.document_dir(), doc.filename, mimetype=doc.mimetype, max_age=86400,
                                   as_attachment=request.args.get("download") == "1",
                                   download_name=f"{doc.title[:80]}.{ext}")
    response.headers["Cache-Control"] = "private, max-age=86400"
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    return response


@bp.route("/projekt/datei/<int:doc_id>/loeschen", methods=["POST"])
@editor_required
def file_delete(doc_id):
    doc = db.session.get(ProjectDocument, doc_id) or abort(404)
    project = doc.project
    if not (current_user.is_admin or doc.user_id == current_user.id or projects.can_manage(project, current_user)):
        abort(403)
    db.session.delete(doc)
    db.session.commit()
    uploads.delete_document(doc.filename)
    flash(_("„{title}“ gelöscht.", title=doc.title), "success")
    return _detail(project, "#dateien")
