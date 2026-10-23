"""Navigation: vier Bereiche oben (PC), darunter die Unterpunkte des aktuellen Bereichs; Verwaltung extra.

Ein Unterpunkt ist aktiv, wenn der aktuelle Endpunkt in `endpoints` steht (oder mit einem Präfix aus
`prefixes` beginnt). `role` = mindestens nötige Rolle. Die Texte sind mit N_() markiert und werden in den
Templates übersetzt.
"""
from flask import url_for

from .i18n import N_
from .models import ROLE_ADMIN, ROLE_BETRACHTER, ROLE_MITARBEITER


def item(endpoint, label, icon, role=ROLE_BETRACHTER, endpoints=(), prefixes=(), badge=None):
    return {"endpoint": endpoint, "label": label, "icon": icon, "role": role,
            "endpoints": {endpoint, *endpoints}, "prefixes": prefixes, "badge": badge}


AREAS = [
    {"key": "start", "label": N_("Übersicht"), "icon": "bi-speedometer2", "items": [
        item("main.dashboard", N_("Heute"), "bi-sun"),
        item("tasks.task_list", N_("Aufgaben"), "bi-list-check", prefixes=("tasks.",), badge="tasks"),
        item("purchases.purchase_list", N_("Bestellungen"), "bi-box-seam", prefixes=("purchases.",), badge="purchases"),
        item("agenda.month", N_("Kalender"), "bi-calendar3", prefixes=("agenda.",)),
        item("main.search", N_("Suche"), "bi-search"),
    ]},
    {"key": "lager", "label": N_("Lager"), "icon": "bi-grid-3x3-gap", "items": [
        item("main.warehouse_map", N_("Lagerkarte"), "bi-grid-3x3-gap", endpoints=("main.stack_view",)),
        item("main.boxes", N_("Kisten"), "bi-boxes", endpoints=("main.box_detail",)),
        item("main.scan", N_("Scannen"), "bi-qr-code-scan", role=ROLE_MITARBEITER),
        item("main.history", N_("Verlauf"), "bi-clock-history"),
        item("main.labels", N_("Etiketten"), "bi-qr-code", role=ROLE_ADMIN),
    ]},
    {"key": "feld", "label": N_("Feld & Hof"), "icon": "bi-map", "items": [
        item("fields.field_map", N_("Schläge"), "bi-map", endpoints=("fields.field_detail", "fields.field_year_save")),
        item("fields.protection", N_("Pflanzenschutz"), "bi-shield-plus", endpoints=("fields.protection_product",)),
        item("machines.machine_list", N_("Maschinen"), "bi-gear-wide-connected", prefixes=("machines.",)),
        item("fuel.tank", N_("Tankbuch"), "bi-fuel-pump", prefixes=("fuel.",)),
        item("main.weather_page", N_("Wetter"), "bi-cloud-sun"),
    ]},
    {"key": "verkauf", "label": N_("Verkauf"), "icon": "bi-truck", "items": [
        item("sales.deliveries", N_("Lieferscheine"), "bi-truck",
             endpoints=("sales.delivery_detail", "sales.delivery_load")),
        item("sales.customers", N_("Kunden"), "bi-person-vcard", role=ROLE_ADMIN),
        item("sales.certifications", N_("Anerkennungen"), "bi-patch-check", role=ROLE_ADMIN),
    ]},
]

ADMIN = {"key": "admin", "label": N_("Verwaltung"), "icon": "bi-sliders", "items": [
    item("admin.index", N_("Start"), "bi-sliders"),
    item("admin.settings", N_("Hof"), "bi-house-gear"),
    item("admin.warehouses", N_("Lager"), "bi-buildings"),
    item("admin.varieties", N_("Sorten"), "bi-palette"),
    item("admin.plots", N_("Schläge"), "bi-map", endpoints=("admin.plots_kml_preview", "admin.plots_csv_preview")),
    item("admin.seed_classes", N_("Stufen"), "bi-award"),
    item("admin.box_types", N_("Kistentypen"), "bi-box"),
    item("admin.users", N_("Benutzer"), "bi-people"),
    item("admin.data", N_("Import / Export"), "bi-arrow-down-up", endpoints=("admin.import_preview", "admin.import_apply")),
    item("admin.tile_defaults", N_("Startseite"), "bi-grid-1x2"),
    item("admin.backups", N_("Datensicherung"), "bi-shield-check", endpoints=("admin.backup_now", "admin.backup_download")),
]}
# Kurzbeschreibungen für die Kacheln der Verwaltungs-Startseite
ADMIN_HINTS = {
    "admin.settings": N_("Hofname, Anschrift, Standort für das Wetter"),
    "admin.warehouses": N_("Reihen, Plätze, Höhe, Beschriftung"),
    "admin.varieties": N_("Kartoffelsorten mit Farbe"),
    "admin.plots": N_("Schläge, Import aus Myfarm24"),
    "admin.seed_classes": N_("Saatgut-Stufen (PB, SE, E …)"),
    "admin.box_types": N_("Kistengrößen und Gewicht"),
    "admin.users": N_("Konten, Rollen, Sprache"),
    "admin.data": N_("Excel-Vorlage, Import, Jahres-Export"),
    "admin.backups": N_("Nächtliche Sicherung, Stände herunterladen"),
    "admin.tile_defaults": N_("Kacheln am Handy je Rolle"),
}


def _active(it, endpoint):
    return endpoint in it["endpoints"] or any(endpoint.startswith(p) for p in it["prefixes"])


def _visible(items, user):
    return [it for it in items if user.has_role(it["role"])]


def current(endpoint, user):
    """{"areas": [...], "area": aktueller Bereich, "items": seine sichtbaren Unterpunkte, "active": Endpunkt}"""
    endpoint = endpoint or ""
    area = None
    if endpoint.startswith("admin."):
        area = ADMIN
    else:
        area = next((a for a in AREAS if any(_active(it, endpoint) for it in a["items"])), None)
    items = _visible(area["items"], user) if area else []
    active = next((it["endpoint"] for it in items if _active(it, endpoint)), None)
    areas = [{**a, "href": url_for(_visible(a["items"], user)[0]["endpoint"])} for a in AREAS]
    return {"areas": areas, "area": area, "items": items, "active": active}


def admin_tiles():
    return [{**it, "hint": ADMIN_HINTS.get(it["endpoint"]), "url": url_for(it["endpoint"])}
            for it in ADMIN["items"] if it["endpoint"] != "admin.index"]
