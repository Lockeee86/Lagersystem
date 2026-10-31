"""Rubrik Biogas: Übersicht der BHKW-Motoren (SKVE-Daten, Laufzeit, Wartungsprognose)."""
from datetime import date, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import biogas, feedin
from .models import FeedPoint
from .i18n import gettext as _
from .extensions import db
from .storage import StorageError
from .utils import admin_required, flash_error, form_int, form_str

bp = Blueprint("biogas", __name__)


@bp.before_request
def _only_with_biogas():
    """Rubrik Biogas ist je Benutzer abwählbar (User.biogas)."""
    if current_user.is_authenticated and not current_user.sees_biogas:
        abort(404)


@bp.route("/biogas")
@login_required
def overview():
    windows, windows_error = biogas.maintenance_windows()
    live = biogas.live()
    budget_year = request.args.get("jahr", type=int) or date.today().year
    budget = feedin.overview(budget_year)
    outlook = []
    if budget and biogas.configured():
        try:
            outlook = biogas.price_outlook()
        except Exception:                                  # noqa: BLE001 – ohne Preise nur ohne Preis-Hinweis
            outlook = []
    for s in budget:
        s["price_from"] = feedin.price_threshold(s["hours_per_day"], outlook) if budget_year == date.today().year \
            else None
    return render_template("biogas/overview.html", live=live,
                           strip=biogas.price_strip(live.get("price_series")) if not live.get("error") else None, rows=biogas.overview(),
                           status=biogas.status(), configured=biogas.configured(), today=date.today(),
                           linked=biogas.linked_machines(), windows=windows, windows_error=windows_error,
                           work=biogas.window_hours(), week=biogas.week_report(), plan_only=biogas.PLAN_ONLY,
                           quality=biogas.flex_quality(date.today() - timedelta(days=biogas.QUALITY_DAYS),
                                                       date.today() - timedelta(days=1)),
                           availability=biogas.availability_overview(),
                           starts={m["machine_id"]: biogas.start_label(m) for m in live.get("motors") or []},
                           budget=budget, budget_year=budget_year, feed_candidates=feedin.candidates(),
                           month_names=feedin.MONTH_NAMES)


def _feed_point(point_id):
    return db.session.get(FeedPoint, point_id) or abort(404)


def _feed_save(action, message):
    try:
        action()
        db.session.commit()
        flash(message, "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(_(str(e)))
    return redirect(url_for("biogas.overview", jahr=request.form.get("jahr") or None) + "#budget")


@bp.route("/biogas/einspeisestelle", methods=["POST"])
@admin_required
def feed_point_add():
    return _feed_save(lambda: feedin.save_point(None, form_str("name", 100), form_str("amount", 20),
                                                request.form.getlist("machine", type=int), form_str("unit", 5)),
                      _("Einspeisestelle angelegt."))


@bp.route("/biogas/einspeisestelle/<int:point_id>", methods=["POST"])
@admin_required
def feed_point_save(point_id):
    point = _feed_point(point_id)
    return _feed_save(lambda: feedin.save_point(point, form_str("name", 100), form_str("amount", 20),
                                                request.form.getlist("machine", type=int), form_str("unit", 5)), _("Gespeichert."))


@bp.route("/biogas/einspeisestelle/<int:point_id>/loeschen", methods=["POST"])
@admin_required
def feed_point_delete(point_id):
    point = _feed_point(point_id)
    return _feed_save(lambda: feedin.delete_point(point), _("Einspeisestelle entfernt."))


@bp.route("/biogas/einspeisestelle/<int:point_id>/monate", methods=["POST"])
@admin_required
def feed_months_save(point_id):
    point = _feed_point(point_id)
    year = form_int("jahr", minimum=2000, maximum=2100) or date.today().year
    values = {m: request.form.get(f"m{m}", "") for m in range(1, 13)}
    return _feed_save(lambda: feedin.save_months(point, year, values), _("Monatsmengen gespeichert."))


@bp.route("/biogas/arbeitszeit", methods=["POST"])
@admin_required
def window_settings():
    try:
        biogas.save_window_hours(form_int("start", minimum=0, maximum=24), form_int("end", minimum=0, maximum=24))
        db.session.commit()
        flash(_("Arbeitszeit für Wartungen gespeichert."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("biogas.overview") + "#fenster")


@bp.route("/biogas/abrufen", methods=["POST"])
@admin_required
def sync_now():
    if not biogas.configured():
        flash_error(_("Für den Abruf fehlt der SKVE-API-Schlüssel (SKVE_API_KEY im Stack)."))
        return redirect(url_for("biogas.overview"))
    days = biogas.sync(current_app._get_current_object(), force=True)
    state = biogas.status()
    if state and state.get("ok"):
        flash(_("SKVE-Daten abgerufen ({n} Tage).", n=days or 0), "success")
    else:
        flash_error(_("SKVE-Abruf fehlgeschlagen: {message}", message=state.get("message") if state else "–"))
    return redirect(url_for("biogas.overview"))
