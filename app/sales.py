"""Verkauf: Lieferscheine anlegen, Kisten verladen, abgepackte Ware und Schüttgut erfassen.

Wie in `storage.py` verändern alle Funktionen nur die Session, committen muss der Aufrufer.
Kisten werden über `storage.ship_box` gebucht, damit jede Änderung eine Bewegung erzeugt.
"""
import base64
import binascii
import io
import re
from collections import OrderedDict

from PIL import Image, UnidentifiedImageError

from sqlalchemy import func

from . import bulk, storage
from .extensions import db
from .i18n import N_, gettext as _
from .models import (DELIVERY_CLOSED, DELIVERY_OPEN, Certification, Delivery, DeliveryItem, Setting,
                     utcnow)
from .storage import StorageError

SIGNED_LOCKED = N_("Lieferschein {number} ist schon unterschrieben – zum Ändern zuerst die Unterschriften entfernen.")
NUMBER_RE = re.compile(r"^LS-(\d{4})-(\d+)$")


def next_number(day):
    """Fortlaufend je Kalenderjahr: LS-2026-0001, LS-2026-0002 …"""
    prefix = f"LS-{day.year}-"
    numbers = [int(m.group(2)) for (n,) in db.session.query(Delivery.number).filter(Delivery.number.like(prefix + "%"))
               if (m := NUMBER_RE.match(n))]
    return f"{prefix}{max(numbers, default=0) + 1:04d}"


def create_delivery(customer, day, user, vehicle=None, note=None, site_id=None):
    delivery = Delivery(number=next_number(day), customer=customer, date=day, vehicle=vehicle or None,
                        note=note or None, site_id=site_id, created_by_id=user.id if user else None)
    db.session.add(delivery)
    db.session.flush()
    return delivery


def _require_open(delivery):
    if not delivery.is_open:
        raise StorageError(_("Lieferschein {number} ist abgeschlossen.", number=delivery.number))
    if delivery.is_signed:
        raise StorageError(_(SIGNED_LOCKED, number=delivery.number))


def add_box(delivery, box, user):
    """Kiste verladen. Liefert (Position, nachgerückte Kisten)."""
    _require_open(delivery)
    # Inhalt vor dem Verladen festhalten – danach ist die Kiste leer
    snapshot = dict(variety_id=box.variety_id, usage=box.effective_usage, seed_class_id=box.seed_class_id,
                    grade=box.grade, harvest_date=box.harvest_date, plot_id=box.plot_id, fill=box.fill,
                    weight_kg=box.weight_kg)
    from_location, shifted = storage.ship_box(box, user, delivery.number)
    item = DeliveryItem(delivery=delivery, box=box, from_location=from_location,
                        user_id=user.id if user else None, **snapshot)
    db.session.add(item)
    db.session.flush()
    return item, shifted


def add_quantity(delivery, user, variety_id, usage, seed_class_id, grade, packaging, quantity, unit_kg):
    """Abgepackte Ware (Säcke, Big Bags …): Anzahl × Gewicht je Gebinde."""
    _require_open(delivery)
    if not variety_id:
        raise StorageError(_("Bitte eine Sorte wählen."))
    if not quantity or quantity < 1 or unit_kg is None or unit_kg < 1:
        raise StorageError(_("Bitte Anzahl und Gewicht je Gebinde angeben."))
    item = DeliveryItem(
        delivery=delivery, variety_id=variety_id, usage=usage or None, seed_class_id=seed_class_id,
        grade=grade or None, packaging=packaging or None, quantity=quantity, weight_kg=quantity * unit_kg,
        user_id=user.id if user else None,
    )
    db.session.add(item)
    db.session.flush()
    return item


def add_bulk(delivery, user, store, net_kg=None, gross_kg=None, tare_kg=None, bags=None):
    """Schüttgut verladen: bucht eine Auslagerung „Verkauf“ am Lagerort und eine Position auf dem Lieferschein."""
    _require_open(delivery)
    entry = bulk.take_out(store, user, delivery.date, "verkauf", net_kg=net_kg, gross_kg=gross_kg, tare_kg=tare_kg,
                          bags=bags, delivery=delivery)
    item = DeliveryItem(delivery=delivery, bulk_entry=entry, crop=store.ware or None, weight_kg=entry.net_kg,
                        quantity=entry.bags, packaging="Big Bag" if entry.bags else None, from_location=store.name,
                        user_id=user.id if user else None)
    db.session.add(item)
    db.session.flush()
    return item


def remove_item(item, user):
    delivery = item.delivery
    _require_open(delivery)
    if item.box is not None:
        storage.unship_box(item.box, item.fill, user, delivery.number)
    entry = item.bulk_entry
    db.session.delete(item)
    if entry is not None:                  # Schüttgut: Auslagerung zurücknehmen
        db.session.delete(entry)
    db.session.flush()


def set_weights(delivery, gross_kg, tare_kg):
    _require_open(delivery)
    if gross_kg is not None and tare_kg is not None and tare_kg > gross_kg:
        raise StorageError(_("Das Leergewicht ist größer als das Vollgewicht."))
    delivery.gross_kg, delivery.tare_kg = gross_kg, tare_kg


def close_delivery(delivery):
    if not delivery.is_open:
        raise StorageError(_("Lieferschein {number} ist abgeschlossen.", number=delivery.number))
    if not delivery.items:
        raise StorageError(_("Der Lieferschein hat noch keine Positionen."))
    if sign_required() and not delivery.fully_signed:
        raise StorageError(_("Bitte zuerst unterschreiben lassen (Hof und Empfänger)."))
    delivery.status = DELIVERY_CLOSED
    delivery.closed_at = utcnow()


def reopen_delivery(delivery):
    delivery.status = DELIVERY_OPEN
    delivery.closed_at = None


# --- Unterschriften ----------------------------------------------------------

SIGNERS = ("hof", "kunde")                    # URL-Teil → Spalten sign_farm / sign_customer
SIGN_MAX_BYTES = 400_000                      # Daten-URL vom Handy
SIGN_WIDTH = 600                              # gespeichert höchstens so breit (reicht fürs PDF)


def sign_required():
    """Muss vor dem Abschließen unterschrieben werden? (Admin-Einstellung bei den Lieferscheinen)"""
    setting = db.session.get(Setting, "delivery_sign_required")
    return bool(setting and setting.value == "1")


def save_sign_required(required):
    Setting.put("delivery_sign_required", "1" if required else None)


def _column(who):
    if who not in SIGNERS:
        raise StorageError(_("Unbekannte Unterschrift."))
    return "sign_farm" if who == "hof" else "sign_customer"


def signature(delivery, who):
    """(Bild, Name, Zeitpunkt) oder (None, None, None)."""
    col = _column(who)
    return getattr(delivery, col), getattr(delivery, f"{col}_name"), getattr(delivery, f"{col}_at")


def _signature_png(data_url):
    """Daten-URL aus dem Zeichenfeld → zugeschnittenes PNG, schwarz auf weiß. Leeres Feld → Fehler."""
    prefix = "data:image/png;base64,"
    if not data_url or len(data_url) > SIGN_MAX_BYTES or not data_url.startswith(prefix):
        raise StorageError(_("Bitte im Feld unterschreiben."))
    try:
        raw = base64.b64decode(data_url[len(prefix):], validate=True)
        with Image.open(io.BytesIO(raw)) as img:
            if img.format != "PNG" or img.width > 4000 or img.height > 4000:
                raise ValueError
            img = img.convert("RGBA")
    except (binascii.Error, UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise StorageError(_("Die Unterschrift konnte nicht gelesen werden.")) from None
    # Strich = alles, was nicht durchsichtig und nicht weiß ist
    ink = Image.new("L", img.size, 0)
    ink.paste(255, mask=img.getchannel("A").point(lambda a: 255 if a > 40 else 0))
    gray = img.convert("L").point(lambda v: 255 if v < 200 else 0)
    ink = Image.composite(ink, Image.new("L", img.size, 0), gray)
    box = ink.getbbox()
    if box is None or ((box[2] - box[0]) < 15 and (box[3] - box[1]) < 15):     # nur ein Punkt
        raise StorageError(_("Bitte im Feld unterschreiben."))
    margin = 8
    box = (max(box[0] - margin, 0), max(box[1] - margin, 0), min(box[2] + margin, img.width),
           min(box[3] + margin, img.height))
    out = Image.new("L", img.size, 255)
    out.paste(0, mask=ink)
    out = out.crop(box)
    if out.width > SIGN_WIDTH:
        out = out.resize((SIGN_WIDTH, max(1, round(out.height * SIGN_WIDTH / out.width))), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def sign(delivery, who, name, data_url):
    """Unterschrift speichern. Geht auch nach dem Abschließen, solange das Feld noch leer ist."""
    col = _column(who)
    if not delivery.items:
        raise StorageError(_("Der Lieferschein hat noch keine Positionen."))
    if getattr(delivery, col):
        raise StorageError(_("Hier wurde schon unterschrieben."))
    name = (name or "").strip()[:100]
    if not name:
        raise StorageError(_("Bitte den Namen in Druckschrift eintragen."))
    setattr(delivery, col, _signature_png(data_url))
    setattr(delivery, f"{col}_name", name)
    setattr(delivery, f"{col}_at", utcnow())


def remove_signature(delivery, who):
    """Nur bei offenem Lieferschein (sonst erst wieder öffnen)."""
    col = _column(who)
    if not delivery.is_open:
        raise StorageError(_("Lieferschein {number} ist abgeschlossen.", number=delivery.number))
    for suffix in ("", "_name", "_at"):
        setattr(delivery, col + suffix, None)


def delete_delivery(delivery):
    if delivery.items:
        raise StorageError(_("Bitte zuerst alle Positionen entfernen."))
    db.session.delete(delivery)
    db.session.flush()


def find_open_item(box):
    """Position auf einem offenen Lieferschein, falls die Kiste gerade verladen wird."""
    return (DeliveryItem.query.join(Delivery)
            .filter(DeliveryItem.box_id == box.id, Delivery.status == DELIVERY_OPEN).first())


# --- Zusammenfassung für Anzeige und PDF -------------------------------------

def code_ranges(codes):
    """['K-0001', 'K-0002', 'K-0003', 'K-0007'] → 'K-0001 – K-0003, K-0007'"""
    numbers = sorted({int(m.group(1)) for c in codes if (m := storage.CODE_RE.match(c))})
    parts, start = [], None
    for i, n in enumerate(numbers):
        if start is None:
            start = n
        if i + 1 == len(numbers) or numbers[i + 1] != n + 1:
            fmt = storage.format_code
            parts.append(fmt(start) if start == n else f"{fmt(start)} – {fmt(n)}")
            start = None
    others = sorted(c for c in codes if not storage.CODE_RE.match(c))
    return ", ".join(parts + others)


def certification_for(variety_id, seed_class_id, year):
    if not (variety_id and seed_class_id and year):
        return None
    return Certification.query.filter_by(variety_id=variety_id, seed_class_id=seed_class_id, year=year).first()


def summarize(delivery):
    """Fasst Positionen zu Zeilen zusammen: Kisten je Sorte/Verwendung/Stufe/Sortierung/Erntejahr,
    abgepackte Ware je Gebinde, Schüttgut je Ware. Jede Zeile: dict mit is_box, is_bulk, crop, variety, usage_label,
    seed_class, grade, year,
    boxes, box_units (Kisten-Äquivalent aus Füllständen), codes, packaging, quantity, weight_kg, certification."""
    lines = OrderedDict()
    for item in delivery.items:
        year = item.harvest_date.year if item.harvest_date else None
        if item.bulk_entry_id or item.crop:
            key = ("bulk", item.crop or "", item.packaging or "")
        elif item.box_id:
            key = ("box", item.variety_id, item.usage, item.seed_class_id, item.grade or "", year)
        else:
            key = ("qty", item.variety_id, item.usage, item.seed_class_id, item.grade or "", item.packaging or "",
                   item.weight_kg // item.quantity if item.quantity else 0)
        line = lines.get(key)
        if line is None:
            line = lines[key] = {
                "is_box": bool(item.box_id), "is_bulk": key[0] == "bulk", "crop": item.crop,
                "variety": item.variety, "usage": item.usage,
                "usage_label": item.usage_label, "seed_class": item.seed_class, "grade": item.grade, "year": year,
                "boxes": 0, "box_units": 0, "codes": [], "packaging": item.packaging, "quantity": 0,
                "weight_kg": 0,
            }
        if item.box_id:
            line["boxes"] += 1
            line["box_units"] += (item.fill or 0) / 100
            line["codes"].append(item.box.code)
        else:
            line["quantity"] += item.quantity or 0
        line["weight_kg"] += item.weight_kg or 0
    result = list(lines.values())
    for line in result:
        line["code_ranges"] = code_ranges(line["codes"])
        line["certification"] = certification_for(
            line["variety"].id if line["variety"] else None,
            line["seed_class"].id if line["seed_class"] else None, line["year"])
    return result


def open_deliveries():
    return Delivery.query.filter_by(status=DELIVERY_OPEN).order_by(Delivery.date, Delivery.id).all()


def delivery_years():
    from sqlalchemy import extract
    return sorted({int(y) for (y,) in db.session.query(extract("year", Delivery.date)).distinct() if y},
                  reverse=True)


def count_by_customer():
    return dict(db.session.query(Delivery.customer_id, func.count(Delivery.id)).group_by(Delivery.customer_id))
