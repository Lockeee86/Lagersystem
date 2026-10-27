from datetime import date

from app import machines, sales, storage, tasks
from app.models import Customer


def test_area_and_subnav(as_admin, as_worker, seed):
    html = as_worker.get("/kisten").get_data(as_text=True)
    # Bereich „Lager“ aktiv, Unterpunkte sichtbar, Etiketten (nur Admin) nicht
    assert 'class="active"><i class="bi bi-grid-3x3-gap"></i>Lager</a>' in html
    assert 'class="active"><i class="bi bi-boxes"></i>Kisten</a>' in html
    assert "/etiketten" not in html and "/admin/" not in html
    html = as_admin.get("/kisten").get_data(as_text=True)
    assert "/etiketten" in html and 'href="/admin/"' in html
    # Verwaltung hat ihre eigene Unterleiste und eine Startseite mit Kacheln
    html = as_admin.get("/admin/").get_data(as_text=True)
    assert "area-tile" in html and "Myfarm24" in html and 'href="/admin/benutzer"' in html
    assert as_worker.get("/admin/").status_code == 403
    # Benutzermenü ist schlank: keine Verwaltungsliste mehr
    assert "Kistentypen" not in as_admin.get("/schlagkarte").get_data(as_text=True)


def test_mobile_start_and_tabbar(as_worker, as_admin, db, seed):
    html = as_worker.get("/").get_data(as_text=True)
    # untere Leiste: Heute · Lager · Scannen · Feld & Hof · Mehr
    for href in ('href="/lager/uebersicht"', 'href="/scan"', 'href="/bereich/feld"', 'href="/bereich/mehr"'):
        assert href in html, href
    assert 'class="quick-row d-lg-none"' in html and "Jetzt wichtig" in html
    assert "Alles erledigt" in html                           # noch nichts Dringendes
    tasks.create_task(seed["admin"], title="Zaun reparieren", assignees=[seed["worker"]], important=True)
    db.session.commit()
    html = as_worker.get("/").get_data(as_text=True)
    # Aufgaben stehen in einem eigenen Block (mit Haken), nicht mehr in „Jetzt wichtig“
    tasks_block = html[html.index('hub-heading mt-0">Aufgaben'):]
    assert "Zaun reparieren" in tasks_block and "/aufgabe/" in tasks_block and "erledigt" in tasks_block
    assert "Alles erledigt" in html and "dot-danger" not in html


def test_area_pages(as_worker, as_admin, as_viewer, seed):
    html = as_worker.get("/bereich/feld").get_data(as_text=True)
    assert "Schläge" in html and "Tankbuch" in html and "Pflanzenschutz" in html and "?tanken=1" in html
    assert as_worker.get("/bereich/lager").headers["Location"].endswith("/lager/uebersicht")
    html = as_worker.get("/lager/uebersicht").get_data(as_text=True)
    assert "Kartoffellager" in html and "Schüttlager" in html and "Bestand je Sorte" in html
    assert "Letzte Bewegungen" in html and "Kartoffellager" in html
    html = as_worker.get("/bereich/mehr").get_data(as_text=True)
    assert "Aufgaben" in html and "Lieferscheine" in html and "Mein Konto" in html and "/admin/" not in html
    assert 'href="/admin/"' in as_admin.get("/bereich/mehr").get_data(as_text=True)
    assert "?tanken=1" not in as_viewer.get("/bereich/feld").get_data(as_text=True)   # Betrachter tankt nicht
    assert as_worker.get("/bereich/gibtsnicht").status_code == 404
    # Zurück-Link am Handy statt der Reiterleiste
    assert 'class="mobile-crumb d-lg-none" href="/lager/uebersicht"' in as_worker.get("/kisten").get_data(as_text=True)
    assert 'class="mobile-crumb d-lg-none" href="/lager/uebersicht"' in as_worker.get("/schuettgut").get_data(as_text=True)
    assert 'href="/bereich/mehr"' in as_worker.get("/aufgaben").get_data(as_text=True)


def test_filter_chips(as_worker, db, seed):
    html = as_worker.get(f"/kisten?sorte={seed['belana'].id}&status=befuellt").get_data(as_text=True)
    assert 'class="filter-chip" href="/kisten?status=befuellt"' in html     # × entfernt nur die Sorte
    assert ">Belana<i" in html and 'data-bs-target="#boxFilters"' in html


def test_search(as_worker, db, seed):
    box = storage.create_boxes(1, seed["bt"].id, seed["admin"])[0]
    m = machines.create_machine("Fendt 724", "schlepper", seed["admin"], plate="UE-F 724")
    c = Customer(name="Saatzucht Nord")
    db.session.add(c)
    d = sales.create_delivery(c, date(2026, 10, 1), seed["admin"])
    tasks.create_task(seed["admin"], title="Fendt abschmieren")
    db.session.commit()
    # direkte Treffer
    assert as_worker.get("/suche?q=k1").headers["Location"].endswith(f"/kiste/{box.code}")
    assert as_worker.get("/suche?q=M-001").headers["Location"].endswith(f"/maschine/{m.id}")
    assert as_worker.get(f"/suche?q={d.number.lower()}").headers["Location"].endswith(f"/lieferschein/{d.id}")
    # Volltext über mehrere Bereiche
    html = as_worker.get("/suche?q=fendt").get_data(as_text=True)
    assert "Fendt 724" in html and "Fendt abschmieren" in html
    html = as_worker.get("/suche?q=Acker").get_data(as_text=True)
    assert "Acker Nord" in html
    assert "Saatzucht Nord" in as_worker.get("/suche?q=saatzucht").get_data(as_text=True)   # über Lieferschein
    assert "Nichts gefunden" in as_worker.get("/suche?q=xyzzy").get_data(as_text=True)
    from app import purchases
    purchases.create_request(seed["worker"], "Keilriemen Roder", details="Gates 8PK1500")
    db.session.commit()
    assert "Keilriemen Roder" in as_worker.get("/suche?q=8pk1500").get_data(as_text=True)
    assert as_worker.get("/suche").status_code == 200


def test_tabbar_choice(as_worker, as_admin, db, seed):
    """Die zwei freien Plätze der unteren Leiste: Voreinstellung je Rolle, eigene Wahl unter Mein Konto."""
    from app import tabbar
    worker = seed["worker"]
    assert tabbar.keys_for(worker) == ["lager", "feld"]
    r = as_worker.post("/konto/leiste", data={"tab": ["biogas", "biogas"]}, follow_redirects=True)
    assert "zwei verschiedene" in r.get_data(as_text=True)
    as_worker.post("/konto/leiste", data={"tab": ["biogas", "verkauf"]})
    assert worker.tabbar == "biogas,verkauf"
    html = as_worker.get("/").get_data(as_text=True)
    tabs = html[html.index('class="tabbar'):html.index("</nav>", html.index('class="tabbar'))]
    assert 'href="/bereich/biogas"' in tabs and 'href="/lager/uebersicht"' not in tabs
    more = as_worker.get("/bereich/mehr").get_data(as_text=True)
    assert 'href="/lager/uebersicht"' in more and 'href="/bereich/feld"' in more      # Lager/Feld jetzt unter Mehr
    assert "Biogas</h2>" not in more                                                  # steht schon in der Leiste
    assert 'class="mobile-crumb' not in as_worker.get("/bereich/biogas").get_data(as_text=True)
    assert 'href="/bereich/mehr"' in as_worker.get("/lager/uebersicht").get_data(as_text=True)   # zurück zu Mehr

    # Hühner sieht er nicht → wird aufgefüllt
    worker.tabbar = "huehner,verkauf"
    assert tabbar.keys_for(worker) == ["verkauf", "lager"]
    worker.chickens = True
    assert tabbar.keys_for(worker) == ["huehner", "verkauf"]
    as_worker.post("/konto/leiste", data={"reset": "1"})
    assert worker.tabbar == ""

    # Voreinstellung der Rolle (Verwaltung → Startseite)
    as_admin.post("/admin/startseite", data={"tab_mitarbeiter": ["feld", "huehner"]})
    assert tabbar.role_default("mitarbeiter") == ["feld", "huehner"] and tabbar.keys_for(worker) == ["feld", "huehner"]
    assert "Untere Leiste am Handy" in as_worker.get("/konto").get_data(as_text=True)


def test_biogas_optional(as_admin, as_worker, db, seed):
    """Rubrik Biogas je Benutzer abwählbar – bestehende Benutzer haben sie, Admins immer."""
    from app import tabbar, tiles
    from app.models import CheckStation
    worker = seed["worker"]
    assert worker.biogas and worker.sees_biogas
    station = CheckStation(code="R-01", name="Fermenter", area="biogas")
    hens = CheckStation(code="R-02", name="Stall", area="huehner")
    db.session.add_all([station, hens])
    db.session.commit()
    as_admin.post("/admin/benutzer", data={"id": worker.id, "username": "max", "role": "mitarbeiter",
                                           "language": "de", "active": "1"})              # ohne Haken „Biogas“
    assert not worker.biogas and not worker.sees_biogas
    for url in ("/biogas", "/rundgang", "/station/R-01", "/bereich/biogas"):
        assert as_worker.get(url).status_code == 404, url
    assert as_worker.get("/station/R-02").status_code != 404                  # Hühnerstall bleibt erreichbar
    assert "biogas" not in tabbar.available(worker) and "bhkw" not in tiles.sections_for(worker)
    assert not [k for k in worker.push_topics() if k in ("round_issue", "round_due", "biogas_week")]
    more = as_worker.get("/bereich/mehr").get_data(as_text=True)
    assert 'href="/biogas"' not in more and 'href="/rundgang"' not in more
    assert as_admin.get("/biogas").status_code == 200
    seed["admin"].biogas = False
    assert seed["admin"].sees_biogas                                           # Admins sehen sie immer
    assert "ohne Biogas" in as_admin.get("/admin/benutzer").get_data(as_text=True)
