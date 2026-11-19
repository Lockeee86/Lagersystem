"""iPhone-Widget (Scriptable): Schlüssel, Daten je Benutzer ohne Anmeldung, Skript zum Herunterladen."""
from datetime import date

from app import chickens, machines, tasks, widget
from app.models import User


def test_widget_token_and_script(app, as_worker, db, seed):
    assert "Widget einrichten" in as_worker.get("/konto").get_data(as_text=True)
    assert as_worker.get("/konto/widget/hof-widget.js").status_code == 404            # noch kein Schlüssel
    as_worker.post("/konto/widget")
    token = db.session.get(User, seed["worker"].id).widget_token
    assert token
    page = as_worker.get("/konto").get_data(as_text=True)
    assert "Skript laden" in page and token not in page                               # Schlüssel nicht auf der Seite
    js = as_worker.get("/konto/widget/hof-widget.js")
    text = js.get_data(as_text=True)
    assert js.headers["Content-Disposition"].startswith("attachment") and f"/widget/{token}.json" in text
    assert "__WIDGET_URL__" not in text and "ListWidget" in text
    assert "accessoryRectangular" in text and "accessoryCircular" in text and "accessoryInline" in text
    # Neuer Schlüssel → der alte gilt nicht mehr
    as_worker.post("/konto/widget", data={"neu": "1"})
    assert app.test_client().get(f"/widget/{token}.json").status_code == 404
    assert as_worker.post("/konto/widget").status_code == 302
    assert app.test_client().get("/konto/widget/hof-widget.js").status_code in (302, 401)       # nur angemeldet


def test_widget_data_follows_rights(app, db, seed):
    worker = seed["worker"]
    worker.chickens = True
    worker.biogas = False
    widget.ensure_token(worker)
    tasks.create_task(seed["admin"], title="Radlader abschmieren", assignees=[worker])
    chickens.add_flock_event(date(2026, 1, 1), "gekauft", 10, worker)
    chickens.add_eggs(date.today(), 7, 0, None, worker)
    plant = machines.create_machine("Roder 2", "roder", seed["admin"], with_templates=False)
    machines.add_event(plant, "stoerung", seed["admin"], text="Lager defekt")
    db.session.commit()
    anon = app.test_client()
    r = anon.get(f"/widget/{worker.widget_token}.json")
    assert r.status_code == 200 and r.headers["Cache-Control"].startswith("private")
    d = r.get_json()
    assert d["v"] == 1 and d["chickens"]["eggs"] == 7 and d["chickens"]["url"].startswith("http")
    assert d["projects"] == []                                                       # kein Projekt
    assert "biogas" not in d                                                        # Rubrik Biogas abgewählt
    assert d["tasks"]["open"] == 1 and d["tasks"]["items"] == ["Radlader abschmieren"]
    assert d["t"]["eggs_today"] == "Eier heute" and all(i["level"] in ("danger", "warning") for i in d["important"])
    assert any("Roder 2" in i["text"] and "Lager defekt" in i["text"] for i in d["important"])
    text = r.get_data(as_text=True)
    assert "€" not in text.replace("€/MWh", "")                                       # keine Euro-Beträge
    # polnisch eingestellter Benutzer bekommt polnische Texte
    worker.language = "pl"
    db.session.commit()
    assert anon.get(f"/widget/{worker.widget_token}.json").get_json()["t"]["eggs_today"] != "Eier heute"
    # deaktivierter Benutzer → nichts
    worker.active = False
    db.session.commit()
    assert anon.get(f"/widget/{worker.widget_token}.json").status_code == 404
    assert anon.get("/widget/falsch.json").status_code == 404


def test_widget_without_chickens(app, db, seed):
    viewer = seed["viewer"]
    widget.ensure_token(viewer)
    db.session.commit()
    d = app.test_client().get(f"/widget/{viewer.widget_token}.json").get_json()
    assert "chickens" not in d and d["tasks"]["open"] == 0 and d["stand"]


def test_widget_biogas(app, db, seed, monkeypatch):
    """Biogas-Teil mit erfundenen Motoren und Preisen (SKVE wird nicht gefragt)."""
    from datetime import datetime, timedelta, UTC
    from app import biogas
    admin = seed["admin"]
    admin.biogas = True
    widget.ensure_token(admin)
    m1 = machines.create_machine("Motor 1", "bhkw", admin, with_templates=False)
    m2 = machines.create_machine("Motor 2", "bhkw", admin, with_templates=False)
    machines.add_event(m2, "stoerung", admin, text="CO2-Warnung")
    db.session.commit()
    rows = [{"machine": m1, "now": {"running": True, "kw_now": 390.4}, "start": None, "next": None},
            {"machine": m2, "now": {"running": False, "kw_now": 0}, "start": None, "next": None}]
    monkeypatch.setattr(biogas, "dashboard", lambda today: {"rows": rows, "kw": 390.4, "running": 1})
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    series = [(start + timedelta(hours=h), 80.0 + 10 * h) for h in range(6)]
    monkeypatch.setattr(biogas, "live", lambda cached_only=False: {"price_now": 101.6, "price_series": series})
    d = app.test_client().get(f"/widget/{admin.widget_token}.json").get_json()
    b = d["biogas"]
    assert b["kw"] == 390 and b["running"] == 1 and b["total"] == 2 and b["price"] == 102
    assert [m["state"] for m in b["motors"]] == ["laeuft", "stoerung"]
    assert b["bands"] and abs(sum(x["width"] for x in b["bands"]) - 100) < 0.5
    assert len(b["bands"]) <= 6 and b["running_text"] == "1 von 2 Motoren laufen"


def test_widget_projects(app, db, seed):
    """Laufende Projekte wie „Meine Projekte“ – Name, Fortschritt, nächster Schritt, ohne Kosten."""
    from datetime import timedelta
    from decimal import Decimal
    from app import projects
    worker = seed["worker"]
    widget.ensure_token(worker)
    project = projects.save_project(None, seed["admin"], "Neue Waagensoftware", owner=worker,
                                    target_on=date.today() + timedelta(days=30), budget=Decimal("8000"))
    first = projects.add_step(project, seed["admin"], "Angebote einholen")
    projects.add_step(project, seed["admin"], "Bestellung", due_on=date.today() - timedelta(days=1))
    tasks.complete_task(first, seed["admin"])
    projects.save_project(None, seed["admin"], "Hofladen", status="idee", owner=worker)       # nicht laufend
    db.session.commit()
    d = app.test_client().get(f"/widget/{worker.widget_token}.json").get_json()
    assert [p["name"] for p in d["projects"]] == ["Neue Waagensoftware"]
    p = d["projects"][0]
    assert (p["done"], p["total"], p["pct"]) == (1, 2, 50) and p["next"] == "Bestellung" and p["next_late"]
    assert p["health"] == "warn" and f"/projekt/{project.id}" in p["url"] and d["t"]["projects"] == "Projekte"
    assert "8000" not in str(d) and "8.000" not in str(d)                            # kein Budget
    with open(widget.SCRIPT_FILE, encoding="utf-8") as f:
        js = f.read()
    assert "smallProjects" in js and '"projekte"' in js
