"""Luftbild unter der Schlagkarte: der Server holt das Bild für den sichtbaren Ausschnitt und hält es vor.

Warum über den Server? Die CSP erlaubt nur Bilder von der eigenen Adresse, und so wird jeder Ausschnitt nur einmal
geladen. Quellen (Einstellung `aerial_source`):
  lgln – Niedersachsen, amtliche Digitale Orthophotos (DOP20) des LGLN, Open Data
  esri – Esri World Imagery (weltweit, Nutzungsbedingungen von Esri beachten)
  wms  – eigener WMS-Dienst (z. B. eines anderen Bundeslands), URL und Layer in den Einstellungen
Die Karte rechnet in Länge/Breite. Welche Koordinatensysteme ein WMS kann, steht in seinen GetCapabilities (werden
einmal gelesen und vorgehalten): EPSG:4326/4258/CRS:84 passen ohne Umrechnung; kann der Dienst nur projizierte
Systeme (z. B. Niedersachsen: EPSG:25832 = UTM 32N, oder EPSG:3857), holt der Server den umschließenden Ausschnitt
und entzerrt ihn (Pillow, Viereck-Abbildung – im Kartenausschnitt genauer als ein Pixel).
"""
import hashlib
import io
import math
import os
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from flask import current_app

from .i18n import N_
from .models import Setting

SOURCES = {
    "lgln": {"label": N_("Niedersachsen – amtliche Luftbilder (LGLN, frei nutzbar)"), "kind": "wms",
             "url": "https://opendata.lgln.niedersachsen.de/doorman/noauth/dop_wms", "layer": "ni_dop20",
             "credit": "© GeoBasis-DE/LGLN"},
    "esri": {"label": N_("Weltweit – Esri World Imagery"), "kind": "arcgis",
             "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export",
             "credit": "Esri, Maxar, Earthstar Geographics"},
    "wms": {"label": N_("Eigener WMS-Dienst"), "kind": "wms", "credit": ""},
}
MAX_SIDE = 2048
TIMEOUT = 20
CACHE_LIMIT = 500 * 1024 * 1024          # ab 500 MB werden die ältesten Ausschnitte gelöscht


CAPS_SECONDS = 24 * 3600
CAPS_ERROR_SECONDS = 5 * 60
# Reihenfolge = Vorzug. "geo": Länge/Breite (Achsenreihenfolge bei WMS 1.3.0), sonst projiziert über `_project`.
CRS_CHOICES = [("EPSG:4326", "latlon"), ("EPSG:4258", "latlon"), ("CRS:84", "lonlat"), ("EPSG:3857", "proj"),
               ("EPSG:25832", "proj"), ("EPSG:25833", "proj"), ("EPSG:900913", "proj")]
_caps = {}
_caps_lock = threading.Lock()


class AerialError(Exception):
    pass


# --- Projektionen (nur vorwärts: Länge/Breite → x/y in Metern) ------------------------------

def _mercator(lon, lat):
    r = 6378137.0
    return r * math.radians(lon), r * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def _utm(lon, lat, zone):
    """UTM auf GRS80 (ETRS89), Reihenentwicklung nach Snyder – im Zonenbereich auf wenige mm genau."""
    a, f, k0 = 6378137.0, 1 / 298.257222101, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    phi, lam = math.radians(lat), math.radians(lon)
    lam0 = math.radians(-183 + 6 * zone)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = ep2 * math.cos(phi) ** 2
    big_a = math.cos(phi) * (lam - lam0)
    m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
             - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))
    x = k0 * n * (big_a + (1 - t + c) * big_a ** 3 / 6
                  + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * big_a ** 5 / 120) + 500000
    y = k0 * (m + n * math.tan(phi) * (big_a ** 2 / 2 + (5 - t + 9 * c + 4 * c ** 2) * big_a ** 4 / 24
                                       + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2) * big_a ** 6 / 720))
    return x, y


def _project(crs, lon, lat):
    if crs in ("EPSG:3857", "EPSG:900913"):
        return _mercator(lon, lat)
    if crs in ("EPSG:25832", "EPSG:25833"):
        return _utm(lon, lat, int(crs[-2:]))
    raise AerialError(crs)


# --- GetCapabilities ----------------------------------------------------------------------

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _parse_capabilities(data, layer):
    """{"crs": [...], "formats": [...], "found": bool} für den Layer (CRS werden von übergeordneten Layern geerbt)."""
    if b"<!DOCTYPE" in data[:2000].upper() or b"<!ENTITY" in data.upper():
        raise AerialError("ungültige Capabilities")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise AerialError("Capabilities nicht lesbar")
    formats = []
    for el in root.iter():
        if _local(el.tag) == "GetMap":
            formats = [f.text.strip() for f in el if _local(f.tag) == "Format" and f.text]
            break

    def walk(node, inherited):
        own = [c.text.strip() for c in node if _local(c.tag) in ("CRS", "SRS") and c.text]
        crs = inherited + [c for v in own for c in v.split()]
        name = next((c.text.strip() for c in node if _local(c.tag) == "Name" and c.text), None)
        if name == layer:
            return crs
        for child in node:
            if _local(child.tag) == "Layer":
                found = walk(child, crs)
                if found is not None:
                    return found
        return None
    capability = next((el for el in root.iter() if _local(el.tag) == "Capability"), root)
    crs = None
    for top in capability:
        if _local(top.tag) == "Layer":
            crs = walk(top, [])
            if crs is not None:
                break
    return {"crs": [c.upper() for c in (crs or [])], "formats": formats, "found": crs is not None}


def capabilities(source):
    """Fähigkeiten des WMS (vorgehalten). Nicht erreichbar → None (dann wird EPSG:4326/JPEG versucht)."""
    key = (source["url"], source.get("layer"))
    with _caps_lock:
        cached = _caps.get(key)
    if cached and time.monotonic() - cached[0] < cached[1]:
        return cached[2]
    sep = "&" if "?" in source["url"] else "?"
    url = source["url"] + sep + urllib.parse.urlencode({"SERVICE": "WMS", "REQUEST": "GetCapabilities",
                                                         "VERSION": "1.3.0"})
    try:
        data, _type = _download(url)
        caps, ttl = _parse_capabilities(data, source.get("layer")), CAPS_SECONDS
    except (OSError, AerialError) as e:
        current_app.logger.warning("WMS-Capabilities nicht lesbar (%s): %s", source["url"], e)
        caps, ttl = None, CAPS_ERROR_SECONDS
    with _caps_lock:
        _caps[key] = (time.monotonic(), ttl, caps)
    return caps


def clear_capabilities():
    with _caps_lock:
        _caps.clear()


def diagnose():
    """Klartext für die Verwaltung: was der Dienst kann und wie die App ihn abfragt (bewusst Deutsch, technisch)."""
    source = config()
    if source is None:
        return "Luftbild ist aus oder die WMS-Adresse ist unvollständig."
    lines = [f"Quelle: {source['key']}", f"Adresse: {source['url']}"]
    if source["kind"] != "wms":
        return "\n".join(lines + ["Art: ArcGIS-Export (EPSG:4326), keine Capabilities nötig."])
    clear_capabilities()
    caps = capabilities(source)
    lines.append(f"Layer: {source.get('layer')}")
    if caps is None:
        lines.append("GetCapabilities: nicht erreichbar oder nicht lesbar – es wird EPSG:4326 versucht.")
    else:
        lines += [f"Layer gefunden: {'ja' if caps['found'] else 'NEIN'}",
                  "Koordinatensysteme: " + (", ".join(caps["crs"]) or "–"),
                  "Bildformate: " + (", ".join(caps["formats"]) or "–")]
    try:
        crs, axes, fmt = request_plan(source)
        lines.append(f"Abfrage mit: {crs} ({'wird entzerrt' if axes == 'proj' else 'ohne Umrechnung'}), {fmt}")
    except AerialError as e:
        lines.append(f"Problem: {e}")
    return "\n".join(lines)


def request_plan(source):
    """Koordinatensystem und Bildformat für GetMap: (crs, art, format)."""
    if source["kind"] != "wms":
        return None, None, None
    caps = capabilities(source)
    if caps is not None and not caps["found"]:
        raise AerialError(f"Layer „{source.get('layer')}“ gibt es in diesem Dienst nicht.")
    if caps is None or not caps["crs"]:
        return "EPSG:4326", "latlon", "image/jpeg"
    choice = next(((c, kind) for c, kind in CRS_CHOICES if c in caps["crs"]), None)
    if choice is None:
        raise AerialError("Der Dienst bietet kein passendes Koordinatensystem an (nur "
                          + ", ".join(caps["crs"][:8]) + ").")
    fmt = next((f for f in ("image/jpeg", "image/png") if f in caps["formats"]), None) \
        or (caps["formats"][0] if caps["formats"] else "image/jpeg")
    return choice[0], choice[1], fmt


def config():
    """Aktive Quelle mit URL/Layer – oder None (Luftbild aus)."""
    values = Setting.all()
    key = values.get("aerial_source") or ""
    if key not in SOURCES:
        return None
    source = dict(SOURCES[key], key=key)
    if key == "wms":
        source["url"] = (values.get("aerial_wms_url") or "").strip()
        source["layer"] = (values.get("aerial_wms_layer") or "").strip()
        if not source["url"].startswith(("https://", "http://")) or not source["layer"]:
            return None
    return source


def cache_dir():
    path = current_app.config.get("AERIAL_CACHE_DIR") or os.path.join(
        os.path.dirname(current_app.config["UPLOAD_DIR"].rstrip("/")), "luftbild-cache")
    os.makedirs(path, exist_ok=True)
    return path


def parse_bbox(text):
    """„lon1,lat1,lon2,lat2“ → Tupel (geprüft) oder None."""
    try:
        lon1, lat1, lon2, lat2 = (float(v) for v in (text or "").split(","))
    except ValueError:
        return None
    if not (-180 <= lon1 < lon2 <= 180 and -85 <= lat1 < lat2 <= 85) or lon2 - lon1 > 2 or lat2 - lat1 > 2:
        return None
    return lon1, lat1, lon2, lat2


def _url(source, bbox, width, height, crs="EPSG:4326", axes="latlon", fmt="image/jpeg"):
    lon1, lat1, lon2, lat2 = bbox
    if source["kind"] == "arcgis":
        params = {"bbox": f"{lon1},{lat1},{lon2},{lat2}", "bboxSR": "4326", "imageSR": "4326",
                  "size": f"{width},{height}", "format": "jpg", "f": "image"}
    else:
        if axes == "latlon":
            box = f"{lat1},{lon1},{lat2},{lon2}"     # WMS 1.3.0 mit EPSG:4326/4258: Breite, Länge
        else:
            box = f"{lon1},{lat1},{lon2},{lat2}"     # CRS:84 und projizierte Systeme: x, y
        params = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": source["layer"],
                  "STYLES": "", "CRS": crs, "BBOX": box, "WIDTH": str(width), "HEIGHT": str(height),
                  "FORMAT": fmt}
    sep = "&" if "?" in source["url"] else "?"
    return source["url"] + sep + urllib.parse.urlencode(params)


def _projected_request(crs, bbox, width, height):
    """Umschließender Ausschnitt im projizierten System und die Lage der vier Ecken darin (Pixel).
    Liefert (box, w, h, quad) – quad in der Reihenfolge, die Pillows QUAD-Abbildung erwartet
    (oben links, unten links, unten rechts, oben rechts)."""
    lon1, lat1, lon2, lat2 = bbox
    corners = [_project(crs, lon1, lat2), _project(crs, lon1, lat1), _project(crs, lon2, lat1),
               _project(crs, lon2, lat2)]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
    scale = max((x2 - x1) / width, (y2 - y1) / height)
    w, h = max(16, min(MAX_SIDE, round((x2 - x1) / scale))), max(16, min(MAX_SIDE, round((y2 - y1) / scale)))
    quad = []
    for x, y in corners:
        quad += [(x - x1) / (x2 - x1) * w, (y2 - y) / (y2 - y1) * h]
    return (x1, y1, x2, y2), w, h, tuple(quad)


def _warp(data, quad, width, height):
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as img:
            out = img.convert("RGB").transform((width, height), Image.Transform.QUAD, quad,
                                               Image.Resampling.BILINEAR)
    except (OSError, ValueError) as e:
        raise AerialError(f"Bild nicht lesbar: {e}") from None
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _download(url):
    """Lädt das Bild (in Tests ersetzen). Liefert (bytes, content_type)."""
    request = urllib.request.Request(url, headers={"User-Agent": "Lagersystem-Hofapp/1.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:     # noqa: S310 – feste https-Quellen
        return response.read(30 * 1024 * 1024), response.headers.get("Content-Type", "")


def _prune(folder):
    files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".jpg")]
    total = sum(os.path.getsize(f) for f in files)
    if total <= CACHE_LIMIT:
        return
    for path in sorted(files, key=os.path.getmtime):
        total -= os.path.getsize(path)
        os.remove(path)
        if total <= CACHE_LIMIT * 0.8:
            break


def image(bbox, width, height):
    """Bild (JPEG/PNG-Bytes) für den Ausschnitt – aus dem Zwischenspeicher oder frisch geladen."""
    source = config()
    if source is None:
        raise AerialError("aus")
    width, height = max(16, min(MAX_SIDE, int(width))), max(16, min(MAX_SIDE, int(height)))
    bbox = tuple(round(v, 6) for v in bbox)
    crs, axes, fmt = request_plan(source)
    quad = None
    if axes == "proj":
        box, w, h, quad = _projected_request(crs, bbox, width, height)
        url = _url(source, tuple(round(v, 2) for v in box), w, h, crs, axes, fmt)
    else:
        url = _url(source, bbox, width, height, crs or "EPSG:4326", axes or "latlon", fmt or "image/jpeg")
    folder = cache_dir()
    path = os.path.join(folder, hashlib.sha256(f"{url}|{width}x{height}".encode()).hexdigest()[:40] + ".jpg")
    if os.path.isfile(path):
        os.utime(path)                   # zuletzt benutzt
        with open(path, "rb") as f:
            return f.read()
    try:
        data, content_type = _download(url)
    except OSError as e:
        raise AerialError(str(e)) from None
    if not content_type.startswith("image/") or len(data) < 100:
        # WMS-Dienste melden Fehler als XML-Text
        raise AerialError((data[:300].decode("utf-8", "replace") if data else "leere Antwort").strip()
                          + f"\n\n{crs or ''} {url}".rstrip())
    if quad is not None:
        data = _warp(data, quad, width, height)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    if len(os.listdir(folder)) % 50 == 0:
        _prune(folder)
    return data
