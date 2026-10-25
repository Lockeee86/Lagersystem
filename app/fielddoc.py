"""Schlagdokumentation aus Myfarm24 (CSV): Stammdaten, Kultur/Sorte, Bodenuntersuchung und Maßnahmen je Schlag.

Ergänzt den KML-Import (`fields.py`): die KML-Datei bringt die Umrisse, die Schlagdokumentation alles, was auf dem
Schlag passiert ist. Beide werden über `fields._match` denselben Schlägen zugeordnet (USchlag-ID, Nummer + Name).
Wie `storage.py` verändern die Funktionen nur die Session, committen muss der Aufrufer.

Aufbau der Datei (cp1252, „;“ getrennt): Kopfzeile „Schlagdokumentation Erntejahr 2026“, dann je Schlag ein Block
„Schlag: <Name> / Nr. <n> / FLIK: <FLIK>“ mit Schlüssel-Wert-Zeilen („Kultur: …“ oder „Fläche:;6,5;ha“) und den
Abschnitten Aussaat, Düngung, Pflanzenschutz, Arbeiten / Maschinen, Personen (je eine Kopfzeile „Datum;…“).
Beim erneuten Import werden die importierten Maßnahmen eines Anbaujahrs ersetzt – so bleibt alles auf dem Stand
von Myfarm24, ohne doppelt zu zählen.
"""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .extensions import db
from .fields import FieldImportError, _match, _numbered_name, _same_name
from .i18n import N_, gettext as _
from .models import FieldMeasure, NUTRIENTS, PesticideProduct, Plot, PlotYear, Setting, Variety
from .storage import StorageError

SOURCE = "myfarm"
YEAR_RE = re.compile(r"Schlagdokumentation\s+Erntejahr\s+(\d{4})")
BLOCK_RE = re.compile(r"^Schlag:\s*(.*?)\s*/\s*Nr\.\s*([^/;]*?)\s*(?:/\s*FLIK:\s*([^;]*?))?\s*;*$")
ROW_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4};")
SECTIONS = {
    "Aussaat:": "aussaat",
    "Düngung:": "duengung",
    "Pflanzenschutz:": "pflanzenschutz",
    "Arbeiten / Maschinen:": "arbeit",
    "Personen:": "person",
}
NO_VALUE = {"", "keine angabe", "-", "keine"}
POINT_RE = re.compile(r"N\s*(\d{1,2}(?:[.,]\d+)?)\s*,\s*E\s*(\d{1,3}(?:[.,]\d+)?)")
WAIT_DAYS_RE = re.compile(r"Wartezeit\s*:?\s*(\d{1,3})\s*Tag", re.IGNORECASE)
WAIT_COVERED_RE = re.compile(r"Wartezeit\s*:?\s*F\b", re.IGNORECASE)


def _text(value, length):
    value = (value or "").strip()
    return value[:length] if value.casefold() not in NO_VALUE else None


def _number(text):
    """„6,5“, „4.1“, „1.234,5“, „-7“ → Decimal oder None."""
    text = (text or "").strip().replace(" ", "")
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() and abs(value) < 10 ** 8 else None


def _date(text):
    m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4})", text or "")
    if not m:
        return None
    day, month, year = (int(v) for v in m.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _decode(data):
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def _measure(kind, cols):
    cols = [c.strip() for c in cols]
    if kind == "duengung" and len(cols) >= 15:
        del cols[7]                      # Myfarm24: leere Zusatzspalte nach „N gesamt“ (nur manchmal in den Daten)
    cols += [""] * 15

    def amount(i):
        return _number(cols[i])
    m = {"kind": kind, "done_on": _date(cols[0]), "bbch": None, "area_ha": None, "amount": None, "unit": None,
         "note": None, **{k: None for k in NUTRIENTS}}
    if kind == "aussaat":
        m.update(name=cols[1], area_ha=amount(2), amount=amount(3), unit=cols[4], note=cols[5])
    elif kind == "duengung":
        m.update(bbch=cols[1], name=cols[2], area_ha=amount(3), amount=amount(4), unit=cols[5], note=cols[13],
                 **{k: amount(6 + i) for i, k in enumerate(NUTRIENTS)})
    elif kind == "pflanzenschutz":
        m.update(bbch=cols[1], name=cols[2], area_ha=amount(3), amount=amount(4), unit=cols[5], note=cols[6])
    elif kind == "arbeit":
        m.update(bbch=cols[1], name=cols[2], area_ha=amount(3), note=cols[4])
    else:
        m.update(name=cols[1], amount=amount(2), unit="h" if cols[2] else None, note=cols[3])
    m["name"] = (m["name"] or "").strip()[:150]
    if not m["name"] or m["done_on"] is None:
        return None
    m["bbch"] = (m["bbch"] or "")[:20] or None
    m["unit"] = (m["unit"] or "")[:20] or None
    m["note"] = (m["note"] or "")[:300] or None
    if m["area_ha"] is not None and not 0 < m["area_ha"] < 100000:
        m["area_ha"] = None
    return m


def _new_entry(match):
    return {"name": match.group(1).strip()[:100], "number": (match.group(2) or "").strip()[:20] or None,
            "flik": (match.group(3) or "").strip()[:20] or None, "myfarm_id": None, "area_ha": None,
            "crop": None, "variety": None, "prev_crop": None, "prev_prev_crop": None, "n_demand": None,
            "planted_on": None, "harvested_on": None, "soil": None, "red_area": None, "ph": None,
            "p_class": None, "k_class": None, "mg_value": None, "soil_test_on": None, "catch_crop": None,
            "lat": None, "lon": None, "measures": []}


def _header(e, key, value, cols):
    """Eine Schlüssel-Wert-Zeile des Schlag-Kopfes übernehmen."""
    first = cols[0] if cols else value
    if key == "Fläche":
        area = _number(first)
        e["area_ha"] = area.quantize(Decimal("0.0001")) if area and 0 < area < 100000 else None
    elif key == "USchlag-ID":
        e["myfarm_id"] = _text(first, 30)
    elif key == "Kultur":
        e["crop"] = _text(value, 100)
    elif key == "Sorte":
        e["variety"] = _text(value, 100)
    elif key == "Vorfrucht":
        e["prev_crop"] = _text(value, 100)
    elif key == "Vorvorfrucht":
        e["prev_prev_crop"] = _text(value, 100)
    elif key.startswith("DüV Bedarf"):
        m = re.search(r"-?\d+", value)
        e["n_demand"] = int(m.group()) if m and abs(int(m.group())) < 10000 else None
    elif key == "Saatdatum":
        e["planted_on"] = _date(value)
    elif key == "Erntedatum":
        e["harvested_on"] = _date(value)
    elif key == "Bodenart":
        e["soil"] = _text(value, 60)
    elif key == "Rotes Gebiet":
        flag = value.strip().upper()[:1]
        e["red_area"] = True if flag == "J" else False if flag == "N" else None
    elif key == "pH-Wert":
        ph = _number(first)
        e["ph"] = ph if ph is not None and 0 < ph < 15 else None
    elif key == "P-Klasse":
        e["p_class"] = _text(value, 5)
    elif key == "K-Klasse":
        e["k_class"] = _text(value, 5)
    elif key == "Mg":
        mg = _number(first)
        e["mg_value"] = mg if mg is not None and 0 <= mg < 10000 else None
    elif key == "Analyse" and e["soil_test_on"] is None:
        e["soil_test_on"] = _date(value)     # die erste Analyse ist die Bodenuntersuchung, die zweite N-min
    elif key == "Zwischenfrucht":
        e["catch_crop"] = _text(value, 100)
    elif key.startswith("Geografische Lage"):
        m = POINT_RE.search(value)
        if m:
            lat, lon = (Decimal(v.replace(",", ".")) for v in m.groups())
            if 0 < lat <= 90 and 0 <= lon <= 180:
                e["lat"], e["lon"] = lat.quantize(Decimal("0.000001")), lon.quantize(Decimal("0.000001"))


def parse_csv(data):
    """Liest die Datei. Liefert (Erntejahr, [Schlag-dicts mit "measures"])."""
    text = _decode(data)
    year_match = YEAR_RE.search(text[:500])
    if not year_match:
        raise FieldImportError(_("Die Datei ist keine Schlagdokumentation aus Myfarm24 (CSV)."))
    year = int(year_match.group(1))
    entries, e, section = [], None, None
    for raw in text.splitlines():
        line = raw.strip()
        block = BLOCK_RE.match(line)
        if block:
            e, section = _new_entry(block), None
            if e["name"]:
                entries.append(e)
            continue
        if e is None or not line:
            continue
        bare = line.rstrip(";").strip()
        if bare in SECTIONS:
            section = SECTIONS[bare]
            continue
        if section and ROW_RE.match(line):
            measure = _measure(section, line.split(";"))
            if measure:
                e["measures"].append(measure)
            continue
        if ":" in line and not line.startswith("Datum;"):
            key, rest = line.split(":", 1)
            if ";" in key:
                continue
            cols = rest[1:].split(";") if rest.startswith(";") else []
            _header(e, key.strip(), rest.strip().strip(";").strip(), cols)
    if not entries:
        raise FieldImportError(_("In der Datei sind keine Schläge."))
    return year, entries


# --- Abgleich und Übernahme ------------------------------------------------------

def _changes(plot, e, year):
    if plot is None:
        return ["neu"]
    changes = []
    if not _same_name(plot, e) or plot.number != e["number"]:
        changes.append("name")
    if e["area_ha"] is not None and (plot.area_ha is None or
                                     abs(Decimal(plot.area_ha) - e["area_ha"]) > Decimal("0.0001")):
        changes.append("area")
    current = plot.year(year)
    if (current.crop if current else None) != e["crop"]:
        changes.append("crop")
    old = sorted((m.kind, m.done_on, m.name) for m in (current.measures if current else []) if m.source == SOURCE)
    if old != sorted((m["kind"], m["done_on"], m["name"]) for m in e["measures"]):
        changes.append("measures")
    return changes


def plan_import(entries, year):
    """Vorschau: [{entry, plot, changes, counts}] und aktive Schläge, die nicht in der Datei sind."""
    matches = _match(entries)
    rows = []
    for e, plot in zip(entries, matches):
        counts = {}
        for m in e["measures"]:
            counts[m["kind"]] = counts.get(m["kind"], 0) + 1
        rows.append({"entry": e, "plot": plot, "changes": _changes(plot, e, year), "counts": counts})
    seen = {p.id for p in matches if p}
    missing = [p for p in Plot.query.filter_by(active=True).order_by(Plot.name) if p.id not in seen]
    return rows, missing


def _year_entry(plot, year):
    entry = plot.year(year)
    if entry is None:
        entry = PlotYear(year=year)
        plot.years.append(entry)
    return entry


def apply_import(entries, year, create=True):
    """Übernimmt die Schlagdokumentation. Mit `create=False` werden Schläge, die es noch nicht gibt, übersprungen.
    Pflanz- und Rodedatum aus der App bleiben stehen (werden nur ergänzt). Liefert ein dict mit Zählern."""
    rows, _missing = plan_import(entries, year)
    taken = {p.name.casefold() for p in Plot.query.all()}
    varieties = {v.name.casefold(): v for v in Variety.query.all()}
    stats = {"created": 0, "updated": 0, "skipped": 0, "measures": 0}
    for row in rows:
        e, plot = row["entry"], row["plot"]
        if plot is None:
            if not create:
                stats["skipped"] += 1
                continue
            name = e["name"]
            if name.casefold() in taken:
                name = _numbered_name(e)
            if name.casefold() in taken:
                raise FieldImportError(_("Den Schlag „{name}“ gibt es schon.", name=name))
            plot = Plot(name=name, active=True)
            db.session.add(plot)
            stats["created"] += 1
        else:
            stats["updated"] += 1 if row["changes"] else 0
            if not _same_name(plot, e) and e["name"].casefold() not in (taken - {plot.name.casefold()}):
                taken.discard(plot.name.casefold())
                plot.name = e["name"]
        taken.add(plot.name.casefold())
        plot.number = e["number"]
        plot.myfarm_id = e["myfarm_id"] or plot.myfarm_id
        plot.flik = e["flik"] or plot.flik
        for key in ("area_ha", "soil", "red_area", "ph", "p_class", "k_class", "mg_value", "soil_test_on",
                    "lat", "lon"):
            if e[key] is not None:
                setattr(plot, key, e[key])

        entry = _year_entry(plot, year)
        if entry.crop != e["crop"]:
            entry.crop, entry.crop_eppo = e["crop"], None
        entry.variety_name = e["variety"]
        variety = varieties.get((e["variety"] or "").casefold())
        if variety is not None and entry.variety_id is None:
            entry.variety_id = variety.id
        entry.n_demand = e["n_demand"]
        entry.catch_crop = e["catch_crop"]
        entry.planted_on = entry.planted_on or e["planted_on"]
        entry.harvested_on = entry.harvested_on or e["harvested_on"]
        for back, crop in ((1, e["prev_crop"]), (2, e["prev_prev_crop"])):
            if crop:
                older = _year_entry(plot, year - back)
                if not older.crop:
                    older.crop, older.crop_eppo = crop, None

        for old in [m for m in entry.measures if m.source == SOURCE]:
            entry.measures.remove(old)
        for m in e["measures"]:
            entry.measures.append(FieldMeasure(source=SOURCE, **m))
        stats["measures"] += len(e["measures"])
        db.session.flush()
    return stats


# --- Anzeige -------------------------------------------------------------------

def summary(entry):
    """Maßnahmen eines Anbaujahrs für die Schlagseite: {art: [Maßnahmen]}, Nährstoffsumme (kg/ha), Stunden."""
    groups = {}
    for m in entry.measures if entry else []:
        groups.setdefault(m.kind, []).append(m)
    fert = groups.get("duengung", [])
    nutrients = {k: sum((getattr(m, k) or 0) for m in fert) for k in NUTRIENTS} if fert else None
    hours = sum((m.amount or 0) for m in groups.get("person", []))
    return {"groups": groups, "nutrients": nutrients, "hours": hours}


# --- Pflanzenschutz: Wartezeiten ----------------------------------------------------

def products():
    """Gepflegte Mittel {name.casefold(): PesticideProduct}."""
    return {p.name.casefold(): p for p in PesticideProduct.query.all()}


def note_wait(note):
    """Wartezeit aus der Bemerkung: Tage (int), "F" oder None."""
    m = WAIT_DAYS_RE.search(note or "")
    if m:
        return int(m.group(1))
    return "F" if WAIT_COVERED_RE.search(note or "") else None


def wait_of(measure, known):
    """Wartezeit einer Anwendung: Tage (int), "F" (durch den Anwendungszeitpunkt abgedeckt) oder None (unbekannt).
    Die Bemerkung aus Myfarm24 geht vor, sonst gilt die gepflegte Mittel-Liste."""
    wait = note_wait(measure.note)
    if wait is not None:
        return wait
    product = known.get(measure.name.casefold())
    if product is None:
        return None
    if product.covered:
        return "F"
    return product.wait_days


def protection(entry, known, today=None):
    """Pflanzenschutz eines Anbaujahrs: letzte Anwendung, Ende der Wartezeit, Mittel ohne bekannte Wartezeit."""
    today = today or date.today()
    items = [m for m in (entry.measures if entry else []) if m.kind == "pflanzenschutz" and m.done_on]
    if not items:
        return None
    until, unknown = None, []
    for m in items:
        wait = wait_of(m, known)
        if isinstance(wait, int):
            end = m.done_on + timedelta(days=wait)
            if until is None or end > until:
                until = end
        elif wait is None and m.name not in unknown:
            unknown.append(m.name)
    harvested = entry.harvested_on is not None and entry.harvested_on <= today
    return {"count": len(items), "last": max(items, key=lambda m: (m.done_on, m.id or 0)), "until": until,
            "running": until is not None and until > today and not harvested,
            "days_left": (until - today).days if until and until > today else 0,
            "unknown": unknown, "harvested": harvested}


def protection_overview(year, today=None):
    """Alle Schläge mit Pflanzenschutz im Erntejahr und die eingesetzten Mittel (Anzahl, Fläche, Menge)."""
    known = products()
    entries = (PlotYear.query.filter_by(year=year)
               .join(FieldMeasure, FieldMeasure.plot_year_id == PlotYear.id)
               .filter(FieldMeasure.kind == "pflanzenschutz").distinct().all())
    rows = []
    for entry in entries:
        info = protection(entry, known, today)
        if info:
            rows.append({"plot": entry.plot, "entry": entry, **info})
    # laufende Wartezeiten zuerst (die längste oben), dann nach Schlag
    rows.sort(key=lambda r: (not r["running"], -(r["days_left"] or 0), r["plot"].name.casefold()))
    used = {}
    for row in rows:
        for m in row["entry"].measures:
            if m.kind != "pflanzenschutz":
                continue
            u = used.setdefault(m.name.casefold(), {"name": m.name, "count": 0, "area": Decimal(0), "amounts": {},
                                                    "plots": set(), "note_waits": set()})
            u["count"] += 1
            u["plots"].add(row["plot"].id)
            if m.area_ha:
                u["area"] += m.area_ha
                if m.amount and m.unit:
                    u["amounts"][m.unit] = u["amounts"].get(m.unit, Decimal(0)) + m.area_ha * m.amount
            wait = note_wait(m.note)
            if wait is not None:
                u["note_waits"].add(wait)
    for u in used.values():
        u["product"] = known.get(u["name"].casefold())
        u["note_waits"] = sorted(u["note_waits"], key=str)
    return rows, sorted(used.values(), key=lambda u: u["name"].casefold())


def save_product(name, wait_days, covered):
    """Wartezeit eines Mittels setzen (committet nicht). Ohne Angabe wird der Eintrag entfernt."""
    name = (name or "").strip()[:150]
    if not name:
        raise FieldImportError(_("Bitte einen Namen angeben."))
    product = products().get(name.casefold())
    if wait_days is None and not covered:
        if product is not None:
            db.session.delete(product)
        return None
    if product is None:
        product = PesticideProduct(name=name)
        db.session.add(product)
    product.wait_days, product.covered = (None if covered else wait_days), bool(covered)
    return product


# --- Fruchtfolgeplan (CSV aus Myfarm24) --------------------------------------------------
# Kopf „Fruchtfolgeplan Erntejahre 2026 - 2030“, dann „Schlag;Nr.;ha;2026;2027;…“ und je Schlag eine Zeile mit
# Kürzeln (K, M, RW …). Die Legende darunter („K - Kartoffel;…“) übersetzt die Kürzel.
# „Sonst,“ = sonstige Kultur, „-“ = Schlag in dem Jahr nicht im Plan, leer = noch nicht geplant.

PLAN_RE = re.compile(r"Fruchtfolgeplan\s+Erntejahre?\s+(\d{4})")
LEGEND_RE = re.compile(r"^([A-Za-zÄÖÜäöü]{1,6})\s+-\s+([^;]+)")
PLAN_OTHER = "Sonstige"
PLAN_NONE = "-"


def parse_plan(data):
    """Liest den Fruchtfolgeplan. Liefert (Jahre, [{name, number, area_ha, plan: {jahr: kultur|"-"|None}}])."""
    text = _decode(data)
    if not PLAN_RE.search(text[:300]):
        raise FieldImportError(_("Die Datei ist kein Fruchtfolgeplan aus Myfarm24 (CSV)."))
    lines = [l.strip() for l in text.splitlines()]
    legend = {}
    for line in lines:
        m = LEGEND_RE.match(line)
        if m:
            legend[m.group(1)] = m.group(2).strip()[:100]
    years, rows, header = [], [], False
    for line in lines:
        cols = [c.strip() for c in line.split(";")]
        if not header:
            if [c.lower() for c in cols[:3]] == ["schlag", "nr.", "ha"]:
                years = [int(c) for c in cols[3:] if re.fullmatch(r"\d{4}", c)]
                header = True
            continue
        if not line or cols[0].startswith("Kulturen"):
            break
        if not cols[0]:
            continue
        cols += [""] * (3 + len(years))
        plan = {}
        for year, code in zip(years, cols[3:3 + len(years)]):
            code = code.strip().rstrip(",").strip()
            if not code:
                plan[year] = None
            elif code == PLAN_NONE:
                plan[year] = PLAN_NONE
            elif code.lower().startswith("sonst"):
                plan[year] = PLAN_OTHER
            else:
                plan[year] = legend.get(code, code)[:100]
        area = _number(cols[2])
        rows.append({"name": cols[0][:100], "number": cols[1][:20] or None, "myfarm_id": None, "rings": None,
                     "area_ha": area.quantize(Decimal("0.0001")) if area and 0 < area < 100000 else None,
                     "plan": plan})
    if not years or not rows:
        raise FieldImportError(_("Im Fruchtfolgeplan sind keine Schläge."))
    return years, rows


def plan_rotation(entries, years):
    """Vorschau: [{entry, plot, changes: [jahre]}] und aktive Schläge, die nicht im Plan stehen."""
    matches = _match(entries)
    rows = []
    for e, plot in zip(entries, matches):
        changes = []
        if plot is not None:
            for year in years:
                current = plot.year(year)
                if (current.planned_crop if current else None) != e["plan"][year]:
                    changes.append(year)
        rows.append({"entry": e, "plot": plot, "changes": changes})
    seen = {p.id for p in matches if p}
    missing = [p for p in Plot.query.filter_by(active=True).order_by(Plot.name) if p.id not in seen]
    return rows, missing


def apply_rotation(entries, years, create=True):
    """Übernimmt die geplanten Kulturen für die Jahre des Plans (leere Zellen löschen die Planung).
    Die tatsächliche Kultur (aus Schlagdokumentation/KML) bleibt unberührt. Liefert ein dict mit Zählern."""
    rows, _missing = plan_rotation(entries, years)
    taken = {p.name.casefold() for p in Plot.query.all()}
    stats = {"created": 0, "updated": 0, "skipped": 0}
    for row in rows:
        e, plot = row["entry"], row["plot"]
        if plot is None:
            if not create:
                stats["skipped"] += 1
                continue
            name = e["name"] if e["name"].casefold() not in taken else _numbered_name(e)
            if name.casefold() in taken:
                raise FieldImportError(_("Den Schlag „{name}“ gibt es schon.", name=name))
            plot = Plot(name=name, number=e["number"], area_ha=e["area_ha"], active=True)
            db.session.add(plot)
            taken.add(name.casefold())
            stats["created"] += 1
        elif row["changes"]:
            stats["updated"] += 1
        for year in years:
            planned = e["plan"][year]
            entry = plot.year(year)
            if entry is None:
                if planned is None:
                    continue
                entry = _year_entry(plot, year)
            entry.planned_crop = planned
        db.session.flush()
    return stats


# Anbaupause (Nematoden, Krankheiten): Kulturgruppe (Wort im Namen), Anzeige, Setting-Schlüssel, Jahre von sich aus.
# Pause = Jahre ohne diese Kultur dazwischen (3: Kartoffeln 2026 → wieder ab 2030).
ROTATION_GROUPS = [("kartoffel", N_("Kartoffel"), "rotation_break_kartoffel", 3),
                   ("rübe", N_("Rübe"), "rotation_break_ruebe", 3)]
ROTATION_MAX = 10


def rotation_breaks():
    """{Wort: Jahre} – eingestellt unter Fruchtfolge (Admin), sonst die Voreinstellung."""
    result = {}
    for word, _label, key, default in ROTATION_GROUPS:
        setting = db.session.get(Setting, key)
        value = int(setting.value) if setting and (setting.value or "").isdigit() else default
        result[word] = min(max(value, 1), ROTATION_MAX)
    return result


def save_rotation_breaks(values):
    """`values`: {Wort: Jahre}; nur 1 bis ROTATION_MAX."""
    for word, _label, key, _default in ROTATION_GROUPS:
        years = values.get(word)
        if years is None:
            continue
        if not 1 <= years <= ROTATION_MAX:
            raise StorageError(_("Anbaupause bitte zwischen 1 und {n} Jahren.", n=ROTATION_MAX))
        Setting.put(key, str(years))


def _crop_group(crop, breaks):
    lower = (crop or "").lower()
    return next(((word, breaks[word]) for word, *_rest in ROTATION_GROUPS if word in lower), None)


def rotation(plots, years, breaks=None):
    """Fruchtfolge-Tabelle: je Schlag und Jahr {crop, planned, color, warn}, dazu Hektar je Kultur und Jahr.
    `warn` = gleiche Kultur(gruppe) innerhalb der Anbaupause (Kartoffel, Rübe; `rotation_breaks()`)."""
    from .fields import crop_color
    breaks = breaks or rotation_breaks()
    first = min(years) - max(breaks.values())
    rows, totals = [], {y: {} for y in years}
    for plot in plots:
        by_year = {py.year: py for py in plot.years}
        cells, any_data = [], False
        for year in years:
            entry = by_year.get(year)
            crop = entry.crop if entry else None
            planned = entry.planned_crop if entry else None
            shown = crop or (planned if planned != PLAN_NONE else None)
            warn = None
            group = _crop_group(shown, breaks)
            if group:
                for back in range(1, group[1] + 1):
                    earlier = by_year.get(year - back)
                    if year - back >= first and earlier and _crop_group(earlier.shown_crop, breaks) == group:
                        warn = year - back
                        break
            cells.append({"year": year, "crop": crop, "planned": planned, "shown": shown, "warn": warn,
                          "color": crop_color(shown, entry.crop_eppo if entry and crop else None) if shown else None})
            if shown or planned:
                any_data = True
            if shown:
                bucket = totals[year].setdefault(shown, {"ha": Decimal(0), "color": cells[-1]["color"]})
                bucket["ha"] += Decimal(plot.area_ha or 0)
        if any_data:
            rows.append({"plot": plot, "cells": cells})
    crops = {}
    for year, per_crop in totals.items():
        for crop, t in per_crop.items():
            c = crops.setdefault(crop, {"crop": crop, "color": t["color"], "ha": {}, "total": Decimal(0)})
            c["ha"][year] = t["ha"]
            c["total"] += t["ha"]
    summary = sorted(crops.values(), key=lambda c: (c["crop"] == PLAN_OTHER, -c["total"]))
    return rows, summary
