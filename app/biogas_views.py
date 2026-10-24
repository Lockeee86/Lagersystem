"""Rubrik Biogas: Übersicht der BHKW-Motoren (SKVE-Daten, Laufzeit, Wartungsprognose)."""
from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from flask_login import login_required

from . import biogas
from .i18n import gettext as _
from .utils import admin_required, flash_error

bp = Blueprint("biogas", __name__)


@bp.route("/biogas")
@login_required
def overview():
    return render_template("biogas/overview.html", live=biogas.live(), rows=biogas.overview(),
                           status=biogas.status(), configured=biogas.configured(), today=date.today(),
                           linked=biogas.linked_machines())


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
