from datetime import date
from decimal import Decimal

import pytest

from app import bulk, machines, sales, sites
from app.models import BulkStore, Customer, Delivery, Machine, Plot, Site, Warehouse
from app.storage import StorageError


@pytest.fixture
def two_sites(db, seed):
    nord = sites.save_site(Site(), "Hofstelle Nord", "Feldweg 1\n12345 Musterdorf", phone="0123 456")
    sued = sites.save_site(Site(), "Hofstelle Süd", "Wiesenstraße 9\n12346 Beispielstadt", passport_reg="DE-XX1-00001")
    db.session.commit()
    return nord, sued


def test_save_usage_delete(db, seed, two_sites):
    nord, sued = two_sites
    with pytest.raises(StorageError):
        sites.save_site(Site(), "hofstelle nord")
    seed["plot"].site_id = nord.id
    sites.set_tank_site(sued.id)
    db.session.commit()
    assert sites.usage(nord)["plots"] == 1 and sites.usage(sued)["tank"] == 1
    assert not sites.delete_site(nord) and not nord.active          # zugeordnet → nur deaktiviert
    empty = sites.save_site(Site(), "Leer")
    assert sites.delete_site(empty) and db.session.get(Site, empty.id) is None
    assert sites.from_form(str(sued.id)) == sued.id and sites.from_form("999") is None and sites.from_form("x") is None


def test_sender_on_delivery(db, seed, two_sites):
    nord, sued = two_sites
    customer = Customer(name="Hofladen Beispiel")
    db.session.add(customer)
    delivery = sales.create_delivery(customer, date(2026, 9, 1), seed["admin"], site_id=sued.id)
    farm = {"farm_name": "Gut Musterhof", "farm_address": "Hauptstraße 1", "farm_phone": "0999",
            "farm_passport_reg": "DE-XX1-99999"}
    data = sites.sender(delivery, farm)
    assert data["farm_name"] == "Gut Musterhof" and data["site_name"] == "Hofstelle Süd"
    assert data["farm_address"].startswith("Wiesenstraße 9") and data["farm_passport_reg"] == "DE-XX1-00001"
    assert data["farm_phone"] == "0999"                              # ohne eigenes Telefon bleibt das des Hofs
    delivery.site_id = None
    db.session.flush()
    db.session.expire(delivery, ["site"])
    assert sites.sender(delivery, farm) == farm


def test_admin_and_assignment(as_admin, as_worker, db, seed, two_sites):
    nord, sued = two_sites
    assert as_worker.get("/admin/betriebsstaetten").status_code == 403
    html = as_admin.get("/admin/betriebsstaetten").get_data(as_text=True)
    assert "Hofstelle Nord" in html and "Feldweg 1" in html
    as_admin.post("/admin/betriebsstaetten", data={"name": "Hofstelle West", "address": "Am Bach 2"})
    assert Site.query.filter_by(name="Hofstelle West").one().address == "Am Bach 2"

    # Schläge: einzeln und mehrere auf einmal
    other = Plot(name="Sonnenacker")
    db.session.add(other)
    db.session.commit()
    as_admin.post("/admin/schlaege", data={"id": seed["plot"].id, "name": "Acker Nord", "site_id": nord.id,
                                               "active": "1"})
    assert seed["plot"].site_id == nord.id
    as_admin.post("/admin/schlaege/betrieb", data={"plot_ids": [str(seed["plot"].id), str(other.id)],
                                                      "site_id": str(sued.id)})
    assert {p.site_id for p in Plot.query} == {sued.id}
    from app.models import AuditEntry
    assert AuditEntry.query.filter_by(table="plots", label="Sonnenacker").one().changes == {"site_id": [None, sued.id]}

    # Lager, Schüttlager, Maschine, Tank
    wh = seed["wh"]
    as_admin.post("/admin/lager", data={"id": wh.id, "name": wh.name, "rows": wh.rows, "slots": wh.slots,
                                            "max_levels": wh.max_levels, "site_id": nord.id})
    assert db.session.get(Warehouse, wh.id).site_id == nord.id
    as_admin.post("/schuettgut/speichern", data={"name": "Box 1", "kind": "box", "site_id": sued.id})
    assert BulkStore.query.filter_by(name="Box 1").one().site_id == sued.id
    m = machines.create_machine("Schlepper", "schlepper", seed["admin"])
    db.session.commit()
    as_admin.post(f"/maschine/{m.id}/bearbeiten", data={"name": "Schlepper", "category": "schlepper",
                                                       "site_id": nord.id})
    assert db.session.get(Machine, m.id).site_id == nord.id
    as_admin.post("/tankbuch/einstellungen", data={"capacity": "3000", "site_id": sued.id})
    assert sites.tank_site_id() == sued.id

    # Lieferschein: Absender wählbar, PDF mit Anschrift der Betriebsstätte
    customer = Customer(name="Hofladen Beispiel")
    db.session.add(customer)
    db.session.commit()
    as_admin.post("/lieferscheine/neu", data={"customer_id": customer.id, "date": "2026-09-02", "site_id": sued.id})
    delivery = Delivery.query.one()
    assert delivery.site_id == sued.id
    assert "Absender: Hofstelle Süd" in as_admin.get(f"/lieferschein/{delivery.id}").get_data(as_text=True)
    pdf = as_admin.get(f"/lieferschein/{delivery.id}.pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"


def test_filter_lists(app, as_worker, db, seed, two_sites):
    nord, sued = two_sites
    seed["plot"].site_id = nord.id
    db.session.add(Plot(name="Sonnenacker", site_id=sued.id))
    bulk.save_store(BulkStore(), "Box Nord", "box", capacity_t=Decimal("10"), site_id=nord.id)
    bulk.save_store(BulkStore(), "Box Süd", "box", capacity_t=Decimal("10"), site_id=sued.id)
    db.session.commit()

    html = as_worker.get("/schlagkarte").get_data(as_text=True)
    assert "Alle Betriebe" in html and "Acker Nord" in html and "Sonnenacker" in html
    r = as_worker.get(f"/schlagkarte?betrieb={sued.id}")
    html = r.get_data(as_text=True)
    assert "Sonnenacker" in html and "Acker Nord" not in html and "betrieb=" in r.headers.get("Set-Cookie", "")
    # Wahl ist gemerkt (Cookie) – auch auf anderen Listen
    html = as_worker.get("/schuettgut").get_data(as_text=True)
    assert "Box Süd" in html and "Box Nord" not in html
    html = as_worker.get("/schuettgut?betrieb=").get_data(as_text=True)
    assert "Box Süd" in html and "Box Nord" in html
    assert "Box Nord" in as_worker.get("/schuettgut").get_data(as_text=True)
