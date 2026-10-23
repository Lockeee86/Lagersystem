import threading
import time
from datetime import date, datetime, timezone
from functools import wraps
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user

from .models import ROLE_ADMIN, ROLE_MITARBEITER


# --- Rechte -----------------------------------------------------------------

def role_required(role):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            if not current_user.has_role(role):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


admin_required = role_required(ROLE_ADMIN)
editor_required = role_required(ROLE_MITARBEITER)


# --- Login-Sperre -----------------------------------------------------------

class LoginThrottle:
    """Sperrt eine IP bzw. einen Benutzernamen nach zu vielen Fehlversuchen.

    Hält den Zustand im Speicher – Gunicorn läuft deshalb mit einem Worker
    (und mehreren Threads).
    """

    def __init__(self, max_attempts=5, window=15 * 60):
        self.max_attempts = max_attempts
        self.window = window
        self._failures = {}
        self._lock = threading.Lock()

    def _recent(self, key, now):
        attempts = [t for t in self._failures.get(key, []) if now - t < self.window]
        if attempts:
            self._failures[key] = attempts
        else:
            self._failures.pop(key, None)
        return attempts

    def blocked_for(self, *keys):
        """Sekunden bis zur Freigabe, 0 = nicht gesperrt."""
        now = time.monotonic()
        with self._lock:
            waits = []
            for key in keys:
                attempts = self._recent(key, now)
                if len(attempts) >= self.max_attempts:
                    waits.append(self.window - (now - attempts[0]))
            return int(max(waits, default=0))

    def fail(self, *keys):
        now = time.monotonic()
        with self._lock:
            for key in keys:
                self._failures.setdefault(key, []).append(now)

    def reset(self, *keys):
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)


login_throttle = LoginThrottle()


# --- Formulare --------------------------------------------------------------

def form_int(name, default=None, minimum=None, maximum=None):
    raw = request.form.get(name, "").strip()
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


def form_str(name, max_len=None):
    value = request.form.get(name, "").strip()
    if max_len:
        value = value[:max_len]
    return value


def form_money(name):
    """Betrag in € aus dem Formular („1.234,50“, „125.5“, „80 €“). Leer → None, ungültig → StorageError."""
    from decimal import Decimal, InvalidOperation
    from .i18n import gettext
    from .storage import StorageError
    raw = form_str(name, 20).replace("€", "").replace(" ", "")
    if not raw:
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        value = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        value = None
    if value is None or value < 0 or value > Decimal("9999999"):
        raise StorageError(gettext("Bitte die Kosten als Zahl angeben, z. B. 125,50."))
    return value


def form_date(name):
    raw = request.form.get(name, "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _is_local_path(target):
    parts = urlsplit(target)
    return (target.startswith("/") and not target.startswith("//")
            and "\\" not in target and not parts.netloc and not parts.scheme)


def safe_next(default_endpoint="main.dashboard"):
    """Weiterleitung nur auf relative Pfade dieser Anwendung."""
    target = request.values.get("next", "")
    return target if _is_local_path(target) else url_for(default_endpoint)


def back(default_endpoint="main.dashboard", **kwargs):
    """Zurück zur Seite aus dem Formularfeld `back` (nur lokale Pfade)."""
    target = request.form.get("back", "")
    return redirect(target if _is_local_path(target) else url_for(default_endpoint, **kwargs))


def flash_error(message):
    flash(message, "danger")


# --- Anzeige ----------------------------------------------------------------

def local_tz():
    return ZoneInfo(current_app.config["TIMEZONE"])


def to_local(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(local_tz())


def fmt_datetime(value):
    value = to_local(value)
    return value.strftime("%d.%m.%Y %H:%M") if value else "–"


def fmt_date(value):
    if value is None:
        return "–"
    if isinstance(value, datetime):
        value = to_local(value)
    return value.strftime("%d.%m.%Y")


def fmt_kg(value):
    """1234 → "1,2 t"; Tausendertrennzeichen: Punkt (de) bzw. geschütztes Leerzeichen (pl)."""
    if value is None:
        return "–"
    from .i18n import get_locale
    thousands = "\u00a0" if get_locale() == "pl" else "."
    if value >= 1000:
        return f"{value / 1000:,.1f} t".replace(",", "X").replace(".", ",").replace("X", thousands)
    return f"{value:,} kg".replace(",", thousands)


def fmt_bytes(value):
    """Dateigröße: 532 B, 12,3 kB, 4,5 MB, 1,2 GB"""
    if value is None:
        return "–"
    for unit in ("B", "kB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{fmt_num(value, 0 if unit == 'B' else 1)} {unit}"
        value /= 1000


def fmt_num(value, digits=0):
    """12.34 → "12" bzw. mit digits=1 → "12,3" (deutsches Komma)"""
    if value is None:
        return "–"
    text = f"{value:.{digits}f}"
    if float(text) == 0:          # kein „-0“
        text = f"{0:.{digits}f}"
    return text.replace(".", ",")


def fmt_weekday(value):
    from .i18n import gettext
    from .weather import WEEKDAYS
    return gettext(WEEKDAYS[value.weekday()])


def fmt_money(value):
    """Decimal('1234.5') → "1.234,50 €" """
    if value is None:
        return "–"
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def text_color_for(hex_color):
    """Schwarz oder Weiß – je nachdem, was auf der Hintergrundfarbe besser lesbar ist."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return "#000"
    return "#000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#fff"
