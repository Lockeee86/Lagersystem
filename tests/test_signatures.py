import base64
import io
from datetime import date

import pytest
from PIL import Image, ImageDraw

from app import sales, storage
from app.models import AuditEntry, Customer, Delivery
from app.storage import StorageError


def drawing(blank=False, size=(400, 150)):
    """Daten-URL wie aus dem Zeichenfeld: schwarzer Strich auf durchsichtigem Grund."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    if not blank:
        ImageDraw.Draw(img).line([(30, 100), (120, 40), (200, 110), (360, 50)], fill=(0, 0, 0, 255), width=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def delivery(db, seed):
    customer = Customer(name="Kartoffelhandel Beispiel")
    db.session.add(customer)
    db.session.flush()
    (box,) = storage.create_boxes(1, seed["bt"].id, seed["admin"])
    storage.update_contents(box, seed["admin"], seed["belana"].id, date(2026, 9, 23), None, None, 100, None)
    storage.store_box(box, seed["wh"].id, 1, 1, seed["admin"])
    d = sales.create_delivery(customer, date(2026, 10, 24), seed["admin"])
    sales.add_box(d, box, seed["admin"])
    db.session.commit()
    return d


def test_signature_image_is_cropped_black_on_white(db, delivery):
    sales.sign(delivery, "hof", "  Max Muster ", drawing())
    with Image.open(io.BytesIO(delivery.sign_farm)) as img:
        assert img.mode == "L" and img.width < 400 and img.height < 150          # zugeschnitten
        assert img.getpixel((0, 0)) == 255 and img.getextrema()[0] == 0          # weißer Grund, schwarzer Strich
    assert delivery.sign_farm_name == "Max Muster" and delivery.sign_farm_at is not None
    assert delivery.is_signed and not delivery.fully_signed
    for bad in (drawing(blank=True), "", "data:image/png;base64,kaputt", "data:text/plain;base64,aGFsbG8="):
        with pytest.raises(StorageError):
            sales.sign(delivery, "kunde", "Erika Beispiel", bad)
    with pytest.raises(StorageError, match="Namen"):
        sales.sign(delivery, "kunde", " ", drawing())
    with pytest.raises(StorageError, match="schon unterschrieben"):
        sales.sign(delivery, "hof", "Noch einmal", drawing())


def test_signed_delivery_is_locked_and_required_setting(db, seed, delivery):
    sales.sign(delivery, "hof", "Max Muster", drawing())
    with pytest.raises(StorageError, match="unterschrieben"):
        sales.set_weights(delivery, 1000, 500)
    with pytest.raises(StorageError, match="unterschrieben"):
        sales.remove_item(delivery.items[0], seed["admin"])

    # Pflicht: erst mit beiden Unterschriften abschließen
    sales.save_sign_required(True)
    assert sales.sign_required()
    with pytest.raises(StorageError, match="Hof und Empfänger"):
        sales.close_delivery(delivery)
    sales.sign(delivery, "kunde", "Erika Beispiel", drawing())
    sales.close_delivery(delivery)
    assert not delivery.is_open
    # abgeschlossen: Entfernen erst nach dem Öffnen
    with pytest.raises(StorageError, match="abgeschlossen"):
        sales.remove_signature(delivery, "kunde")
    sales.reopen_delivery(delivery)
    sales.remove_signature(delivery, "kunde")
    sales.remove_signature(delivery, "hof")
    assert not delivery.is_signed and delivery.sign_customer_name is None
    sales.set_weights(delivery, 1000, 500)                     # wieder änderbar


def test_sign_without_requirement_after_closing(db, delivery):
    sales.close_delivery(delivery)                             # keine Pflicht
    sales.sign(delivery, "kunde", "Erika Beispiel", drawing())  # später nachholen geht
    assert delivery.sign_customer


def test_pages_pdf_and_audit(as_admin, as_worker, as_viewer, db, seed, delivery):
    url = f"/lieferschein/{delivery.id}"
    html = as_worker.get(url).get_data(as_text=True)
    assert 'id="unterschrift"' in html and 'id="signModal"' in html and "Unterschreiben" in html
    assert f'data-sign-name="{seed["worker"].name}"' in html                         # Hof: eigener Name vorgewählt
    assert as_viewer.post(f"{url}/unterschrift/hof", data={"name": "X", "bild": drawing()}).status_code == 403
    assert as_worker.post(f"{url}/unterschrift/chef", data={"name": "X", "bild": drawing()}).status_code == 404

    r = as_worker.post(f"{url}/unterschrift/hof", data={"name": "Max Muster", "bild": drawing()})
    assert r.headers["Location"].endswith("#unterschrift")
    as_worker.post(f"{url}/unterschrift/kunde", data={"name": "Erika Beispiel", "bild": drawing()})
    d = db.session.get(Delivery, delivery.id)
    assert d.fully_signed
    img = as_viewer.get(f"{url}/unterschrift/kunde.png")
    assert img.status_code == 200 and img.mimetype == "image/png"
    html = as_viewer.get(url).get_data(as_text=True)
    assert "Erika Beispiel" in html and 'id="signModal"' not in html
    assert "unterschrieben" in as_viewer.get("/lieferscheine").get_data(as_text=True)

    pdf = as_viewer.get(f"{url}.pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF" and b"/Image" in pdf.data

    # Kunde/Waage über die Seite gesperrt, Entfernen nur Admin
    as_admin.post(f"{url}/kopf", data={"customer_id": d.customer_id, "vehicle": "Neu"})
    assert db.session.get(Delivery, delivery.id).vehicle is None
    assert as_worker.post(f"{url}/unterschrift/kunde/entfernen").status_code == 403
    as_admin.post(f"{url}/unterschrift/kunde/entfernen")
    assert not db.session.get(Delivery, delivery.id).sign_customer
    e = AuditEntry.query.filter_by(table="deliveries").order_by(AuditEntry.id.desc()).first()
    assert e.changes["sign_customer"] == ["…", "…"] and e.changes["sign_customer_name"] == ["Erika Beispiel", None]


def test_settings_switch(as_admin, as_worker, db, delivery):
    assert as_worker.post("/lieferscheine/einstellungen", data={"sign_required": "1"}).status_code == 403
    as_admin.post("/lieferscheine/einstellungen", data={"sign_required": "1"})
    assert sales.sign_required()
    html = as_admin.get(f"/lieferschein/{delivery.id}").get_data(as_text=True)
    assert "Vor dem Abschließen müssen Hof und Empfänger unterschreiben." in html
    as_admin.post(f"/lieferschein/{delivery.id}/abschliessen")
    assert db.session.get(Delivery, delivery.id).is_open
    as_admin.post("/lieferscheine/einstellungen", data={})
    assert not sales.sign_required()
