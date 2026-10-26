from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .extensions import db
from .i18n import LANGUAGE_FLAGS, LANG_COOKIE, LANGUAGES, get_locale, gettext as _, set_request_locale
from .models import PUSH_TOPICS, ROLE_ADMIN, BoxType, User, Warehouse, utcnow
from .utils import flash_error, form_int, form_str, login_throttle, safe_next

bp = Blueprint("auth", __name__)

MIN_PASSWORD_LENGTH = 8


def validate_password(password, repeat):
    if len(password) < MIN_PASSWORD_LENGTH:
        return _("Das Passwort muss mindestens {n} Zeichen lang sein.", n=MIN_PASSWORD_LENGTH)
    if password != repeat:
        return _("Die Passwörter stimmen nicht überein.")
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        username = form_str("username", 64).lower()
        password = request.form.get("password", "")
        keys = (f"ip:{request.remote_addr}", f"user:{username}")

        wait = login_throttle.blocked_for(*keys)
        if wait:
            flash_error(_("Zu viele Fehlversuche. Bitte in {n} Minuten erneut versuchen.", n=wait // 60 + 1))
            return render_template("auth/login.html", username=username), 429

        user = User.query.filter_by(username=username).first()
        if user and user.active and user.check_password(password):
            login_throttle.reset(*keys)
            chosen = request.cookies.get(LANG_COOKIE)
            if user.last_login is None and chosen in LANGUAGES:
                # Erste Anmeldung: auf der Login-Seite ausdrücklich gewählte Sprache übernehmen.
                # Sonst gilt die Sprache, die der Admin beim Anlegen eingestellt hat.
                user.language = chosen
            user.last_login = utcnow()
            db.session.commit()
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(safe_next())

        login_throttle.fail(*keys)
        current_app.logger.warning("Fehlgeschlagener Login für '%s' von %s", username, request.remote_addr)
        flash_error(_("Benutzername oder Passwort falsch."))
        return render_template("auth/login.html", username=username), 401

    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash(_("Du wurdest abgemeldet."), "info")
    return redirect(url_for("auth.login"))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """Ersteinrichtung: nur möglich, solange noch kein Benutzer existiert."""
    if db.session.query(User.id).first() is not None:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        username = form_str("username", 64).lower()
        password = request.form.get("password", "")
        error = None
        if not username:
            error = _("Bitte einen Benutzernamen angeben.")
        else:
            error = validate_password(password, request.form.get("password2", ""))
        rows = form_int("rows", minimum=1, maximum=100)
        slots = form_int("slots", minimum=1, maximum=100)
        levels = form_int("max_levels", minimum=1, maximum=20)
        weight = form_int("full_weight_kg", minimum=1, maximum=5000)
        if not error and None in (rows, slots, levels, weight):
            error = _("Bitte gültige Werte für das Lager angeben.")
        if error:
            flash_error(error)
            return render_template("auth/setup.html", form=request.form)

        user = User(username=username, display_name=form_str("display_name", 100) or None, role=ROLE_ADMIN,
                    language=get_locale())
        user.set_password(password)
        db.session.add(user)
        db.session.add(Warehouse(name=form_str("warehouse_name", 100) or "Kartoffellager",
                                 rows=rows, slots=slots, max_levels=levels))
        db.session.add(BoxType(name="Standardkiste", full_weight_kg=weight))
        db.session.commit()
        current_app.config["_setup_done"] = True
        login_user(user, remember=True)
        flash(_("Einrichtung abgeschlossen. Als Nächstes: Sorten anlegen und Kisten erzeugen."), "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/setup.html", form={})


@bp.route("/konto", methods=["GET", "POST"])
@login_required
def account():
    """Einstellungen des eigenen Kontos: Sprache und Passwort."""
    if request.method == "POST":
        if not current_user.check_password(request.form.get("current_password", "")):
            flash_error(_("Das aktuelle Passwort ist falsch."))
        else:
            error = validate_password(request.form.get("password", ""), request.form.get("password2", ""))
            if error:
                flash_error(error)
            else:
                current_user.set_password(request.form["password"])
                db.session.commit()
                # Neues Token → diese Sitzung neu anmelden, alle anderen Geräte sind abgemeldet
                login_user(current_user._get_current_object(), remember=True)
                flash(_("Passwort geändert. Andere Geräte wurden abgemeldet."), "success")
                return redirect(url_for("auth.account"))
    from . import tabbar
    return render_template("auth/account.html", languages=LANGUAGES, flags=LANGUAGE_FLAGS, push_topics=PUSH_TOPICS,
                           tab_choices=tabbar.available(current_user), tab_keys=tabbar.keys_for(current_user),
                           tab_labels=tabbar.labels())


@bp.route("/konto/leiste", methods=["POST"])
@login_required
def tabbar_save():
    """Eigene Wahl für die zwei freien Plätze der unteren Leiste."""
    from . import tabbar
    keys = [] if request.form.get("reset") else request.form.getlist("tab")
    if len(set(keys)) < len(keys):
        flash_error(_("Bitte zwei verschiedene Rubriken wählen."))
    else:
        tabbar.save_user(current_user, keys)
        db.session.commit()
        flash(_("Leiste gespeichert."), "success")
    return redirect(url_for("auth.account") + "#leiste")


@bp.route("/sprache", methods=["POST"])
def set_language():
    """Sprache umschalten – angemeldet dauerhaft am Benutzer, sonst per Cookie (z. B. auf der Login-Seite)."""
    lang = request.form.get("lang")
    if lang not in LANGUAGES:
        return redirect(safe_next())
    if current_user.is_authenticated:
        current_user.language = lang
        db.session.commit()
    set_request_locale(lang)
    if current_user.is_authenticated:
        flash(_("Sprache gespeichert."), "success")
    response = redirect(safe_next())
    response.set_cookie(LANG_COOKIE, lang, max_age=365 * 24 * 3600, samesite="Lax",
                        secure=current_app.config["SESSION_COOKIE_SECURE"], httponly=True)
    return response
