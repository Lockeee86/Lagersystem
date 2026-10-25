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
from .push import Msg
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
    start = _midnight(today)
    end = max(now + timedelta(hours=26), _midnight(today + timedelta(days=2)))     # morgen ganz, sobald bekannt
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    result = {"configured": True, "error": None, "motors": [], "gas": None, "price_now": None, "at": now,
              "price_series": []}
    try:
        price_map = prices(start, end)
        result["price_series"] = [(ts, p) for ts, p in sorted(price_map.items()) if p is not None]
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
        if configured():
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


# --- Wartungsfenster nach Strompreis ----------------------------------------------------

WINDOW_DAYS = 7               # so weit voraus wird gesucht (Day-Ahead + SKVE-Prognose)
DEFAULT_DOWNTIME = 4          # Stunden, wenn an der Wartung nichts eingetragen ist
WINDOW_KEY = "biogas_window_hours"
WINDOW_DEFAULT = (6, 18)      # Arbeitszeit, in der gewartet wird (Ortszeit)
OUTLOOK_TTL = 1800


def window_hours():
    setting = db.session.get(Setting, WINDOW_KEY)
    try:
        start, end = (int(x) for x in setting.value.split("-")) if setting and setting.value else WINDOW_DEFAULT
        return (start, end) if 0 <= start < end <= 24 else WINDOW_DEFAULT
    except ValueError:
        return WINDOW_DEFAULT


def save_window_hours(start, end):
    if start is None or end is None or not 0 <= start < end <= 24:
        from .storage import StorageError
        from .i18n import gettext as _
        raise StorageError(_("Bitte eine gültige Arbeitszeit angeben, z. B. 6 bis 18 Uhr."))
    Setting.put(WINDOW_KEY, f"{start}-{end}")


def price_outlook(now=None):
    """[(UTC-Viertelstunde, €/MWh, Prognose?)] für die nächsten WINDOW_DAYS: Day-Ahead, wo es ihn schon gibt,
    sonst die SKVE-Preisprognose. Zwischengespeichert (OUTLOOK_TTL)."""
    real_now = now is None
    now = now or datetime.now(timezone.utc)
    with _lock:
        cached = _cache.get("outlook")
        if real_now and cached and clock.monotonic() - cached[0] < OUTLOOK_TTL:
            return cached[1]
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(days=WINDOW_DAYS)
    actual = prices(start, end)
    forecast = dict(_fetch("/module/data-service/prices/forecast/DAA", start, end))
    merged = {ts: (price, True) for ts, price in forecast.items() if price is not None}
    merged.update({ts: (price, False) for ts, price in actual.items() if price is not None})
    result = sorted((ts, price, is_forecast) for ts, (price, is_forecast) in merged.items() if ts >= start)
    with _lock:
        if real_now:
            _cache["outlook"] = (clock.monotonic(), result)
    return result


def _quarters(outlook):
    """Auf Viertelstunden auffüllen (Stundenpreise gelten für alle vier Viertel)."""
    filled = {}
    for ts, price, is_forecast in outlook:
        filled[ts] = (price, is_forecast)
    for ts, price, is_forecast in outlook:
        for q in (1, 2, 3):
            filled.setdefault(ts + timedelta(minutes=15 * q), (price, is_forecast))
    return filled


def best_windows(power_kw, hours, outlook, now, until=None, limit=3, work=None):
    """Die günstigsten Zeitfenster von `hours` Stunden: [{start, end, cost, avg_price, forecast}] (Ortszeit).
    Kosten = entgangener Erlös bei Nennleistung (negative Preise: Stillstand spart sogar). Fenster bis zu einem
    Arbeitstag liegen ganz in der Arbeitszeit; je Tag höchstens ein Vorschlag."""
    if not outlook or not hours:
        return []
    tz = local_tz()
    work = work or window_hours()
    quarters = _quarters(outlook)
    first = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    last_start = max(quarters) + timedelta(minutes=15) - timedelta(hours=hours)
    if until is not None:
        last_start = min(last_start, until)
    candidates = []
    start = first
    while start <= last_start:
        local = start.astimezone(tz)
        end_local = (start + timedelta(hours=hours)).astimezone(tz)
        fits = hours > work[1] - work[0] or (local.hour >= work[0] and local.date() == end_local.date()
                                              and (end_local.hour < work[1] or (end_local.hour == work[1]
                                                                                and end_local.minute == 0)))
        if fits:
            slots = [quarters.get(start + timedelta(minutes=15 * i)) for i in range(int(hours * 4))]
            if all(slots):
                avg = sum(p for p, _f in slots) / len(slots)
                candidates.append({"start": local, "end": end_local, "avg_price": avg,
                                   "cost": (power_kw or 0) * hours / 1000 * avg,
                                   "forecast": any(f for _p, f in slots)})
        start += timedelta(hours=1)
    chosen, days = [], set()
    for c in sorted(candidates, key=lambda c: (c["avg_price"], c["start"])):
        if c["start"].date() in days:
            continue
        chosen.append(c)
        days.add(c["start"].date())
        if len(chosen) == limit:
            break
    return sorted(chosen, key=lambda c: c["avg_price"])


def maintenance_windows(today=None, now=None):
    """Für die Biogas-Seite: Wartungen mit Stillstand, die in den nächsten WINDOW_DAYS fällig werden (oder schon
    fällig sind), mit den drei günstigsten Fenstern. Gibt (Zeilen, Fehler) zurück."""
    if not configured():
        return [], None
    given_now = now
    now = now or datetime.now(timezone.utc)
    today = today or now.astimezone(local_tz()).date()
    horizon = today + timedelta(days=WINDOW_DAYS)
    rows = []
    for machine in linked_machines():
        for f in maintenance_forecast(machine, today):
            downtime = f["task"].downtime_hours
            downtime = DEFAULT_DOWNTIME if downtime is None else downtime
            due_soon = f["status"]["state"] in ("overdue", "soon") or (f["due_on"] and f["due_on"] <= horizon)
            if f["left"] is None or not downtime or not due_soon:
                continue
            rows.append({"machine": machine, "task": f["task"], "due_on": f["due_on"], "left": f["left"],
                         "downtime": downtime, "state": f["status"]["state"]})
    if not rows:
        return [], None
    try:
        outlook = price_outlook(given_now)
    except SkveError as e:
        return rows, str(e)
    for row in rows:
        # bis zum Fälligkeitstag suchen (überfällig: so bald wie möglich, d. h. die ganze Woche)
        until = _midnight(row["due_on"] + timedelta(days=1)) if row["due_on"] and row["due_on"] > today else None
        row["windows"] = best_windows(row["machine"].power_kw, row["downtime"], outlook, now, until=until)
    return rows, None


# --- Wochenbericht ------------------------------------------------------------------

def week_report(today=None):
    """Letzte volle Woche (Mo–So): je BHKW Stunden, MWh, Erlös, Tage ohne Lauf; Summen, Ø Preis, nächste Wartung,
    Rundgang-Mängel. None, wenn es keine BHKW-Daten gibt."""
    from .models import StationCheck
    today = today or date.today()
    monday = today - timedelta(days=today.weekday() + 7)
    sunday = monday + timedelta(days=6)
    machines_ = linked_machines()
    rows = []
    for machine in machines_:
        days = machine.chp_days.filter(ChpDay.day >= monday, ChpDay.day <= sunday).all()
        if not days:
            continue
        revenue = [float(d.revenue_eur) for d in days if d.revenue_eur is not None]
        rows.append({"machine": machine, "hours": sum(float(d.hours) for d in days),
                     "kwh": sum(float(d.kwh) for d in days), "revenue": sum(revenue) if revenue else None,
                     "idle_days": sum(1 for d in days if float(d.hours) == 0)})
    if not rows:
        return None
    kwh = sum(r["kwh"] for r in rows)
    revenues = [r["revenue"] for r in rows if r["revenue"] is not None]
    revenue = sum(revenues) if revenues else None
    upcoming = []
    for machine in machines_:
        for f in maintenance_forecast(machine, today):
            if f["due_on"] is not None:
                upcoming.append((f["due_on"], machine, f["task"]))
    upcoming.sort(key=lambda u: (u[0], u[1].name))
    since = _midnight(monday)
    until = _midnight(sunday + timedelta(days=1))
    issues = StationCheck.query.filter(StationCheck.ok.is_(False), StationCheck.created_at >= since,
                                       StationCheck.created_at < until).count()
    return {"monday": monday, "sunday": sunday, "rows": rows, "kwh": kwh, "hours": sum(r["hours"] for r in rows),
            "revenue": revenue, "avg_price": revenue / (kwh / 1000) if revenue is not None and kwh else None,
            "idle": [r for r in rows if r["idle_days"]], "next": upcoming[0] if upcoming else None, "issues": issues}


def week_push(today=None):
    """Montags aus dem Zeitplan: Wochenbericht als Push (Thema „Montags: Wochenbericht Biogas“)."""
    from flask import url_for
    from . import push
    report = week_report(today)
    if report is None:
        return 0
    parts = [Msg("{hours} Betriebsstunden", hours=f"{report['hours']:.0f}")]
    if report["avg_price"] is not None:
        parts.append(Msg("Ø {price} €/MWh", price=f"{report['avg_price']:.0f}"))
    for r in report["idle"]:
        parts.append(Msg("{name}: {n} Tag Stillstand", "{name}: {n} Tage Stillstand", r["idle_days"],
                         name=r["machine"].name))
    if report["next"]:
        due_on, machine, task = report["next"]
        parts.append(Msg("nächste Wartung: {task} {machine} ca. {date}", task=task.name, machine=machine.name,
                         date=due_on.strftime("%d.%m.")))
    if report["issues"]:
        parts.append(Msg("{n} Mangel beim Rundgang", "{n} Mängel beim Rundgang", report["issues"]))
    body = _Joined(parts)
    return push.notify(push.editors(), "biogas_week",
                       Msg("Biogas letzte Woche: {mwh} MWh", mwh=f"{report['kwh'] / 1000:.1f}".replace(".", ",")),
                       body, url_for("biogas.overview") + "#woche", tag=f"biogas-week-{report['monday'].isoformat()}")


class _Joined(Msg):
    """Mehrere `Msg` mit „ · “ verbunden – jede wird beim Empfänger in seiner Sprache übersetzt."""

    def __init__(self, parts):                   # noqa: super-Aufruf nicht nötig, render() ist ersetzt
        self.parts = parts

    def render(self):
        return " · ".join(p.render() for p in self.parts)


# --- Strompreis-Ampel ---------------------------------------------------------------

AMPEL = ((0.0, (46, 158, 91)), (0.5, (232, 177, 58)), (1.0, (201, 64, 58)))   # günstig → mittel → teuer


def _color(rank):
    for (r0, c0), (r1, c1) in zip(AMPEL, AMPEL[1:]):
        if rank <= r1:
            t = (rank - r0) / (r1 - r0) if r1 > r0 else 0
            return "#%02x%02x%02x" % tuple(round(a + (b - a) * t) for a, b in zip(c0, c1))
    return "#%02x%02x%02x" % AMPEL[-1][1]


PRICE_VIEW_KEY = "price_colors"
PRICE_VIEWS = ("verbraucher", "erzeuger")     # verbraucher: niedrig = grün · erzeuger (BGA): hoch = grün


def price_view():
    setting = db.session.get(Setting, PRICE_VIEW_KEY)
    return setting.value if setting and setting.value in PRICE_VIEWS else "verbraucher"


def save_price_view(view):
    Setting.put(PRICE_VIEW_KEY, view if view in PRICE_VIEWS else "verbraucher")


def price_strip(series, now=None, view=None):
    """Ampel aus Viertelstunden-Preisen (heute ab 0 Uhr, morgen sobald bekannt): Farbe nach Rang innerhalb des
    angezeigten Zeitraums. Sicht „verbraucher“: günstig grün, teuer rot; „erzeuger“: umgekehrt, und statt der
    günstigsten wird die teuerste Stunde genannt. None ohne Preise.
    {"segments": [{start, end, price, color, width}], "now_pct", "now_price", "level", "good", "best", "view", "days"}"""
    view = view or price_view()
    producer = view == "erzeuger"
    if not series:
        return None
    now = now or datetime.now(timezone.utc)
    tz = local_tz()
    points = [(ts, price) for ts, price in series if price is not None]
    if not points:
        return None
    first, last = points[0][0], points[-1][0] + timedelta(minutes=15)
    total = (last - first).total_seconds()
    ordered = sorted(p for _ts, p in points)

    def rank(price):
        below = sum(1 for p in ordered if p < price)
        return below / max(len(ordered) - 1, 1)

    segments = []
    for i, (ts, price) in enumerate(points):
        end = points[i + 1][0] if i + 1 < len(points) else ts + timedelta(minutes=15)
        segments.append({"start": ts.astimezone(tz), "end": end.astimezone(tz), "price": price,
                         "color": _color(1 - rank(price) if producer else rank(price)), "width": (end - ts).total_seconds() / total * 100})
    now_price = _current(points, now)
    level = None
    if now_price is not None:
        r = rank(now_price)
        level = "low" if r < 1 / 3 else "high" if r > 2 / 3 else "mid"
    # günstigste volle Stunde ab jetzt (heute und morgen, soweit bekannt)
    hours = {}
    for ts, price in points:
        if ts + timedelta(minutes=15) > now:
            hours.setdefault(ts.replace(minute=0, second=0, microsecond=0), []).append(price)
    averages = [(h, sum(v) / len(v)) for h, v in hours.items() if v]
    pick = max if producer else min
    best = pick(averages, key=lambda x: x[1], default=None)
    days = []
    day = first.astimezone(tz).date()
    while _midnight(day) < last:
        begin = max(_midnight(day), first)
        days.append({"date": day, "pct": (begin - first).total_seconds() / total * 100})
        day += timedelta(days=1)
    return {"segments": segments, "now_pct": max(0, min(100, (now - first).total_seconds() / total * 100))
            if first <= now <= last else None, "now_price": now_price, "level": level,
            # gut = grün: für Verbraucher niedrige, für Erzeuger hohe Preise
            "good": None if level is None else ("good" if level == ("high" if producer else "low")
                                                else "bad" if level == ("low" if producer else "high") else "mid"),
            "best": (best[0].astimezone(tz), best[1]) if best else None, "view": view,
            "min": ordered[0], "max": ordered[-1], "days": days}
