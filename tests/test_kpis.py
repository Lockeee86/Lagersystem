"""Kennzahlen der Startseite: je Rubrik nur für passende Benutzer, Verlauf über 14 Tage, abwählbar."""
from datetime import date, timedelta

from app import chickens, forest, kpis, tiles
from app.models import ChpDay, Machine


def _cards(app, user):
    with app.test_request_context():
        stock = {"kg": 1500, "filled": 3}
        return {c["key"]: c for c in kpis.cards(user, date.today(), stock,
                                               [{"capacity": 10, "stored": 3}])}


def test_cards_per_area(app, db, seed):
    admin, worker = seed["admin"], seed["worker"]
    today = date.today()
    admin.biogas = admin.chickens = admin.forest = True
    worker.biogas = worker.chickens = worker.forest = False
    bhkw = Machine(code="M-900", name="BHKW Test", category="bhkw")
    db.session.add(bhkw)
    db.session.flush()
    db.session.add(ChpDay(machine_id=bhkw.id, day=today - timedelta(days=1), hours=20, kwh=10000))
    db.session.add(ChpDay(machine_id=bhkw.id, day=today - timedelta(days=3), hours=12, kwh=6000))
    chickens.add_eggs(today, 9, 0, None, admin)
    chickens.add_eggs(today - timedelta(days=2), 7, 0, None, admin)
    forest.create_trees(1, 2, places=4, species="Buche", section="Nordhang")
    db.session.commit()

    cards = _cards(app, admin)
    assert list(cards) == ["lager", "bhkw", "huehner", "wald"]
    assert cards["lager"]["value"] == "1,5" and cards["lager"]["sub"] == "3 Kisten · 30 % belegt"
    assert cards["bhkw"]["label"] == "BHKW gestern" and cards["bhkw"]["value"] == "20,0"
    assert len(cards["bhkw"]["series"]) == kpis.DAYS - 1 and cards["bhkw"]["series"][-1] == 20
    assert cards["huehner"]["value"] == "9" and cards["huehner"]["series"][-3:] == [7, 0, 9]
    assert cards["wald"]["value"] == "8" and cards["wald"]["bar"] == 0
    assert list(_cards(app, worker)) == ["lager"]                 # ohne Rubriken nur das Lager


def test_section_on_top_and_can_be_hidden(app, db, seed, as_worker):
    worker = seed["worker"]
    assert tiles.sections_for(worker)[0] == "kennzahlen"
    assert 'class="kpi-row"' in as_worker.get("/").get_data(as_text=True)
    keys = [k for k in tiles.sections_for(worker) if k != "kennzahlen"]
    tiles.save_user_sections(worker, keys)
    db.session.commit()
    assert "kennzahlen" not in tiles.sections_for(worker)
    assert 'class="kpi-row"' not in as_worker.get("/").get_data(as_text=True)


def test_success_flash_is_toast(app, db, seed, as_admin):
    r = as_admin.post("/startseite/kacheln", data={"tile": [], "section": ["wichtig"]}, follow_redirects=True)
    page = r.get_data(as_text=True)
    assert 'class="app-toast app-toast-success"' in page and "Startseite gespeichert." in page
    assert "flash-stack" not in page
