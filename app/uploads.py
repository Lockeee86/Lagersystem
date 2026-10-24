"""Foto-Uploads: werden verkleinert, gedreht (EXIF) und ohne Metadaten als JPEG gespeichert.

Das Verzeichnis kommt aus `UPLOAD_DIR` (im Container ein eigenes Volume, damit Fotos Updates überleben).
"""
import os
import uuid

from flask import current_app, has_app_context
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import event
from sqlalchemy.orm import Session

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


# --- Dokumente (PDF, Bilder) – unverändert gespeichert ------------------------------

DOC_DIR = "dokumente"
DOC_TYPES = (                       # (Dateianfang, Endung, MIME-Typ)
    (b"%PDF-", "pdf", "application/pdf"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
)


def document_dir():
    path = os.path.join(upload_dir(), DOC_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def save_document(file_storage):
    """Speichert PDF/JPEG/PNG so wie hochgeladen. Liefert (Dateiname, MIME-Typ, Größe).
    Der Typ wird am Dateiinhalt erkannt, nicht an der Endung."""
    head = file_storage.stream.read(8)
    file_storage.stream.seek(0)
    match = next(((ext, mime) for magic, ext, mime in DOC_TYPES if head.startswith(magic)), None)
    if match is None:
        raise StorageError(_("„{name}“ ist kein PDF oder Bild (JPEG, PNG).", name=(file_storage.filename or "")[:60]))
    name = f"{uuid.uuid4().hex}.{match[0]}"
    path = os.path.join(document_dir(), name)
    file_storage.save(path)
    return name, match[1], os.path.getsize(path)


def delete_document(name):
    try:
        os.remove(os.path.join(document_dir(), name))
    except OSError:
        pass


# Fotos, die (auch über cascade) aus der Datenbank gelöscht werden, verlieren nach dem Commit ihre Datei;
# bei einem Rollback bleibt sie.
@event.listens_for(Session, "after_flush")
def _collect_deleted_photos(session, _context):
    from .models import Photo
    names = [obj.filename for obj in session.deleted if isinstance(obj, Photo)]
    if names:
        session.info.setdefault("deleted_photo_files", []).extend(names)


@event.listens_for(Session, "after_commit")
def _remove_deleted_photo_files(session):
    names = session.info.pop("deleted_photo_files", None)
    if names and has_app_context():
        delete_files(names)


@event.listens_for(Session, "after_rollback")
def _forget_deleted_photo_files(session):
    session.info.pop("deleted_photo_files", None)
