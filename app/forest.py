"""Bestattungswald: Baumauswahl, Bäume mit Beisetzungsplätzen, Verträge (Reservierungen), Beisetzungen, Baumkontrolle.

Alles beginnt mit dem Baumauswahl-Termin (`ForestSelection`): wer kommt, wann, wer vom Hof führt (Aufgabe für ihn).
Nach dem Termin werden Baum und Plätze eingetragen; daraus entstehen Vertrag und später die Beisetzung.

Ein Baum hat meist 12 Plätze. Stand je Platz: belegt (Beisetzung eingetragen), reserviert (Vertrag), sonst frei.
Eine Beisetzung legt zwei Aufgaben an: „Grab ausheben“ am Werktag davor und „Grab schließen“ am Tag selbst – sie
landen in „Offen“ und im Tagesplan. Die Baumkontrolle ist ein Rundgang mit Stationen der Rubrik `wald`.
Der angezeigte Name kommt aus `settings` `forest_name` (sonst „Bestattungswald“). Committet nicht.
"""
import csv
import io
import re
from datetime import date, time as dtime, timedelta
from decimal import Decimal, InvalidOperation

from flask import url_for
from sqlalchemy import func

from . import push
from .extensions import db
from .i18n import gettext as _, ngettext
from .machines import add_months
from .models import (FOREST_KINDS, ForestBurial, ForestContract, ForestPlace, ForestSelection, ForestTree, Setting,
                     Task, User)
from .push import Msg
from .storage import StorageError

DEFAULT_PLACES = 12
TERM_YEARS = 99                 # übliche Nutzungszeit, im Vertrag änderbar
STORM_GUST = 70                 # km/h Böen: danach Baumkontrolle anmahnen
CODE_RE = re.compile(r"^W-?([A-Z]{0,3})-?(\d{1,5})$")
KEY_RE = re.compile(r"^([A-Z]{0,3})[\s-]*(\d{1,5})$")
MAX_PLACES = 999                # Waldruheflächen haben mehrere hundert Plätze
AREA = "wald"                   # Aufgaben und Rundgang-Stationen der Rubrik


def name():
    return (Setting.all().get("forest_name") or "").strip() or _("Bestattungswald")


def normalize_code(raw):
    """„W-A147“, „w-047“ oder eine URL, die darauf endet → „W-A147“ / „W-047“ (None, wenn es kein Baum-Code ist)."""
    value = (raw or "").strip().upper().rstrip("/").rsplit("/", 1)[-1]
    m = CODE_RE.match(value)
    return f"W-{m.group(1)}{int(m.group(2)):03d}" if m else None


def split_key(raw):
    """„A 147“, „a147“, „A-147“, „147“ → ("A", 147); sonst None."""
    m = KEY_RE.match((raw or "").strip().upper())
    return (m.group(1), int(m.group(2))) if m else None


def find_tree(raw):
    """Baum zu einer Eingabe: Code „W-A147“, „A 147“ oder nur „147“ (dann nur, wenn die Nummer eindeutig ist)."""
    code = normalize_code(raw)
    key = split_key(code[2:]) if code else split_key(raw)
    if key is None:
        return None
    field, number = key
    tree = ForestTree.query.filter_by(field=field, number=number).first()
    if tree is None and not field:
        hits = ForestTree.query.filter_by(number=number).limit(2).all()
        tree = hits[0] if len(hits) == 1 else None
    return tree


tree_by_code = find_tree


def members():
    return [u for u in User.query.filter_by(active=True) if u.sees_forest]


# --- Bäume ----------------------------------------------------------------------------------------

def _set_places(tree, count):
    count = int(count or 0)
    if not 1 <= count <= MAX_PLACES:
        raise StorageError(_("Ein Baum hat 1 bis {n} Plätze.", n=MAX_PLACES))
    have = {p.number for p in tree.places}
    for n in range(1, count + 1):
        if n not in have:
            tree.places.append(ForestPlace(number=n))
    for place in [p for p in tree.places if p.number > count]:
        if place.state != "frei":
            raise StorageError(_("Platz {n} ist vergeben – so wenige Plätze gehen nicht.", n=place.number))
        tree.places.remove(place)


def _field(raw):
    return (raw or "").strip().upper()[:10]


def create_trees(first, last, places=DEFAULT_PLACES, species=None, section=None, kind="gemeinschaft", field=None):
    """Bäume mit den Nummern first–last (im Feld `field`) anlegen, vorhandene bleiben. Liefert die Zahl der neuen."""
    if first is None or last is None or not 1 <= first <= last or last - first >= 2000:
        raise StorageError(_("Bitte die Nummern von–bis angeben (höchstens 2000 auf einmal)."))
    field = _field(field)
    existing = {n for (n,) in db.session.query(ForestTree.number).filter(ForestTree.field == field,
                                                                           ForestTree.number.between(first, last))}
    added = 0
    for number in range(first, last + 1):
        if number in existing:
            continue
        tree = ForestTree(field=field, number=number, species=(species or "").strip()[:60] or None,
                          section=(section or "").strip()[:60] or None, kind=kind if kind in FOREST_KINDS else "gemeinschaft")
        _set_places(tree, places)
        db.session.add(tree)
        added += 1
    db.session.flush()
    return added


def _coord(raw, low, high):
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise StorageError(_("Bitte die Lage als Breite und Länge in Grad angeben (z. B. 52,41 und 10,52)."))
    if not low <= value <= high:
        raise StorageError(_("Bitte die Lage als Breite und Länge in Grad angeben (z. B. 52,41 und 10,52)."))
    return value


def save_tree(tree, number, species, section, kind, places, lat, lon, note, active=True, field=None, category=None,
              trunk_cm=None):
    field = _field(field) if field is not None else (tree.field if tree else "")
    if number is None or number < (0 if kind == "flaeche" else 1):
        raise StorageError(_("Bitte eine Baumnummer angeben."))
    other = ForestTree.query.filter(ForestTree.field == field, ForestTree.number == number,
                                    ForestTree.id != (tree.id if tree else None)).first()
    if other:
        raise StorageError(_("Baum {n} gibt es schon.", n=other.name))
    if tree is None:
        tree = ForestTree(field=field, number=number)
        db.session.add(tree)
    tree.field, tree.number = field, number
    tree.category = (category or "").strip()[:10] or None if category is not None else tree.category
    if trunk_cm is not None:
        tree.trunk_cm = trunk_cm or None
    tree.species = (species or "").strip()[:60] or None
    tree.section = (section or "").strip()[:60] or None
    tree.kind = kind if kind in FOREST_KINDS else "gemeinschaft"
    tree.lat, tree.lon = _coord(lat, -90, 90), _coord(lon, -180, 180)
    if (tree.lat is None) != (tree.lon is None):
        raise StorageError(_("Bitte Breite und Länge zusammen angeben."))
    tree.note = (note or "").strip() or None
    tree.active = bool(active)
    _set_places(tree, places)
    db.session.flush()
    return tree


def _kind_from_text(text):
    """„Familienbaum“, „Familien/Freundesbaum“, „Einzelbaum“, „Sternchenbaum“, „Waldruhefläche“ … → Schlüssel aus
    FOREST_KINDS (sonst Gemeinschaftsbaum)."""
    text = (text or "").strip().lower()
    for prefix, kind in (("famil", "familie"), ("einzel", "einzel"), ("stern", "sternchen"), ("waldruh", "flaeche")):
        if text.startswith(prefix):
            return kind
    return "gemeinschaft"


def parse_trees_csv(data):
    """CSV mit Spalten nummer (auch „A 147“), feld, baumart, abschnitt, art, plaetze, breite, laenge (nur nummer ist
    Pflicht)."""
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    try:
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    fields = {(f or "").strip().lower().replace("ä", "ae"): f for f in reader.fieldnames or []}
    if "nummer" not in fields:
        raise StorageError(_("In der Datei fehlt die Spalte „nummer“."))
    rows = []
    for i, raw in enumerate(reader, start=2):
        get = lambda key: (raw.get(fields.get(key, ""), "") or "").strip()     # noqa: E731
        if not get("nummer"):
            continue
        key = split_key(get("feld") + " " + get("nummer") if get("feld") else get("nummer"))
        if key is None:
            raise StorageError(_("Zeile {line}: „{value}“ ist keine Baumnummer.", line=i, value=get("nummer")[:20]))
        rows.append({"field": key[0], "number": key[1], "species": get("baumart"), "section": get("abschnitt"),
                     "kind": _kind_from_text(get("art")),
                     "places": int(get("plaetze") or DEFAULT_PLACES) if get("plaetze").isdigit() or not get("plaetze")
                     else DEFAULT_PLACES, "lat": get("breite"), "lon": get("laenge")})
    if not rows:
        raise StorageError(_("Die Datei enthält keine Bäume."))
    return rows


def apply_trees(rows):
    """Bäume aus `parse_trees_csv` anlegen bzw. aktualisieren (nach Nummer). Liefert (neu, geändert)."""
    added = changed = 0
    for r in rows:
        tree = ForestTree.query.filter_by(field=r.get("field", ""), number=r["number"]).first()
        if tree is None:
            added += 1
        else:
            changed += 1
        save_tree(tree, r["number"], r["species"], r["section"], r["kind"], r["places"], r["lat"], r["lon"],
                  tree.note if tree else None, tree.active if tree else True, field=r.get("field", ""))
    return added, changed


def search_trees(query, q):
    """Freitext-Suche über die Bäume: „27“ findet A 27, B 27 …; „A 27“ / „W-A027“ genau den einen; sonst Baumart,
    Abschnitt oder Notiz."""
    q = (q or "").strip()
    if not q:
        return query
    code = normalize_code(q)
    key = split_key(code[2:]) if code else split_key(q)
    if key and key[0]:
        return query.filter(ForestTree.field == key[0], ForestTree.number == key[1])
    if key:
        return query.filter(ForestTree.number == key[1])
    like = f"%{q}%"
    return query.filter(ForestTree.species.ilike(like) | ForestTree.section.ilike(like) | ForestTree.note.ilike(like))


def species_list():
    """Baumarten der aktiven Bäume (für den Filter)."""
    return [s for (s,) in db.session.query(ForestTree.species).filter(ForestTree.active.is_(True),
                                                                        ForestTree.species.isnot(None))
            .distinct().order_by(ForestTree.species)]


def kinds_in_use():
    used = {k for (k,) in db.session.query(ForestTree.kind).filter(ForestTree.active.is_(True)).distinct()}
    return [k for k in FOREST_KINDS if k in used]


def tree_rows(section=None, only_free=False, species=None, kinds=None, q=None):
    """Bäume mit Zählern je Stand: [{tree, frei, reserviert, belegt, total}]. Filter: Abschnitt, nur mit freien
    Plätzen, Baumarten (Liste), Arten (Liste: gemeinschaft, einzel …), Suche `q` (`search_trees`)."""
    query = ForestTree.query.filter_by(active=True)
    if section:
        query = query.filter(ForestTree.section == section)
    if species:
        query = query.filter(ForestTree.species.in_(species))
    if kinds:
        query = query.filter(ForestTree.kind.in_(kinds))
    query = search_trees(query, q)
    from sqlalchemy.orm import selectinload
    rows = []
    for tree in query.options(selectinload(ForestTree.places).selectinload(ForestPlace.burials)).order_by(ForestTree.field, ForestTree.number):
        states = [p.state for p in tree.places]
        row = {"tree": tree, "frei": states.count("frei"), "reserviert": states.count("reserviert"),
               "belegt": states.count("belegt"), "total": len(states)}
        if only_free and not row["frei"]:
            continue
        rows.append(row)
    return rows


def sections():
    return [s for (s,) in db.session.query(ForestTree.section).filter(ForestTree.section.isnot(None))
            .distinct().order_by(ForestTree.section)]


def overview(today=None):
    """Zahlen für die Übersicht: Bäume, Plätze je Stand, je Abschnitt, nächste Beisetzungen."""
    today = today or date.today()
    total = db.session.query(func.count(ForestPlace.id)).join(ForestTree).filter(ForestTree.active.is_(True)).scalar()
    used_ids = db.session.query(ForestBurial.place_id).distinct()
    occupied = db.session.query(func.count(ForestPlace.id)).filter(ForestPlace.id.in_(used_ids)).scalar()
    reserved = db.session.query(func.count(ForestPlace.id)).filter(
        ForestPlace.contract_id.isnot(None), ForestPlace.id.notin_(used_ids)).scalar()
    by_section = {}
    for row in tree_rows():
        s = by_section.setdefault(row["tree"].section or "", {"trees": 0, "frei": 0, "reserviert": 0, "belegt": 0})
        s["trees"] += 1
        for key in ("frei", "reserviert", "belegt"):
            s[key] += row[key]
    return {
        "trees": ForestTree.query.filter_by(active=True).count(), "places": total or 0, "belegt": occupied or 0,
        "reserviert": reserved or 0, "frei": (total or 0) - (occupied or 0) - (reserved or 0),
        "sections": sorted(by_section.items()), "upcoming": upcoming(today, 30),
        "this_year": ForestBurial.query.filter(func.extract("year", ForestBurial.day) == today.year).count(),
    }


def map_data(species=None, kinds=None, only_free=False, tree_id=None):
    """Bäume mit Lage für die Karte (Schlagkarte.js): Farbe nach freien Plätzen. Filter wie `tree_rows`,
    `tree_id` = nur dieser eine Baum (Knopf „Lage öffnen“ auf der Baumseite)."""
    items = []
    for row in tree_rows(only_free=only_free, species=species, kinds=kinds):
        tree = row["tree"]
        if tree_id is not None and tree.id != tree_id:
            continue
        outline = tree.outline_points
        if (tree.lat is None or tree.lon is None) and not outline:
            continue
        color = "#3f8f4f" if row["frei"] == row["total"] else "#e0a800" if row["frei"] else \
            "#7a5ea8" if row["reserviert"] else "#8a8a8a"
        items.append({"id": tree.id, "name": tree.label, "number": tree.name, "color": color,
                      "rings": [outline] if outline else [], "point": None if outline else [tree.lon, tree.lat], "ha": None,
                      "crop": _("{free} von {total} frei", free=row["frei"], total=row["total"])})
    return items


WHEEL_MAX = 48                  # mehr Plätze (Waldruhefläche) → nur die Kacheln, kein Kreis


WHEEL_RING = 12                 # Plätze je Ring (wie die Stunden auf dem Zifferblatt)
WHEEL_FIRST_M = 2.0             # innerer Ring beginnt 2 m vom Baum (Stammmitte) …
WHEEL_WIDTH_M = 0.5             # … und ist 0,5 m breit; der nächste schließt direkt an (2,5–3 m)
WHEEL_UNITS_PER_M = 34          # Zeichenmaßstab im SVG


def wheel_position(number, count):
    """(Ring, Winkel der Mitte in Grad ab Norden im Uhrzeigersinn) eines Platzes.
    Innerer Ring wie eine analoge Uhr: Platz 12 oben im Norden, Platz 1 bei 1 Uhr, 3 im Osten, 6 im Süden, 9 im
    Westen. Zweiter Ring (13–24) weiter außen, um eine halbe Stunde (15°) versetzt: Platz 13 über der Lücke zwischen
    12 und 1, Platz 14 zwischen 1 und 2 … Bäume mit weniger als 12 Plätzen: ein Ring ab Norden, gleichmäßig verteilt."""
    step = 360.0 / WHEEL_RING
    if count < WHEEL_RING:
        return 0, (number - 1) * 360.0 / count
    ring, index = divmod(number - 1, WHEEL_RING)
    if ring % 2 == 0:                                      # Stunden-Ring: Platz 12 (24 …) oben
        return ring, ((index + 1) % WHEEL_RING) * step
    return ring, index * step + step / 2


def place_wheel(tree, marks=None):
    """Plätze in Ringen um den Baum (`wheel_position`), maßstäblich: innerer Ring 2–2,5 m vom Baum, äußerer 2,5–3 m,
    Stamm aus `trunk_cm` (sonst 0,6 m Durchmesser). Liefert {"size": halbe Breite fürs viewBox, "trunk": Radius des
    Stamms, "segments": [{number, ring, state, d (SVG-Pfad), x, y}], "rings"} oder None."""
    import math
    places = sorted(tree.places, key=lambda p: p.number)
    count = max((p.number for p in places), default=0)
    if tree.kind == "flaeche" or not 1 <= count <= WHEEL_MAX:
        return None
    marks = marks or {}
    unit = WHEEL_UNITS_PER_M
    trunk = min(max((tree.trunk_cm or 60) / 200.0, 0.1), 1.5) * unit
    first, width, gap = WHEEL_FIRST_M * unit, WHEEL_WIDTH_M * unit, 0.0
    per_ring = min(count, WHEEL_RING)
    step = 360.0 / per_ring

    def pt(r, deg):
        rad = math.radians(deg)
        return r * math.sin(rad), -r * math.cos(rad)

    segments, outer_max = [], first
    for p in places:
        ring, center = wheel_position(p.number, count)
        inner = first + ring * (width + gap)
        outer = inner + width
        outer_max = max(outer_max, outer)
        if per_ring == 1:                                  # ein Platz je Ring: ganzer Kreisring
            d = (f"M0 {-outer:.2f}A{outer:.2f} {outer:.2f} 0 1 1 0 {outer:.2f}A{outer:.2f} {outer:.2f} 0 1 1 0 {-outer:.2f}Z"
                 f"M0 {-inner:.2f}A{inner:.2f} {inner:.2f} 0 1 0 0 {inner:.2f}A{inner:.2f} {inner:.2f} 0 1 0 0 {-inner:.2f}Z")
            lx, ly = 0.0, -(inner + outer) / 2
        else:
            a0, a1 = center - step / 2, center + step / 2
            large = 1 if step > 180 else 0
            (x0, y0), (x1, y1) = pt(outer, a0), pt(outer, a1)
            (x2, y2), (x3, y3) = pt(inner, a1), pt(inner, a0)
            d = (f"M{x0:.2f} {y0:.2f}A{outer:.2f} {outer:.2f} 0 {large} 1 {x1:.2f} {y1:.2f}"
                 f"L{x2:.2f} {y2:.2f}A{inner:.2f} {inner:.2f} 0 {large} 0 {x3:.2f} {y3:.2f}Z")
            lx, ly = pt((outer + inner) / 2, center)
        state = p.state
        if state == "frei" and p.number in marks:
            state = "vorgemerkt"
        segments.append({"number": p.number, "ring": ring, "state": state, "d": d, "x": round(lx, 2), "y": round(ly, 2)})
    return {"size": round(outer_max + 14), "trunk": round(trunk, 2), "segments": segments,
            "rings": max(s["ring"] for s in segments) + 1}


def map_background():
    """Grundkarte (Wege, Flächen …) für Schlagkarte.js: (Formen, Legende [(Bezeichnung, Farbe)]). Große Flächen
    zuerst, damit Wege und Plätze obenauf liegen."""
    import json
    from .models import ForestShape
    shapes = ForestShape.query.all()
    priority = {"rasen": 0, "wasser": 1, "sonstiges": 2, "parkplatz": 3, "kies": 4, "weg": 5, "aussicht": 6, "andacht": 7}
    shapes.sort(key=lambda s: priority.get(s.kind, 2))
    items = [{"name": f"{s.kind_label}" + (f" · {s.layer}" if s.layer and s.layer.lower() != s.kind_label.lower() else ""),
              "color": s.color, "rings": [json.loads(s.points)], "line": s.line} for s in shapes]
    legend = []
    for s in shapes:
        if (s.kind_label, s.color) not in legend:
            legend.append((s.kind_label, s.color))
    return items, legend


# --- Verträge ------------------------------------------------------------------------------------

def next_contract_number(day=None):
    year = (day or date.today()).year
    prefix = f"V-{year}-"
    numbers = [int(n[len(prefix):]) for (n,) in db.session.query(ForestContract.number)
               .filter(ForestContract.number.like(prefix + "%")) if n[len(prefix):].isdigit()]
    return f"{prefix}{max(numbers, default=0) + 1:03d}"


def _money(raw):
    raw = (raw or "").strip().replace("€", "").replace(" ", "")
    if not raw:
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise StorageError(_("Bitte den Preis als Zahl angeben."))
    if value < 0:
        raise StorageError(_("Bitte den Preis als Zahl angeben."))
    return value.quantize(Decimal("0.01"))


def save_contract(contract, user, holder, signed_on, places, years=TERM_YEARS, address=None, phone=None, email=None,
                  price=None, note=None, selection=None, ends_on=None, holder_birthday=None):
    """Vertrag anlegen bzw. ändern. `places`: ForestPlace-Objekte (frei oder schon zu diesem Vertrag).
    `selection`: Baumauswahl, aus der der Vertrag entsteht (wird mit ihm verknüpft)."""
    holder = (holder or "").strip()[:150]
    if not holder:
        raise StorageError(_("Bitte angeben, wer den Vertrag hat."))
    if signed_on is None:
        raise StorageError(_("Bitte das Datum des Vertrags angeben."))
    if not places:
        raise StorageError(_("Bitte mindestens einen Platz wählen."))
    if not years or not 1 <= years <= 150:
        raise StorageError(_("Bitte die Laufzeit in Jahren angeben."))
    for place in places:
        if place.contract_id and (contract is None or place.contract_id != contract.id):
            raise StorageError(_("{place} ist schon vergeben.", place=place.label))
    if contract is None:
        contract = ForestContract(number=next_contract_number(signed_on), created_by_id=user.id if user else None)
        db.session.add(contract)
    contract.holder = holder
    contract.address = (address or "").strip() or None
    contract.phone = (phone or "").strip()[:60] or None
    contract.email = (email or "").strip()[:120] or None
    contract.signed_on = signed_on
    if ends_on is not None and ends_on <= signed_on:
        raise StorageError(_("Das Nutzungsende muss nach dem Vertragsdatum liegen."))
    contract.ends_on = ends_on or add_months(signed_on, 12 * years)
    contract.holder_birthday = holder_birthday
    contract.price = _money(price) if isinstance(price, str) else price
    contract.note = (note or "").strip() or None
    keep = {p.id for p in places}
    for place in list(contract.places):
        if place.id not in keep:
            place.contract = None
    for place in places:
        place.contract = contract
    db.session.flush()
    for burial in ForestBurial.query.filter(ForestBurial.place_id.in_(keep), ForestBurial.contract_id.is_(None)):
        burial.contract = contract
    if selection is not None:
        selection.contract = contract
    return contract


def default_end(signed_on, places):
    """Vorgabe fürs Nutzungsende: Einzelbaum = `forest_single_years` Jahre, sonst `forest_end` (fester Tag), sonst
    TERM_YEARS ab Vertragsdatum."""
    settings = Setting.all()
    years = (settings.get("forest_single_years") or "").strip()
    if years.isdigit() and places and all(p.tree.kind == "einzel" for p in places):
        return add_months(signed_on, 12 * int(years))
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", (settings.get("forest_end") or "").strip())
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return add_months(signed_on, 12 * TERM_YEARS)


def delete_contract(contract):
    if contract.burials:
        raise StorageError(_("Zu diesem Vertrag gibt es schon Beisetzungen – er kann nicht gelöscht werden."))
    for place in list(contract.places):
        place.contract = None
    for selection in ForestSelection.query.filter_by(contract_id=contract.id):
        selection.contract = None
    db.session.delete(contract)


def contracts(search=None):
    query = ForestContract.query
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(ForestContract.holder.ilike(like) | ForestContract.number.ilike(like))
    return query.order_by(ForestContract.signed_on.desc(), ForestContract.id.desc()).all()


def ending_contracts(today=None, years=1):
    """Verträge, deren Nutzungszeit innerhalb von `years` Jahren endet (oder schon vorbei ist)."""
    today = today or date.today()
    return (ForestContract.query.filter(ForestContract.ends_on.isnot(None),
                                        ForestContract.ends_on <= add_months(today, 12 * years))
            .order_by(ForestContract.ends_on).all())


# --- Baumauswahl ----------------------------------------------------------------------------------

def _selection_task(selection, user):
    """Aufgabe „Baumauswahl: {Name}“ für den, der führt (sonst für alle) – anlegen bzw. nachziehen."""
    from . import tasks
    title = _("Baumauswahl: {name}", name=selection.contact)
    info = " · ".join(p for p in (
        selection.day.strftime("%d.%m.%Y") + (f" {selection.time}" if selection.time else ""),
        selection.phone, _("für {name}", name=selection.deceased) if selection.deceased else None,
        ngettext("{n} Platz", "{n} Plätze", selection.places_wanted),
        _(FOREST_KINDS[selection.kind_wanted]) if selection.kind_wanted in FOREST_KINDS else None) if p)
    task = selection.task
    if task is None:
        task = tasks.create_task(user, title=title, description=info, due_on=selection.day,
                                 assignees=[selection.guide] if selection.guide else [])
        task.area = AREA
        selection.task = task
    elif not task.is_done:
        task.title, task.description, task.due_on = title, info, selection.day
        task.assignees = [selection.guide] if selection.guide else []


def save_selection(selection, user, contact, day, time=None, phone=None, email=None, deceased=None, places_wanted=1,
                   kind_wanted=None, guide=None, note=None):
    """Baumauswahl-Termin anlegen bzw. ändern (legt die Aufgabe für den an, der führt)."""
    contact = (contact or "").strip()[:150]
    if not contact:
        raise StorageError(_("Bitte angeben, wer zur Baumauswahl kommt."))
    if day is None:
        raise StorageError(_("Bitte das Datum des Termins angeben."))
    if not places_wanted or not 1 <= places_wanted <= 99:
        raise StorageError(_("Bitte angeben, wie viele Plätze gesucht werden."))
    with db.session.no_autoflush:
        if selection is None:
            selection = ForestSelection(created_by_id=user.id if user else None, status="geplant")
            db.session.add(selection)
        selection.contact = contact
        selection.day = day
        selection.time = _time(time)
        selection.phone = (phone or "").strip()[:60] or None
        selection.email = (email or "").strip()[:120] or None
        selection.deceased = (deceased or "").strip()[:150] or None
        selection.places_wanted = places_wanted
        selection.kind_wanted = kind_wanted if kind_wanted in FOREST_KINDS else None
        selection.guide = guide
        selection.note = (note or "").strip() or None
    db.session.flush()
    if selection.status == "geplant":
        _selection_task(selection, user)
        db.session.flush()
    return selection


def _taken_by_selection(place, selection):
    """Eine andere, noch nicht abgeschlossene Auswahl hat diesen Platz schon ausgesucht?"""
    others = ForestSelection.query.filter(ForestSelection.tree_id == place.tree_id, ForestSelection.status == "ausgesucht",
                                          ForestSelection.id != selection.id)
    for other in others:
        if place.number in other.numbers and not any(b.place_id == place.id for b in other.burials) \
                and not other.contract_id:
            return other
    return None


def free_for(selection, tree):
    """Plätze am Baum, die diese Auswahl nehmen kann (frei und von keiner anderen Auswahl vorgemerkt)."""
    return [p for p in tree.places
            if (p.state == "frei" or (selection.contract_id and p.contract_id == selection.contract_id))
            and not _taken_by_selection(p, selection)]


def choose_tree(selection, user, tree, numbers=None):
    """Ergebnis des Termins: Baum und Plätze. Ohne Platznummern die ersten freien in gewünschter Anzahl.
    Hakt die Aufgabe „Baumauswahl“ ab."""
    from . import tasks
    if tree is None:
        raise StorageError(_("Keinen Baum mit dieser Nummer gefunden."))
    if selection.status == "abgesagt":
        raise StorageError(_("Der Termin ist abgesagt."))
    free = {p.number: p for p in free_for(selection, tree)}
    numbers = sorted(set(numbers or []))
    if not numbers:
        numbers = sorted(free)[:selection.places_wanted]
        if len(numbers) < selection.places_wanted:
            raise StorageError(ngettext("Am Baum {tree} ist nur noch {n} Platz frei.",
                                        "Am Baum {tree} sind nur noch {n} Plätze frei.", len(free), tree=tree.name))
    for n in numbers:
        if not any(p.number == n for p in tree.places):
            raise StorageError(_("Baum {n} hat keinen Platz {p}.", n=tree.name, p=n))
        if n not in free:
            raise StorageError(_("Baum {n}, Platz {p} ist nicht mehr frei.", n=tree.name, p=n))
    selection.tree = tree
    selection.place_numbers = ",".join(str(n) for n in numbers)
    selection.status = "ausgesucht"
    if selection.task is not None and not selection.task.is_done:
        tasks.complete_task(selection.task, user)
    db.session.flush()
    return selection


def cancel_selection(selection):
    """Termin absagen: offene Aufgabe fällt weg, ausgesuchte Plätze werden wieder frei."""
    if selection.burials:
        raise StorageError(_("Zu dieser Baumauswahl gibt es schon eine Beisetzung."))
    if selection.task is not None and not selection.task.is_done:
        db.session.delete(selection.task)
        selection.task = None
    selection.status = "abgesagt"
    db.session.flush()


def reopen_selection(selection, user):
    """Abgesagt bzw. ausgesucht → wieder geplant (z. B. Termin verschoben, anderer Baum)."""
    selection.status = "geplant"
    selection.tree = None
    selection.place_numbers = None
    if selection.task is not None and selection.task.is_done:
        from . import tasks
        tasks.reopen_task(selection.task)
    _selection_task(selection, user)
    db.session.flush()


def delete_selection(selection):
    if selection.burials:
        raise StorageError(_("Zu dieser Baumauswahl gibt es schon eine Beisetzung."))
    if selection.task is not None and not selection.task.is_done:
        db.session.delete(selection.task)
    db.session.delete(selection)


def selections(today=None):
    """{"planned": kommende/offene Termine, "chosen": ausgesucht, aber noch ohne Beisetzung, "done": übrige}"""
    today = today or date.today()
    from sqlalchemy.orm import selectinload
    rows = (ForestSelection.query.options(selectinload(ForestSelection.burials), selectinload(ForestSelection.guide))
            .order_by(ForestSelection.day.desc(), ForestSelection.time.desc()).all())
    planned = sorted([s for s in rows if s.status == "geplant"], key=lambda s: (s.day, s.time or ""))
    chosen = [s for s in rows if s.status == "ausgesucht" and len(s.burials) < len(s.numbers)]
    done = [s for s in rows if s not in planned and s not in chosen]
    return {"planned": planned, "chosen": chosen, "done": done}


def upcoming_selections(today=None, days=30):
    today = today or date.today()
    return (ForestSelection.query.filter(ForestSelection.status == "geplant", ForestSelection.day <= today + timedelta(days=days))
            .order_by(ForestSelection.day, ForestSelection.time).all())


def next_place(selection):
    """Platz für die nächste Beisetzung aus dieser Auswahl (der erste ohne Beisetzung)."""
    return next((p for p in selection.places if not p.burials), None)


# --- Beisetzungen --------------------------------------------------------------------------------

def _workday_before(day):
    day -= timedelta(days=1)
    while day.weekday() >= 5:            # Samstag/Sonntag → Freitag davor
        day -= timedelta(days=1)
    return day


def _time(raw):
    raw = (raw or "").strip().replace(".", ":")
    if not raw:
        return None
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", raw)
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
        raise StorageError(_("Bitte die Uhrzeit wie 11:00 angeben."))
    return f"{int(m.group(1)):02d}:{int(m.group(2) or 0):02d}"


GRAVE_KEYS = ("forest_grave_person", "forest_grave_deputy")


def grave_team():
    """Wer standardmäßig die Grabstelle vorbereitet: (Person, Vertretung) – je ein aktiver Mitarbeiter oder None."""
    team = []
    for key in GRAVE_KEYS:
        setting = db.session.get(Setting, key)
        user = db.session.get(User, int(setting.value)) if setting and (setting.value or "").isdigit() else None
        team.append(user if user is not None and user.active and user.can_edit and user.sees_forest else None)
    return tuple(team)


def save_grave_team(person, deputy):
    if person is not None and deputy is not None and person.id == deputy.id:
        raise StorageError(_("Person und Vertretung müssen verschiedene Mitarbeiter sein."))
    for key, user in zip(GRAVE_KEYS, (person, deputy)):
        Setting.put(key, str(user.id) if user else None)


def _grave_assignees(person, deputy, day):
    """Wer am `day` das Grab vorbereitet: die Person – ist sie laut Kalender abwesend, die Vertretung;
    sind beide abwesend, beide (damit die Aufgabe nicht verloren geht)."""
    from .agenda import absent
    if person is not None and not absent(person, day):
        return [person]
    if deputy is not None and (person is None or deputy.id != person.id) and not absent(deputy, day):
        return [deputy]
    return [u for i, u in enumerate((person, deputy)) if u is not None and (i == 0 or person is None or u.id != person.id)]


def _burial_tasks(burial, user):
    """Aufgaben „Grab ausheben“ (Werktag davor) und „Grab schließen“ (am Tag) anlegen bzw. nachziehen.
    Zuständig je Aufgabe nach `_grave_assignees` (Person, bei Abwesenheit die Vertretung)."""
    from . import tasks
    where = burial.place.label
    when = burial.day.strftime("%d.%m.%Y") + (f" {burial.time}" if burial.time else "")
    info = _("Beisetzung {when}", when=when) + (f" · {burial.undertaker}" if burial.undertaker else "") + \
        (" · " + _("mit Kirchenglocken") if burial.bells else "")
    for attr, title, due in (("dig_task", _("Grab ausheben: {place}", place=where), _workday_before(burial.day)),
                             ("close_task", _("Grab schließen: {place}", place=where), burial.day)):
        task = getattr(burial, attr)
        assignees = _grave_assignees(burial.grave_person, burial.grave_deputy, due)
        if task is None:
            task = tasks.create_task(user, title=title, description=info, due_on=due, important=True,
                                     assignees=assignees)
            task.area = AREA
            setattr(burial, attr, task)
        elif not task.is_done:
            task.title, task.description, task.due_on = title, info, due
            if burial.grave_person or burial.grave_deputy:
                task.assignees = assignees


def refresh_grave_tasks(today=None):
    """Nach Änderungen im Kalender (Abwesenheit) bzw. morgens: Zuständige der offenen Grab-Aufgaben neu bestimmen.
    Liefert [(Aufgabe, neu zugeteilte Benutzer)] für den Push; committet nicht."""
    today = today or date.today()
    changed = []
    burials = ForestBurial.query.filter(ForestBurial.day >= today) \
        .filter((ForestBurial.grave_person_id.isnot(None)) | (ForestBurial.grave_deputy_id.isnot(None)))
    for burial in burials:
        for task in (burial.dig_task, burial.close_task):
            if task is None or task.is_done or task.due_on is None:
                continue
            wanted = _grave_assignees(burial.grave_person, burial.grave_deputy, task.due_on)
            if {u.id for u in wanted} != {u.id for u in task.assignees}:
                added = [u for u in wanted if u not in task.assignees]
                task.assignees = wanted
                changed.append((task, added))
    db.session.flush()
    return changed


def save_burial(burial, user, place, deceased, day, time=None, undertaker=None, note=None, selection=None, team=None,
                bells=None):
    """Beisetzung anlegen bzw. ändern. `team`: (Person, Vertretung) fürs Grab – None = bei neuen der Standard
    aus `grave_team()`, bei vorhandenen unverändert."""
    if team is not None:
        team = (list(team) + [None, None])[:2]
        if team[0] is not None and team[1] is not None and team[0].id == team[1].id:
            raise StorageError(_("Person und Vertretung müssen verschiedene Mitarbeiter sein."))
    deceased = (deceased or "").strip()[:150]
    if place is None:
        raise StorageError(_("Bitte Baum und Platz wählen."))
    if not deceased:
        raise StorageError(_("Bitte den Namen der verstorbenen Person angeben."))
    if day is None:
        raise StorageError(_("Bitte das Datum der Beisetzung angeben."))
    taken = [b for b in place.burials if burial is None or b.id != burial.id]
    if taken:
        raise StorageError(_("{place} ist schon belegt.", place=place.label))
    with db.session.no_autoflush:
        if burial is None:
            burial = ForestBurial(created_by_id=user.id if user else None)
            db.session.add(burial)
        burial.place = place
        burial.contract = place.contract
        burial.deceased = deceased
        burial.day = day
        burial.time = _time(time)
        burial.undertaker = (undertaker or "").strip()[:150] or None
        if bells is not None:
            burial.bells = bool(bells)
        burial.note = (note or "").strip() or None
        if selection is not None:
            burial.selection = selection
    db.session.flush()
    if team is None and burial.grave_person is None and burial.grave_deputy is None and burial.dig_task is None:
        team = grave_team()
    if team is not None:
        burial.grave_person, burial.grave_deputy = team
    _burial_tasks(burial, user)
    db.session.flush()
    return burial


def set_bells(burial, user, bells):
    """Kirchenglocken nachträglich an/aus – die Beschreibung der offenen Grab-Aufgaben zieht mit."""
    burial.bells = bool(bells)
    _burial_tasks(burial, user)
    db.session.flush()


def delete_burial(burial):
    for task in (burial.dig_task, burial.close_task):
        if task is not None and not task.is_done:
            db.session.delete(task)
    db.session.delete(burial)


def upcoming(today=None, days=30):
    today = today or date.today()
    return (ForestBurial.query.filter(ForestBurial.day >= today, ForestBurial.day <= today + timedelta(days=days))
            .order_by(ForestBurial.day, ForestBurial.time).all())


def burials(year=None):
    query = ForestBurial.query
    if year:
        query = query.filter(func.extract("year", ForestBurial.day) == year)
    return query.order_by(ForestBurial.day.desc(), ForestBurial.time.desc()).all()


def calendar_events(start, end, event):
    """Für den Kalender (agenda): Baumauswahl-Termine und Beisetzungen im Zeitraum. `event` = agenda._event."""
    rows = ForestBurial.query.filter(ForestBurial.day >= start, ForestBurial.day <= end)
    choices = ForestSelection.query.filter(ForestSelection.day >= start, ForestSelection.day <= end,
                                           ForestSelection.status != "abgesagt")
    return [event(s.day, "baumauswahl", f"baumauswahl-{s.id}", _("Baumauswahl: {name}", name=s.contact), "bi-signpost-2",
                  "success", url_for("forest.selection_detail", selection_id=s.id),
                  " · ".join(p for p in (s.guide.name if s.guide else None, s.phone) if p),
                  time=dtime.fromisoformat(s.time) if s.time else None) for s in choices] + [event(b.day, "beisetzung", f"beisetzung-{b.id}", _("Beisetzung: {place}", place=b.place.label),
                  "bi-tree", "secondary", url_for("forest.burial_page") + f"#b{b.id}",
                  " · ".join(p for p in (b.deceased, b.undertaker, _("mit Kirchenglocken") if b.bells else None) if p),
                  time=dtime.fromisoformat(b.time) if b.time else None) for b in rows]


# --- Sturm: Baumkontrolle anmahnen -------------------------------------------------------------------

def storm_check(now, done, mark):
    """Aus `push.daily` (morgens): Waren gestern Sturmböen, Push an alle der Rubrik – einmal je Tag."""
    from . import weather
    today = now.date()
    job = "forest_storm"
    if done(job, today) or not ForestTree.query.first():
        return 0
    mark(job, today)
    data = weather.get_weather()
    if not data or data.get("error"):
        return 0
    yesterday = next((d for d in data.get("days", []) if d["date"] == today - timedelta(days=1)), None)
    if not yesterday or (yesterday.get("gust") or 0) < STORM_GUST:
        return 0
    return push.notify(members(), "forest_storm",
                       Msg("Sturm gestern: Böen bis {kmh} km/h", kmh=round(yesterday["gust"])),
                       Msg("Bitte vor der nächsten Beisetzung Bäume und Wege im Wald kontrollieren."),
                       url_for("forest.overview"), tag=f"forest-storm-{today.isoformat()}")

