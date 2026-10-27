"""Seiten für den Rundgang: Stationen, Kontrolle per QR-Scan, Mängel, Etiketten (Logik in rounds.py)."""
from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import rounds, uploads
from .extensions import db
from .i18n import gettext as _
from .labels import LABEL_FORMATS, render_labels
from .models import CheckStation, StationCheck
from .storage import StorageError
from .utils import admin_required, editor_required, flash_error, form_int

bp = Blueprint("rounds", __name__)


@bp.before_request
def _biogas_rounds_only_with_biogas():
    """Der Rundgang der Biogasanlage gehört zur Rubrik Biogas (je Benutzer abwählbar); Stationen im Hühnerstall
    nicht."""
    if not current_user.is_authenticated or current_user.sees_biogas:
        return None
    args = request.view_args or {}
    station = None
    if "code" in args:
        station = CheckStation.query.filter_by(code=rounds.normalize_code(args["code"]) or "").first()
    elif "check_id" in args:
        check = db.session.get(StationCheck, args["check_id"])
        station = check.station if check else None
    if station is None or station.area != "huehner":
        abort(404)
    return None


def _home(station=None):
    """Übersicht, zu der eine Station gehört (Rundgang Biogas oder Hühnerstall)."""
    if station is not None and station.area == "huehner":
        return url_for("chickens.overview")
    return url_for("rounds.overview")


def _station(code):
    station = CheckStation.query.filter_by(code=rounds.normalize_code(code) or "").first()
    if station is None:
        abort(404)
    return station


@bp.route("/rundgang")
@login_required
def overview():
    existing = {s.name.casefold() for s in CheckStation.query}
    suggestions = [(i, _(name)) for i, (name, _items, _days) in enumerate(rounds.SUGGESTIONS)
                   if _(name).casefold() not in existing]
    inactive = CheckStation.query.filter_by(active=False, area="biogas").order_by(CheckStation.code).all()
    return render_template("rounds/overview.html", data=rounds.overview(), suggestions=suggestions,
                           inactive=inactive, label_formats=LABEL_FORMATS)


@bp.route("/rundgang/neu", methods=["POST"])
@admin_required
def station_create():
    area = "huehner" if request.form.get("area") == "huehner" else "biogas"
    try:
        index = form_int("vorschlag")
        if area == "huehner" and request.form.get("vorschlag") == "stall":
            from .chickens import STATION_SUGGESTION
            name, items, days = STATION_SUGGESTION
            station = rounds.save_station(None, _(name), "\n".join(_(i) for i in items), days, area=area)
        elif index is not None and 0 <= index < len(rounds.SUGGESTIONS):
            name, items, days = rounds.SUGGESTIONS[index]
            station = rounds.save_station(None, _(name), "\n".join(_(i) for i in items), days)
        else:
            station = rounds.save_station(None, request.form.get("name"), request.form.get("items"),
                                          form_int("interval_days", 1), area=area)
        db.session.commit()
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("chickens.overview") if area == "huehner" else url_for("rounds.overview"))
    flash(_("Station {code} „{name}“ angelegt – jetzt das QR-Etikett drucken.", code=station.code, name=station.name),
          "success")
    return redirect(url_for("rounds.station", code=station.code))


@bp.route("/station/<code>")
@login_required
def station(code):
    station = _station(code)
    return render_template("rounds/station.html", station=station, state=rounds.status(station),
                           history=station.checks.limit(30).all(), label_formats=LABEL_FORMATS)


@bp.route("/station/<code>/kontrolle", methods=["POST"])
@editor_required
def station_check(code):
    station = _station(code)
    failed = [int(v) for v in request.form.getlist("failed") if v.isdigit()]
    names = []
    try:
        names = uploads.save_photos(request.files.getlist("photos"))
        check = rounds.record_check(station, current_user, failed, request.form.get("note"), names)
        db.session.commit()
    except StorageError as e:
        db.session.rollback()
        uploads.delete_files(names)
        flash_error(str(e))
        return redirect(url_for("rounds.station", code=station.code))
    if check.ok:
        flash(_("{name}: alles in Ordnung – danke!", name=station.name), "success")
    else:
        flash(_("{name}: Mangel gemeldet.", name=station.name), "warning")
        rounds.issue_reported(check)
    return redirect(_home(station) if check.ok else url_for("rounds.station", code=station.code))


@bp.route("/kontrolle/<int:check_id>/behoben", methods=["POST"])
@editor_required
def check_resolve(check_id):
    check = db.session.get(StationCheck, check_id) or abort(404)
    try:
        rounds.resolve(check, current_user)
        db.session.commit()
        flash(_("Mangel als behoben markiert."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("rounds.station", code=check.station.code))


@bp.route("/station/<code>/bearbeiten", methods=["POST"])
@admin_required
def station_edit(code):
    station = _station(code)
    try:
        rounds.save_station(station, request.form.get("name"), request.form.get("items"),
                            form_int("interval_days", 1), active=request.form.get("active") == "1")
        db.session.commit()
        flash(_("{name} gespeichert.", name=station.name), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("rounds.station", code=station.code))


@bp.route("/station/<code>/loeschen", methods=["POST"])
@admin_required
def station_delete(code):
    station = _station(code)
    if station.checks.first() is not None:
        flash_error(_("{name} hat schon Kontrollen – bitte nur deaktivieren.", name=station.name))
        return redirect(url_for("rounds.station", code=station.code))
    db.session.delete(station)
    db.session.commit()
    flash(_("{name} gelöscht.", name=station.name), "success")
    return redirect(_home(station))


@bp.route("/rundgang/etiketten.pdf", methods=["POST"])
@admin_required
def labels():
    codes = [rounds.normalize_code(c) for c in request.form.getlist("codes")]
    stations = CheckStation.query.filter(CheckStation.code.in_([c for c in codes if c])).order_by(CheckStation.code)
    codes = [s.code for s in stations] or [s.code for s in CheckStation.query.filter_by(active=True)
                                           .order_by(CheckStation.code)]
    if not codes:
        flash_error(_("Es gibt noch keine Stationen."))
        return redirect(url_for("rounds.overview"))
    fmt = request.form.get("format", "2x2")
    fmt = fmt if fmt in LABEL_FORMATS else "2x2"
    pdf = render_labels(codes, fmt, skip=form_int("skip", 0, minimum=0, maximum=100))
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="rundgang-etiketten.pdf"'})
