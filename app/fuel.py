"""Tankbuch des Hoftanks: Tanken in Maschinen, Lieferungen, Peilung, Füllstand und Verbrauch.

Füllstand = letzte Peilung (oder 0) + spätere Lieferungen − spätere Tankungen.
Verbrauch je Maschine aus den Zählerständen beim Tanken (Annahme: jedes Mal vollgetankt).
Wie `storage.py` verändern die Funktionen nur die Session, committen muss der Aufrufer.
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, func, or_

from . import machines
from .extensions import db
from .i18n import gettext as _
from .models import FuelEntry, Machine, Setting
from .storage import StorageError

MAX_LITERS = Decimal("100000")


def settings():
    """Tankgröße und Warnschwelle in Litern (None = nicht eingestellt)."""
    def number(key):
        setting = db.session.get(Setting, key)
        try:
            return int(setting.value) if setting and setting.value else None
        except ValueError:
            return None
    return {"capacity": number("tank_capacity"), "warn": number("tank_warn")}


def save_settings(capacity, warn):
    Setting.put("tank_capacity", str(capacity) if capacity else None)
    Setting.put("tank_warn", str(warn) if warn else None)


def _liters(value):
    try:
        liters = Decimal(str(value)).quantize(Decimal("0.1"))
    except (InvalidOperation, ValueError, TypeError):
        liters = None
    if liters is None or liters <= 0 or liters > MAX_LITERS:
        raise StorageError(_("Bitte die Liter als Zahl angeben, z. B. 85,5."))
    return liters


def parse_liters(text):
    """„1.250,5“ / „85,5“ / „85.5“ → Decimal (für Formulare)."""
    text = (text or "").strip().replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return _liters(text) if text else None


# --- Buchen -------------------------------------------------------------------

def refuel(machine, user, liters, counter=None, day=None, note=None):
    """Aus dem Hoftank in eine Maschine getankt. Ein höherer Zählerstand wird an der Maschine übernommen."""
    if not machine.uses_fuel:
        raise StorageError(_("{name} wird nicht getankt (z. B. Anhänger oder Anbaugerät).", name=machine.name))
    liters = _liters(liters)
    if counter is not None:
        if not machine.counter_unit:
            counter = None
        elif counter < 0:
            raise StorageError(_("Bitte einen gültigen Zählerstand angeben."))
        elif machine.counter is not None and counter < machine.counter:
            raise StorageError(_("Der Zählerstand ist kleiner als der letzte ({counter} {unit}).",
                                 counter=machine.counter, unit=machine.unit_short))
    entry = FuelEntry(kind="tanken", date=day or date.today(), liters=liters, machine_id=machine.id,
                      counter=counter, note=note or None, user_id=user.id if user else None)
    db.session.add(entry)
    machines._raise_counter(machine, counter)
    db.session.flush()
    return entry


def delivery(user, liters, price_per_liter=None, supplier_id=None, day=None, note=None):
    entry = FuelEntry(kind="lieferung", date=day or date.today(), liters=_liters(liters),
                      price_per_liter=price_per_liter, supplier_id=supplier_id, note=note or None,
                      user_id=user.id if user else None)
    db.session.add(entry)
    db.session.flush()
    return entry


def dip(user, liters, day=None, note=None):
    """Gemessener Füllstand („gepeilt“) – ab hier wird neu gerechnet. 0 Liter ist erlaubt (Tank leer)."""
    try:
        value = Decimal(str(liters)).quantize(Decimal("0.1"))
    except (InvalidOperation, ValueError, TypeError):
        value = None
    if value is None or value < 0 or value > MAX_LITERS:
        raise StorageError(_("Bitte die Liter als Zahl angeben, z. B. 85,5."))
    entry = FuelEntry(kind="peilung", date=day or date.today(), liters=value, note=note or None,
                      user_id=user.id if user else None)
    db.session.add(entry)
    db.session.flush()
    return entry


# --- Auswerten ------------------------------------------------------------------

def level(day=None):
    """Füllstand in Litern (Decimal) – aktuell oder am Ende von `day` – und die letzte Peilung davor (oder None)."""
    dips = FuelEntry.query.filter_by(kind="peilung")
    if day is not None:
        dips = dips.filter(FuelEntry.date <= day)
    last_dip = dips.order_by(FuelEntry.date.desc(), FuelEntry.id.desc()).first()
    query = db.session.query(FuelEntry.kind, func.coalesce(func.sum(FuelEntry.liters), 0)) \
        .filter(FuelEntry.kind.in_(("lieferung", "tanken")))
    if day is not None:
        query = query.filter(FuelEntry.date <= day)
    if last_dip is not None:
        query = query.filter(or_(FuelEntry.date > last_dip.date,
                                 and_(FuelEntry.date == last_dip.date, FuelEntry.id > last_dip.id)))
    sums = dict(query.group_by(FuelEntry.kind).all())
    base = last_dip.liters if last_dip is not None else Decimal(0)
    return Decimal(base) + Decimal(sums.get("lieferung", 0)) - Decimal(sums.get("tanken", 0)), last_dip


def status():
    """Für Tankbuch-Seite und Übersicht: Stand, Prozent, Warnung."""
    current, last_dip = level()
    conf = settings()
    percent = None
    if conf["capacity"]:
        percent = max(0, min(100, round(float(current) / conf["capacity"] * 100)))
    low = conf["warn"] is not None and current <= conf["warn"]
    has_entries = db.session.query(FuelEntry.id).first() is not None
    return {"liters": current, "percent": percent, "low": low, "last_dip": last_dip, "has_entries": has_entries,
            **conf}


def last_price():
    entry = FuelEntry.query.filter(FuelEntry.kind == "lieferung", FuelEntry.price_per_liter.isnot(None)) \
        .order_by(FuelEntry.date.desc(), FuelEntry.id.desc()).first()
    return entry.price_per_liter if entry else None


def consumption(machine, last=10):
    """Durchschnittsverbrauch aus den letzten Tankungen mit Zählerstand: l/h bzw. l/100 km (oder None)."""
    rows = machine.fuel_entries.filter(FuelEntry.kind == "tanken", FuelEntry.counter.isnot(None)) \
        .order_by(FuelEntry.counter.desc(), FuelEntry.id.desc()).limit(last + 1).all()
    rows.reverse()
    if len(rows) < 2 or not machine.counter_unit:
        return None
    distance = rows[-1].counter - rows[0].counter
    if distance <= 0:
        return None
    liters = sum(float(r.liters) for r in rows[1:])   # die erste Tankung füllt nur auf den Ausgangsstand
    value = liters / distance * (100 if machine.counter_unit == "km" else 1)
    return {"value": value, "unit": "l/100 km" if machine.counter_unit == "km" else "l/h", "count": len(rows) - 1}


def liters_in_year(machine_id=None, year=None):
    year = year or date.today().year
    query = db.session.query(func.coalesce(func.sum(FuelEntry.liters), 0)).filter(
        FuelEntry.kind == "tanken", FuelEntry.date >= date(year, 1, 1), FuelEntry.date <= date(year, 12, 31))
    if machine_id is not None:
        query = query.filter(FuelEntry.machine_id == machine_id)
    return Decimal(query.scalar() or 0)


def per_machine(year=None):
    """Liter je Maschine im Jahr mit Verbrauch – für die Tabelle im Tankbuch."""
    year = year or date.today().year
    rows = db.session.query(FuelEntry.machine_id, func.sum(FuelEntry.liters), func.count(FuelEntry.id)).filter(
        FuelEntry.kind == "tanken", FuelEntry.date >= date(year, 1, 1), FuelEntry.date <= date(year, 12, 31)) \
        .group_by(FuelEntry.machine_id).all()
    result = []
    for machine_id, liters, count in rows:
        machine = db.session.get(Machine, machine_id)
        if machine is not None:
            result.append({"machine": machine, "liters": Decimal(liters), "count": count,
                           "consumption": consumption(machine)})
    return sorted(result, key=lambda r: -r["liters"])
