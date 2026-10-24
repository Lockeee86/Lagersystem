"""Unterweisungsnachweis als PDF (A4): ein Thema, ein Jahr, je Person Datum, Unterweisender und Unterschrift.

Der Nachweis ist für Kontrollen (Berufsgenossenschaft) gedacht und deshalb immer auf Deutsch – wie der Lieferschein.
"""
import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import signatures
from .delivery_note import BASE, BOLD, FARM, HEAD, LINE, SMALL, _lines, _p


def render_instruction_record(topic, records, year, farm):
    buf = io.BytesIO()
    farm_name = farm.get("farm_name") or ""

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(18 * mm, 10 * mm, f"{farm_name} · Unterweisung {topic.name} · {year}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Seite {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title=f"Unterweisung {topic.name} {year}", author=farm_name)
    width = A4[0] - 36 * mm
    story = []
    if farm_name:
        story.append(_p(escape(farm_name), FARM))
    if farm.get("farm_address"):
        story.append(_p(_lines(farm["farm_address"]), SMALL))
    story += [Spacer(1, 6 * mm), Paragraph(f"Nachweis der Unterweisung: {escape(topic.name)}",
                                           FARM), _p(f"Jahr {year} · Wiederholung alle {topic.interval_months} Monate",
                                                     SMALL), Spacer(1, 4 * mm)]
    if topic.content:
        story += [_p("Inhalt der Unterweisung", SMALL), _p(_lines(topic.content)), Spacer(1, 5 * mm)]
    if topic.documents:
        story += [_p("Unterlagen", SMALL), _p("<br/>".join(escape(d.title) for d in topic.documents)),
                  Spacer(1, 5 * mm)]
    story.append(_p("Mit meiner Unterschrift bestätige ich, dass ich unterwiesen wurde und den Inhalt verstanden "
                    "habe.", BASE))
    story.append(Spacer(1, 4 * mm))

    sign_w = 62 * mm
    rows = [[_p("Datum", HEAD), _p("Name", HEAD), _p("Unterwiesen von", HEAD), _p("Unterschrift", HEAD)]]
    for r in records:
        rows.append([_p(r.day.strftime("%d.%m.%Y")), _p(escape(r.signed_name), BOLD), _p(escape(r.instructor_name)),
                     signatures.pdf_image(r.signature, sign_w - 4, 13 * mm)])
    if not records:
        rows.append(["", _p("Keine Unterweisungen in diesem Jahr."), "", ""])
    table = Table(rows, colWidths=[24 * mm, width - 24 * mm - 44 * mm - sign_w, 44 * mm, sign_w], repeatRows=1)
    table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
                               ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story.append(table)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
