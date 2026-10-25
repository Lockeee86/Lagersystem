"""Schnellzugriff auf der Übersicht (Handy, die ersten 4): Voreinstellung je Rolle, jeder kann Kacheln dazunehmen
oder ausblenden.

Sichtbar = Voreinstellung der Rolle + eigene Zusätze − eigene ausgeblendete, nur was die Rolle darf,
immer in der Reihenfolge des Katalogs. Ändert der Admin die Voreinstellung, bekommen alle die Änderung,
die eigenen Zusätze/Ausblendungen bleiben erhalten.
"""
from .extensions import db
from .i18n import N_
from .models import ROLE_ADMIN, ROLE_BETRACHTER, ROLE_MITARBEITER, ROLES, Setting


def tile(label, icon, endpoint, role=ROLE_BETRACHTER, **args):
    return {"label": label, "icon": icon, "endpoint": endpoint, "args": args, "role": role}


# Reihenfolge = Reihenfolge auf der Übersicht
CATALOG = {
    "scan": tile(N_("Scannen"), "bi-qr-code-scan", "main.scan", ROLE_MITARBEITER),
    "tanken": tile(N_("Tanken"), "bi-fuel-pump", "fuel.tank", ROLE_MITARBEITER, tanken=1),
    "aufgaben": tile(N_("Aufgaben"), "bi-list-check", "tasks.task_list"),
    "lager": tile(N_("Lager"), "bi-grid-3x3-gap", "main.warehouse_map"),
    "kisten": tile(N_("Kisten"), "bi-boxes", "main.boxes"),
    "ernte": tile(N_("Ernte"), "bi-basket", "main.harvest", ROLE_MITARBEITER),
    "verkauf": tile(N_("Verkauf"), "bi-truck", "sales.deliveries"),
    "maschinen": tile(N_("Maschinen"), "bi-gear-wide-connected", "machines.machine_list"),
    "bestellungen": tile(N_("Bestellungen"), "bi-box-seam", "purchases.purchase_list"),
    "kalender": tile(N_("Kalender"), "bi-calendar3", "agenda.month"),
    "schlaege": tile(N_("Schläge"), "bi-map", "fields.field_map"),
    "pflanzenschutz": tile(N_("Pflanzenschutz"), "bi-shield-plus", "fields.protection"),
    "wetter": tile(N_("Wetter"), "bi-cloud-sun", "main.weather_page"),
    "tankbuch": tile(N_("Tankbuch"), "bi-fuel-pump-diesel", "fuel.tank"),
    "lieferanten": tile(N_("Lieferanten"), "bi-shop", "purchases.suppliers"),
    "kunden": tile(N_("Kunden"), "bi-person-vcard", "sales.customers", ROLE_ADMIN),
    "verlauf": tile(N_("Verlauf"), "bi-clock-history", "main.history"),
    "etiketten": tile(N_("Etiketten"), "bi-qr-code", "main.labels", ROLE_ADMIN),
    "verwaltung": tile(N_("Verwaltung"), "bi-sliders", "admin.index", ROLE_ADMIN),
}

DEFAULTS = {
    ROLE_ADMIN: ["aufgaben", "bestellungen", "kalender", "tankbuch"],
    ROLE_MITARBEITER: ["tanken", "aufgaben", "maschinen", "bestellungen"],
    ROLE_BETRACHTER: ["lager", "schlaege", "kalender", "wetter"],
}
QUICK_COUNT = 4             # so viele erscheinen am Handy im Schnellzugriff (Scannen steht schon in der Leiste)


def quick(keys):
    """Die Kacheln für den Schnellzugriff am Handy: die ersten 4 der Auswahl, ohne „Scannen“."""
    return [k for k in keys if k != "scan"][:QUICK_COUNT]


def _split(text):
    return [k for k in (text or "").split(",") if k in CATALOG]


def allowed(user):
    """Kacheln, die der Benutzer überhaupt sehen darf (in Katalog-Reihenfolge)."""
    return [key for key, t in CATALOG.items() if user.has_role(t["role"])]


def role_default(role):
    setting = db.session.get(Setting, f"tiles_{role}")
    return _split(setting.value) if setting is not None and setting.value is not None else list(DEFAULTS[role])


def save_role_default(role, keys):
    Setting.put(f"tiles_{role}", ",".join(k for k in CATALOG if k in keys) or ",")   # „,“ = bewusst leer


def for_user(user):
    base = set(role_default(user.role)) | set(_split(user.tiles_add))
    base -= set(_split(user.tiles_hide))
    return [key for key in allowed(user) if key in base]


def save_user(user, keys):
    """Eigene Auswahl als Abweichung von der Voreinstellung speichern."""
    chosen = {k for k in keys if k in allowed(user)}
    default = set(role_default(user.role))
    user.tiles_add = ",".join(k for k in CATALOG if k in chosen - default)
    user.tiles_hide = ",".join(k for k in CATALOG if k in default - chosen)


def reset_user(user):
    user.tiles_add = user.tiles_hide = ""


ROLE_ORDER = [r for r in (ROLE_ADMIN, ROLE_MITARBEITER, ROLE_BETRACHTER) if r in ROLES]


# --- Bereiche der Startseite -------------------------------------------------------
# Gleiches Prinzip wie die Kacheln: Voreinstellung je Rolle (`settings` `home_<rolle>`), eigene Abweichung
# `User.home_add` / `User.home_hide`. Bereiche ohne Daten (z. B. BHKW ohne Motoren) blendet die Startseite selbst aus.

def section(label, icon, role=ROLE_BETRACHTER, hint=None):
    return {"label": label, "icon": icon, "role": role, "hint": hint}


SECTIONS = {
    "strompreis": section(N_("Strompreis-Ampel"), "bi-lightning", hint=N_("Day-Ahead-Preise heute und morgen (SKVE)")),
    "wichtig": section(N_("Jetzt wichtig"), "bi-exclamation-circle"),
    "plan": section(N_("Mein Tagesplan"), "bi-people", ROLE_MITARBEITER),
    "uebergabe": section(N_("Übergabe"), "bi-chat-square-text"),
    "aufgaben": section(N_("Aufgaben"), "bi-list-check"),
    "bhkw": section(N_("BHKW"), "bi-lightning-charge", hint=N_("Motoren, Leistung, nächste Wartung")),
    "woche": section(N_("Diese Woche"), "bi-calendar-week", hint=N_("Termine der nächsten 7 Tage")),
    "wetter": section(N_("Wetter"), "bi-cloud-sun"),
    "bestand": section(N_("Lager und Bestand"), "bi-grid-3x3-gap", hint=N_("Belegung, Bestand je Sorte, letzte Bewegungen")),
}

SECTION_DEFAULTS = {
    ROLE_ADMIN: list(SECTIONS),
    ROLE_MITARBEITER: ["wichtig", "plan", "uebergabe", "aufgaben", "woche", "wetter", "bestand"],
    ROLE_BETRACHTER: ["wichtig", "uebergabe", "woche", "wetter", "bestand"],
}


def _split_sections(text):
    return [k for k in (text or "").split(",") if k in SECTIONS]


def allowed_sections(user):
    return [key for key, sec in SECTIONS.items() if user.has_role(sec["role"])]


def section_default(role):
    setting = db.session.get(Setting, f"home_{role}")
    if setting is not None and setting.value is not None:
        return _split_sections(setting.value)
    return list(SECTION_DEFAULTS[role])


def save_section_default(role, keys):
    Setting.put(f"home_{role}", ",".join(k for k in SECTIONS if k in keys) or ",")


def sections_for(user):
    base = set(section_default(user.role)) | set(_split_sections(user.home_add))
    base -= set(_split_sections(user.home_hide))
    return [key for key in allowed_sections(user) if key in base]


def save_user_sections(user, keys):
    chosen = {k for k in keys if k in allowed_sections(user)}
    default = set(section_default(user.role))
    user.home_add = ",".join(k for k in SECTIONS if k in chosen - default)
    user.home_hide = ",".join(k for k in SECTIONS if k in default - chosen)


def reset_user_sections(user):
    user.home_add = user.home_hide = ""
