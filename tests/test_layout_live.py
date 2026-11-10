"""Akzentfarbe, Menü am PC (Leiste links/oben), geteilte Ansicht (iframe), Live-Zähler."""
from datetime import date

from app import live, team


def test_accent_and_nav_layout(app, db, seed, as_worker):
    worker = seed["worker"]
    home = as_worker.get("/").get_data(as_text=True)
    assert 'class="side-nav' in home and "has-side" in home and "data-accent" not in home.split("<head>")[0]
    as_worker.post("/konto/schrift", data={"accent": "blau", "nav_layout": "oben"})
    assert (worker.accent, worker.nav_layout, worker.font_size) == ("blau", "oben", "")
    home = as_worker.get("/").get_data(as_text=True)
    assert 'data-accent="blau"' in home and 'class="side-nav' not in home and "has-side" not in home
    as_worker.post("/konto/schrift", data={"accent": "pink", "nav_layout": "unten"})        # Unbekanntes → Standard
    assert (worker.accent, worker.nav_layout) == ("", "")
    konto = as_worker.get("/konto").get_data(as_text=True)
    assert 'id="ac_blau"' in konto and 'id="nl_oben"' in konto


def test_sidebar_shows_areas_and_items(app, db, seed, as_admin, as_worker):
    page = as_admin.get("/kisten").get_data(as_text=True)
    side = page[page.index('class="side-nav'):page.index("</aside>")]
    assert "Lagerkarte" in side and 'href="/admin/"' in side and "side-area active" in side
    side = as_worker.get("/").get_data(as_text=True)
    side = side[side.index('class="side-nav'):side.index("</aside>")]
    assert "/admin/" not in side


def test_split_view_embed(app, db, seed, as_worker):
    """Listen bieten die geteilte Ansicht an; im iframe kommt die Seite ohne Kopfzeile und Leisten."""
    from app import storage
    (box,) = storage.create_boxes(1, seed["bt"].id, seed["admin"])
    db.session.commit()
    liste = as_worker.get("/kisten")
    assert 'data-split="^/kiste/[^/]+$"' in liste.get_data(as_text=True)
    assert "frame-ancestors 'self'" in liste.headers["Content-Security-Policy"]
    assert liste.headers["X-Frame-Options"] == "SAMEORIGIN"
    voll = as_worker.get(f"/kiste/{box.code}").get_data(as_text=True)
    assert "app-header" in voll and "is-embed" not in voll
    fenster = as_worker.get(f"/kiste/{box.code}", headers={"Sec-Fetch-Dest": "iframe"}).get_data(as_text=True)
    assert "is-embed" in fenster and '<header class="app-header' not in fenster and 'class="side-nav' not in fenster
    assert "data-split=" not in as_worker.get("/kisten", headers={"Sec-Fetch-Dest": "iframe"}).get_data(as_text=True)


def test_live_counters(app, db, seed, as_worker):
    """Zähler je Thema steigen nur bei einer echten Änderung; die Seiten nennen ihre Themen."""
    worker = seed["worker"]
    before = live.stamps()
    task = team.add_plan_entry(date.today(), worker, "Tor streichen", seed["admin"])
    db.session.commit()
    after = live.stamps()
    assert after["tasks"] == before["tasks"] + 1 and after["feeding"] == before["feeding"]
    db.session.commit()                                                  # ohne Änderung kein Zählen
    assert live.stamps() == after
    task.title = "Tor lackieren"
    db.session.rollback()                                                # verworfen zählt nicht
    assert live.stamps() == after
    data = as_worker.get("/api/version").get_json()
    assert data["live"]["tasks"] == after["tasks"] and "movement" in data
    assert 'data-live="tasks"' in as_worker.get("/tagesplan").get_data(as_text=True)
    assert 'data-live="movement,tasks,feeding,handover,purchases"' in as_worker.get("/").get_data(as_text=True)
