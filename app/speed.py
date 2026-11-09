"""Schneller laden: statische Dateien lange zwischenspeichern und Antworten mit gzip packen.

- `url_for('static', …)` hängt die App-Version an (`?v=0.72.3`). Solche Adressen ändern sich mit jedem Update,
  der Browser darf sie daher ein Jahr behalten, ohne nachzufragen. Ohne `v` (z. B. Schriften, die eine CSS-Datei
  nachlädt) gilt ein Tag.
- Text (HTML, CSS, JS, JSON, SVG) über 1 KB wird gepackt, wenn der Browser gzip versteht. Gepackte statische Dateien
  bleiben im Speicher, damit sie nicht bei jedem Abruf neu gepackt werden.
"""
import gzip
import threading

from flask import request

STATIC_LONG = 365 * 24 * 3600
STATIC_SHORT = 24 * 3600
MIN_SIZE = 1024
PACKABLE = {"text/html", "text/css", "text/javascript", "application/javascript", "application/json",
            "image/svg+xml", "text/plain", "application/manifest+json", "text/calendar"}
_static_cache = {}
_lock = threading.Lock()


def init_app(app):
    version = app.config["APP_VERSION"]

    @app.url_defaults
    def static_version(endpoint, values):
        if endpoint == "static" and "v" not in values:
            values["v"] = version

    @app.after_request
    def cache_and_pack(response):
        if request.endpoint == "static" and response.status_code in (200, 304):
            age = STATIC_LONG if request.args.get("v") else STATIC_SHORT
            response.headers["Cache-Control"] = f"public, max-age={age}" + (", immutable" if age == STATIC_LONG else "")
        return _pack(response)


def _pack(response):
    if (response.status_code != 200 or "gzip" not in request.headers.get("Accept-Encoding", "")
            or response.headers.get("Content-Encoding") or response.mimetype not in PACKABLE
            or (response.is_streamed and not response.direct_passthrough)):
        return response
    response.headers.add("Vary", "Accept-Encoding")
    key = None
    if request.endpoint == "static":
        key = (request.path, response.headers.get("ETag"))
        with _lock:
            packed = _static_cache.get(key)
        if packed is not None:
            response.direct_passthrough = False
            return _apply(response, packed)
    response.direct_passthrough = False
    data = response.get_data()
    if len(data) < MIN_SIZE:
        return response
    packed = gzip.compress(data, compresslevel=6)
    if key is not None:
        with _lock:
            if len(_static_cache) > 200:
                _static_cache.clear()
            _static_cache[key] = packed
    return _apply(response, packed)


def _apply(response, packed):
    response.set_data(packed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(packed))
    etag = response.headers.get("ETag")
    if etag and not etag.endswith('-gz"'):
        response.headers["ETag"] = etag[:-1] + '-gz"' if etag.endswith('"') else etag + "-gz"
    return response
