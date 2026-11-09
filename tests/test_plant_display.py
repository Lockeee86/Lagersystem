"""Anzeige der Biogasanlage (800×480 bzw. Farb-E-Paper) – ohne Anmeldung über geheimen Link."""
import io
from datetime import datetime, timedelta, UTC

from PIL import Image

from app import biogas, machines, plant_display
from app.models import CheckStation

PALETTE = {(0, 0, 0), (255, 255, 255), (255, 0, 0), (255, 255, 0), (0, 255, 0), (0, 0, 255)}


def _fake_live(monkeypatch, motor_id, gas=95):
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    series = [(now + timedelta(minutes=15 * i), 40 + (i % 8) * 20) for i in range(-8, 40)]
    data = {"configured": True, "error": None, "price_now": 60.0, "price_series": series,
            "gas": {"now": gas, "min": 70, "max": 98, "plan": []},
            "motors": [{"machine_id": motor_id, "kw_now": 520.0, "running": True, "today_kwh": 4000.0,
                        "today_hours": 7.5, "plan": [], "next_start": None}]}
    monkeypatch.setattr(biogas, "live", lambda now=None, cached_only=False: data)


def test_plant_display(app, monkeypatch, as_admin, as_worker, db, seed):
    admin = seed["admin"]
    motor = machines.create_machine("BHKW Nord", "bhkw", admin, with_templates=False)
    plant = machines.create_machine("Biogasanlage", "anlage", admin, with_templates=False)
    tractor = machines.create_machine("Schlepper 3", "schlepper", admin, with_templates=False)
    machines.add_event(plant, "stoerung", admin, text="Rührwerk 2 klemmt")
    machines.add_event(tractor, "stoerung", admin, text="Reifen platt")              # gehört nicht zur Anlage
    db.session.add(CheckStation(code="R-01", name="Fermenter", area="biogas"))
    db.session.commit()
    _fake_live(monkeypatch, motor.id)

    assert app.test_client().get("/biogas/anzeige/falsch").status_code == 404
    assert as_worker.post("/biogas/anzeige").status_code == 403
    as_admin.post("/biogas/anzeige")
    token = plant_display.token()
    assert token and f"/biogas/anzeige/{token}" in as_admin.get("/biogas/einstellungen").get_data(as_text=True)
    assert as_worker.get("/biogas/einstellungen").status_code == 403                  # Link nur für Admins

    anon = app.test_client()
    html = anon.get(f"/biogas/anzeige/{token}").get_data(as_text=True)
    assert 'http-equiv="refresh"' in html and "BHKW Nord" in html and "läuft" in html and "520 kW" in html
    assert "95 %" in html and "Gasspeicher fast voll (95 %)." in html
    assert "Störung Biogasanlage: Rührwerk 2 klemmt" in html and "Reifen platt" not in html
    assert "1 Station offen" in html
    d = plant_display.data()
    assert {b["level"] for b in d["bands"]} == {"good", "mid", "bad"}

    r = anon.get(f"/biogas/anzeige/{token}/bild.png")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.headers["Cache-Control"] == "no-store"
    img = Image.open(io.BytesIO(r.data)).convert("RGB")
    assert img.size == (800, 480) and {c for _n, c in img.getcolors(10000)} <= PALETTE

    as_admin.post("/biogas/anzeige")                                     # neuer Link: der alte gilt nicht mehr
    assert anon.get(f"/biogas/anzeige/{token}").status_code == 404


def test_plant_display_without_data(app, as_admin, db, seed):
    """Ohne BHKW und ohne SKVE: Anzeige und Bild gehen trotzdem."""
    as_admin.post("/biogas/anzeige")
    token = plant_display.token()
    anon = app.test_client()
    assert "Keine BHKW angelegt." in anon.get(f"/biogas/anzeige/{token}").get_data(as_text=True)
    img = Image.open(io.BytesIO(anon.get(f"/biogas/anzeige/{token}/bild.png").data)).convert("RGB")
    assert {c for _n, c in img.getcolors(10000)} <= PALETTE
