"""Glas-Design: Hintergrund je Benutzer."""
from app import backgrounds


def test_effective_and_by_time(db, seed):
    user = seed["worker"]
    assert backgrounds.effective(user) == "sonnenaufgang" and backgrounds.strength(user) == 80
    user.background = "klassisch"
    assert backgrounds.effective(user) is None
    assert [backgrounds.by_time(h) for h in (3, 6, 12, 18, 22)] == ["nacht", "sonnenaufgang", "wiese", "abend", "nacht"]
    user.background = "unsinn"
    assert backgrounds.effective(user) == "sonnenaufgang"
    assert backgrounds.effective(None) == "sonnenaufgang"                          # Anmeldeseite


def test_save_and_pages(app, as_worker, db, seed):
    html = as_worker.get("/").get_data(as_text=True)
    assert 'data-bg="sonnenaufgang"' in html and "glass.css" in html and "app-backdrop" in html
    r = as_worker.post("/konto/hintergrund", data={"background": "abend", "strength": "40"}, follow_redirects=True)
    assert "Hintergrund gespeichert" in r.get_data(as_text=True)
    assert seed["worker"].background == "abend" and seed["worker"].bg_strength == 40
    html = as_worker.get("/konto").get_data(as_text=True)
    assert 'data-bg="abend"' in html and "--bg-strength: 0.4" in html and 'id="bg_abend" value="abend" checked' in html
    as_worker.post("/konto/hintergrund", data={"background": "klassisch", "strength": "500"})
    assert seed["worker"].bg_strength == 100
    html = as_worker.get("/").get_data(as_text=True)
    assert "data-bg=" not in html and "glass.css" not in html and "themeToggle" in html
    assert "glass.css" in app.test_client().get("/login").get_data(as_text=True)
