from datetime import datetime, timedelta, timezone

from app import biogas, tiles
from app.models import Setting


def test_sections_per_role_and_user(app, as_admin, as_worker, as_viewer, db, seed):
    worker = seed["worker"]
    assert "strompreis" not in tiles.sections_for(worker) and "aufgaben" in tiles.sections_for(worker)
    assert "plan" not in tiles.allowed_sections(seed["viewer"])                 # Betrachter haben keinen Tagesplan
    # eigene Auswahl: Strompreis dazu, Aufgaben weg
    as_worker.post("/startseite/kacheln", data={"section": ["wichtig", "strompreis", "woche"], "tile": ["aufgaben"]})
    assert set(tiles.sections_for(worker)) == {"wichtig", "strompreis", "woche"}
    assert worker.home_add == "strompreis" and "aufgaben" in worker.home_hide.split(",")
    html = as_worker.get("/").get_data(as_text=True)
    assert "Jetzt wichtig" in html and "Neue Aufgabe" not in html and "Diese Woche" in html
    # zurücksetzen
    as_worker.post("/startseite/kacheln", data={"reset": "1"})
    assert worker.home_add == "" and "aufgaben" in tiles.sections_for(worker)

    # Admin ändert die Voreinstellung der Mitarbeiter: ohne Wetter
    keys = [k for k in tiles.SECTION_DEFAULTS["mitarbeiter"] if k != "wetter"]
    form = {f"home_{r}": tiles.section_default(r) for r in tiles.ROLE_ORDER}
    form["home_mitarbeiter"] = keys
    for r in tiles.ROLE_ORDER:
        form[f"tiles_{r}"] = tiles.role_default(r)
    as_admin.post("/admin/startseite", data=form)
    assert "wetter" not in tiles.sections_for(worker)
    assert db.session.get(Setting, "home_mitarbeiter").value == ",".join(keys)
    html = as_admin.get("/startseite/kacheln").get_data(as_text=True)
    assert "Startseite anpassen" in html and "Strompreis-Ampel" in html
    # Farben der Ampel: Erzeuger-Sicht für alle
    form["price_colors"] = "erzeuger"
    as_admin.post("/admin/startseite", data=form)
    assert biogas.price_view() == "erzeuger"
    assert 'value="erzeuger" id="pc_erzeuger" checked' in as_admin.get("/admin/startseite").get_data(as_text=True)


def test_price_strip(app):
    tz = biogas.local_tz()
    start = datetime(2026, 7, 15, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    series = [(start + timedelta(minutes=15 * i), 50.0 + (i // 4) * 5) for i in range(96)]     # steigt über den Tag
    series += [(start + timedelta(days=1, minutes=15 * i), 20.0) for i in range(96)]           # morgen billig
    now = start + timedelta(hours=12)
    strip = biogas.price_strip(series, now=now, view="verbraucher")
    assert len(strip["segments"]) == 192 and abs(sum(s["width"] for s in strip["segments"]) - 100) < 0.01
    assert strip["now_price"] == 110.0 and strip["level"] == "high" and strip["good"] == "bad"
    assert strip["segments"][0]["color"] != strip["segments"][95]["color"]
    assert strip["best"][1] == 20.0 and strip["best"][0].date() == (start + timedelta(days=1)).astimezone(tz).date()
    assert [d["pct"] for d in strip["days"]] == [0, 50.0] and round(strip["now_pct"], 1) == 25.0
    assert biogas.price_strip([]) is None
    # Erzeuger: Farben umgekehrt, hoher Preis ist gut, genannt wird die teuerste Stunde
    seller = biogas.price_strip(series, now=now, view="erzeuger")
    cheapest = min(range(192), key=lambda i: series[i][1])
    assert seller["segments"][cheapest]["color"] == "#c9403a" and strip["segments"][cheapest]["color"] == "#2e9e5b"
    assert seller["good"] == "good" and seller["best"][1] == 165.0 and seller["best"][0].hour == 23


def test_dashboard_strip_from_cache_only(app, as_admin, db, seed, monkeypatch):
    app.config["SKVE_API_KEY"] = "test-key"
    biogas.clear_cache()
    called = []
    monkeypatch.setattr(biogas, "_get", lambda *a: called.append(a) or {})
    html = as_admin.get("/").get_data(as_text=True)
    assert 'id="strompreis"' in html and "gerade abgerufen" in html and not called       # wartet nie auf die API
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    times = [int((now + timedelta(minutes=15 * i)).timestamp()) for i in range(-8, 40)]
    monkeypatch.setattr(biogas, "_get", lambda path, s, e: {"times": times, "values": [80.0] * len(times)})
    biogas.refresh_live(app)
    html = as_admin.get("/").get_data(as_text=True)
    assert "price-bar" in html and "80 €/MWh" in html
    biogas.clear_cache()


def test_section_order(app, as_worker, db, seed):
    worker = seed["worker"]
    default = tiles.sections_for(worker)
    assert default[:2] == ["wichtig", "plan"]
    html = as_worker.get("/startseite/kacheln").get_data(as_text=True)
    assert 'name="order" value="wichtig"' in html and "nach oben" in html
    # Aufgaben ganz nach oben, Wetter vor Woche
    order = ["aufgaben", "strompreis", "wichtig", "plan", "uebergabe", "bhkw", "wetter", "woche", "bestand"]
    as_worker.post("/startseite/kacheln", data={"section": default, "order": order})
    shown = tiles.sections_for(worker)
    assert shown[0] == "aufgaben" and shown.index("wetter") < shown.index("woche")
    assert worker.home_order.startswith("aufgaben,")
    html = as_worker.get("/").get_data(as_text=True)
    assert html.index("home-aufgaben") < html.index("home-wichtig") < html.index("home-woche")
    # Reihenfolge wie die Voreinstellung → nichts Eigenes gespeichert; zurücksetzen löscht sie
    as_worker.post("/startseite/kacheln", data={"section": default, "order": tiles.section_order(worker)})
    as_worker.post("/startseite/kacheln", data={"reset": "1"})
    assert worker.home_order == "" and tiles.sections_for(worker) == default


def test_new_section_after_saved_default(db, seed):
    """Eine früher gespeicherte Voreinstellung blendet später dazugekommene Bereiche (Hühner) nicht aus."""
    worker = seed["worker"]
    Setting.put("home_mitarbeiter", "wichtig,aufgaben")                  # gespeichert vor dem Bereich Hühner
    db.session.commit()
    assert tiles.section_default("mitarbeiter") == ["wichtig", "aufgaben", "huehner"]
    assert "huehner" not in tiles.sections_for(worker)                    # nur mit Freischaltung
    worker.chickens = True
    assert "huehner" in tiles.sections_for(worker)
    tiles.save_section_default("mitarbeiter", ["wichtig"])                # bewusst abgewählt bleibt abgewählt
    db.session.commit()
    assert tiles.section_default("mitarbeiter") == ["wichtig"]
