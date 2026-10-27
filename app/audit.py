"""Änderungsprotokoll: jede Änderung und jedes Löschen wird beim Speichern (before_flush) mit Benutzer, Zeit und
vorher/nachher in `AuditEntry` festgehalten – über alle Bereiche, ohne dass die Views etwas tun müssen.

Nicht protokolliert: Neuanlagen, Bewegungen der Kisten (die stehen schon im Verlauf `Movement`), persönliche
Einstellungen (Kacheln, Push-Themen, Ansicht …), Anmeldedaten (nur „Passwort geändert“) und interne Werte
(Push-Zeitpunkte, Schlüssel). Massenimporte laufen unter `paused()` und erscheinen als ein Eintrag `note()`.
Sichtbar nur für Admins (Verwaltung → Protokoll). Einträge älter als `KEEP_DAYS` löscht `cleanup()`.
"""
import datetime as dt
from contextlib import contextmanager
from decimal import Decimal

from flask import has_request_context
from flask_login import current_user
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from .i18n import N_

KEEP_DAYS = 730

# Bereich (Tabelle) → Anzeige
AREAS = {
    "settings": N_("Einstellungen"), "users": N_("Benutzer"), "sites": N_("Betriebsstätten"),
    "warehouses": N_("Kartoffellager"), "varieties": N_("Sorten"), "plots": N_("Schläge"),
    "plot_years": N_("Anbaujahre"), "field_measures": N_("Maßnahmen"), "pesticide_products": N_("Pflanzenschutzmittel"),
    "seed_classes": N_("Stufen"), "box_types": N_("Kistentypen"), "boxes": N_("Kisten"), "customers": N_("Kunden"),
    "certifications": N_("Anerkennungen"), "deliveries": N_("Lieferscheine"), "delivery_items": N_("Lieferschein-Positionen"),
    "bulk_stores": N_("Schüttlager"), "bulk_entries": N_("Schüttgut-Buchungen"), "machines": N_("Maschinen"),
    "maintenance_tasks": N_("Wartungen"), "machine_events": N_("Maschinen-Logbuch"), "photos": N_("Fotos"),
    "tasks": N_("Aufgaben"), "purchase_requests": N_("Bestellwünsche"), "suppliers": N_("Lieferanten"),
    "machine_parts": N_("Ersatzteile"), "machine_documents": N_("Dokumente"), "fuel_entries": N_("Tankbuch"),
    "calendar_entries": N_("Kalender"),
}
ACTIONS = {"geaendert": N_("geändert"), "geloescht": N_("gelöscht"), "import": N_("Import")}

# Feldnamen → Anzeige (sonst erscheint der technische Name)
FIELDS = {
    "name": N_("Name"), "note": N_("Notiz"), "active": N_("Aktiv"), "address": N_("Anschrift"), "phone": N_("Telefon"),
    "email": N_("E-Mail"), "role": N_("Rolle"), "display_name": N_("Anzeigename"), "password_hash": N_("Passwort"),
    "can_order": N_("Darf bestellen"), "can_plan": N_("Darf den Tagesplan pflegen"), "chickens": N_("Rubrik Hühner"), "area_ha": N_("Fläche"), "site_id": N_("Betriebsstätte"),
    "crop": N_("Kultur"), "variety_id": N_("Sorte"), "variety_name": N_("Sorte"), "planted_on": N_("Gepflanzt am"),
    "harvested_on": N_("Gerodet am"), "seed_note": N_("Pflanzgut"), "planned_crop": N_("Geplante Kultur"),
    "boundary": N_("Umriss"), "number": N_("Nummer"), "date": N_("Datum"), "vehicle": N_("Fahrzeug"),
    "gross_kg": N_("Vollgewicht"), "tare_kg": N_("Leergewicht"), "net_kg": N_("Netto"), "status": N_("Status"),
    "customer_id": N_("Kunde"), "weight_kg": N_("Gewicht"), "quantity": N_("Menge"), "title": N_("Titel"),
    "due_on": N_("Fällig am"), "assignee_id": N_("Zuständig"), "supplier": N_("Lieferant"), "price": N_("Preis"),
    "counter": N_("Zählerstand"), "interval_months": N_("Intervall Monate"), "interval_hours": N_("Intervall Stunden"),
    "last_done_on": N_("Zuletzt erledigt"), "liters": N_("Liter"), "value": N_("Wert"), "rows": N_("Reihen"),
    "slots": N_("Stellplätze"), "max_levels": N_("Ebenen"), "capacity_t": N_("Kapazität"), "color": N_("Farbe"),
    "usage": N_("Verwendung"), "code": N_("Code"), "kind": N_("Art"), "location": N_("Standort"),
    "wait_days": N_("Wartezeit"), "plate": N_("Kennzeichen"), "model": N_("Modell"), "manufacturer": N_("Hersteller"),
}

SKIP_TABLES = {"audit_log", "movements", "push_subscriptions"}
# Spalten, deren Änderung nicht zählt (Zeitstempel, persönliche Einstellungen, Zähler …)
SKIP_COLUMNS = {
    "*": {"updated_at", "created_at"},
    "users": {"last_login", "map_view", "tiles_add", "tiles_hide", "home_add", "home_hide", "home_order", "push_off", "push_on", "session_token",
              "calendar_token", "language", "tabbar"},
    # Meshtastic-Knoten: letzte Werte ändern sich mit jedem Paket – protokolliert wird nur die Zuordnung
    "mesh_nodes": {"last_seen", "temperature", "humidity", "co2", "battery", "voltage", "rssi", "snr"},
    # Platz und Inhalt der Kisten stehen im Verlauf (Movement) – hier nur andere Felder und das Löschen
    "machines": {"counter", "counter_at", "skve_base_counter", "skve_base_day"},   # Zähler stehen im Logbuch
    "boxes": {"warehouse_id", "row", "slot", "level", "fill", "variety_id", "plot_id", "harvest_date", "usage",
              "seed_class_id", "grade", "note", "box_type_id", "condition", "condition_note", "condition_at"},
}
MASKED = {"password_hash", "sign_farm", "sign_customer", "signature"}   # nur „geändert“, kein Wert (Hash, Unterschrift)
BIG = {"boundary"}                             # Umriss: nur „geändert“, nicht die Koordinaten
SKIP_SETTINGS_PREFIX = ("push_", "vapid", "tiles_", "tabbar_", "aerial_cache", "backup_", "audit_", "biogas_", "home_known_",
                        "coop_", "mesh_")
MAX_TEXT = 300


def _skip_setting(obj):
    return obj.__tablename__ == "settings" and str(obj.key).startswith(SKIP_SETTINGS_PREFIX)


def _value(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    text = str(value).replace("\r\n", "\n")          # Formulare schicken Zeilenumbrüche als \r\n
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + " …"


def _label(obj):
    for attr in ("code", "number", "name", "title", "username", "key", "label"):
        value = getattr(obj, attr, None)
        if value:
            label = str(value)
            break
    else:
        label = f"#{getattr(obj, 'id', '')}"
    plot = getattr(obj, "plot", None) if obj.__tablename__ in ("plot_years",) else None
    if plot is not None:
        label = f"{plot.name} {obj.year}"
    machine = getattr(obj, "machine", None) if obj.__tablename__ in ("maintenance_tasks", "machine_parts") else None
    if machine is not None:
        label = f"{machine.name}: {label}"
    return label[:200]


def _columns(obj):
    table = obj.__tablename__
    skip = SKIP_COLUMNS["*"] | SKIP_COLUMNS.get(table, set())
    return [c for c in inspect(obj).mapper.column_attrs if c.key not in skip]


def _who():
    try:
        if has_request_context() and current_user.is_authenticated:
            return current_user.id
    except Exception:     # noqa: BLE001 – ohne Login-Kontext (Zeitplan, Konsole)
        pass
    return None


def _old_values(session, obj, keys):
    """Alte Werte aus der Datenbank, wenn das Feld vor der Änderung nicht geladen war (z. B. nach einem Commit)."""
    from sqlalchemy import select
    mapper = inspect(obj).mapper
    identity = inspect(obj).identity
    if not keys or not identity:
        return {}
    cols = [mapper.columns[k] for k in keys]
    pk = mapper.primary_key
    stmt = select(*cols).where(*[c == v for c, v in zip(pk, identity)])
    row = session.connection().execute(stmt).first()
    return dict(zip(keys, row)) if row else {}


def _changes(obj, session):
    state = inspect(obj)
    result, unknown, found = {}, [], []
    for col in _columns(obj):
        hist = state.attrs[col.key].history
        if not hist.has_changes():
            continue
        if not hist.deleted:
            unknown.append(col.key)
        found.append((col.key, hist))
    old = _old_values(session, obj, unknown)
    for key, hist in found:
        before = hist.deleted[0] if hist.deleted else old.get(key)
        after = hist.added[0] if hist.added else None
        col_key = key
        if _value(before) == _value(after):
            continue
        if col_key in MASKED or col_key in BIG:
            result[col_key] = ["…", "…"]
        else:
            result[col_key] = [_value(before), _value(after)]
    return result


def _snapshot(obj):
    data = {}
    for col in _columns(obj):
        if col.key in MASKED:
            continue
        value = getattr(obj, col.key, None)
        if value is None or value == "":
            continue
        data[col.key] = "…" if col.key in BIG else _value(value)
    return data


@event.listens_for(Session, "before_flush")
def _record(session, _context, _instances):
    if session.info.get("audit_paused"):
        return
    from .models import AuditEntry
    user_id = _who()
    entries = []
    for obj in list(session.dirty):
        table = getattr(obj, "__tablename__", None)
        if table is None or table in SKIP_TABLES or _skip_setting(obj) or not session.is_modified(obj):
            continue
        changes = _changes(obj, session)
        if changes:
            entries.append(AuditEntry(user_id=user_id, action="geaendert", table=table,
                                      object_id=str(inspect(obj).identity[0]) if inspect(obj).identity else None,
                                      label=_label(obj), changes=changes))
    for obj in list(session.deleted):
        table = getattr(obj, "__tablename__", None)
        if table is None or table in SKIP_TABLES or _skip_setting(obj):
            continue
        entries.append(AuditEntry(user_id=user_id, action="geloescht", table=table,
                                  object_id=str(inspect(obj).identity[0]) if inspect(obj).identity else None,
                                  label=_label(obj), changes=_snapshot(obj)))
    for entry in entries:
        session.add(entry)


@contextmanager
def paused(session=None):
    """Für Massenimporte: einzelne Änderungen nicht protokollieren (stattdessen `note()`)."""
    from .extensions import db
    session = session or db.session()
    previous = session.info.get("audit_paused")
    session.info["audit_paused"] = True
    try:
        yield
    finally:
        session.info["audit_paused"] = previous


def note(table, label, changes=None):
    """Einen zusammenfassenden Eintrag anlegen (z. B. „Import Schlagdokumentation, 120 Schläge“)."""
    from .extensions import db
    from .models import AuditEntry
    db.session.add(AuditEntry(user_id=_who(), action="import", table=table, label=(label or "")[:200],
                              changes=changes or None))


def cleanup(app, now=None):
    """Einträge älter als KEEP_DAYS entfernen (aus dem Zeitplan, höchstens einmal am Tag)."""
    from .extensions import db
    from .models import AuditEntry, Setting, utcnow
    with app.app_context():
        now = now or utcnow()
        day = now.date().isoformat()
        setting = db.session.get(Setting, "audit_cleanup")
        if setting is not None and setting.value == day:
            return 0
        n = AuditEntry.query.filter(AuditEntry.created_at < now - dt.timedelta(days=KEEP_DAYS)).delete()
        Setting.put("audit_cleanup", day)
        db.session.commit()
        return n


# --- Anzeige --------------------------------------------------------------------

def _refs():
    from . import models as m
    return {"site_id": m.Site, "variety_id": m.Variety, "customer_id": m.Customer, "plot_id": m.Plot,
            "warehouse_id": m.Warehouse, "machine_id": m.Machine, "assignee_id": m.User, "user_id": m.User,
            "box_type_id": m.BoxType, "seed_class_id": m.SeedClass, "store_id": m.BulkStore,
            "supplier_id": m.Supplier, "certification_id": m.Certification, "delivery_id": m.Delivery}


def _show(key, value, refs, cache):
    """Wert lesbar machen: Verweise als Name, Ja/Nein, leer als –."""
    from .extensions import db
    from .i18n import gettext as _
    if value is None or value == "":
        return "–"
    if isinstance(value, bool):
        return _("Ja") if value else _("Nein")
    model = refs.get(key)
    if model is not None and isinstance(value, int):
        if (key, value) not in cache:
            obj = db.session.get(model, value)
            name = next((getattr(obj, a) for a in ("name", "code", "number", "username") if obj is not None
                         and getattr(obj, a, None)), None)
            cache[(key, value)] = name or f"#{value}"
        return cache[(key, value)]
    return str(value)


def describe(entries):
    """Für die Seite: je Eintrag die Felder als [(Bezeichnung, vorher, nachher)] (beim Löschen nachher = None)."""
    from .i18n import gettext as _
    refs, cache, result = _refs(), {}, {}
    for e in entries:
        rows = []
        for key, value in (e.changes or {}).items():
            label = _(FIELDS[key]) if key in FIELDS else key
            if e.action == "geaendert" and isinstance(value, list) and len(value) == 2:
                rows.append((label, _show(key, value[0], refs, cache), _show(key, value[1], refs, cache)))
            else:
                rows.append((label, _show(key, value, refs, cache), None))
        result[e.id] = rows
    return result
