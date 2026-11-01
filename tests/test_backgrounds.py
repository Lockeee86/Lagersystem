"""Glas-Design: Hintergrund je Benutzer."""
from app import backgrounds


def test_effective_and_by_time(db, seed):
    user = seed["worker"]
    assert user.background == "klassisch" and backgrounds.effective(user) is None      # Standard: klassisch
    assert backgrounds.effective(None) is None                                         # Anmeldeseite
    user.background = "sonnenaufgang"
    assert backgrounds.effective(user) == "sonnenaufgang" and backgrounds.strength(user) == 80
    assert [backgrounds.by_time(h) for h in (3, 6, 12, 18, 22)] == ["nacht", "sonnenaufgang", "wiese", "abend", "nacht"]
    user.background = "unsinn"
    assert backgrounds.effective(user) == "sonnenaufgang"


def test_classic_theme_by_daylight(db, seed):
    from datetime import datetime
    from app.models import Setting
    from app.utils import local_tz
    user = seed["worker"]
    noon = datetime(2026, 12, 21, 12, 0, tzinfo=local_tz())
    evening = datetime(2026, 12, 21, 17, 30, tzinfo=local_tz())         # im Winter nach Sonnenuntergang
    summer_evening = datetime(2026, 6, 21, 20, 30, tzinfo=local_tz())   # im Sommer noch hell
    assert user.theme_mode == "auto"
    Setting.put("farm_location", "52.4, 10.5")                          # erfundener Ort in Norddeutschland
    db.session.commit()
    assert backgrounds.theme(user, noon) == "light" and backgrounds.theme(user, evening) == "dark"
    assert backgrounds.theme(user, summer_evening) == "light"
    user.theme_mode = "dark"
    assert backgrounds.theme(user, noon) == "dark"
    user.theme_mode = "system"
    assert backgrounds.theme(user, noon) is None
    # ohne Standort: hell von 7 bis 19 Uhr
    Setting.put("farm_location", None)
    db.session.commit()
    user.theme_mode = "auto"
    assert backgrounds.theme(user, noon) == "light" and backgrounds.theme(user, summer_evening) == "dark"


def test_save_and_pages(app, as_worker, db, seed):
    html = as_worker.get("/").get_data(as_text=True)
    assert "data-bg=" not in html and "glass.css" not in html and "themeToggle" in html
    assert 'data-theme-fixed="1"' in html
    r = as_worker.post("/konto/hintergrund", data={"background": "abend", "strength": "40"}, follow_redirects=True)
    assert "Hintergrund gespeichert" in r.get_data(as_text=True)
    assert seed["worker"].background == "abend" and seed["worker"].bg_strength == 40
    html = as_worker.get("/konto").get_data(as_text=True)
    assert 'data-bg="abend"' in html and "--bg-strength: 0.4" in html and 'id="bg_abend" value="abend" checked' in html
    as_worker.post("/konto/hintergrund", data={"background": "klassisch", "strength": "500", "theme_mode": "dark"})
    assert seed["worker"].bg_strength == 100 and seed["worker"].theme_mode == "dark"
    html = as_worker.get("/").get_data(as_text=True)
    assert "data-bg=" not in html and 'data-bs-theme="dark" data-theme-fixed="1"' in html
    # Umschalter oben merkt sich hell/dunkel; „wie das Gerät“ überlässt es dem Browser
    assert as_worker.post("/konto/darstellung", data={"theme_mode": "light"},
                          headers={"X-Requested-With": "fetch"}).status_code == 204
    assert seed["worker"].theme_mode == "light"
    as_worker.post("/konto/darstellung", data={"theme_mode": "system"})
    assert 'data-theme-fixed="1"' not in as_worker.get("/").get_data(as_text=True)
    assert "glass.css" not in app.test_client().get("/login").get_data(as_text=True)


def test_starry_sky(as_worker, db, seed):
    as_worker.post("/konto/hintergrund", data={"background": "sterne", "strength": "80"})
    assert seed["worker"].background == "sterne"
    assert 'data-bg="sterne"' in as_worker.get("/").get_data(as_text=True)
    assert "Sternenhimmel" in as_worker.get("/konto").get_data(as_text=True)


def test_milky_way(as_worker, db, seed):
    as_worker.post("/konto/hintergrund", data={"background": "milchstrasse", "strength": "80"})
    assert seed["worker"].background == "milchstrasse"
    assert 'data-bg="milchstrasse"' in as_worker.get("/").get_data(as_text=True)
    assert "bg-swatch-milchstrasse" in as_worker.get("/konto").get_data(as_text=True)
