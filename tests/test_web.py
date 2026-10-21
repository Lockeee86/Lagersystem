from app import storage
from app.models import Box, User
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
                                                  "field": "Acker Nord", "fill": 100})
    as_worker.post("/kiste/K-0001/einlagern", data={"warehouse_id": seed["wh"].id, "row": 3, "slot": 7})
    as_worker.post(f"/lager/{seed['wh'].id}/stapel/3/7/einlagern", data={"code": "2"})
    box1, box2 = Box.query.order_by(Box.code).all()
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
