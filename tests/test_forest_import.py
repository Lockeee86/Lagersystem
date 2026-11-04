"""Übernahme aus dem Altsystem – nur mit erfundenen Daten."""
import io
from datetime import date, timedelta

from app import forest, forest_import
from app.models import ForestBurial, ForestContract, ForestPlace, ForestTree, Setting

POS_HEAD = ["Personenname", "Ruhebaum", "Vertragsende", "Ruhebaumnummer", "Name Vertragspartner", "Vertragsdatum",
            "Sterbeort", "Vertragspartner Geburtsdatum", "Sterbedatum", "Vertragsbeginn", "Umbettunghinweis", "Gravur",
            "Nummer", "Bestattungsdatum", "Bestattungsnummer"]
TREE_HEAD = ["wird belegt", "Nutzungszeit", "Nummer formatiert", "Feldbezeichnung", "Nummer", "Art", "Stammdurchmesser",
             "Typ", "Kategorie", "Feld"]


def _xml(sheets):
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
           'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">']
    for name, rows in sheets:
        out.append(f'<Worksheet ss:Name="{name}"><Table>')
        for row in rows:
            out.append("<Row>" + "".join(
                "<Cell/>" if v is None else f'<Cell><Data ss:Type="String">{v}</Data></Cell>' for v in row) + "</Row>")
        out.append("</Table></Worksheet>")
    out.append("</Workbook>")
    return "\n".join(out).encode()


def _pos(tree, ruhebaum, no, person=None, holder=None, signed=None, ends=None, burial=None, died=None):
    row = dict.fromkeys(POS_HEAD)
    row.update({"Ruhebaumnummer": tree, "Ruhebaum": ruhebaum, "Nummer": f"{no:02d}", "Personenname": person,
                "Name Vertragspartner": holder, "Vertragsdatum": signed, "Vertragsbeginn": signed,
                "Vertragsende": ends, "Bestattungsdatum": burial, "Sterbedatum": died,
                "Sterbeort": "Musterstadt" if died else None, "Bestattungsnummer": "0001" if burial else None,
                "Vertragspartner Geburtsdatum": "1960-01-02T00:00:00.000" if holder else None})
    return [row[h] for h in POS_HEAD]


def _file():
    t = "T00:00:00.000"
    future = (date.today() + timedelta(days=10)).isoformat() + t
    positions = [POS_HEAD]
    for no in range(1, 25):
        positions.append(_pos("A 7", "A 7 (Familien/Freundesbaum/Stieleiche)", no))
    for no in range(1, 25):
        positions.append(_pos("B 12", "B 12 (Einzelbaum/Rotbuche)", no))
    for no in range(1, 31):
        positions.append(_pos("Waldruhefläche A", "Waldruhefläche A (Waldruhefläche/)", no))
    positions[1] = _pos("A 7", "A 7 (Familien/Freundesbaum/Stieleiche)", 1, "Hans Muster", "Frau\nErika Muster\nWeg 1\n12345 Musterstadt",
                        "2023-04-01" + t, "2110-12-31" + t, "2023-05-02" + t, "2023-04-20" + t)
    positions[2] = _pos("A 7", "A 7 (Familien/Freundesbaum/Stieleiche)", 2, "Erika Muster", "Frau\nErika Muster\nWeg 1\n12345 Musterstadt",
                        "2023-04-01" + t, "2110-12-31" + t)
    positions[25] = _pos("B 12", "B 12 (Einzelbaum/Rotbuche)", 1, None, "Herrn\nMax Beispiel\nAllee 2\n12345 Musterstadt",
                         "2024-01-10" + t, "2069-01-10" + t)
    positions[49] = _pos("Waldruhefläche A", "Waldruhefläche A (Waldruhefläche/)", 1, "Karl Probe", None, None, None,
                         future, "2026-01-01" + t)
    positions.append([None] * len(POS_HEAD))
    positions.append(_pos("A neben 9", "A neben 9 (Gemeinschaftsbaum/Birke)", 1))
    trees = [TREE_HEAD, ["0", "0", "B 12", "B", "B 12", "Rotbuche", "40", "Einzelbaum", "3", "2"],
             ["0", "0", "B 99", "B", "B 99", "Kiefer", "10", "Sternchenbaum", "1", "2"]]
    return _xml([("Baumposition", positions), ("Ruhebäume", trees)])


def test_helpers():
    assert forest_import.tree_key("A 147") == ("A", 147, None) and forest_import.tree_key("b12") == ("B", 12, None)
    assert forest_import.tree_key("Waldruhefläche B") == ("B", 0, "flaeche") and forest_import.tree_key("B neben 3") is None
    assert forest_import.kind_species("A 9 (Familien/Freundesbaum/Stieleiche)") == ("familie", "Stieleiche")
    assert forest_import.kind_species("A 9 (Sternchenbaum/Birke)") == ("sternchen", "Birke")
    assert forest_import.holder("Frau\nErika Muster\nWeg 1\n12345 Musterstadt") == ("Erika Muster", "Weg 1\n12345 Musterstadt")
    assert forest_import._date("2023-04-01T00:00:00.000") == date(2023, 4, 1) and forest_import._date("02.03.2024") == date(2024, 3, 2)


def test_apply(app, db, seed):
    admin = seed["admin"]
    parsed = forest_import.parse_file(_file())
    assert len(parsed["positions"]) == 79 and len(parsed["trees"]) == 2
    with app.test_request_context():
        stats = forest_import.apply(parsed, admin)
    db.session.commit()
    assert stats["trees_new"] == 4 and stats["skipped_rows"] == 1 and stats["contracts"] == 2
    assert stats["burials"] == 2 and stats["persons"] == 1
    a7 = ForestTree.query.filter_by(field="A", number=7).one()
    assert a7.kind == "familie" and a7.species == "Stieleiche" and len(a7.places) == 24 and a7.section == "Feld A"
    assert a7.label == "Baum A 7" and a7.code == "W-A007" and a7.key == "A7"
    b12 = ForestTree.query.filter_by(field="B", number=12).one()
    assert (b12.kind, b12.category, b12.trunk_cm) == ("einzel", "3", 40)
    assert ForestTree.query.filter_by(field="B", number=99).one().kind == "sternchen"
    area = ForestTree.query.filter_by(kind="flaeche").one()
    assert area.name == "Waldruhefläche A" and len(area.places) == 30 and area.places[0].label == "Waldruhefläche A, Platz 1"

    contract = a7.places[0].contract
    assert contract is a7.places[1].contract and contract.holder == "Erika Muster"
    assert contract.address == "Weg 1\n12345 Musterstadt" and contract.ends_on == date(2110, 12, 31)
    assert contract.holder_birthday == date(1960, 1, 2) and contract.number.startswith("V-2023-")
    burial = a7.places[0].burials[0]
    assert (burial.deceased, burial.day, burial.died_on, burial.died_in) == ("Hans Muster", date(2023, 5, 2),
                                                                            date(2023, 4, 20), "Musterstadt")
    assert burial.dig_task is None                                   # vergangen → keine Aufgaben
    assert a7.places[1].person == "Erika Muster" and a7.places[1].state == "reserviert"
    upcoming = area.places[0].burials[0]
    assert upcoming.dig_task is not None and upcoming.contract is None

    # zweiter Lauf ändert nichts
    with app.test_request_context():
        again = forest_import.apply(parsed, admin)
    db.session.commit()
    assert again.get("contracts", 0) == 0 and again.get("burials", 0) == 0 and again["trees_known"] == 4
    assert ForestContract.query.count() == 2 and ForestBurial.query.count() == 2

    # ohne Personen
    db.session.query(ForestBurial).delete()
    for p in ForestPlace.query:
        p.contract, p.person = None, None
    db.session.query(ForestContract).delete()
    db.session.commit()
    with app.test_request_context():
        forest_import.apply(parsed, admin, with_people=False)
    db.session.commit()
    assert {c.holder for c in ForestContract.query} == {"unbekannt"}
    assert {b.deceased for b in ForestBurial.query} == {"unbekannt"} and ForestContract.query.first().address is None


def test_route_and_keys(app, db, seed, as_admin, as_worker):
    from app.models import AuditEntry
    seed["admin"].forest = seed["worker"].forest = True
    db.session.commit()
    data = {"file": (io.BytesIO(_file()), "export.xml"), "with_people": "1", "action": "check"}
    r = as_admin.post("/wald/baeume/altsystem", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert "Vorschau" in r.get_data(as_text=True) and ForestTree.query.count() == 0
    data = {"file": (io.BytesIO(_file()), "export.xml"), "with_people": "1", "action": "apply"}
    r = as_admin.post("/wald/baeume/altsystem", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert "Übernommen" in r.get_data(as_text=True) and ForestTree.query.count() == 4
    assert AuditEntry.query.filter_by(action="import", table="forest_trees").count() == 1
    assert as_worker.post("/wald/baeume/altsystem", data={}).status_code == 403
    bad = {"file": (io.BytesIO(b"kein export"), "x.txt"), "action": "check"}
    assert "Excel-XML" in as_admin.post("/wald/baeume/altsystem", data=bad, content_type="multipart/form-data",
                                        follow_redirects=True).get_data(as_text=True)

    for url in ("/wald/baum/A7", "/wald/baum/B12", "/wald/baum/A0", "/wald/baeume", "/wald", "/wald/karte",
                "/wald/vertraege", "/wald/beisetzungen"):
        assert as_admin.get(url).status_code == 200, url
    assert "Erika Muster" in as_admin.get("/wald/baum/A7").get_data(as_text=True)
    assert "vorgesehen: Erika Muster" not in as_worker.get("/wald/baum/A7").get_data(as_text=True)
    assert as_admin.get("/wald/suche?baum=a 7").headers["Location"].endswith("/wald/baum/A7")
    assert as_admin.get("/wald/suche?baum=W-B012").headers["Location"].endswith("/wald/baum/B12")
    tree = ForestTree.query.filter_by(field="A", number=7).one()
    assert as_admin.get(f"/wald/baum/id/{tree.id}").headers["Location"].endswith("/wald/baum/A7")
    assert as_worker.get("/scan?code=W-A007").headers["Location"].endswith("/wald/baum/A7")
    # Beisetzung über „A 7“
    as_worker.post("/wald/beisetzung", data={"tree": "A 7", "place": "5", "deceased": "Gerda Muster",
                                             "day": (date.today() + timedelta(days=3)).isoformat()})
    assert tree.places[4].burials[0].deceased == "Gerda Muster"
    # Feld beim Anlegen, gleiche Nummer in anderem Feld erlaubt
    as_admin.post("/wald/baeume/anlegen", data={"field": "c", "first": "7", "last": "8", "places": "24"})
    assert ForestTree.query.filter_by(field="C").count() == 2


def test_contract_end_defaults(app, db, seed):
    seed["admin"].forest = True
    forest.create_trees(1, 2, places=4, kind="einzel", field="A")
    forest.create_trees(3, 3, places=4, field="A")
    db.session.commit()
    single = ForestTree.query.filter_by(number=1).one().places[:1]
    shared = ForestTree.query.filter_by(number=3).one().places[:1]
    signed = date(2026, 3, 1)
    assert forest.default_end(signed, shared) == date(2125, 3, 1)               # 99 Jahre
    Setting.put("forest_end", "31.12.2110")
    Setting.put("forest_single_years", "45")
    db.session.commit()
    assert forest.default_end(signed, shared) == date(2110, 12, 31)
    assert forest.default_end(signed, single) == date(2071, 3, 1)
    c = forest.save_contract(None, seed["admin"], "Erika Muster", signed, shared, ends_on=date(2110, 12, 31),
                             holder_birthday=date(1960, 1, 2))
    assert c.ends_on == date(2110, 12, 31) and c.holder_birthday == date(1960, 1, 2)
    import pytest
    from app.storage import StorageError
    with pytest.raises(StorageError):
        forest.save_contract(c, seed["admin"], "Erika Muster", signed, shared, ends_on=date(2020, 1, 1))


def _shapefile_zip(records):
    """Minimales Punkt-Shapefile: records = [(nummer, geokoordin, (x, y))] – erfundene Lagen."""
    import struct
    import zipfile
    fields = [("nummer", 20), ("geokoordin", 40)]
    header_len = 32 + 32 * len(fields) + 1
    record_len = 1 + sum(length for _n, length in fields)
    dbf = bytearray(struct.pack("<BBBBIHH20x", 3, 126, 1, 1, len(records), header_len, record_len))
    for name, length in fields:
        dbf += name.encode().ljust(11, b"\0") + b"C" + b"\0" * 4 + bytes([length]) + b"\0" * 15
    dbf += b"\r"
    for nummer, geo, _xy in records:
        dbf += b" " + nummer.encode().ljust(20) + geo.encode().ljust(40)
    dbf += b"\x1a"
    body = b""
    for i, (_n, _g, (x, y)) in enumerate(records, start=1):
        content = struct.pack("<i2d", 1, x, y)
        body += struct.pack(">2i", i, len(content) // 2) + content
    shp = struct.pack(">7i", 9994, 0, 0, 0, 0, 0, (100 + len(body)) // 2) + struct.pack("<2i4d4d", 1000, 1, 0, 0, 0, 0,
                                                                                       0, 0, 0, 0) + body
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Wald_Point.dbf", bytes(dbf))
        z.writestr("Wald_Point.shp", shp)
        z.writestr("Wald_Point.prj", 'PROJCS["ETRS_1989_UTM_Zone_32N"]')
        z.writestr("Tabellenexport - Baumposition.xml", _file())
    return buf.getvalue()


def test_utm():
    lat, lon = forest_import.utm_to_latlon(500000, 0, zone=32)
    assert abs(lat) < 1e-9 and abs(lon - 9.0) < 1e-9
    lat, lon = forest_import.utm_to_latlon(500000, 5538630.7, zone=32)            # ~50° N am Mittelmeridian
    assert abs(lat - 50.0) < 1e-4 and abs(lon - 9.0) < 1e-9
    lat, lon = forest_import.utm_to_latlon(500000, 5538630.7, zone=33)
    assert abs(lon - 15.0) < 1e-9


def test_shapefile_points(app, db, seed, as_admin):
    seed["admin"].forest = True
    db.session.commit()
    data = _shapefile_zip([("A 7", "N: 50.100000 O: 9.200000", (0, 0)),
                           ("B 12", "", (500000.0, 5538630.7)),
                           ("C 1", "N: 50.2 O: 9.3", (0, 0))])
    found = forest_import.read_upload(data)
    assert len(found["tables"]["positions"]) == 79 and len(found["points"]) == 3 and found["shapes"] is None
    r = as_admin.post("/wald/baeume/altsystem", data={"file": (io.BytesIO(data), "export.zip"), "action": "apply"},
                      content_type="multipart/form-data", follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "Lage gesetzt: 2 Bäume" in page and "ohne passenden Baum: 1" in page
    a7 = ForestTree.query.filter_by(field="A", number=7).one()
    b12 = ForestTree.query.filter_by(field="B", number=12).one()
    assert (a7.lat, a7.lon) == (50.1, 9.2) and abs(b12.lat - 50.0) < 1e-4 and abs(b12.lon - 9.0) < 1e-6
    assert len(forest.map_data()) == 2
    assert as_admin.get("/wald/karte").status_code == 200


def _dxf(*entities):
    """Minimale DXF-Zeichnung (erfundene Lagen in UTM 32 um 9° O / 50° N)."""
    out = ["0", "SECTION", "2", "HEADER", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    for kind, layer, pts, closed in entities:
        if kind == "LWPOLYLINE":
            out += ["0", "LWPOLYLINE", "8", layer, "90", str(len(pts)), "70", "1" if closed else "0"]
            for x, y in pts:
                out += ["10", str(x), "20", str(y)]
        elif kind == "POLYLINE":
            out += ["0", "POLYLINE", "8", layer, "66", "1", "10", "0.0", "20", "0.0", "70", "1" if closed else "0"]
            for x, y in pts:
                out += ["0", "VERTEX", "8", layer, "10", str(x), "20", str(y)]
            out += ["0", "SEQEND"]
        else:
            (x1, y1), (x2, y2) = pts
            out += ["0", "LINE", "8", layer, "10", str(x1), "20", str(y1), "11", str(x2), "21", str(y2)]
    out += ["0", "ENDSEC", "0", "EOF"]
    return ("\n".join(out) + "\n").encode("utf-8")


E, N = 500000.0, 5538630.7
PARK = ("LWPOLYLINE", "Parkplatz", [(E, N), (E + 20, N), (E + 20, N + 10), (E, N + 10)], True)
WAY = ("POLYLINE", "wassergebundene Wege", [(E, N), (E + 50, N + 30), (E + 90, N + 35)], False)
FENCE = ("LINE", "Zaun", [(E - 10, N - 10), (E + 100, N - 10)], False)
LAWN = ("LWPOLYLINE", "Rasenfläche", [(E + 30, N + 40), (E + 80, N + 40), (E + 80, N + 90)], True)


def test_dxf(app, db, seed):
    from app.models import ForestShape
    shapes = forest_import.parse_dxf(_dxf(PARK, WAY, FENCE, LAWN), zone=32)
    assert [(s["kind"], s["layer"], s["line"], len(s["points"])) for s in shapes] == [
        ("parkplatz", "Parkplatz", False, 4), ("weg", "wassergebundene Wege", True, 3), ("sonstiges", "Zaun", True, 2),
        ("rasen", "Rasenfläche", False, 3)]
    lon, lat = shapes[0]["points"][0]
    assert abs(lon - 9.0) < 1e-6 and abs(lat - 50.0) < 1e-4
    assert forest_import.shape_kind("Kies") == "kies" and forest_import.shape_kind("Feuchtbiotop") == "wasser"
    assert forest_import.shape_kind("Aussichtsplatform") == "aussicht" and forest_import.shape_kind("Andachtsplatz") == "andacht"
    assert forest_import.is_dxf(_dxf(PARK)) and not forest_import.is_dxf(b"PK\x03\x04")
    assert forest_import.apply_shapes(shapes, "plan.dxf") == {"shapes": 4, "layers": 4}
    db.session.commit()
    forest_import.apply_shapes(forest_import.parse_dxf(_dxf(PARK), zone=32))      # gleiche Ebene → ersetzt
    db.session.commit()
    assert ForestShape.query.count() == 4 and ForestShape.query.filter_by(layer="Parkplatz").count() == 1
    with app.test_request_context():
        items, legend = forest.map_background()
    assert [i["color"] for i in items][0] == "#a8d08d"                            # Rasen zuerst (unten)
    assert ("Parkplatz", "#9aa0a6") in legend and any(i["line"] for i in items)


def test_dxf_upload(app, db, seed, as_admin):
    import zipfile
    from app.models import ForestShape
    seed["admin"].forest = True
    db.session.commit()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:                    # Dateien ohne Endung wie aus dem CAD-Programm
        z.writestr("wege", _dxf(WAY, PARK))
        z.writestr("flaechen", _dxf(LAWN))
    r = as_admin.post("/wald/baeume/altsystem", data={"file": (io.BytesIO(buf.getvalue()), "karte.zip"), "action": "check"},
                      content_type="multipart/form-data", follow_redirects=True)
    assert "Grundkarte: 3 Formen in 3 Ebenen" in r.get_data(as_text=True) and ForestShape.query.count() == 0
    as_admin.post("/wald/baeume/altsystem", data={"file": (io.BytesIO(buf.getvalue()), "karte.zip"), "action": "apply"},
                  content_type="multipart/form-data")
    assert ForestShape.query.count() == 3
    page = as_admin.get("/wald/karte").get_data(as_text=True)                   # Karte auch ohne Bäume mit Lage
    assert "background" in page and "Parkplatz" in page and "forestMap" in page
    assert "Grundkarte: 3 Formen" in as_admin.get("/wald/baeume").get_data(as_text=True)
    as_admin.post("/wald/baeume/altsystem", data={"file": (io.BytesIO(_dxf(PARK)), "park.dxf"), "action": "apply"},
                  content_type="multipart/form-data")
    assert ForestShape.query.count() == 3
    as_admin.post("/wald/karte/grundkarte/loeschen")
    assert ForestShape.query.count() == 0
