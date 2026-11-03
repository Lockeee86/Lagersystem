"""Bestattungswald: Bäume mit Beisetzungsplätzen, Verträge (Reservierungen), Beisetzungen, Baumkontrolle.

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
from .i18n import gettext as _
from .machines import add_months
from .models import FOREST_KINDS, ForestBurial, ForestContract, ForestPlace, ForestTree, Setting, Task, User
from .push import Msg
from .storage import StorageError

DEFAULT_PLACES = 12
TERM_YEARS = 99                 # übliche Nutzungszeit, im Vertrag änderbar
STORM_GUST = 70                 # km/h Böen: danach Baumkontrolle anmahnen
CODE_RE = re.compile(r"^W-?(\d{1,5})$")
AREA = "wald"                   # Aufgaben und Rundgang-Stationen der Rubrik


def name():
    return (Setting.all().get("forest_name") or "").strip() or _("Bestattungswald")


def normalize_code(raw):
    """„W-047“, „w47“ oder eine URL, die darauf endet → „W-047“ (None, wenn es kein Baum-Code ist)."""
    value = (raw or "").strip().upper().rstrip("/").rsplit("/", 1)[-1]
    m = CODE_RE.match(value)
    return f"W-{int(m.group(1)):03d}" if m else None


def tree_by_code(raw):
    code = normalize_code(raw)
    return ForestTree.query.filter_by(number=int(code[2:])).first() if code else None


def members():
    return [u for u in User.query.filter_by(active=True) if u.sees_forest]


# --- Bäume ----------------------------------------------------------------------------------------

def _set_places(tree, count):
    count = int(count or 0)
    if not 1 <= count <= 99:
        raise StorageError(_("Ein Baum hat 1 bis 99 Plätze."))
    have = {p.number for p in tree.places}
    for n in range(1, count + 1):
        if n not in have:
            tree.places.append(ForestPlace(number=n))
    for place in [p for p in tree.places if p.number > count]:
        if place.state != "frei":
            raise StorageError(_("Platz {n} ist vergeben – so wenige Plätze gehen nicht.", n=place.number))
        tree.places.remove(place)


def create_trees(first, last, places=DEFAULT_PLACES, species=None, section=None, kind="gemeinschaft"):
    """Bäume mit den Nummern first–last anlegen (vorhandene bleiben). Liefert die Zahl der neuen."""
    if first is None or last is None or not 1 <= first <= last or last - first >= 2000:
        raise StorageError(_("Bitte die Nummern von–bis angeben (höchstens 2000 auf einmal)."))
    existing = {n for (n,) in db.session.query(ForestTree.number).filter(ForestTree.number.between(first, last))}
    added = 0
    for number in range(first, last + 1):
        if number in existing:
            continue
        tree = ForestTree(number=number, species=(species or "").strip()[:60] or None,
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


def save_tree(tree, number, species, section, kind, places, lat, lon, note, active=True):
    if not number or number < 1:
        raise StorageError(_("Bitte eine Baumnummer angeben."))
    other = ForestTree.query.filter(ForestTree.number == number, ForestTree.id != (tree.id if tree else None)).first()
    if other:
        raise StorageError(_("Baum {n} gibt es schon.", n=number))
    if tree is None:
        tree = ForestTree(number=number)
        db.session.add(tree)
    tree.number = number
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
    """„Familienbaum“, „familie“, „Einzelbaum“, „einzel“ … → Schlüssel aus FOREST_KINDS (sonst Gemeinschaftsbaum)."""
    text = (text or "").strip().lower()
    return "familie" if text.startswith("famil") else "einzel" if text.startswith("einzel") else "gemeinschaft"


def parse_trees_csv(data):
    """CSV mit Spalten nummer, baumart, abschnitt, art, plaetze, breite, laenge (nur nummer ist Pflicht)."""
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
        try:
            number = int(get("nummer"))
        except ValueError:
            raise StorageError(_("Zeile {line}: „{value}“ ist keine Baumnummer.", line=i, value=get("nummer")[:20]))
        rows.append({"number": number, "species": get("baumart"), "section": get("abschnitt"),
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
        tree = ForestTree.query.filter_by(number=r["number"]).first()
        if tree is None:
            added += 1
        else:
            changed += 1
        save_tree(tree, r["number"], r["species"], r["section"], r["kind"], r["places"], r["lat"], r["lon"],
                  tree.note if tree else None, tree.active if tree else True)
    return added, changed


def tree_rows(section=None, only_free=False):
    """Bäume mit Zählern je Stand: [{tree, frei, reserviert, belegt, total}]."""
    query = ForestTree.query.filter_by(active=True)
    if section:
        query = query.filter(ForestTree.section == section)
    from sqlalchemy.orm import selectinload
    rows = []
    for tree in query.options(selectinload(ForestTree.places).selectinload(ForestPlace.burials)).order_by(ForestTree.number):
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


def map_data():
    """Bäume mit Lage für die Karte (Schlagkarte.js): Farbe nach freien Plätzen."""
    items = []
    for row in tree_rows():
        tree = row["tree"]
        if tree.lat is None or tree.lon is None:
            continue
        color = "#3f8f4f" if row["frei"] == row["total"] else "#e0a800" if row["frei"] else \
            "#7a5ea8" if row["reserviert"] else "#8a8a8a"
        items.append({"id": tree.number, "name": tree.label, "number": str(tree.number), "color": color, "rings": [],
                      "point": [tree.lon, tree.lat], "ha": None,
                      "crop": _("{free} von {total} frei", free=row["frei"], total=row["total"])})
    return items


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
                  price=None, note=None):
    """Vertrag anlegen bzw. ändern. `places`: ForestPlace-Objekte (frei oder schon zu diesem Vertrag)."""
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
    contract.ends_on = add_months(signed_on, 12 * years)
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
    return contract


def delete_contract(contract):
    if contract.burials:
        raise StorageError(_("Zu diesem Vertrag gibt es schon Beisetzungen – er kann nicht gelöscht werden."))
    for place in list(contract.places):
        place.contract = None
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


def _burial_tasks(burial, user):
    """Aufgaben „Grab ausheben“ (Werktag davor) und „Grab schließen“ (am Tag) anlegen bzw. nachziehen."""
    from . import tasks
    where = burial.place.label
    when = burial.day.strftime("%d.%m.%Y") + (f" {burial.time}" if burial.time else "")
    info = _("Beisetzung {when}", when=when) + (f" · {burial.undertaker}" if burial.undertaker else "")
    for attr, title, due in (("dig_task", _("Grab ausheben: {place}", place=where), _workday_before(burial.day)),
                             ("close_task", _("Grab schließen: {place}", place=where), burial.day)):
        task = getattr(burial, attr)
        if task is None:
            task = tasks.create_task(user, title=title, description=info, due_on=due, important=True)
            task.area = AREA
            setattr(burial, attr, task)
        elif not task.is_done:
            task.title, task.description, task.due_on = title, info, due


def save_burial(burial, user, place, deceased, day, time=None, undertaker=None, note=None):
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
        burial.note = (note or "").strip() or None
    db.session.flush()
    _burial_tasks(burial, user)
    db.session.flush()
    return burial


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
    """Für den Kalender (agenda): Beisetzungen im Zeitraum. `event` = agenda._event."""
    rows = ForestBurial.query.filter(ForestBurial.day >= start, ForestBurial.day <= end)
    return [event(b.day, "beisetzung", f"beisetzung-{b.id}", _("Beisetzung: {place}", place=b.place.label),
                  "bi-tree", "secondary", url_for("forest.burial_page") + f"#b{b.id}",
                  " · ".join(p for p in (b.deceased, b.undertaker) if p),
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

