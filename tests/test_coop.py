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
    assert info["temperature"] == 7.5 and info["humidity"] == 81 and info["state"] == "zu" and not info["offline"]
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
                    "temperature": 6.25, "humidity": 83.1, "pressure": 1010}
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
    # oberer Knoten: Kontakt geschlossen (state: 0) = ganz oben
    top = {"from": 2, "type": "text", "payload": {"text": "Klappe oben state: 0"}}
    assert bridge.parse_meshtastic(top, "!00000001", "klappe", "open", "!00000002", "away")["door_top"] == "open"
    top["payload"]["text"] = "Klappe oben detected"
    assert bridge.parse_meshtastic(top, "!00000001", "klappe", "open", "!00000002", "away")["door_top"] == "closed"
    top_telemetry = {"from": 2, "type": "telemetry", "payload": {"voltage": 3.9}}
    assert bridge.parse_meshtastic(top_telemetry, "!00000001", "klappe", "open", "!00000002", "away") is None


def test_top_contact_and_morning(app, client, db, seed, token, monkeypatch):
    """Wahlweise oberer Kontakt: zu / halb (hängt) / offen; morgens Push, wenn sie noch zu ist."""
    keeper = seed["worker"]
    keeper.chickens = True
    db.session.add(PushSubscription(user_id=keeper.id, endpoint="https://push.example/w", p256dh="k", auth="a"))
    chickens.add_flock_event(datetime(2026, 10, 1).date(), "gekauft", 20, keeper)
    db.session.commit()
    now = datetime.now(timezone.utc)
    coop.record({"door": "closed"}, now=now - timedelta(hours=3))
    assert coop.status(now)["state"] == "zu" and not coop.status(now)["has_top"]
    coop.record({"door": "open"}, now=now - timedelta(minutes=30))
    assert coop.status(now)["state"] == "offen"                                   # ohne oberen Kontakt
    coop.record({"door_top": "closed"}, now=now - timedelta(minutes=20))         # oben nicht angekommen
    db.session.commit()
    info = coop.status(now)
    assert info["state"] == "halb" and info["has_top"]
    monkeypatch.setattr(chickens, "farm_sunset", lambda day: None)
    monkeypatch.setattr(chickens, "farm_sunrise", lambda day: None)
    push.daily(app, now.astimezone(BERLIN))
    assert [m["title"] for m in push.outbox if m["tag"].startswith("coop-hang")] == ["Hühnerklappe hängt!"]
    push.daily(app, (now + timedelta(minutes=5)).astimezone(BERLIN))                # nur einmal je Hängen
    assert len([m for m in push.outbox if m["tag"].startswith("coop-hang")]) == 1
    coop.record({"door_top": "open"}, now=now)
    db.session.commit()
    assert coop.status(now)["state"] == "offen"
    rows = coop.day_times(BERLIN, now=now + timedelta(minutes=1))
    assert rows[0]["top"] is not None or rows[-1]["top"] is not None

    # Morgen: noch zu eine Stunde nach Sonnenaufgang → Push (feste Zeit, damit es nicht von der Uhrzeit abhängt)
    push.outbox.clear()
    morning = datetime(2026, 10, 27, 9, 30, tzinfo=BERLIN)
    coop.record({"door": "closed"}, now=morning.astimezone(timezone.utc) - timedelta(minutes=1))
    db.session.commit()
    monkeypatch.setattr(chickens, "farm_sunrise", lambda day: morning - timedelta(hours=2))
    monkeypatch.setattr(coop, "utcnow", lambda: morning.astimezone(timezone.utc))
    push.daily(app, morning)
    assert [m["title"] for m in push.outbox if m["tag"].startswith("coop-morning")] == \
        ["Hühnerklappe heute noch nicht aufgegangen"]


def test_sunrise():
    rise = chickens.sunrise(datetime(2026, 6, 21).date(), 52.4, 10.5).astimezone(BERLIN)
    assert (4, 40) <= (rise.hour, rise.minute) <= (5, 10)


def _varint(n):
    out = b""
    while True:
        byte, n = n & 0x7F, n >> 7
        out += bytes([byte | (0x80 if n else 0)])
        if not n:
            return out


def _field(number, wire, value):
    import struct
    key = _varint(number << 3 | wire)
    if wire == 0:
        return key + _varint(value)
    if wire == 2:
        return key + _varint(len(value)) + value
    if wire == 5:
        return key + (struct.pack("<f", value) if isinstance(value, float) else struct.pack("<I", value))
    raise ValueError(wire)


def _envelope(sender, packet_id, port, payload, encrypt_key=None, bridge=None):
    data = _field(1, 0, port) + _field(2, 2, payload)
    packet = _field(1, 5, sender) + _field(2, 5, 0xFFFFFFFF) + _field(6, 5, packet_id) + _field(7, 5, 1761400000)
    packet += _field(8, 5, 6.5) + _field(12, 0, (-95) & 0xFFFFFFFFFFFFFFFF)
    if encrypt_key:
        packet += _field(5, 2, bridge._decrypt(data, packet_id, sender, encrypt_key))   # CTR: hin = zurück
    else:
        packet += _field(4, 2, data)
    return _field(1, 2, packet) + _field(2, 2, b"LongFast") + _field(3, 2, b"!00000009")


def test_bridge_protobuf():
    """Ohne JSON-Schalter: ServiceEnvelope (msh/…/e/…) selbst lesen, auch verschlüsselt mit dem Kanal-Schlüssel."""
    bridge = load_bridge()
    env = _field(1, 5, 23.5) + _field(2, 5, 64.25)
    telemetry = _field(1, 5, 1761400000) + _field(3, 2, env)
    message = bridge.envelope_to_message(_envelope(0xA1B2C3D4, 77, 67, telemetry))
    assert message["type"] == "telemetry" and message["rssi"] == -95 and message["snr"] == 6.5
    data = bridge.parse_meshtastic(message, "!a1b2c3d4", "klappe", "open")
    assert data["temperature"] == 23.5 and data["humidity"] == 64.2 and data["node"] == "!a1b2c3d4"
    device = _field(2, 2, _field(1, 0, 87) + _field(2, 5, 3.95))
    data = bridge.parse_meshtastic(bridge.envelope_to_message(_envelope(5, 78, 67, device)), "", "klappe", "open")
    assert data["battery"] == 87 and data["voltage"] == 3.95
    text = bridge.envelope_to_message(_envelope(5, 79, 10, b"Klappe state: 0"))
    assert bridge.parse_meshtastic(text, "", "klappe", "open")["door"] == "closed"
    # verschlüsselt: ohne Schlüssel nur Hinweis, mit Standardschlüssel („AQ==“) lesbar
    key = bridge.channel_key("AQ==")
    assert key == bridge.DEFAULT_KEY and len(bridge.channel_key("Ag==")) == 16
    raw = _envelope(5, 80, 1, b"Klappe detected", encrypt_key=key, bridge=bridge)
    assert bridge.envelope_to_message(raw)["encrypted"]
    assert bridge.parse_meshtastic(bridge.envelope_to_message(raw, key), "", "klappe", "open")["door"] == "open"
    assert bridge.envelope_to_message(_envelope(5, 81, 3, b"\x01")) is None        # andere Ports (Position …)


def test_air_quality(app, as_worker, db, seed, monkeypatch):
    """BME680: Luftdruck, Gas-Widerstand/IAQ – Trend gegenüber der Vorwoche, Hinweis und Push."""
    seed["worker"].chickens = True
    db.session.add(PushSubscription(user_id=seed["worker"].id, endpoint="https://push.example/w", p256dh="k", auth="a"))
    db.session.commit()
    now = datetime.now(timezone.utc)
    for h in range(8 * 24, 24, -6):                                    # Woche davor: sauber (hoher Widerstand)
        coop.record({"temperature": 10, "gas": 0.120, "pressure": 1013.2}, now=now - timedelta(hours=h))
    coop.record({"temperature": 10, "gas": 0.110}, now=now - timedelta(hours=20))
    db.session.commit()
    air = coop.status(now)["air"]
    assert air["iaq"] is None and not air["bad"] and 0.9 < air["gas_ratio"] < 1
    assert coop.status(now)["pressure"] == 1013.2
    for h in (10, 6, 0.1):                                               # jetzt: Widerstand fällt deutlich
        coop.record({"temperature": 10, "gas": 0.050}, now=now - timedelta(hours=h))
    db.session.commit()
    assert coop.air_quality(now)["bad"]
    html = as_worker.get("/huehner").get_data(as_text=True)
    assert "deutlich schlechter als sonst" in html and "coop-air" in html and "1013 hPa" in html
    monkeypatch.setattr(chickens, "farm_sunset", lambda day: None)
    monkeypatch.setattr(chickens, "farm_sunrise", lambda day: None)
    push.daily(app, now.astimezone(BERLIN))
    assert "Hühnerstall: schlechte Luft" in [m["title"] for m in push.outbox]
    # mit IAQ zählt dessen Stufe
    coop.record({"iaq": 260}, now=now)
    db.session.commit()
    air = coop.air_quality(now)
    assert air["iaq"] is not None and coop.iaq_label(42) == "ausgezeichnet" and coop.iaq_label(260) == "sehr stark belastet"
    assert coop.air_history(48)[0] == "iaq"


def test_bridge_air_values():
    bridge = load_bridge()
    env = _field(1, 5, 18.0) + _field(2, 5, 70.0) + _field(3, 5, 1009.5) + _field(4, 5, 0.085) + _field(7, 0, 64)
    message = bridge.envelope_to_message(_envelope(5, 90, 67, _field(3, 2, env)))
    data = bridge.parse_meshtastic(message, "", "klappe", "open")
    assert data["pressure"] == 1009.5 and data["gas"] == 0.085 and data["iaq"] == 64
    bme280 = {"from": 5, "type": "telemetry", "payload": {"temperature": 9.0, "barometric_pressure": 0, "iaq": 0}}
    assert bridge.parse_meshtastic(bme280, "", "klappe", "open") == {"node": "!00000005", "temperature": 9.0}


def test_nodes_from_app(as_admin, client, db, seed, token):
    """Knoten-IDs pflegt der Admin in der App; die Brücke holt sie über /api/meshtastic/knoten."""
    from app import climate
    seed["admin"].chickens = True
    db.session.commit()
    assert client.get("/api/meshtastic/knoten").status_code == 401
    r = as_admin.post("/huehner/sensor/knoten", data={"node": "A1B2C3D4", "top_node": "!a1b2c3d4"},
                      follow_redirects=True)
    assert "eigenen Knoten" in r.get_data(as_text=True) and coop.nodes()["coop"] is None
    as_admin.post("/huehner/sensor/knoten", data={"node": "A1B2C3D4", "top_node": ""})
    climate.add_sensor("!0000beef", "Halle", seed["wh"], None)
    db.session.commit()
    data = client.get("/api/meshtastic/knoten", headers={"Authorization": f"Bearer {token}"}).get_json()
    assert data == {"ok": True, "coop": "!a1b2c3d4", "top": None, "climate": ["!0000beef"]}
    assert "!a1b2c3d4" in as_admin.get("/huehner").get_data(as_text=True)

    bridge = load_bridge()
    nodes = bridge.Nodes(token="x", coop="!00000001", top="", climate={"!00000009"})
    assert nodes.current()["coop"] == "!00000001"                     # Stack, solange die App nichts kennt
    nodes.update(data)
    assert nodes.current() == {"coop": "!a1b2c3d4", "top": "", "climate": {"!0000beef", "!00000009"}}
    nodes._get = lambda: (_ for _ in ()).throw(OSError("weg"))
    nodes.refresh(now=10**9)
    assert nodes.current()["coop"] == "!a1b2c3d4"                     # App nicht erreichbar → letzte Zuordnung
