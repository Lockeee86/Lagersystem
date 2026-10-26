import os
from datetime import timedelta

from flask import Flask, g, redirect, request, url_for
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix

from .extensions import csrf, db, login_manager, migrate
from .models import FILL_LEVELS, ROLES, SORT_USAGES, Setting, User
from . import branding, navigation, tabbar, utils
from .i18n import LANGUAGE_FLAGS, LANGUAGES, get_locale, gettext, ngettext


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "ja", "on")


def _version():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "VERSION")
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return "dev"


def create_app(config=None):
    app = Flask(__name__)

    secret = os.environ.get("SECRET_KEY")
    app.config.update(
        SECRET_KEY=secret or os.urandom(32).hex(),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///lagersystem.db"),
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        TIMEZONE=os.environ.get("TZ", "Europe/Berlin"),
        APP_VERSION=_version(),
        # Hinter HTTPS (Reverse Proxy / Tunnel) auf true setzen
        SESSION_COOKIE_SECURE=_env_bool("COOKIE_SECURE"),
        REMEMBER_COOKIE_SECURE=_env_bool("COOKIE_SECURE"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_DURATION=timedelta(days=90),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        WTF_CSRF_TIME_LIMIT=None,
        # Handyfotos sind groß; sie werden beim Speichern verkleinert
        MAX_CONTENT_LENGTH=40 * 1024 * 1024,
        UPLOAD_DIR=os.environ.get("UPLOAD_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "uploads"),
        WEATHER_API_URL=os.environ.get("WEATHER_API_URL", "https://api.brightsky.dev/weather"),
        WEATHER_TIMEOUT=float(os.environ.get("WEATHER_TIMEOUT", "6")),
        # Datensicherung (leer = aus); im Stack ein Volume, das auf das NAS zeigen kann
        BACKUP_DIR=os.environ.get("BACKUP_DIR") or None,
        BACKUP_HOUR=int(os.environ.get("BACKUP_HOUR", "3")),
        # Biogas: Speicher-Kraftwerk-API (nur lesend). Ohne Schlüssel ist der Abruf aus.
        SKVE_API_KEY=os.environ.get("SKVE_API_KEY", "").strip(),
        SKVE_API_BASE=os.environ.get("SKVE_API_BASE", "https://www.speicher-kraftwerk.de/api").rstrip("/"),
        SKVE_GAS_STORAGE_ID=os.environ.get("SKVE_GAS_STORAGE_ID", "").strip(),
        SKVE_TIMEOUT=float(os.environ.get("SKVE_TIMEOUT", "20")),
    )
    if config:
        app.config.update(config)
    if not secret and not app.config.get("TESTING"):
        app.logger.warning("SECRET_KEY ist nicht gesetzt – Anmeldungen gehen bei jedem Neustart verloren.")

    # Anzahl vertrauenswürdiger Proxies vor der App (X-Forwarded-For/-Proto)
    proxies = int(os.environ.get("TRUSTED_PROXIES", "1"))
    if proxies > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)

    db.init_app(app)
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        with app.app_context():
            _fix_sqlite_transactions(db.engine)
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations"))
    csrf.init_app(app)
    login_manager.init_app(app)

    from .auth import bp as auth_bp
    from .views import bp as main_bp
    from .admin import bp as admin_bp
    from .api import bp as api_bp
    from .sales_views import bp as sales_bp
    from .machine_views import bp as machines_bp
    from .task_views import bp as tasks_bp
    from .fields_views import bp as fields_bp
    from .purchase_views import bp as purchases_bp
    from .push_views import bp as push_bp
    from .fuel_views import bp as fuel_bp
    from .bulk_views import bp as bulk_bp
    from .agenda_views import bp as agenda_bp
    from .branding import bp as branding_bp
    from .team_views import bp as team_bp
    from .biogas_views import bp as biogas_bp
    from .rounds_views import bp as rounds_bp
    from .chickens_views import bp as chickens_bp
    from .climate_views import bp as climate_bp
    from .sensors_views import bp as sensors_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(sales_bp)
    app.register_blueprint(machines_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(fields_bp)
    app.register_blueprint(purchases_bp)
    app.register_blueprint(push_bp)
    app.register_blueprint(fuel_bp)
    app.register_blueprint(bulk_bp)
    app.register_blueprint(agenda_bp)
    app.register_blueprint(branding_bp)
    app.register_blueprint(team_bp)
    app.register_blueprint(biogas_bp)
    app.register_blueprint(rounds_bp)
    app.register_blueprint(chickens_bp)
    app.register_blueprint(climate_bp)
    app.register_blueprint(sensors_bp)

    from . import cli
    cli.register(app)

    app.jinja_env.globals.update(_=gettext, ngettext=ngettext, get_locale=get_locale)

    def url_without(*params):
        """Aktuelle Seite ohne die genannten Parameter (und ohne Seitenzahl) – für die Filter-Chips."""
        args = {k: v for k, v in request.args.items(multi=True) if k not in params and k != "page"}
        return url_for(request.endpoint, **(request.view_args or {}), **args)
    app.jinja_env.globals["url_without"] = url_without

    def url_set(param, value):
        """Aktuelle Seite mit `param=value` (ohne Seitenzahl) – z. B. für den Filter „Betrieb“."""
        args = {k: v for k, v in request.args.items(multi=True) if k not in (param, "page")}
        return url_for(request.endpoint, **(request.view_args or {}), **args, **{param: value})
    app.jinja_env.globals["url_set"] = url_set

    from . import audit, sites  # noqa: F401 – audit registriert die Protokoll-Hooks
    from . import storage as _storage
    app.jinja_env.globals["potato_sizes"] = _storage.sizes
    app.jinja_env.globals.update(any_sites=sites.any_sites, chosen_site=sites.chosen, all_sites=sites.sites,
                                  default_site_id=sites.default_id)
    app.after_request(sites.remember)
    login_manager.localize_callback = gettext
    app.jinja_env.filters.update(
        datetime=utils.fmt_datetime,
        date=utils.fmt_date,
        time=utils.fmt_time,
        kg=utils.fmt_kg,
        money=utils.fmt_money,
        num=utils.fmt_num,
        qty=utils.fmt_qty,
        bytes=utils.fmt_bytes,
        weekday=utils.fmt_weekday,
        textcolor=utils.text_color_for,
    )

    @app.context_processor
    def inject_globals():
        if "farm" not in g:
            g.farm = Setting.all()
        if "my_tasks" not in g:
            # Zahl am Menüpunkt „Aufgaben“: eigene (und für alle) überfällig/heute/ohne Termin
            from . import tasks
            g.my_tasks = (len(tasks.due_now(current_user)) if current_user.is_authenticated
                          and current_user.can_edit else 0)
        if "purchase_count" not in g:
            # Zahl am Menüpunkt „Bestellungen“: für Besteller neue Wünsche + überfällige Lieferungen
            from . import purchases
            c = purchases.counts(current_user) if current_user.is_authenticated and current_user.is_buyer else None
            g.purchase_count = sum(c.values()) if c else 0
        endpoint = navigation.resolve(request.endpoint, request.view_args) if current_user.is_authenticated \
            else request.endpoint
        return {
            "farm": g.farm,
            "my_task_count": g.my_tasks,
            "purchase_count": g.purchase_count,
            "nav": navigation.current(endpoint, current_user) if current_user.is_authenticated else None,
            "mobile_tab": navigation.tab_for(endpoint, request.view_args, current_user),
            "mobile_crumb": navigation.crumb(endpoint, request.view_args, current_user)
            if current_user.is_authenticated else None,
            "tabbar": tabbar.entries(current_user) if current_user.is_authenticated else [],
            "app_name": g.farm["farm_name"] or gettext("Lagersystem"),
            "FILL_LEVELS": FILL_LEVELS,
            "ROLES": ROLES,
            "SORT_USAGES": SORT_USAGES,
            "app_version": app.config["APP_VERSION"],
            "logo_url": branding.url,
            "custom_logo": branding.version() is not None,
            "LANGUAGES": LANGUAGES,
            "LANGUAGE_FLAGS": LANGUAGE_FLAGS,
        }

    @app.before_request
    def require_setup():
        """Solange kein Benutzer existiert, führt jeder Weg zur Ersteinrichtung."""
        if request.endpoint in ("auth.setup", "static", "api.health", "api.coop_sensor", "api.climate_sensor", "api.meshtastic", "branding.logo"):
            return None
        if not app.config.get("_setup_done"):
            if db.session.query(User.id).first() is None:
                return redirect(url_for("auth.setup"))
            app.config["_setup_done"] = True
        return None

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=(self)")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; media-src 'self' blob:; frame-ancestors 'none'",
        )
        return response

    from flask import render_template

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403, message=gettext("Dafür fehlen dir die Rechte.")), 403

    @app.errorhandler(413)
    def too_large(_e):
        return render_template("error.html", code=413,
                               message=gettext("Die Datei ist zu groß (höchstens 40 MB).")), 413

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, message=gettext("Diese Seite gibt es nicht.")), 404

    return app


@login_manager.user_loader
def load_user(token):
    user = User.query.filter_by(session_token=token).first()
    return user if user and user.active else None


def _fix_sqlite_transactions(engine):
    """pysqlite startet Transaktionen zu spät; dadurch würde das Freigeben eines Savepoints
    (Import-Vorschau, `begin_nested`) bereits committen. Offizielles SQLAlchemy-Rezept:
    Transaktionen selbst mit BEGIN starten. Betrifft nur SQLite (Entwicklung/Tests)."""
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")
