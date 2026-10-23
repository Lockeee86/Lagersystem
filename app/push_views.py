"""Push-Nachrichten einschalten/ausschalten, Themen wählen, Service-Worker ausliefern."""
import json

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, request, send_from_directory, url_for
from flask_login import current_user, login_required

from . import branding, push
from .extensions import db
from .i18n import gettext as _
from .models import PushSubscription, Setting

bp = Blueprint("push", __name__)


@bp.route("/sw.js")
def service_worker():
    """Service-Worker muss im Wurzelverzeichnis liegen, damit er für die ganze App gilt."""
    response = send_from_directory(current_app.static_folder, "js/sw.js", mimetype="text/javascript", max_age=0)
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/manifest.webmanifest")
def manifest():
    """App-Beschreibung für „Zum Startbildschirm hinzufügen“ – mit dem Hofnamen."""
    name = Setting.all().get("farm_name") or "Lagersystem"
    icon = lambda size: {"src": branding.url(f"icon-{size}"), "sizes": f"{size}x{size}",   # noqa: E731
                         "type": "image/png", "purpose": "any"}
    data = {
        "id": "/", "name": name, "short_name": name if len(name) <= 12 else name.split()[-1][:12],
        "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#f4f0e6", "theme_color": "#3f5a2a",
        "icons": [icon(192), icon(512), {**icon(512), "purpose": "maskable"}],
    }
    response = current_app.response_class(json.dumps(data), mimetype="application/manifest+json")
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/konto/push/schluessel")
@login_required
def vapid_key():
    return jsonify(key=push.public_key())


@bp.route("/konto/push/abo", methods=["POST"])
@login_required
def subscribe():
    sub = push.subscribe(current_user, request.get_json(silent=True), request.headers.get("User-Agent"))
    if sub is None:
        return jsonify(ok=False), 400
    db.session.commit()
    return jsonify(ok=True, id=sub.id)


@bp.route("/konto/push/abmelden", methods=["POST"])
@login_required
def unsubscribe():
    push.unsubscribe(current_user, (request.get_json(silent=True) or {}).get("endpoint"))
    db.session.commit()
    return jsonify(ok=True)


@bp.route("/konto/push/geraet/<int:sub_id>/loeschen", methods=["POST"])
@login_required
def device_delete(sub_id):
    sub = db.session.get(PushSubscription, sub_id)
    if sub is None or sub.user_id != current_user.id:
        abort(404)
    db.session.delete(sub)
    db.session.commit()
    flash(_("Gerät entfernt – es bekommt keine Nachrichten mehr."), "success")
    return redirect(url_for("auth.account") + "#push")


@bp.route("/konto/push/themen", methods=["POST"])
@login_required
def topics():
    chosen = set(request.form.getlist("topic"))
    current_user.push_off = ",".join(t for t in current_user.push_topics() if t not in chosen)
    db.session.commit()
    flash(_("Benachrichtigungen gespeichert."), "success")
    return redirect(url_for("auth.account") + "#push")


@bp.route("/konto/push/test", methods=["POST"])
@login_required
def test():
    count = push.notify([current_user], None, push.Msg("Testnachricht"),
                        push.Msg("Push-Nachrichten kommen auf diesem Gerät an."), url_for("auth.account"), tag="test")
    if count:
        flash(_("Testnachricht verschickt."), "success")
    else:
        flash(_("Auf keinem Gerät eingeschaltet."), "warning")
    return redirect(url_for("auth.account") + "#push")
