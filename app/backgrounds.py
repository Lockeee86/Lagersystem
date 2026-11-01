"""Hintergrund der Oberfläche (Glas-Design): je Benutzer wählbar, `User.background` / `User.bg_strength`.

„auto“ wechselt nach Tageszeit (Ortszeit). „klassisch“ ist das Design ohne Glas und Standard für alle; hell oder
dunkel über `User.theme_mode` (Standard „auto“: hell zwischen Sonnenauf- und -untergang am Hof, `theme()`).
Die Farben stehen in `static/css/glass.css` (`html[data-bg=…]`). Committet nicht.
"""
from datetime import datetime

from .i18n import N_
from .utils import local_tz

CHOICES = {"sonnenaufgang": N_("Sonnenaufgang"), "wiese": N_("Wiese"), "abend": N_("Abend"), "kuehl": N_("Kühl"),
           "felder": N_("Felder"), "nacht": N_("Nacht"),
           "sterne": N_("Sternenhimmel"), "milchstrasse": N_("Milchstraße")}
SPECIAL = {"klassisch": N_("Klassisch (ohne Glas)"), "auto": N_("Glas nach Tageszeit")}
# Vorschau in der Auswahl (dieselben Farben wie in glass.css)
PREVIEWS = {"sonnenaufgang": ("#3a2033", "#ff8a4c", "#ffc061"), "wiese": ("#1f3328", "#7fd38a", "#ffd36b"),
            "abend": ("#221d40", "#8c6cff", "#ff7aa8"), "kuehl": ("#1c2a44", "#5ab0ff", "#7fe0c4"),
            "felder": ("#6b3d63", "#e98a6a", "#47552f"), "nacht": ("#0f1424", "#3b5bdb", "#6f42c1"),
            "sterne": ("#03050b", "#1d2a5a", "#2a1d4a"), "milchstrasse": ("#02030a", "#3a3470", "#6a5a8a")}
DEFAULT, STRENGTH = "klassisch", 80
GLASS_DEFAULT = "sonnenaufgang"            # unbekannter Wert eines Glas-Hintergrunds
# Klassisch: hell/dunkel – nach Tageszeit (Standard), fest oder wie das Gerät
THEME_MODES = {"auto": N_("Nach Tageszeit"), "light": N_("Hell"), "dark": N_("Dunkel"), "system": N_("Wie das Gerät")}
LIGHT_HOURS = (7, 19)                     # ohne Standort des Hofs: hell von 7 bis 19 Uhr
# Nach Tageszeit: ab dieser Stunde (Ortszeit)
DAY_TIMES = ((5, "sonnenaufgang"), (10, "wiese"), (17, "abend"), (21, "nacht"))


def by_time(hour):
    current = "nacht"
    for start, key in DAY_TIMES:
        if hour >= start:
            current = key
    return current


def effective(user, now=None):
    """Hintergrund, der jetzt gezeigt wird (None = klassisch). Nicht angemeldet: Standard."""
    choice = getattr(user, "background", None) or DEFAULT
    if choice == "klassisch":
        return None
    if choice == "auto":
        return by_time((now or datetime.now(local_tz())).hour)
    return choice if choice in CHOICES else GLASS_DEFAULT


def is_daylight(now=None):
    """Tag am Hof? Zwischen Sonnenauf- und -untergang (Standort aus den Einstellungen), sonst nach LIGHT_HOURS."""
    now = now or datetime.now(local_tz())
    from . import chickens
    try:
        rise, set_ = chickens.farm_sunrise(now.date()), chickens.farm_sunset(now.date())
    except Exception:                       # kein Standort / keine Datenbank: einfache Uhrzeit
        rise = set_ = None
    if rise and set_:
        return rise <= now <= set_
    return LIGHT_HOURS[0] <= now.hour < LIGHT_HOURS[1]


def theme(user, now=None):
    """Klassisches Design: "light" / "dark" – oder None = wie das Gerät (entscheidet der Browser)."""
    mode = getattr(user, "theme_mode", None) or "auto"
    if mode in ("light", "dark"):
        return mode
    if mode == "system":
        return None
    return "light" if is_daylight(now) else "dark"


def save_theme(user, mode):
    user.theme_mode = mode if mode in THEME_MODES else "auto"


def strength(user):
    value = getattr(user, "bg_strength", None)
    return value if isinstance(value, int) and 20 <= value <= 100 else STRENGTH


def save(user, choice, value):
    if choice not in CHOICES and choice not in SPECIAL:
        choice = DEFAULT
    user.background = choice
    user.bg_strength = min(max(value if isinstance(value, int) else STRENGTH, 20), 100)
