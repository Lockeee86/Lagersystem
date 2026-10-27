"""Alte Legedaten aus einer Excel-Liste übernehmen (Hühner → Eier → „Alte Daten importieren“).

Erwartet je Blatt eine Kopfzeile mit „Datum“ und „Eier“, wahlweise „kaputt“, „Windei“, „Tot“ und eine Bemerkung.
„Eier“ sind die guten Eier – kaputte und Windeier kommen dazu (gelegt = Eier + kaputt + Windei, davon kaputt).
„Tot“ wird ein Abgang „verendet“. Importierte Einträge (`EggEntry.imported`) zählen in Auswertung und Legeleistung,
aber nicht zum heutigen Vorrat (die Eier sind längst verteilt). Tage, für die es schon Einträge gibt, werden
übersprungen. `apply` committet nicht.
"""
import io
from datetime import date, datetime

from .extensions import db
from .i18n import gettext as _
from .models import EggEntry, FlockEvent

NOTE_MAX = 200


class EggImportError(ValueError):
    pass


def _header(row):
    """{Feld: Spalte} aus einer Kopfzeile oder None, wenn es keine ist."""
    cols = {}
    for i, cell in enumerate(row):
        name = str(cell or "").strip().lower()
        if not name:
            continue
        if name == "datum":
            cols["day"] = i
        elif name == "eier" or (name.startswith("eier") and "eggs" not in cols and "kaputt" not in name):
            cols.setdefault("eggs", i)
        elif "kaputt" in name or "bruch" in name:
            cols["cracked"] = i
        elif "windei" in name:
            cols["wind"] = i
        elif name in ("tot", "tote", "verendet") or name.startswith("tot"):
            cols["dead"] = i
        elif "bemerk" in name or "notiz" in name or "behandlung" in name or "reinigung" in name:
            cols["note"] = i
    return cols if "day" in cols and "eggs" in cols else None


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _count(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    try:
        number = float(str(value).replace(",", "."))
    except ValueError:
        return None
    return int(number) if number >= 0 and number == int(number) else None


def parse_file(data):
    """Excel (xlsx) → {"rows": [{day, eggs, cracked, dead, note, sheet}], "warnings": [...]}."""
    import openpyxl
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:                                        # noqa: BLE001 – kaputte/fremde Datei
        raise EggImportError(_("Die Datei lässt sich nicht lesen. Bitte eine Excel-Datei (.xlsx) wählen."))
    rows, warnings, seen = [], [], set()
    for sheet in book.worksheets:
        cols = None
        for number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            if cols is None:
                cols = _header(row)
                continue
            get = lambda key: row[cols[key]] if key in cols and cols[key] < len(row) else None  # noqa: E731
            day = _day(get("day"))
            if day is None:
                continue
            values = {key: _count(get(key)) for key in ("eggs", "cracked", "wind", "dead")}
            bad = [key for key, v in values.items() if v is None]
            if bad:
                warnings.append(_("{sheet}, Zeile {row}: keine Zahl – übersprungen.", sheet=sheet.title, row=number))
                continue
            note = " ".join(str(get("note") or "").split())[:NOTE_MAX] or None
            if not (values["eggs"] or values["cracked"] or values["wind"] or values["dead"] or note):
                continue
            if day in seen:
                warnings.append(_("{date} steht doppelt in der Datei – nur der erste Eintrag zählt.",
                                  date=day.strftime("%d.%m.%Y")))
                continue
            seen.add(day)
            broken = values["cracked"] + values["wind"]
            rows.append({"day": day, "eggs": values["eggs"] + broken, "cracked": broken, "dead": values["dead"],
                         "note": note, "sheet": sheet.title})
        if cols is None:
            warnings.append(_("Blatt „{sheet}“: keine Kopfzeile mit „Datum“ und „Eier“ gefunden.", sheet=sheet.title))
    if not rows:
        raise EggImportError(_("In der Datei stehen keine Legedaten (Spalten „Datum“ und „Eier“)."))
    rows.sort(key=lambda r: r["day"])
    return {"rows": rows, "warnings": warnings}


def plan(parsed, today=None):
    """Vorschau: je Monat Tage/Eier/kaputt/tot, schon vorhandene Tage, und ob die Herde für die Abgänge reicht."""
    from .chickens import flock_size
    today = today or date.today()
    existing = {d for (d,) in db.session.query(EggEntry.day).distinct()}
    months, skipped, future = {}, [], []
    for row in parsed["rows"]:
        if row["day"] > today:
            future.append(row)
            continue
        if row["day"] in existing:
            skipped.append(row)
            continue
        key = row["day"].replace(day=1)
        m = months.setdefault(key, {"month": key, "days": 0, "eggs": 0, "cracked": 0, "dead": 0})
        m["days"] += 1 if row["eggs"] else 0
        m["eggs"] += row["eggs"]
        m["cracked"] += row["cracked"]
        m["dead"] += row["dead"]
    rows = sorted(months.values(), key=lambda m: m["month"])
    first = parsed["rows"][0]["day"]
    dead = sum(m["dead"] for m in rows)
    return {"months": rows, "skipped": skipped, "future": future, "first": first, "last": parsed["rows"][-1]["day"],
            "eggs": sum(m["eggs"] for m in rows), "cracked": sum(m["cracked"] for m in rows), "dead": dead,
            "flock_at_start": flock_size(first), "needs_flock": dead > 0 and flock_size(first) < dead}


def apply(parsed, user, start_flock=None, breed=None, age_months=None, today=None):
    """Übernehmen. `start_flock`: Hühner, die am ersten Tag schon da waren (wird ein Zugang). Gibt Zahlen zurück."""
    from .chickens import add_flock_event, flock_size
    today = today or date.today()
    existing = {d for (d,) in db.session.query(EggEntry.day).distinct()}
    stats = {"days": 0, "eggs": 0, "dead": 0, "dead_skipped": 0}
    first = parsed["rows"][0]["day"]
    if start_flock:
        add_flock_event(first, "zugang", start_flock, user, breed=breed, age_months=age_months,
                        note="Import alte Legedaten")
    for row in parsed["rows"]:
        if row["day"] > today or row["day"] in existing:
            continue
        if row["eggs"] or row["note"]:
            db.session.add(EggEntry(day=row["day"], eggs=row["eggs"], cracked=row["cracked"], note=row["note"],
                                    user_id=user.id if user else None, imported=True))
            stats["days"] += 1 if row["eggs"] else 0
            stats["eggs"] += row["eggs"]
        if row["dead"]:
            db.session.flush()
            if flock_size(row["day"]) >= row["dead"]:
                db.session.add(FlockEvent(day=row["day"], change=-row["dead"], reason="verendet",
                                          note="Import alte Legedaten", user_id=user.id if user else None))
                stats["dead"] += row["dead"]
            else:
                stats["dead_skipped"] += row["dead"]
    db.session.flush()
    return stats
