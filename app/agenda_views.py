"""Kalender-Seite und iCal-Abo (Wartungen, erwartete Lieferungen)."""
from datetime import date, datetime

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import agenda, tasks
from .extensions import db
from .i18n import force_locale, gettext as _
from .models import CALENDAR_KINDS, CALENDAR_REPEATS, CalendarEntry, Setting, User
from .storage import StorageError
from .utils import editor_required, flash_error, form_date, form_str
from .weather import MONTHS

bp = Blueprint("agenda", __name__)


def _feed_url(token, scheme=None):
    url = url_for("agenda.feed", token=token, _external=True)
    return url.replace("https://", "webcal://").replace("http://", "webcal://") if scheme == "webcal" else url


@bp.route("/kalender")
@login_required
def month():
    today = date.today()
    year = request.args.get("jahr", type=int) or today.year
    month_ = request.args.get("monat", type=int) or today.month
    if not (1 <= month_ <= 12 and 2000 <= year <= 2100):
        abort(404)
    prev_ = (year - 1, 12) if month_ == 1 else (year, month_ - 1)
    next_ = (year + 1, 1) if month_ == 12 else (year, month_ + 1)
    token = current_user.calendar_token
    mine = request.args.get("nur") == "meine"
    return render_template(
        "agenda/month.html", data=agenda.month(year, month_, today, current_user, mine), year=year, month=month_,
        mine=mine, kinds=CALENDAR_KINDS, repeats=CALENDAR_REPEATS, users=tasks.assignable_users(),
        prefill_date=request.args.get("datum") or today.isoformat(),
        month_name=dict(MONTHS)[month_], prev=prev_, next=next_, today=today,
        feed_url=_feed_url(token) if token else None, webcal_url=_feed_url(token, "webcal") if token else None,
    )


@bp.route("/kalender/abo", methods=["POST"])
@login_required
def subscribe():
    if request.form.get("neu"):
        agenda.renew_token(current_user)
        flash(_("Neuer Abo-Link erstellt. Der alte funktioniert nicht mehr."), "success")
    else:
        agenda.ensure_token(current_user)
    db.session.commit()
    return redirect(url_for("agenda.month") + "#abo")


@bp.route("/kalender/<token>.ics")
def feed(token):
    """Ohne Anmeldung (Kalender-Apps können sich nicht anmelden) – der geheime Schlüssel im Link reicht."""
    user = agenda.user_by_token(token)
    if user is None:
        abort(404)
    with force_locale(user.language):
        name = _("{farm}: Kalender", farm=Setting.all().get("farm_name") or _("Hof"))
        body = agenda.ics(name, request.host_url, user)
    return Response(body, mimetype="text/calendar", headers={
        "Content-Disposition": 'inline; filename="hof-kalender.ics"', "Cache-Control": "private, no-cache"})


# --- Eigene Termine ---------------------------------------------------------------

def _time(name):
    value = form_str(name, 5)
    try:
        return datetime.strptime(value, "%H:%M").time() if value else None
    except ValueError:
        raise StorageError(_("Bitte die Uhrzeit als hh:mm angeben."))


def _save(entry):
    ids = {int(v) for v in request.form.getlist("participant_ids") if v.isdigit()}
    all_day = bool(request.form.get("all_day"))
    agenda.save_entry(
        entry, current_user, form_str("title", 200), form_str("kind", 20), form_date("start_date"),
        form_date("end_date"), None if all_day else _time("start_time"), None if all_day else _time("end_time"),
        form_str("location", 200), form_str("note", 4000), form_str("repeat", 20),
        User.query.filter(User.id.in_(ids)).all() if ids else [],
    )


@bp.route("/kalender/termin", methods=["POST"])
@editor_required
def entry_create():
    entry = CalendarEntry()
    try:
        _save(entry)
        db.session.commit()
        flash(_("Termin „{title}“ eingetragen.", title=entry.title), "success")
        return redirect(url_for("agenda.month", jahr=entry.start_date.year, monat=entry.start_date.month))
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("agenda.month", neu=1))


def _get(entry_id):
    entry = db.session.get(CalendarEntry, entry_id)
    if entry is None:
        abort(404)
    return entry


@bp.route("/kalender/termin/<int:entry_id>")
@login_required
def entry_detail(entry_id):
    return render_template("agenda/entry.html", entry=_get(entry_id), kinds=CALENDAR_KINDS, repeats=CALENDAR_REPEATS,
                           users=tasks.assignable_users())


@bp.route("/kalender/termin/<int:entry_id>", methods=["POST"])
@editor_required
def entry_edit(entry_id):
    entry = _get(entry_id)
    if not entry.can_edit(current_user):
        abort(403)
    try:
        _save(entry)
        db.session.commit()
        flash(_("Termin „{title}“ gespeichert.", title=entry.title), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("agenda.entry_detail", entry_id=entry.id))


@bp.route("/kalender/termin/<int:entry_id>/loeschen", methods=["POST"])
@editor_required
def entry_delete(entry_id):
    entry = _get(entry_id)
    if not entry.can_edit(current_user):
        abort(403)
    day = entry.start_date
    db.session.delete(entry)
    db.session.commit()
    flash(_("Termin „{title}“ gelöscht.", title=entry.title), "success")
    return redirect(url_for("agenda.month", jahr=day.year, monat=day.month))
