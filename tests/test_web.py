from app import storage
from app.models import Box, Plot, User
from app.utils import login_throttle

from .conftest import login


def test_setup_flow(app):
    client = app.test_client()
    assert client.get("/").headers["Location"].endswith("/setup")
    resp = client.post("/setup", data={
        "username": "Chef", "password": "geheim123", "password2": "geheim123",
        "warehouse_name": "Halle", "rows": 10, "slots": 15, "max_levels": 5, "full_weight_kg": 500,
    })
    assert resp.status_code == 302
    assert User.query.one().username == "chef"
    assert client.get("/").status_code == 200
    # Setup ist danach gesperrt
    assert client.get("/setup").headers["Location"].endswith("/login")


def test_login_required(app, seed):
    resp = app.test_client().get("/kisten")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_login_throttle(app, seed):
    login_throttle.reset("ip:127.0.0.1", "user:admin")
    client = app.test_client()
    for _ in range(5):
        assert login(client, "admin", "falsch").status_code == 401
    assert login(client, "admin").status_code == 429
    login_throttle.reset("ip:127.0.0.1", "user:admin")


def test_open_redirect_blocked(app, seed):
    client = app.test_client()
    resp = client.post("/login?next=//evil.example", data={"username": "admin", "password": "geheim123"})
    assert resp.headers["Location"] == "/"


def test_pages_render(as_admin, db, seed):
    storage.create_boxes(3, seed["bt"].id, seed["admin"])
    box = Box.query.first()
    storage.update_contents(box, seed["admin"], seed["belana"].id, None, None, None, 100, None)
    storage.store_box(box, seed["wh"].id, 1, 1, seed["admin"])
    db.session.commit()
    for url in ["/", "/lager", f"/lager/{seed['wh'].id}/stapel/1/1", f"/lager/{seed['wh'].id}/stapel/1/1?partial=1",
                "/kisten", "/kisten?status=leer&sort=ernte", "/kiste/K-0001", "/kiste/K-0001/qr.svg",
                "/scan", "/verlauf", "/verlauf?q=K-0001", "/etiketten", "/konto",
                "/admin/lager", "/admin/sorten", "/admin/kistentypen", "/admin/benutzer", "/api/health", "/api/version"]:
        resp = as_admin.get(url)
        assert resp.status_code == 200, url
    assert as_admin.get("/kiste/1").status_code == 302
    assert as_admin.get("/kiste/K-9999").status_code == 404


def test_labels_pdf(as_admin, db, seed):
    storage.create_boxes(30, seed["bt"].id, seed["admin"])
    db.session.commit()
    for fmt in ("3x8", "2x4", "2x2"):
        resp = as_admin.post("/etiketten.pdf", data={"von": "K-0001", "bis": "K-0030", "format": fmt, "skip": 2})
        assert resp.status_code == 200
        assert resp.data.startswith(b"%PDF")


def test_worker_workflow(as_worker, db, seed):
    storage.create_boxes(2, seed["bt"].id, seed["admin"])
    db.session.commit()
    as_worker.post("/kiste/K-0001/inhalt", data={"variety_id": seed["belana"].id, "harvest_date": "2026-10-05",
                                                  "plot_id": seed["plot"].id, "fill": 100})
    as_worker.post("/kiste/K-0001/einlagern", data={"warehouse_id": seed["wh"].id, "row": 3, "slot": 7})
    as_worker.post(f"/lager/{seed['wh'].id}/stapel/3/7/einlagern", data={"code": "2"})
    box1, box2 = Box.query.order_by(Box.code).all()
    assert box1.plot.name == "Acker Nord"
    assert box1.location == "C-07-1" and box2.location == "C-07-2"
    as_worker.post("/kiste/K-0001/fuellstand", data={"fill": 50})
    assert box1.fill == 50
    as_worker.post("/kiste/K-0001/auslagern", data={"empty": "1"})
    assert not box1.is_stored and box1.fill == 0 and box2.level == 1
    # Mitarbeiter darf nicht verwalten
    assert as_worker.get("/admin/benutzer").status_code == 403
    assert as_worker.post("/kisten/neu", data={"count": 1, "box_type_id": seed["bt"].id}).status_code == 403


def test_viewer_is_read_only(as_viewer, db, seed):
    storage.create_boxes(1, seed["bt"].id, seed["admin"])
    db.session.commit()
    assert as_viewer.get("/kiste/K-0001").status_code == 200
    assert as_viewer.post("/kiste/K-0001/fuellstand", data={"fill": 50}).status_code == 403
    assert as_viewer.post("/kiste/K-0001/einlagern", data={"warehouse_id": seed["wh"].id, "row": 1, "slot": 1}).status_code == 403


def test_password_change_logs_out_other_devices(app, seed):
    phone, laptop = app.test_client(), app.test_client()
    login(phone, "max")
    login(laptop, "max")
    resp = laptop.post("/konto", data={"current_password": "geheim123", "password": "neuespw123", "password2": "neuespw123"})
    assert resp.status_code == 302
    assert laptop.get("/kisten").status_code == 200
    assert phone.get("/kisten").status_code == 302


def test_deactivated_user_is_logged_out(as_admin, app, seed):
    phone = app.test_client()
    login(phone, "max")
    assert phone.get("/kisten").status_code == 200
    as_admin.post("/admin/benutzer", data={"id": seed["worker"].id, "username": "max", "role": "mitarbeiter"})
    assert phone.get("/kisten").status_code == 302


def test_last_admin_cannot_be_demoted(as_admin, seed):
    as_admin.post("/admin/benutzer", data={"id": seed["admin"].id, "username": "admin", "role": "betrachter", "active": "1"})
    assert User.query.filter_by(username="admin").one().role == "admin"


def test_warehouse_shrink_blocked(as_admin, db, seed):
    (box,) = storage.create_boxes(1, seed["bt"].id, seed["admin"])
    storage.store_box(box, seed["wh"].id, 10, 15, seed["admin"])
    db.session.commit()
    as_admin.post("/admin/lager", data={"id": seed["wh"].id, "name": "Kartoffellager", "rows": 5, "slots": 15, "max_levels": 5})
    assert seed["wh"].rows == 10


# --- Sprache ----------------------------------------------------------------

def test_language_switch_is_saved_per_user(as_worker, app, seed):
    assert "Übersicht" in as_worker.get("/").get_data(as_text=True)
    resp = as_worker.post("/sprache", data={"lang": "pl", "next": "/konto"})
    assert resp.headers["Location"] == "/konto"
    assert User.query.filter_by(username="max").one().language == "pl"
    html = as_worker.get("/").get_data(as_text=True)
    assert 'lang="pl"' in html and "Przegląd" in html and "Übersicht" not in html
    # Gilt auch auf einem anderen Gerät
    other = app.test_client()
    login(other, "max")
    assert "Przegląd" in other.get("/").get_data(as_text=True)


def test_invalid_language_ignored(as_worker, seed):
    as_worker.post("/sprache", data={"lang": "xx"})
    assert User.query.filter_by(username="max").one().language == "de"


def test_login_page_language(app, seed):
    client = app.test_client()
    assert "Zaloguj się" in client.get("/login", headers={"Accept-Language": "pl-PL,pl;q=0.9"}).get_data(as_text=True)
    client.post("/sprache", data={"lang": "pl", "next": "/login"})
    assert "Zaloguj się" in client.get("/login").get_data(as_text=True)
    # Erste Anmeldung übernimmt die Sprache der Login-Seite
    login(client, "oma")
    assert User.query.filter_by(username="oma").one().language == "pl"


def test_admin_sets_language_for_new_user(app, as_admin, seed):
    as_admin.post("/admin/benutzer", data={"username": "jan", "role": "mitarbeiter", "language": "pl",
                                           "password": "haslo1234", "password2": "haslo1234"})
    assert User.query.filter_by(username="jan").one().language == "pl"
    # Erste Anmeldung von einem Handy mit deutscher Browsersprache: die Einstellung des Admins bleibt
    client = app.test_client()
    client.post("/login", data={"username": "jan", "password": "haslo1234"},
                headers={"Accept-Language": "de-DE,de;q=0.9"})
    assert User.query.filter_by(username="jan").one().language == "pl"
    assert "Zadania" in client.get("/", headers={"Accept-Language": "de-DE"}).get_data(as_text=True)


def test_messages_and_history_translated(as_worker, db, seed):
    as_worker.post("/sprache", data={"lang": "pl"})
    a, b = storage.create_boxes(2, seed["bt"].id, seed["admin"])
    storage.update_contents(a, seed["admin"], seed["belana"].id, None, seed["plot"].id, None, 100, None)
    storage.store_box(a, seed["wh"].id, 1, 1, seed["admin"])
    storage.store_box(b, seed["wh"].id, 1, 1, seed["admin"])
    db.session.commit()
    resp = as_worker.post("/kiste/K-0001/auslagern", data={}, follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "Wydano K-0001." in html and "Przesunięto w dół: K-0002." in html
    html = as_worker.get("/kiste/K-0002").get_data(as_text=True)
    assert "K-0001 wyjęto spod spodu" in html
    html = as_worker.get("/kiste/K-0001").get_data(as_text=True)
    assert "Zmieniono: Odmiana, Pole" in html
    # Fehlermeldung aus der Lagerlogik
    for _ in range(4):
        storage.store_box(storage.create_boxes(1, seed["bt"].id, seed["admin"])[0], seed["wh"].id, 2, 2, seed["admin"])
    storage.store_box(storage.create_boxes(1, seed["bt"].id, seed["admin"])[0], seed["wh"].id, 2, 2, seed["admin"])
    db.session.commit()
    html = as_worker.post("/kiste/K-0001/einlagern", data={"warehouse_id": seed["wh"].id, "row": 2, "slot": 2},
                          follow_redirects=True).get_data(as_text=True)
    assert "Stos B-02 jest pełny" in html


def test_all_pages_render_in_polish(as_admin, db, seed):
    as_admin.post("/sprache", data={"lang": "pl"})
    storage.create_boxes(1, seed["bt"].id, seed["admin"])
    db.session.commit()
    for url in ["/", "/lager", f"/lager/{seed['wh'].id}/stapel/1/1", "/kisten", "/kiste/K-0001", "/scan",
                "/verlauf", "/etiketten", "/konto", "/admin/lager", "/admin/sorten", "/admin/kistentypen",
                "/admin/benutzer"]:
        resp = as_admin.get(url)
        assert resp.status_code == 200, url
        assert 'lang="pl"' in resp.get_data(as_text=True), url


# --- Schläge ----------------------------------------------------------------

def test_plot_admin(as_admin, db, seed):
    as_admin.post("/admin/schlaege", data={"name": "Hofwiese", "area_ha": "2,5", "note": "Pacht"})
    plot = Plot.query.filter_by(name="Hofwiese").one()
    assert str(plot.area_ha) == "2.50" and plot.active
    # Doppelter Name (Groß/Klein egal) wird abgelehnt
    as_admin.post("/admin/schlaege", data={"name": "hofwiese"})
    assert Plot.query.count() == 2
    assert "2,50 ha" in as_admin.get("/admin/schlaege").get_data(as_text=True)
    # Ungültige Fläche → leer
    as_admin.post("/admin/schlaege", data={"id": plot.id, "name": "Hofwiese", "area_ha": "abc", "active": "1"})
    assert plot.area_ha is None


def test_plot_delete_only_deactivates_when_used(as_admin, db, seed):
    (box,) = storage.create_boxes(1, seed["bt"].id, seed["admin"])
    storage.update_contents(box, seed["admin"], None, None, seed["plot"].id, None, 100, None)
    db.session.commit()
    as_admin.post(f"/admin/schlaege/{seed['plot'].id}/loeschen")
    assert seed["plot"].active is False and box.plot_id == seed["plot"].id
    # Inaktiver Schlag bleibt bei der Kiste auswählbar, taucht aber bei anderen nicht auf
    assert "Acker Nord" in as_admin.get("/kiste/K-0001").get_data(as_text=True)
    storage.create_boxes(1, seed["bt"].id, seed["admin"])
    db.session.commit()
    assert "Acker Nord" not in as_admin.get("/kiste/K-0002").get_data(as_text=True).split('id="plot_id"')[1].split("</select>")[0]
    unused = Plot(name="Leer")
    db.session.add(unused)
    db.session.commit()
    as_admin.post(f"/admin/schlaege/{unused.id}/loeschen")
    assert db.session.get(Plot, unused.id) is None


def test_boxes_filter_and_search_by_plot(as_admin, db, seed):
    a, b = storage.create_boxes(2, seed["bt"].id, seed["admin"])
    storage.update_contents(a, seed["admin"], None, None, seed["plot"].id, None, 100, None)
    db.session.commit()
    for url in (f"/kisten?schlag={seed['plot'].id}", "/kisten?q=acker"):
        html = as_admin.get(url).get_data(as_text=True)
        assert 'href="/kiste/K-0001"' in html and 'href="/kiste/K-0002"' not in html, url


def test_unknown_plot_id_is_ignored(as_worker, db, seed):
    storage.create_boxes(1, seed["bt"].id, seed["admin"])
    db.session.commit()
    as_worker.post("/kiste/K-0001/inhalt", data={"plot_id": 999, "fill": 100})
    assert Box.query.one().plot_id is None


def test_worker_cannot_manage_plots(as_worker, seed):
    assert as_worker.get("/admin/schlaege").status_code == 403


# --- Ansichten der Lagerkarte -----------------------------------------------

def test_map_views_switch_and_are_remembered(as_worker, app, db, seed):
    a, b = storage.create_boxes(2, seed["bt"].id, seed["admin"])
    storage.update_contents(a, seed["admin"], seed["belana"].id, None, None, None, 100, None)
    storage.store_box(a, seed["wh"].id, 3, 7, seed["admin"])
    storage.store_box(b, seed["wh"].id, 3, 7, seed["admin"])
    db.session.commit()

    html = as_worker.get("/lager").get_data(as_text=True)
    assert 'class="lager-map"' in html

    html = as_worker.get("/lager?ansicht=3d").get_data(as_text=True)
    assert 'id="iso3d"' in html and "lager3d.js" in html
    assert User.query.filter_by(username="max").one().map_view == "3d"
    # Ohne Parameter bleibt die zuletzt gewählte Ansicht – auch auf einem anderen Gerät
    other = app.test_client()
    login(other, "max")
    assert 'id="iso3d"' in other.get("/lager").get_data(as_text=True)

    html = as_worker.get("/lager?ansicht=liste").get_data(as_text=True)
    assert 'class="row-list"' in html and "Reihe C" in html and ">BE<" in html
    assert "/lager/1/stapel/3/7?partial=1" in html

    as_worker.get("/lager?ansicht=quatsch")
    assert User.query.filter_by(username="max").one().map_view == "liste"


def test_3d_data(as_worker, db, seed):
    (box,) = storage.create_boxes(1, seed["bt"].id, seed["admin"])
    storage.update_contents(box, seed["admin"], seed["belana"].id, None, None, None, 100, None)
    storage.store_box(box, seed["wh"].id, 2, 4, seed["admin"])
    db.session.commit()
    html = as_worker.get("/lager?ansicht=3d").get_data(as_text=True)
    import json, re
    data = json.loads(re.search(r'<script type="application/json" id="iso3dData">(.*?)</script>', html, re.S).group(1))
    assert data["rows"] == 10 and data["slots"] == 15 and data["levels"] == 5
    assert data["stacks"] == [[2, 4, [["#e6b422", seed["belana"].id]]]]
    assert data["stackUrl"] == "/lager/1/stapel/0/0?partial=1"


def test_variety_shorts_are_unique():
    from app.models import Variety
    from app.views import variety_shorts
    vs = [Variety(id=1, name="Belana (Pflanz)"), Variety(id=2, name="Bellarosa"), Variety(id=3, name="Laura (Speise)"),
          Variety(id=4, name="Anuschka"), Variety(id=5, name="Be")]
    shorts = variety_shorts(vs)
    assert shorts[1] == "BE" and shorts[3] == "LA" and shorts[4] == "AN"
    assert len(set(shorts.values())) == len(vs)


def test_farm_settings(app, db, seed):
    from app.models import Setting
    worker = app.test_client()
    login(worker, "max")
    assert worker.get("/admin/einstellungen").status_code in (302, 403)
    client = app.test_client()
    login(client, "admin")
    assert "Lagersystem" in client.get("/").get_data(as_text=True)
    client.post("/admin/einstellungen", data={"farm_name": "Hof Sonnenfeld", "farm_address": "Feldweg 1\n12345 Dorf",
                                              "farm_phone": "", "farm_email": ""})
    assert Setting.all()["farm_name"] == "Hof Sonnenfeld"
    assert Setting.all()["farm_address"] == "Feldweg 1\n12345 Dorf"
    assert db.session.get(Setting, "farm_phone").value is None
    assert "Hof Sonnenfeld" in client.get("/").get_data(as_text=True)
