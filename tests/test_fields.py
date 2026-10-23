import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from app import fields, storage
from app.models import Plot, PlotYear

PLACEMARK = """<Placemark><name>{kml_name}</name><description>
Erstellt: 11.06.2025 00:00;
SchlagBez: {name};
SchlagNr: {nr};
SchlagHa: {ha};
KulturBez: {crop};
KulturEPPO: {eppo};
VorfruchtBez: {prev};
VorfruchtEPPO: ;
Bodenart: Sand;
RotesGebiet: {red};
</description><Polygon><outerBoundaryIs><LinearRing><coordinates>
{lon},52.42 {lon2},52.42 {lon2},52.43 {lon},52.43 {lon},52.42
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"""


def kml(*rows):
    marks = []
    for i, (name, nr, ha, crop, eppo, prev, red) in enumerate(rows):
        lon = 10.5 + i * 0.02
        marks.append(PLACEMARK.format(kml_name=f"{name} ( Nr.{nr} )", name=name, nr=nr, ha=ha, crop=crop, eppo=eppo,
                                      prev=prev, red=red, lon=lon, lon2=round(lon + 0.01, 4)))
    return ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            + "".join(marks) + "</Document></kml>").encode()


SAMPLE = kml(
    ("Mittelwiese", "16", "8.3257", "Kartoffel (Pflanz)", "SOLTU", "Winterroggen", "N"),
    ("Obere Wiese", "2", "5.19", "Zuckerrüben", "BEAVA", "Winterroggen", "N"),
    ("Obere Wiese", "1002", "0.423", "Ackergras", "CERAR", "Kleegras", "N"),
    ("Kleiner Garten", "7", "6.61", "Wickroggen", "", "Wickroggen", "N"),
    ("Bachfeld rechts (1)", "7", "2.5067", "keine", "", "keine", "J"),
)


def test_parse_myfarm24():
    entries = fields.parse_kml("export.kml", SAMPLE)
    assert len(entries) == 5
    e = entries[0]
    assert (e["name"], e["number"], e["area_ha"], e["crop"], e["crop_eppo"]) == \
        ("Mittelwiese", "16", Decimal("8.3257"), "Kartoffel (Pflanz)", "SOLTU")
    assert e["prev_crop"] == "Winterroggen" and e["soil"] == "Sand" and e["red_area"] is False
    assert entries[4]["crop"] is None and entries[4]["red_area"] is True
    assert len(e["rings"]) == 1 and len(e["rings"][0]) == 5


def test_parse_kmz_generic_and_invalid():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("doc.kml", SAMPLE)
    assert len(fields.parse_kml("export.kmz", buf.getvalue())) == 5
    generic = (b'<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><name>Hofwiese</name><Polygon><outerBoundaryIs>'
               b'<LinearRing><coordinates>10.5,52.42 10.51,52.42 10.51,52.43 10.5,52.43</coordinates></LinearRing>'
               b'</outerBoundaryIs></Polygon></Placemark></kml>')
    (e,) = fields.parse_kml("x.kml", generic)
    assert e["name"] == "Hofwiese" and e["number"] is None and 70 < e["area_ha"] < 80   # aus dem Umriss
    for bad in (b"kein xml", b'<!DOCTYPE x [<!ENTITY a "b">]><kml/>', b'<kml xmlns="http://www.opengis.net/kml/2.2"/>'):
        with pytest.raises(fields.FieldImportError):
            fields.parse_kml("x.kml", bad)


def test_import_and_reimport(db, seed):
    seed["plot"].name = "Kleiner Garten"        # von Hand angelegt, ohne Nummer → wird wiedererkannt
    db.session.commit()
    created, updated = fields.apply_import(fields.parse_kml("x.kml", SAMPLE), 2026)
    db.session.commit()
    assert (created, updated) == (4, 1)
    names = sorted(p.name for p in Plot.query)
    assert names == ["Obere Wiese", "Obere Wiese (1002)", "Bachfeld rechts (1)", "Mittelwiese", "Kleiner Garten"]
    heeg = seed["plot"]
    assert heeg.number == "7" and heeg.rings and heeg.year(2026).crop == "Wickroggen"
    fw = Plot.query.filter_by(name="Mittelwiese").one()
    assert fw.year(2026).is_potato and fw.year(2025).crop == "Winterroggen"

    # Pflanzung eintragen, dann erneut importieren: nichts doppelt, Pflanzung bleibt, Vorjahr nicht überschrieben
    fw.year(2026).planted_on = date(2026, 4, 20)
    fw.year(2025).crop = "Winterroggen (eigene Angabe)"
    db.session.commit()
    rows, missing = fields.plan_import(fields.parse_kml("x.kml", SAMPLE), 2026)
    assert all(r["plot"] for r in rows) and not any(r["changes"] for r in rows) and not missing
    changed = SAMPLE.replace(b"SchlagHa: 8.3257", b"SchlagHa: 8.4").replace(b"SchlagBez: Kleiner Garten", b"SchlagBez: Kleiner Heeg")
    created, updated = fields.apply_import(fields.parse_kml("x.kml", changed), 2026)
    db.session.commit()
    assert (created, updated) == (0, 2) and Plot.query.count() == 5
    assert fw.area_ha == Decimal("8.4") and fw.year(2026).planted_on == date(2026, 4, 20)
    assert fw.year(2025).crop == "Winterroggen (eigene Angabe)"
    assert heeg.name == "Kleiner Heeg"     # umbenannt, am unveränderten Umriss erkannt (Nr. 7 ist doppelt)
    assert PlotYear.query.filter_by(year=2026).count() == 5


def test_yield_per_hectare(db, seed):
    fields.apply_import(fields.parse_kml("x.kml", SAMPLE), 2026)
    fw = Plot.query.filter_by(name="Mittelwiese").one()
    boxes = storage.create_boxes(3, seed["bt"].id, seed["admin"])     # Standardkiste 400 kg
    for b in boxes:
        storage.update_contents(b, seed["admin"], seed["belana"].id, date(2026, 9, 20), fw.id, None, 100, None)
    db.session.commit()
    y = fields.yields(2026)[fw.id]
    assert y["kg"] == 1200 and y["boxes"] == 3 and y["varieties"] == {"Belana": 1200}
    assert fields.t_per_ha(1200, fw.area_ha) == 0.1


def test_pages(as_admin, as_worker, as_viewer, db, seed):
    r = as_admin.post("/admin/schlaege/kml", data={"file": (io.BytesIO(SAMPLE), "export.kml"), "year": "2026"},
                      content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Obere Wiese" in html and Plot.query.count() == 1     # nur Vorschau
    r = as_admin.post("/admin/schlaege/kml/uebernehmen")
    assert r.headers["Location"].endswith("/schlagkarte?jahr=2026") and Plot.query.count() == 6
    assert as_worker.post("/admin/schlaege/kml/uebernehmen").status_code == 403

    fw = Plot.query.filter_by(name="Mittelwiese").one()
    html = as_viewer.get("/schlagkarte?jahr=2026").get_data(as_text=True)
    assert "Mittelwiese" in html and "Kartoffel (Pflanz)" in html
    assert "Mittelwiese" in as_viewer.get("/schlagkarte?jahr=2026&kartoffeln=1").get_data(as_text=True)
    assert "Obere Wiese" not in as_viewer.get("/schlagkarte?jahr=2026&kartoffeln=1").get_data(as_text=True)
    assert as_viewer.get(f"/schlag/{fw.id}").status_code == 200

    data = {"year": "2026", "variety_id": str(seed["belana"].id), "planted_on": "2026-04-22",
            "seed_note": "eigene Partie E", "crop": "soll nicht gehen"}
    assert as_viewer.post(f"/schlag/{fw.id}/jahr", data=data).status_code == 403
    as_worker.post(f"/schlag/{fw.id}/jahr", data=data)
    y = fw.year(2026)
    assert y.variety_id == seed["belana"].id and y.planted_on == date(2026, 4, 22)
    assert y.crop == "Kartoffel (Pflanz)"          # Kultur ändert nur der Admin
    assert "eigene Partie E" in as_viewer.get(f"/schlag/{fw.id}").get_data(as_text=True)
