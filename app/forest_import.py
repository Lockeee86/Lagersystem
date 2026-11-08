"""Bestattungswald: Übernahme aus dem bisherigen Friedhofs-/Waldprogramm (Tabellenexport).

Zwei Tabellen werden erkannt (Excel-XML „Tabellenexport“ oder .xlsx):
- **Baumposition** (je Grabplatz eine Zeile: Ruhebaumnummer „A 147“ bzw. „Waldruhefläche A“, Nummer = Platz,
  Ruhebaum „A 147 (Gemeinschaftsbaum/Stieleiche)“, Vertrag, Vertragspartner, Person, Bestattung …)
  → Bäume mit Plätzen, Verträge (Reservierungen), Beisetzungen, vorgesehene Personen.
- **Ruhebäume** (je Baum: Nummer, Typ, Art, Stammdurchmesser, Kategorie) → Stammdaten der Bäume.
- **Grundkarte** aus CAD (DXF, auch ohne Dateiendung im ZIP): Polylinien je Ebene („wassergebundene Wege“,
  „Parkplatz“, „Andachtsplatz“ …) → `ForestShape` für die Karte, Art am Ebenen-Namen (`shape_kind`).
- **Shapefile der Baumpunkte** als ZIP (.shp + .dbf, Feld `nummer`, Lage aus `geokoordin` „N: … O: …“ oder aus dem
  Punkt in UTM, Zone aus der .prj) → Lage der Bäume für die Karte.

`parse_file()` → `apply()`; `apply()` committet nicht (Vorschau = Rollback). Vorhandenes bleibt: Plätze, die in der
App schon einen Vertrag oder eine Beisetzung haben, werden nicht überschrieben – mehrfaches Einlesen ist harmlos.
Die Dateien enthalten Personendaten: nie ins Repository, Tests nur mit erfundenen Daten.
"""
import io
import math
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import date

from . import forest
from .extensions import db
from .i18n import gettext as _
from .models import ForestBurial, ForestContract, ForestPlace, ForestShape, ForestTree, Setting
from .storage import StorageError

NS = "{urn:schemas-microsoft-com:office:spreadsheet}"
SALUTATIONS = {"frau", "herrn", "herr", "familie", "eheleute", "firma", "fam.", "frau und herrn", "herrn und frau"}
TREE_RE = re.compile(r"^([A-Za-z]{1,3})\s*-?\s*(\d{1,5})$")
AREA_RE = re.compile(r"^Waldruhefl(?:ä|ae)che\s*([A-Za-z]{0,3})$", re.I)


# --- Datei lesen ----------------------------------------------------------------------------------

def _rows_spreadsheetml(data):
    head = data.upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise StorageError(_("Die Datei konnte nicht gelesen werden."))
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise StorageError(_("Die Datei konnte nicht gelesen werden."))
    tables = []
    for sheet in root.iter(f"{NS}Worksheet"):
        rows = []
        for r in sheet.iter(f"{NS}Row"):
            row = []
            for cell in r.findall(f"{NS}Cell"):
                index = cell.get(f"{NS}Index")
                if index:
                    row.extend([None] * (int(index) - 1 - len(row)))
                value = cell.find(f"{NS}Data")
                row.append(value.text if value is not None else None)
            rows.append(row)
        tables.append(rows)
    return tables


def _rows_xlsx(data):
    import openpyxl
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:                                      # noqa: BLE001 – kaputte Datei
        raise StorageError(_("Die Datei konnte nicht gelesen werden."))
    tables = []
    for sheet in book.worksheets:
        tables.append([[c.isoformat() if hasattr(c, "isoformat") else (None if c is None else str(c)) for c in row]
                       for row in sheet.iter_rows(values_only=True)])
    return tables


def utm_to_latlon(easting, northing, zone=32):
    """UTM (Nordhalbkugel, GRS80/WGS84) → (Breite, Länge) in Grad."""
    a, f, k0 = 6378137.0, 1 / 298.257222101, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x, y = easting - 500000.0, northing
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32)
            * math.sin(4 * mu) + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    n1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    t1, c1 = math.tan(phi1) ** 2, ep2 * math.cos(phi1) ** 2
    r1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
                                              + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2)
                                              * d ** 6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d ** 3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2)
           * d ** 5 / 120) / math.cos(phi1)
    return math.degrees(lat), (zone - 1) * 6 - 180 + 3 + math.degrees(lon)


def _dbf_records(data):
    count, header_len, record_len = struct.unpack("<IHH", data[4:12])
    fields, i = [], 32
    while i < header_len - 1 and data[i] != 0x0D:
        fields.append((data[i:i + 11].split(b"\0")[0].decode("latin-1").lower(), data[i + 16]))
        i += 32
    records = []
    for n in range(count):
        raw = data[header_len + n * record_len: header_len + (n + 1) * record_len]
        if not raw or raw[:1] == b"*":                     # gelöscht
            records.append(None)
            continue
        values, pos = {}, 1
        for name, length in fields:
            values[name] = raw[pos:pos + length].decode("cp1252", errors="replace").strip()
            pos += length
        records.append(values)
    return records


def _shp_records(data):
    """Shapefile → je Datensatz ("point", (x, y)) bzw. ("polygon", [[(x, y), …], …]) oder None."""
    if len(data) < 100 or struct.unpack(">i", data[:4])[0] != 9994:
        raise StorageError(_("Die Datei konnte nicht gelesen werden."))
    records, pos = [], 100
    while pos + 8 <= len(data):
        length = struct.unpack(">i", data[pos + 4:pos + 8])[0] * 2
        content = data[pos + 8:pos + 8 + length]
        shape = struct.unpack("<i", content[:4])[0] if len(content) >= 4 else 0
        if shape in (1, 11, 21) and len(content) >= 20:
            records.append(("point", struct.unpack("<2d", content[4:20])))
        elif shape in (5, 15, 25) and len(content) >= 44:
            parts, count = struct.unpack("<2i", content[36:44])
            starts = list(struct.unpack(f"<{parts}i", content[44:44 + 4 * parts])) + [count]
            base = 44 + 4 * parts
            coords = [struct.unpack("<2d", content[base + 16 * i:base + 16 * i + 16]) for i in range(count)]
            records.append(("polygon", [coords[starts[i]:starts[i + 1]] for i in range(parts)]))
        else:
            records.append(None)
        pos += 8 + length
    return records


def _ring_area(ring):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))) / 2


def parse_shapefile(data):
    """ZIP mit Shapefiles aus dem GIS → [{"key": (Feld, Nummer, Art), "lat", "lon", "outline"}]. Punkte = Bäume,
    Polygone = Flächen (Waldruhefläche): `outline` ist der größte Ring als [[Länge, Breite], …], Lage = Mitte."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = {n.lower(): n for n in archive.namelist() if not n.startswith("__MACOSX")}
        stems = sorted(n[:-4] for n in names if n.endswith(".dbf"))
        if not stems:
            raise StopIteration
        layers = []
        for stem in stems:
            records = _dbf_records(archive.read(names[stem + ".dbf"]))
            shapes = _shp_records(archive.read(names[stem + ".shp"])) if stem + ".shp" in names else []
            prj = archive.read(names[stem + ".prj"]).decode("latin-1") if stem + ".prj" in names else ""
            layers.append((records, shapes, prj))
    except (zipfile.BadZipFile, StopIteration, KeyError, struct.error):
        raise StorageError(_("Im ZIP wurde kein Shapefile mit Baumpunkten gefunden."))
    result = []
    for records, shapes, prj in layers:
        m = re.search(r"UTM_Zone_(\d+)", prj)
        zone = int(m.group(1)) if m else 32

        def latlon(xy):
            return utm_to_latlon(*xy, zone=zone) if abs(xy[0]) > 180 else (xy[1], xy[0])

        for i, rec in enumerate(records):
            if not rec:
                continue
            key = tree_key(rec.get("nummer") or rec.get("nrform"))
            shape = shapes[i] if i < len(shapes) else None
            item = {"key": key, "outline": None}
            if shape and shape[0] == "polygon":
                ring = max(shape[1], key=_ring_area)
                outline = []
                for xy in ring:
                    lat, lon = latlon(xy)
                    outline.append([round(lon, 7), round(lat, 7)])
                if len(outline) > 1 and outline[0] == outline[-1]:
                    outline.pop()
                if len(outline) < 3:
                    continue
                item["outline"] = outline
                item["lon"] = round(sum(p[0] for p in outline) / len(outline), 7)
                item["lat"] = round(sum(p[1] for p in outline) / len(outline), 7)
            else:
                m = re.search(r"N:\s*(-?[\d.]+)\s*O:\s*(-?[\d.]+)", rec.get("geokoordin", ""))
                if m:
                    lat, lon = float(m.group(1)), float(m.group(2))
                elif shape and shape[0] == "point":
                    lat, lon = latlon(shape[1])
                else:
                    continue
                item["lat"], item["lon"] = round(lat, 7), round(lon, 7)
            result.append(item)
    # dieselbe Ebene mehrfach im ZIP (z. B. mehrere Exporte): je Baum nur einmal, Umriss vor Punkt
    unique, rest = {}, []
    for item in result:
        if not item["key"]:
            rest.append(item)
        elif item["key"] not in unique or item["outline"] or not unique[item["key"]]["outline"]:
            unique[item["key"]] = item
    return list(unique.values()) + rest


def apply_points(points):
    """Lage der Bäume setzen, Flächen bekommen ihren Umriss (eine fehlende Waldruhefläche wird angelegt).
    Liefert {"located": …, "areas": …, "unknown": …}; committet nicht."""
    import json
    stats = {"located": 0, "areas": 0, "unknown": 0}
    for point in points:
        key = point["key"]
        tree = ForestTree.query.filter_by(field=key[0], number=key[1]).first() if key else None
        if tree is None and key and key[2] == "flaeche" and point.get("outline"):
            tree = ForestTree(field=key[0], number=0, kind="flaeche",
                              section=_("Feld {field}", field=key[0]) if key[0] else None)
            db.session.add(tree)
        if tree is None:
            stats["unknown"] += 1
            continue
        tree.lat, tree.lon = point["lat"], point["lon"]
        if point.get("outline"):
            tree.outline = json.dumps(point["outline"])
            stats["areas"] += 1
        else:
            stats["located"] += 1
    db.session.flush()
    return stats


# --- Grundkarte aus CAD (DXF) ------------------------------------------------------------------------

SHAPE_WORDS = (("parkplatz", "parkplatz"), ("kies", "kies"), ("weg", "weg"), ("straße", "weg"), ("strasse", "weg"),
               ("rasen", "rasen"), ("wiese", "rasen"), ("biotop", "wasser"), ("teich", "wasser"),
               ("wasser", "wasser"), ("andacht", "andacht"), ("aussicht", "aussicht"))


def shape_kind(layer):
    layer = (layer or "").lower()
    return next((kind for word, kind in SHAPE_WORDS if word in layer), "sonstiges")


def is_dxf(data):
    head = data[:400].lstrip().replace(b"\r", b"")
    return head.startswith(b"0\nSECTION") or head.startswith(b"999\n")


def _utm_zone():
    """UTM-Zone für Zeichnungen ohne Angabe: aus dem Hof-Standort, sonst 32 (West- und Mitteldeutschland)."""
    m = re.match(r"^\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)", Setting.all().get("farm_location") or "")
    return int((float(m.group(2)) + 180) // 6) + 1 if m else 32


def parse_dxf(data, zone=None):
    """DXF → [{"kind", "layer", "points": [[Länge, Breite], …], "line"}]. Liest LWPOLYLINE, POLYLINE und LINE."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    lines = text.replace("\r", "").split("\n")
    pairs = [(lines[i].strip(), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]
    zone = zone or _utm_zone()
    shapes, cur, section, i = [], None, None, 0

    def finish(entity):
        if entity and len(entity["xy"]) >= 2:
            pts = []
            for x, y in entity["xy"]:
                lat, lon = utm_to_latlon(x, y, zone) if abs(x) > 180 else (y, x)
                pts.append([round(lon, 7), round(lat, 7)])
            closed = entity["closed"] and len(pts) >= 3
            shapes.append({"kind": shape_kind(entity["layer"]), "layer": entity["layer"][:100] or None,
                           "points": pts, "line": not closed})

    while i < len(pairs):
        code, value = pairs[i]
        if code == "0" and value == "SECTION" and i + 1 < len(pairs):
            section = pairs[i + 1][1]
            i += 2
            continue
        if section != "ENTITIES":
            i += 1
            continue
        if code == "0":
            if value == "VERTEX" and cur and cur["type"] == "POLYLINE":
                cur["vertex"] = True                       # Punkte folgen in VERTEX-Einträgen
                i += 1
                continue
            if value == "SEQEND" or cur is None or cur["type"] != "POLYLINE" or value != "VERTEX":
                finish(cur)
                cur = None
            if value in ("LWPOLYLINE", "POLYLINE", "LINE"):
                cur = {"type": value, "layer": "", "xy": [], "closed": False, "vertex": False}
            i += 1
            continue
        if cur is not None:
            if code == "8" and not cur["vertex"]:
                cur["layer"] = value
            elif code == "70" and not cur["vertex"]:
                cur["closed"] = bool(int(value or 0) & 1)
            elif code in ("10", "11") and (cur["type"] != "POLYLINE" or cur["vertex"]):
                try:
                    cur["xy"].append([float(value), None])
                except ValueError:
                    pass
            elif code in ("20", "21") and cur["xy"] and cur["xy"][-1][1] is None:
                cur["xy"][-1][1] = float(value)
        i += 1
    finish(cur)
    for shape in shapes:
        shape["points"] = [p for p in shape["points"] if p[1] is not None]
    return [s for s in shapes if len(s["points"]) >= 2]


def apply_shapes(shapes, source=None):
    """Grundkarte übernehmen: Ebenen, die in der Datei vorkommen, werden ersetzt. Committet nicht."""
    import json
    layers = {s["layer"] for s in shapes}
    for old in ForestShape.query.filter(ForestShape.layer.in_([layer for layer in layers if layer])):
        db.session.delete(old)
    if None in layers:
        ForestShape.query.filter(ForestShape.layer.is_(None)).delete()
    for s in shapes:
        db.session.add(ForestShape(kind=s["kind"], layer=s["layer"], points=json.dumps(s["points"]), line=s["line"],
                                   source=(source or "")[:150] or None))
    db.session.flush()
    return {"shapes": len(shapes), "layers": len(layers)}


def read_upload(data, name=None):
    """Hochgeladene Datei → {"tables": wie `parse_file` oder None, "points": wie `parse_shapefile` oder None,
    "shapes": wie `parse_dxf` oder None}. Ein ZIP darf Tabellen (.xml/.xlsx), ein Shapefile und Zeichnungen (DXF,
    auch ohne Endung) zugleich enthalten."""
    data = data or b""
    result = {"tables": None, "points": None, "shapes": None}
    if is_dxf(data):
        result["shapes"] = parse_dxf(data)
        return result
    archive = None
    if data[:2] == b"PK":
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise StorageError(_("Die Datei konnte nicht gelesen werden."))
        if any(n.lower().startswith("xl/") for n in archive.namelist()):       # selbst eine .xlsx
            archive = None
    if archive is None:
        result["tables"] = parse_file(data)
        return result
    names = [n for n in archive.namelist() if not n.startswith("__MACOSX") and not n.endswith("/")]
    if sum(archive.getinfo(n).file_size for n in names) > 200 * 1024 * 1024:
        raise StorageError(_("Die Datei konnte nicht gelesen werden."))
    parsed, shapes = {"positions": [], "trees": []}, []
    for name in names:
        if name.lower().endswith((".xml", ".xlsx")):
            try:
                part = parse_file(archive.read(name))
            except StorageError:                          # andere Tabelle im ZIP – überspringen
                continue
            parsed["positions"] += part["positions"]
            parsed["trees"] += part["trees"]
        elif not name.lower().endswith((".shp", ".shx", ".dbf", ".prj", ".cpg")):
            content = archive.read(name)
            if is_dxf(content):
                shapes += parse_dxf(content)
    if any(n.lower().endswith(".dbf") for n in names):
        result["points"] = parse_shapefile(data)
    if parsed["positions"] or parsed["trees"]:
        result["tables"] = parsed
    if shapes:
        result["shapes"] = shapes
    if not any(result.values()):
        raise StorageError(_("Im ZIP wurden weder Tabellen noch Baumpunkte oder Zeichnungen gefunden."))
    return result


def parse_file(data):
    """→ {"positions": [...], "trees": [...]} aus einer oder beiden Tabellen."""
    data = data or b""
    head = data[:600].lstrip()
    tables = _rows_xlsx(data) if data[:2] == b"PK" else _rows_spreadsheetml(data) if head.startswith(b"<") else None
    if tables is None:
        raise StorageError(_("Bitte den Tabellenexport als Excel-XML oder .xlsx hochladen."))
    result = {"positions": [], "trees": []}
    for rows in tables:
        if not rows:
            continue
        header = [(h or "").strip() for h in rows[0]]
        records = [dict(zip(header, r)) for r in rows[1:] if any(v not in (None, "") for v in r)]
        if "Ruhebaumnummer" in header and "Nummer" in header:
            result["positions"] += records
        elif "Nummer" in header and ("Typ" in header or "Stammdurchmesser" in header):
            result["trees"] += records
    if not result["positions"] and not result["trees"]:
        raise StorageError(_("In der Datei wurde weder die Tabelle „Baumposition“ noch „Ruhebäume“ gefunden."))
    return result


# --- Werte ------------------------------------------------------------------------------------------

def _text(value):
    return (value or "").strip() if isinstance(value, str) else ("" if value is None else str(value).strip())


def _date(value):
    value = _text(value)
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})", value)
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _int(value):
    value = _text(value).replace(",", ".")
    try:
        return int(float(value)) if value else None
    except ValueError:
        return None


def tree_key(raw):
    """„A 147“ → ("A", 147, None); „Waldruhefläche B“ → ("B", 0, "flaeche"); sonst None."""
    raw = _text(raw)
    m = TREE_RE.match(raw)
    if m:
        return m.group(1).upper(), int(m.group(2)), None
    m = AREA_RE.match(raw)
    if m:
        return m.group(1).upper(), 0, "flaeche"
    return None


def kind_species(raw):
    """„A 9 (Familien/Freundesbaum/Stieleiche)“ → ("familie", "Stieleiche")."""
    m = re.search(r"\((.*)\)", _text(raw))
    if not m:
        return None, None
    parts = [p.strip() for p in m.group(1).split("/")]
    if len(parts) == 1:
        return forest._kind_from_text(parts[0]) if parts[0] else None, None
    kind_text, species = "/".join(parts[:-1]), parts[-1]
    if kind_text.lower().startswith("waldruh"):
        species = None
    return (forest._kind_from_text(kind_text) if kind_text else None), (species or None)


def holder(raw):
    """Mehrzeiliges Feld „Frau\\nName\\nStraße\\nPLZ Ort“ → (Name, Anschrift)."""
    lines = [line.strip() for line in _text(raw).replace("\r", "").split("\n") if line.strip()]
    if lines and lines[0].lower() in SALUTATIONS and len(lines) > 1:
        lines = lines[1:]
    return (lines[0][:150] if lines else ""), ("\n".join(lines[1:]) or None)


# --- Übernehmen ---------------------------------------------------------------------------------------

def _tree(cache, field, number, kind, species, stats):
    key = (field, number)
    if key in cache:
        return cache[key]
    tree = ForestTree.query.filter_by(field=field, number=number).first()
    if tree is None:
        tree = ForestTree(field=field, number=number, kind=kind or "gemeinschaft", species=species,
                          section=_("Feld {field}", field=field) if field else None)
        db.session.add(tree)
        stats["trees_new"] += 1
    else:
        if kind and tree.kind != kind:
            tree.kind = kind
        if species and not tree.species:
            tree.species = species
        stats["trees_known"] += 1
    cache[key] = tree
    return tree


def _ensure_places(tree, count, stats):
    have = {p.number for p in tree.places}
    for n in range(1, min(count, forest.MAX_PLACES) + 1):
        if n not in have:
            tree.places.append(ForestPlace(number=n))
            stats["places_new"] += 1


def apply(parsed, user, with_people=True, today=None):
    """Bäume, Plätze, Verträge, Beisetzungen übernehmen. Liefert Zahlen für die Rückmeldung; committet nicht."""
    today = today or date.today()
    stats = defaultdict(int)
    unknown = _("unbekannt")
    cache = {}

    # 1. Bäume und Plätze aus den Positionen
    per_tree = defaultdict(list)
    for row in parsed.get("positions", []):
        key = tree_key(row.get("Ruhebaumnummer"))
        place_no = _int(row.get("Nummer"))
        if key is None or not place_no:
            stats["skipped_rows"] += 1
            continue
        per_tree[key].append((place_no, row))
    for (field, number, kind_hint), rows in per_tree.items():
        kind, species = kind_species(rows[0][1].get("Ruhebaum"))
        tree = _tree(cache, field, number, kind_hint or kind, species, stats)
        _ensure_places(tree, max(n for n, _row in rows), stats)
    db.session.flush()

    # 2. Stammdaten aus der Tabelle „Ruhebäume“
    for row in parsed.get("trees", []):
        found = tree_key(row.get("Nummer"))
        if found is None:
            stats["skipped_trees"] += 1
            continue
        field, number, kind_hint = found
        kind = kind_hint or (forest._kind_from_text(row.get("Typ")) if _text(row.get("Typ")) else None)
        species = _text(row.get("Art")) or None
        tree = _tree(cache, field, number, kind, species, stats)
        if species and kind != "flaeche":
            tree.species = species[:60]
        if _text(row.get("Kategorie")):
            tree.category = _text(row.get("Kategorie"))[:10]
        trunk = _int(row.get("Stammdurchmesser"))
        if trunk:
            tree.trunk_cm = trunk
        if not tree.places:
            _ensure_places(tree, forest.DEFAULT_PLACES, stats)
    db.session.flush()

    # 3. Verträge (gleicher Vertragspartner + Datum = ein Vertrag), Beisetzungen, vorgesehene Personen
    places = {}
    for (field, number, _k), rows in per_tree.items():
        tree = cache[(field, number)]
        by_no = {p.number: p for p in tree.places}
        for place_no, row in rows:
            places[id(row)] = by_no.get(place_no)
    contracts = defaultdict(list)
    for rows in per_tree.values():
        for _n, row in rows:
            if _date(row.get("Vertragsdatum")) or _date(row.get("Vertragsbeginn")):
                key = (_text(row.get("Name Vertragspartner")), _date(row.get("Vertragsdatum")),
                       _date(row.get("Vertragsende")))
                contracts[key].append(row)
    for (raw_holder, signed, ends), rows in contracts.items():
        targets = [places[id(r)] for r in rows if places.get(id(r)) is not None]
        free = [p for p in targets if p.contract_id is None and not p.burials]
        stats["skipped_places"] += len(targets) - len(free)
        if not free:
            continue
        name, address = holder(raw_holder)
        first = rows[0]
        signed = signed or _date(first.get("Vertragsbeginn"))
        contract = ForestContract(number=forest.next_contract_number(signed), signed_on=signed, ends_on=ends,
                                  holder=(name if with_people and name else unknown),
                                  address=address if with_people else None,
                                  holder_birthday=_date(first.get("Vertragspartner Geburtsdatum")) if with_people else None,
                                  note=_("aus dem Altsystem übernommen"), created_by_id=user.id if user else None)
        db.session.add(contract)
        for place in free:
            place.contract = contract
        db.session.flush()
        stats["contracts"] += 1
    for rows in per_tree.values():
        for _n, row in rows:
            place = places.get(id(row))
            if place is None:
                continue
            person = _text(row.get("Personenname"))[:150]
            day = _date(row.get("Bestattungsdatum"))
            if day is None:
                if person and with_people and not place.person and not place.burials:
                    place.person = person
                    stats["persons"] += 1
                continue
            if place.burials:
                stats["skipped_burials"] += 1
                continue
            burial = ForestBurial(place=place, contract=place.contract, day=day,
                                  deceased=person if with_people and person else unknown,
                                  died_on=_date(row.get("Sterbedatum")) if with_people else None,
                                  died_in=_text(row.get("Sterbeort"))[:100] or None if with_people else None,
                                  number=_text(row.get("Bestattungsnummer"))[:20] or None,
                                  created_by_id=user.id if user else None)
            db.session.add(burial)
            if day >= today:                          # noch bevorstehend → Aufgaben fürs Grab
                burial.grave_person, burial.grave_deputy = forest.grave_team()
                db.session.flush()
                forest._burial_tasks(burial, user)
            stats["burials"] += 1
    db.session.flush()
    return dict(stats)
