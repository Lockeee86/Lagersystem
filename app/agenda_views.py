"""Kalender-Seite und iCal-Abo (Wartungen, erwartete Lieferungen)."""
from datetime import date

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import agenda
from .extensions import db
from .i18n import force_locale, gettext as _
from .models import Setting
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
    return render_template(
        "agenda/month.html", data=agenda.month(year, month_, today), year=year, month=month_,
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
        name = _("{farm}: Wartungen und Lieferungen", farm=Setting.all().get("farm_name") or _("Hof"))
        body = agenda.ics(name, request.host_url)
    return Response(body, mimetype="text/calendar", headers={
        "Content-Disposition": 'inline; filename="hof-kalender.ics"', "Cache-Control": "private, no-cache"})
