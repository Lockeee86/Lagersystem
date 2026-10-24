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
    assert as_viewer.get("/ernte").status_code == 403
    # auf der Lager-Übersicht erreichbar (nur für Mitarbeiter und Admins)
    assert 'href="/ernte"' in as_worker.get("/lager/uebersicht").get_data(as_text=True)
    assert 'href="/ernte"' not in as_viewer.get("/lager/uebersicht").get_data(as_text=True)
    html = as_worker.get("/ernte").get_data(as_text=True)
    assert "Scannen starten" in html and "befüllen + einlagern" in html
    url = f"/ernte?los=1&sorte={belana.id}&schlag={seed['plot'].id}&datum=2026-09-10&fuell=100&modus=einlagern&lager={wh.id}"
    html = as_worker.get(url).get_data(as_text=True)
    assert "Nächster Platz: A-01" in html and 'name="lager" value="%d"' % wh.id in html
    form = {"sorte": belana.id, "schlag": seed["plot"].id, "datum": "2026-09-10", "fuell": "100",
            "modus": "einlagern", "lager": wh.id}
    r = as_worker.post("/ernte/kiste", data={**form, "code": b1.code}, headers={"Accept": "application/json"})
    data = r.get_json()
    assert data["ok"] and data["place"] == "A-01-1" and "Belana" in data["text"]
    assert b1.harvest_date == date(2026, 9, 10)
    r = as_worker.post("/ernte/kiste", data={**form, "code": b1.code}, headers={"Accept": "application/json"})
    assert not r.get_json()["ok"] and "schon befüllt" in r.get_json()["message"]
    r = as_worker.post("/ernte/kiste", data={**form, "code": "K-9999"}, headers={"Accept": "application/json"})
    assert not r.get_json()["ok"]
    # nur befüllen, per Formular
    form["modus"] = "befuellen"
    r = as_worker.post("/ernte/kiste", data={**form, "code": b2.code, "back": "/ernte?los=1"})
    assert r.status_code == 302 and db.session.get(Box, b2.id).fill == 100 and not b2.is_stored
