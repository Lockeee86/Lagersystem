from datetime import date

import pytest

from app import storage
from app.models import Box, Variety, Warehouse
from app.storage import StorageError

DAY = date(2026, 9, 10)


def boxes(db, seed, n):
    result = storage.create_boxes(n, seed["bt"].id, seed["admin"])
    db.session.commit()
    return result


def test_suggest_place(db, seed):
    wh = Warehouse(name="Halle Klein", rows=2, slots=3, max_levels=2)
    other = Variety(name="Testsorte Rot", color="#c00000")
    db.session.add_all([wh, other])
    db.session.flush()
    belana, admin = seed["belana"], seed["admin"]
    b = boxes(db, seed, 6)
    # leeres Lager → erster freier Platz
    assert storage.suggest_place(wh, belana.id, "speise") == (1, 1)
    for box, variety, pos in ((b[0], other, (1, 1)), (b[1], belana, (2, 3))):
        storage.update_contents(box, admin, variety.id, DAY, None, None, 100, None)
        storage.store_box(box, wh.id, *pos, admin)
    # Stapel mit derselben Sorte und Platz
    assert storage.suggest_place(wh, belana.id, "speise") == (2, 3)
    # andere Verwendung zählt nicht als gleiche Ware → nächster freier Platz neben … nichts Gleichem: erster freier
    assert storage.suggest_place(wh, belana.id, "pflanzgut") == (1, 2)
    storage.update_contents(b[2], admin, belana.id, DAY, None, None, 100, None)
    storage.store_box(b[2], wh.id, 2, 3, admin)
    # Stapel voll → freier Platz, der am nächsten an Belana liegt (Nachbar in derselben Reihe)
    assert storage.suggest_place(wh, belana.id, "speise") == (2, 2)
    for box, pos in ((b[3], (2, 2)), (b[4], (2, 2))):
        storage.update_contents(box, admin, other.id, DAY, None, None, 100, None)
        storage.store_box(box, wh.id, *pos, admin)
    assert storage.suggest_place(wh, belana.id, "speise") == (1, 3)      # dann gegenüber


def test_harvest_box(db, seed):
    belana, admin, wh, plot = seed["belana"], seed["admin"], seed["wh"], seed["plot"]
    b1, b2, b3 = boxes(db, seed, 3)
    # nur befüllen
    assert storage.harvest_box(b1, admin, belana, plot.id, DAY, fill=75) is None
    assert b1.fill == 75 and b1.variety_id == belana.id and b1.plot_id == plot.id and not b1.is_stored
    with pytest.raises(StorageError, match="schon befüllt"):
        storage.harvest_box(b1, admin, belana, plot.id, DAY)
    # befüllen + einlagern: alle auf denselben Stapel
    assert storage.harvest_box(b2, admin, belana, plot.id, DAY, usage="speise", warehouse=wh) == "A-01-1"
    assert b2.usage is None                                  # wie bei der Sorte → nicht extra gespeichert
    assert storage.harvest_box(b3, admin, belana, plot.id, DAY, warehouse=wh) == "A-01-2"
    assert [m.action for m in sorted(b3.movements, key=lambda m: m.id)] == ["angelegt", "befuellt", "eingelagert"]


def test_pages(as_worker, as_viewer, db, seed):
    belana, wh = seed["belana"], seed["wh"]
    b1, b2 = boxes(db, seed, 2)
    # Betrachter: normale Scan-Seite ohne Ernte-Modus
    assert 'id="harvestSwitch"' not in as_viewer.get("/scan").get_data(as_text=True)
    assert as_viewer.post("/scan/ernte", data={"an": "1"}).status_code == 403
    # Mitarbeiter: Schalter über der Kamera, ohne Sorte sind die Einstellungen offen
    html = as_worker.get("/scan").get_data(as_text=True)
    assert 'id="harvestSwitch"' in html and 'class="collapse show" id="harvestSettings"' in html
    assert 'href="/ernte"' in as_worker.get("/lager/uebersicht").get_data(as_text=True)
    assert as_worker.get("/ernte").headers["Location"].endswith("/scan?einstellungen=1")

    # Einschalten ohne Sorte geht nicht
    r = as_worker.post("/scan/ernte", data={"an": "1"}, headers={"Accept": "application/json"})
    assert r.get_json() == {"ok": False, "on": False, "message": "Bitte zuerst die Sorte wählen."}
    # Einstellungen speichern schaltet ein
    form = {"sorte": belana.id, "schlag": seed["plot"].id, "datum": "2026-09-10", "fuell": "100",
            "modus": "einlagern", "lager": wh.id}
    as_worker.post("/scan/ernte", data=form)
    html = as_worker.get("/scan").get_data(as_text=True)
    assert 'id="harvestSwitch" checked' in html and "Belana · Acker Nord · Kartoffellager" in html
    assert "Nächster Platz: A-01" in html and "harvest-on" in html

    # Scannen: Kiste wird befüllt und eingelagert, nächster Platz kommt mit
    r = as_worker.post("/ernte/kiste", data={"code": b1.code}, headers={"Accept": "application/json"})
    data = r.get_json()
    assert data["ok"] and data["place"] == "A-01-1" and data["next"] == "A-01" and "Belana" in data["text"]
    assert b1.harvest_date == date(2026, 9, 10)
    r = as_worker.post("/ernte/kiste", data={"code": b1.code}, headers={"Accept": "application/json"})
    assert not r.get_json()["ok"] and "schon befüllt" in r.get_json()["message"]
    assert not as_worker.post("/ernte/kiste", data={"code": "K-9999"},
                              headers={"Accept": "application/json"}).get_json()["ok"]

    # Ausschalten: Einstellungen bleiben, Schalter aus
    as_worker.post("/scan/ernte", data={"an": "0"}, headers={"Accept": "application/json"})
    html = as_worker.get("/scan").get_data(as_text=True)
    assert 'id="harvestSwitch" checked' not in html and "Belana · Acker Nord" in html
    # wieder an – ohne neue Einstellungen
    assert as_worker.post("/scan/ernte", data={"an": "1"}, headers={"Accept": "application/json"}).get_json()["on"]

    # nur befüllen
    as_worker.post("/scan/ernte", data={**form, "modus": "befuellen"})
    as_worker.post("/ernte/kiste", data={"code": b2.code})
    assert db.session.get(Box, b2.id).fill == 100 and not b2.is_stored


def test_old_date_becomes_today(as_worker, db, seed):
    belana = seed["belana"]
    as_worker.set_cookie("ernte", f"sorte={belana.id}&datum=2026-09-10&modus=befuellen&an=1&tag=2026-09-10")
    html = as_worker.get("/scan").get_data(as_text=True)
    assert f'value="{date.today().isoformat()}"' in html and 'id="harvestSwitch" checked' in html


def test_fixed_row(as_worker, db, seed):
    belana, wh = seed["belana"], seed["wh"]
    small = Warehouse(name="Halle Klein", rows=3, slots=2, max_levels=1)
    db.session.add(small)
    db.session.commit()
    b = boxes(db, seed, 4)
    # Logik: nur in der gewählten Reihe, voll → klare Meldung
    assert storage.suggest_place(small, belana.id, "speise", row=2) == (2, 1)
    assert storage.harvest_box(b[0], seed["admin"], belana, None, DAY, warehouse=small, row=2) == "B-01-1"
    assert storage.harvest_box(b[1], seed["admin"], belana, None, DAY, warehouse=small, row=2) == "B-02-1"
    with pytest.raises(StorageError, match="Reihe B ist voll"):
        storage.harvest_box(b[2], seed["admin"], belana, None, DAY, warehouse=small, row=2)
    db.session.rollback()

    # Oberfläche: Reihe wird gemerkt und angezeigt, ungültige Reihe = automatisch
    form = {"sorte": belana.id, "fuell": "100", "modus": "einlagern", "lager": wh.id, "reihe": "3"}
    as_worker.post("/scan/ernte", data=form)
    html = as_worker.get("/scan").get_data(as_text=True)
    assert "Kartoffellager · Reihe C" in html and 'data-selected="3"' in html and "Nächster Platz: C-01" in html
    r = as_worker.post("/ernte/kiste", data={"code": b[3].code}, headers={"Accept": "application/json"})
    assert r.get_json()["place"] == "C-01-1"
    as_worker.post("/scan/ernte", data={**form, "reihe": "99"})
    assert "Reihe" not in as_worker.get("/scan").get_data(as_text=True).split('id="harvestSummary"')[1][:200]
