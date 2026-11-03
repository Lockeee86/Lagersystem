"""Anzeige der Biogasanlage für ein kleines Display (800×480) – wie die Stall-Anzeige der Hühner.

Als Webseite (`biogas/display.html`, z. B. Raspberry Pi im Vollbild) und als Bild für Farb-E-Paper (reTerminal E1002,
sechs Farben, ESPHome `online_image`). Öffnet ohne Anmeldung über einen geheimen Link (`settings`
`biogas_display_token`, ein Admin erzeugt bzw. erneuert ihn auf der Biogas-Seite). Nur lesend; committet nicht.
Live-Werte nur aus dem Zwischenspeicher (`biogas.live(cached_only=True)`) – nie auf die API warten.
"""
import hmac
import io
import secrets
from datetime import datetime

from PIL import Image, ImageDraw

from . import biogas, feedin, machines, rounds
from .epaper import BLACK, BLUE, GREEN, H, RED, W, WHITE, YELLOW, _fit, _num, font
from .extensions import db
from .i18n import gettext as _, ngettext
from .models import Setting
from .utils import local_tz

TOKEN_KEY = "biogas_display_token"
REFRESH = 60                       # Sekunden (Webseite); das E-Paper holt das Bild in seinem eigenen Takt
GAS_HIGH, GAS_LOW = 90, 10         # Gasspeicher in %: ab hier Warnung


def token():
    setting = db.session.get(Setting, TOKEN_KEY)
    return setting.value if setting and setting.value else None


def new_token():
    value = secrets.token_urlsafe(18)
    Setting.put(TOKEN_KEY, value)
    return value


def check_token(value):
    current = token()
    return bool(value and current and hmac.compare_digest(current, value))


def _price_bands(strip):
    """Preis-Ampel in drei Stufen (für die sechs Farben des E-Papers): je Viertelstunde gut/mittel/schlecht."""
    if not strip:
        return []
    low, high = strip["min"], strip["max"]
    span = (high - low) or 1
    producer = strip["view"] == "erzeuger"
    bands = []
    for seg in strip["segments"]:
        r = (seg["price"] - low) / span
        level = "low" if r < 1 / 3 else "high" if r > 2 / 3 else "mid"
        good = level == ("high" if producer else "low")
        bad = level == ("low" if producer else "high")
        bands.append({"width": seg["width"], "level": "good" if good else "bad" if bad else "mid"})
    return bands


def data(now=None):
    """Alles für die Anzeige der Biogasanlage auf einen Blick."""
    tz = local_tz()
    now = now or datetime.now(tz)
    today = now.date()
    plant = biogas.dashboard(today)
    cached = biogas.live(cached_only=True) or {}
    if cached.get("error"):
        cached = {}
    strip = biogas.price_strip(cached.get("price_series")) if cached.get("price_series") else None
    motors = []
    for row in (plant or {}).get("rows", []):
        current = row["now"] or {}
        motors.append({"machine": row["machine"], "running": current.get("running"), "kw": current.get("kw_now"),
                       "today_hours": current.get("today_hours"), "start": row["start"], "next": row["next"],
                       "known": bool(row["now"])})
    faults = [e for e in machines.open_faults() if e.machine.category in machines.BIOGAS_HIDDEN
              or (e.machine.place and getattr(e.machine.place, "in_biogas", False))]
    inspections = [(t, s) for t, s in biogas.plant_inspections(today) if s["state"] in ("overdue", "soon")]
    round_overview = rounds.overview() if rounds.has_stations() else None
    budgets = [s for s in feedin.overview(today.year) if s["budget"]]
    gas = cached.get("gas")
    alerts = []
    for e in faults[:2]:
        alerts.append(_("Störung {machine}: {what}", machine=e.machine.name, what=e.text or _("ohne Beschreibung")))
    if gas and gas.get("now") is not None:
        if gas["now"] >= GAS_HIGH:
            alerts.append(_("Gasspeicher fast voll ({pct} %).", pct=_num(gas["now"])))
        elif gas["now"] <= GAS_LOW:
            alerts.append(_("Gasspeicher fast leer ({pct} %).", pct=_num(gas["now"])))
    if round_overview and round_overview["overdue"]:
        alerts.append(ngettext("Rundgang: {n} Station nicht kontrolliert.", "Rundgang: {n} Stationen nicht kontrolliert.",
                               round_overview["overdue"]))
    overdue = [t for t, s in inspections if s["state"] == "overdue"]
    if overdue:
        alerts.append(ngettext("Prüfung überfällig: {names}", "Prüfungen überfällig: {names}", len(overdue),
                               names=", ".join(t.name for t in overdue[:3])))
    for s in budgets:
        if s["level"] == "over":
            alerts.append(_("{name}: Jahresbudget wird überschritten.", name=s["point"].name))
    return {
        "now": now, "today": today, "motors": motors, "kw": (plant or {}).get("kw", 0),
        "running": (plant or {}).get("running", 0), "live": bool(plant and plant.get("live")),
        "plan_only": biogas.PLAN_ONLY, "gas": gas, "price_now": cached.get("price_now"), "strip": strip,
        "bands": _price_bands(strip), "budgets": budgets[:2], "rounds": round_overview, "faults": faults,
        "inspections": inspections[:3], "alerts": alerts, "refresh": REFRESH, "configured": biogas.configured(),
    }


# --- Bild für Farb-E-Paper -------------------------------------------------------------------

def _start_text(start):
    if not start:
        return ""
    if start["day"] == "today":
        return _("Start {time}", time=start["time"])
    if start["day"] == "tomorrow":
        return _("Start morgen {time}", time=start["time"])
    return _("Start {date} {time}", date=start["day"].strftime("%d.%m."), time=start["time"])


def render(d):
    img = Image.new("RGB", (W, H), WHITE)
    dr = ImageDraw.Draw(img)
    dr.fontmode = "1"                       # keine Kantenglättung → nur reine Panel-Farben

    dr.rectangle([0, 0, W, 52], fill=BLACK)
    dr.text((16, 26), _("Biogasanlage"), font=font(26), fill=WHITE, anchor="lm")
    dr.text((222, 26), d["today"].strftime("%d.%m.%Y"), font=font(20, False), fill=WHITE, anchor="lm")
    if d["motors"]:
        dr.text((370, 26), _("{kw} kW", kw=_num(d["kw"])), font=font(20), fill=YELLOW, anchor="lm")
    dr.text((W - 16, 26), _("Stand {time}", time=d["now"].strftime("%H:%M")), font=font(20), fill=WHITE, anchor="rm")

    alerts = d["alerts"][:2]
    bottom = H - 8 - 40 * len(alerts)
    top = 62
    cols = [(10, 330), (340, 560), (570, 790)]
    for x0, x1 in cols:
        dr.rounded_rectangle([x0, top, x1, bottom], radius=14, outline=BLACK, width=3)

    # --- BHKW ---
    x0, x1 = cols[0]
    dr.text((x0 + 14, top + 12), _("BHKW") + (" · " + _("laut Fahrplan") if d["plan_only"] else ""), font=font(17), fill=BLACK)
    y = top + 40
    if not d["motors"]:
        dr.text((x0 + 14, y), _("Keine BHKW angelegt."), font=font(18, False), fill=BLACK)
    room = (bottom - y - 8) // max(len(d["motors"]), 1)
    row_h = max(58, min(96, room))
    for m in d["motors"]:
        if y + 54 > bottom:
            break
        running = m["running"]
        color = GREEN if running else BLUE if m["known"] else BLACK
        dr.rounded_rectangle([x0 + 14, y, x0 + 104, y + 40], radius=10, fill=color)
        word = _("läuft") if running else _("steht") if m["known"] else "–"
        dr.text((x0 + 59, y + 20), word, font=font(19), fill=BLACK if running else WHITE, anchor="mm")
        dr.text((x0 + 116, y + 1), _fit(dr, m["machine"].name, 21, x1 - x0 - 130), font=font(21), fill=BLACK)
        if running and m["kw"] is not None:
            info = _("{kw} kW · heute {h} h", kw=_num(m["kw"]), h=_num(m["today_hours"], 1))
        elif m["start"]:
            info = _start_text(m["start"])
        elif m["known"]:
            info = _("heute {h} h", h=_num(m["today_hours"], 1))
        else:
            info = _("kein aktueller Wert")
        dr.text((x0 + 116, y + 27), _fit(dr, info, 16, x1 - x0 - 130, False), font=font(16, False), fill=BLACK)
        nxt = m["next"]
        if nxt and row_h >= 76:
            left = nxt["left"]
            late = left is not None and left <= 0
            text = _("{name} fällig", name=nxt["task"].name) if late else \
                _("{name} in {h} h", name=nxt["task"].name, h=_num(left)) if left is not None else nxt["task"].name
            dr.text((x0 + 116, y + 49), _fit(dr, text, 15, x1 - x0 - 130, False), font=font(15, False),
                    fill=RED if late else BLACK)
        y += row_h

    # --- Gasspeicher und Strompreis ---
    x0, x1 = cols[1]
    dr.text((x0 + 14, top + 12), _("GASSPEICHER"), font=font(17), fill=BLACK)
    gas = d["gas"]
    if gas and gas.get("now") is not None:
        pct = gas["now"]
        warn = pct >= GAS_HIGH or pct <= GAS_LOW
        dr.text((x0 + 12, top + 34), _num(pct) + "%", font=font(62), fill=RED if warn else BLACK)
        bx0, bx1, by = x0 + 14, x1 - 14, top + 110
        dr.rectangle([bx0, by, bx1, by + 20], outline=BLACK, width=2)
        fill_w = round((bx1 - bx0 - 4) * max(0, min(pct, 100)) / 100)
        if fill_w:
            dr.rectangle([bx0 + 2, by + 2, bx0 + 2 + fill_w, by + 18], fill=RED if warn else BLUE)
        if gas.get("min") is not None:
            dr.text((x0 + 14, by + 28), _("24 h: {low} bis {high} %", low=_num(gas["min"]), high=_num(gas["max"])),
                    font=font(15, False), fill=BLACK)
    else:
        dr.text((x0 + 14, top + 44), "–", font=font(40), fill=BLACK)
    y = top + 176
    dr.line([(x0 + 14, y), (x1 - 14, y)], fill=BLACK, width=2)
    dr.text((x0 + 14, y + 10), _("STROMPREIS"), font=font(17), fill=BLACK)
    if d["price_now"] is not None:
        dr.text((x0 + 14, y + 34), _num(d["price_now"]), font=font(44), fill=BLACK)
        dr.text((x1 - 14, y + 60), "€/MWh", font=font(15, False), fill=BLACK, anchor="rm")
    else:
        dr.text((x0 + 14, y + 40), "–", font=font(32), fill=BLACK)
    if d["bands"]:
        sx0, sx1, sy = x0 + 14, x1 - 14, bottom - 54
        total = sx1 - sx0
        pos = float(sx0)
        colors = {"good": GREEN, "mid": YELLOW, "bad": RED}
        for band in d["bands"]:
            w = total * band["width"] / 100
            dr.rectangle([round(pos), sy, max(round(pos + w) - 1, round(pos)), sy + 22], fill=colors[band["level"]])
            pos += w
        dr.rectangle([sx0, sy, sx1, sy + 22], outline=BLACK, width=1)
        strip = d["strip"]
        if strip.get("now_pct") is not None:
            nx = sx0 + total * strip["now_pct"] / 100
            dr.polygon([(nx - 7, sy - 10), (nx + 7, sy - 10), (nx, sy - 1)], fill=BLACK)
        if strip.get("best"):
            when, price = strip["best"]
            label = _("Teuerste Stunde {time}", time=when.strftime("%H:%M")) if strip["view"] == "erzeuger" \
                else _("Günstigste Stunde {time}", time=when.strftime("%H:%M"))
            dr.text((sx0, sy + 28), _fit(dr, label, 14, total, False), font=font(14, False), fill=BLACK)

    # --- Budget, Rundgang, Prüfungen ---
    x0, x1 = cols[2]
    y = top + 12
    for s in d["budgets"]:
        dr.text((x0 + 14, y), _fit(dr, _("BUDGET {name}", name=s["point"].name.upper()), 15, x1 - x0 - 28), font=font(15), fill=BLACK)
        y += 22
        used = s["used_pct"] or 0
        color = RED if s["level"] == "over" else YELLOW if s["level"] == "tight" else GREEN
        bx0, bx1 = x0 + 14, x1 - 14
        dr.rectangle([bx0, y, bx1, y + 20], outline=BLACK, width=2)
        w = round((bx1 - bx0 - 4) * max(0, min(used, 100)) / 100)
        if w:
            dr.rectangle([bx0 + 2, y + 2, bx0 + 2 + w, y + 18], fill=color)
        mark = bx0 + (bx1 - bx0) * min(s["year_pct"], 100) / 100
        dr.line([(mark, y - 4), (mark, y + 24)], fill=BLACK, width=3)
        dr.text((x0 + 14, y + 26), _("{used} % verbraucht · Jahr {year} %", used=_num(used), year=_num(s["year_pct"])),
                font=font(14, False), fill=BLACK)
        y += 52
    if d["rounds"]:
        r = d["rounds"]
        dr.text((x0 + 14, y), _("RUNDGANG"), font=font(15), fill=BLACK)
        y += 22
        ok = not r["overdue"] and not r["issues"]
        box = [x0 + 14, y, x1 - 14, y + 36]
        dr.rounded_rectangle(box, radius=8, fill=GREEN if ok else RED if r["overdue"] else YELLOW)
        if ok:
            text = _("alles kontrolliert")
        elif r["overdue"]:
            text = ngettext("{n} Station offen", "{n} Stationen offen", r["overdue"])
        else:
            text = ngettext("{n} offener Mangel", "{n} offene Mängel", r["issues"])
        dr.text(((x0 + x1) / 2, y + 18), _fit(dr, text, 17, x1 - x0 - 36), font=font(17),
                fill=WHITE if (not ok and r["overdue"]) else BLACK, anchor="mm")
        y += 48
    if d["inspections"] and y + 40 < bottom:
        dr.text((x0 + 14, y), _("PRÜFUNGEN"), font=font(15), fill=BLACK)
        y += 22
        for t, s in d["inspections"]:
            if y + 22 > bottom - 6:
                break
            when = s["due_on"].strftime("%d.%m.") if s.get("due_on") else ""
            col = RED if s["state"] == "overdue" else BLACK
            room = dr.textlength(when, font=font(14, False))
            dr.text((x0 + 16, y), _fit(dr, t.name, 15, x1 - x0 - 40 - room, False), font=font(15, False), fill=col)
            dr.text((x1 - 16, y + 1), when, font=font(14, False), fill=col, anchor="ra")
            y += 22
    if not d["budgets"] and not d["rounds"] and not d["inspections"]:
        dr.text((x0 + 14, y), _("Alles in Ordnung."), font=font(18, False), fill=BLACK)

    for i, text in enumerate(alerts):
        ay = bottom + 6 + i * 40
        dr.rounded_rectangle([10, ay, W - 10, ay + 34], radius=8, fill=RED)
        dr.text((24, ay + 17), "! " + _fit(dr, text, 20, W - 60), font=font(20), fill=WHITE, anchor="lm")

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
