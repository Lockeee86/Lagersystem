"""Stall-Display als Bild für Farb-E-Paper (reTerminal E1002: 7,3", 800×480, E Ink Spectra 6).

Das Gerät hat keinen Browser: ESPHome holt mit `online_image` ein PNG und zeichnet es. Darum zeichnet die App das
Bild selbst – nur in den sechs Farben des Panels, Text ohne Kantenglättung (keine Mischfarben, die das Panel
rastern müsste), große Schrift, weißer Grund. Daten aus `chickens.display()`.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

from .i18n import gettext as _, ngettext

W, H = 800, 480
BLACK, WHITE, RED, YELLOW, GREEN, BLUE = (0, 0, 0), (255, 255, 255), (255, 0, 0), (255, 255, 0), (0, 255, 0), (0, 0, 255)
FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF")
_fonts = {}


def font(size, bold=True):
    key = (size, bold)
    if key not in _fonts:
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        path = next((os.path.join(d, name) for d in FONT_DIRS if os.path.exists(os.path.join(d, name))), None)
        _fonts[key] = ImageFont.truetype(path, size) if path else ImageFont.load_default(size)
    return _fonts[key]


def _num(value, digits=0):
    if value is None:
        return "–"
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fit(draw, text, size, width, bold=True):
    """Text kürzen, bis er in `width` passt."""
    f = font(size, bold)
    if draw.textlength(text, font=f) <= width:
        return text
    while text and draw.textlength(text + "…", font=f) > width:
        text = text[:-1]
    return text + "…"


def _check(draw, x, y, s, color):
    draw.line([(x, y + s * .55), (x + s * .38, y + s * .9), (x + s, y + s * .1)], fill=color, width=max(3, s // 6))


def render(d):
    img = Image.new("RGB", (W, H), WHITE)
    dr = ImageDraw.Draw(img)
    dr.fontmode = "1"                       # keine Kantenglättung → nur reine Panel-Farben
    tz_now = d["now"]

    # Kopf: schwarzes Band
    dr.rectangle([0, 0, W, 52], fill=BLACK)
    dr.text((16, 26), _("Hühnerstall"), font=font(26), fill=WHITE, anchor="lm")
    dr.text((200, 26), d["today"].strftime("%d.%m.%Y"), font=font(20, False), fill=WHITE, anchor="lm")
    sun = []
    if d["sunrise"]:
        sun.append("↑ " + d["sunrise"].strftime("%H:%M"))
    if d["sunset"]:
        sun.append("↓ " + d["sunset"].strftime("%H:%M"))
    if sun:
        dr.text((350, 26), "   ".join(sun), font=font(19, False), fill=YELLOW, anchor="lm")
    dr.text((W - 16, 26), _("Stand {time}", time=tz_now.strftime("%H:%M")), font=font(20), fill=WHITE, anchor="rm")

    alerts = d["alerts"][:2]
    bottom = H - 8 - 40 * len(alerts)
    top = 62
    cols = [(10, 270), (280, 530), (540, 790)]
    for x0, x1 in cols:
        dr.rounded_rectangle([x0, top, x1, bottom], radius=14, outline=BLACK, width=3)

    # --- Eier ---
    x0, x1 = cols[0]
    dr.text((x0 + 14, top + 12), _("EIER HEUTE"), font=font(17), fill=BLACK)
    dr.text((x0 + 12, top + 34), str(d["eggs"]["eggs"]), font=font(104), fill=BLACK)
    y = top + 150
    if d["eggs"]["cracked"]:
        dr.text((x0 + 14, y), ngettext("davon {n} Bruch", "davon {n} Bruch", d["eggs"]["cracked"]), font=font(18, False), fill=BLACK)
    y += 26
    if d["week_rate"] is not None:
        rate = _("7 Tage: {rate} %", rate=d["week_rate"])
        low = d["week_expected"] and d["week_rate"] < d["week_expected"] - 10
        dr.text((x0 + 14, y), rate, font=font(20), fill=RED if low else BLACK)
        if d["week_expected"]:
            dr.text((x0 + 14, y + 26), _("Richtwert {rate} %", rate=d["week_expected"]), font=font(16, False), fill=BLACK)
    # 14 Tage als Balken, heute rot
    days = d["days"]
    bx0, bx1, by1 = x0 + 14, x1 - 14, bottom - 30
    bh = max(30, by1 - (y + 60))
    step = (bx1 - bx0) / len(days)
    for i, day in enumerate(days):
        h = max(3, round(bh * day["pct"] / 100))
        left = bx0 + i * step + 2
        dr.rectangle([left, by1 - h, left + step - 4, by1], fill=RED if day["today"] else BLACK)
    dr.text((bx0, by1 + 6), days[0]["day"].strftime("%d.%m."), font=font(14, False), fill=BLACK)
    dr.text((bx1, by1 + 6), _("heute"), font=font(14), fill=RED, anchor="ra")

    # --- Klappe und Stallklima ---
    x0, x1 = cols[1]
    s = d["sensor"]
    dr.text((x0 + 14, top + 12), _("KLAPPE"), font=font(17), fill=BLACK)
    state = s["state"] if s and s["state"] else None
    words = {"offen": (_("offen"), GREEN), "zu": (_("zu"), BLUE), "halb": (_("halb offen"), RED)}
    word, color = words.get(state, ("–", BLACK))
    if s and s["offline"]:
        color = BLACK
    dr.rounded_rectangle([x0 + 14, top + 38, x1 - 14, top + 108], radius=12, fill=color)
    dr.text(((x0 + x1) / 2, top + 73), word, font=font(46), fill=BLACK if color == GREEN else WHITE, anchor="mm")
    y = top + 118
    if d["since"]:
        label = _("letzter Stand {time}", time=d["since"].strftime("%H:%M")) if s["offline"] \
            else _("seit {time}", time=d["since"].strftime("%H:%M"))
        dr.text((x0 + 14, y), label, font=font(18, False), fill=BLACK)
    elif not s:
        dr.text((x0 + 14, y), _("kein Sensor"), font=font(18, False), fill=BLACK)
    y += 28
    if d["sunset"] and state in ("offen", "halb") and tz_now < d["sunset"]:
        dr.text((x0 + 14, y), _fit(dr, _("Klappe zu ab {time}", time=d["sunset"].strftime("%H:%M")), 18,
                                   x1 - x0 - 28), font=font(18), fill=BLACK)
    if s:
        cy = bottom - 92
        dr.line([(x0 + 14, cy - 10), (x1 - 14, cy - 10)], fill=BLACK, width=2)
        cells = [(_num(s["temperature"], 1) + "°", _("Temp."), BLACK),
                 (_num(s["humidity"]) + "%", _("Feuchte"), BLACK),
                 (_num(s["co2"]["value"]) if s["co2"] else "–", "CO₂ ppm",
                  RED if s["co2"] and s["co2"]["level"] != "gut" else BLACK)]
        cw = (x1 - x0 - 28) / 3
        for i, (value, label, col) in enumerate(cells):
            cx = x0 + 14 + cw * i + cw / 2
            dr.text((cx, cy + 18), value, font=font(25), fill=col, anchor="mm")
            dr.text((cx, cy + 52), label, font=font(14, False), fill=BLACK, anchor="mm")
        if s["min24"] is not None:
            dr.text(((x0 + x1) / 2, cy + 74), _("24 h: {low}° bis {high}°", low=_num(s["min24"], 1), high=_num(s["max24"], 1)),
                    font=font(14, False), fill=BLACK, anchor="mm")

    # --- Fütterung, Herde, Wartezeit, Aufgaben ---
    x0, x1 = cols[2]
    dr.text((x0 + 14, top + 12), _("GEFÜTTERT HEUTE"), font=font(17), fill=BLACK)
    bw = (x1 - x0 - 38) / 2
    for i, (done, label) in enumerate(((d["wheat"], _("Weizen")), (d["mash"], _("Legemehl")))):
        bx = x0 + 14 + i * (bw + 10)
        box = [bx, top + 38, bx + bw, top + 84]
        if done:
            dr.rounded_rectangle(box, radius=10, fill=GREEN)
            _check(dr, bx + 8, top + 53, 15, BLACK)
            dr.text((bx + 28, top + 61), _fit(dr, label, 15, bw - 32), font=font(15), fill=BLACK, anchor="lm")
        else:
            dr.rounded_rectangle(box, radius=10, outline=BLACK, width=2)
            dr.text((bx + bw / 2, top + 61), _fit(dr, label, 16, bw - 10, False), font=font(16, False), fill=BLACK, anchor="mm")
    y = top + 98
    dr.text((x0 + 14, y), _("HERDE"), font=font(15), fill=BLACK)
    dr.text((x0 + 134, y), _("VORRAT"), font=font(15), fill=BLACK)
    dr.text((x0 + 14, y + 18), str(d["flock"]), font=font(34), fill=BLACK)
    dr.text((x0 + 134, y + 18), str(d["stock"]), font=font(34), fill=BLACK)
    y += 66
    if d["blocked"]:
        dr.rounded_rectangle([x0 + 14, y, x1 - 14, y + 34], radius=8, fill=RED)
        dr.text(((x0 + x1) / 2, y + 17), _fit(dr, _("Eier gesperrt bis {date}", date=d["blocked"].blocked_until.strftime("%d.%m.")),
                                            15, x1 - x0 - 36), font=font(15), fill=WHITE, anchor="mm")
        y += 44
    for t in d["tasks"]:
        if y + 24 > bottom - 8:
            break
        dr.rectangle([x0 + 16, y + 5, x0 + 28, y + 17], outline=BLACK, width=2)
        dr.text((x0 + 36, y), _fit(dr, t.title, 17, x1 - x0 - 54, False), font=font(17, False), fill=BLACK)
        y += 26

    # --- Warnungen unten ---
    for i, text in enumerate(alerts):
        ay = bottom + 6 + i * 40
        dr.rounded_rectangle([10, ay, W - 10, ay + 34], radius=8, fill=RED)
        dr.text((24, ay + 17), "! " + _fit(dr, text, 20, W - 60), font=font(20), fill=WHITE, anchor="lm")

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
