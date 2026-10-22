import os
from datetime import timedelta

from flask import Flask, redirect, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from .extensions import csrf, db, login_manager, migrate
from .models import FILL_LEVELS, ROLES, SORT_USAGES, User
from . import utils
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
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
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
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")

    from . import cli
    cli.register(app)

    app.jinja_env.globals.update(_=gettext, ngettext=ngettext, get_locale=get_locale)
    login_manager.localize_callback = gettext
    app.jinja_env.filters.update(
        datetime=utils.fmt_datetime,
        date=utils.fmt_date,
        kg=utils.fmt_kg,
        textcolor=utils.text_color_for,
    )

    @app.context_processor
    def inject_globals():
        return {
            "FILL_LEVELS": FILL_LEVELS,
            "ROLES": ROLES,
            "SORT_USAGES": SORT_USAGES,
            "app_version": app.config["APP_VERSION"],
            "LANGUAGES": LANGUAGES,
            "LANGUAGE_FLAGS": LANGUAGE_FLAGS,
        }

    @app.before_request
    def require_setup():
        """Solange kein Benutzer existiert, führt jeder Weg zur Ersteinrichtung."""
        if request.endpoint in ("auth.setup", "static", "api.health"):
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
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
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
