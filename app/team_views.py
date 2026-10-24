"""Seiten fürs Team: Übergabe-Notizen, Tagesplan, Unterweisungen (Logik in team.py)."""
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import team
from .extensions import db
from .i18n import gettext as _, ngettext
from .instruction_pdf import render_instruction_record
from .models import Handover, InstructionRecord, InstructionTopic, Machine, PlanEntry, Plot, Setting, User
from .storage import StorageError
from .utils import admin_required, back, editor_required, flash_error, form_date, form_int, form_str, local_tz

bp = Blueprint("team", __name__)


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
    return now.date() + timedelta(days=1) if current_user.is_admin and now.hour >= 14 else now.date()


def _form_day():
    return form_date("datum") or date.today()


def _to_plan(day, anchor=None):
    return redirect(url_for("team.day_plan", datum=day.isoformat()) + (f"#{anchor}" if anchor else ""))


@bp.route("/tagesplan")
@login_required
def day_plan():
    try:
        day = date.fromisoformat(request.args["datum"]) if request.args.get("datum") else _default_day()
    except ValueError:
        day = date.today()
    plan = team.plan_for(day)
    members = team.team_members()
    # Leute mit Einträgen zuerst, dann die übrigen; der eigene Plan ganz oben
    members.sort(key=lambda u: (u.id != current_user.id, u.id not in plan))
    recent = []
    if current_user.is_admin:                    # Vorschläge fürs Textfeld: zuletzt verwendete Einträge
        for (text,) in (db.session.query(PlanEntry.text).filter(PlanEntry.day >= day - timedelta(days=60))
                        .group_by(PlanEntry.text).order_by(db.func.max(PlanEntry.id).desc()).limit(40)):
            recent.append(text)
    return render_template(
        "team/day_plan.html", day=day, plan=plan, members=members, today=date.today(), timedelta1=timedelta(days=1),
        recent_texts=recent, sent=db.session.get(Setting, f"push_plan_sent_{day.isoformat()}") is not None,
        previous=team.last_planned_day(day) if current_user.is_admin else None,
        machines=Machine.query.filter_by(active=True).order_by(Machine.name).all() if current_user.is_admin else [],
        plots=Plot.query.filter_by(active=True).order_by(Plot.name).all() if current_user.is_admin else [],
    )


@bp.route("/tagesplan/eintrag", methods=["POST"])
@admin_required
def plan_add():
    day = _form_day()
    user = db.session.get(User, form_int("user_id", 0))
    try:
        team.add_plan_entry(day, user, request.form.get("text"), current_user,
                            machine_id=form_int("machine_id"), plot_id=form_int("plot_id"))
        db.session.commit()
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _to_plan(day, f"p{user.id}" if user else None)


@bp.route("/tagesplan/eintrag/<int:entry_id>/loeschen", methods=["POST"])
@admin_required
def plan_delete(entry_id):
    entry = _get(PlanEntry, entry_id)
    day, user_id = entry.day, entry.user_id
    db.session.delete(entry)
    db.session.commit()
    return _to_plan(day, f"p{user_id}")


@bp.route("/tagesplan/uebernehmen", methods=["POST"])
@admin_required
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


@bp.route("/tagesplan/verschicken", methods=["POST"])
@admin_required
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


@bp.route("/unterweisung/<int:topic_id>/loeschen", methods=["POST"])
@admin_required
def instruction_delete(topic_id):
    topic = _get(InstructionTopic, topic_id)
    if topic.records:
        flash_error(_("{name} hat schon Nachweise – bitte nur deaktivieren.", name=topic.name))
        return redirect(url_for("team.instruction_detail", topic_id=topic.id))
    db.session.delete(topic)
    db.session.commit()
    flash(_("{name} gelöscht.", name=topic.name), "success")
    return redirect(url_for("team.instructions"))
