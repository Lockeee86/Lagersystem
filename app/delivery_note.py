"""Lieferschein als PDF (A4).

Der Lieferschein geht an Kunden und ist deshalb immer auf Deutsch – unabhängig von der Sprache
des Benutzers, der ihn druckt. Die Standardschrift Helvetica kann keine polnischen Sonderzeichen.
"""
import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

from . import signatures
from .utils import fmt_datetime

USAGE_TEXT = {"pflanzgut": "Pflanzgut", "speise": "Speiseware", "futter": "Futter", "sonstiges": "Sonstiges"}

BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=12.5)
SMALL = ParagraphStyle("small", parent=BASE, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
BOLD = ParagraphStyle("bold", parent=BASE, fontName="Helvetica-Bold")
FARM = ParagraphStyle("farm", parent=BASE, fontName="Helvetica-Bold", fontSize=14, leading=17)
TITLE = ParagraphStyle("title", parent=BASE, fontName="Helvetica-Bold", fontSize=18, leading=22, alignment=TA_RIGHT)
RIGHT = ParagraphStyle("right", parent=BASE, alignment=TA_RIGHT)
HEAD = ParagraphStyle("head", parent=BASE, fontName="Helvetica-Bold", fontSize=8.5, leading=10.5)
HEAD_R = ParagraphStyle("head_r", parent=HEAD, alignment=TA_RIGHT)
LINE = colors.HexColor("#999999")


def _p(text, style=BASE):
    return Paragraph(text, style)


def _lines(text):
    return "<br/>".join(escape(line) for line in (text or "").splitlines() if line.strip())


def _kg(value):
    return f"{value:,} kg".replace(",", ".") if value is not None else "–"


def _num(value):
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _date(value):
    return value.strftime("%d.%m.%Y") if value else ""


def _quantity(line):
    if line.get("is_bulk"):
        if line["quantity"]:
            return f"{line['quantity']} Big Bag" + ("s" if line["quantity"] != 1 else "")
        return "lose (Schüttgut)"
    if line["is_box"]:
        text = f"{line['boxes']} Kiste" if line["boxes"] == 1 else f"{line['boxes']} Kisten"
        if abs(line["box_units"] - line["boxes"]) > 0.001:
            text += f"<br/><font size=8 color='#555555'>Inhalt {_num(line['box_units'])} Kisten</font>"
        return text
    return f"{line['quantity']} × {escape(line['packaging'] or 'Gebinde')}"


def _passport_country(reg):
    prefix = (reg or "").strip()[:2].upper()
    return prefix if prefix.isalpha() else "DE"


def render_delivery_note(delivery, lines, farm):
    buf = io.BytesIO()
    farm_name = farm.get("farm_name") or "Lieferschein"

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(18 * mm, 10 * mm, f"{farm.get('farm_name') or ''} · Lieferschein {delivery.number}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Seite {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title=f"Lieferschein {delivery.number}", author=farm_name)
    width = A4[0] - 36 * mm
    story = []

    # Kopf: Hof links, Lieferschein rechts
    contact = [c for c in (farm.get("farm_phone") and f"Tel. {escape(farm['farm_phone'])}",
                           farm.get("farm_email") and escape(farm["farm_email"])) if c]
    farm_block = [_p(escape(farm.get("farm_name") or ""), FARM)]
    if farm.get("site_name"):                   # Betriebsstätte (sites.sender) unter dem Hofnamen
        farm_block.append(_p(f"<b>{escape(farm['site_name'])}</b>"))
    farm_block.append(_p(_lines(farm.get("farm_address"))))
    if contact:
        farm_block.append(_p(" · ".join(contact), SMALL))
    meta = [["Nummer", delivery.number], ["Datum", _date(delivery.date)]]
    if delivery.vehicle:
        meta.append(["Fahrzeug", delivery.vehicle])
    meta_table = Table([[_p(a, SMALL), _p(escape(b), RIGHT)] for a, b in meta], colWidths=[22 * mm, 40 * mm])
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                    ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    head = Table([[farm_block, [_p("Lieferschein", TITLE), Spacer(1, 3 * mm), meta_table]]],
                 colWidths=[width - 64 * mm, 64 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    story += [head, Spacer(1, 10 * mm)]

    # Empfänger
    customer = delivery.customer
    recipient = [_p("Empfänger", SMALL), _p(escape(customer.name), BOLD), _p(_lines(customer.address))]
    if customer.number:
        recipient.append(_p(f"Kundennummer: {escape(customer.number)}", SMALL))
    story += recipient + [Spacer(1, 8 * mm)]

    # Positionen
    rows = [[_p("Pos.", HEAD), _p("Ware", HEAD), _p("Menge", HEAD), _p("Gewicht (Kisten geschätzt)" if any(ln["is_box"] for ln in lines) else "Gewicht", HEAD_R)]]
    style = [("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    for pos, line in enumerate(lines, start=1):
        if line.get("is_bulk"):
            rows.append([_p(str(pos)), _p(f"<b>{escape(line['crop'] or 'Schüttgut')}</b><br/>Schüttgut, gewogen"),
                         _p(_quantity(line)), _p(_kg(line["weight_kg"]), RIGHT)])
            style.append(("LINEBELOW", (0, pos), (-1, pos), 0.4, LINE))
            continue
        variety = escape(line["variety"].name) if line["variety"] else "–"
        details = [USAGE_TEXT.get(line["usage"], line["usage"] or "")]
        if line["seed_class"]:
            details.append(f"Stufe {escape(line['seed_class'].name)}")
        if line["grade"]:
            details.append(f"Kaliber {escape(line['grade'])}")
        if line["year"]:
            details.append(f"Ernte {line['year']}")
        text = f"<b>{variety}</b><br/>{' · '.join(d for d in details if d)}"
        if line["certification"]:
            text += f"<br/>Anerkennungsnummer: {escape(line['certification'].number)}"
        if line["code_ranges"]:
            text += f"<br/><font size=8 color='#555555'>Kisten: {escape(line['code_ranges'])}</font>"
        rows.append([_p(str(pos)), _p(text), _p(_quantity(line)), _p(_kg(line["weight_kg"]), RIGHT)])
        style.append(("LINEBELOW", (0, pos), (-1, pos), 0.4, LINE))
    if not lines:
        rows.append(["", _p("Keine Positionen"), "", ""])
    total = sum(line["weight_kg"] for line in lines)
    boxes = sum(line["boxes"] for line in lines)
    sum_text = "Summe" + (f" ({boxes} Kiste{'n' if boxes != 1 else ''})" if boxes else "")
    rows.append(["", _p(sum_text, BOLD), "", _p(_kg(total), ParagraphStyle("sr", parent=RIGHT, fontName="Helvetica-Bold"))])
    table = Table(rows, colWidths=[12 * mm, width - 12 * mm - 42 * mm - 34 * mm, 42 * mm, 34 * mm], repeatRows=1)
    table.setStyle(TableStyle(style))
    story += [table, Spacer(1, 6 * mm)]

    # Fuhrwerkswaage
    if delivery.gross_kg is not None or delivery.tare_kg is not None:
        weigh = [["Vollgewicht (brutto)", _kg(delivery.gross_kg)], ["Leergewicht (tara)", _kg(delivery.tare_kg)],
                 ["Nettogewicht", _kg(delivery.net_kg)]]
        wt = Table([[_p(a, BOLD if i == 2 else BASE), _p(b, ParagraphStyle("w", parent=RIGHT,
                     fontName="Helvetica-Bold" if i == 2 else "Helvetica"))] for i, (a, b) in enumerate(weigh)],
                   colWidths=[50 * mm, 34 * mm])
        wt.setStyle(TableStyle([("LINEABOVE", (0, 2), (-1, 2), 0.8, colors.black),
                                ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
        outer = Table([[_p("Verwogen auf der Fuhrwerkswaage", SMALL), wt]], colWidths=[width - 84 * mm, 84 * mm])
        outer.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        story += [outer, Spacer(1, 6 * mm)]

    # Angaben für den Pflanzenpass (nur Pflanzgut, nur wenn eine Registriernummer eingetragen ist)
    reg = (farm.get("farm_passport_reg") or "").strip()
    seed_lines = [line for line in lines if line["usage"] == "pflanzgut" and line["variety"]]
    if reg and seed_lines:
        blocks = []
        for line in seed_lines:
            trace = line["certification"].number if line["certification"] else delivery.number
            blocks.append(_p(
                f"<b>Pflanzenpass / Plant Passport</b><br/>"
                f"A Solanum tuberosum L. – {escape(line['variety'].name)}<br/>"
                f"B {escape(reg)}<br/>C {escape(trace)}<br/>D {_passport_country(reg)}", SMALL))
        cells = [blocks[i:i + 3] + [""] * (3 - len(blocks[i:i + 3])) for i in range(0, len(blocks), 3)]
        pt = Table(cells, colWidths=[width / 3] * 3)
        pt.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.4, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                                ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [KeepTogether([_p("Angaben für den Pflanzenpass", SMALL), Spacer(1, 1.5 * mm), pt]),
                  Spacer(1, 6 * mm)]

    if delivery.note:
        story += [_p("Bemerkung", SMALL), _p(_lines(delivery.note)), Spacer(1, 6 * mm)]

    # Unterschriften (am Handy unterschrieben → Bild über der Linie, Name und Zeitpunkt darunter)
    half = (width - 12 * mm) / 2

    def signed(image, name, at, label):
        if not image:
            return "", _p(f"{label} (Datum, Unterschrift)", SMALL)
        return signatures.pdf_image(image, half, 15 * mm), _p(f"{label}: {escape(name or '')}, {fmt_datetime(at)}", SMALL)

    farm_sign = signed(delivery.sign_farm, delivery.sign_farm_name, delivery.sign_farm_at, "Übergeben")
    customer_sign = signed(delivery.sign_customer, delivery.sign_customer_name, delivery.sign_customer_at,
                           "Ware erhalten")
    sign = Table([[farm_sign[0], "", customer_sign[0]], [farm_sign[1], "", customer_sign[1]]],
                 colWidths=[half, 12 * mm, half], rowHeights=[17 * mm, None])
    sign.setStyle(TableStyle([("LINEBELOW", (0, 0), (0, 0), 0.6, colors.black),
                              ("LINEBELOW", (2, 0), (2, 0), 0.6, colors.black),
                              ("VALIGN", (0, 0), (-1, 0), "BOTTOM"), ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(KeepTogether([Spacer(1, 6 * mm), sign]))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
