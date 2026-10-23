"""Schlagkartei: Import aus KML (z. B. Myfarm24), Fruchtfolge je Jahr, Ertrag aus den Kisten.

Wie `storage.py` verändern die Funktionen nur die Session, committen muss der Aufrufer.

Myfarm24 schreibt die Schlagdaten als „Schlüssel: Wert;“ in die <description> eines Placemarks:
SchlagBez, SchlagNr, SchlagHa, KulturBez, KulturEPPO, VorfruchtBez, VorfruchtEPPO, Bodenart, RotesGebiet.
Andere KML-Dateien gehen auch – dann kommt der Name aus <name> und die Fläche aus dem Umriss.
"""
import io
import json
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

from .extensions import db
from .i18n import gettext as _
from .models import Plot, PlotYear
from .storage import StorageError

KML_NS = "{http://www.opengis.net/kml/2.2}"
NAME_NUMBER_RE = re.compile(r"^(.*?)\s*\(\s*Nr\.?\s*([\w./-]+)\s*\)\s*$")
DESCRIPTION_RE = re.compile(r"(\w+):[ \t]*([^;\n]*);")
NO_CROP = {"", "keine", "-"}

# Farben je Kultur (EPPO-Code), sonst aus dem Namen abgeleitet
CROP_COLORS = {
    "SOLTU": "#c8a24a",   # Kartoffel
    "ZEAMX": "#e3c84f",   # Mais
    "TRZAW": "#d19b5a",   # Winterweizen
    "TRZSP": "#b9825a",   # Dinkel
    "SECCW": "#9c7b52",   # Winterroggen
    "BEAVA": "#8a5aa8",   # Zuckerrübe
    "CERAR": "#5c9e4f",   # Ackergras
    "HORVW": "#c7b26a",   # Wintergerste
    "BRSNW": "#e8d33a",   # Winterraps
}
FALLBACK_COLORS = ["#4f8fba", "#7fae5b", "#c76d4f", "#5aa89a", "#a8845a", "#8f6fb3", "#b5a33c", "#6b8e3d"]
NO_CROP_COLOR = "#b7b2a6"


class FieldImportError(StorageError):
    pass


# --- KML lesen ---------------------------------------------------------------

def _kml_bytes(filename, data):
    if (filename or "").lower().endswith(".kmz") or data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                name = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
                if name is None:
                    raise FieldImportError(_("In der KMZ-Datei ist keine KML-Datei."))
                return z.read(name)
        except zipfile.BadZipFile:
            raise FieldImportError(_("Die Datei ist keine gültige KML- oder KMZ-Datei."))
    return data


def _ring(text):
    points = []
    for chunk in (text or "").split():
        parts = chunk.split(",")
        try:
            lon, lat = float(parts[0]), float(parts[1])
        except (IndexError, ValueError):
            continue
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            points.append([round(lon, 7), round(lat, 7)])
    return points if len(points) >= 3 else None


def ring_area_ha(ring):
    """Fläche eines Rings in ha (sphärische Näherung, für Feldgröße ausreichend genau)."""
    if len(ring) < 3:
        return 0.0
    radius = 6371008.8
    lat0 = math.radians(sum(p[1] for p in ring) / len(ring))
    xs = [math.radians(p[0]) * radius * math.cos(lat0) for p in ring]
    ys = [math.radians(p[1]) * radius for p in ring]
    area = sum(xs[i] * ys[(i + 1) % len(ring)] - xs[(i + 1) % len(ring)] * ys[i] for i in range(len(ring)))
    return abs(area) / 2 / 10000


def _decimal(text):
    try:
        value = Decimal((text or "").strip().replace(",", "."))
    except InvalidOperation:
        return None
    return value if value > 0 else None


def parse_kml(filename, data):
    """Liest alle Placemarks mit Polygon. Liefert eine Liste von dicts (Reihenfolge wie in der Datei)."""
    data = _kml_bytes(filename, data)
    if b"<!DOCTYPE" in data[:2000].upper() or b"<!ENTITY" in data.upper():
        raise FieldImportError(_("Die Datei ist keine gültige KML- oder KMZ-Datei."))
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise FieldImportError(_("Die Datei ist keine gültige KML- oder KMZ-Datei."))
    entries = []
    for pm in root.iter(f"{KML_NS}Placemark"):
        rings = [r for el in pm.iter(f"{KML_NS}outerBoundaryIs")
                 for coords in el.iter(f"{KML_NS}coordinates") if (r := _ring(coords.text))]
        if not rings:
            continue
        raw_name = (pm.findtext(f"{KML_NS}name") or "").strip()
        info = dict(DESCRIPTION_RE.findall(pm.findtext(f"{KML_NS}description") or ""))
        name = info.get("SchlagBez", "").strip()
        number = info.get("SchlagNr", "").strip()
        if not name:
            m = NAME_NUMBER_RE.match(raw_name)
            name, number = (m.group(1), number or m.group(2)) if m else (raw_name, number)
        if not name:
            continue
        area = _decimal(info.get("SchlagHa")) or Decimal(str(round(sum(ring_area_ha(r) for r in rings), 4)))

        def crop(key):
            value = info.get(key, "").strip()
            return None if value.lower() in NO_CROP else value[:100]
        red = info.get("RotesGebiet", "").strip().upper()
        entries.append({
            "name": name[:100], "number": number[:20] or None, "area_ha": area,
            "crop": crop("KulturBez"), "crop_eppo": info.get("KulturEPPO", "").strip()[:10] or None,
            "prev_crop": crop("VorfruchtBez"), "prev_eppo": info.get("VorfruchtEPPO", "").strip()[:10] or None,
            "soil": info.get("Bodenart", "").strip()[:60] or None,
            "red_area": True if red == "J" else False if red == "N" else None,
            "rings": rings,
        })
    if not entries:
        raise FieldImportError(_("In der Datei sind keine Schläge mit Umriss."))
    return entries


# --- Abgleich und Übernahme ------------------------------------------------------

def _numbered_name(e):
    """Name bei doppeltem Schlagnamen (z. B. zweimal „Obere Wiese“ mit Nr. 2 und 1002)."""
    return f"{e['name']} ({e['number']})"[:100] if e["number"] else e["name"]


def _same_name(plot, e):
    return plot.name.casefold() in (e["name"].casefold(), _numbered_name(e).casefold())

def _match(entries):
    """Ordnet jedem Eintrag einen vorhandenen Schlag zu (oder None = neu).
    Nummern sind in Myfarm24 nicht eindeutig, daher zuerst Nummer + Name, dann Name (Schlag ohne Nummer).
    Umbenannte Schläge werden am unveränderten Umriss erkannt oder an einer Nummer, die in Datei und
    Datenbank nur einmal vorkommt. Einträge aus der Schlagdokumentation bringen die USchlag-ID mit – die zählt zuerst."""
    plots = Plot.query.all()
    by_id = {p.myfarm_id: p for p in plots if p.myfarm_id}
    by_key = {((p.number or ""), p.name.casefold()): p for p in plots}
    by_name = {p.name.casefold(): p for p in plots if not p.number}
    by_shape = {p.boundary: p for p in plots if p.boundary}
    numbers_db, numbers_file = {}, {}
    for p in plots:
        if p.number:
            numbers_db.setdefault(p.number, []).append(p)
    for e in entries:
        if e["number"]:
            numbers_file[e["number"]] = numbers_file.get(e["number"], 0) + 1
    used, matches = set(), []
    for e in entries:
        plot = (by_id.get(e.get("myfarm_id"))
                or by_key.get((e["number"] or "", e["name"].casefold()))
                or by_key.get((e["number"] or "", _numbered_name(e).casefold()))
                or by_name.get(e["name"].casefold())
                or (by_shape.get(json.dumps(e["rings"], separators=(",", ":"))) if e.get("rings") else None))
        if plot is None and e["number"] and numbers_file[e["number"]] == 1 \
                and len(numbers_db.get(e["number"], [])) == 1:
            plot = numbers_db[e["number"]][0]
        if plot is not None and plot.id in used:
            plot = None
        if plot is not None:
            used.add(plot.id)
        matches.append(plot)
    return matches


def _changes(plot, e, year):
    if plot is None:
        return ["neu"]
    changes = []
    if not _same_name(plot, e):
        changes.append("name")
    if plot.number != e["number"]:
        changes.append("number")
    if plot.area_ha is None or abs(Decimal(plot.area_ha) - e["area_ha"]) > Decimal("0.0001"):
        changes.append("area")
    if plot.soil != e["soil"] or plot.red_area != e["red_area"]:
        changes.append("soil")
    if plot.rings != e["rings"]:
        changes.append("boundary")
    current = plot.year(year)
    if (current.crop if current else None) != e["crop"]:
        changes.append("crop")
    return changes


def plan_import(entries, year):
    """Vorschau: [{entry, plot, changes}] und Schläge mit Umriss, die nicht mehr in der Datei sind."""
    matches = _match(entries)
    rows = [{"entry": e, "plot": p, "changes": _changes(p, e, year)} for e, p in zip(entries, matches)]
    seen = {p.id for p in matches if p}
    missing = [p for p in Plot.query.filter(Plot.boundary.isnot(None)).order_by(Plot.name) if p.id not in seen]
    return rows, missing


def _set_year(plot, year, crop, eppo, overwrite):
    entry = plot.year(year)
    if entry is None:
        entry = PlotYear(year=year)
        plot.years.append(entry)
    if overwrite or not entry.crop:
        entry.crop, entry.crop_eppo = crop, eppo
    return entry


def apply_import(entries, year):
    """Übernimmt die Schläge. Pflanzung/Rodung/Sorte bleiben unangetastet. Liefert (neu, geändert)."""
    rows, _missing = plan_import(entries, year)
    taken = {p.name.casefold() for p in Plot.query.all()}
    created = updated = 0
    for row in rows:
        e, plot = row["entry"], row["plot"]
        if plot is None:
            name = e["name"]
            if name.casefold() in taken:
                name = _numbered_name(e)
            if name.casefold() in taken:
                raise FieldImportError(_("Den Schlag „{name}“ gibt es schon.", name=name))
            plot = Plot(name=name)
            db.session.add(plot)
            created += 1
        else:
            updated += 1 if row["changes"] else 0
            # In der Schlagkartei umbenannt → mitziehen, sofern der neue Name frei ist
            if not _same_name(plot, e) and e["name"].casefold() not in (taken - {plot.name.casefold()}):
                taken.discard(plot.name.casefold())
                plot.name = e["name"]
        taken.add(plot.name.casefold())
        plot.number, plot.area_ha, plot.soil, plot.red_area = e["number"], e["area_ha"], e["soil"], e["red_area"]
        plot.boundary = json.dumps(e["rings"], separators=(",", ":"))
        plot.active = True
        _set_year(plot, year, e["crop"], e["crop_eppo"], overwrite=True)
        if e["prev_crop"]:
            _set_year(plot, year - 1, e["prev_crop"], e["prev_eppo"], overwrite=False)
        db.session.flush()
    return created, updated


# --- Anzeige -------------------------------------------------------------------

def crop_color(crop, eppo):
    if not crop:
        return NO_CROP_COLOR
    if eppo and eppo in CROP_COLORS:
        return CROP_COLORS[eppo]
    if "kartoffel" in crop.lower():
        return CROP_COLORS["SOLTU"]
    return FALLBACK_COLORS[sum(map(ord, crop)) % len(FALLBACK_COLORS)]


def yields(year):
    """{plot_id: {"kg": geschätzte Ernte, "boxes": n, "varieties": {Sorte: kg}}} aus den Kisten (Momentaufnahmen)."""
    from .transfer import season_summary
    result = {}
    for (y, variety, _usage, plot), g in season_summary(year).items():
        if y != year or not plot:
            continue
        entry = result.setdefault(plot.id, {"kg": 0, "boxes": 0, "varieties": {}})
        entry["kg"] += g["harvest_kg"]
        entry["boxes"] += g["boxes"]
        entry["varieties"][variety.name] = entry["varieties"].get(variety.name, 0) + g["harvest_kg"]
    return result


def t_per_ha(kg, area_ha):
    if not kg or not area_ha:
        return None
    return round(kg / 1000 / float(area_ha), 1)


def map_data(plots, year):
    """JSON für die Schlagkarte (Projektion übernimmt das JavaScript)."""
    items = []
    for p in plots:
        if not p.rings:
            continue
        y = p.year(year)
        crop = y.crop if y else None
        items.append({
            "id": p.id, "name": p.name, "number": p.number or "",
            "ha": float(p.area_ha) if p.area_ha is not None else None,
            "crop": crop or "", "color": crop_color(crop, y.crop_eppo if y else None), "rings": p.rings,
        })
    return items


def crop_legend(plots, year):
    legend = {}
    for p in plots:
        y = p.year(year)
        crop = y.crop if y else None
        entry = legend.setdefault(crop or "", {"crop": crop, "color": crop_color(crop, y.crop_eppo if y else None),
                                                "ha": Decimal(0), "count": 0})
        entry["ha"] += Decimal(p.area_ha or 0)
        entry["count"] += 1
    return sorted(legend.values(), key=lambda e: -e["ha"])


def plot_years():
    return sorted({y for (y,) in db.session.query(PlotYear.year).distinct()}, reverse=True)
