"""Routen fürs iPhone-Widget: Daten ohne Anmeldung (Schlüssel im Link), Schlüssel und Skript unter Mein Konto."""
from flask import Blueprint, Response, abort, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from . import widget
from .extensions import db
from .i18n import force_locale, gettext as _

bp = Blueprint("widget", __name__)


@bp.route("/widget/<token>.json")
def feed(token):
    """Ohne Anmeldung – Scriptable kann sich nicht anmelden, der geheime Schlüssel im Link reicht."""
    user = widget.user_by_token(token)
    if user is None:
        abort(404)
    with force_locale(user.language):
        payload = widget.data(user)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/konto/widget", methods=["POST"])
@login_required
def token():
    if request.form.get("neu"):
        widget.renew_token(current_user)
        flash(_("Neuer Widget-Schlüssel erstellt. Das alte Skript funktioniert nicht mehr – bitte neu laden."), "success")
    else:
        widget.ensure_token(current_user)
    db.session.commit()
    return redirect(url_for("auth.account") + "#widget")


@bp.route("/konto/widget/hof-widget.js")
@login_required
def script():
    if not current_user.widget_token:
        abort(404)
    return Response(widget.script(current_user), mimetype="text/javascript", headers={
        "Content-Disposition": 'attachment; filename="Hof-Widget.js"', "Cache-Control": "private, no-store"})
