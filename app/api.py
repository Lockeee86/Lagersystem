from flask import Blueprint, jsonify
from flask_login import login_required
from sqlalchemy import func, text

from .extensions import db
from .models import Movement

bp = Blueprint("api", __name__)


@bp.route("/health")
def health():
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify(status="ok")
    except Exception:  # noqa: BLE001 – Healthcheck soll nie mit 500 abbrechen
        return jsonify(status="error", detail="database"), 503


@bp.route("/version")
@login_required
def version():
    """Letzte Bewegungs-ID. Seiten fragen das regelmäßig ab und laden bei Änderung neu."""
    latest = db.session.query(func.max(Movement.id)).scalar() or 0
    return jsonify(movement=latest)
