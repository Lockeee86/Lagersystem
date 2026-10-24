"""Schüttgut-Lager (Getreide u. Ä.): Lagerorte, Einlagern (Fuhre mit Schlag, Waage, Qualität), Auslagern
(Eigenverbrauch hier, Verkauf über den Lieferschein – `sales.add_bulk`).

Jeder Lagerort enthält eine Ware (Kultur, optional Sorte). Die Ware wird beim ersten Einlagern in den leeren
Lagerort gesetzt; solange etwas drin ist, passt nur dieselbe Ware dazu. Bestand, Qualität (Mittel nach Menge)
und Anteile je Schlag ergeben sich aus den Buchungen. Wie `storage.py` committen die Funktionen nicht selbst.
"""
import re
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from sqlalchemy import func

from .extensions import db
from .i18n import gettext as _
from .models import BULK_KINDS, BULK_PURPOSES, BULK_QUALITY, BulkEntry, BulkStore
from .storage import StorageError

CODE_RE = re.compile(r"^S-?(\d+)$")

QUALITY_RANGES = {"moisture": (0, 60), "protein": (0, 40), "hl_weight": (30, 100), "falling_number": (0, 1000),
                  "admixture": (0, 100)}


def parse_number(text, minimum=None, maximum=None, integer=False):
    """„12,5“ → Decimal('12.5'), „38.000“ → 38000 (Punkt als Tausendertrenner wie auf dem Wiegeschein);
    leer oder außerhalb der Grenzen → None."""
    text = (text or "").strip().replace(" ", "").replace("\u00a0", "")
    if not text:
        return None
    if "," in text or re.fullmatch(r"-?\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "").replace(",", ".")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite() or (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        return None
    return int(value) if integer else value


def _same_ware(a, b):
    return (a or "").strip().casefold() == (b or "").strip().casefold()


# --- Lagerorte ------------------------------------------------------------------

def normalize_code(raw):
    """„S-001“, „s1“ oder eine URL, die auf den Code endet → „S-001“; sonst None."""
    if not raw:
        return None
    m = CODE_RE.match(raw.strip().upper().rstrip("/").rsplit("/", 1)[-1])
    return f"S-{int(m.group(1)):03d}" if m else None


def next_code():
    numbers = [int(m.group(1)) for (c,) in db.session.query(BulkStore.code).filter(BulkStore.code.isnot(None))
               if (m := CODE_RE.match(c))]
    return f"S-{max(numbers, default=0) + 1:03d}"


def save_store(store, name, kind, location=None, capacity_t=None, bag_kg=None, note=None, active=True, site_id=None):
    name = (name or "").strip()[:100]
    if not name:
        raise StorageError(_("Bitte einen Namen angeben."))
    if kind not in BULK_KINDS:
        raise StorageError(_("Unbekannte Lagerart."))
    other = BulkStore.query.filter(func.lower(BulkStore.name) == name.lower()).first()
    if other is not None and other is not store:
        raise StorageError(_("Den Lagerort „{name}“ gibt es schon.", name=name))
    store.name, store.kind, store.location = name, kind, (location or "").strip()[:100] or None
    store.capacity_t, store.note, store.active = capacity_t, (note or "").strip() or None, bool(active)
    store.bag_kg = bag_kg if kind == "bigbag" else None
    store.site_id = site_id
    if not store.code:
        store.code = next_code()
    if store.id is None:
        db.session.add(store)
    db.session.flush()
    return store


def stock(store):
    """{"kg": Bestand, "bags": Anzahl Big Bags oder None, "percent": Füllgrad oder None}"""
    rows = (db.session.query(BulkEntry.kind, func.coalesce(func.sum(BulkEntry.net_kg), 0),
                             func.coalesce(func.sum(BulkEntry.bags), 0))
            .filter(BulkEntry.store_id == store.id).group_by(BulkEntry.kind).all()) if store.id else []
    sums = {kind: (int(kg), int(bags)) for kind, kg, bags in rows}
    kg = sums.get("ein", (0, 0))[0] - sums.get("aus", (0, 0))[0]
    bags = sums.get("ein", (0, 0))[1] - sums.get("aus", (0, 0))[1] if store.kind == "bigbag" else None
    percent = None
    if store.capacity_t:
        percent = max(0, min(100, round(kg / (float(store.capacity_t) * 1000) * 100)))
    return {"kg": kg, "bags": bags, "percent": percent}


def _intakes_since_empty(store):
    """Fuhren der aktuellen Füllung (seit der Lagerort zuletzt leer war), älteste zuerst."""
    entries = sorted(store.entries, key=lambda e: (e.date, e.id or 0))
    level, current = 0, []
    for e in entries:
        level += e.net_kg if e.kind == "ein" else -e.net_kg
        if e.kind == "ein":
            current.append(e)
        if level <= 0:
            level, current = 0, []
    return current


def quality(store):
    """Qualität der aktuellen Füllung als Mittel nach Menge (nur Fuhren mit Wert): {feld: Decimal oder None}."""
    intakes = _intakes_since_empty(store)
    result = {}
    for field in BULK_QUALITY:
        pairs = [(getattr(e, field), e.net_kg) for e in intakes if getattr(e, field) is not None and e.net_kg]
        weight = sum(kg for _v, kg in pairs)
        step = Decimal("1") if field == "falling_number" else Decimal("0.1")
        result[field] = (sum(Decimal(v) * kg for v, kg in pairs) / weight).quantize(step) if weight else None
    return result


def plot_shares(store):
    """Herkunft der aktuellen Füllung: [{plot, kg, percent}] (größter Anteil zuerst; plot None = ohne Schlag)."""
    shares = OrderedDict()
    for e in _intakes_since_empty(store):
        key = e.plot_id or 0
        entry = shares.setdefault(key, {"plot": e.plot, "kg": 0})
        entry["kg"] += e.net_kg
    total = sum(s["kg"] for s in shares.values())
    rows = sorted(shares.values(), key=lambda s: -s["kg"])
    for s in rows:
        s["percent"] = round(100 * s["kg"] / total) if total else 0
    return rows


def _weights(gross_kg, tare_kg, net_kg):
    if gross_kg is not None and tare_kg is not None:
        if tare_kg >= gross_kg:
            raise StorageError(_("Das Leergewicht ist größer als das Vollgewicht."))
        net_kg = gross_kg - tare_kg
    if not net_kg or net_kg <= 0:
        raise StorageError(_("Bitte das Gewicht angeben (netto oder brutto und tara)."))
    return net_kg


def take_in(store, user, day, crop, net_kg=None, gross_kg=None, tare_kg=None, variety_name=None, plot=None,
            bags=None, vehicle=None, note=None, **quality_values):
    """Fuhre einlagern. Leerer Lagerort übernimmt die Ware, sonst muss sie passen."""
    crop = (crop or "").strip()[:100] or store.crop
    variety_name = (variety_name or "").strip()[:100] or None
    if not crop:
        raise StorageError(_("Bitte die Ware (Kultur) angeben."))
    net_kg = _weights(gross_kg, tare_kg, net_kg)
    if store.kind == "bigbag" and not bags:
        raise StorageError(_("Bitte die Anzahl der Big Bags angeben."))
    level = stock(store)["kg"]
    if level > 0 and store.crop and not (_same_ware(store.crop, crop)
                                         and (not store.variety_name or not variety_name
                                              or _same_ware(store.variety_name, variety_name))):
        raise StorageError(_("In „{store}“ liegt {ware} – bitte einen anderen Lagerort wählen.",
                             store=store.name, ware=store.ware))
    if level <= 0:
        store.crop, store.variety_name = crop, variety_name
    elif variety_name and not store.variety_name:
        store.variety_name = variety_name
    entry = BulkEntry(store=store, kind="ein", date=day, crop=crop, variety_name=variety_name or store.variety_name,
                      plot_id=plot.id if plot else None, gross_kg=gross_kg, tare_kg=tare_kg, net_kg=net_kg,
                      bags=bags if store.kind == "bigbag" else None, vehicle=(vehicle or "").strip()[:50] or None,
                      note=(note or "").strip()[:300] or None, user_id=user.id if user else None,
                      **{k: v for k, v in quality_values.items() if k in BULK_QUALITY})
    db.session.add(entry)
    db.session.flush()
    return entry


def take_out(store, user, day, purpose, net_kg=None, gross_kg=None, tare_kg=None, bags=None, delivery=None,
             note=None):
    """Auslagern (Eigenverbrauch oder – mit `delivery` – Verkauf). Mehr als drin ist geht nicht."""
    if purpose not in BULK_PURPOSES:
        raise StorageError(_("Bitte den Zweck wählen."))
    net_kg = _weights(gross_kg, tare_kg, net_kg)
    level = stock(store)
    if net_kg > level["kg"]:
        raise StorageError(_("In „{store}“ sind nur {kg} kg.", store=store.name, kg=level["kg"]))
    if store.kind == "bigbag":
        if not bags:
            raise StorageError(_("Bitte die Anzahl der Big Bags angeben."))
        if bags > (level["bags"] or 0):
            raise StorageError(_("In „{store}“ sind nur {n} Big Bags.", store=store.name, n=level["bags"] or 0))
    entry = BulkEntry(store=store, kind="aus", date=day, crop=store.crop, variety_name=store.variety_name,
                      gross_kg=gross_kg, tare_kg=tare_kg, net_kg=net_kg, bags=bags if store.kind == "bigbag" else None,
                      purpose=purpose, delivery_id=delivery.id if delivery else None,
                      note=(note or "").strip()[:300] or None, user_id=user.id if user else None)
    db.session.add(entry)
    db.session.flush()
    return entry


def delete_entry(entry):
    """Buchung löschen (Admin). Auslagerungen eines Lieferscheins nur über den Lieferschein."""
    if entry.delivery_id:
        raise StorageError(_("Diese Auslagerung gehört zu Lieferschein {number} – dort entfernen.",
                             number=entry.delivery.number))
    store = entry.store
    if entry.kind == "ein" and stock(store)["kg"] - entry.net_kg < 0:
        raise StorageError(_("Dann wäre der Bestand negativ – bitte zuerst die Auslagerungen prüfen."))
    db.session.delete(entry)
    db.session.flush()


# --- Übersichten -----------------------------------------------------------------

def stores(active_only=True, site=None):
    query = BulkStore.query
    if active_only:
        query = query.filter_by(active=True)
    if site is not None:
        query = query.filter_by(site_id=site.id)
    return query.order_by(BulkStore.location, BulkStore.name).all()


def overview(site=None):
    """Für Liste und Lager-Übersicht: [{store, stock}] und Summe je Ware {ware: kg} (optional je Betriebsstätte)."""
    rows = [{"store": s, "stock": stock(s)} for s in stores(site=site)]
    per_ware = OrderedDict()
    for r in sorted(rows, key=lambda r: -r["stock"]["kg"]):
        if r["stock"]["kg"] > 0:
            ware = r["store"].crop or _("ohne Ware")
            per_ware[ware] = per_ware.get(ware, 0) + r["stock"]["kg"]
    return rows, per_ware


def yields(year):
    """Eingelagerte Menge je Schlag im Jahr (kg): {plot_id: kg} – für den Ertrag t/ha auf der Schlagkarte."""
    from sqlalchemy import extract
    rows = (db.session.query(BulkEntry.plot_id, func.sum(BulkEntry.net_kg))
            .filter(BulkEntry.kind == "ein", BulkEntry.plot_id.isnot(None), extract("year", BulkEntry.date) == year)
            .group_by(BulkEntry.plot_id).all())
    return {plot_id: int(kg) for plot_id, kg in rows}


def known_crops():
    """Vorschläge für das Feld „Ware“: Kulturen aus Schlagkartei und bisherigen Buchungen."""
    from .models import PlotYear
    names = {c for (c,) in db.session.query(PlotYear.crop).filter(PlotYear.crop.isnot(None)).distinct()}
    names |= {c for (c,) in db.session.query(BulkEntry.crop).filter(BulkEntry.crop.isnot(None)).distinct()}
    return sorted(names, key=str.casefold)
