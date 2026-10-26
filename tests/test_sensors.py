"""Rubrik Sensoren: Knoten melden sich über /api/meshtastic, der Admin legt fest, wofür sie da sind."""
import pytest

from app import climate, coop, sensors
from app.models import ClimateReading, ClimateSensor, CoopDoorEvent, CoopReading, MeshNode


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def token(db):
    value = coop.new_token()
    db.session.commit()
    return value


def send(client, token, data):
    return client.post("/api/meshtastic", json=data, headers={"Authorization": f"Bearer {token}"})


def test_new_node_waits_for_assignment(as_admin, as_worker, client, db, seed, token):
    assert send(client, "falsch", {"node": "!0000aaaa"}).status_code == 401
    r = send(client, token, {"node": "!0000AAAA", "rssi": -90, "values": {"temperature": 5.5, "co2": 900}})
    assert r.get_json() == {"ok": True, "purpose": None}
    mesh = MeshNode.query.one()
    assert mesh.node == "!0000aaaa" and mesh.temperature == 5.5 and mesh.co2 == 900 and mesh.rssi == -90
    assert ClimateReading.query.count() == 0 and CoopReading.query.count() == 0      # noch nirgends eingetragen
    html = as_admin.get("/sensoren").get_data(as_text=True)
    assert "Neue Knoten" in html and "!0000aaaa" in html
    assert as_worker.get("/sensoren").status_code == 404                             # noch nichts in Betrieb

    r = as_admin.post(f"/sensoren/{mesh.id}", data={"purpose": "climate"}, follow_redirects=True)
    assert "Bitte ein Lager wählen" in r.get_data(as_text=True) and mesh.purpose is None
    as_admin.post(f"/sensoren/{mesh.id}", data={"purpose": "climate", "name": "Halle Mitte",
                                                "warehouse_id": seed["wh"].id, "row": "2"})
    sensor = ClimateSensor.query.one()
    assert mesh.purpose == "climate" and sensor.row == 2 and sensor.name == "Halle Mitte" and sensor.active
    from app.models import AuditEntry
    before = AuditEntry.query.count()
    send(client, token, {"node": "!0000aaaa", "values": {"temperature": 6.0, "humidity": 92, "co2": 950}})
    assert climate.status(sensor)["temperature"] == 6.0
    assert AuditEntry.query.count() == before                                       # Messwerte nicht im Protokoll
    assert "Halle Mitte" in as_worker.get("/sensoren").get_data(as_text=True)
    assert "Zuordnen" not in as_worker.get("/sensoren").get_data(as_text=True)      # nur der Admin ordnet zu

    as_admin.post(f"/sensoren/{mesh.id}", data={"purpose": "ignore"})
    assert not sensor.active
    send(client, token, {"node": "!0000aaaa", "values": {"temperature": 7.0}})
    assert ClimateReading.query.count() == 1                                         # abgeschaltet: nichts mehr


def test_coop_nodes(as_admin, client, db, seed, token):
    seed["admin"].chickens = True
    bottom, top = MeshNode(node="!00000001"), MeshNode(node="!00000002")
    db.session.add_all([bottom, top])
    db.session.commit()
    sensors.assign(bottom, "coop")
    sensors.assign(top, "coop_top")
    db.session.commit()
    send(client, token, {"node": "!00000001", "values": {"temperature": 3.5, "humidity": 80, "co2": 400}})
    send(client, token, {"node": "!00000001", "text": "Klappe state: 1"})
    send(client, token, {"node": "!00000002", "text": "Klappe oben state: 0"})       # Kontakt oben geschlossen
    assert CoopReading.query.one().temperature == 3.5
    events = {e.position: e.is_open for e in CoopDoorEvent.query}
    assert events == {"unten": True, "oben": True}
    assert sensors.node_ids() == {"coop": "!00000001", "top": "!00000002"}

    other = MeshNode(node="!00000003")                                              # nur ein Stall-Knoten
    db.session.add(other)
    sensors.assign(other, "coop")
    assert bottom.purpose is None and other.purpose == "coop"

    sensors.save_door_settings(True, False)                                        # Klappe verkehrt herum
    db.session.commit()
    send(client, token, {"node": "!00000003", "text": "Klappe state: 1"})
    assert coop.door_state().is_open is False
    html = as_admin.get("/sensoren").get_data(as_text=True)
    assert "Hühnerstall" in html and "Einrichtung" in html


def test_hint_and_manual(as_admin, client, db, seed, token):
    send(client, token, {"node": "!00000009", "hint": "coop", "values": {"temperature": 4.0}})
    assert MeshNode.query.one().purpose == "coop" and CoopReading.query.count() == 1   # alter Stack: COOP_NODE
    r = as_admin.post("/sensoren/neu", data={"node": "xyz", "purpose": "ignore"}, follow_redirects=True)
    assert "Knoten-ID" in r.get_data(as_text=True)
    as_admin.post("/sensoren/neu", data={"node": "A1B2C3D4", "purpose": "climate", "warehouse_id": seed["wh"].id})
    assert ClimateSensor.query.filter_by(node="!a1b2c3d4").one().warehouse_id == seed["wh"].id
    r = as_admin.post("/sensoren/neu", data={"node": "!a1b2c3d4"}, follow_redirects=True)
    assert "gibt es schon" in r.get_data(as_text=True)
    mesh = MeshNode.query.filter_by(node="!a1b2c3d4").one()
    as_admin.post(f"/sensoren/{mesh.id}/loeschen")
    assert MeshNode.query.filter_by(node="!a1b2c3d4").first() is None and ClimateSensor.query.count() == 0


def test_open_by_setting(as_admin, db, seed):
    r = as_admin.post("/sensoren/klappe", data={"open_by": "09:30"}, follow_redirects=True)
    assert "Gespeichert" in r.get_data(as_text=True) and coop.open_by().strftime("%H:%M") == "09:30"
    r = as_admin.post("/sensoren/klappe", data={"open_by": "quatsch"}, follow_redirects=True)
    assert "Uhrzeit" in r.get_data(as_text=True) and coop.open_by().strftime("%H:%M") == "09:30"
    assert 'value="09:30"' in as_admin.get("/sensoren").get_data(as_text=True)
