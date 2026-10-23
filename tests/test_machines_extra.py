"""Tankbuch, Ersatzteile, Dokumente, Lieferanten."""
import io
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import fuel, machines, push, purchases
from app.models import FuelEntry, MachineDocument, MachinePart, PurchaseRequest, PushSubscription, Supplier
from app.storage import StorageError

from .conftest import make_user


@pytest.fixture
def tractor(db, seed):
    m = machines.create_machine("Fendt 724", "schlepper", seed["admin"])
    m.counter_unit = "h"
    m.counter = 1000
    db.session.commit()
    return m


def test_tank_level_and_consumption(db, seed, tractor):
    admin = seed["admin"]
    assert fuel.level()[0] == 0
    fuel.delivery(admin, "3000", Decimal("1.459"))
    fuel.refuel(tractor, admin, "200", counter=1010)
    fuel.refuel(tractor, admin, "150", counter=1020)            # 150 l / 10 h
    fuel.refuel(tractor, admin, "170.5", counter=1030)          # 170,5 l / 10 h
    db.session.commit()
    assert fuel.level()[0] == Decimal("2479.5")
    assert tractor.counter == 1030                              # höherer Stand an der Maschine übernommen
    c = fuel.consumption(tractor)
    assert c["unit"] == "l/h" and round(c["value"], 1) == 16.0 and c["count"] == 2
    assert fuel.liters_in_year(tractor.id) == Decimal("520.5")
    assert fuel.last_price() == Decimal("1.459")

    # Peilung setzt den Stand neu, spätere Buchungen rechnen weiter
    fuel.dip(admin, "2400")
    fuel.refuel(tractor, admin, "100", counter=1035)
    db.session.commit()
    assert fuel.level()[0] == Decimal("2300")

    with pytest.raises(StorageError):
        fuel.refuel(tractor, admin, "50", counter=900)          # Zähler rückwärts
    with pytest.raises(StorageError):
        fuel.refuel(tractor, admin, "0")
    assert fuel.parse_liters("1.250,5") == Decimal("1250.5") and fuel.parse_liters("85.5") == Decimal("85.5")


def test_tank_pages_and_warning(app, as_admin, as_worker, as_viewer, db, seed, tractor):
    push.outbox.clear()
    db.session.add(PushSubscription(user_id=seed["admin"].id, endpoint="https://push.example/a", p256dh="k", auth="a"))
    db.session.commit()
    as_admin.post("/tankbuch/einstellungen", data={"capacity": "5000", "warn": "500"})
    assert fuel.settings() == {"capacity": 5000, "warn": 500}
    as_admin.post("/tankbuch/lieferung", data={"liters": "700", "price": "1,529", "supplier": "Raiffeisen"})
    assert as_worker.post("/tankbuch/lieferung", data={"liters": "700"}).status_code == 403
    assert as_viewer.post("/tankbuch/tanken", data={"machine_id": tractor.id, "liters": "10"}).status_code == 403

    # Mitarbeiter tankt an der Maschine (QR → Maschinenseite) und unterschreitet die Warnschwelle
    r = as_worker.post(f"/maschine/{tractor.id}/getankt", data={"liters": "250", "counter": "1012"})
    assert r.headers["Location"].endswith("#diesel")
    assert fuel.level()[0] == Decimal("450") and tractor.counter == 1012
    msg = [m for m in push.outbox if m["tag"] == "tank"]
    assert len(msg) == 1 and msg[0]["title"] == "Hoftank: noch 450 l"
    as_worker.post("/tankbuch/tanken", data={"machine_id": tractor.id, "liters": "10"})
    assert len([m for m in push.outbox if m["tag"] == "tank"]) == 1     # nur beim Unterschreiten

    html = as_worker.get("/tankbuch").get_data(as_text=True)
    assert "440 l" in html and "Unter der Warnschwelle" in html and "Fendt 724" in html
    assert "Lieferung eintragen" not in html                            # nur für Besteller
    assert "Fendt 724" in as_admin.get(f"/tankbuch?maschine={tractor.id}").get_data(as_text=True)
    page = as_worker.get(f"/maschine/{tractor.id}").get_data(as_text=True)
    assert "Getankt" in page and "260 l" in page

    entry = FuelEntry.query.filter_by(kind="lieferung").one()
    assert entry.supplier is None and entry.price_per_liter == Decimal("1.529")
    assert as_worker.post(f"/tankbuch/{entry.id}/loeschen").status_code == 403
    as_admin.post(f"/tankbuch/{entry.id}/loeschen")
    assert FuelEntry.query.filter_by(kind="lieferung").count() == 0


def test_suppliers_and_parts(app, as_admin, as_worker, db, seed, tractor):
    as_admin.post("/lieferanten", data={"name": "Landtechnik Meyer", "phone": "05371 1234", "customer_no": "K-77",
                                        "website": "meyer-landtechnik.de"})
    meyer = Supplier.query.one()
    assert meyer.website == "https://meyer-landtechnik.de"
    assert as_worker.post("/lieferanten", data={"name": "X"}).status_code == 403
    as_admin.post("/lieferanten", data={"name": "landtechnik meyer"})                 # doppelt
    assert Supplier.query.count() == 1
    assert "05371 1234" in as_worker.get("/lieferanten").get_data(as_text=True)

    # Mitarbeiter legt ein Teil an, mit Lieferant aus dem Verzeichnis
    as_worker.post(f"/maschine/{tractor.id}/teil", data={"name": "Ölfilter Motor", "part_number": "F716200060010",
                                                          "manufacturer": "AGCO", "supplier": "Landtechnik Meyer",
                                                          "quantity": "2 Stück"})
    part = MachinePart.query.one()
    assert part.supplier == meyer and part.machine == tractor

    # Nochmal bestellen: Formular vorbefüllt, Bestellung verknüpft, Lieferant vorgeschlagen
    html = as_worker.get(f"/bestellungen?neu=1&teil={part.id}").get_data(as_text=True)
    assert 'value="Ölfilter Motor"' in html and "Teilenummer: F716200060010" in html and f'name="part_id" value="{part.id}"' in html
    as_worker.post("/bestellungen/neu", data={"title": "Ölfilter Motor", "quantity": "2 Stück", "part_id": str(part.id),
                                              "machine_id": str(tractor.id)})
    req = PurchaseRequest.query.one()
    assert req.part == part and req.supplier_id == meyer.id and req.supplier == "Landtechnik Meyer"
    assert part.last_purchase == req
    detail = as_admin.get(f"/bestellung/{req.id}").get_data(as_text=True)
    assert "Kd.-Nr. K-77" in detail and "Ölfilter Motor" in detail

    # Besteller tippt einen anderen Lieferanten frei ein → keine Verknüpfung
    purchases.update_request(req, seed["admin"], "bestellt", supplier="Ebay")
    assert req.supplier_id is None

    # Suche nach Teilenummer
    assert "Ölfilter Motor" in as_worker.get("/suche?q=716200060").get_data(as_text=True)

    # Löschen eines verwendeten Lieferanten blendet nur aus; Teil löschen nur Admin
    as_admin.post(f"/lieferant/{meyer.id}/loeschen")
    assert db.session.get(Supplier, meyer.id).active is False
    assert as_worker.post(f"/maschine/{tractor.id}/teil", data={"id": part.id, "name": "x", "delete": "1"}).status_code == 403
    as_admin.post(f"/maschine/{tractor.id}/teil", data={"id": part.id, "name": "x", "delete": "1"})
    assert MachinePart.query.count() == 0 and req.part_id is None


def test_documents(app, as_admin, as_worker, as_viewer, db, seed, tractor):
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    r = as_worker.post(f"/maschine/{tractor.id}/dokument",
                       data={"file": (io.BytesIO(pdf), "Betriebsanleitung 724.pdf"), "kind": "anleitung"},
                       content_type="multipart/form-data")
    doc = MachineDocument.query.one()
    assert doc.title == "Betriebsanleitung 724" and doc.mimetype == "application/pdf" and doc.size == len(pdf)

    # Kein PDF/Bild (z. B. HTML mit .pdf-Endung) wird abgelehnt
    as_worker.post(f"/maschine/{tractor.id}/dokument",
                   data={"file": (io.BytesIO(b"<html><script>alert(1)</script>"), "boese.pdf")},
                   content_type="multipart/form-data")
    assert MachineDocument.query.count() == 1

    r = as_viewer.get(f"/dokument/{doc.id}")
    assert r.status_code == 200 and r.data == pdf and r.mimetype == "application/pdf"
    assert "sandbox" in r.headers["Content-Security-Policy"]
    assert as_viewer.get(f"/dokument/{doc.id}?download=1").headers["Content-Disposition"].startswith("attachment")
    assert "Betriebsanleitung 724" in as_viewer.get(f"/maschine/{tractor.id}").get_data(as_text=True)

    # Löschen: der Hochladende oder ein Admin
    other = make_user("jan", "mitarbeiter")
    client = app.test_client()
    client.post("/login", data={"username": "jan", "password": "geheim123"})
    assert client.post(f"/dokument/{doc.id}/loeschen").status_code == 403
    as_worker.post(f"/dokument/{doc.id}/loeschen")
    assert MachineDocument.query.count() == 0

