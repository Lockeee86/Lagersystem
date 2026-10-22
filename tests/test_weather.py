from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import weather
from app.models import Setting

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 7, 15, 10, 30, tzinfo=TZ)


def fake_data(days=None, start=NOW.date() - timedelta(days=30), end=NOW.date() + timedelta(days=9), **default):
    """Bright-Sky-Antwort: je Tag {tmin, tmax, humid_hours, rain} (Regen gleichmäßig auf 24 h verteilt)."""
    days = days or {}
    base = {"tmin": 12, "tmax": 22, "humid_hours": 2, "rain": 0.0} | default
    records, day = [], start
    while day <= end:
        d = base | days.get(day, {})
        for hour in range(24):
            temp = d["tmin"] if hour < 6 else d["tmax"] if 12 <= hour <= 16 else (d["tmin"] + d["tmax"]) / 2
            records.append({
                "timestamp": datetime(day.year, day.month, day.day, hour, tzinfo=TZ).isoformat(),
                "temperature": temp, "relative_humidity": 95 if hour < d["humid_hours"] else 70,
                "precipitation": round(d["rain"] / 24, 3), "wind_speed": 12, "wind_gust_speed": 30,
                "icon": "rain" if d["rain"] >= 5 else "partly-cloudy-day", "precipitation_probability": 20,
            })
        day += timedelta(days=1)
    return {"weather": records, "sources": [{"station_name": "Uelzen"}]}


def test_parse_location():
    assert weather.parse_location("50.11, 8.68") == (50.11, 8.68)
    assert weather.parse_location("50,11 8,68") == (50.11, 8.68)
    assert weather.parse_location("Uelzen") is None
    assert weather.parse_location("95, 10") is None


def test_hutton_period_is_high_risk():
    today = NOW.date()
    humid_warm = {"tmin": 13, "humid_hours": 8}
    w = weather.build(fake_data({today: humid_warm, today + timedelta(days=1): humid_warm}), NOW)
    assert w["blight"]["level"] == "high"
    assert w["blight"]["hutton_days"] == [today, today + timedelta(days=1)]


def test_single_day_or_cold_nights():
    today = NOW.date()
    w = weather.build(fake_data({today: {"tmin": 13, "humid_hours": 8}}), NOW)
    assert w["blight"]["level"] == "medium"
    w = weather.build(fake_data(tmin=6, humid_hours=10), NOW)        # feucht, aber zu kalt
    assert w["blight"]["level"] == "low"
    w = weather.build(fake_data(), NOW)
    assert w["blight"]["level"] == "low"


def test_frost_and_rain():
    today = NOW.date()
    w = weather.build(fake_data({today + timedelta(days=1): {"tmin": -2.5}}), NOW)
    assert w["frost"]["level"] == "frost" and w["frost"]["min"] == -2.5
    w = weather.build(fake_data({today + timedelta(days=2): {"tmin": 1.5}}), NOW)
    assert w["frost"]["level"] == "risk"
    w = weather.build(fake_data({today + timedelta(days=5): {"tmin": -5}}), NOW)   # zu weit weg
    assert w["frost"] is None

    rainy = {today - timedelta(days=d): {"rain": 6.0} for d in (1, 2, 10)}
    w = weather.build(fake_data(rainy), NOW)
    assert w["rain"]["last7"] == 12.0 and w["rain"]["last14"] == 18.0 and w["rain"]["last30"] == 18.0
    assert len(w["past"]) == 30 and len(w["forecast"]) == 7 and w["forecast"][0]["today"]
    assert w["past"][-1]["icon"] == "rain" and w["station"] == "Uelzen"
    assert w["current"]["time"].hour == 10


def test_humidity_from_dew_point():
    assert weather._humidity({"temperature": 20, "dew_point": 20}) == pytest.approx(100)
    assert 40 < weather._humidity({"temperature": 20, "dew_point": 8}) < 50


def test_blight_season(app, db):
    assert weather.blight_in_season(date(2026, 6, 1)) and not weather.blight_in_season(date(2026, 11, 1))
    Setting.put("blight_from_month", "10")
    Setting.put("blight_to_month", "3")
    assert weather.blight_in_season(date(2027, 1, 10)) and not weather.blight_in_season(date(2026, 6, 1))


@pytest.fixture
def located(db, seed, monkeypatch):
    Setting.put("farm_location", "50.11, 8.68")
    db.session.commit()
    weather.clear_cache()
    calls = []

    def fetch(lat, lon, start, end):
        calls.append((lat, lon, start, end))
        return fake_data(start=start, end=end)
    monkeypatch.setattr(weather, "_fetch", fetch)
    yield calls
    weather.clear_cache()


def test_pages_and_cache(as_worker, as_admin, located):
    html = as_worker.get("/wetter").get_data(as_text=True)
    assert "Krautfäule-Risiko" in html and "Uelzen" in html
    as_worker.get("/wetter")
    assert len(located) == 1 and located[0][:2] == (50.11, 8.68)    # zweiter Aufruf aus dem Zwischenspeicher
    assert as_worker.get("/").status_code == 200

    # Speichern der Einstellungen leert den Zwischenspeicher
    as_admin.post("/admin/einstellungen", data={"farm_location": "53,1 10,2", "blight_from_month": "5",
                                                "blight_to_month": "9"})
    assert Setting.all()["farm_location"] == "53.1000, 10.2000"
    as_worker.get("/wetter")
    assert len(located) == 2 and located[1][:2] == (53.1, 10.2)

    as_admin.post("/admin/einstellungen", data={"farm_location": "irgendwo"})
    assert Setting.all()["farm_location"] == "53.1000, 10.2000"


def test_errors_and_missing_location(as_worker, db, seed, monkeypatch):
    weather.clear_cache()
    assert "fehlt noch der Standort" in as_worker.get("/wetter").get_data(as_text=True)
    Setting.put("farm_location", "50.11, 8.68")
    db.session.commit()

    def broken(*args):
        raise weather.WeatherError("offline")
    monkeypatch.setattr(weather, "_fetch", broken)
    assert "nicht erreichbar" in as_worker.get("/wetter").get_data(as_text=True)
    assert as_worker.get("/").status_code == 200
    weather.clear_cache()
