"""RainDancer-Wasserbuch: Testdateien werden hier im Aufbau des Excel-Exports erzeugt – alle Namen erfunden."""
import io
from datetime import date, datetime
from decimal import Decimal

import pytest
import xlwt

from app import irrigation
from app.models import Irrigation, Plot, Setting, Well
from app.storage import StorageError

DT = xlwt.easyxf(num_format_str="DD.MM.YYYY HH:MM")
D = xlwt.easyxf(num_format_str="DD.MM.YYYY")

# (Brunnen, [(Tag, [(Schlag, Regner, Start, Ende, Stunden, m³)])])
DATA = [
    ("Brunnen Wiese  (7)", [
        (date(2026, 6, 10), [("Acker Nord ", "Regner A 400m", datetime(2026, 6, 10, 20, 0), datetime(2026, 6, 11, 8, 0),
                              12.0, 600.0)]),
        (date(2026, 6, 12), [("Acker Nord ", "Regner A 400m", datetime(2026, 6, 12, 6, 0), datetime(2026, 6, 12, 12, 0),
                              6.0, 300.0),
                             ("Hinterm Hof", "Regner B 300m", datetime(2026, 6, 12, 14, 0), datetime(2026, 6, 12, 22, 0),
                              8.0, 450.5)]),
    ]),
    ("Zapfstelle Ost  (2b)", [
        (date(2026, 7, 1), [("Senke Süd", "Regner A 400m", datetime(2026, 7, 1, 19, 0), datetime(2026, 7, 2, 7, 0),
                             12.0, 649.5)]),
    ]),
]


def waterbook(data=DATA, total=None, period="01.04.2026 - 31.10.2026"):
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Waterbook")
    ws.write(0, 0, "Wasserbuch")
    ws.write(1, 0, "Mandant: Erfundener Betrieb")
    ws.write(2, 0, f"Zeitraum: {period}")
    for c, label in ((0, "Datum"), (1, "Schlag"), (2, "Regnername"), (3, "Startzeit"), (5, "Endzeit"), (7, "Stunden"),
                     (8, "Kubikmeter")):
        ws.write(3, c, label)
    r, grand = 4, 0.0
    for well, days in data:
        ws.write(r, 0, well)
        well_m3 = sum(e[5] for _d, rows in days for e in rows)
        ws.write(r, 8, well_m3)
        r += 1
        for day, rows in days:
            ws.write(r, 0, day, D)
            ws.write(r, 8, sum(e[5] for e in rows))
            r += 1
            for plot, sprinkler, start, end, hours, m3 in rows:
                ws.write(r, 1, plot)
                ws.write(r, 2, sprinkler)
                ws.write(r, 3, start, DT)
                ws.write(r, 5, end, DT)
                ws.write(r, 7, hours)
                ws.write(r, 8, m3)
                r += 1
                grand += m3
    ws.write(r, 0, "Total")
    ws.write(r, 8, grand if total is None else total)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse(app):
    parsed = irrigation.parse_file(waterbook())
    assert parsed["period"] == (date(2026, 4, 1), date(2026, 10, 31)) and parsed["total_m3"] == 2000.0
    events = parsed["events"]
    assert len(events) == 4 and events[0]["well"] == ("Brunnen Wiese", "7") and events[3]["well"] == ("Zapfstelle Ost", "2b")
    assert events[0]["plot"] == "Acker Nord" and events[0]["start"] == datetime(2026, 6, 10, 20, 0)
    assert events[2]["sprinkler"] == "Regner B 300m" and events[2]["m3"] == 450.5
    # unvollständig (wie der CSV-Export), falsche Datei
    with pytest.raises(StorageError, match="unvollständig"):
        irrigation.parse_file(waterbook(total=99999))
    with pytest.raises(StorageError, match="kein RainDancer-Wasserbuch"):
        irrigation.parse_file(b"textBox28,textBox26\nDatum,(default)")
    with pytest.raises(StorageError, match="keine Beregnungseinsätze"):
        irrigation.parse_file(waterbook(data=[]))


def test_xlsx_works_too(app):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Wasserbuch"])
    ws.append(["Zeitraum: 01.06.2026 - 30.06.2026"])
    ws.append(["Datum", "Schlag", "Regnername", "Startzeit", None, "Endzeit", None, "Stunden", "Kubikmeter"])
    ws.append(["Brunnen Wiese  (7)", None, None, None, None, None, None, 5.0, 250.0])
    ws.append([datetime(2026, 6, 3), None, None, None, None, None, None, 5.0, 250.0])
    ws.append([None, "Acker Nord", "Regner A", datetime(2026, 6, 3, 5), None, datetime(2026, 6, 3, 10), None, 5.0, 250.0])
    ws.append(["Total", None, None, None, None, None, None, 5.0, 250.0])
    buf = io.BytesIO()
    wb.save(buf)
    parsed = irrigation.parse_file(buf.getvalue())
    assert len(parsed["events"]) == 1 and parsed["events"][0]["hours"] == 5.0


def test_apply_replaces_period_and_remembers_mapping(db, seed):
    nord = seed["plot"]                                       # „Acker Nord“
    nord.area_ha = Decimal("2.0")
    other = Plot(name="Feld am Hof", area_ha=Decimal("1.5"))
    db.session.add(other)
    db.session.commit()
    parsed = irrigation.parse_file(waterbook())
    plan = irrigation.plan(parsed)
    rows = {r["name"]: r for r in plan["plots"]}
    assert rows["Acker Nord"]["plot"] == nord and rows["Hinterm Hof"]["plot"] is None
    assert rows["Acker Nord"]["events"] == 2 and all(w["new"] for w in plan["wells"]) and plan["m3"] == 2000.0

    stats = irrigation.apply(parsed, {"Acker Nord": nord.id, "Hinterm Hof": other.id, "Senke Süd": None})
    db.session.commit()
    assert stats == {"events": 4, "removed": 0, "wells_new": 2}
    first = Irrigation.query.filter_by(plot_id=nord.id).order_by(Irrigation.start_at).first()
    assert first.start_at.hour == 18 and first.mm == pytest.approx(30.0)      # 20 Uhr Sommerzeit = 18 Uhr UTC
    # Zuordnung gemerkt: zweiter Import findet „Hinterm Hof“ von selbst und ersetzt statt zu verdoppeln
    assert irrigation.match_plots(["Hinterm Hof"])["Hinterm Hof"] == other
    stats = irrigation.apply(irrigation.parse_file(waterbook()), {})
    db.session.commit()
    assert stats["removed"] == 4 and Irrigation.query.count() == 4 and Well.query.count() == 2
    assert Irrigation.query.filter_by(plot_name="Hinterm Hof").one().plot_id == other.id

    data = irrigation.overview(2026)
    assert data["m3"] == 2000.0 and data["count"] == 4 and data["unassigned"] == ["Senke Süd"]
    by_name = {p["name"]: p for p in data["plots"]}
    assert by_name["Acker Nord"]["mm"] == pytest.approx(45.0) and by_name["Senke Süd"]["mm"] is None
    assert irrigation.mm_by_plot_year([nord.id], [2025, 2026]) == {(nord.id, 2026): pytest.approx(45.0)}
    assert irrigation.plot_summary(nord, 2026)["count"] == 2 and irrigation.plot_summary(nord, 2025) is None

    # Genehmigung: ab 80 % Hinweis
    well = Well.query.filter_by(name="Brunnen Wiese").one()
    irrigation.save_permit(well, 1500, "Az. 123")
    db.session.commit()
    (alert,) = irrigation.alerts(2026)
    assert alert["well"] == well and alert["percent"] == 90 and alert["level"] == "warning"
    with pytest.raises(StorageError):
        irrigation.save_permit(well, -5)


def test_pages(app, as_admin, as_worker, as_viewer, db, seed):
    seed["plot"].area_ha = Decimal("2.0")
    db.session.commit()
    assert "Wasserbuch aus RainDancer einlesen" not in as_worker.get("/beregnung").get_data(as_text=True)
    assert as_worker.post("/admin/beregnung/import").status_code == 403
    r = as_admin.post("/admin/beregnung/import", data={"file": (io.BytesIO(waterbook()), "Waterbook.xls")},
                      content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    assert "Vorschau" in html and "Hinterm Hof" in html and "2000 m³" in html and 'name="plot_0"' in html
    # nichts gespeichert, bevor übernommen wird
    assert Irrigation.query.count() == 0
    r = as_admin.post("/admin/beregnung/uebernehmen", data={"plot_0": seed["plot"].id})
    assert r.headers["Location"].endswith("/beregnung?jahr=2026") and Irrigation.query.count() == 4
    assert db.session.get(Setting, irrigation.ALIAS_KEY) is not None

    html = as_viewer.get("/beregnung?jahr=2026").get_data(as_text=True)
    assert "Brunnen Wiese (7)" in html and "nicht zugeordnet" in html and "45" in html
    well = Well.query.filter_by(name="Brunnen Wiese").one()
    assert as_worker.post(f"/brunnen/{well.id}", data={"permit_m3": "1000"}).status_code == 403
    as_admin.post(f"/brunnen/{well.id}", data={"permit_m3": "1000", "jahr": "2026"})
    assert db.session.get(Well, well.id).permit_m3 == 1000
    html = as_admin.get(f"/schlag/{seed['plot'].id}").get_data(as_text=True)
    if date.today().year == 2026:
        assert 'id="beregnung"' in html and "45 mm" in html
        assert "der genehmigten Jahresmenge" in as_admin.get("/").get_data(as_text=True)    # 135 % → „Jetzt wichtig“
    # kaputte Datei → Hinweis, keine Vorschau
    r = as_admin.post("/admin/beregnung/import", data={"file": (io.BytesIO(b"textBox1,textBox2"), "Waterbook.csv")},
                      content_type="multipart/form-data", follow_redirects=True)
    assert "kein RainDancer-Wasserbuch" in r.get_data(as_text=True)
