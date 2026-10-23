from datetime import date
from decimal import Decimal

import pytest

from app import bulk, fields, sales
from app.models import BulkEntry, BulkStore, Customer, Plot
from app.storage import StorageError

DAY = date(2026, 8, 5)


@pytest.fixture
def box3(db, seed):
    store = bulk.save_store(BulkStore(), "Box 3", "box", "Getreidehalle", Decimal("120"))
    db.session.commit()
    return store


def test_store_and_intake(db, seed, box3):
    with pytest.raises(StorageError):
        bulk.save_store(BulkStore(), "box 3", "box")                       # Name schon vergeben
    with pytest.raises(StorageError):
        bulk.save_store(BulkStore(), "Silo", "silo")                       # unbekannte Art
    feld = Plot(name="Sonnenacker", area_ha=Decimal("10"))
    nord = seed["plot"]
    db.session.add(feld)
    db.session.flush()
    admin = seed["admin"]

    # erste Fuhre: Waage brutto/tara, Ware wird gesetzt
    e1 = bulk.take_in(box3, admin, DAY, "Winterweizen", gross_kg=38000, tare_kg=14000, plot=feld,
                      variety_name="Testweizen", moisture=Decimal("14.0"), protein=Decimal("12.0"))
    assert e1.net_kg == 24000 and box3.crop == "Winterweizen" and box3.variety_name == "Testweizen"
    bulk.take_in(box3, admin, DAY, "winterweizen", net_kg=12000, plot=nord, moisture=Decimal("17.0"), falling_number=300)
    st = bulk.stock(box3)
    assert st == {"kg": 36000, "bags": None, "percent": 30}
    q = bulk.quality(box3)
    assert q["moisture"] == Decimal("15.0")              # (14 × 24 t + 17 × 12 t) / 36 t
    assert q["protein"] == Decimal("12.0") and q["hl_weight"] is None and str(q["falling_number"]) == "300"
    shares = bulk.plot_shares(box3)
    assert [(s["plot"].name, s["percent"]) for s in shares] == [("Sonnenacker", 67), ("Acker Nord", 33)]

    # andere Ware passt nicht, falsche Waage ebenso
    with pytest.raises(StorageError, match="Winterweizen"):
        bulk.take_in(box3, admin, DAY, "Winterraps", net_kg=1000)
    with pytest.raises(StorageError):
        bulk.take_in(box3, admin, DAY, "Winterweizen", gross_kg=1000, tare_kg=2000)
    with pytest.raises(StorageError):
        bulk.take_in(box3, admin, DAY, "Winterweizen")

    # Ertrag je Schlag (gewogen) fließt in die Schlagkarte ein: 24 t auf 10 ha
    y = fields.yields(2026)
    assert y[feld.id]["kg"] == 24000 and y[feld.id]["bulk_kg"] == 24000
    assert fields.t_per_ha(y[feld.id]["kg"], feld.area_ha) == 2.4


def test_take_out_and_empty_resets_ware(db, seed, box3):
    admin = seed["admin"]
    bulk.take_in(box3, admin, DAY, "Winterroggen", net_kg=5000, moisture=Decimal("13"))
    with pytest.raises(StorageError, match="nur 5000 kg"):
        bulk.take_out(box3, admin, DAY, "futter", net_kg=6000)
    with pytest.raises(StorageError):
        bulk.take_out(box3, admin, DAY, "unbekannt", net_kg=100)
    bulk.take_out(box3, admin, DAY, "futter", net_kg=5000)
    assert bulk.stock(box3)["kg"] == 0 and bulk.plot_shares(box3) == []
    # leer → neue Ware darf hinein, Qualität zählt nur die neue Füllung
    bulk.take_in(box3, admin, DAY, "Winterraps", net_kg=2000, moisture=Decimal("8"))
    assert box3.crop == "Winterraps" and bulk.quality(box3)["moisture"] == Decimal("8.0")

    # Löschen: Einlagerung nicht, wenn sonst negativ
    first_in = BulkEntry.query.filter_by(kind="ein", crop="Winterroggen").one()
    out = BulkEntry.query.filter_by(kind="aus").one()
    bulk.delete_entry(out)
    bulk.delete_entry(first_in)
    assert bulk.stock(box3)["kg"] == 2000


def test_big_bags(db, seed):
    bags = bulk.save_store(BulkStore(), "Big Bags Saatgut", "bigbag", bag_kg=1000)
    admin = seed["admin"]
    with pytest.raises(StorageError, match="Anzahl"):
        bulk.take_in(bags, admin, DAY, "Winterweizen", net_kg=3000)
    bulk.take_in(bags, admin, DAY, "Winterweizen", net_kg=3000, bags=3)
    with pytest.raises(StorageError, match="nur 3 Big Bags"):
        bulk.take_out(bags, admin, DAY, "saatgut", net_kg=1000, bags=4)
    bulk.take_out(bags, admin, DAY, "saatgut", net_kg=1000, bags=1)
    assert bulk.stock(bags) == {"kg": 2000, "bags": 2, "percent": None}


def test_delivery(db, seed, box3):
    admin = seed["admin"]
    bulk.take_in(box3, admin, DAY, "Winterweizen", net_kg=30000)
    customer = Customer(name="Mühle Musterstadt")
    db.session.add(customer)
    delivery = sales.create_delivery(customer, DAY, admin)
    item = sales.add_bulk(delivery, admin, box3, gross_kg=40000, tare_kg=15000)
    assert item.weight_kg == 25000 and item.crop == "Winterweizen" and bulk.stock(box3)["kg"] == 5000
    entry = item.bulk_entry
    assert entry.purpose == "verkauf" and entry.delivery_id == delivery.id
    with pytest.raises(StorageError, match="Lieferschein"):
        bulk.delete_entry(entry)                         # nur über den Lieferschein
    (line,) = sales.summarize(delivery)
    assert line["is_bulk"] and line["crop"] == "Winterweizen" and line["weight_kg"] == 25000
    sales.remove_item(item, admin)
    assert bulk.stock(box3)["kg"] == 30000 and BulkEntry.query.filter_by(kind="aus").count() == 0


def test_pages(as_admin, as_worker, as_viewer, db, seed, box3):
    assert "Box 3" in as_viewer.get("/schuettgut").get_data(as_text=True)
    assert as_worker.post("/schuettgut/speichern", data={"name": "Box 4", "kind": "box"}).status_code == 403
    r = as_admin.post("/schuettgut/speichern", data={"name": "Haufen Halle 2", "kind": "haufen", "capacity_t": "250"})
    haufen = BulkStore.query.filter_by(name="Haufen Halle 2").one()
    assert r.status_code == 302 and haufen.capacity_t == Decimal("250")

    plot = seed["plot"]
    data = {"date": "2026-08-05", "crop": "Winterweizen", "gross_kg": "38.000", "tare_kg": "14000",
            "plot_id": str(plot.id), "moisture": "14,5", "protein": "abc", "falling_number": "320"}
    assert as_viewer.post(f"/schuettgut/{box3.id}/einlagern", data=data).status_code == 403
    as_worker.post(f"/schuettgut/{box3.id}/einlagern", data=data)
    e = BulkEntry.query.one()
    assert e.net_kg == 24000 and e.moisture == Decimal("14.5") and e.protein is None and e.falling_number == 320
    html = as_viewer.get(f"/schuettgut/{box3.id}").get_data(as_text=True)
    assert "24,0 t" in html and "Feuchte 14,5 %" in html and "Acker Nord" in html and "Fallzahl 320 s" in html

    # Verkauf nur über Lieferschein, Eigenverbrauch direkt
    as_worker.post(f"/schuettgut/{box3.id}/auslagern", data={"purpose": "verkauf", "net_kg": "1000"})
    assert BulkEntry.query.count() == 1
    as_worker.post(f"/schuettgut/{box3.id}/auslagern", data={"purpose": "futter", "net_kg": "4000"})
    assert bulk.stock(box3)["kg"] == 20000

    # Lieferschein: Schüttgut-Position, PDF
    customer = Customer(name="Mühle Musterstadt")
    db.session.add(customer)
    delivery = sales.create_delivery(customer, date(2026, 8, 6), seed["admin"])
    db.session.commit()
    html = as_worker.get(f"/lieferschein/{delivery.id}").get_data(as_text=True)
    assert "Schüttgut verladen" in html and "Box 3 – Winterweizen" in html
    as_worker.post(f"/lieferschein/{delivery.id}/schuettgut", data={"store_id": str(box3.id), "net_kg": "15000"})
    assert bulk.stock(box3)["kg"] == 5000
    html = as_viewer.get(f"/lieferschein/{delivery.id}").get_data(as_text=True)
    assert "Winterweizen" in html and "lose" in html
    pdf = as_viewer.get(f"/lieferschein/{delivery.id}.pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    assert "Lieferschein" in as_viewer.get(f"/schuettgut/{box3.id}").get_data(as_text=True)

    # Lager-Übersicht und Bereichsseite zeigen den Bestand
    assert "Schüttgut" in as_viewer.get("/bereich/lager").get_data(as_text=True)
    assert "Winterweizen 5,0 t" in as_viewer.get("/bereich/lager").get_data(as_text=True)
