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


def test_mobile_tiles_and_tabbar(as_worker, seed):
    html = as_worker.get("/").get_data(as_text=True)
    assert 'class="area-tiles d-lg-none"' in html and 'href="/suche"' in html
    assert "Verwaltung" not in html          # Mitarbeiter sehen die Kachel nicht


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
