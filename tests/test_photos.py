import io
import os
from datetime import date
from decimal import Decimal

from PIL import Image

from app import bulk, sales, storage
from app.models import BulkStore, Customer, Photo, PlotYear


def jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (120, 160, 60)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def upload(client, url, **extra):
    return client.post(url, data={"photos": (jpeg(), "bild.jpg"), **extra}, content_type="multipart/form-data")


def test_photos_on_box_plot_store_delivery(app, as_admin, as_worker, as_viewer, db, seed):
    admin, worker = seed["admin"], seed["worker"]
    (box,) = storage.create_boxes(1, seed["bt"].id, admin)
    store = bulk.save_store(BulkStore(), "Box 7", "box", capacity_t=Decimal("50"))
    customer = Customer(name="Hofladen Beispiel")
    db.session.add(customer)
    delivery = sales.create_delivery(customer, date(2026, 9, 1), admin)
    db.session.commit()
    plot = seed["plot"]

    # Betrachter dürfen nicht hochladen
    assert upload(as_viewer, f"/fotos/kiste/{box.id}").status_code == 403
    r = upload(as_worker, f"/fotos/kiste/{box.id}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/kiste/{box.code}#fotos")
    upload(as_worker, f"/fotos/schuettlager/{store.id}")
    upload(as_worker, f"/fotos/lieferschein/{delivery.id}")
    # Schlag: Anbaujahr wird bei Bedarf angelegt
    r = upload(as_worker, f"/fotos/schlag/{plot.id}", year="2026")
    assert r.headers["Location"].endswith(f"/schlag/{plot.id}#jahr-2026")
    entry = PlotYear.query.filter_by(plot_id=plot.id, year=2026).one()
    assert len(entry.photos) == 1 and len(box.photos) == 1 and len(store.photos) == 1 and len(delivery.photos) == 1
    assert box.photos[0].user_id == worker.id

    # ungültige Datei → Fehlermeldung, nichts gespeichert
    as_worker.post(f"/fotos/kiste/{box.id}", data={"photos": (io.BytesIO(b"kein bild"), "x.jpg")},
                   content_type="multipart/form-data")
    assert len(box.photos) == 1

    # Anzeige auf den Seiten
    for url in (f"/kiste/{box.code}", f"/schuettgut/{store.id}", f"/lieferschein/{delivery.id}", f"/schlag/{plot.id}"):
        html = as_viewer.get(url).get_data(as_text=True)
        assert "photo-thumb" in html and "photo-add" not in html, url
    assert "photo-add" in as_worker.get(f"/kiste/{box.code}").get_data(as_text=True)

    # Löschen: Betrachter nicht, fremde Fotos nur der Admin; Datei verschwindet nach dem Commit
    photo = box.photos[0]
    path = os.path.join(app.config["UPLOAD_DIR"], photo.filename)
    assert os.path.exists(path)
    assert as_viewer.post(f"/foto/{photo.id}/loeschen").status_code == 403
    upload(as_admin, f"/fotos/kiste/{box.id}")
    admin_photo = Photo.query.filter_by(user_id=admin.id).one()
    assert as_worker.post(f"/foto/{admin_photo.id}/loeschen").status_code == 403
    as_worker.post(f"/foto/{photo.id}/loeschen", data={"back": f"/kiste/{box.code}"})
    assert db.session.get(Photo, photo.id) is None and not os.path.exists(path)

    # Kiste löschen nimmt ihre Fotos samt Dateien mit
    other = os.path.join(app.config["UPLOAD_DIR"], admin_photo.filename)
    as_admin.post(f"/kiste/{box.code}/loeschen")
    assert Photo.query.filter_by(box_id=box.id).count() == 0 and not os.path.exists(other)
