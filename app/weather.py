"""Wetter vom Deutschen Wetterdienst über die freie Schnittstelle Bright Sky (https://brightsky.dev).

Der Server holt Messwerte der letzten 30 Tage und die Vorhersage (MOSMIX, ca. 10 Tage) stündlich in einem
Aufruf und hält sie 30 Minuten im Speicher (Gunicorn läuft mit einem Worker). Der Browser spricht nie
direkt mit Bright Sky.

Krautfäule-Ampel nach den Hutton-Kriterien (Großbritannien, Nachfolger der Smith-Perioden): Ein Tag
zählt, wenn die Tiefsttemperatur mindestens 10 °C beträgt und die relative Luftfeuchte an mindestens
6 Stunden ≥ 90 % liegt. Zwei solche Tage hintereinander = Hutton-Periode = hohes Infektionsrisiko.
Das ist eine Faustregel und ersetzt keine Beratung (z. B. ISIP/SIMPHYT des Pflanzenschutzdienstes).
"""
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta

from flask import current_app

from .i18n import N_
from .models import Setting

MONTHS = [(1, N_("Januar")), (2, N_("Februar")), (3, N_("März")), (4, N_("April")), (5, N_("Mai")),
          (6, N_("Juni")), (7, N_("Juli")), (8, N_("August")), (9, N_("September")), (10, N_("Oktober")),
          (11, N_("November")), (12, N_("Dezember"))]

WEEKDAYS = [N_("Mo"), N_("Di"), N_("Mi"), N_("Do"), N_("Fr"), N_("Sa"), N_("So")]

PAST_DAYS = 30
FORECAST_DAYS = 9
CACHE_SECONDS = 30 * 60
ERROR_CACHE_SECONDS = 5 * 60

HUTTON_MIN_TEMP = 10.0
HUTTON_HUMID_HOURS = 6
HUMID = 90.0
FROST = 0.0
FROST_RISK = 2.0          # 2 m Lufttemperatur ≤ 2 °C → am Boden kann es frieren

# Bright-Sky-Symbole → Bootstrap Icons (in der Reihenfolge ihrer „Schwere“ für die Tageszusammenfassung)
ICONS = {
    "thunderstorm": "bi-cloud-lightning-rain", "hail": "bi-cloud-hail", "snow": "bi-cloud-snow",
    "sleet": "bi-cloud-sleet", "rain": "bi-cloud-rain", "fog": "bi-cloud-fog", "wind": "bi-wind",
    "cloudy": "bi-clouds", "partly-cloudy-day": "bi-cloud-sun", "partly-cloudy-night": "bi-cloud-moon",
    "clear-day": "bi-sun", "clear-night": "bi-moon-stars",
}
SEVERITY = list(ICONS)
LABELS = {"thunderstorm": N_("Gewitter"), "hail": N_("Hagel"), "snow": N_("Schnee"), "sleet": N_("Schneeregen"),
          "rain": N_("Regen"), "fog": N_("Nebel"), "wind": N_("Windig"), "cloudy": N_("Bewölkt"),
          "partly-cloudy-day": N_("Teils bewölkt"), "partly-cloudy-night": N_("Teils bewölkt"),
          "clear-day": N_("Sonnig"), "clear-night": N_("Klar")}

_cache = {}
_month_cache = {}          # (Ort, Jahr, Monat) → mm; abgeschlossene Monate ändern sich nicht mehr
_lock = threading.Lock()
MONTH_FINAL_DAYS = 3       # so lange nach Monatsende kann der DWD noch Werte nachliefern


class WeatherError(Exception):
    pass


def parse_location(text):
    """'50.11, 8.68' oder '50,11 8,68' → (50.11, 8.68); ungültig → None."""
    if not text:
        return None
    numbers = re.findall(r"-?\d+(?:[.,]\d+)?", text)
    if len(numbers) != 2:
        return None
    lat, lon = (float(n.replace(",", ".")) for n in numbers)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def clear_cache():
    with _lock:
        _cache.clear()
        _month_cache.clear()


def _fetch(lat, lon, start, end):
    params = urllib.parse.urlencode({
        "lat": f"{lat:.4f}", "lon": f"{lon:.4f}", "date": start.isoformat(), "last_date": end.isoformat(),
        "tz": current_app.config["TIMEZONE"],
    })
    url = f"{current_app.config['WEATHER_API_URL']}?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "Lagersystem (Hof-App)",
                                                   "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=current_app.config["WEATHER_TIMEOUT"]) as response:
            data = json.load(response)
    except (OSError, ValueError) as e:      # Netz, Zeitüberschreitung, HTTP-Fehler, kaputtes JSON
        raise WeatherError(str(e)) from e
    if not isinstance(data, dict) or not isinstance(data.get("weather"), list):
        raise WeatherError("unerwartete Antwort")
    return data


def _humidity(record):
    rh = record.get("relative_humidity")
    if rh is not None:
        return float(rh)
    t, td = record.get("temperature"), record.get("dew_point")
    if t is None or td is None:
        return None
    # Magnus-Formel, falls die Vorhersage nur den Taupunkt liefert
    return max(0.0, min(100.0, 100 * math.exp(17.62 * td / (243.12 + td) - 17.62 * t / (243.12 + t))))


def _hours(data):
    hours = []
    for r in data["weather"]:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        hours.append({
            "time": ts, "temp": r.get("temperature"), "rh": _humidity(r), "rain": r.get("precipitation"),
            "wind": r.get("wind_speed"), "gust": r.get("wind_gust_speed"), "icon": r.get("icon"),
            "rain_prob": r.get("precipitation_probability"), "sunshine": r.get("sunshine"),
        })
    hours.sort(key=lambda h: h["time"])
    return hours


def _day_icon(hours):
    daytime = [h["icon"] for h in hours if h["icon"] and 8 <= h["time"].hour <= 19] or \
              [h["icon"] for h in hours if h["icon"]]
    if not daytime:
        return None
    counts = Counter(i.replace("-night", "-day") for i in daytime)
    # Unwetter schon bei 1 Stunde zeigen, Regen/Schnee ab 2 Stunden, sonst das häufigste Symbol
    for icon in SEVERITY[:3]:
        if counts.get(icon):
            return icon
    for icon in SEVERITY[3:5]:
        if counts.get(icon, 0) >= 2:
            return icon
    return counts.most_common(1)[0][0]


def _days(hours, today):
    by_day = {}
    for h in hours:
        by_day.setdefault(h["time"].date(), []).append(h)
    days = []
    for day in sorted(by_day):
        hs = by_day[day]
        temps = [h["temp"] for h in hs if h["temp"] is not None]
        rains = [h["rain"] for h in hs if h["rain"] is not None]
        winds = [h["wind"] for h in hs if h["wind"] is not None]
        gusts = [h["gust"] for h in hs if h["gust"] is not None]
        probs = [h["rain_prob"] for h in hs if h["rain_prob"] is not None]
        humid_hours = sum(1 for h in hs if h["rh"] is not None and h["rh"] >= HUMID)
        tmin = min(temps) if temps else None
        days.append({
            "date": day, "past": day < today, "today": day == today, "hours": len(hs),
            "tmin": tmin, "tmax": max(temps) if temps else None,
            "rain": round(sum(rains), 1) if rains else None, "rain_prob": max(probs) if probs else None,
            "wind": max(winds) if winds else None, "gust": max(gusts) if gusts else None,
            "icon": _day_icon(hs), "humid_hours": humid_hours,
            "hutton_day": len(hs) >= 20 and tmin is not None and tmin >= HUTTON_MIN_TEMP
            and humid_hours >= HUTTON_HUMID_HOURS,
        })
    for i, d in enumerate(days):
        d["bi"] = ICONS.get(d["icon"] or "", "bi-question-circle")
        prev_ok = i > 0 and days[i - 1]["hutton_day"] and days[i - 1]["date"] == d["date"] - timedelta(days=1)
        next_ok = i + 1 < len(days) and days[i + 1]["hutton_day"] and days[i + 1]["date"] == d["date"] + timedelta(days=1)
        near = (d["tmin"] is not None and d["tmin"] >= HUTTON_MIN_TEMP - 2 and d["humid_hours"] >= HUTTON_HUMID_HOURS - 2)
        if d["hutton_day"] and (prev_ok or next_ok):
            d["blight"] = "high"
        elif d["hutton_day"] or near:
            d["blight"] = "medium"
        else:
            d["blight"] = "low"
    return days


def _blight_summary(days, today):
    """Ampel aus den letzten 2 Tagen und den nächsten 3 Tagen."""
    window = [d for d in days if today - timedelta(days=2) <= d["date"] <= today + timedelta(days=3)]
    level = "low"
    for d in window:
        if d["blight"] == "high":
            level = "high"
            break
        if d["blight"] == "medium":
            level = "medium"
    return {"level": level, "days": window,
            "hutton_days": [d["date"] for d in window if d["blight"] == "high"]}


def _frost(hours, now):
    """Tiefste Temperatur in den nächsten 72 Stunden und ab wann es frostig wird."""
    upcoming = [h for h in hours if now <= h["time"] <= now + timedelta(hours=72) and h["temp"] is not None]
    if not upcoming:
        return None
    coldest = min(upcoming, key=lambda h: h["temp"])
    if coldest["temp"] > FROST_RISK:
        return None
    first = next(h for h in upcoming if h["temp"] <= FROST_RISK)
    return {"level": "frost" if coldest["temp"] <= FROST else "risk", "min": coldest["temp"],
            "at": coldest["time"], "from": first["time"]}


def _rain_totals(days, today):
    def total(start, end):
        values = [d["rain"] for d in days if start <= d["date"] <= end and d["rain"] is not None]
        return round(sum(values), 1) if values else None
    yesterday = today - timedelta(days=1)
    return {
        "last7": total(today - timedelta(days=7), yesterday),
        "last14": total(today - timedelta(days=14), yesterday),
        "last30": total(today - timedelta(days=PAST_DAYS), yesterday),
        "next7": total(today, today + timedelta(days=6)),
    }


def build(data, now):
    today = now.date()
    hours = _hours(data)
    days = _days(hours, today)
    station = next((s.get("station_name") for s in data.get("sources", []) if s.get("station_name")), None)
    return {
        "now": now,
        "station": station,
        "current": next((h for h in reversed(hours) if h["time"] <= now and h["temp"] is not None), None),
        "hourly": [h for h in hours if now - timedelta(hours=1) <= h["time"] <= now + timedelta(hours=36)],
        "past": [d for d in days if d["past"]][-PAST_DAYS:],
        "forecast": [d for d in days if not d["past"]][:7],
        "blight": _blight_summary(days, today),
        "frost": _frost(hours, now),
        "rain": _rain_totals(days, today),
    }


def get_weather(now=None):
    """Wetter für den Hof-Standort. Liefert None (kein Standort), {"error": …} oder die aufbereiteten Daten."""
    location = parse_location(Setting.all().get("farm_location"))
    if location is None:
        return None
    now = now or datetime.now().astimezone()
    key = (location, now.date())
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < cached[1]:
            return cached[2]
    try:
        data = _fetch(*location, now.date() - timedelta(days=PAST_DAYS), now.date() + timedelta(days=FORECAST_DAYS))
        result = build(data, now)
        ttl = CACHE_SECONDS
    except WeatherError as e:
        current_app.logger.warning("Wetter konnte nicht geladen werden: %s", e)
        result, ttl = {"error": True}, ERROR_CACHE_SECONDS
    with _lock:
        _cache.clear()          # nur der aktuelle Tag/Ort wird gehalten
        _cache[key] = (time.monotonic(), ttl, result)
    return result


def _month_total(location, year, month, today):
    """Niederschlag eines abgeschlossenen Monats in mm (aus dem Zwischenspeicher oder von Bright Sky)."""
    key = (location, year, month)
    with _lock:
        cached = _month_cache.get(key)
    if cached is not None and (cached[1] or time.monotonic() - cached[2] < CACHE_SECONDS):
        return cached[0]
    first = date(year, month, 1)
    after = date(year + month // 12, month % 12 + 1, 1)
    data = _fetch(*location, first, after)
    values = []
    for r in data["weather"]:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if (ts.year, ts.month) == (year, month) and r.get("precipitation") is not None:
            values.append(float(r["precipitation"]))
    total = round(sum(values), 1) if values else None
    final = total is not None and today >= after + timedelta(days=MONTH_FINAL_DAYS)
    with _lock:
        _month_cache[key] = (total, final, time.monotonic())
    return total


def rain_year(result, now=None):
    """Niederschlag seit 1. Januar: {"year", "total", "months": [{month, mm, current}]} oder None.
    Abgeschlossene Monate kommen einzeln von Bright Sky (danach aus dem Speicher), der laufende Monat aus den
    30-Tage-Messwerten von `get_weather()` (bis gestern)."""
    location = parse_location(Setting.all().get("farm_location"))
    if location is None or not result or result.get("error"):
        return None
    today = (now or datetime.now().astimezone()).date()
    months = []
    try:
        for month in range(1, today.month):
            months.append({"month": month, "mm": _month_total(location, today.year, month, today), "current": False})
    except WeatherError as e:
        current_app.logger.warning("Jahresniederschlag konnte nicht geladen werden: %s", e)
        return {"year": today.year, "total": None, "months": [], "error": True}
    current = [d["rain"] for d in result["past"] if d["date"].year == today.year and d["date"].month == today.month
               and d["rain"] is not None]
    months.append({"month": today.month, "mm": round(sum(current), 1) if current else None, "current": True})
    known = [m["mm"] for m in months if m["mm"] is not None]
    return {"year": today.year, "total": round(sum(known), 1) if known else None, "months": months,
            "incomplete": len(known) < len(months), "error": False}


def blight_in_season(today=None):
    today = today or date.today()
    values = Setting.all()
    try:
        start, end = int(values["blight_from_month"]), int(values["blight_to_month"])
    except (TypeError, ValueError):
        return False
    if start <= end:
        return start <= today.month <= end
    return today.month >= start or today.month <= end   # z. B. Oktober bis März
