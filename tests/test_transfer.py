import io
from datetime import date

from openpyxl import Workbook, load_workbook

from app import storage, transfer
from app.models import Box, Movement, Plot, Variety


def xlsx(rows, headers=("Kiste", "Sorte", "Schlag", "Erntedatum", "Füllstand", "Reihe", "Platz", "Auslagern", "Notiz")):
    wb = Workbook()
    ws = wb.active
    ws.title = "Kisten"
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(client, data, name="zettel.xlsx", create_missing=False):
    form = {"file": (io.BytesIO(data), name)}
    if create_missing:
        form["create_missing"] = "1"
    return client.post("/admin/daten/import", data=form, content_type="multipart/form-data")


def make_boxes(db, seed, n):
    boxes = storage.create_boxes(n, seed["bt"].id, seed["admin"])
    db.session.commit()
    return boxes


# --- Vorlage ----------------------------------------------------------------

def test_template_has_lists_and_reads_back_empty(as_admin, seed):
    resp = as_admin.get("/admin/daten/vorlage.xlsx")
    assert resp.status_code == 200 and resp.data[:2] == b"PK"
    wb = load_workbook(io.BytesIO(resp.data))
    assert wb.sheetnames == ["Anleitung", "Kisten", "Listen"]
    ws = wb["Kisten"]
    assert [c.value for c in ws[1]][:5] == ["Kiste", "Sorte", "Verwendung", "Stufe", "Schlag"]
    assert ws.data_validations.dataValidation  # Auswahllisten vorhanden
    assert wb["Listen"]["A2"].value == "Belana" and wb["Listen"]["B2"].value == "Acker Nord"
    # Leere Vorlage → keine Zeilen
    assert transfer.read_file("v.xlsx", resp.data) == []


def test_template_in_polish_can_be_read(as_admin, db, seed):
    make_boxes(db, seed, 1)
    as_admin.post("/sprache", data={"lang": "pl"})
    data = as_admin.get("/admin/daten/vorlage.xlsx").data
    wb = load_workbook(io.BytesIO(data))
    assert "Skrzynie" in wb.sheetnames
    ws = wb["Skrzynie"]
    headers = [c.value for c in ws[1]]
    ws.cell(row=2, column=1, value="1")
    ws.cell(row=2, column=headers.index("Napełnienie") + 1, value="pełna")
    buf = io.BytesIO()
    wb.save(buf)
    rows = transfer.read_file("pl.xlsx", buf.getvalue())
    assert rows == [(2, {"code": "1", "variety": None, "usage": None, "seed_class": None, "plot": None,
                         "harvest_date": None, "fill": "pełna", "grade": None, "warehouse": None, "row": None,
                         "slot": None, "remove": None, "note": None})]


# --- Import -----------------------------------------------------------------

def test_import_preview_then_apply(as_admin, db, seed):
    make_boxes(db, seed, 3)
    data = xlsx([
        ("K-0001", "Belana", "Acker Nord", date(2026, 10, 5), "voll", "C", 7, None, "erste"),
        ("2", "belana", None, "06.10.2026", "¾", "C", 7, None, None),
        (3, None, None, None, None, None, None, None, None),
    ])
    resp = upload(as_admin, data)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and "Import übernehmen" in html
    assert "eingelagert auf C-07-1" in html and "eingelagert auf C-07-2" in html and "keine Änderung" in html
    # Vorschau speichert nichts
    assert Box.query.filter(Box.warehouse_id.isnot(None)).count() == 0

    resp = as_admin.post("/admin/daten/import/uebernehmen", follow_redirects=True)
    assert "3 Zeilen importiert." in resp.get_data(as_text=True)
    b1, b2, b3 = Box.query.order_by(Box.code).all()
    assert (b1.location, b1.variety.name, b1.plot.name, b1.fill, b1.note) == ("C-07-1", "Belana", "Acker Nord", 100, "erste")
    assert (b2.location, b2.fill, b2.harvest_date) == ("C-07-2", 75, date(2026, 10, 6))
    assert not b3.is_stored
    # Datei ist nach dem Übernehmen weg → zweites Übernehmen geht nicht
    resp = as_admin.post("/admin/daten/import/uebernehmen", follow_redirects=True)
    assert "abgelaufen" in resp.get_data(as_text=True)


def test_import_with_errors_saves_nothing(as_admin, db, seed):
    make_boxes(db, seed, 2)
    data = xlsx([
        ("1", "Belana", None, None, None, "A", 1, None, None),
        ("2", "Gibtsnicht", None, None, None, "A", 2, None, None),
        ("2", None, None, None, "sehr voll", "Z", 1, None, None),
        ("99", None, None, None, None, None, None, None, None),
    ])
    html = upload(as_admin, data).get_data(as_text=True)
    assert "Import übernehmen" not in html
    assert "Sorte „Gibtsnicht“ gibt es nicht." in html
    assert "K-0099 gibt es nicht." in html
    assert Box.query.filter(Box.warehouse_id.isnot(None)).count() == 0
    # Auch ein direktes Übernehmen speichert nichts
    as_admin.post("/admin/daten/import/uebernehmen")
    assert Box.query.filter(Box.warehouse_id.isnot(None)).count() == 0


def test_import_creates_missing_boxes_and_removes(as_admin, db, seed):
    (box,) = make_boxes(db, seed, 1)
    storage.update_contents(box, seed["admin"], seed["belana"].id, None, None, None, 100, None)
    storage.store_box(box, seed["wh"].id, 1, 1, seed["admin"])
    db.session.commit()
    data = xlsx([
        ("K-0001", None, None, None, "leer", None, None, "x", None),
        ("K-0500", "Belana", None, None, None, "B", 2, None, None),
    ])
    upload(as_admin, data, create_missing=True)
    as_admin.post("/admin/daten/import/uebernehmen")
    assert not box.is_stored and box.fill == 0
    new = Box.query.filter_by(code="K-0500").one()
    assert new.location == "B-02-1" and new.fill == 100   # leere Kiste mit Sorte → voll


def test_import_csv_with_semicolons(as_admin, db, seed):
    make_boxes(db, seed, 1)
    csv_data = "Kistennummer;Sorte;Füllstand;Reihe;Platz\nK-0001;Belana;50%;D;3\n".encode("cp1252")
    upload(as_admin, csv_data, name="zettel.csv")
    as_admin.post("/admin/daten/import/uebernehmen")
    box = Box.query.one()
    assert box.location == "D-03-1" and box.fill == 50


def test_import_rejects_wrong_files(as_admin, seed):
    resp = upload(as_admin, b"hallo", name="bild.png")
    assert resp.status_code == 302
    assert "Bitte eine Excel-Datei" in as_admin.get("/admin/daten").get_data(as_text=True)
    resp = upload(as_admin, xlsx([("1", "x")], headers=("Foo", "Bar")))
    assert resp.status_code == 302
    assert "Keine Kopfzeile" in as_admin.get("/admin/daten").get_data(as_text=True)


def test_parse_fill_variants():
    assert transfer._parse_fill(0.75) == 75   # Excel-Prozentzelle
    assert transfer._parse_fill(1) == 100
    assert transfer._parse_fill("1/2") == 50
    assert transfer._parse_fill("25 %") == 25
    assert transfer._parse_fill("pusta") == 0


def test_import_only_for_admins(as_worker, seed):
    assert as_worker.get("/admin/daten").status_code == 403
    assert as_worker.get("/admin/daten/export.xlsx").status_code == 403


# --- Export / Kartoffeljahr ---------------------------------------------------

def test_snapshot_keeps_old_season_after_refill(as_admin, db, seed):
    (box,) = make_boxes(db, seed, 1)
    linda = Variety(name="Linda", color="#c8553d")
    hof = Plot(name="Hofwiese")
    db.session.add_all([linda, hof])
    db.session.commit()

    # Saison 2026: Belana vom Acker Nord, voll eingelagert, später halb
    storage.update_contents(box, seed["admin"], seed["belana"].id, date(2026, 10, 1), seed["plot"].id, None, 100, None)
    storage.store_box(box, seed["wh"].id, 1, 1, seed["admin"])
    storage.set_fill(box, 50, seed["admin"])
    storage.remove_box(box, seed["admin"], empty=True)
    # Saison 2027: dieselbe Kiste neu befüllt mit Linda
    storage.update_contents(box, seed["admin"], linda.id, date(2027, 9, 20), hof.id, None, 75, None)
    db.session.commit()

    assert transfer.season_years() == [2027, 2026]
    groups = transfer.season_summary(2026)
    g = groups[(2026, seed["belana"], "speise", None)]
    assert (g["boxes"], g["harvest_kg"], g["stock_kg"]) == (1, 400, 0)
    assert groups[(2026, seed["belana"], "speise", seed["plot"])]["harvest_kg"] == 400
    g = transfer.season_summary(2027)[(2027, linda, "speise", None)]
    assert (g["boxes"], g["harvest_kg"], g["stock_kg"]) == (1, 300, 300)

    resp = as_admin.get("/admin/daten/export.xlsx?jahr=2026")
    assert resp.status_code == 200
    assert "lager_2026_" in resp.headers["Content-Disposition"]
    wb = load_workbook(io.BytesIO(resp.data))
    assert wb.sheetnames == ["Auswertung", "Kisten", "Bewegungen"]
    values = [tuple(row[:8]) for row in wb["Auswertung"].iter_rows(values_only=True)]
    assert (2026, "Belana", "Speise", None, 1, 400, 0, 0) in values
    assert (2026, "Belana", "Speise", "Acker Nord", 1, 400, 0, 0) in values
    moves = list(wb["Bewegungen"].iter_rows(values_only=True))
    assert len(moves) == 1 + 4   # Kopfzeile + 4 Bewegungen der Saison 2026
    assert all(m[7] == "Belana" and m[8] == "Speise" for m in moves[1:])
    # Kistenblatt 2026 ist leer (Kiste gehört jetzt zu 2027)
    assert wb["Kisten"].max_row == 1

    wb = load_workbook(io.BytesIO(as_admin.get("/admin/daten/export.xlsx").data))
    assert wb["Kisten"].max_row == 2 and wb["Bewegungen"].max_row == 1 + Movement.query.count()


def test_used_variety_and_plot_only_deactivated(as_admin, db, seed):
    (box,) = make_boxes(db, seed, 1)
    storage.update_contents(box, seed["admin"], seed["belana"].id, None, seed["plot"].id, None, 100, None)
    storage.update_contents(box, seed["admin"], None, None, None, None, 0, None)
    db.session.commit()
    # Kiste nutzt Sorte/Schlag nicht mehr, der Verlauf aber schon
    as_admin.post(f"/admin/sorten/{seed['belana'].id}/loeschen")
    as_admin.post(f"/admin/schlaege/{seed['plot'].id}/loeschen")
    assert seed["belana"].active is False and seed["plot"].active is False


def test_data_page_renders(as_admin, db, seed):
    (box,) = make_boxes(db, seed, 1)
    storage.update_contents(box, seed["admin"], seed["belana"].id, date(2026, 10, 1), None, None, 100, None)
    db.session.commit()
    html = as_admin.get("/admin/daten").get_data(as_text=True)
    assert '<option value="2026" selected>2026</option>' in html


# --- Verwendung, Stufe, Reihen mit Zahlen ---------------------------------------

def test_import_with_usage_seed_class_and_short_date(as_admin, db, seed):
    from app.models import SeedClass
    seed["wh"].numeric_rows = True
    db.session.commit()
    data = xlsx([
        ("K-0001", "Rumba", "Saat", "BE", None, "23.9", "voll", "9", 1, None, None),
        ("K-0002", "Rumba", "Saat", "BE", None, "23.9", "3/4", "9", 1, None, None),
        ("K-0003", "Laura", "SP", None, "Acker Nord", "19.10", "voll", "1", 1, None, None),
    ], headers=("Kiste", "Sorte", "Verwendung", "Stufe", "Schlag", "Erntedatum", "Füllstand", "Reihe", "Platz",
                "Auslagern", "Notiz"))
    html = upload(as_admin, data, create_missing=True).get_data(as_text=True)
    assert "Sorte „Rumba“ neu angelegt" in html and "Stufe „BE“ neu angelegt" in html
    as_admin.post("/admin/daten/import/uebernehmen")
    b1, b2, b3 = Box.query.order_by(Box.code).all()
    assert (b1.location, b2.location, b3.location) == ("9-01-1", "9-01-2", "1-01-1")
    assert (b1.variety.name, b1.usage, b1.seed_class.name, b1.harvest_date.day, b1.harvest_date.month) == \
        ("Rumba", "pflanzgut", "BE", 23, 9)
    assert (b2.fill, b3.usage, b3.usage_label, b3.seed_class) == (75, "speise", "Speise", None)
    m = Movement.query.filter_by(box_id=b1.id, action="befuellt").one()
    assert (m.usage, m.seed_class.name) == ("pflanzgut", "BE")
    assert SeedClass.query.filter_by(name="BE").one()


def test_box_form_usage_and_seed_class(as_worker, db, seed):
    from app.models import SeedClass
    z = SeedClass(name="Z", position=1)
    db.session.add(z)
    make_boxes(db, seed, 1)
    html = as_worker.get("/kiste/K-0001").get_data(as_text=True)
    assert 'name="seed_class_id"' in html and "Saat" in html
    as_worker.post("/kiste/K-0001/inhalt", data={"variety_id": seed["belana"].id, "fill": 100, "usage": "pflanzgut",
                                                 "seed_class_id": z.id})
    box = Box.query.one()
    assert (box.usage, box.seed_class_id) == ("pflanzgut", z.id)
    assert "Saat Z" in as_worker.get("/kiste/K-0001").get_data(as_text=True)


def test_seed_class_admin_and_numeric_rows(as_admin, db, seed):
    from app.models import SeedClass
    as_admin.post("/admin/stufen", data={"name": "PB", "description": "Vorstufe", "position": 1})
    pb = SeedClass.query.filter_by(name="PB").one()
    (box,) = make_boxes(db, seed, 1)
    storage.update_contents(box, seed["admin"], seed["belana"].id, None, None, None, 100, None, seed_class_id=pb.id)
    db.session.commit()
    as_admin.post(f"/admin/stufen/{pb.id}/loeschen")
    assert pb.active is False
    as_admin.post("/admin/lager", data={"id": seed["wh"].id, "name": "Halle 1", "rows": 9, "slots": 17,
                                        "max_levels": 5, "numeric_rows": "1"})
    assert seed["wh"].numeric_rows and seed["wh"].row_label(3) == "3"


def test_season_date_guess():
    assert transfer._season_date(23, 9, today=date(2026, 10, 22)) == date(2026, 9, 23)
    assert transfer._season_date(23, 9, today=date(2027, 3, 1)) == date(2026, 9, 23)


def test_import_blocked_by_old_box_and_clear_first(as_admin, db, seed):
    """Nachgestellt: eine alte Testkiste steht auf A-04 und blockiert den Stapel."""
    old = storage.create_boxes(30, seed["bt"].id, seed["admin"])[-1]   # K-0030
    storage.update_contents(old, seed["admin"], seed["belana"].id, None, None, None, 100, None)
    storage.store_box(old, seed["wh"].id, 1, 4, seed["admin"])
    db.session.commit()
    rows = [(f"K-{n:04d}", "Belana", None, None, "voll", "A", (n - 1) // 5 + 1, None, None) for n in range(1, 21)]

    html = upload(as_admin, xlsx(rows)).get_data(as_text=True)
    assert "Stapel A-04 ist voll – dort stehen schon: K-0030" in html and "Import übernehmen" not in html

    html = as_admin.post("/admin/daten/import", data={"file": (io.BytesIO(xlsx(rows)), "z.xlsx"), "clear_first": "1"},
                         content_type="multipart/form-data").get_data(as_text=True)
    assert "1 Kiste vorher ausgelagert" in html and "Import übernehmen" in html
    assert old.is_stored   # Vorschau ändert nichts
    as_admin.post("/admin/daten/import/uebernehmen")
    assert not old.is_stored and old.variety_id == seed["belana"].id   # Inhalt bleibt
    assert Box.query.filter_by(code="K-0020").one().location == "A-04-5"
    assert Movement.query.filter_by(box_id=old.id, action="ausgelagert").count() == 1
