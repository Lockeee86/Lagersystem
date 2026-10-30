"""Hintergrund der Oberfläche (Glas-Design): je Benutzer wählbar, `User.background` / `User.bg_strength`.

„auto“ wechselt nach Tageszeit (Ortszeit), „klassisch“ ist das alte Design ohne Glas (mit Hell/Dunkel).
Die Farben stehen in `static/css/glass.css` (`html[data-bg=…]`). Committet nicht.
"""
from datetime import datetime

from .i18n import N_
from .utils import local_tz

CHOICES = {"sonnenaufgang": N_("Sonnenaufgang"), "wiese": N_("Wiese"), "abend": N_("Abend"), "kuehl": N_("Kühl"),
           "felder": N_("Felder"), "nacht": N_("Nacht")}
SPECIAL = {"auto": N_("Nach Tageszeit"), "klassisch": N_("Klassisch (ohne Glas)")}
# Vorschau in der Auswahl (dieselben Farben wie in glass.css)
PREVIEWS = {"sonnenaufgang": ("#3a2033", "#ff8a4c", "#ffc061"), "wiese": ("#1f3328", "#7fd38a", "#ffd36b"),
            "abend": ("#221d40", "#8c6cff", "#ff7aa8"), "kuehl": ("#1c2a44", "#5ab0ff", "#7fe0c4"),
            "felder": ("#6b3d63", "#e98a6a", "#47552f"), "nacht": ("#0f1424", "#3b5bdb", "#6f42c1")}
DEFAULT, STRENGTH = "sonnenaufgang", 80
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
    return choice if choice in CHOICES else DEFAULT


def strength(user):
    value = getattr(user, "bg_strength", None)
    return value if isinstance(value, int) and 20 <= value <= 100 else STRENGTH


def save(user, choice, value):
    if choice not in CHOICES and choice not in SPECIAL:
        choice = DEFAULT
    user.background = choice
    user.bg_strength = min(max(value if isinstance(value, int) else STRENGTH, 20), 100)
