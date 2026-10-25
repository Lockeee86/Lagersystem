"""Seiten der Rubrik Hühner (Logik in chickens.py). Sichtbar nur für freigeschaltete Benutzer und Admins."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import chickens, coop, tasks
from .extensions import db
from .i18n import gettext as _, ngettext
from .models import (FLOCK_REASONS, RECURRENCES, EggEntry, EggShare, FeedEntry, FlockEvent, FlockTreatment,
                     Household)
from .storage import StorageError
from .utils import admin_required, back, flash_error, form_date, form_int, form_money, form_str

bp = Blueprint("chickens", __name__)


def viewer_required(view):
    """Rubrik ansehen: in der Benutzerverwaltung freigeschaltet (oder Admin)."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not (current_user.sees_chickens or current_user.is_admin):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def keeper_required(view):
    """Eintragen: freigeschalteter Mitarbeiter (oder Admin)."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not (current_user.keeps_chickens or current_user.is_admin):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _save(action, success, default):
    """Formular speichern: `action()` ausführen, committen, Meldung – zurück zur Seite."""
    try:
        result = action()
        db.session.commit()
        if success:
            flash(success(result) if callable(success) else success, "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return back(default)


def _delete(model, obj_id, default):
    obj = db.session.get(model, obj_id)
    if obj is None:
        abort(404)
    db.session.delete(obj)
    db.session.commit()
    flash(_("Eintrag gelöscht."), "success")
    return back(default)


def _kg(name):
    raw = form_str(name, 20).replace(" ", "").replace("kg", "")
    if not raw:
        return None
    try:
        return Decimal(raw.replace(".", "").replace(",", ".") if "," in raw else raw).quantize(Decimal("0.1"))
    except InvalidOperation:
        raise StorageError(_("Bitte die Menge in kg angeben."))


# --- Übersicht ----------------------------------------------------------------------------

@bp.route("/huehner")
@viewer_required
def overview():
    today = date.today()
    existing = {t.title for t in chickens.open_tasks()}
    suggestions = [(i, _(title), RECURRENCES[rec]) for i, (title, rec) in enumerate(chickens.TASK_SUGGESTIONS)
                   if _(title) not in existing]
    return render_template(
        "chickens/overview.html", info=chickens.summary(today), today=today, days=chickens.recent_days(today),
        open_tasks=chickens.open_tasks(), suggestions=suggestions, sunset=chickens.farm_sunset(today),
        sensor=coop.status(), sensor_history=coop.history(48), door_events=coop.door_events(10),
        sensor_token=coop.has_token() if current_user.is_admin else None,
        entries=EggEntry.query.filter(EggEntry.day >= today - timedelta(days=1))
        .order_by(EggEntry.day.desc(), EggEntry.id.desc()).all(),
    )


@bp.route("/huehner/eier", methods=["POST"])
@keeper_required
def egg_add():
    return _save(lambda: chickens.add_eggs(form_date("day") or date.today(), form_int("eggs"),
                                           form_int("cracked", 0), form_str("note", 200), current_user),
                 lambda e: ngettext("{n} Ei eingetragen – danke!", "{n} Eier eingetragen – danke!", e.eggs),
                 "chickens.overview")


@bp.route("/huehner/eier/<int:entry_id>/loeschen", methods=["POST"])
@keeper_required
def egg_delete(entry_id):
    return _delete(EggEntry, entry_id, "chickens.overview")


@bp.route("/huehner/sensor/schluessel", methods=["POST"])
@admin_required
def sensor_token():
    token = coop.new_token()
    db.session.commit()
    flash(_("Neuer Schlüssel: {token} – als COOP_API_TOKEN im Stack eintragen (wird nur jetzt angezeigt).",
            token=token), "warning")
    return redirect(url_for("chickens.overview") + "#sensor")


@bp.route("/huehner/aufgabe", methods=["POST"])
@keeper_required
def task_suggestion():
    index = form_int("vorschlag")
    if index is None or not 0 <= index < len(chickens.TASK_SUGGESTIONS):
        abort(400)
    title, recurrence = chickens.TASK_SUGGESTIONS[index]

    def create():
        task = tasks.create_task(current_user, title=_(title), recurrence=recurrence, due_on=date.today())
        task.area = chickens.AREA
        return task
    return _save(create, lambda t: _("Aufgabe „{title}“ angelegt.", title=t.title), "chickens.overview")


# --- Eier & Haushalte ---------------------------------------------------------------------

@bp.route("/huehner/eier-haushalte")
@viewer_required
def eggs():
    today = date.today()
    return render_template(
        "chickens/eggs.html", today=today, stock=chickens.stock(), months=chickens.months(today),
        households=chickens.households(active_only=False), turns=chickens.fair_order(today),
        shares=EggShare.query.order_by(EggShare.day.desc(), EggShare.id.desc()).limit(40).all(),
        entries=EggEntry.query.order_by(EggEntry.day.desc(), EggEntry.id.desc()).limit(40).all(),
        blocked=chickens.blocked(today),
    )


@bp.route("/huehner/verteilen", methods=["POST"])
@keeper_required
def share_add():
    household = db.session.get(Household, form_int("household_id", 0))
    return _save(lambda: chickens.give_eggs(form_date("day") or date.today(), household, form_int("eggs"),
                                            form_str("note", 200), current_user),
                 lambda s: ngettext("{n} Ei an {name}.", "{n} Eier an {name}.", s.eggs, name=s.household.name),
                 "chickens.eggs")


@bp.route("/huehner/verteilung/<int:share_id>/loeschen", methods=["POST"])
@keeper_required
def share_delete(share_id):
    return _delete(EggShare, share_id, "chickens.eggs")


@bp.route("/huehner/haushalt", methods=["POST"])
@keeper_required
def household_save():
    household = db.session.get(Household, form_int("id", 0)) if form_int("id") else None
    active = request.form.get("active") == "1" if household else True
    return _save(lambda: chickens.save_household(household, form_str("name", 100), form_str("note", 200), active),
                 lambda h: _("{name} gespeichert.", name=h.name), "chickens.eggs")


# --- Herde --------------------------------------------------------------------------------

@bp.route("/huehner/herde")
@viewer_required
def flock():
    today = date.today()
    return render_template(
        "chickens/flock.html", today=today, size=chickens.flock_size(today), groups=chickens.groups(),
        events=FlockEvent.query.order_by(FlockEvent.day.desc(), FlockEvent.id.desc()).limit(60).all(),
        reasons=FLOCK_REASONS,
    )


@bp.route("/huehner/herde", methods=["POST"])
@keeper_required
def flock_add():
    return _save(lambda: chickens.add_flock_event(form_date("day") or date.today(), form_str("reason", 20),
                                                  form_int("count"), current_user, breed=form_str("breed", 80),
                                                  hatched_on=form_date("hatched_on"), note=form_str("note", 200)),
                 _("Herde aktualisiert."), "chickens.flock")


@bp.route("/huehner/herde/<int:event_id>/loeschen", methods=["POST"])
@keeper_required
def flock_delete(event_id):
    return _delete(FlockEvent, event_id, "chickens.flock")


# --- Futter -------------------------------------------------------------------------------

@bp.route("/huehner/futter")
@viewer_required
def feed():
    today = date.today()
    return render_template(
        "chickens/feed.html", today=today, per_hen=chickens.feed_per_hen(today), months=chickens.months(today),
        entries=FeedEntry.query.order_by(FeedEntry.day.desc(), FeedEntry.id.desc()).limit(40).all(),
        products=[p for (p,) in db.session.query(FeedEntry.product).filter(FeedEntry.product.isnot(None))
                  .distinct().limit(20)],
    )


@bp.route("/huehner/futter", methods=["POST"])
@keeper_required
def feed_add():
    return _save(lambda: chickens.add_feed(form_date("day") or date.today(), _kg("kg"), form_money("cost"),
                                           form_str("product", 100), form_str("note", 200), current_user),
                 _("Futter eingetragen."), "chickens.feed")


@bp.route("/huehner/futter/<int:entry_id>/loeschen", methods=["POST"])
@keeper_required
def feed_delete(entry_id):
    return _delete(FeedEntry, entry_id, "chickens.feed")


# --- Stallbuch ----------------------------------------------------------------------------

@bp.route("/huehner/stallbuch")
@viewer_required
def log():
    today = date.today()
    return render_template(
        "chickens/log.html", today=today, blocked=chickens.blocked(today),
        entries=FlockTreatment.query.order_by(FlockTreatment.day.desc(), FlockTreatment.id.desc()).limit(60).all(),
        products=[p for (p,) in db.session.query(FlockTreatment.product).distinct().limit(30)],
    )


@bp.route("/huehner/stallbuch", methods=["POST"])
@keeper_required
def log_add():
    return _save(lambda: chickens.add_treatment(form_date("day") or date.today(), form_str("product", 120),
                                                form_str("reason", 200), form_int("withdrawal_days", 0),
                                                form_str("note", 300), current_user),
                 _("Im Stallbuch eingetragen."), "chickens.log")


@bp.route("/huehner/stallbuch/<int:entry_id>/loeschen", methods=["POST"])
@keeper_required
def log_delete(entry_id):
    return _delete(FlockTreatment, entry_id, "chickens.log")
