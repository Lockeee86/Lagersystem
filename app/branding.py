"""Eigenes Hof-Logo: aus einem hochgeladenen Bild werden alle Größen erzeugt (Kopfzeile, Tab, App-Icons).

Die Dateien liegen in `UPLOAD_DIR/branding/` (werden also mitgesichert). `Setting["logo_version"]` sagt, ob ein
eigenes Logo aktiv ist, und hängt als `?v=` an den Adressen – so laden Browser ein neues Logo sofort.
Ohne eigenes Logo gelten die Dateien aus `static/img/`.
"""
import os
import uuid

from flask import Blueprint, abort, current_app, g, redirect, send_from_directory, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

from .extensions import db
from .i18n import gettext as _
from .models import Setting
from .storage import StorageError
from .uploads import upload_dir

# Name → (Kantenlänge, Hintergrund). Logos transparent, App-Icons mit weißem Grund und Rand
# (Handys runden die Ecken ab bzw. schneiden bei „maskable“ bis zu 20 % weg).
SIZES = {
    "logo-32": (32, None), "logo-64": (64, None), "logo-512": (512, None),
    "icon-180": (180, "#ffffff"), "icon-192": (192, "#ffffff"), "icon-512": (512, "#ffffff"),
}
ICON_PADDING = 0.14
MIN_SIDE = 64


def branding_dir():
    path = os.path.join(upload_dir(), "branding")
    os.makedirs(path, exist_ok=True)
    return path


def version():
    """Kennung des eigenen Logos oder None (Standard-Logo)."""
    if "logo_version" not in g:
        setting = db.session.get(Setting, "logo_version")
        g.logo_version = setting.value if setting and setting.value else None
    return g.logo_version


def url(name):
    """Adresse eines Logos/Icons, z. B. url("logo-64")."""
    v = version()
    if v:
        return url_for("branding.logo", name=name, v=v)
    static = "img/icon.svg" if name.startswith("logo-") else f"img/{name}.png"
    return url_for("static", filename=static, v=current_app.config.get("APP_VERSION"))


def _square(img, side, background):
    """Bild ohne Abschneiden mittig in ein Quadrat einpassen."""
    canvas = Image.new("RGBA", (side, side), background or (0, 0, 0, 0))
    inner = side if background is None else round(side * (1 - 2 * ICON_PADDING))
    fitted = ImageOps.contain(img, (inner, inner), Image.LANCZOS)
    canvas.alpha_composite(fitted, ((side - fitted.width) // 2, (side - fitted.height) // 2))
    return canvas.convert("RGB") if background else canvas


def save(file_storage):
    """Hochgeladenes Logo prüfen und alle Größen schreiben."""
    try:
        with Image.open(file_storage.stream) as img:
            img.load()
            img = ImageOps.exif_transpose(img).convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        raise StorageError(_("Das Logo muss ein Bild sein (PNG, JPEG oder WebP)."))
    box = img.getchannel("A").getbbox()            # durchsichtigen Rand wegschneiden
    if box:
        img = img.crop(box)
    if max(img.size) < MIN_SIDE:
        raise StorageError(_("Das Logo ist zu klein – bitte mindestens {n} × {n} Pixel.", n=MIN_SIDE))
    new_version = uuid.uuid4().hex[:10]
    folder = branding_dir()
    for name, (side, background) in SIZES.items():
        _square(img, side, background).save(os.path.join(folder, f"{name}.png"), "PNG", optimize=True)
    Setting.put("logo_version", new_version)
    g.pop("logo_version", None)


def reset():
    folder = branding_dir()
    for name in SIZES:
        try:
            os.remove(os.path.join(folder, f"{name}.png"))
        except OSError:
            pass
    Setting.put("logo_version", None)
    g.pop("logo_version", None)


# --- Auslieferung ---------------------------------------------------------------
bp = Blueprint("branding", __name__)


@bp.route("/logo/<name>.png")
def logo(name):
    """Ohne Anmeldung (Login-Seite, Push-Nachrichten). Ohne eigenes Logo → Standard-Datei."""
    if name not in SIZES:
        abort(404)
    path = os.path.join(branding_dir(), f"{name}.png")
    if not version() or not os.path.isfile(path):
        static = "img/icon.svg" if name.startswith("logo-") else f"img/{name}.png"
        return redirect(url_for("static", filename=static))
    return send_from_directory(branding_dir(), f"{name}.png", mimetype="image/png", max_age=7 * 86400)
