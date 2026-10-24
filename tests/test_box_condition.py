from datetime import date

import pytest

from app import storage
from app.models import Box
from app.storage import StorageError


def new_boxes(db, seed, n):
    boxes = storage.create_boxes(n, seed["bt"].id, seed["admin"])
    db.session.commit()
    return boxes


def test_defect_repair_retire(db, seed):
    admin, belana = seed["admin"], seed["belana"]
    a, b = new_boxes(db, seed, 2)
    with pytest.raises(StorageError, match="was kaputt"):
        storage.report_defect(a, admin, " ")
    storage.report_defect(a, admin, "Gitter gerissen")
    assert a.is_defect and a.condition_note == "Gitter gerissen" and a.condition_at
    assert storage.defect_boxes() == [a]
    # Ernte-Modus weist die Kiste ab, andere Kisten gehen
    with pytest.raises(StorageError, match="defekt"):
        storage.harvest_box(a, admin, belana, None, date(2026, 9, 10))
    storage.harvest_box(b, admin, belana, None, date(2026, 9, 10))
    # befüllte Kiste kann auch defekt sein, aber nicht ausgemustert werden
    storage.report_defect(b, admin, "Rahmen verbogen")
    with pytest.raises(StorageError, match="leeren"):
        storage.retire_box(b, admin)
    storage.repair_box(b, admin)
    assert not b.condition and b.condition_note is None
    with pytest.raises(StorageError, match="in Ordnung"):
        storage.repair_box(b, admin)

    # ausmustern: Notiz der Meldung bleibt, nicht mehr befüllbar/einlagerbar
    storage.retire_box(a, admin)
    assert a.is_retired and a.condition_note == "Gitter gerissen" and storage.retired_boxes() == [a]
    for action in (lambda: storage.set_fill(a, 100, admin),
                   lambda: storage.store_box(a, seed["wh"].id, 1, 1, admin),
                   lambda: storage.update_contents(a, admin, belana.id, None, None, None, 50, None),
                   lambda: storage.report_defect(a, admin, "x")):
        with pytest.raises(StorageError, match="ausgemustert"):
            action()
    storage.repair_box(a, admin)                        # wieder in Betrieb
    assert not a.condition
    db.session.commit()
    assert [m.action for m in sorted(a.movements, key=lambda m: m.id)] == \
        ["angelegt", "defekt", "ausgemustert", "repariert"]


def test_pages(as_admin, as_worker, as_viewer, db, seed):
    a, b = new_boxes(db, seed, 2)
    html = as_worker.get(f"/kiste/{a.code}").get_data(as_text=True)
    assert 'id="defectForm"' in html and "Gleich ausmustern" not in html
    assert 'id="defectForm"' not in as_viewer.get(f"/kiste/{a.code}").get_data(as_text=True)
    assert as_viewer.post(f"/kiste/{a.code}/zustand", data={"aktion": "defekt", "note": "x"}).status_code == 403

    r = as_worker.post(f"/kiste/{a.code}/zustand", data={"aktion": "defekt", "note": "Schweißnaht gebrochen"})
    assert r.headers["Location"].endswith("#zustand")
    assert db.session.get(Box, a.id).is_defect
    # Mitarbeiter darf nicht ausmustern
    assert as_worker.post(f"/kiste/{a.code}/zustand", data={"aktion": "ausmustern"}).status_code == 400

    html = as_viewer.get("/kisten/reparatur").get_data(as_text=True)
    assert a.code in html and "Schweißnaht gebrochen" in html and "1 Kiste defekt" in html
    assert "defekt" in as_viewer.get("/kisten?status=defekt").get_data(as_text=True)
    assert "1 Kiste defekt" in as_viewer.get("/lager/uebersicht").get_data(as_text=True)

    # Ernte-Modus per Scan: klare Meldung
    as_worker.post("/scan/ernte", data={"sorte": seed["belana"].id, "fuell": "100", "modus": "befuellen"})
    r = as_worker.post("/ernte/kiste", data={"code": a.code}, headers={"Accept": "application/json"}).get_json()
    assert not r["ok"] and "defekt (Schweißnaht gebrochen)" in r["message"]

    # Reparaturliste: repariert → zurück zur Liste
    r = as_worker.post(f"/kiste/{a.code}/zustand", data={"aktion": "repariert", "weiter": "reparatur"})
    assert r.headers["Location"].endswith("/kisten/reparatur") and not db.session.get(Box, a.id).condition

    # Admin mustert eine leere Kiste aus → aus der normalen Liste verschwunden
    as_admin.post(f"/kiste/{b.code}/zustand", data={"aktion": "ausmustern", "note": "zu alt"})
    assert db.session.get(Box, b.id).is_retired
    assert f'href="/kiste/{b.code}"' not in as_admin.get("/kisten").get_data(as_text=True)
    assert b.code in as_admin.get("/kisten?status=ausgemustert").get_data(as_text=True)
    html = as_admin.get("/kisten/reparatur").get_data(as_text=True)
    assert "1 Kiste ausgemustert" in html and "zu alt" in html
