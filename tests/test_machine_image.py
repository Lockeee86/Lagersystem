import io
import os

from PIL import Image

from app import machines, uploads
from app.models import Machine


def jpeg(size=(800, 400)):
    buf = io.BytesIO()
    Image.new("RGB", size, (60, 120, 40)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def test_machine_image(app, as_admin, as_worker, as_viewer, db, seed):
    m = machines.create_machine("Testschlepper", "schlepper", seed["admin"])
    db.session.commit()
    # ohne Bild: Symbol der Art in der Liste
    html = as_viewer.get("/maschinen").get_data(as_text=True)
    assert "machine-thumb-empty" in html and "bi-truck-front" in html
    assert as_viewer.get(f"/maschine/{m.id}/bild.jpg").status_code == 404

    # Betrachter dürfen kein Bild setzen, Mitarbeiter schon
    assert as_viewer.post(f"/maschine/{m.id}/bild", data={"image": (jpeg(), "a.jpg")},
                          content_type="multipart/form-data").status_code == 403
    as_worker.post(f"/maschine/{m.id}/bild", data={"image": (jpeg(), "a.jpg")}, content_type="multipart/form-data")
    first = db.session.get(Machine, m.id).image
    folder = app.config["UPLOAD_DIR"]
    assert first and os.path.exists(os.path.join(folder, first))
    with Image.open(os.path.join(folder, uploads.thumb_name(first))) as thumb:
        assert thumb.size == (uploads.THUMB_SIDE, uploads.THUMB_SIDE)          # quadratische Vorschau
    html = as_viewer.get("/maschinen").get_data(as_text=True)
    assert f"/maschine/{m.id}/bild.jpg?klein=1" in html
    assert as_viewer.get(f"/maschine/{m.id}/bild.jpg?klein=1").status_code == 200
    assert f"/maschine/{m.id}/bild.jpg" in as_viewer.get(f"/maschine/{m.id}").get_data(as_text=True)

    # ersetzen: alte Dateien verschwinden; kein Bild → Fehlermeldung
    as_worker.post(f"/maschine/{m.id}/bild", data={"image": (jpeg((300, 600)), "b.jpg")},
                   content_type="multipart/form-data")
    second = db.session.get(Machine, m.id).image
    assert second != first and not os.path.exists(os.path.join(folder, first))
    assert not os.path.exists(os.path.join(folder, uploads.thumb_name(first)))
    as_worker.post(f"/maschine/{m.id}/bild", data={"image": (io.BytesIO(b"kaputt"), "x.jpg")},
                   content_type="multipart/form-data")
    assert db.session.get(Machine, m.id).image == second

    # entfernen
    as_worker.post(f"/maschine/{m.id}/bild", data={"entfernen": "1"})
    assert db.session.get(Machine, m.id).image is None and not os.path.exists(os.path.join(folder, second))


def test_fuel_only_for_machines_with_tank(as_admin, as_worker, db, seed):
    import pytest
    from app import fuel
    from app.storage import StorageError
    tractor = machines.create_machine("Testschlepper", "schlepper", seed["admin"])
    trailer = machines.create_machine("Testkipper", "anhaenger", seed["admin"])
    combine = machines.create_machine("Testdrescher", "maehdrescher", seed["admin"],
                                      counter_unit=machines.DEFAULT_UNITS["maehdrescher"])
    db.session.commit()
    assert tractor.uses_fuel and combine.uses_fuel and not trailer.uses_fuel
    assert combine.counter_unit == "h" and any("Saisonkontrolle" in t.name for t in combine.tasks)
    # Anhänger: keine Tank-Karte, Tanken abgewiesen, nicht in der Auswahl im Tankbuch
    assert 'id="diesel"' not in as_worker.get(f"/maschine/{trailer.id}").get_data(as_text=True)
    assert 'id="diesel"' in as_worker.get(f"/maschine/{tractor.id}").get_data(as_text=True)
    with pytest.raises(StorageError, match="wird nicht getankt"):
        fuel.refuel(trailer, seed["admin"], 50)
    html = as_worker.get("/tankbuch").get_data(as_text=True)
    select = html.split('id="r_machine"')[1].split("</select>")[0]
    assert "Testschlepper" in select and "Testkipper" not in select

    # je Maschine umstellbar; gleich wie Voreinstellung → wieder „nach Art“
    form = {"name": "Testkipper", "category": "anhaenger", "refuels_form": "1", "refuels": "1"}
    as_admin.post(f"/maschine/{trailer.id}/bearbeiten", data=form)
    assert db.session.get(Machine, trailer.id).refuels is True and trailer.uses_fuel
    as_admin.post(f"/maschine/{trailer.id}/bearbeiten", data={**form, "refuels": ""})
    assert db.session.get(Machine, trailer.id).refuels is None and not trailer.uses_fuel
    # neue Gruppe erscheint in der Liste
    assert "Mähdrescher / Häcksler" in as_worker.get("/maschinen").get_data(as_text=True)
