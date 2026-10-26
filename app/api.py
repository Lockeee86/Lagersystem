from flask import Blueprint, jsonify, request
from flask_login import login_required
from sqlalchemy import func, text

from .extensions import csrf, db
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


def _bridge_token_ok():
    """Schlüssel der Meshtastic-Brücke als `Authorization: Bearer …` oder `X-Api-Key`."""
    from . import coop
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("X-Api-Key", "").strip()
    return coop.check_token(token)


@bp.route("/meshtastic", methods=["POST"])
@csrf.exempt
def meshtastic():
    """Jedes Paket der Meshtastic-Brücke; wohin es gehört, entscheidet die App (Rubrik Sensoren)."""
    from . import sensors
    if not _bridge_token_ok():
        return jsonify(ok=False, error="unauthorized"), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="JSON-Objekt erwartet"), 400
    try:
        purpose = sensors.record(data)
        db.session.commit()
    except sensors.SensorError as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, purpose=purpose)


@bp.route("/huehner/sensor", methods=["POST"])
@csrf.exempt
def coop_sensor():
    """Messwerte vom Hühnerstall (Meshtastic-Brücke)."""
    from . import coop
    if not _bridge_token_ok():
        return jsonify(ok=False, error="unauthorized"), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="JSON-Objekt erwartet"), 400
    try:
        result = coop.record(data)
        db.session.commit()
    except coop.SensorError as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, reading=result["reading"] is not None, door_changed=result["door"] is not None)


@bp.route("/lager/klima", methods=["POST"])
@csrf.exempt
def climate_sensor():
    """Messwerte aus dem Lager (Meshtastic-Brücke), gleicher Schlüssel wie der Stall-Sensor."""
    from . import climate
    if not _bridge_token_ok():
        return jsonify(ok=False, error="unauthorized"), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="JSON-Objekt erwartet"), 400
    try:
        reading = climate.record(data)
        db.session.commit()
    except climate.SensorError as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, stored=reading is not None)
