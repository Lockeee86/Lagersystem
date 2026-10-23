"""Kalender: fällige Wartungen und erwartete Lieferungen (Bestellungen) – als Monatsansicht und als iCal-Abo.

Termine werden nicht gespeichert, sondern bei jedem Abruf aus Wartungen und Bestellungen berechnet.
Wartungen nach Zählerstand haben kein Datum und erscheinen nur in der Liste neben dem Kalender.
"""
import calendar as _cal          # Standardbibliothek (Monatsraster)
import secrets
from datetime import date, datetime, timedelta, timezone

from flask import url_for

from . import machines
from .i18n import gettext as _
from .models import Machine, MaintenanceTask, PurchaseRequest

ICS_PAST_DAYS = 90
ICS_FUTURE_DAYS = 400


def overdue(today=None):
    """Überfällige Wartungen mit Datum – sie sollen auch im aktuellen Monat sichtbar sein, nicht nur am Fälligkeitstag."""
    today = today or date.today()
    rows = []
    for task in MaintenanceTask.query.join(Machine).filter(Machine.active.is_(True)):
        status = machines.task_status(task, today)
        if status["state"] == "overdue" and status["due_on"] is not None:
            rows.append({"task": task, "status": status,
                         "url": url_for("machines.machine_detail", machine_id=task.machine_id) + "#wartungen"})
    return sorted(rows, key=lambda r: r["status"]["due_on"])


def _maintenance(start, end, today):
    events, undated = [], []
    for task in MaintenanceTask.query.join(Machine).filter(Machine.active.is_(True)):
        status = machines.task_status(task, today)
        url = url_for("machines.machine_detail", machine_id=task.machine_id) + "#wartungen"
        if status["due_on"] is None:
            if status["state"] in ("overdue", "soon"):
                undated.append({"task": task, "status": status, "url": url})
            continue
        if start <= status["due_on"] <= end:
            events.append({
                "date": status["due_on"], "kind": "wartung", "uid": f"wartung-{task.id}-{status['due_on']:%Y%m%d}",
                "title": f"{task.machine.name}: {task.name}", "icon": "bi-wrench-adjustable",
                "color": "danger" if status["state"] == "overdue" else "warning" if status["state"] == "soon" else "secondary",
                "url": url, "note": _("Wartung fällig"),
            })
    return events, undated


def _deliveries(start, end):
    events = []
    query = PurchaseRequest.query.filter(PurchaseRequest.status == "bestellt", PurchaseRequest.expected_on.isnot(None),
                                         PurchaseRequest.expected_on >= start, PurchaseRequest.expected_on <= end)
    for req in query:
        note = " · ".join(p for p in (req.quantity, req.supplier, req.machine.name if req.machine else None) if p)
        events.append({
            "date": req.expected_on, "kind": "lieferung", "uid": f"bestellung-{req.id}",
            "title": _("Lieferung: {title}", title=req.title), "icon": "bi-truck", "color": "primary",
            "url": url_for("purchases.purchase_detail", req_id=req.id), "note": note,
        })
    return events


def events(start, end, today=None):
    """Alle Termine zwischen `start` und `end` (einschließlich), nach Datum sortiert, und die Wartungen ohne Datum."""
    today = today or date.today()
    maintenance, undated = _maintenance(start, end, today)
    items = sorted(maintenance + _deliveries(start, end), key=lambda e: (e["date"], e["kind"], e["title"]))
    return items, undated


def month(year, month_, today=None):
    """Wochen (Mo–So) des Monats mit den Terminen je Tag – für die Monatsansicht."""
    today = today or date.today()
    weeks = _cal.Calendar(firstweekday=0).monthdatescalendar(year, month_)
    items, undated = events(weeks[0][0], weeks[-1][-1], today)
    by_day = {}
    for e in items:
        by_day.setdefault(e["date"], []).append(e)
    return {
        "weeks": [[{"date": d, "in_month": d.month == month_, "today": d == today, "events": by_day.get(d, [])}
                   for d in week] for week in weeks],
        "entries": [e for e in items if e["date"].month == month_ and e["date"].year == year],
        "undated": undated,
        "overdue": overdue(today),
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


def ics(name, base_url, today=None):
    """iCal-Datei mit ganztägigen Terminen (für Google-, Apple- und Outlook-Kalender)."""
    today = today or date.today()
    items, _undated = events(today - timedelta(days=ICS_PAST_DAYS), today + timedelta(days=ICS_FUTURE_DAYS), today)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    host = base_url.split("//", 1)[-1].split("/", 1)[0] or "lagersystem"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Lagersystem//Kalender//DE", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_escape(name)}", "X-WR-TIMEZONE:Europe/Berlin",
             "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    for e in items:
        lines += [
            "BEGIN:VEVENT", f"UID:{e['uid']}@{host}", f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{e['date']:%Y%m%d}", f"DTEND;VALUE=DATE:{e['date'] + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_escape(e['title'])}", f"DESCRIPTION:{_escape(e['note'])}",
            f"URL:{base_url.rstrip('/')}{e['url']}", "TRANSP:TRANSPARENT", "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def user_by_token(token):
    from .models import User
    if not token or len(token) < 20:
        return None
    user = User.query.filter_by(calendar_token=token).first()
    return user if user is not None and user.active else None

