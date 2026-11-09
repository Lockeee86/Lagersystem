"""Navigation: vier Bereiche oben (PC), darunter die Unterpunkte des aktuellen Bereichs; Verwaltung extra.

Ein Unterpunkt ist aktiv, wenn der aktuelle Endpunkt in `endpoints` steht (oder mit einem Präfix aus
`prefixes` beginnt). `role` = mindestens nötige Rolle. Die Texte sind mit N_() markiert und werden in den
Templates übersetzt.
"""
from flask import url_for

from .i18n import N_
from .models import ROLE_ADMIN, ROLE_BETRACHTER, ROLE_MITARBEITER


def item(endpoint, label, icon, role=ROLE_BETRACHTER, endpoints=(), prefixes=(), badge=None, show=None):
    """`show(user)`: Unterpunkt nur für passende Benutzer (z. B. Biogas-Seiten nur mit Rubrik Biogas)."""
    return {"endpoint": endpoint, "label": label, "icon": icon, "role": role,
            "endpoints": {endpoint, *endpoints}, "prefixes": prefixes, "badge": badge, "show": show}


def _biogas(user):
    return user.sees_biogas


def _sensors_shown(user):
    from .sensors import any_in_use
    return user.is_admin or any_in_use()


AREAS = [
    {"key": "start", "label": N_("Übersicht"), "icon": "bi-speedometer2", "items": [
        item("main.dashboard", N_("Heute"), "bi-sun"),
        item("team.day_plan", N_("Aufgaben"), "bi-list-check", endpoints=("team.open_page",), prefixes=("tasks.",),
             badge="tasks"),
        item("team.handovers", N_("Übergabe"), "bi-chat-square-text"),
        item("purchases.purchase_list", N_("Bestellungen"), "bi-box-seam", prefixes=("purchases.",), badge="purchases"),
        item("agenda.month", N_("Kalender"), "bi-calendar3", prefixes=("agenda.",)),
        item("main.search", N_("Suche"), "bi-search"),
        item("assistant.ask_page", N_("Fragen"), "bi-stars", role=ROLE_ADMIN, prefixes=("assistant.",)),
    ]},
    {"key": "lager", "label": N_("Lager"), "icon": "bi-grid-3x3-gap", "items": [
        # Übersicht mit den zwei Teilen Kartoffellager und Schüttlager, danach die Kartoffel-Seiten, dann Schüttlager
        item("main.storage_home", N_("Übersicht"), "bi-grid-1x2"),
        item("main.warehouse_map", N_("Lagerkarte"), "bi-grid-3x3-gap", endpoints=("main.stack_view",)),
        item("main.batch_store", N_("Einlagern"), "bi-box-arrow-in-down", role=ROLE_MITARBEITER),
        item("main.lots_page", N_("Partien"), "bi-stack"),
        item("main.boxes", N_("Kisten"), "bi-boxes", endpoints=("main.box_detail",)),
        item("main.history", N_("Verlauf"), "bi-clock-history"),
        item("main.harvest", N_("Ernte"), "bi-basket", role=ROLE_MITARBEITER),
        item("main.oldest", N_("Älteste Ware"), "bi-hourglass-split"),
        item("climate.overview_page", N_("Klima"), "bi-thermometer-half", prefixes=("climate.",)),
        item("main.repairs", N_("Kisten-Reparatur"), "bi-wrench-adjustable"),
        item("main.harvest_report_page", N_("Erntebericht"), "bi-clipboard-data"),
        item("main.labels", N_("Etiketten"), "bi-qr-code", role=ROLE_ADMIN),
        item("bulk.store_list", N_("Schüttlager"), "bi-minecart-loaded", prefixes=("bulk.",)),
        item("main.scan", N_("Scannen"), "bi-qr-code-scan", role=ROLE_MITARBEITER),
    ]},
    {"key": "feld", "label": N_("Feld & Hof"), "icon": "bi-map", "items": [
        item("fields.field_map", N_("Schläge"), "bi-map", endpoints=("fields.field_detail", "fields.field_year_save")),
        item("fields.rotation_page", N_("Fruchtfolge"), "bi-arrow-repeat"),
        item("fields.yields_page", N_("Erträge"), "bi-bar-chart"),
        item("fields.irrigation_page", N_("Beregnung"), "bi-droplet", endpoints=("admin.irrigation_preview",)),
        item("fields.protection", N_("Pflanzenschutz"), "bi-shield-plus", endpoints=("fields.protection_product",)),
        item("machines.machine_list", N_("Maschinen"), "bi-gear-wide-connected", prefixes=("machines.",)),
        item("machines.place_list", N_("Technische Plätze"), "bi-diagram-3",
             endpoints=("machines.place_detail",)),
        item("fuel.tank", N_("Tankbuch"), "bi-fuel-pump", prefixes=("fuel.",)),
        item("main.weather_page", N_("Wetter"), "bi-cloud-sun"),
    ]},
    # Biogas: in der Benutzerverwaltung je Benutzer abwählbar (User.biogas)
    {"key": "biogas", "label": N_("Biogas"), "icon": "bi-lightning-charge",
     # Wer nur „BGA füttern“ hat (ohne Rubrik Biogas), sieht hier allein den Fütterungsplan
     "show": lambda u: u.sees_feeding, "items": [
        item("biogas.overview", N_("Jetzt"), "bi-lightning-charge", endpoints=("biogas.sync_now",), show=_biogas),
        item("biogas.budget_page", N_("Budget"), "bi-speedometer2", show=_biogas),
        item("biogas.maintenance_page", N_("Wartung"), "bi-wrench-adjustable", endpoints=("biogas.window_settings",),
             show=_biogas),
        item("biogas.analysis_page", N_("Auswertung"), "bi-graph-up", show=_biogas),
        item("biogas.feeding_page", N_("Fütterung"), "bi-basket2"),
        item("rounds.overview", N_("Rundgang"), "bi-signpost-2", endpoints=("rounds.station",), show=_biogas),
        item("biogas.settings_page", N_("Einstellungen"), "bi-gear", role=ROLE_ADMIN,
             endpoints=("biogas.feed_import_preview",), show=_biogas),
    ]},
    # Bestattungswald: nur für Benutzer, die in der Benutzerverwaltung dafür freigeschaltet sind
    {"key": "wald", "label": N_("Bestattungswald"), "icon": "bi-tree", "show": lambda u: u.sees_forest, "items": [
        item("forest.overview", N_("Übersicht"), "bi-tree", endpoints=("forest.find",)),
        item("forest.selection_page", N_("Baumauswahl"), "bi-signpost-2", endpoints=("forest.selection_detail",)),
        item("forest.burial_page", N_("Beisetzungen"), "bi-calendar-event"),
        item("forest.trees", N_("Bäume"), "bi-list-ol", endpoints=("forest.tree_detail",)),
        item("forest.map_page", N_("Karte"), "bi-map"),
        item("forest.contracts_page", N_("Verträge"), "bi-file-earmark-text", role=ROLE_ADMIN,
             endpoints=("forest.contract_detail",)),
    ]},
    # nur für Benutzer, die in der Benutzerverwaltung für die Hühner freigeschaltet sind
    {"key": "huehner", "label": N_("Hühner"), "icon": "bi-egg", "show": lambda u: u.sees_chickens, "items": [
        item("chickens.overview", N_("Übersicht"), "bi-egg"),
        item("chickens.eggs", N_("Eier & Haushalte"), "bi-basket2"),
        item("chickens.analysis", N_("Auswertung"), "bi-graph-up"),
        item("chickens.flock", N_("Herde"), "bi-egg-fried"),
        item("chickens.feed", N_("Futter"), "bi-bag"),
        item("chickens.log", N_("Stallbuch"), "bi-journal-medical"),
    ]},
    # Meshtastic-Knoten (Stall, Lagerklima): für Admins immer, sonst sobald ein Sensor in Betrieb ist
    {"key": "sensoren", "label": N_("Sensoren"), "icon": "bi-broadcast", "show": _sensors_shown, "items": [
        item("sensors.overview_page", N_("Sensoren"), "bi-broadcast", prefixes=("sensors.",)),
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
    item("admin.site_list", N_("Betriebsstätten"), "bi-house-gear"),
    item("admin.warehouses", N_("Kartoffellager"), "bi-buildings"),
    item("admin.varieties", N_("Sorten"), "bi-palette"),
    item("admin.plots", N_("Schläge"), "bi-map", endpoints=("admin.plots_kml_preview", "admin.plots_csv_preview",
                                                             "admin.plots_plan_preview")),
    item("admin.seed_classes", N_("Stufen"), "bi-award"),
    item("admin.box_types", N_("Kistentypen"), "bi-box"),
    item("admin.users", N_("Benutzer"), "bi-people"),
    item("team.instructions", N_("Unterweisungen"), "bi-person-check", prefixes=("team.instruction",)),
    item("admin.data", N_("Import / Export"), "bi-arrow-down-up", endpoints=("admin.import_preview", "admin.import_apply")),
    item("admin.tile_defaults", N_("Startseite"), "bi-grid-1x2"),
    item("admin.backups", N_("Datensicherung"), "bi-shield-check", endpoints=("admin.backup_now", "admin.backup_download")),
    item("admin.audit_log", N_("Protokoll"), "bi-journal-text"),
]}
# Kurzbeschreibungen für die Kacheln der Verwaltungs-Startseite
ADMIN_HINTS = {
    "admin.settings": N_("Hofname, Anschrift, Standort für das Wetter"),
    "admin.site_list": N_("Hofstellen mit eigener Anschrift für den Lieferschein"),
    "admin.warehouses": N_("Reihen, Plätze, Höhe, Beschriftung"),
    "admin.audit_log": N_("Wer hat was geändert oder gelöscht"),
    "admin.varieties": N_("Kartoffelsorten mit Farbe"),
    "admin.plots": N_("Schläge, Import aus Myfarm24"),
    "admin.seed_classes": N_("Saatgut-Stufen (PB, SE, E …)"),
    "admin.box_types": N_("Kistengrößen und Gewicht"),
    "admin.users": N_("Konten, Rollen, Sprache"),
    "team.instructions": N_("Pflicht-Unterweisungen mit Unterschrift"),
    "admin.data": N_("Excel-Vorlage, Import, Jahres-Export"),
    "admin.backups": N_("Nächtliche Sicherung, Stände herunterladen"),
    "admin.tile_defaults": N_("Bereiche und Kacheln je Rolle"),
}


def _active(it, endpoint):
    return endpoint in it["endpoints"] or any(endpoint.startswith(p) for p in it["prefixes"])


def _visible(items, user):
    return [it for it in items if user.has_role(it["role"]) and (it.get("show") is None or it["show"](user))]


def _area_shown(area, user):
    # nur mit mindestens einem sichtbaren Unterpunkt (der erste ist das Ziel des Reiters)
    return (area.get("show") is None or area["show"](user)) and bool(_visible(area["items"], user))


def resolve(endpoint, view_args=None):
    """Station des Hühnerstalls gehört zur Rubrik Hühner, die der Baumkontrolle zum Bestattungswald (sonst Biogas)."""
    if endpoint == "rounds.station" and (view_args or {}).get("code"):
        from .models import CheckStation
        from .rounds import normalize_code
        station = CheckStation.query.filter_by(code=normalize_code(view_args["code"]) or "").first()
        if station is not None and station.area in ("huehner", "wald"):
            return "chickens.overview" if station.area == "huehner" else "forest.overview"
    return endpoint


# Benutzer „nur eine Rubrik“ (Hühner bzw. Bestattungswald): die Rubrik plus Aufgaben (area_only.py)
ONLY_TASKS = item("tasks.task_list", N_("Aufgaben"), "bi-list-check", prefixes=("tasks.",), badge="tasks")


def _only_area(user):
    area = next(a for a in AREAS if a["key"] == user.only_area)
    return {**area, "items": area["items"] + [ONLY_TASKS]}


def _only_home(user):
    from .area_only import config
    return config(user)["home"]


def _active_item(items, endpoint):
    """Genau passender Unterpunkt vor dem über den Präfix (Technische Plätze liegen unter „machines.“)."""
    exact = next((it["endpoint"] for it in items if endpoint in it["endpoints"]), None)
    return exact or next((it["endpoint"] for it in items if _active(it, endpoint)), None)


def current(endpoint, user):
    """{"areas": [...], "area": aktueller Bereich, "items": seine sichtbaren Unterpunkte, "active": Endpunkt}"""
    endpoint = endpoint or ""
    if user.restricted:
        area = _only_area(user)
        items = _visible(area["items"], user)
        active = _active_item(items, endpoint)
        return {"areas": [{**area, "href": url_for(_only_home(user))}], "area": area, "items": items,
                "active": active}
    area = None
    if endpoint.startswith("admin.") or any(_active(it, endpoint) for it in ADMIN["items"]):
        area = ADMIN
    else:
        area = next((a for a in AREAS if any(_active(it, endpoint) for it in a["items"])), None)
    items = _visible(area["items"], user) if area else []
    active = _active_item(items, endpoint)
    areas = [{**a, "href": url_for(_visible(a["items"], user)[0]["endpoint"])} for a in AREAS if _area_shown(a, user)]
    return {"areas": areas, "area": area, "items": items, "active": active}


# --- Handy: untere Leiste und Bereichsseiten ----------------------------------------------
# Unten: Heute · [frei] · Scannen · [frei] · Mehr – die zwei freien Plätze wählt jeder selbst (tabbar.py).
# Rubriken öffnen eine Bereichsseite (`main.area`) mit großen Zeilen, „Lager“ die eigene Lager-Übersicht;
# die Reiterleiste oben gibt es nur am PC.
HUBS = {"lager": N_("Lager"), "feld": N_("Feld & Hof"), "mehr": N_("Mehr")}
MORE_EXTRA = [
    item("admin.index", N_("Verwaltung"), "bi-sliders", role=ROLE_ADMIN),
    item("auth.account", N_("Mein Konto"), "bi-person-gear"),
]


def area_of(endpoint):
    """Schlüssel der Rubrik, zu der ein Endpunkt gehört (oder None)."""
    area = next((a for a in AREAS if any(_active(it, endpoint) for it in a["items"])), None)
    return area["key"] if area else None


def hub_title(key):
    if key in HUBS:
        return HUBS[key]
    area = next((a for a in AREAS if a["key"] == key and key != "start"), None)
    return area["label"] if area else None


def _tabs(user):
    from .tabbar import keys_for
    return keys_for(user) if user is not None and user.is_authenticated else ["lager", "feld"]


def tab_for(endpoint, view_args=None, user=None):
    """Welcher Punkt der unteren Leiste zum Endpunkt gehört: heute | scan | mehr | eine Rubrik der Leiste."""
    endpoint = endpoint or ""
    if user is not None and user.is_authenticated and user.restricted:
        from .area_only import config
        tab = config(user)["tab"]
        return {_only_home(user): "heute", tab["endpoint"]: tab["key"], "main.scan": "scan",
                "forest.map_page": "waldkarte"}.get(
            endpoint, "aufgaben" if endpoint.startswith("tasks.") else "mehr")
    tabs = _tabs(user)
    if endpoint == "main.dashboard":
        return "heute"
    if endpoint == "main.scan":
        return "scan"
    if endpoint == "main.batch_store":
        return "einlagern"
    if "aufgaben" in tabs and (endpoint in ("team.day_plan", "team.open_page") or endpoint.startswith("tasks.")):
        return "aufgaben"
    key = (view_args or {}).get("key") if endpoint == "main.area" else area_of(endpoint)
    return key if key in tabs else "mehr"


def hub(key, user):
    """Gruppen der Bereichsseite: [{"label", "items": [...]}] – nur, was die Rolle darf."""
    by_key = {a["key"]: a for a in AREAS}
    if user.restricted:
        if key not in ("mehr", user.only_area):
            return []
        groups = [{"label": None, "items": _visible(_only_area(user)["items"], user)}]
        return groups + ([{"label": None, "items": _visible(MORE_EXTRA, user)}] if key == "mehr" else [])
    if key != "mehr":
        area = by_key[key]
        if not _area_shown(area, user):
            return []
        items = [it for it in _visible(area["items"], user) if it["endpoint"] != "main.scan"]
        return [{"label": None, "items": items}]
    tabs = _tabs(user)
    start = [it for it in _visible(by_key["start"]["items"], user) if it["endpoint"] != "main.dashboard"]
    groups = [{"label": None, "items": start}]
    # Lager und Feld & Hof haben viele Seiten – unter „Mehr“ je eine Zeile zur Bereichsseite
    overview = [{**item("main.storage_home" if k == "lager" else "main.area", by_key[k]["label"], by_key[k]["icon"]),
                 "args": {} if k == "lager" else {"key": k}}
                for k in ("lager", "feld") if k not in tabs and _visible(by_key[k]["items"], user)]
    groups.append({"label": None, "items": overview})
    for k in ("verkauf", "biogas", "wald", "huehner", "sensoren"):
        if k in tabs or not _area_shown(by_key[k], user):
            continue
        items = _visible(by_key[k]["items"], user)
        if len(items) == 1:                            # Rubrik mit einer Seite: keine eigene Überschrift
            overview.append(items[0])
        else:
            groups.append({"label": by_key[k]["label"], "items": items})
    groups.append({"label": None, "items": _visible(MORE_EXTRA, user)})
    return [g for g in groups if g["items"]]


def crumb(endpoint, view_args=None, user=None):
    """Zurück-Link am Handy über dem Seiteninhalt (statt der Reiterleiste): (Ziel-URL, Text) oder None."""
    endpoint = endpoint or ""
    if endpoint in ("main.dashboard", "main.scan") or not endpoint:
        return None
    if (endpoint.startswith("admin.") or any(_active(it, endpoint) for it in ADMIN["items"])) \
            and endpoint != "admin.index":
        return url_for("admin.index"), ADMIN["label"]
    if user is not None and user.restricted:
        from .area_only import config
        conf = config(user)
        if endpoint in (conf["home"], conf["tab"]["endpoint"], "tasks.task_list", "main.area"):
            return None
        if endpoint.startswith("tasks."):
            return url_for("tasks.task_list"), ONLY_TASKS["label"]
        return url_for(conf["home"]), conf["label"]
    if endpoint == "assistant.query_detail":
        return url_for("assistant.ask_page"), N_("Fragen an die Daten")
    tabs = _tabs(user)
    more = (url_for("main.area", key="mehr"), HUBS["mehr"])
    if endpoint == "main.area":
        key = (view_args or {}).get("key")
        return None if key == "mehr" or key in tabs else more
    if endpoint == "main.storage_home":
        return None if "lager" in tabs else more
    key = area_of(endpoint)
    if key is None or key == "start":
        return more
    if key == "lager":
        return url_for("main.storage_home"), HUBS["lager"]
    area = next(a for a in AREAS if a["key"] == key)
    if user is not None and len([it for it in _visible(area["items"], user) if it["endpoint"] != "main.scan"]) > 1:
        return url_for("main.area", key=key), area["label"]
    return None if key in tabs else more


def admin_tiles():
    return [{**it, "hint": ADMIN_HINTS.get(it["endpoint"]), "url": url_for(it["endpoint"])}
            for it in ADMIN["items"] if it["endpoint"] != "admin.index"]


# --- Befehlssuche (Strg+K) -------------------------------------------------------------
EXTRA_COMMANDS = [
    # (Endpunkt, Text, Symbol, Rolle, Bedingung)
    ("main.scan", N_("Kiste scannen"), "bi-qr-code-scan", ROLE_MITARBEITER, None),
    ("main.batch_store", N_("Partie einlagern"), "bi-box-arrow-in-down", ROLE_MITARBEITER, None),
    ("fuel.tank", N_("Tanken eintragen"), "bi-fuel-pump", ROLE_MITARBEITER, None),
    ("main.tile_settings", N_("Startseite anpassen"), "bi-sliders", ROLE_BETRACHTER, None),
    ("auth.account", N_("Mein Konto"), "bi-person-circle", ROLE_BETRACHTER, None),
]


def commands(user):
    """Alle Seiten, die der Benutzer öffnen darf, als Liste für die Befehlssuche."""
    from .i18n import gettext as _
    out, seen = [], set()

    def add(endpoint, label, icon, group, **args):
        try:
            url = url_for(endpoint, **args)
        except Exception:
            return
        if url in seen:
            return
        seen.add(url)
        out.append({"label": _(label), "icon": icon, "group": group, "url": url})

    areas = [_only_area(user)] if user.restricted else [a for a in AREAS if _area_shown(a, user)]
    for area in areas:
        for it in _visible(area["items"], user):
            add(it["endpoint"], it["label"], it["icon"], _(area["label"]))
    if not user.restricted:
        for endpoint, label, icon, role, cond in EXTRA_COMMANDS:
            if user.has_role(role) and (cond is None or cond(user)):
                add(endpoint, label, icon, _("Aktion"))
    if user.is_admin:
        for it in ADMIN["items"]:
            add(it["endpoint"], it["label"], it["icon"], _(ADMIN["label"]))
    return out
