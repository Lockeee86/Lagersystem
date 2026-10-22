"""Foto-Uploads: werden verkleinert, gedreht (EXIF) und ohne Metadaten als JPEG gespeichert.

Das Verzeichnis kommt aus `UPLOAD_DIR` (im Container ein eigenes Volume, damit Fotos Updates überleben).
"""
import os
import uuid

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

from .i18n import gettext as _
from .storage import StorageError

MAX_SIDE = 1600
MAX_PHOTOS = 6


def upload_dir():
    path = current_app.config["UPLOAD_DIR"]
    os.makedirs(path, exist_ok=True)
    return path


def save_photo(file_storage):
    """Speichert ein hochgeladenes Bild und liefert den Dateinamen. Ungültige Dateien → StorageError."""
    try:
        with Image.open(file_storage.stream) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            name = f"{uuid.uuid4().hex}.jpg"
            # ohne exif= gespeichert → keine Metadaten (z. B. GPS) im Foto
            img.save(os.path.join(upload_dir(), name), "JPEG", quality=82, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        raise StorageError(_("„{name}“ ist kein lesbares Foto (JPEG, PNG oder WebP).",
                             name=(file_storage.filename or "")[:60]))
    return name


def save_photos(files):
    files = [f for f in files if f and f.filename]
    if len(files) > MAX_PHOTOS:
        raise StorageError(_("Höchstens {n} Fotos auf einmal.", n=MAX_PHOTOS))
    names = []
    try:
        for f in files:
            names.append(save_photo(f))
    except StorageError:
        delete_files(names)
        raise
    return names


def delete_files(names):
    for name in names:
        try:
            os.remove(os.path.join(upload_dir(), name))
        except OSError:
            pass
