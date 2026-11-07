"""Seiten des Daten-Agenten (nur Admin): Frage stellen, Antwort abwarten, Verlauf, Monatslimit."""
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from . import assistant
from .extensions import db
from .i18n import N_, gettext as _
from .models import AssistantQuery
from .storage import StorageError
from .utils import admin_required, flash_error

bp = Blueprint("assistant", __name__)

TOOL_LABELS = {
    "lager": N_("Kartoffellager"), "ertraege": N_("Erträge"), "biogas": N_("Biogas"),
    "maschinen": N_("Maschinenkosten und Diesel"), "wald": N_("Bestattungswald"), "huehner": N_("Hühner"),
}
EXAMPLES = [
    N_("Wie viel Saatgut je Sorte liegt gerade im Lager?"),
    N_("Welche Sorte hatte in den letzten Jahren den besten Ertrag je Hektar?"),
    N_("Schaffen wir das Biogas-Jahresbudget, wenn es so weiterläuft wie bisher?"),
    N_("Welche Maschine war dieses Jahr am teuersten und warum?"),
    N_("Wie viele Plätze sind im Bestattungswald noch frei, nach Baumart?"),
]


@bp.route("/fragen")
@admin_required
def ask_page():
    return render_template("assistant/index.html", enabled=assistant.enabled(), queries=assistant.recent(),
                           month_cost=assistant.month_cost(), limit=assistant.limit_usd(), examples=EXAMPLES,
                           tool_labels=TOOL_LABELS)


@bp.route("/fragen", methods=["POST"])
@admin_required
def ask():
    try:
        query = assistant.start(request.form.get("frage", ""), current_user, current_user.language or "de")
    except StorageError as e:
        flash_error(str(e))
        return redirect(url_for("assistant.ask_page"))
    return redirect(url_for("assistant.query_detail", query_id=query.id))


@bp.route("/fragen/<int:query_id>")
@admin_required
def query_detail(query_id):
    assistant.expire_stale()
    query = db.session.get(AssistantQuery, query_id) or abort(404)
    return render_template("assistant/detail.html", query=query, answer=assistant.render_markdown(query.answer),
                           tool_labels=TOOL_LABELS, enabled=assistant.enabled())


@bp.route("/fragen/<int:query_id>/stand")
@admin_required
def query_state(query_id):
    assistant.expire_stale()
    query = db.session.get(AssistantQuery, query_id) or abort(404)
    return jsonify({"status": query.status})


@bp.route("/fragen/limit", methods=["POST"])
@admin_required
def save_limit():
    try:
        assistant.save_limit(request.form.get("limit", ""))
        db.session.commit()
        flash(_("Monatslimit gespeichert."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("assistant.ask_page") + "#limit")
