"""Betriebsstätten: Stammdaten, Zuordnung (Schläge, Lager, Schüttlager, Maschinen, Tank, Lieferscheine),
Absender auf dem Lieferschein und der Filter „Betrieb“ in den Listen (gemerkt im Cookie `betrieb`).

Wie `storage.py` committen die Funktionen nicht selbst.
"""
from flask import has_request_context, request
from sqlalchemy import func

from .extensions import db
from .i18n import gettext as _
from .models import BulkStore, Delivery, Machine, Plot, Setting, Site, Warehouse
from .storage import StorageError

COOKIE = "betrieb"
ARG = "betrieb"
# was einer Betriebsstätte zugeordnet sein kann (für die Zählung in der Verwaltung)
ASSIGNABLE = (("plots", Plot), ("warehouses", Warehouse), ("bulk_stores", BulkStore), ("machines", Machine),
              ("deliveries", Delivery))


def sites(active_only=True):
    query = Site.query
    if active_only:
        query = query.filter_by(active=True)
    return query.order_by(Site.name).all()


def any_sites():
    """Gibt es überhaupt Betriebsstätten? (sonst bleiben Auswahl und Filter unsichtbar)"""
    # je Anfrage zwischengespeichert (die Vorlagen fragen mehrfach)
    cached = getattr(request, "_has_sites", None) if has_request_context() else None
    if cached is None:
        cached = db.session.query(Site.id).filter_by(active=True).first() is not None
        if has_request_context():
            request._has_sites = cached
    return cached


def save_site(site, name, address=None, phone=None, email=None, passport_reg=None, note=None, active=True):
    name = (name or "").strip()[:100]
    if not name:
        raise StorageError(_("Bitte einen Namen angeben."))
    other = Site.query.filter(func.lower(Site.name) == name.lower()).first()
    if other is not None and other is not site:
        raise StorageError(_("Die Betriebsstätte „{name}“ gibt es schon.", name=name))
    site.name = name
    site.address = (address or "").strip()[:500] or None
    site.phone = (phone or "").strip()[:50] or None
    site.email = (email or "").strip()[:120] or None
    site.passport_reg = (passport_reg or "").strip()[:40] or None
    site.note = (note or "").strip() or None
    site.active = bool(active)
    if site.id is None:
        db.session.add(site)
    db.session.flush()
    return site


def usage(site):
    """{"plots": n, "warehouses": n, …} – wie viel der Betriebsstätte zugeordnet ist."""
    counts = {key: model.query.filter_by(site_id=site.id).count() for key, model in ASSIGNABLE}
    counts["tank"] = 1 if tank_site_id() == site.id else 0
    return counts


def delete_site(site):
    """Löschen, wenn nichts mehr zugeordnet ist – sonst nur deaktivieren. True = gelöscht."""
    if any(usage(site).values()):
        site.active = False
        return False
    db.session.delete(site)
    db.session.flush()
    return True


def from_form(value):
    """Auswahlfeld „Betriebsstätte“ → site_id oder None (unbekannte Werte werden ignoriert)."""
    try:
        site_id = int(value or 0)
    except ValueError:
        return None
    return site_id if site_id and db.session.get(Site, site_id) else None


# --- Tank ---------------------------------------------------------------------

def tank_site_id():
    setting = db.session.get(Setting, "tank_site")
    try:
        return int(setting.value) if setting and setting.value else None
    except ValueError:
        return None


def set_tank_site(site_id):
    Setting.put("tank_site", str(site_id) if site_id else None)


# --- Absender auf dem Lieferschein ----------------------------------------------

def sender(delivery, farm):
    """Angaben für den Kopf des Lieferscheins: die des Hofs, mit Anschrift/Kontakt der Betriebsstätte, wenn der
    Lieferschein einer zugeordnet ist (der Hofname bleibt, die Betriebsstätte steht darunter)."""
    data = dict(farm)
    site = delivery.site
    if site is None:
        return data
    data["site_name"] = site.name if site.name != farm.get("farm_name") else None
    for key, value in (("farm_address", site.address), ("farm_phone", site.phone), ("farm_email", site.email),
                       ("farm_passport_reg", site.passport_reg)):
        if value:
            data[key] = value
    return data


# --- Filter „Betrieb“ in den Listen ---------------------------------------------

def chosen():
    """Gewählte Betriebsstätte für die Listen: aus `?betrieb=` (leer = alle), sonst aus dem Cookie."""
    if not has_request_context():
        return None
    if hasattr(request, "_site_filter"):
        return request._site_filter
    raw = request.args.get(ARG) if ARG in request.args else request.cookies.get(COOKIE, "")
    site = None
    if raw and raw.isdigit() and any_sites():
        site = db.session.get(Site, int(raw))
        if site is not None and not site.active:
            site = None
    request._site_filter = site
    return site


def default_id():
    """Vorauswahl in neuen Formularen: die im Filter gewählte Betriebsstätte, sonst die einzige."""
    site = chosen()
    if site:
        return site.id
    active = sites() if any_sites() else []
    return active[0].id if len(active) == 1 else None


def apply(query, model):
    site = chosen()
    return query.filter(model.site_id == site.id) if site else query


def keep(items):
    """Liste von Objekten mit `site_id` auf die gewählte Betriebsstätte beschränken."""
    site = chosen()
    return [i for i in items if i.site_id == site.id] if site else list(items)


def remember(response):
    """Nach der Anfrage: Wahl aus `?betrieb=` im Cookie merken (ein Jahr)."""
    if ARG in request.args and response.status_code < 400:
        value = request.args.get(ARG, "")
        if value.isdigit():
            response.set_cookie(COOKIE, value, max_age=365 * 86400, samesite="Lax", httponly=True,
                                secure=request.is_secure)
        else:
            response.delete_cookie(COOKIE)
    return response
