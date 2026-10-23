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
from datetime import date
from decimal import Decimal, InvalidOperation

from .extensions import db
from .fields import FieldImportError, _match, _numbered_name, _same_name
from .i18n import gettext as _
from .models import FieldMeasure, NUTRIENTS, Plot, PlotYear, Variety

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
            "measures": []}


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
        for key in ("area_ha", "soil", "red_area", "ph", "p_class", "k_class", "mg_value", "soil_test_on"):
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
