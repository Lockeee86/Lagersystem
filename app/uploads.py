"""Foto-Uploads: werden verkleinert, gedreht (EXIF) und ohne Metadaten als JPEG gespeichert.

Das Verzeichnis kommt aus `UPLOAD_DIR` (im Container ein eigenes Volume, damit Fotos Updates überleben).
"""
import os
import uuid
import zipfile

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


THUMB_SIDE = 240


def thumb_name(name):
    """Dateiname der quadratischen Vorschau zu einem Bild (`abc.jpg` → `abc_t.jpg`)."""
    return name[:-4] + "_t.jpg" if name and name.endswith(".jpg") else name


def save_with_thumb(file_storage):
    """Bild speichern (wie `save_photo`) und eine quadratische Vorschau für Listen dazu (THUMB_SIDE Pixel)."""
    name = save_photo(file_storage)
    path = os.path.join(upload_dir(), name)
    with Image.open(path) as img:
        ImageOps.fit(img, (THUMB_SIDE, THUMB_SIDE)).save(os.path.join(upload_dir(), thumb_name(name)), "JPEG",
                                                         quality=80, optimize=True)
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


# Präsentationen und Office-Dateien (z. B. Unterweisungs-Unterlagen der Berufsgenossenschaft): Endung → MIME-Typ.
# Neue Formate sind ZIP-Archive (OOXML mit [Content_Types].xml, ODF mit `mimetype`), alte sind OLE-Dateien.
OFFICE_ZIP = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppsx": "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
}
OFFICE_OLE = {"ppt": "application/vnd.ms-powerpoint", "pps": "application/vnd.ms-powerpoint",
              "doc": "application/msword", "xls": "application/vnd.ms-excel"}
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _office_type(file_storage, head):
    ext = (file_storage.filename or "").rsplit(".", 1)[-1].lower()
    if head.startswith(b"PK\x03\x04") and ext in OFFICE_ZIP:
        try:
            with zipfile.ZipFile(file_storage.stream) as archive:
                names = set(archive.namelist())
        except (zipfile.BadZipFile, OSError):
            names = set()
        finally:
            file_storage.stream.seek(0)
        if "[Content_Types].xml" in names or "mimetype" in names:
            return ext, OFFICE_ZIP[ext]
    if head.startswith(OLE_MAGIC) and ext in OFFICE_OLE:
        return ext, OFFICE_OLE[ext]
    return None


def save_document(file_storage, office=False):
    """Speichert PDF/JPEG/PNG (mit `office` auch Präsentationen, Word, Excel) so wie hochgeladen.
    Liefert (Dateiname, MIME-Typ, Größe). Der Typ wird am Dateiinhalt erkannt, nicht nur an der Endung."""
    head = file_storage.stream.read(8)
    file_storage.stream.seek(0)
    match = next(((ext, mime) for magic, ext, mime in DOC_TYPES if head.startswith(magic)), None)
    if match is None and office:
        match = _office_type(file_storage, head)
    if match is None and office:
        raise StorageError(_("„{name}“ ist kein PDF, Bild oder keine Präsentation (PowerPoint, LibreOffice).",
                             name=(file_storage.filename or "")[:60]))
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
