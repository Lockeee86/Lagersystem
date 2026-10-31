"""Jahresbudget je Einspeisestelle (Höchstbemessungsleistung).

Die Höchstbemessungsleistung ist ein Jahresmittel: erlaubt ist die eingetragene Jahresmenge (`annual_kwh`) bzw.
`max_kw` × Stunden des Jahres. Erzeugt wird je Monat
entweder die echte Menge aus der Abrechnung (`FeedMonth`) oder – solange es die noch nicht gibt – die Summe aus dem
SKVE-Fahrplan der zugeordneten BHKW (`ChpDay.kwh`, also geplant, nicht gemessen). Daraus: Stand, Prognose beim
jetzigen Tempo, Rest bis 31.12. (Ø kW, Volllaststunden) und ab welchem Preis sich eine Stunde noch lohnt.
Committet nicht; `check()` läuft aus `push.daily`.
"""
import calendar
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import extract, func

from .extensions import db
from .i18n import N_
from .models import ChpDay, FeedMonth, FeedPoint, Machine
from .storage import StorageError

TIGHT = 0.98          # Prognose ab 98 % des Budgets: „knapp“
MONTH_NAMES = (N_("Jan"), N_("Feb"), N_("Mär"), N_("Apr"), N_("Mai"), N_("Jun"), N_("Jul"), N_("Aug"), N_("Sep"),
               N_("Okt"), N_("Nov"), N_("Dez"))


def year_days(year):
    return 366 if calendar.isleap(year) else 365


def points():
    return FeedPoint.query.order_by(FeedPoint.name).all()


def candidates():
    """BHKW-Maschinen, die man einer Einspeisestelle zuordnen kann."""
    from .machines import BIOGAS_CATEGORY
    return Machine.query.filter_by(category=BIOGAS_CATEGORY).order_by(Machine.name).all()


def _number(raw, low, high, message):
    """Zahl wie auf Abrechnungen: „1.234.567,8“, „1234567.8“ oder „746.370“ (Punkte als Tausendertrenner)."""
    text = (raw or "").strip().replace(" ", "").replace("\u202f", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise StorageError(message)
    if not low <= value <= high:
        raise StorageError(message)
    return value


def save_point(point, name, amount, machine_ids, unit="kwh"):
    """Budget als Jahresmenge (`unit` „kwh“) oder als Höchstbemessungsleistung in kW („kw“)."""
    name = (name or "").strip()[:100]
    if not name:
        raise StorageError(N_("Bitte einen Namen für die Einspeisestelle angeben."))
    if point is None:
        point = FeedPoint(name=name)
        db.session.add(point)
    point.name = name
    if unit == "kw":
        point.max_kw = _number(amount, Decimal("1"), Decimal("100000"),
                               N_("Bitte die Höchstbemessungsleistung in kW angeben."))
        point.annual_kwh = None
    else:
        point.annual_kwh = _number(amount, Decimal("1000"), Decimal("1000000000"),
                                   N_("Bitte die erlaubte Jahresmenge in kWh angeben."))
        point.max_kw = None
    db.session.flush()
    wanted = set(machine_ids)
    for machine in candidates():
        if machine.id in wanted:
            machine.feed_point_id = point.id
        elif machine.feed_point_id == point.id:
            machine.feed_point_id = None
    return point


def delete_point(point):
    for machine in point.machines:
        machine.feed_point_id = None
    db.session.delete(point)


def save_months(point, year, values):
    """`values`: {Monat: Text} – leer = Wert löschen (dann zählt wieder der Fahrplan)."""
    existing = {m.month: m for m in point.months.filter_by(year=year)}
    for month, raw in values.items():
        raw = (raw or "").strip()
        row = existing.get(month)
        if not raw:
            if row is not None:
                db.session.delete(row)
            continue
        kwh = _number(raw, Decimal("0"), Decimal("100000000"), N_("Bitte die Monatsmengen in kWh angeben."))
        if row is None:
            db.session.add(FeedMonth(feed_point_id=point.id, year=year, month=month, kwh=kwh))
        else:
            row.kwh = kwh


def months(point, year, today=None):
    """12 × {month, kwh, source: "abrechnung" | "fahrplan" | None} (künftige Monate ohne Wert)."""
    today = today or date.today()
    stored = {m.month: m for m in point.months.filter_by(year=year)}
    actual = {k: float(m.kwh) for k, m in stored.items() if k}
    ids = [m.id for m in point.machines]
    planned = {}
    if ids:
        rows = (db.session.query(extract("month", ChpDay.day), func.sum(ChpDay.kwh), func.count(func.distinct(ChpDay.day)))
                .filter(ChpDay.machine_id.in_(ids), extract("year", ChpDay.day) == year, ChpDay.day < today)
                .group_by(extract("month", ChpDay.day)).all())
        planned = {int(m): (float(k or 0), int(n)) for m, k, n in rows}
    result = []
    for month in range(1, 13):
        length = calendar.monthrange(year, month)[1]
        last = date(year, month, length)
        past = min(length, max((min(today, last + timedelta(days=1)) - date(year, month, 1)).days, 0))
        if month in actual:
            result.append({"month": month, "kwh": actual[month], "source": "abrechnung", "days": length, "past": length})
        elif month in planned:
            kwh, days = planned[month]
            result.append({"month": month, "kwh": kwh, "source": "fahrplan", "days": days, "past": past})
        else:
            result.append({"month": month, "kwh": None, "source": None, "days": 0, "past": past})
        result[-1]["missing"] = result[-1]["past"] - result[-1]["days"] > 2       # mehr als 2 Tage ohne Werte
        row = stored.get(month)
        result[-1]["reduced"] = float(row.reduced_kwh) if row is not None and row.reduced_kwh is not None else None
    return result


def _elapsed_days(year, rows, today):
    """Tage, für die es Werte gibt: bis gestern, mindestens bis zum Ende des letzten Abrechnungsmonats."""
    if today.year > year:
        return year_days(year)
    if today.year < year:
        return 0
    days = (today - date(year, 1, 1)).days
    billed = [r["month"] for r in rows if r["source"] == "abrechnung"]
    if billed:
        last = max(billed)
        days = max(days, (date(year, last, calendar.monthrange(year, last)[1]) - date(year, 1, 1)).days + 1)
    return days


def status(point, year=None, today=None):
    today = today or date.today()
    year = year or today.year
    rows = months(point, year, today)
    budget = point.budget_kwh(year)
    used = sum(r["kwh"] for r in rows if r["kwh"] is not None)
    elapsed = _elapsed_days(year, rows, today)
    total = year_days(year)
    left_days = max(total - elapsed, 0)
    covered = sum(min(r["days"], r["past"]) if r["source"] == "fahrplan" else r["days"] for r in rows if r["source"])
    pace = used / covered if covered else None                     # kWh je Tag, nur aus Tagen mit Werten
    gap = max(elapsed - covered, 0)                                # vergangene Tage ohne Werte
    estimated = used + pace * gap if pace is not None else used    # bisher (fehlende Tage im selben Tempo)
    projected = estimated + pace * left_days if pace is not None else None
    annual = point.months.filter_by(year=year, month=0).first()
    if annual is not None and not any(r["source"] == "abrechnung" for r in rows):
        # nur die Jahresabrechnung: sie gilt für das ganze Jahr
        used = estimated = float(annual.kwh)
        gap, elapsed, left_days = 0, total, 0
        projected = estimated
        for r in rows:
            r["missing"] = False
    remaining = budget - estimated
    reduced = [r["reduced"] for r in rows if r["reduced"] is not None]
    if annual is not None and annual.reduced_kwh is not None and not reduced:
        reduced = [float(annual.reduced_kwh)]
    capacity = sum(m.power_kw or 0 for m in point.machines)
    level = None
    if projected is not None:
        level = "over" if projected > budget else "tight" if projected >= budget * TIGHT else "ok"
    if estimated > budget:
        level = "over"
    return {
        "point": point, "year": year, "months": rows, "budget": budget, "used": used,
        "used_pct": round(100 * estimated / budget, 1) if budget else None,
        "year_pct": round(100 * elapsed / total, 1), "elapsed": elapsed, "left_days": left_days,
        "projected": projected, "diff": projected - budget if projected is not None else None,
        "remaining": remaining,
        "avg_kw_left": remaining / (left_days * 24) if left_days and remaining > 0 else 0.0 if left_days else None,
        "capacity": capacity or None,
        "full_hours_left": remaining / capacity if capacity and remaining > 0 else 0.0 if capacity else None,
        "hours_per_day": remaining / capacity / left_days if capacity and left_days and remaining > 0 else 0.0
        if capacity and left_days else None,
        "billed": sum(1 for r in rows if r["source"] == "abrechnung"),
        "gap_days": gap, "estimated": estimated, "missing": [r["month"] for r in rows if r["missing"]],
        "annual": float(annual.kwh) if annual is not None else None, "reduced": sum(reduced) if reduced else None,
        "level": level,
    }


def overview(year=None, today=None):
    return [status(p, year, today) for p in points()]


# --- Import der Abrechnungs-Positionen (CSV) -------------------------------------------------
# Je Zeile: einspeisestelle;monat (2026-01 oder 2025-JAHR);anlage;kategorie;menge_kwh … Die eingespeiste Menge steht bei
# der Flexibilitätsprämie (= Marktwert + Verringerung); ohne Flexprämie wird Marktwert + Verringerung genommen.

FLEX, MARKET, REDUCED = "flexibilitätsprämie", "marktwert", "verringerung"


class FeedImportError(ValueError):
    pass


def _csv_float(text):
    text = (text or "").strip()
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    return float(text) if text else 0.0


def parse_csv(data):
    """Bytes → {"points": {Name: {(Jahr, Monat 0–12): {"kwh", "reduced"}}}, "skipped": n}."""
    import csv
    import io
    text = data.decode("utf-8-sig", errors="replace")
    dialect = ";" if text.split("\n", 1)[0].count(";") >= text.split("\n", 1)[0].count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=dialect)
    fields = {(f or "").strip().lower(): f for f in reader.fieldnames or []}
    for needed in ("einspeisestelle", "monat", "kategorie", "menge_kwh"):
        if needed not in fields:
            raise FeedImportError(N_("Die Datei passt nicht: Es fehlen die Spalten einspeisestelle, monat, kategorie und menge_kwh."))
    parts, skipped = {}, 0
    for row in reader:
        get = lambda key: (row.get(fields.get(key, ""), "") or "").strip()      # noqa: E731
        name, period, kind = get("einspeisestelle"), get("monat").upper(), " ".join(get("kategorie").lower().split())
        match = re.fullmatch(r"(\d{4})-(\d{2}|JAHR)", period)
        if not name or not match:
            skipped += 1
            continue
        year, month = int(match.group(1)), 0 if match.group(2) == "JAHR" else int(match.group(2))
        if not 0 <= month <= 12:
            skipped += 1
            continue
        try:
            kwh = _csv_float(get("menge_kwh"))
        except ValueError:
            skipped += 1
            continue
        key = (name, year, month, get("anlage"))
        part = parts.setdefault(key, {"flex": 0.0, "has_flex": False, "market": 0.0, "reduced": 0.0})
        if kind.startswith(FLEX):
            part["flex"] += kwh
            part["has_flex"] = True
        elif kind.startswith(MARKET):
            part["market"] += kwh
        elif kind.startswith(REDUCED):
            part["reduced"] += kwh
    points = {}
    for (name, year, month, _plant), part in parts.items():
        kwh = part["flex"] if part["has_flex"] else part["market"] + part["reduced"]
        if not kwh:
            continue
        slot = points.setdefault(name, {}).setdefault((year, month), {"kwh": 0.0, "reduced": 0.0})
        slot["kwh"] += kwh
        slot["reduced"] += part["reduced"]
    if not points:
        raise FeedImportError(N_("In der Datei stehen keine Einspeisemengen (Kategorie Flexibilitätsprämie oder Marktwert)."))
    return {"points": {n: dict(sorted(v.items())) for n, v in sorted(points.items())}, "skipped": skipped}


def guess(name):
    """Einspeisestelle der App mit gleichem Namen (ohne Groß/Klein) oder None."""
    wanted = name.strip().lower()
    return next((p for p in points() if p.name.strip().lower() == wanted), None)


def apply_import(parsed, mapping):
    """`mapping`: {Name in der Datei: FeedPoint | None}. Vorhandene Monate werden überschrieben. Gibt die Anzahl zurück."""
    count = 0
    for name, values in parsed["points"].items():
        point = mapping.get(name)
        if point is None:
            continue
        existing = {(m.year, m.month): m for m in point.months}
        for (year, month), v in values.items():
            row = existing.get((year, month))
            if row is None:
                row = FeedMonth(feed_point_id=point.id, year=year, month=month, kwh=0)
                db.session.add(row)
            row.kwh = Decimal(str(round(v["kwh"], 1)))
            row.reduced_kwh = Decimal(str(round(v["reduced"], 1))) if v["reduced"] else None
            count += 1
    db.session.flush()
    return count


def price_threshold(hours_per_day, outlook):
    """Ab welchem Preis sich eine Stunde lohnt, wenn je Tag nur `hours_per_day` Volllaststunden übrig sind: der Preis
    der teuersten so vielen Viertelstunden im Ausblick (Day-Ahead und SKVE-Prognose). None ohne Grenze oder Daten."""
    from .biogas import _quarters
    if hours_per_day is None or hours_per_day >= 24 or not outlook:
        return None
    prices = sorted((p for p, _f in _quarters(outlook).values() if p is not None), reverse=True)
    if not prices:
        return None
    take = max(int(round(len(prices) * hours_per_day / 24)), 1)
    return prices[min(take, len(prices)) - 1]


def check(now, done, mark):
    """Aus dem Zeitplan: montags Push, wenn eine Einspeisestelle laut Prognose über ihr Jahresbudget kommt."""
    if now.weekday() != 0:
        return
    from flask import url_for
    from . import push
    from .push import Msg
    today = now.date()
    for s in overview(today=today):
        if s["level"] != "over" or done(f"feed_budget_{s['point'].id}", today):
            continue
        mark(f"feed_budget_{s['point'].id}", today)
        over = (s["diff"] or 0) / 1000
        push.notify(push.editors(), "feed_budget",
                    Msg("{name}: Jahresbudget wird überschritten", name=s["point"].name),
                    Msg("Prognose {mwh} MWh über der Höchstbemessungsleistung – bis 31.12. im Schnitt höchstens {kw} kW.",
                        mwh=f"{over:.0f}", kw=f"{s['avg_kw_left'] or 0:.0f}"),
                    url_for("biogas.overview") + "#budget", tag=f"feed-budget-{s['point'].id}-{today.isoformat()}")
