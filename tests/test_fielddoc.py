import io
from datetime import date
from decimal import Decimal

import pytest

from app import fielddoc, fields
from app.fields import FieldImportError
from app.models import FieldMeasure, Plot

from .test_fields import kml

# Aufbau wie die Myfarm24-„Schlagdokumentation“ (Daten erfunden), inklusive Seitenumbruch-Zeilen
CSV = """Schlagdokumentation Erntejahr 2026
Betrieb: Testhof (12345 Musterdorf)
Schlag: Hofkoppel (2) / Nr. 1 / FLIK: DENILI0000000001
Fläche:;6,5;ha
USchlag-ID:;MY000000001
Kultur: Kartoffel (Pflanz)
Sorte: Belana
Vorfrucht: Zuckerrüben
Vorvorfrucht: Winterroggen
DüV Bedarf Frühjahr: 127 kg N/ha
Saatdatum: 29.04.2026
Saatmenge:;30;dt/ha
Bestellverfahren: Mulchsaat
Erntedatum:
Ertrag:;
Bodenart: S
Rotes Gebiet: N
pH-Wert:;6,08
P-Klasse: C
K-Klasse: D
Mg:;4.1;mg/100g
Analyse: 02.02.2022 (CAL)
N-min 30:;0;kg
Analyse:
Aussaat:
Datum;Sorte;Fläche ha;Menge je ha;Einheit;Bemerkungen
28.04.26;Belana (Basis);6,5;30;dt;(Beizinfo: Testbeize)
Düngung:
Datum;BBCH;Dünger;Fläche ha;Menge je ha;Einheit;N gesamt;;N anrechenbar;P2O5 ;K2O ;MgO ;S ;CaO ;Bemerkungen
24.04.26;;Gärrest Frühjahr;6,5;15;m³;80;45;30;81;12;8;33;
27.04.26;;Mischdünger NPK;2,8;3;dt;17;17;5;20;8;12;7;
24.06.26;;Kalkammonsalpeter 27;2;1,5;dt;12;;12;;;;;-7;nur Teilfläche
Summe Nährstoffe (kg/ha);;;;;;109;74;35;101;20;20;33
Bei Düngerbuchungen auf Teilflächen wird der jeweilige Anteil am Nährstoffsaldo entsprechend verringert.
Pflanzenschutz:
Datum;BBCH Stadium;Produkt;Fläche [ha];Menge je ha;Mengeneinheit;Bemerkung / Indikation
16.05.26;09;Mittel A;6,5;2,5;l;Unkräuter, Wartezeit F
Schlagdokumentation Erntejahr 2026
Betrieb: Testhof (12345 Musterdorf)
Arbeiten / Maschinen:
Datum;BBCH Stadium;Arbeit / Maschine;Fläche [ha];Bemerkungen
22.04.26;;Traktor 1;6,5;
22.04.26;;Grubber;6,5;
Personen:
Datum;Person;Dauer [h];Bemerkungen
22.04.26;Muster, Max;2,5;
Inhalt der Checkliste "Gute fachliche Praxis" ist bekannt.
Geografische Lage des Schlages (WGS 84): N 52,4, E 10,5
Schlag: Bachfeld / Nr. 22
Fläche:;16,6;ha
USchlag-ID:;MY000000002
Kultur: Winterweizen
Sorte: Testweizen
Bodenart: Keine Angabe
Rotes Gebiet: J
Zwischenfrucht: ZF Ölrettich (nicht eingearbeitet)
Schlag: Ohne Kultur / Nr. 30 / FLIK:
Fläche:;;ha
Kultur:
"""


def data(text=CSV):
    return text.encode("cp1252")


def test_parse():
    year, entries = fielddoc.parse_csv(data())
    assert year == 2026 and [e["name"] for e in entries] == ["Hofkoppel (2)", "Bachfeld", "Ohne Kultur"]
    e = entries[0]
    assert (e["number"], e["flik"], e["myfarm_id"], e["area_ha"]) == ("1", "DENILI0000000001", "MY000000001",
                                                                      Decimal("6.5"))
    assert (e["crop"], e["variety"], e["prev_crop"], e["prev_prev_crop"]) == \
        ("Kartoffel (Pflanz)", "Belana", "Zuckerrüben", "Winterroggen")
    assert e["n_demand"] == 127 and e["planted_on"] == date(2026, 4, 29) and e["harvested_on"] is None
    assert (e["soil"], e["red_area"], e["ph"], e["p_class"], e["k_class"], e["mg_value"], e["soil_test_on"]) == \
        ("S", False, Decimal("6.08"), "C", "D", Decimal("4.1"), date(2022, 2, 2))
    kinds = [m["kind"] for m in e["measures"]]
    assert kinds == ["aussaat", "duengung", "duengung", "duengung", "pflanzenschutz", "arbeit", "arbeit", "person"]
    gaerrest = e["measures"][1]
    assert (gaerrest["name"], gaerrest["amount"], gaerrest["unit"], gaerrest["n_total"], gaerrest["n_avail"],
            gaerrest["cao"], gaerrest["note"]) == ("Gärrest Frühjahr", Decimal("15"), "m³", 80, 45, 33, None)
    # Zeile mit der zusätzlichen leeren Spalte nach „N gesamt“
    kas = e["measures"][3]
    assert (kas["n_total"], kas["n_avail"], kas["p2o5"], kas["cao"], kas["note"]) == (12, 12, None, -7, "nur Teilfläche")
    psm = e["measures"][4]
    assert (psm["bbch"], psm["name"], psm["amount"], psm["unit"], psm["note"]) == \
        ("09", "Mittel A", Decimal("2.5"), "l", "Unkräuter, Wartezeit F")
    person = e["measures"][-1]
    assert (person["name"], person["amount"], person["unit"]) == ("Muster, Max", Decimal("2.5"), "h")
    b = entries[1]
    assert b["flik"] is None and b["soil"] is None and b["red_area"] is True and b["catch_crop"].startswith("ZF Öl")
    assert entries[2]["area_ha"] is None and entries[2]["crop"] is None and entries[2]["flik"] is None
    # UTF-8 geht auch
    assert fielddoc.parse_csv(CSV.encode("utf-8"))[1][0]["measures"][1]["unit"] == "m³"


def test_parse_invalid():
    for bad in (b"", b"Datum;Konto;Betrag\n01.01.26;4000;12", b"Schlagdokumentation Erntejahr 2026\nnix"):
        with pytest.raises(FieldImportError):
            fielddoc.parse_csv(bad)


def test_import_matches_kml_plots_and_reimport(db, seed):
    # Umrisse aus der KML zuerst – die Schlagdokumentation findet sie über Nummer + Name wieder
    fields.apply_import(fields.parse_kml("x.kml", kml(("Hofkoppel (2)", "1", "6.4", "Kartoffel", "SOLTU", "", "N"))),
                        2026)
    db.session.commit()
    koppel = Plot.query.filter_by(name="Hofkoppel (2)").one()
    year, entries = fielddoc.parse_csv(data())
    rows, missing = fielddoc.plan_import(entries, year)
    assert rows[0]["plot"] is koppel and rows[1]["plot"] is None
    assert rows[0]["counts"] == {"aussaat": 1, "duengung": 3, "pflanzenschutz": 1, "arbeit": 2, "person": 1}
    assert [p.name for p in missing] == ["Acker Nord"]

    stats = fielddoc.apply_import(entries, year, create=False)
    db.session.commit()
    assert stats == {"created": 0, "updated": 1, "skipped": 2, "measures": 8} and Plot.query.count() == 2
    assert koppel.myfarm_id == "MY000000001" and koppel.area_ha == Decimal("6.5") and koppel.rings
    assert koppel.ph == Decimal("6.08") and koppel.p_class == "C"
    y = koppel.year(2026)
    assert y.crop == "Kartoffel (Pflanz)" and y.crop_eppo is None and y.is_potato
    assert y.variety_id == seed["belana"].id and y.variety_name == "Belana" and y.n_demand == 127
    assert y.planted_on == date(2026, 4, 29)
    assert koppel.year(2025).crop == "Zuckerrüben" and koppel.year(2024).crop == "Winterroggen"
    summary = fielddoc.summary(y)
    assert summary["nutrients"]["n_total"] == 109 and summary["nutrients"]["n_avail"] == 74 and summary["hours"] == 2.5

    # Pflanzdatum aus der App bleibt, Maßnahmen werden ersetzt statt verdoppelt, eigene Einträge bleiben
    y.planted_on = date(2026, 4, 30)
    y.measures.append(FieldMeasure(kind="arbeit", done_on=date(2026, 5, 1), name="Von Hand", source="app"))
    db.session.commit()
    changed = CSV.replace("22.04.26;;Grubber;6,5;\n", "").replace("USchlag-ID:;MY000000001", "USchlag-ID:;MY000000001")
    changed = changed.replace("Schlag: Hofkoppel (2) / Nr. 1", "Schlag: Hofkoppel / Nr. 1")   # in Myfarm umbenannt
    stats = fielddoc.apply_import(fielddoc.parse_csv(data(changed))[1], 2026)
    db.session.commit()
    assert stats["created"] == 2 and stats["measures"] == 7 and Plot.query.count() == 4
    assert koppel.name == "Hofkoppel"            # über die USchlag-ID wiedererkannt
    assert y.planted_on == date(2026, 4, 30)
    assert FieldMeasure.query.filter_by(plot_year_id=y.id, source="myfarm").count() == 7
    assert FieldMeasure.query.filter_by(plot_year_id=y.id, source="app").count() == 1
    bach = Plot.query.filter_by(name="Bachfeld").one()
    assert bach.boundary is None and bach.red_area is True and bach.year(2026).catch_crop.startswith("ZF")
    assert bach.year(2026).variety_id is None and bach.year(2026).variety_name == "Testweizen"
    # Unverändert erneut → Vorschau zeigt keine Änderungen
    rows, _ = fielddoc.plan_import(fielddoc.parse_csv(data(changed))[1], 2026)
    assert not any(r["changes"] for r in rows)


def test_pages(as_admin, as_worker, as_viewer, db, seed):
    r = as_admin.post("/admin/schlaege/myfarm", data={"file": (io.BytesIO(data()), "schlagkartei.csv")},
                      content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Hofkoppel (2)" in html and "Erntejahr 2026" in html
    assert Plot.query.count() == 1                       # nur Vorschau
    assert as_worker.post("/admin/schlaege/myfarm/uebernehmen", data={"create": "1"}).status_code == 403
    r = as_admin.post("/admin/schlaege/myfarm/uebernehmen", data={"create": "1"})
    assert r.status_code == 302 and Plot.query.count() == 4
    koppel = Plot.query.filter_by(name="Hofkoppel (2)").one()
    html = as_viewer.get(f"/schlag/{koppel.id}").get_data(as_text=True)
    for text in ("Gärrest Frühjahr", "Mittel A", "Traktor 1", "Muster, Max", "N anrechenbar 74 kg/ha",
                 "von 127 kg/ha Bedarf", "DENILI0000000001", "6,08", "Belana"):
        assert text in html, text
    r = as_admin.post("/admin/schlaege/myfarm", data={"file": (io.BytesIO(b"kaputt"), "x.csv")},
                      content_type="multipart/form-data")
    assert r.status_code == 302
    assert "Schlagdokumentation importieren" in as_admin.get("/admin/schlaege").get_data(as_text=True)


def test_fmt_qty():
    from app.utils import fmt_qty
    assert [fmt_qty(v) for v in (Decimal("4.500"), Decimal("15"), 80, None, Decimal("0.25"))] == \
        ["4,5", "15", "80", "", "0,25"]
    assert fmt_qty(Decimal("130.0"), 0) == "130" and fmt_qty(Decimal("-0.0"), 1) == "0" and fmt_qty(2.5, 1) == "2,5"


def test_point_on_map(db, seed):
    year, entries = fielddoc.parse_csv(data())
    assert (entries[0]["lat"], entries[0]["lon"]) == (Decimal("52.4"), Decimal("10.5"))
    assert entries[1]["lat"] is None
    fielddoc.apply_import(entries, year)
    db.session.commit()
    koppel = Plot.query.filter_by(name="Hofkoppel (2)").one()
    (item,) = fields.map_data([koppel, Plot.query.filter_by(name="Bachfeld").one()], 2026)   # Bachfeld: keine Lage
    assert item["point"] == [10.5, 52.4] and item["rings"] == []
    # Kommt später der Umriss aus der KML, gilt der Umriss
    fields.apply_import(fields.parse_kml("x.kml", kml(("Hofkoppel (2)", "1", "6.5", "Kartoffel", "SOLTU", "", "N"))),
                        2026)
    (item,) = fields.map_data([koppel], 2026)
    assert item["point"] is None and item["rings"]


PSM = CSV.replace("16.05.26;09;Mittel A;6,5;2,5;l;Unkräuter, Wartezeit F\n",
                  "16.05.26;09;Mittel A;6,5;2,5;l;Unkräuter, Wartezeit F\n"
                  "20.07.26;;Mittel B;6,5;0,5;l;Krautfäule, Wartezeit 7 Tage\n"
                  "01.08.26;;Mittel C;6,5;1;kg;\n")


def test_waiting_periods(db, seed):
    fielddoc.apply_import(fielddoc.parse_csv(data(PSM))[1], 2026)
    db.session.commit()
    koppel = Plot.query.filter_by(name="Hofkoppel (2)").one()
    entry = koppel.year(2026)
    assert fielddoc.note_wait("Blattläuse, Wartezeit 14 Tage") == 14 and fielddoc.note_wait("Wartezeit F") == "F"
    assert fielddoc.note_wait("Benetzung") is None

    info = fielddoc.protection(entry, fielddoc.products(), today=date(2026, 7, 22))
    assert info["count"] == 3 and info["last"].name == "Mittel C"
    assert info["until"] == date(2026, 7, 27) and info["running"] and info["days_left"] == 5
    assert info["unknown"] == ["Mittel C"]

    # Wartezeit für Mittel C pflegen (Groß-/Kleinschreibung egal) → längste Wartezeit zählt
    fielddoc.save_product("mittel c", 21, False)
    db.session.commit()
    info = fielddoc.protection(entry, fielddoc.products(), today=date(2026, 7, 22))
    assert info["until"] == date(2026, 8, 22) and not info["unknown"]
    info = fielddoc.protection(entry, fielddoc.products(), today=date(2026, 8, 22))
    assert not info["running"] and info["days_left"] == 0
    fielddoc.save_product("Mittel C", None, True)            # „F“
    info = fielddoc.protection(entry, fielddoc.products(), today=date(2026, 7, 22))
    assert info["until"] == date(2026, 7, 27) and not info["unknown"]
    fielddoc.save_product("Mittel C", None, False)           # leer → wieder unbekannt
    db.session.commit()
    assert fielddoc.products() == {}
    entry.harvested_on = date(2026, 7, 21)
    assert fielddoc.protection(entry, {}, today=date(2026, 7, 22))["harvested"]
    assert not fielddoc.protection(entry, {}, today=date(2026, 7, 22))["running"]

    rows, used = fielddoc.protection_overview(2026, today=date(2026, 7, 22))
    assert [r["plot"].name for r in rows] == ["Hofkoppel (2)"]
    assert [u["name"] for u in used] == ["Mittel A", "Mittel B", "Mittel C"]
    b = used[1]
    assert b["count"] == 1 and b["area"] == Decimal("6.5") and b["amounts"] == {"l": Decimal("3.25")}
    assert b["note_waits"] == [7]


def test_protection_pages(as_admin, as_worker, as_viewer, db, seed):
    fielddoc.apply_import(fielddoc.parse_csv(data(PSM))[1], 2026)
    db.session.commit()
    html = as_viewer.get("/pflanzenschutz?jahr=2026").get_data(as_text=True)
    assert "Hofkoppel (2)" in html and "Mittel C" in html and "1 Mittel ohne Wartezeit" in html
    assert 'name="wait"' not in html                         # nur der Admin pflegt Wartezeiten
    assert "Eingesetzte Mittel" in as_viewer.get("/pflanzenschutz?jahr=2026&laufend=1").get_data(as_text=True)
    assert as_worker.post("/pflanzenschutz/mittel", data={"name": "Mittel C", "wait": "14"}).status_code == 403
    r = as_admin.post("/pflanzenschutz/mittel", data={"name": "Mittel C", "wait": "14", "year": "2026"})
    assert r.headers["Location"].endswith("/pflanzenschutz?jahr=2026#mittel")
    assert fielddoc.products()["mittel c"].wait_days == 14
    as_admin.post("/pflanzenschutz/mittel", data={"name": "Mittel C", "wait": "f", "year": "2026"})
    assert fielddoc.products()["mittel c"].covered
    html = as_admin.get("/pflanzenschutz?jahr=2026").get_data(as_text=True)
    assert 'value="F"' in html and "Mittel ohne Wartezeit" not in html
    koppel = Plot.query.filter_by(name="Hofkoppel (2)").one()
    assert as_viewer.get(f"/schlag/{koppel.id}").status_code == 200


# Aufbau wie der Myfarm24-„Fruchtfolgeplan“ (Daten erfunden)
PLAN = """Fruchtfolgeplan Erntejahre 2026 - 2028
Betriebsstätte: Musterhof (. .)
Schlag;Nr.;ha;2026;2027;2028
Hofkoppel (2);1;6,5;K;-;-
Bachfeld;22;16,6;WW;M;K
Neuland;300;2,25;;Sonst,;ZR
Rübenacker;301;3;ZR;RW;ZR

Kulturen;2026 ha;2026 %;2027 ha;2027 %;2028 ha;2028 %
K - Kartoffel;6,5;30;;;;
M - Mais;;;16,6;80;;
RW - Winterroggen;;;3;10;;
WW - Winterweichweizen;16,6;70;;;;
ZR - Zuckerrübe;3;10;;;2,25;10
Sonstige:
Ackergras;;;2,25;10;;
Gesamt;26,1;;21,85;;;
"""


def test_rotation_plan(db, seed):
    years, entries = fielddoc.parse_plan(PLAN.encode("cp1252"))
    assert years == [2026, 2027, 2028] and [e["name"] for e in entries] == ["Hofkoppel (2)", "Bachfeld", "Neuland",
                                                                              "Rübenacker"]
    assert entries[0]["plan"] == {2026: "Kartoffel", 2027: "-", 2028: "-"}
    assert entries[2]["plan"] == {2026: None, 2027: "Sonstige", 2028: "Zuckerrübe"}
    assert entries[2]["area_ha"] == Decimal("2.25")
    with pytest.raises(FieldImportError):
        fielddoc.parse_plan(CSV.encode("cp1252"))

    fielddoc.apply_import(fielddoc.parse_csv(data())[1], 2026)          # Schlagdokumentation: Kultur 2026
    db.session.commit()
    rows, missing = fielddoc.plan_rotation(entries, years)
    assert [r["plot"] is None for r in rows] == [False, False, True, True] and rows[0]["changes"] == [2026, 2027, 2028]
    stats = fielddoc.apply_rotation(entries, years, create=False)
    db.session.commit()
    assert stats == {"created": 0, "updated": 2, "skipped": 2}
    stats = fielddoc.apply_rotation(entries, years)
    db.session.commit()
    assert stats == {"created": 2, "updated": 0, "skipped": 0}
    bach = Plot.query.filter_by(name="Bachfeld").one()
    assert bach.year(2026).crop == "Winterweizen" and bach.year(2026).planned_crop == "Winterweichweizen"
    assert bach.year(2027).crop is None and bach.year(2027).shown_crop == "Mais"
    neu = Plot.query.filter_by(name="Neuland").one()
    assert neu.number == "300" and neu.area_ha == Decimal("2.25") and neu.year(2026) is None
    koppel = Plot.query.filter_by(name="Hofkoppel (2)").one()
    assert koppel.year(2027).planned_crop == "-" and koppel.year(2027).shown_crop is None
    # Planung geändert: leere Zelle löscht die Planung, die tatsächliche Kultur bleibt
    fielddoc.apply_rotation(fielddoc.parse_plan(PLAN.replace("Bachfeld;22;16,6;WW;M;K", "Bachfeld;22;16,6;;M;").encode())[1],
                            years)
    assert bach.year(2026).planned_crop is None and bach.year(2026).crop == "Winterweizen"
    assert bach.year(2028).planned_crop is None

    # Fruchtfolge-Tabelle: Zuckerrübe 2026 und 2028 → Anbaupause unterschritten
    plots = Plot.query.order_by(Plot.name).all()
    rows, summary = fielddoc.rotation(plots, [2025, 2026, 2027, 2028])
    by_name = {r["plot"].name: r for r in rows}
    ruebe = {c["year"]: c for c in by_name["Rübenacker"]["cells"]}
    assert ruebe[2028]["warn"] == 2026 and ruebe[2026]["warn"] is None and ruebe[2027]["shown"] == "Winterroggen"
    kop = {c["year"]: c for c in by_name["Hofkoppel (2)"]["cells"]}
    assert kop[2026]["shown"] == "Kartoffel (Pflanz)" and kop[2025]["shown"] == "Zuckerrüben"
    assert kop[2027]["shown"] is None and kop[2027]["planned"] == "-"
    mais = next(c for c in summary if c["crop"] == "Mais")
    assert mais["ha"] == {2027: Decimal("16.6")}
    assert fields.crop_color("Mais", None) == fields.crop_color("Silomais", None)


def test_rotation_pages(as_admin, as_worker, as_viewer, db, seed):
    r = as_admin.post("/admin/schlaege/fruchtfolge", data={"file": (io.BytesIO(PLAN.encode("cp1252")), "plan.csv")},
                      content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Rübenacker" in html and "2026–2028" in html and Plot.query.count() == 1
    assert as_worker.post("/admin/schlaege/fruchtfolge/uebernehmen", data={"create": "1"}).status_code == 403
    r = as_admin.post("/admin/schlaege/fruchtfolge/uebernehmen", data={"create": "1"})
    assert r.headers["Location"].endswith("/fruchtfolge") and Plot.query.count() == 5
    html = as_viewer.get("/fruchtfolge?von=2025").get_data(as_text=True)
    assert "Rübenacker" in html and "Winterweichweizen" in html and "Anbauumfang" in html
    assert "Gleiche Kultur schon 2026" in html
    assert "Hofkoppel" in as_viewer.get("/fruchtfolge?kartoffeln=1").get_data(as_text=True)
    assert "Bachfeld" in as_viewer.get("/schlagkarte?jahr=2028&kartoffeln=1").get_data(as_text=True)   # geplant
    bach = Plot.query.filter_by(name="Bachfeld").one()
    assert "geplant" in as_viewer.get(f"/schlag/{bach.id}").get_data(as_text=True)
