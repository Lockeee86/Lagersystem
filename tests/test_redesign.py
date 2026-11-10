"""Aufräumrunde: Menü in Gruppen, kurze Startseite je Rolle, Mein Konto, Reiter auf Detailseiten."""
from app import navigation, storage, tiles


def test_menu_groups(app, db, seed, as_admin):
    lager = next(a for a in navigation.AREAS if a["key"] == "lager")
    groups = navigation.grouped(lager["items"])
    assert [g["label"] for g in groups] == ["Bestand", "Arbeiten", "Auswertung"]
    assert all(len(g["items"]) <= 6 for g in groups) and lager["items"][0]["endpoint"] == "main.storage_home"
    assert not any(a["key"] == "sensoren" for a in navigation.AREAS)             # Sensoren stehen jetzt unter Feld & Hof → Technik
    with app.test_request_context():
        hub = navigation.hub("feld", seed["admin"])
    assert [g["label"] for g in hub] == ["Acker", "Technik"]
    assert any(it["endpoint"] == "sensors.overview_page" for it in hub[1]["items"])
    page = as_admin.get("/kisten").get_data(as_text=True)
    side = page[page.index('class="side-nav'):page.index("</aside>")]
    assert side.count('class="side-group"') == 3 and "Auswertung" in side
    admin = as_admin.get("/admin/").get_data(as_text=True)
    assert admin.index("Betrieb") < admin.index("Stammdaten") < admin.index("System") and admin.count("area-tiles-wide") == 3


def test_short_home_and_show_all(app, db, seed, as_worker):
    worker = seed["worker"]
    assert tiles.sections_for(worker) == ["wichtig", "plan", "uebergabe", "aufgaben", "wetter"]
    assert "bestand" in tiles.more_for(worker) and "plan" not in tiles.more_for(worker)
    home = as_worker.get("/").get_data(as_text=True)
    assert "Alle Bereiche anzeigen" in home and "home-bestand" not in home
    alle = as_worker.get("/?alle=1").get_data(as_text=True)
    assert "home-bestand" in alle and "Weniger anzeigen" in alle and "Dauerhaft anpassen" in alle
    assert tiles.sections_for(worker) == ["wichtig", "plan", "uebergabe", "aufgaben", "wetter"]      # nichts gespeichert


def test_account_two_parts_and_advanced(app, db, seed, as_worker):
    page = as_worker.get("/konto").get_data(as_text=True)
    assert page.index('id="schrift"') < page.index('id="push"') < page.index('id="erweitert"')
    inner = page[page.index('id="erweitert"'):]
    assert 'id="leiste"' in inner and 'id="hintergrund"' in inner


def test_detail_pages_have_tabs(app, db, seed, as_worker):
    (box,) = storage.create_boxes(1, seed["bt"].id, seed["admin"])
    db.session.commit()
    page = as_worker.get(f"/kiste/{box.code}").get_data(as_text=True)
    assert "data-page-tabs" in page and 'data-tab-key="verlauf"' in page
    assert 'data-tab="kiste" class="card shadow-sm mb-4 jump-target" id="fuellstand"' in page
