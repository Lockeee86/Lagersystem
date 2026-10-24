"""Biogas: BHKW-Motoren aus der Speicher-Kraftwerk-API (SKVE) – Laufstunden, Wartung und Übersicht.

Nur lesende Abrufe (wie der SKVE-Collector): `x-api-key`-Header, Zeitraum als Unix-Sekunden.
- Fahrplan je Motor: /module/data-service/chp/{id}/schedule → Viertelstunden mit Leistung in kW
- Day-Ahead-Preis:   /module/data-service/prices/DAA          → €/MWh je Viertelstunde
- Gasspeicher:       /module/gas-storage/{id}/gas-storages    → Füllstand in % (Ist + Prognose)

`sync` läuft einmal am Tag aus dem Zeitplan: rechnet je verknüpftem Motor (Maschine mit `skve_chp_id`) die
abgeschlossenen Tage zu `ChpDay` zusammen und schreibt den Betriebsstundenzähler fort – ausgehend vom zuletzt von Hand
abgelesenen Stand (`Machine.skve_base_counter/_day`). Über den Zähler greifen die normalen Wartungsintervalle.
`live` holt die aktuelle Lage für die Übersichtsseite (zwischengespeichert). In Tests `_get` ersetzen (kein Netz).
"""
import json
import math
from contextlib import nullcontext
import threading
import time as clock
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from flask import current_app, has_app_context
from sqlalchemy import func

from .extensions import db
from .models import ChpDay, Machine, Setting, utcnow
from .utils import local_tz

RUN_KW = 1.0                  # ab dieser Leistung läuft der Motor
MAX_STEP_H = 1.0              # größere Lücken zwischen zwei Werten zählen nur bis hierhin
FIRST_SYNC_DAYS = 60          # beim ersten Abruf so viele Tage zurück
OVERLAP_DAYS = 2              # die letzten Tage jedes Mal neu rechnen (Fahrplan kann sich nachträglich ändern)
CHUNK_DAYS = 14               # größere Zeiträume liefert die API nicht zuverlässig am Stück
SYNC_HOUR = 2                 # ab dieser Uhrzeit (Ortszeit) ist der Vortag fertig
LIVE_TTL = 600                # Sekunden, die die Übersicht zwischengespeichert wird
FORECAST_DAYS = 28            # Grundlage für die Wartungsprognose (Ø Stunden je Tag)
STATUS_KEY = "biogas_status"
SYNC_KEY = "biogas_sync_day"

_cache = {}
_lock = threading.Lock()


class SkveError(Exception):
    pass


def configured():
    return bool(current_app.config.get("SKVE_API_KEY"))


def linked_machines():
    return (Machine.query.filter(Machine.skve_chp_id.isnot(None), Machine.active.is_(True))
            .order_by(Machine.name).all())


# --- Abruf ------------------------------------------------------------------

def _get(path, start, end):
    """GET mit API-Schlüssel → JSON. Die API nennt bei zu frühem Start das früheste erlaubte Datum – dann einmal neu."""
    params = {"start_date": int(start.timestamp()), "end_date": int(end.timestamp())}
    for _attempt in range(2):
        url = f"{current_app.config['SKVE_API_BASE']}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"x-api-key": current_app.config["SKVE_API_KEY"],
                                                       "Accept": "application/json",
                                                       "User-Agent": "Lagersystem (Hof-App)"})
        try:
            with urllib.request.urlopen(request, timeout=current_app.config["SKVE_TIMEOUT"]) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            body = e.read(300).decode("utf-8", "replace")
            if e.code == 401:
                raise SkveError("401 – API-Schlüssel ungültig") from e
            marker = "greater than or equal to "
            if marker in body:
                digits = "".join(ch for ch in body.split(marker, 1)[1][:12] if ch.isdigit())
                if digits and int(digits) > params["start_date"]:
                    if int(digits) >= params["end_date"]:
                        return {}
                    params["start_date"] = int(digits)
                    continue
            raise SkveError(f"{e.code} für {path}") from e
        except (OSError, ValueError) as e:
            raise SkveError(str(e)) from e
    return {}


def _series(payload):
    """[(UTC-Zeit, Wert)] aus {"times", "values"} oder einer verschachtelten {"timestamps", "values"}-Reihe."""
    if not isinstance(payload, dict):
        return []
    if "times" in payload and "values" in payload:
        pairs = zip(payload["times"], payload["values"])
    else:
        def find(node):
            if isinstance(node, dict):
                if "timestamps" in node and "values" in node:
                    return zip(node["timestamps"], node["values"])
                for value in node.values():
                    found = find(value)
                    if found is not None:
                        return found
            return None
        pairs = find(payload) or []
    result = []
    for ts, value in pairs:
        try:
            result.append((datetime.fromtimestamp(int(ts), timezone.utc), float(value) if value is not None else None))
        except (TypeError, ValueError, OverflowError, OSError):
            continue
    return sorted(result)


def _fetch(path, start, end, extract=_series):
    rows = []
    cursor = start
    while cursor < end:
        upper = min(cursor + timedelta(days=CHUNK_DAYS), end)
        rows.extend(extract(_get(path, cursor, upper)))
        cursor = upper
    unique = {}
    for ts, value in rows:
        unique[ts] = value
    return sorted(unique.items())


def chp_points(chp_id, start, end):
    return _fetch(f"/module/data-service/chp/{int(chp_id)}/schedule", start, end)


def prices(start, end):
    return dict(_fetch("/module/data-service/prices/DAA", start, end))


def _gas_series(payload):
    view = ((payload or {}).get("timeSeries") or {}).get("customer_view") or {}
    return _series({"times": view.get("timestamps") or [], "values": view.get("values") or []})


def gas_levels(start, end):
    storage = current_app.config.get("SKVE_GAS_STORAGE_ID")
    if not storage:
        return []
    return _fetch(f"/module/gas-storage/{urllib.parse.quote(storage)}/gas-storages", start, end, _gas_series)


# --- Rechnen ----------------------------------------------------------------

def _steps(points, until=None):
    """(Zeit, Dauer in h, kW) je Wert; die Dauer reicht bis zum nächsten Wert (höchstens MAX_STEP_H)."""
    for i, (ts, kw) in enumerate(points):
        if until is not None and ts >= until:
            break
        nxt = points[i + 1][0] if i + 1 < len(points) else ts + timedelta(minutes=15)
        if until is not None:
            nxt = min(nxt, until)
        yield ts, min((nxt - ts).total_seconds() / 3600, MAX_STEP_H), kw or 0.0


def _price_at(price_map, ts):
    if not price_map:
        return None
    quarter = ts.replace(minute=ts.minute - ts.minute % 15, second=0, microsecond=0)
    return price_map.get(quarter, price_map.get(quarter.replace(minute=0)))


def aggregate(points, price_map=None, until=None):
    """{Tag (Ortszeit): {"hours", "kwh", "revenue", "starts"}} aus den Fahrplanwerten."""
    tz = local_tz()
    days, running = {}, False
    for ts, hours, kw in _steps(points, until):
        day = ts.astimezone(tz).date()
        row = days.setdefault(day, {"hours": 0.0, "kwh": 0.0, "revenue": None, "starts": 0})
        on = kw >= RUN_KW
        if on:
            row["hours"] += hours
            row["kwh"] += kw * hours
            price = _price_at(price_map, ts)
            if price is not None:
                row["revenue"] = (row["revenue"] or 0.0) + kw * hours / 1000 * price
            if not running:
                row["starts"] += 1
        running = on
    return days


def _midnight(day):
    return datetime.combine(day, time()).replace(tzinfo=local_tz()).astimezone(timezone.utc)


def _store_days(machine, days, first, last):
    """ChpDay für jeden Tag first..last schreiben (Tage ohne Werte = 0 Stunden)."""
    existing = {row.day: row for row in machine.chp_days.filter(ChpDay.day >= first, ChpDay.day <= last)}
    day = first
    while day <= last:
        values = days.get(day, {"hours": 0.0, "kwh": 0.0, "revenue": None, "starts": 0})
        row = existing.get(day) or ChpDay(machine=machine, day=day)
        row.hours = Decimal(str(round(min(values["hours"], 24), 2)))
        row.kwh = Decimal(str(round(values["kwh"], 1)))
        row.revenue_eur = Decimal(str(round(values["revenue"], 2))) if values["revenue"] is not None else None
        row.starts = values["starts"]
        db.session.add(row)
        day += timedelta(days=1)
    db.session.flush()


def update_counter(machine, last_day):
    """Zählerstand = abgelesener Stand + SKVE-Stunden der Tage danach. Ohne Ablesung zählt es ab heute weiter."""
    if machine.skve_base_day is None:
        machine.skve_base_counter = Decimal(machine.counter or 0)
        machine.skve_base_day = last_day
        if not machine.counter_unit:
            machine.counter_unit = "h"
        return
    hours = (db.session.query(func.coalesce(func.sum(ChpDay.hours), 0))
             .filter(ChpDay.machine_id == machine.id, ChpDay.day > machine.skve_base_day).scalar())
    counter = int(Decimal(machine.skve_base_counter or 0) + Decimal(hours or 0))
    if machine.counter is None or counter > machine.counter:
        machine.counter, machine.counter_at = counter, utcnow()


def sync_machine(machine, today, price_map=None):
    """Abgeschlossene Tage eines Motors holen und den Zähler fortschreiben. Gibt die Zahl der Tage zurück."""
    last = db.session.query(func.max(ChpDay.day)).filter(ChpDay.machine_id == machine.id).scalar()
    first = (last - timedelta(days=OVERLAP_DAYS)) if last else today - timedelta(days=FIRST_SYNC_DAYS)
    yesterday = today - timedelta(days=1)
    if first > yesterday:
        return 0
    start, end = _midnight(first), _midnight(today)
    points = chp_points(machine.skve_chp_id, start, end)
    if price_map is None:
        price_map = prices(start, end)
    if points:
        _store_days(machine, aggregate(points, price_map, until=end), first, yesterday)
    update_counter(machine, yesterday)
    return (yesterday - first).days + 1 if points else 0


def _status(ok, message):
    Setting.put(STATUS_KEY, json.dumps({"at": utcnow().isoformat(), "ok": ok, "message": message[:300]}))


def status():
    setting = db.session.get(Setting, STATUS_KEY)
    try:
        data = json.loads(setting.value) if setting and setting.value else None
        if data:
            data["at"] = datetime.fromisoformat(data["at"])
        return data
    except (ValueError, TypeError, KeyError):
        return None


def sync(app, now=None, force=False):
    """Zeitplan-Aufgabe: einmal am Tag nach SYNC_HOUR alle verknüpften Motoren abrufen (`force`: sofort,
    z. B. über „Jetzt abrufen“ – dann im schon offenen Kontext der Anfrage)."""
    inside = has_app_context() and current_app._get_current_object() is app
    with nullcontext() if inside else app.app_context():
        if not configured():
            return None
        now = (now or datetime.now(timezone.utc)).astimezone(local_tz())
        today = now.date()
        done = db.session.get(Setting, SYNC_KEY)
        if not force and (now.hour < SYNC_HOUR or (done and done.value == today.isoformat())):
            return None
        machines = linked_machines()
        days = 0
        try:
            if machines:
                first = today - timedelta(days=FIRST_SYNC_DAYS)
                price_map = prices(_midnight(first), _midnight(today))
                for machine in machines:
                    days += sync_machine(machine, today, price_map)
            _status(True, f"{len(machines)} Motoren, {days} Tage")
            Setting.put(SYNC_KEY, today.isoformat())
            db.session.commit()
        except SkveError as e:
            db.session.rollback()
            _status(False, str(e))
            db.session.commit()
            app.logger.warning("SKVE-Abruf: %s", e)
        clear_cache()
        return days


# --- Übersicht ----------------------------------------------------------------

def clear_cache():
    with _lock:
        _cache.clear()


def _hourly(points, start, hours):
    """Stundenmittel der Leistung ab `start` für `hours` Stunden: [(Ortszeit, kW)]."""
    tz = local_tz()
    buckets = {}
    for ts, kw in points:
        if start <= ts < start + timedelta(hours=hours):
            key = ts.replace(minute=0, second=0, microsecond=0)
            buckets.setdefault(key, []).append(kw or 0.0)
    result = []
    for h in range(hours):
        key = start + timedelta(hours=h)
        values = buckets.get(key)
        result.append((key.astimezone(tz), sum(values) / len(values) if values else None))
    return result


def _current(series, now):
    past = [(ts, v) for ts, v in series if ts <= now and v is not None]
    return past[-1][1] if past else None


def live(now=None, cached_only=False):
    """Aktuelle Lage: je Motor Leistung jetzt, heute erzeugt, Fahrplan der nächsten 24 h; Gasspeicher; Preis.
    `cached_only` (Startseite): nur den Zwischenspeicher lesen, nie auf die API warten – sonst None."""
    if not configured():
        return None if cached_only else {"configured": False}
    real_now = now is None
    now = now or datetime.now(timezone.utc)
    with _lock:
        cached = _cache.get("live")
        if cached_only:
            return cached[1] if cached and clock.monotonic() - cached[0] < 3 * LIVE_TTL else None
        if real_now and cached and clock.monotonic() - cached[0] < LIVE_TTL:
            return cached[1]
    today = now.astimezone(local_tz()).date()
    start, end = _midnight(today), now + timedelta(hours=26)
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    result = {"configured": True, "error": None, "motors": [], "gas": None, "price_now": None, "at": now}
    try:
        price_map = prices(start, end)
        result["price_now"] = _current(sorted(price_map.items()), now)
        result["prices"] = _hourly(sorted(price_map.items()), next_hour, 24)
        for machine in linked_machines():
            points = chp_points(machine.skve_chp_id, start, end)
            today_rows = aggregate(points, price_map, until=now).get(today, {})
            kw_now = _current(points, now)
            result["motors"].append({
                "machine_id": machine.id, "kw_now": kw_now, "running": (kw_now or 0) >= RUN_KW,
                "today_kwh": today_rows.get("kwh", 0.0), "today_hours": today_rows.get("hours", 0.0),
                "plan": _hourly(points, next_hour, 24),
            })
        levels = gas_levels(now - timedelta(hours=6), end)
        if levels:
            ahead = [v for ts, v in levels if now <= ts <= now + timedelta(hours=24) and v is not None]
            result["gas"] = {"now": _current(levels, now), "min": min(ahead) if ahead else None,
                             "max": max(ahead) if ahead else None, "plan": _hourly(levels, next_hour, 24)}
    except SkveError as e:
        result["error"] = str(e)
    with _lock:
        if real_now:
            _cache["live"] = (clock.monotonic(), result)
    return result


def refresh_live(app):
    """Zeitplan: den Zwischenspeicher der Übersicht frisch halten, damit die Startseite sofort Werte hat."""
    inside = has_app_context() and current_app._get_current_object() is app
    with nullcontext() if inside else app.app_context():
        if configured() and linked_machines():
            live()


def dashboard(today=None):
    """Kurzübersicht für die Startseite: je BHKW Zustand, Leistung, Stunden gestern und die nächste Wartung."""
    today = today or date.today()
    machines_ = (Machine.query.filter(Machine.active.is_(True),
                                      (Machine.category == "bhkw") | Machine.skve_chp_id.isnot(None))
                 .order_by(Machine.name).all())
    if not machines_:
        return None
    current = live(cached_only=True) or {}
    now_by_id = {m["machine_id"]: m for m in current.get("motors") or []}
    yesterday = {row.machine_id: row for row in
                 ChpDay.query.filter(ChpDay.day == today - timedelta(days=1),
                                     ChpDay.machine_id.in_([m.id for m in machines_]))}
    rows = []
    for machine in machines_:
        forecast = maintenance_forecast(machine, today)
        known = [f for f in forecast if f["left"] is not None]
        rows.append({"machine": machine, "now": now_by_id.get(machine.id), "yesterday": yesterday.get(machine.id),
                     "next": known[0] if known else (forecast[0] if forecast else None)})
    return {"rows": rows, "live": bool(now_by_id),
            "kw": sum(r["now"]["kw_now"] or 0 for r in rows if r["now"]),
            "running": sum(1 for r in rows if r["now"] and r["now"]["running"])}


def avg_hours(machine, today=None, days=FORECAST_DAYS):
    today = today or date.today()
    total = (db.session.query(func.coalesce(func.sum(ChpDay.hours), 0))
             .filter(ChpDay.machine_id == machine.id, ChpDay.day >= today - timedelta(days=days),
                     ChpDay.day < today).scalar())
    count = (db.session.query(func.count(ChpDay.id))
             .filter(ChpDay.machine_id == machine.id, ChpDay.day >= today - timedelta(days=days),
                     ChpDay.day < today).scalar())
    return float(total) / count if count else None


def maintenance_forecast(machine, today=None):
    """Wartungen nach Stunden mit voraussichtlichem Datum: [{task, status, left, due_on}] – die nächste zuerst."""
    from .machines import task_status
    today = today or date.today()
    per_day = avg_hours(machine, today)
    rows = []
    for task in machine.tasks:
        state = task_status(task, today)
        left = state["left_counter"]
        if left is None:
            if task.interval_hours and state["state"] == "unknown":
                rows.append({"task": task, "status": state, "left": None, "due_on": None})
            continue
        due_on = today if left <= 0 else (today + timedelta(days=math.ceil(left / per_day)) if per_day else None)
        rows.append({"task": task, "status": state, "left": left, "due_on": due_on})
    # bekannte nach Reststunden, noch nie erledigte nach Intervall (die häufigen zuerst)
    return sorted(rows, key=lambda r: (r["left"] is None, r["left"] if r["left"] is not None else r["task"].interval_hours))


def overview(today=None):
    """Für die Biogas-Seite: je BHKW Stunden/kWh/Erlös (14 Tage und Jahr), Ø Laufzeit und die nächsten Wartungen."""
    today = today or date.today()
    bhkw = (Machine.query.filter(Machine.active.is_(True),
                                 (Machine.category == "bhkw") | Machine.skve_chp_id.isnot(None))
            .order_by(Machine.name).all())
    since = today - timedelta(days=14)
    rows = []
    for machine in bhkw:
        recent = machine.chp_days.filter(ChpDay.day >= since, ChpDay.day < today).order_by(ChpDay.day).all()
        year = (db.session.query(func.coalesce(func.sum(ChpDay.hours), 0), func.coalesce(func.sum(ChpDay.kwh), 0),
                                 func.sum(ChpDay.revenue_eur))
                .filter(ChpDay.machine_id == machine.id, ChpDay.day >= date(today.year, 1, 1)).one())
        rows.append({"machine": machine, "recent": recent, "avg": avg_hours(machine, today),
                     "year_hours": float(year[0]), "year_kwh": float(year[1]),
                     "year_revenue": float(year[2]) if year[2] is not None else None,
                     "forecast": maintenance_forecast(machine, today)[:3]})
    return rows
