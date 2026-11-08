"""Seiten fürs Team: Übergabe-Notizen, Tagesplan, Unterweisungen (Logik in team.py)."""
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required

from . import machines, team, uploads
from .extensions import db
from .i18n import gettext as _, ngettext
from .instruction_pdf import render_instruction_record
from .models import (Handover, InstructionDocument, InstructionRecord, InstructionTopic, Machine, Plot, Setting, Task,
                     User)
from .storage import StorageError
from .utils import admin_required, back, back_target, editor_required, flash_error, flash_undo, form_date, form_int, form_str, local_tz

bp = Blueprint("team", __name__)


def planner_required(view):
    """Tagesplan pflegen: Admin oder Mitarbeiter mit „Darf den Tagesplan pflegen“."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_planner:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _get(model, obj_id):
    obj = db.session.get(model, obj_id)
    if obj is None:
        abort(404)
    return obj


# --- Übergabe ----------------------------------------------------------------

@bp.route("/uebergabe", methods=["GET", "POST"])
@login_required
def handovers():
    if request.method == "POST":
        if not current_user.can_edit:
            abort(403)
        try:
            note = team.add_handover(current_user, request.form.get("text"))
            db.session.commit()
            flash(_("Übergabe gespeichert."), "success")
            team.handover_posted(note)
        except StorageError as e:
            db.session.rollback()
            flash_error(str(e))
        return back("team.handovers")
    return render_template("team/handovers.html", notes=team.recent_handovers(), handover_days=team.HANDOVER_DAYS)


@bp.route("/uebergabe/<int:note_id>/erledigt", methods=["POST"])
@editor_required
def handover_done(note_id):
    try:
        team.close_handover(_get(Handover, note_id), current_user)
        db.session.commit()
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return back("team.handovers")


# --- Tagesplan ---------------------------------------------------------------

def _default_day():
    """Nachmittags plant man meist den nächsten Tag."""
    now = datetime.now(local_tz())
    return now.date() + timedelta(days=1) if current_user.is_planner and now.hour >= 14 else now.date()


def _form_day():
    return form_date("datum") or date.today()


def _to_plan(day, anchor=None):
    return redirect(url_for("team.day_plan", datum=day.isoformat()) + (f"#{anchor}" if anchor else ""))


def _day_arg():
    try:
        return date.fromisoformat(request.args["datum"]) if request.args.get("datum") else _default_day()
    except ValueError:
        return date.today()


def _ids(name):
    return [int(v) for v in request.form.getlist(name) if v.isdigit()]


def _open_context(day):
    """Gemeinsam für „Heute“ und „Offen“: offene Aufgaben, Vorschläge (Planer), Maschinen fürs Notieren."""
    return dict(
        open=team.open_tasks(day, current_user) if current_user.can_edit else [],
        suggestions=team.suggestions(day, current_user) if current_user.is_planner else [],
        note_machines=machines.visible(Machine.query.filter_by(active=True), current_user).order_by(Machine.name).all()
        if current_user.can_edit else [],
        note_plots=Plot.query.filter_by(active=True).order_by(Plot.name).all() if current_user.can_edit else [],
    )


@bp.route("/tagesplan")
@login_required
def day_plan():
    """Aufgaben → Heute: je Person der Tag; links „Offen“ zum Verteilen (Planer) bzw. „Meine Aufgaben“ (Mitarbeiter)."""
    day = _day_arg()
    today = date.today()
    plan = team.plan_for(day, today)
    members = team.team_members()
    # Leute mit Einträgen zuerst, dann die übrigen; der eigene Plan ganz oben
    members.sort(key=lambda u: (u.id != current_user.id, u.id not in plan))
    recent = []
    if current_user.is_planner:                  # Vorschläge fürs Textfeld: zuletzt verwendete Einträge
        for (text,) in (db.session.query(Task.title).filter(Task.plan_day >= day - timedelta(days=60))
                        .group_by(Task.title).order_by(db.func.max(Task.id).desc()).limit(40)):
            recent.append(text)
    return render_template(
        "team/day_plan.html", day=day, plan=plan, week=team.week_summary(day, current_user), members=members, today=today, timedelta1=timedelta(days=1),
        recent_texts=recent, sent=db.session.get(Setting, f"push_plan_sent_{day.isoformat()}") is not None,
        previous=team.last_planned_day(day) if current_user.is_planner else None,
        machines=Machine.query.filter_by(active=True).order_by(Machine.name).all() if current_user.is_planner else [],
        plots=Plot.query.filter_by(active=True).order_by(Plot.name).all() if current_user.is_planner else [],
        **_open_context(day),
    )


@bp.route("/aufgaben/offen")
@editor_required
def open_page():
    """Aufgaben → Offen: alles Notierte und Fällige, das noch niemand eingeplant hat."""
    day = date.today()
    return render_template("team/open.html", day=day, today=day, members=team.team_members(), **_open_context(day))


def _back_or_plan(day, anchor=None):
    if target := back_target():
        return redirect(target)
    return _to_plan(day, anchor)


@bp.route("/tagesplan/eintrag", methods=["POST"])
@planner_required
def plan_add():
    day = _form_day()
    user = db.session.get(User, form_int("user_id", 0))
    try:
        team.add_plan_entry(day, user, request.form.get("text"), current_user,
                            machine_ids=_ids("machine_ids") + _ids("machine_id"), plot_ids=_ids("plot_ids"))
        db.session.commit()
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _to_plan(day, f"p{user.id}" if user else None)


@bp.route("/aufgaben/notieren", methods=["POST"])
@editor_required
def open_add():
    """Kurz notieren – landet unter „Offen“."""
    try:
        team.add_open_task(request.form.get("text"), current_user, important=bool(request.form.get("important")),
                           machine_ids=_ids("machine_ids") + _ids("machine_id"), plot_ids=_ids("plot_ids"))
        db.session.commit()
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _back_or_plan(_form_day(), "offen")


@bp.route("/aufgabe/<int:task_id>/einplanen", methods=["POST"])
@editor_required
def task_plan(task_id):
    """Einplanen: Planer wählen die Person, ein Mitarbeiter nimmt es sich selbst („Mach ich“)."""
    task = _get(Task, task_id)
    day = _form_day()
    user = db.session.get(User, form_int("user_id", 0)) or current_user
    try:
        team.plan_task(task, day, user, current_user)
        db.session.commit()
        if user.id == current_user.id and not current_user.is_planner:
            flash_undo(_("Übernommen: {title}", title=task.title), url_for("team.task_giveback", task_id=task.id),
                       back=back_target() or "")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _back_or_plan(day, f"p{user.id}")


@bp.route("/aufgabe/<int:task_id>/zurueck", methods=["POST"])
@planner_required
def task_unplan(task_id):
    """Aus dem Tagesplan zurück nach „Offen“."""
    task = _get(Task, task_id)
    day = task.plan_day or _form_day()
    team.unplan_task(task)
    db.session.commit()
    return _back_or_plan(day, "offen")


@bp.route("/aufgabe/<int:task_id>/zuruecklegen", methods=["POST"])
@editor_required
def task_giveback(task_id):
    """Eigenes „Mach ich“ zurücknehmen (Rückgängig im Hinweis) – nur offen und nur eigene."""
    task = _get(Task, task_id)
    if task.done_at or not task.plan_user or task.plan_user.id != current_user.id:
        abort(403)
    day = task.plan_day or _form_day()
    team.unplan_task(task)
    db.session.commit()
    return _back_or_plan(day, "offen")


@bp.route("/aufgabe/<int:task_id>/hat-sich-erledigt", methods=["POST"])
@editor_required
def task_settled(task_id):
    """Ohne Einplanen erledigt."""
    from . import tasks
    task = _get(Task, task_id)
    if not team.can_change(task, current_user):
        abort(403)
    if not task.is_done:
        tasks.complete_task(task, current_user)
    db.session.commit()
    return _back_or_plan(_form_day(), "offen")


@bp.route("/aufgabe/<int:task_id>/weg", methods=["POST"])
@editor_required
def task_remove(task_id):
    """Notiz löschen (Planer oder wer sie notiert hat)."""
    task = _get(Task, task_id)
    if not team.can_change(task, current_user):
        abort(403)
    names = [ph.filename for ph in task.photos]
    db.session.delete(task)
    db.session.commit()
    uploads.delete_files(names)
    return _back_or_plan(_form_day(), "offen")


@bp.route("/tagesplan/vorschlag", methods=["POST"])
@planner_required
def plan_suggestion():
    day = _form_day()
    user = db.session.get(User, form_int("user_id", 0))
    try:
        team.add_suggestion(day, user, request.form.get("key", ""), current_user)
        db.session.commit()
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _back_or_plan(day, "offen")


@bp.route("/tagesplan/uebernehmen", methods=["POST"])
@planner_required
def plan_copy():
    day = _form_day()
    source = team.last_planned_day(day)
    try:
        n = team.copy_plan(source, day, current_user)
        db.session.commit()
        flash(ngettext("{n} Eintrag vom {date} übernommen.", "{n} Einträge vom {date} übernommen.", n,
                       date=source.strftime("%d.%m.")) if n else _("Alle haben schon einen Plan."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _to_plan(day)


@bp.route("/tagesplan/eintrag/<int:task_id>/erledigt", methods=["POST"])
@editor_required
def plan_check(task_id):
    task = _get(Task, task_id)
    try:
        team.toggle_plan_entry(task, current_user)
        db.session.commit()
        if task.done_at:
            flash_undo(_("Erledigt: {title}", title=task.title), url_for("team.plan_check", task_id=task.id),
                       back=back_target() or "")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    user = task.plan_user
    return back("team.day_plan", datum=(task.plan_day or date.today()).isoformat(),
                _anchor=f"p{user.id}" if user else None)


@bp.route("/tagesplan/verschicken", methods=["POST"])
@planner_required
def plan_send():
    day = _form_day()
    if not team.plan_for(day):
        flash_error(_("Für diesen Tag ist noch niemand eingeteilt."))
        return _to_plan(day)
    Setting.put(f"push_plan_sent_{day.isoformat()}", "1")       # nur zur Anzeige „verschickt“
    db.session.commit()
    n = team.send_plan(day, current_user)
    flash(ngettext("Plan an {n} Person aufs Handy geschickt.", "Plan an {n} Personen aufs Handy geschickt.", n)
          if n else _("Niemand hat Push-Nachrichten für den Tagesplan eingeschaltet."), "success" if n else "warning")
    return _to_plan(day)


# --- Unterweisungen ----------------------------------------------------------

@bp.route("/unterweisungen")
@admin_required
def instructions():
    existing = {t.name.lower() for t in InstructionTopic.query}
    suggestions = [(i, _(name), months) for i, (name, months, _c) in enumerate(team.INSTRUCTION_SUGGESTIONS)
                   if _(name).lower() not in existing]
    inactive = InstructionTopic.query.filter_by(active=False).order_by(InstructionTopic.name).all()
    return render_template("team/instructions.html", rows=team.overview(), suggestions=suggestions,
                           inactive=inactive, members=team.team_members())


def _topic_from_form(topic):
    return team.save_topic(topic, request.form.get("name"), form_int("interval_months", 12),
                           request.form.get("content"), request.form.get("for_all") == "1",
                           [int(v) for v in request.form.getlist("user_ids") if v.isdigit()],
                           active=topic is None or request.form.get("active") == "1")


@bp.route("/unterweisungen/neu", methods=["POST"])
@admin_required
def instruction_create():
    try:
        index = form_int("vorschlag")
        if index is not None and 0 <= index < len(team.INSTRUCTION_SUGGESTIONS):
            name, months, content = team.INSTRUCTION_SUGGESTIONS[index]
            topic = team.save_topic(None, _(name), months, _(content), True, [])
        else:
            topic = _topic_from_form(None)
        db.session.commit()
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("team.instructions"))
    flash(_("{name} angelegt.", name=topic.name), "success")
    return redirect(url_for("team.instruction_detail", topic_id=topic.id))


@bp.route("/unterweisung/<int:topic_id>", methods=["GET", "POST"])
@admin_required
def instruction_detail(topic_id):
    topic = _get(InstructionTopic, topic_id)
    if request.method == "POST":
        try:
            _topic_from_form(topic)
            db.session.commit()
            flash(_("{name} gespeichert.", name=topic.name), "success")
        except StorageError as e:
            db.session.rollback()
            flash_error(str(e))
        return redirect(url_for("team.instruction_detail", topic_id=topic.id))
    last = team.last_records(topic)
    people = [(u, team.status(topic, u, last)) for u in team.participants(topic)]
    years = sorted({r.day.year for r in topic.records}, reverse=True)
    return render_template("team/instruction.html", topic=topic, people=people, years=years,
                           members=team.team_members(), today=date.today())


@bp.route("/unterweisung/<int:topic_id>/unterschrift/<int:user_id>", methods=["POST"])
@admin_required
def instruction_sign(topic_id, user_id):
    topic = _get(InstructionTopic, topic_id)
    user = _get(User, user_id)
    try:
        team.sign_instruction(topic, user, form_date("datum") or date.today(), request.form.get("bild"), current_user)
        db.session.commit()
        flash(_("{name} hat unterschrieben.", name=user.name), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("team.instruction_detail", topic_id=topic.id) + "#personen")


@bp.route("/unterweisung/nachweis/<int:record_id>.png")
@admin_required
def instruction_signature(record_id):
    record = _get(InstructionRecord, record_id)
    return Response(record.signature, mimetype="image/png", headers={"Cache-Control": "private, max-age=3600"})


@bp.route("/unterweisung/nachweis/<int:record_id>/loeschen", methods=["POST"])
@admin_required
def instruction_record_delete(record_id):
    record = _get(InstructionRecord, record_id)
    topic_id = record.topic_id
    db.session.delete(record)
    db.session.commit()
    flash(_("Nachweis gelöscht."), "success")
    return redirect(url_for("team.instruction_detail", topic_id=topic_id) + "#nachweise")


@bp.route("/unterweisung/<int:topic_id>/nachweis.pdf")
@admin_required
def instruction_pdf(topic_id):
    topic = _get(InstructionTopic, topic_id)
    year = request.args.get("jahr", type=int) or date.today().year
    pdf = render_instruction_record(topic, team.records_in_year(topic, year), year, Setting.all())
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="unterweisung-{topic.id}-{year}.pdf"'})


@bp.route("/unterweisung/<int:topic_id>/unterlage", methods=["POST"])
@admin_required
def instruction_document_upload(topic_id):
    topic = _get(InstructionTopic, topic_id)
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash_error(_("Bitte eine Datei auswählen."))
        return redirect(url_for("team.instruction_detail", topic_id=topic.id) + "#unterlagen")
    name = None
    try:
        name, mimetype, size = uploads.save_document(upload, office=True)
        title = form_str("title", 150) or upload.filename.rsplit(".", 1)[0][:150]
        db.session.add(InstructionDocument(topic=topic, title=title, filename=name, original_name=upload.filename[:200],
                                           mimetype=mimetype, size=size, user_id=current_user.id))
        db.session.commit()
        flash(_("„{title}“ hochgeladen.", title=title), "success")
    except StorageError as e:
        db.session.rollback()
        if name:
            uploads.delete_document(name)
        flash_error(str(e))
    return redirect(url_for("team.instruction_detail", topic_id=topic.id) + "#unterlagen")


@bp.route("/unterweisung/unterlage/<int:doc_id>")
@admin_required
def instruction_document(doc_id):
    doc = _get(InstructionDocument, doc_id)
    download = not doc.viewable or request.args.get("download") == "1"
    response = send_from_directory(uploads.document_dir(), doc.filename, mimetype=doc.mimetype, max_age=86400,
                                   as_attachment=download, download_name=f"{doc.title[:80]}.{doc.extension}")
    response.headers["Cache-Control"] = "private, max-age=86400"
    # Hochgeladene Dateien dürfen im Browser nichts ausführen
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    return response


@bp.route("/unterweisung/unterlage/<int:doc_id>/loeschen", methods=["POST"])
@admin_required
def instruction_document_delete(doc_id):
    doc = _get(InstructionDocument, doc_id)
    topic_id, filename = doc.topic_id, doc.filename
    db.session.delete(doc)
    db.session.commit()
    uploads.delete_document(filename)
    flash(_("„{title}“ gelöscht.", title=doc.title), "success")
    return redirect(url_for("team.instruction_detail", topic_id=topic_id) + "#unterlagen")


@bp.route("/unterweisung/<int:topic_id>/loeschen", methods=["POST"])
@admin_required
def instruction_delete(topic_id):
    topic = _get(InstructionTopic, topic_id)
    if topic.records:
        flash_error(_("{name} hat schon Nachweise – bitte nur deaktivieren.", name=topic.name))
        return redirect(url_for("team.instruction_detail", topic_id=topic.id))
    files = [d.filename for d in topic.documents]
    db.session.delete(topic)
    db.session.commit()
    for name in files:
        uploads.delete_document(name)
    flash(_("{name} gelöscht.", name=topic.name), "success")
    return redirect(url_for("team.instructions"))


# --- Werkstatt-Bildschirm (Fernseher, ohne Anmeldung über geheimen Link) ---------------------

@bp.route("/werkstatt/anzeige/<token>")
def screen(token):
    from . import screen as screen_data
    from .weather import LABELS
    if not screen_data.check_token(token):
        abort(404)
    return render_template("team/screen.html", d=screen_data.data(), weather_labels=LABELS)
