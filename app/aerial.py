"""Luftbild unter der Schlagkarte: der Server holt das Bild für den sichtbaren Ausschnitt und hält es vor.

Warum über den Server? Die CSP erlaubt nur Bilder von der eigenen Adresse, und so wird jeder Ausschnitt nur einmal
geladen. Quellen (Einstellung `aerial_source`):
  lgln – Niedersachsen, amtliche Digitale Orthophotos (DOP20) des LGLN, Open Data
  esri – Esri World Imagery (weltweit, Nutzungsbedingungen von Esri beachten)
  wms  – eigener WMS-Dienst (z. B. eines anderen Bundeslands), URL und Layer in den Einstellungen
Die Ausschnitte werden in EPSG:4326 (Länge/Breite) abgefragt – das passt ohne Umrechnung zur Schlagkarte.
"""
import hashlib
import os
import urllib.parse
import urllib.request

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


class AerialError(Exception):
    pass


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


def _url(source, bbox, width, height):
    lon1, lat1, lon2, lat2 = bbox
    if source["kind"] == "arcgis":
        params = {"bbox": f"{lon1},{lat1},{lon2},{lat2}", "bboxSR": "4326", "imageSR": "4326",
                  "size": f"{width},{height}", "format": "jpg", "f": "image"}
    else:
        # WMS 1.3.0 mit EPSG:4326: Achsenreihenfolge Breite, Länge
        params = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": source["layer"],
                  "STYLES": "", "CRS": "EPSG:4326", "BBOX": f"{lat1},{lon1},{lat2},{lon2}",
                  "WIDTH": str(width), "HEIGHT": str(height), "FORMAT": "image/jpeg"}
    sep = "&" if "?" in source["url"] else "?"
    return source["url"] + sep + urllib.parse.urlencode(params)


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
    """Bild (JPEG-Bytes) für den Ausschnitt – aus dem Zwischenspeicher oder frisch geladen."""
    source = config()
    if source is None:
        raise AerialError("aus")
    width, height = max(16, min(MAX_SIDE, int(width))), max(16, min(MAX_SIDE, int(height)))
    url = _url(source, tuple(round(v, 6) for v in bbox), width, height)
    folder = cache_dir()
    path = os.path.join(folder, hashlib.sha256(url.encode()).hexdigest()[:40] + ".jpg")
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
        raise AerialError((data[:300].decode("utf-8", "replace") if data else "leere Antwort").strip())
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    if len(os.listdir(folder)) % 50 == 0:
        _prune(folder)
    return data
