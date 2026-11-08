"""Rubrik Biogas: Übersicht der BHKW-Motoren (SKVE-Daten, Laufzeit, Wartungsprognose)."""
from datetime import date, timedelta

import os
import secrets

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from . import biogas, feedin, plant_display
from .models import FeedPoint, Machine
from .i18n import gettext as _, ngettext
from .extensions import db
from .storage import StorageError
from .utils import admin_required, flash_error, form_int, form_str

bp = Blueprint("biogas", __name__)


@bp.before_request
def _only_with_biogas():
    """Rubrik Biogas ist je Benutzer abwählbar (User.biogas)."""
    if current_user.is_authenticated and not current_user.sees_biogas:
        # „BGA füttern“ ohne Rubrik Biogas: nur der Fütterungsplan
        if not (current_user.feeds_plant and not current_user.restricted
                and (request.endpoint or "").startswith("biogas.feeding")):
            abort(404)


def _page(template, tab, **context):
    return render_template(template, tab=tab, status=biogas.status(), configured=biogas.configured(),
                           today=date.today(), **context)


def _year():
    return request.args.get("jahr", type=int) or date.today().year


@bp.route("/biogas")
@login_required
def overview():
    """Reiter „Jetzt“: Leistung, Preis-Ampel, je BHKW Zustand und Fahrplan, Gasspeicher, Kurzfassung Budget."""
    live = biogas.live()
    today = date.today()
    return _page("biogas/overview.html", "jetzt", live=live, plan_only=biogas.PLAN_ONLY,
                 strip=biogas.price_strip(live.get("price_series")) if not live.get("error") else None,
                 rows=biogas.overview(), quality=None,
                 starts={m["machine_id"]: biogas.start_label(m) for m in live.get("motors") or []},
                 budget_short=[s for s in feedin.overview(today.year) if s["budget"]],
                 overdue_inspections=[t for t, st in biogas.plant_inspections() if st["state"] == "overdue"])


@bp.route("/biogas/budget")
@login_required
def budget_page():
    """Reiter „Budget“: Jahresbudget je Einspeisestelle, Fahrplan bis 31.12., Mehrstrom-Rechner, Erlöse."""
    budget_year = _year()
    budget = feedin.overview(budget_year)
    outlook = []
    if budget and biogas.configured() and budget_year == date.today().year:
        try:
            outlook = biogas.price_outlook()
        except Exception:                                  # noqa: BLE001 – ohne Preise nur ohne Preis-Hinweis
            outlook = []
    for s in budget:
        s["plan"] = feedin.plan(s)
        s["revenue"] = feedin.revenue(s["point"], budget_year)
        s["extra"] = feedin.extra(s)
        s["price_from"] = feedin.price_threshold(s["hours_per_day"], outlook) if budget_year == date.today().year \
            else None
    return _page("biogas/budget.html", "budget", budget=budget, budget_year=budget_year,
                 month_names=feedin.MONTH_NAMES, revenue_groups=feedin.REVENUE_GROUPS)


@bp.route("/biogas/wartung")
@login_required
def maintenance_page():
    """Reiter „Wartung“: Wartungen nach Laufzeit je BHKW, Wartungsfenster nach Strompreis, Prüffristen der Anlage."""
    windows, windows_error = biogas.maintenance_windows()
    return _page("biogas/maintenance.html", "wartung", rows=biogas.overview(), windows=windows,
                 windows_error=windows_error, work=biogas.window_hours(), linked=biogas.linked_machines(),
                 inspections=biogas.plant_inspections(),
                 plants=Machine.query.filter_by(category=biogas.PLANT_CATEGORY, active=True).all())


@bp.route("/biogas/auswertung")
@login_required
def analysis_page():
    """Reiter „Auswertung“: Wochenbericht, Fahrplan-Güte, Verfügbarkeit laut Logbuch."""
    return _page("biogas/analysis.html", "auswertung", week=biogas.week_report(),
                 quality=biogas.flex_quality(date.today() - timedelta(days=biogas.QUALITY_DAYS),
                                             date.today() - timedelta(days=1)),
                 availability=biogas.availability_overview())


@bp.route("/biogas/einstellungen")
@admin_required
def settings_page():
    """Reiter „Einstellungen“ (Admin): Einspeisestellen mit Monatsmengen und Vergütung, CSV-Import, Display-Link."""
    budget_year = _year()
    return _page("biogas/settings.html", "einstellungen", budget=feedin.overview(budget_year),
                 budget_year=budget_year, feed_candidates=feedin.candidates(), month_names=feedin.MONTH_NAMES,
                 display_token=plant_display.token())


# --- Anzeige an der Anlage (Display 800×480 bzw. Farb-E-Paper) – ohne Anmeldung über geheimen Link ---------------

@bp.route("/biogas/anzeige/<token>")
def display(token):
    if not plant_display.check_token(token):
        abort(404)
    return render_template("biogas/display.html", d=plant_display.data())


@bp.route("/biogas/anzeige/<token>/bild.png")
def display_image(token):
    """Dasselbe als Bild für Farb-E-Paper (reTerminal E1002, 800×480, sechs Farben) – ESPHome `online_image`."""
    if not plant_display.check_token(token):
        abort(404)
    response = Response(plant_display.render(plant_display.data()), mimetype="image/png")
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/biogas/anzeige", methods=["POST"])
@admin_required
def display_link():
    """Link für das Display anlegen bzw. erneuern (der alte gilt danach nicht mehr)."""
    plant_display.new_token()
    db.session.commit()
    flash(_("Neuer Link für das Display erzeugt – der alte funktioniert nicht mehr."), "success")
    return redirect(url_for("biogas.settings_page") + "#anzeige")


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
    return redirect(url_for("biogas.settings_page", jahr=request.form.get("jahr") or None) + "#budget")


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

    def save():
        feedin.save_point(point, form_str("name", 100), form_str("amount", 20),
                          request.form.getlist("machine", type=int), form_str("unit", 5))
        if "aw_stage1" in request.form:
            feedin.save_tariff(point, request.form)
    return _feed_save(save, _("Gespeichert."))


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
    return redirect(url_for("biogas.maintenance_page") + "#fenster")


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


@bp.route("/biogas/einspeisestelle/import", methods=["POST"])
@admin_required
def feed_import_preview():
    """Abrechnungs-Positionen (CSV): erst Vorschau mit Zuordnung, dann übernehmen."""
    from .admin import _cleanup_imports, _import_dir
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash_error(_("Bitte eine Datei auswählen."))
        return redirect(url_for("biogas.settings_page") + "#budget")
    data = upload.read()
    try:
        parsed = feedin.parse_csv(data)
    except feedin.FeedImportError as e:
        flash_error(_(str(e)))
        return redirect(url_for("biogas.settings_page") + "#budget")
    _cleanup_imports()
    token = secrets.token_hex(16)
    with open(os.path.join(_import_dir(), token), "wb") as f:
        f.write(data)
    session["feed_import"] = {"token": token}
    return render_template("biogas/feed_import.html", parsed=parsed, filename=upload.filename,
                           feed_points=feedin.points(), guesses={n: feedin.guess(n) for n in parsed["points"]},
                           month_names=feedin.MONTH_NAMES)


@bp.route("/biogas/einspeisestelle/import/uebernehmen", methods=["POST"])
@admin_required
def feed_import_apply():
    from .admin import _import_dir
    token = (session.pop("feed_import", None) or {}).get("token", "")
    path = os.path.join(_import_dir(), token) if token and token.isalnum() else ""
    if not path or not os.path.exists(path):
        flash_error(_("Die Vorschau ist abgelaufen – bitte die Datei noch einmal hochladen."))
        return redirect(url_for("biogas.settings_page") + "#budget")
    with open(path, "rb") as f:
        parsed = feedin.parse_csv(f.read())
    os.remove(path)
    mapping = {}
    try:
        for i, name in enumerate(parsed["points"]):
            choice = request.form.get(f"point{i}", "")
            if choice == "new":                    # Einspeisestelle aus der Datei neu anlegen
                mapping[name] = feedin.save_point(None, name, form_str(f"amount{i}", 20), [])
            else:
                mapping[name] = db.session.get(FeedPoint, int(choice)) if choice.isdigit() else None
        count = feedin.apply_import(parsed, mapping)
        db.session.commit()
    except StorageError as e:
        db.session.rollback()
        flash_error(_(str(e)))
        return redirect(url_for("biogas.settings_page") + "#budget")
    flash(ngettext("{n} Abrechnungswert übernommen.", "{n} Abrechnungswerte übernommen.", count), "success")
    return redirect(url_for("biogas.settings_page") + "#budget")


@bp.route("/biogas/pruefliste", methods=["POST"])
@admin_required
def plant_create():
    from flask_login import current_user as user
    machine = biogas.create_plant(user)
    db.session.commit()
    flash(_("Prüfliste angelegt – Fristen und letzte Prüfung bitte an Genehmigung und Prüfbescheide anpassen."), "success")
    return redirect(url_for("machines.machine_detail", machine_id=machine.id) + "#wartungen")


# --- Fütterungsplan -----------------------------------------------------------------------------

def _feeding_back(day=None):
    from . import plant_feeding
    target = request.form.get("back", "")
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    monday = plant_feeding.monday_of(day or date.today())
    return redirect(url_for("biogas.feeding_page", woche=monday.isoformat()) + (f"#t{day.isoformat()}" if day else ""))


def _form_day():
    try:
        return date.fromisoformat(request.form.get("tag", ""))
    except ValueError:
        abort(400)


@bp.route("/biogas/fuetterung")
@login_required
def feeding_page():
    """Reiter „Fütterung“: Kalenderwoche mit 1×/2× je Tag, eintragen, abgeben, abhaken."""
    from . import plant_feeding
    from .models import User
    today = date.today()
    try:
        start = date.fromisoformat(request.args.get("woche", ""))
    except ValueError:
        start = today
    monday = plant_feeding.monday_of(start)
    days = plant_feeding.week(monday, current_user, today)
    absent = set()
    if plant_feeding.is_member(current_user):
        absent = {d["day"] for d in days if not d["past"] and plant_feeding.absent_warning(current_user, d["day"])}
    members = sorted(plant_feeding.members(), key=lambda u: u.name.casefold()) \
        if plant_feeding.is_planner(current_user) else []
    return _page("biogas/feeding.html", "fuetterung", days=days, monday=monday,
                 week_no=monday.isocalendar()[1], prev=monday - timedelta(days=7), next=monday + timedelta(days=7),
                 last_day=plant_feeding.last_day(today), member=plant_feeding.is_member(current_user),
                 planner=plant_feeding.is_planner(current_user), feeders=len(plant_feeding.members()),
                 members=members, absent=absent, defaults=plant_feeding.defaults(),
                 weeks_ahead=plant_feeding.WEEKS_AHEAD)


@bp.route("/biogas/fuetterung/eintragen", methods=["POST"])
@login_required
def feeding_take():
    from . import plant_feeding
    from .models import User
    day = _form_day()
    user = db.session.get(User, form_int("user_id") or 0) if request.form.get("user_id") else None
    try:
        shift = plant_feeding.take(day, form_int("slot") or 1, current_user, user)
        db.session.commit()
        if plant_feeding.absent_warning(shift.user, day):
            flash(_("Hinweis: {name} ist an dem Tag laut Kalender abwesend.", name=shift.user.name), "warning")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _feeding_back(day)


@bp.route("/biogas/fuetterung/<int:shift_id>/abgeben", methods=["POST"])
@login_required
def feeding_give_up(shift_id):
    from . import plant_feeding
    from .models import FeedingShift
    shift = db.session.get(FeedingShift, shift_id) or abort(404)
    day = shift.day
    try:
        info = plant_feeding.give_up(shift, current_user)
        db.session.commit()
        plant_feeding.notify_free(info[0], info[1], current_user)
        flash(_("Fütterung abgegeben – das Team bekommt Bescheid."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _feeding_back(day)


@bp.route("/biogas/fuetterung/<int:shift_id>/erledigt", methods=["POST"])
@login_required
def feeding_done(shift_id):
    from . import plant_feeding
    from .models import FeedingShift
    shift = db.session.get(FeedingShift, shift_id) or abort(404)
    try:
        if request.form.get("zurueck"):
            plant_feeding.undo_done(shift, current_user)
        else:
            plant_feeding.mark_done(shift, current_user, request.form.get("note"))
        db.session.commit()
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _feeding_back(shift.day)


@bp.route("/biogas/fuetterung/tag", methods=["POST"])
@login_required
def feeding_times():
    from . import plant_feeding
    day = _form_day()
    try:
        removed = plant_feeding.set_times(day, form_int("times") or 1, current_user)
        db.session.commit()
        plant_feeding.notify_removed(removed, day, current_user)
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return _feeding_back(day)


@bp.route("/biogas/fuetterung/standard", methods=["POST"])
@admin_required
def feeding_defaults():
    from . import plant_feeding
    try:
        plant_feeding.save_defaults([request.form.get(f"d{i}", "1") for i in range(plant_feeding.WEEKDAY_KEYS)])
        db.session.commit()
        flash(_("Standard gespeichert."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("biogas.feeding_page") + "#standard")
