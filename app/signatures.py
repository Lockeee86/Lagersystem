"""Unterschriften am Handy (Lieferschein, Unterweisungen): Zeichenfeld → zugeschnittenes PNG, schwarz auf weiß.

Das Zeichenfeld liefert `static/js/signpad.js` als Daten-URL. Gespeichert wird das PNG in der Datenbank (klein,
höchstens SIGN_WIDTH breit), im PDF über `reportlab.platypus.Image` eingebunden.
"""
import base64
import binascii
import io

from PIL import Image, UnidentifiedImageError

from .i18n import gettext as _
from .storage import StorageError

SIGN_MAX_BYTES = 400_000                      # Daten-URL vom Handy
SIGN_WIDTH = 600                              # gespeichert höchstens so breit (reicht fürs PDF)


def to_png(data_url):
    """Daten-URL aus dem Zeichenfeld → zugeschnittenes PNG, schwarz auf weiß. Leeres Feld → Fehler."""
    prefix = "data:image/png;base64,"
    if not data_url or len(data_url) > SIGN_MAX_BYTES or not data_url.startswith(prefix):
        raise StorageError(_("Bitte im Feld unterschreiben."))
    try:
        raw = base64.b64decode(data_url[len(prefix):], validate=True)
        with Image.open(io.BytesIO(raw)) as img:
            if img.format != "PNG" or img.width > 4000 or img.height > 4000:
                raise ValueError
            img = img.convert("RGBA")
    except (binascii.Error, UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise StorageError(_("Die Unterschrift konnte nicht gelesen werden.")) from None
    # Strich = alles, was nicht durchsichtig und nicht weiß ist
    ink = Image.new("L", img.size, 0)
    ink.paste(255, mask=img.getchannel("A").point(lambda a: 255 if a > 40 else 0))
    gray = img.convert("L").point(lambda v: 255 if v < 200 else 0)
    ink = Image.composite(ink, Image.new("L", img.size, 0), gray)
    box = ink.getbbox()
    if box is None or ((box[2] - box[0]) < 15 and (box[3] - box[1]) < 15):     # nur ein Punkt
        raise StorageError(_("Bitte im Feld unterschreiben."))
    margin = 8
    box = (max(box[0] - margin, 0), max(box[1] - margin, 0), min(box[2] + margin, img.width),
           min(box[3] + margin, img.height))
    out = Image.new("L", img.size, 255)
    out.paste(0, mask=ink)
    out = out.crop(box)
    if out.width > SIGN_WIDTH:
        out = out.resize((SIGN_WIDTH, max(1, round(out.height * SIGN_WIDTH / out.width))), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def pdf_image(png, max_width, max_height):
    """Unterschrift als reportlab-Bild, so groß wie möglich innerhalb max_width × max_height (Punkte)."""
    from reportlab.platypus import Image as PdfImage

    pic = PdfImage(io.BytesIO(png))
    scale = min(max_width / pic.imageWidth, max_height / pic.imageHeight)
    pic.drawWidth, pic.drawHeight = pic.imageWidth * scale, pic.imageHeight * scale
    pic.hAlign = "LEFT"
    return pic
