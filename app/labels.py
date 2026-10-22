"""QR-Etiketten als PDF (A4)."""
import io

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .i18n import N_

# Name: (Spalten, Zeilen, Breite mm, Höhe mm, Beschreibung)
LABEL_FORMATS = {
    "3x8": (3, 8, 70, 37, N_("24 Etiketten pro Blatt, 70 × 37 mm (z. B. Avery Zweckform 3474)")),
    "2x4": (2, 4, 105, 74, N_("8 Etiketten pro Blatt, 105 × 74 mm (z. B. Avery Zweckform 3427)")),
    "2x2": (2, 2, 105, 148, N_("4 große Schilder pro Blatt, 105 × 148 mm (A6) – gut lesbar aus Entfernung")),
}


def _draw_qr(c, value, x, y, size):
    widget = QrCodeWidget(value, barLevel="M", barBorder=0)
    x1, y1, x2, y2 = widget.getBounds()
    w, h = x2 - x1, y2 - y1
    drawing = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, x, y)


def _fit_font(c, text, font, max_size, max_width):
    size = max_size
    while size > 6 and c.stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def _draw_label(c, code, x, y, w, h):
    pad = min(w, h) * 0.08
    if w / h > 1.4:
        # Querformat: QR links, Code rechts
        qr = h - 2 * pad
        _draw_qr(c, code, x + pad, y + pad, qr)
        text_x = x + 2 * pad + qr
        text_w = w - 3 * pad - qr
        size = _fit_font(c, code, "Helvetica-Bold", h * 0.32, text_w)
        c.setFont("Helvetica-Bold", size)
        c.drawString(text_x, y + h / 2 - size * 0.35, code)
    else:
        # Hochformat: QR oben, Code darunter
        text_h = h * 0.18
        qr = min(w - 2 * pad, h - 3 * pad - text_h)
        _draw_qr(c, code, x + (w - qr) / 2, y + h - pad - qr, qr)
        size = _fit_font(c, code, "Helvetica-Bold", text_h, w - 2 * pad)
        c.setFont("Helvetica-Bold", size)
        c.drawCentredString(x + w / 2, y + pad + (text_h - size) / 2 + size * 0.15, code)


def render_labels(codes, fmt="3x8", skip=0):
    """Erzeugt ein PDF. `skip` lässt bereits benutzte Etiketten auf dem ersten Blatt frei."""
    cols, rows, w_mm, h_mm, _ = LABEL_FORMATS[fmt]
    page_w, page_h = A4
    w, h = w_mm * mm, h_mm * mm
    margin_x = (page_w - cols * w) / 2
    margin_y = (page_h - rows * h) / 2
    per_page = cols * rows

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Etiketten")
    positions = [None] * (skip % per_page) + list(codes)
    for i, code in enumerate(positions):
        if i and i % per_page == 0:
            c.showPage()
        if code is None:
            continue
        n = i % per_page
        col, row = n % cols, n // cols
        x = margin_x + col * w
        y = page_h - margin_y - (row + 1) * h
        _draw_label(c, code, x, y, w, h)
    c.showPage()
    c.save()
    return buf.getvalue()
