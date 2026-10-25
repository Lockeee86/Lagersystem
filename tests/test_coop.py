import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import chickens, coop, push
from app.models import CoopDoorEvent, CoopReading, PushSubscription

BERLIN = ZoneInfo("Europe/Berlin")


def load_bridge():
    path = Path(__file__).resolve().parent.parent / "bridge" / "meshtastic_bridge.py"
    spec = importlib.util.spec_from_file_location("meshtastic_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    return client.post("/api/huehner/sensor", json=data, headers={"Authorization": f"Bearer {token}"})


def test_api(client, db, seed, token):
    assert client.post("/api/huehner/sensor", json={"temperature": 5}).status_code == 401
    assert post(client, {"temperature": 5}, "falsch").status_code == 401
    assert post(client, {"temperature": 500}, token).status_code == 400            # unplausibel
    assert post(client, {}, token).status_code == 400
    r = post(client, {"temperature": 7.5, "humidity": 81, "battery": 92, "node": "!a1b2c3d4"}, token)
    assert r.status_code == 200 and r.get_json()["reading"]
    assert post(client, {"door": "open"}, token).get_json()["door_changed"]
    assert not post(client, {"door": "open"}, token).get_json()["door_changed"]   # Wiederholung: keine Änderung
    assert post(client, {"door": "closed", "at": datetime.now(timezone.utc).timestamp()}, token).get_json()["door_changed"]
    assert CoopReading.query.count() == 1 and CoopDoorEvent.query.count() == 2
    info = coop.status()
    assert info["temperature"] == 7.5 and info["humidity"] == 81 and info["door"] is False and not info["offline"]
    # neuer Schlüssel: der alte gilt nicht mehr
    coop.new_token()
    db.session.commit()
    assert post(client, {"temperature": 6}, token).status_code == 401


def test_pages(as_admin, as_worker, client, db, seed, token):
    seed["worker"].chickens = seed["admin"].chickens = True
    db.session.commit()
    html = as_worker.get("/huehner").get_data(as_text=True)
    assert 'id="sensor"' not in html                                                # ohne Werte nur für den Admin
    assert "Schlüssel für die Brücke erzeugen" not in as_admin.get("/huehner").get_data(as_text=True)
    r = as_admin.post("/huehner/sensor/schluessel", follow_redirects=True)
    assert "COOP_API_TOKEN" in r.get_data(as_text=True)
    assert as_worker.post("/huehner/sensor/schluessel").status_code == 403
    token = coop.new_token()
    db.session.commit()
    now = datetime.now(timezone.utc)
    for i in range(6):
        coop.record({"temperature": 4 + i, "humidity": 70 + i, "at": (now - timedelta(hours=6 - i)).timestamp()})
    post(client, {"door": "open"}, token)
    html = as_worker.get("/huehner").get_data(as_text=True)
    assert 'id="sensor"' in html and "Klappe offen" in html and "coop-chart" in html and "9,0 °C" in html
    assert "Klappe offen" in as_worker.get("/").get_data(as_text=True)              # Karte auf der Startseite


def test_evening_with_sensor(app, db, seed, monkeypatch):
    keeper = seed["worker"]
    keeper.chickens = True
    db.session.add(PushSubscription(user_id=keeper.id, endpoint="https://push.example/w", p256dh="k", auth="a"))
    chickens.add_flock_event(datetime(2026, 10, 1).date(), "gekauft", 20, keeper)
    db.session.commit()
    monkeypatch.setattr(chickens, "farm_sunset", lambda day: chickens.sunset(day, 52.4, 10.5))
    day = datetime(2026, 10, 20).date()
    evening = (chickens.sunset(day, 52.4, 10.5) + timedelta(minutes=20)).astimezone(BERLIN)
    monkeypatch.setattr(coop, "utcnow", lambda: evening.astimezone(timezone.utc))
    coop.record({"door": "closed", "temperature": 8}, now=evening.astimezone(timezone.utc))
    db.session.commit()
    push.daily(app, evening)
    assert not [m for m in push.outbox if m["tag"].startswith("coop-2")]            # Sensor sagt zu → Ruhe
    # nächster Abend: offen → dringende Nachricht (auch ohne eingeschaltetes „Klappe zu?“)
    evening2 = evening + timedelta(days=1)
    monkeypatch.setattr(coop, "utcnow", lambda: evening2.astimezone(timezone.utc))
    coop.record({"door": "open", "temperature": 8}, now=evening2.astimezone(timezone.utc))
    db.session.commit()
    push.daily(app, evening2)
    assert [m["title"] for m in push.outbox if m["tag"].startswith("coop-2")] == ["Hühnerklappe ist noch offen!"]
    # Frost im Stall
    push.outbox.clear()
    coop.record({"temperature": -2.0}, now=evening2.astimezone(timezone.utc))
    db.session.commit()
    push.daily(app, evening2 + timedelta(minutes=5))
    assert [m["title"] for m in push.outbox] == ["Hühnerstall: Frost (-2,0 °C)"]


def test_bridge_parse():
    bridge = load_bridge()
    telemetry = {"from": 0xA1B2C3D4, "id": 1, "type": "telemetry", "timestamp": 1761400000, "rssi": -101, "snr": 4.5,
                 "payload": {"temperature": 6.25, "relative_humidity": 83.1, "barometric_pressure": 1010}}
    data = bridge.parse_meshtastic(telemetry, wanted_node="!a1b2c3d4", door_name="klappe", active="open")
    assert data == {"node": "!a1b2c3d4", "at": 1761400000, "rssi": -101, "snr": 4.5,
                    "temperature": 6.25, "humidity": 83.1}
    assert bridge.parse_meshtastic(telemetry, wanted_node="!00000001", door_name="klappe", active="open") is None
    device = {"from": 1, "type": "telemetry", "payload": {"battery_level": 101, "voltage": 4.2}}
    assert bridge.parse_meshtastic(device, "", "klappe", "open") == {"node": "!00000001", "voltage": 4.2}

    def door(text, active="open"):
        result = bridge.parse_meshtastic({"from": 1, "type": "text", "payload": {"text": text}}, "", "klappe", active)
        return result and result["door"]
    assert door("Klappe detected") == "open"
    assert door("Klappe state: 0") == "closed" and door("Klappe state: 1") == "open"
    assert door("Klappe state: 1", active="closed") == "closed"
    assert door("Hallo zusammen") is None                                          # andere Textnachricht
