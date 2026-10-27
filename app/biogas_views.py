"""Rubrik Biogas: Übersicht der BHKW-Motoren (SKVE-Daten, Laufzeit, Wartungsprognose)."""
from datetime import date, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from . import biogas
from .i18n import gettext as _
from .extensions import db
from .storage import StorageError
from .utils import admin_required, flash_error, form_int

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
    return render_template("biogas/overview.html", live=live,
                           strip=biogas.price_strip(live.get("price_series")) if not live.get("error") else None, rows=biogas.overview(),
                           status=biogas.status(), configured=biogas.configured(), today=date.today(),
                           linked=biogas.linked_machines(), windows=windows, windows_error=windows_error,
                           work=biogas.window_hours(), week=biogas.week_report(), plan_only=biogas.PLAN_ONLY,
                           quality=biogas.flex_quality(date.today() - timedelta(days=biogas.QUALITY_DAYS),
                                                       date.today() - timedelta(days=1)),
                           availability=biogas.availability_overview(),
                           starts={m["machine_id"]: biogas.start_label(m) for m in live.get("motors") or []})


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
