from datetime import date

from app import storage
from app.models import Box, Customer

DAY = date(2026, 9, 10)


def test_normalize_and_save(db, seed):
    assert storage.normalize_size("25-28") == "25–28 mm"
    assert storage.normalize_size(" 28 - 35mm ") == "28–35 mm"
    assert storage.normalize_size(">55") == "> 55 mm"
    assert storage.normalize_size("Drillinge") == "Drillinge"
    assert storage.save_sizes("25-28\n28–35 mm\n\n25–28 mm\n>55") == ["25–28 mm", "28–35 mm", "> 55 mm"]
    db.session.commit()
    assert storage.sizes() == ["25–28 mm", "28–35 mm", "> 55 mm"]


def test_admin_box_and_harvest(as_admin, as_worker, db, seed):
    assert as_worker.post("/admin/kistentypen/groessen", data={"sizes": "25-28"}).status_code == 403
    r = as_admin.post("/admin/kistentypen/groessen", data={"sizes": "25-28\n28-35\n35-55"})
    assert r.headers["Location"].endswith("/admin/kistentypen#groessen")
    html = as_admin.get("/admin/kistentypen").get_data(as_text=True)
    assert "Kartoffelgrößen" in html and "28–35 mm" in html

    # Kiste nachsortieren: Größe aus der Liste
    (box,) = storage.create_boxes(1, seed["bt"].id, seed["admin"])
    db.session.commit()
    html = as_worker.get(f"/kiste/{box.code}").get_data(as_text=True)
    assert '<option value="25–28 mm"' in html
    as_worker.post(f"/kiste/{box.code}/inhalt", data={"variety_id": seed["belana"].id, "grade": "25–28 mm",
                                                     "fill": "100"})
    assert db.session.get(Box, box.id).grade == "25–28 mm"
    # alter, nicht mehr hinterlegter Wert bleibt wählbar
    as_admin.post("/admin/kistentypen/groessen", data={"sizes": "28-35"})
    assert '<option value="25–28 mm" selected' in as_worker.get(f"/kiste/{box.code}").get_data(as_text=True)

    # Ernte-Modus: Größe wird mitgebucht, gleiche Größe kommt zusammen
    b1, b2, b3 = storage.create_boxes(3, seed["bt"].id, seed["admin"])
    db.session.commit()
    wh = seed["wh"]
    form = {"sorte": seed["belana"].id, "groesse": "28–35 mm", "fuell": "100", "modus": "einlagern", "lager": wh.id}
    as_worker.post("/scan/ernte", data=form)
    assert "Belana 28–35 mm" in as_worker.get("/scan").get_data(as_text=True)
    j = as_worker.post("/ernte/kiste", data={"code": b1.code}, headers={"Accept": "application/json"}).get_json()
    assert j["ok"] and "28–35 mm" in j["text"] and b1.grade == "28–35 mm" and j["place"] == "A-01-1"
    # andere Größe → eigener Stapel
    as_worker.post("/scan/ernte", data={**form, "groesse": ""})
    j = as_worker.post("/ernte/kiste", data={"code": b2.code}, headers={"Accept": "application/json"}).get_json()
    assert j["place"] == "A-02-1"
    as_worker.post("/scan/ernte", data=form)
    j = as_worker.post("/ernte/kiste", data={"code": b3.code}, headers={"Accept": "application/json"}).get_json()
    assert j["place"] == "A-01-2"

    # Lieferschein: Kaliber als Auswahl
    customer = Customer(name="Hofladen Beispiel")
    db.session.add(customer)
    db.session.commit()
    as_admin.post("/lieferscheine/neu", data={"customer_id": customer.id, "date": "2026-09-12"})
    from app.models import Delivery
    d = Delivery.query.one()
    assert 'id="q_grade" name="grade"' in as_admin.get(f"/lieferschein/{d.id}").get_data(as_text=True)


def test_resort_by_scan(as_worker, as_viewer, db, seed):
    storage.save_sizes("25-28\n28-35")
    (box, empty) = storage.create_boxes(2, seed["bt"].id, seed["admin"])
    storage.update_contents(box, seed["admin"], seed["belana"].id, DAY, seed["plot"].id, "35–55 mm", 100, "Notiz")
    db.session.commit()
    assert as_viewer.post("/scan/sortieren", data={"an": "1"}).status_code == 403
    # Schalter: Nachsortieren an schaltet den Ernte-Modus aus
    as_worker.post("/scan/ernte", data={"sorte": seed["belana"].id, "fuell": "100", "modus": "befuellen"})
    assert 'id="harvestSwitch" checked' in as_worker.get("/scan").get_data(as_text=True)
    as_worker.post("/scan/sortieren", data={"an": "1"})
    html = as_worker.get("/scan").get_data(as_text=True)
    assert 'id="sortSwitch" checked' in html and 'id="harvestSwitch" checked' not in html and "sort-on" in html
    assert 'data-size="28–35 mm"' in html
    # laden: leere Kisten abgewiesen
    j = as_worker.get(f"/sortieren/kiste?code={box.code}").get_json()
    assert j["ok"] and j["box"]["grade"] == "35–55 mm" and j["box"]["fill"] == 100
    assert not as_worker.get(f"/sortieren/kiste?code={empty.code}").get_json()["ok"]
    # speichern: nur Größe und Füllstand ändern sich, die zuletzt gewählte Größe wird gemerkt
    j = as_worker.post("/sortieren/kiste", data={"code": box.code, "grade": "28-35", "fill": "75"}).get_json()
    assert j["ok"] and box.grade == "28–35 mm" and box.fill == 75
    assert box.variety_id == seed["belana"].id and box.plot_id == seed["plot"].id and box.note == "Notiz"
    assert 'data-last="28–35 mm"' in as_worker.get("/scan").get_data(as_text=True)
    # Ernte wieder an → Nachsortieren aus
    as_worker.post("/scan/ernte", data={"an": "1"}, headers={"Accept": "application/json"})
    assert 'id="sortSwitch" checked' not in as_worker.get("/scan").get_data(as_text=True)
