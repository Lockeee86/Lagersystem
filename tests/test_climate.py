from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import climate, coop, push
from app.models import ClimateReading, ClimateSensor, PushSubscription
from app.storage import StorageError

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture(autouse=True)
def _clear_outbox():
    push.outbox.clear()
    yield
    push.outbox.clear()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def token(db):
    value = coop.new_token()
    db.session.commit()
    return value


def post(client, data, token):
    return client.post("/api/lager/klima", json=data, headers={"Authorization": f"Bearer {token}"})


def test_api_and_assignment(client, as_admin, as_worker, db, seed, token):
    assert post(client, {"node": "!aa", "temperature": 5}, "falsch").status_code == 401
    assert post(client, {"temperature": 5}, token).status_code == 400                # ohne Knoten
    assert post(client, {"node": "!aa", "battery": 80}, token).get_json()["stored"] is False   # fremder Knoten
    assert ClimateSensor.query.count() == 0
    r = post(client, {"node": "!AA11", "temperature": 5.5, "humidity": 93, "co2": 1850.4, "battery": 77}, token)
    assert r.get_json()["stored"]
    sensor = ClimateSensor.query.one()
    assert sensor.node == "!aa11" and sensor.warehouse is None
    assert "Neue Sensoren" in as_admin.get("/lager/klima").get_data(as_text=True)
    assert as_worker.post(f"/lager/klima/sensor/{sensor.id}", data={"warehouse_id": seed["wh"].id}).status_code == 403
    r = as_admin.post(f"/lager/klima/sensor/{sensor.id}", data={"warehouse_id": seed["wh"].id, "row": "99"},
                      follow_redirects=True)
    assert "gibt es in" in r.get_data(as_text=True)
    as_admin.post(f"/lager/klima/sensor/{sensor.id}", data={"warehouse_id": seed["wh"].id, "row": "3", "name": ""})
    assert sensor.warehouse == seed["wh"] and sensor.row == 3 and sensor.label == "Kartoffellager · C"
    info = climate.status(sensor)
    assert info["co2"] == 1850 and info["in_target"] and not info["issues"] and info["level"] == "ok"
    html = as_worker.get("/lager/klima").get_data(as_text=True)
    assert "5,5 °C" in html and "1850 ppm" in html
    page = as_worker.get(f"/lager/{seed['wh'].id}").get_data(as_text=True)
    assert "climate-badge" in page and "Lagerklima" in page
    # deaktivieren: neue Werte werden ignoriert
    as_admin.post(f"/lager/klima/sensor/{sensor.id}", data={"warehouse_id": seed["wh"].id, "row": "3", "active": "0"})
    assert not sensor.active and post(client, {"node": "!aa11", "temperature": 6}, token).get_json()["stored"] is False


def test_limits_and_alerts(app, as_admin, as_worker, db, seed, token, monkeypatch):
    wh = seed["wh"]
    sensor = ClimateSensor(node="!bb", warehouse_id=wh.id)
    db.session.add(sensor)
    db.session.commit()
    now = datetime.now(timezone.utc)
    climate.record({"node": "!bb", "temperature": 2.1, "humidity": 98, "co2": 6200}, now=now)
    db.session.commit()
    keys = [k for k, _t in climate.status(sensor)["issues"]]
    assert keys == ["temp_low", "hum_high", "co2_high"] and climate.status(sensor)["level"] == "danger"
    assert "Lagerklima" in as_worker.get("/").get_data(as_text=True)                 # Jetzt wichtig
    # eigene Grenzwerte
    r = as_admin.post(f"/lager/klima/grenzwerte/{wh.id}", data={"temp_min": "12", "temp_max": "11"}, follow_redirects=True)
    assert "kleiner als der obere" in r.get_data(as_text=True)
    as_admin.post(f"/lager/klima/grenzwerte/{wh.id}", data={"temp_min": "1,5", "hum_max": "99", "co2_max": "7000"})
    assert climate.limits(wh)["temp_min"] == 1.5 and climate.limits(wh)["co2_max"] == 7000
    assert climate.status(sensor)["issues"] == []
    # Push: einmal je Sensor und Problem am Tag
    db.session.add(PushSubscription(user_id=seed["admin"].id, endpoint="https://push.example/a", p256dh="k", auth="a"))
    as_admin.post(f"/lager/klima/grenzwerte/{wh.id}", data={})
    db.session.commit()
    monkeypatch.setattr("app.chickens.farm_sunset", lambda day: None)
    monkeypatch.setattr("app.chickens.farm_sunrise", lambda day: None)
    push.daily(app, now.astimezone(BERLIN))
    titles = [m["title"] for m in push.outbox if m["tag"].startswith("climate-")]
    assert any("zu kalt" in t for t in titles) and any("CO₂" in t for t in titles)
    count = len(titles)
    push.daily(app, (now + timedelta(minutes=5)).astimezone(BERLIN))
    assert len([m for m in push.outbox if m["tag"].startswith("climate-")]) == count


def test_offline_and_series(db, seed):
    sensor = ClimateSensor(node="!cc", warehouse_id=seed["wh"].id)
    db.session.add(sensor)
    db.session.commit()
    now = datetime.now(timezone.utc)
    for i in range(48):
        climate.record({"node": "!cc", "temperature": 5 + (i % 4) * 0.5, "humidity": 92, "co2": 1200 + i},
                       now=now - timedelta(hours=3) - timedelta(minutes=30 * i))
    db.session.commit()
    info = climate.status(sensor, now)
    assert info["offline"] and [k for k, _t in info["issues"]] == ["offline"]
    assert 42 <= len(climate.series(sensor, "24h", now)) <= 43                      # nur die letzten 24 h
    week = climate.series(sensor, "7d", now)
    assert 20 <= len(week) <= 26 and all(p[1] is not None for p in week)              # stündlich gemittelt
    assert climate.season_start(datetime(2026, 3, 1).date()).year == 2025
    assert ClimateReading.query.count() == 48
    with pytest.raises(StorageError):
        climate.save_limits(seed["wh"], {"hum_min": 120})


def test_bridge_routing():
    from tests.test_coop import _envelope, _field, load_bridge
    bridge = load_bridge()
    msg = {"from": 0xBB, "type": "telemetry", "payload": {"co2": 1500, "co2_temperature": 5.2, "co2_humidity": 91.0,
                                                          "battery_level": 101}}
    assert bridge.route(msg, coop_node="!000000aa", top_node="", climate_nodes=set()) == "climate"
    assert bridge.route({"from": 0xAA}, coop_node="!000000aa", top_node="", climate_nodes=set()) == "coop"
    assert bridge.route(msg, coop_node="", top_node="", climate_nodes=set()) == "coop"            # wie bisher
    assert bridge.route(msg, coop_node="", top_node="", climate_nodes={"!000000bb"}) == "climate"
    assert bridge.route(msg, coop_node="!000000aa", top_node="", climate_nodes={"!000000cc"}) is None
    assert bridge.parse_climate(msg) == {"node": "!000000bb", "temperature": 5.2, "humidity": 91.0, "co2": 1500}
    air = _field(13, 0, 1720) + _field(14, 5, 4.75) + _field(15, 5, 92.5)
    message = bridge.envelope_to_message(_envelope(0xBB, 91, 67, _field(4, 2, air)))
    assert bridge.parse_climate(message)["co2"] == 1720 and bridge.parse_climate(message)["temperature"] == 4.75
