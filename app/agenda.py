"""Kalender: Wartungen, Lieferungen, Aufgaben, Feiertage und eigene Termine – als Monatsansicht und als iCal-Abo.

Automatische Termine werden bei jedem Abruf berechnet (nichts doppelt gespeichert), eigene Termine liegen in
`CalendarEntry` (Wiederholungen werden beim Abruf aufgefächert). Wartungen nach Zählerstand haben kein Datum und
erscheinen nur in der Liste neben dem Kalender.

Ein Termin ist ein dict: date, end (letzter Tag), time, end_time, kind, uid, title, icon, color, url, note, mine.
"""
import calendar as _cal          # Standardbibliothek (Monatsraster)
import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import url_for

from . import machines, tasks
from .i18n import N_, gettext as _
from .machines import add_months
from .models import CalendarEntry, Machine, MaintenanceTask, PurchaseRequest, Task

ICS_PAST_DAYS = 90
ICS_FUTURE_DAYS = 400
BERLIN = ZoneInfo("Europe/Berlin")


# --- Feiertage (je Bundesland) ----------------------------------------------------

def easter(year):
    """Ostersonntag (gregorianisch, Gauß/Anonymous-Algorithmus)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


# Bundesländer → Kürzel (Einstellung `holiday_state`, leer = keine Feiertage)
STATES = {
    "BW": N_("Baden-Württemberg"), "BY": N_("Bayern"), "BE": N_("Berlin"), "BB": N_("Brandenburg"),
    "HB": N_("Bremen"), "HH": N_("Hamburg"), "HE": N_("Hessen"), "MV": N_("Mecklenburg-Vorpommern"),
    "NI": N_("Niedersachsen"), "NW": N_("Nordrhein-Westfalen"), "RP": N_("Rheinland-Pfalz"), "SL": N_("Saarland"),
    "SN": N_("Sachsen"), "ST": N_("Sachsen-Anhalt"), "SH": N_("Schleswig-Holstein"), "TH": N_("Thüringen"),
}
DEFAULT_STATE = "NI"


def holiday_state():
    from .models import Setting
    value = Setting.all().get("holiday_state")
    return value if value in STATES or value == "-" else DEFAULT_STATE


def holidays(year, state=None):
    """Gesetzliche Feiertage eines Bundeslands: {Datum: Name}. Feiertage nur einzelner Gemeinden
    (z. B. Mariä Himmelfahrt in Teilen Bayerns, Fronleichnam in Teilen Sachsens/Thüringens) fehlen bewusst."""
    state = state or holiday_state()
    if state not in STATES:
        return {}
    e = easter(year)
    days = {
        date(year, 1, 1): N_("Neujahr"), e - timedelta(days=2): N_("Karfreitag"),
        e + timedelta(days=1): N_("Ostermontag"), date(year, 5, 1): N_("Tag der Arbeit"),
        e + timedelta(days=39): N_("Christi Himmelfahrt"), e + timedelta(days=50): N_("Pfingstmontag"),
        date(year, 10, 3): N_("Tag der Deutschen Einheit"),
        date(year, 12, 25): N_("1. Weihnachtstag"), date(year, 12, 26): N_("2. Weihnachtstag"),
    }
    if state in ("BW", "BY", "ST"):
        days[date(year, 1, 6)] = N_("Heilige Drei Könige")
    if (state == "BE" and year >= 2019) or (state == "MV" and year >= 2023):
        days[date(year, 3, 8)] = N_("Internationaler Frauentag")
    if state == "BB":
        days[e] = N_("Ostersonntag")
        days[e + timedelta(days=49)] = N_("Pfingstsonntag")
    if state in ("BW", "BY", "HE", "NW", "RP", "SL"):
        days[e + timedelta(days=60)] = N_("Fronleichnam")
    if state == "SL":
        days[date(year, 8, 15)] = N_("Mariä Himmelfahrt")
    if state == "TH" and year >= 2019:
        days[date(year, 9, 20)] = N_("Weltkindertag")
    if state in ("BB", "MV", "SN", "ST", "TH") or (state in ("HB", "HH", "NI", "SH") and year >= 2018) or year == 2017:
        days[date(year, 10, 31)] = N_("Reformationstag")
    if state in ("BW", "BY", "NW", "RP", "SL"):
        days[date(year, 11, 1)] = N_("Allerheiligen")
    if state == "SN":                                    # Mittwoch vor dem 23. November
        nov23 = date(year, 11, 23)
        days[nov23 - timedelta(days=(nov23.weekday() - 2) % 7 or 7)] = N_("Buß- und Bettag")
    return days


def _holidays(start, end):
    events = []
    state = holiday_state()
    for year in range(start.year, end.year + 1):
        for day, name in holidays(year, state).items():
            if start <= day <= end:
                events.append(_event(day, "feiertag", f"feiertag-{day:%Y%m%d}", _(name), "bi-flag", "light", None, ""))
    return events


# --- Automatische Termine -----------------------------------------------------------

def _event(day, kind, uid, title, icon, color, url, note, end=None, time=None, end_time=None, mine=True):
    return {"date": day, "end": end or day, "time": time, "end_time": end_time, "kind": kind, "uid": uid,
            "title": title, "icon": icon, "color": color, "url": url, "note": note or "", "mine": mine}


def overdue(today=None, user=None):
    """Überfällige Wartungen mit Datum – sie sollen auch im aktuellen Monat sichtbar sein, nicht nur am Fälligkeitstag."""
    today = today or date.today()
    rows = []
    for task in machines.visible(MaintenanceTask.query.join(Machine).filter(Machine.active.is_(True)), user):
        status = machines.task_status(task, today)
        if status["state"] == "overdue" and status["due_on"] is not None:
            rows.append({"task": task, "status": status,
                         "url": url_for("machines.machine_detail", machine_id=task.machine_id) + "#wartungen"})
    return sorted(rows, key=lambda r: r["status"]["due_on"])


def _maintenance(start, end, today, user=None):
    events, undated = [], []
    for task in machines.visible(MaintenanceTask.query.join(Machine).filter(Machine.active.is_(True)), user):
        status = machines.task_status(task, today)
        url = url_for("machines.machine_detail", machine_id=task.machine_id) + "#wartungen"
        if status["due_on"] is None:
            if status["state"] in ("overdue", "soon"):
                undated.append({"task": task, "status": status, "url": url})
            continue
        if start <= status["due_on"] <= end:
            color = "danger" if status["state"] == "overdue" else "warning" if status["state"] == "soon" else "secondary"
            events.append(_event(status["due_on"], "wartung", f"wartung-{task.id}-{status['due_on']:%Y%m%d}",
                                 f"{task.machine.name}: {task.name}", "bi-wrench-adjustable", color, url,
                                 _("Wartung fällig")))
    return events, undated


def _deliveries(start, end):
    events = []
    query = PurchaseRequest.query.filter(PurchaseRequest.status == "bestellt", PurchaseRequest.expected_on.isnot(None),
                                         PurchaseRequest.expected_on >= start, PurchaseRequest.expected_on <= end)
    for req in query:
        note = " · ".join(p for p in (req.quantity, req.supplier, req.machine.name if req.machine else None) if p)
        events.append(_event(req.expected_on, "lieferung", f"bestellung-{req.id}",
                             _("Lieferung: {title}", title=req.title), "bi-truck", "primary",
                             url_for("purchases.purchase_detail", req_id=req.id), note))
    return events


def _tasks(start, end, user):
    """Offene Aufgaben mit Termin. Admins sehen alle, sonst die eigenen und die für alle."""
    query = tasks.visible_query(None if user is None or user.is_admin else user)
    query = query.filter(Task.done_at.is_(None), Task.due_on.isnot(None), Task.due_on >= start, Task.due_on <= end)
    events = []
    for t in query:
        names = ", ".join(u.name for u in t.assignees) if t.assignees else _("alle")
        mine = user is None or not t.assignees or user in t.assignees
        events.append(_event(t.due_on, "aufgabe", f"aufgabe-{t.id}", t.title, "bi-list-check",
                             "danger" if t.important else "success", url_for("tasks.task_detail", task_id=t.id),
                             names, mine=mine))
    return events


def _waiting(start, end, today):
    """Laufende Pflanzenschutz-Wartezeiten: „erntefähig ab …“ am Ende der Wartezeit (für alle sichtbar).
    Ist der Schlag geerntet oder die Wartezeit vorbei, verschwindet der Termin."""
    from . import fielddoc
    from .models import FieldMeasure, PlotYear
    years = {today.year, start.year, end.year}
    entries = (PlotYear.query.filter(PlotYear.year.in_(years))
               .join(FieldMeasure, FieldMeasure.plot_year_id == PlotYear.id)
               .filter(FieldMeasure.kind == "pflanzenschutz").distinct().all())
    known = fielddoc.products() if entries else {}
    events = []
    for entry in entries:
        info = fielddoc.protection(entry, known, today)
        if not info or not info["running"] or not start <= info["until"] <= end:
            continue
        crop = entry.shown_crop
        events.append(_event(info["until"], "wartezeit", f"wartezeit-{entry.id}-{info['until']:%Y%m%d}",
                             _("{plot} erntefähig", plot=entry.plot.name), "bi-hourglass-split", "info",
                             url_for("fields.field_detail", plot_id=entry.plot_id),
                             " · ".join(p for p in (crop, _("Wartezeit von {name}", name=info["last"].name)) if p)))
    return events


# --- Eigene Termine -------------------------------------------------------------------

def _advance(day, repeat, n):
    if repeat == "woechentlich":
        return day + timedelta(days=7 * n)
    if repeat == "monatlich":
        return add_months(day, n)
    return add_months(day, 12 * n)          # jährlich


def occurrences(entry, start, end):
    """Anfangstage aller Vorkommen, die den Zeitraum berühren."""
    length = timedelta(days=entry.days - 1)
    if not entry.repeat:
        return [entry.start_date] if entry.start_date <= end and entry.start_date + length >= start else []
    days, n = [], 0
    while n < 2000:
        day = _advance(entry.start_date, entry.repeat, n)
        if day > end:
            break
        if day + length >= start:
            days.append(day)
        n += 1
    return days


def _entries(start, end, user):
    events = []
    query = CalendarEntry.query.filter(CalendarEntry.start_date <= end)
    for entry in query:
        for day in occurrences(entry, start, end):
            names = ", ".join(u.name for u in entry.participants)
            note = " · ".join(p for p in (entry.location, names, entry.note) if p)
            events.append(_event(day, "eintrag", f"eintrag-{entry.id}-{day:%Y%m%d}", entry.title, entry.icon,
                                 entry.color, url_for("agenda.entry_detail", entry_id=entry.id), note,
                                 end=day + timedelta(days=entry.days - 1), time=entry.start_time,
                                 end_time=entry.end_time, mine=user is None or entry.is_for(user)))
    return events


# --- Zusammenführen -------------------------------------------------------------------

def events(start, end, today=None, user=None, mine_only=False):
    """Alle Termine, die den Zeitraum `start`–`end` berühren, sortiert; dazu die Wartungen ohne Datum."""
    today = today or date.today()
    maintenance, undated = _maintenance(start, end, today, user)
    items = maintenance + _deliveries(start, end) + _tasks(start, end, user) + _entries(start, end, user) \
        + _waiting(start, end, today) + _holidays(start, end)
    if mine_only:
        items = [e for e in items if e["mine"] or e["kind"] == "feiertag"]
    order = {"feiertag": 0, "eintrag": 1, "aufgabe": 2, "wartung": 3, "lieferung": 4, "wartezeit": 5}
    items.sort(key=lambda e: (e["date"], order.get(e["kind"], 9), e["time"] is not None, e["time"] or 0, e["title"]))
    return items, undated


def month(year, month_, today=None, user=None, mine_only=False):
    """Wochen (Mo–So) des Monats mit den Terminen je Tag – für die Monatsansicht."""
    today = today or date.today()
    weeks = _cal.Calendar(firstweekday=0).monthdatescalendar(year, month_)
    items, undated = events(weeks[0][0], weeks[-1][-1], today, user, mine_only)
    by_day = {}
    for e in items:
        day = max(e["date"], weeks[0][0])
        while day <= min(e["end"], weeks[-1][-1]):             # mehrtägige Termine an jedem Tag zeigen
            by_day.setdefault(day, []).append(e)
            day += timedelta(days=1)
    first, last = date(year, month_, 1), date(year, month_, _cal.monthrange(year, month_)[1])
    return {
        "weeks": [[{"date": d, "in_month": d.month == month_, "today": d == today, "events": by_day.get(d, []),
                    "holiday": any(e["kind"] == "feiertag" for e in by_day.get(d, []))}
                   for d in week] for week in weeks],
        "entries": [e for e in items if e["date"] <= last and e["end"] >= first],
        "undated": undated,
        "overdue": overdue(today, user),
    }


# --- Abo-Link (iCal) ----------------------------------------------------------

def ensure_token(user):
    if not user.calendar_token:
        user.calendar_token = secrets.token_urlsafe(24)
    return user.calendar_token


def renew_token(user):
    user.calendar_token = secrets.token_urlsafe(24)
    return user.calendar_token


def _escape(text):
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    """Zeilen nach RFC 5545 auf 75 Bytes umbrechen (ohne UTF-8-Zeichen zu zerteilen)."""
    out, current = [], b""
    for ch in line:
        encoded = ch.encode()
        if len(current) + len(encoded) > (75 if not out else 74):
            out.append(current)
            current = b""
        current += encoded
    out.append(current)
    return "\r\n ".join(part.decode() for part in out)


def _utc(day, time_):
    return datetime.combine(day, time_, BERLIN).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _when(e):
    """DTSTART/DTEND: ganztägig als DATE, mit Uhrzeit in UTC (ohne VTIMEZONE, versteht jeder Kalender)."""
    if e["time"] is None:
        return [f"DTSTART;VALUE=DATE:{e['date']:%Y%m%d}", f"DTEND;VALUE=DATE:{e['end'] + timedelta(days=1):%Y%m%d}"]
    end_time = e["end_time"]
    if end_time is None or (e["end"] == e["date"] and end_time <= e["time"]):
        end_dt = datetime.combine(e["end"], e["time"]) + timedelta(hours=1)
        end_day, end_time = end_dt.date(), end_dt.time()
    else:
        end_day = e["end"]
    return [f"DTSTART:{_utc(e['date'], e['time'])}", f"DTEND:{_utc(end_day, end_time)}"]


def ics(name, base_url, user=None, today=None):
    """iCal-Datei (für Google-, Apple- und Outlook-Kalender) – mit den Aufgaben des Benutzers."""
    today = today or date.today()
    items, _undated = events(today - timedelta(days=ICS_PAST_DAYS), today + timedelta(days=ICS_FUTURE_DAYS),
                             today, user)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    host = base_url.split("//", 1)[-1].split("/", 1)[0] or "lagersystem"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Lagersystem//Kalender//DE", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_escape(name)}", "X-WR-TIMEZONE:Europe/Berlin",
             "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    for e in items:
        lines += ["BEGIN:VEVENT", f"UID:{e['uid']}@{host}", f"DTSTAMP:{stamp}", *_when(e),
                  f"SUMMARY:{_escape(e['title'])}", f"DESCRIPTION:{_escape(e['note'])}"]
        if e["url"]:
            lines.append(f"URL:{base_url.rstrip('/')}{e['url']}")
        lines += ["TRANSP:TRANSPARENT" if e["time"] is None else "TRANSP:OPAQUE", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def user_by_token(token):
    from .models import User
    if not token or len(token) < 20:
        return None
    user = User.query.filter_by(calendar_token=token).first()
    return user if user is not None and user.active else None


# --- Eigene Termine speichern (committet nicht) ------------------------------------

def save_entry(entry, user, title, kind, start_date, end_date=None, start_time=None, end_time=None, location=None,
               note=None, repeat=None, participants=()):
    from .extensions import db
    from .models import CALENDAR_KINDS, CALENDAR_REPEATS
    from .storage import StorageError
    title = (title or "").strip()
    if not title:
        raise StorageError(_("Bitte einen Titel angeben."))
    if start_date is None:
        raise StorageError(_("Bitte das Datum angeben."))
    if end_date is not None and end_date < start_date:
        raise StorageError(_("Das Ende liegt vor dem Anfang."))
    if end_date is not None and (end_date - start_date).days > 366:
        raise StorageError(_("Ein Termin kann höchstens ein Jahr dauern."))
    if start_time is None:
        end_time = None
    elif end_time is not None and (end_date in (None, start_date)) and end_time < start_time:
        raise StorageError(_("Das Ende liegt vor dem Anfang."))
    entry.title = title[:200]
    entry.kind = kind if kind in CALENDAR_KINDS else "termin"
    entry.start_date, entry.end_date = start_date, end_date if end_date and end_date != start_date else None
    entry.start_time, entry.end_time = start_time, end_time
    entry.location, entry.note = location or None, note or None
    entry.repeat = repeat if repeat in CALENDAR_REPEATS else None
    entry.participants = list(participants)
    if entry.created_by_id is None and user is not None:
        entry.created_by_id = user.id
    db.session.add(entry)
    db.session.flush()
    return entry
