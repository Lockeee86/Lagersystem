"""Partien: Einlagern ohne Scannen (Anzahl + Reihe), Auslagern und Verladen aus einer Partie."""
from datetime import date

import pytest

from app import sales, storage
from app.models import Box, Customer, Delivery, Movement, Plot, Variety
from app.storage import StorageError

ROW_C = 3


def store(seed, count, rest=None, usage="pflanzgut", plot=None, row=ROW_C, slot=None, variety=None):
    return storage.store_batch(seed["wh"], row, count, rest, variety or seed["belana"], seed["admin"], seed["bt"].id,
                               plot_id=(plot or seed["plot"]).id, harvest_date=date(2026, 9, 20), usage=usage,
                               slot=slot)


def test_plan_fills_row_and_puts_half_box_on_top(db, seed):
    wh = seed["wh"]                                         # 15 Plätze je Reihe, 5 hoch
    plan = storage.plan_batch(wh, ROW_C, 10, 50, seed["belana"], "pflanzgut")
    assert [p for p, _f in plan] == [(3, 1)] * 5 + [(3, 2)] * 5 + [(3, 3)]
    assert [f for _p, f in plan][-1] == 50
    summary = storage.plan_summary(wh, plan)
    assert [(r["label"], r["full"], r["rest"]) for r in summary] == [("C-01", 5, None), ("C-02", 5, None),
                                                                      ("C-03", 0, 50)]
    # mit „ab Platz“ zuerst dorthin
    assert storage.plan_batch(wh, ROW_C, 2, None, seed["belana"], slot=7)[0][0] == (3, 7)
    with pytest.raises(StorageError, match="Reihe"):
        storage.plan_batch(wh, ROW_C, 76, None, seed["belana"])            # 75 passen
    with pytest.raises(StorageError):
        storage.plan_batch(wh, ROW_C, 0, None, seed["belana"])


def test_store_batch_reuses_empty_boxes(db, seed):
    spare = storage.create_boxes(3, seed["bt"].id, seed["admin"])        # leere Kisten im Bestand
    boxes = store(seed, 4, rest=50)
    db.session.commit()
    assert len(boxes) == 5 and {b.id for b in spare} <= {b.id for b in boxes}
    assert Box.query.count() == 5                                        # 2 neu angelegt
    assert [b.location for b in boxes] == ["C-01-1", "C-01-2", "C-01-3", "C-01-4", "C-01-5"]
    assert boxes[-1].fill == 50 and all(b.fill == 100 for b in boxes[:-1])
    assert all(b.variety == seed["belana"] and b.plot == seed["plot"] and b.effective_usage == "pflanzgut"
               for b in boxes)
    assert Movement.query.filter_by(action="eingelagert").count() == 5
    # gleiche Ware kommt dazu: erst auf passende Stapel mit Platz, dann der nächste freie
    more = store(seed, 2)
    assert [b.location for b in more] == ["C-02-1", "C-02-2"]


def test_lots_and_taking_out(db, seed):
    store(seed, 7, rest=50)                                              # C-01 voll, C-02: 2 + ½
    food = store(seed, 3, usage="speise")                                # andere Partie in derselben Reihe
    db.session.commit()
    assert [b.location for b in food] == ["C-03-1", "C-03-2", "C-03-3"]
    lots = storage.lots(seed["wh"])
    assert len(lots) == 2
    seed_lot = next(l for l in lots if l["usage"] == "pflanzgut")
    assert (seed_lot["count"], seed_lot["amount"], seed_lot["label"]) == (8, 7.5, "7½")
    assert seed_lot["stacks"] == ["C-01", "C-02"] and seed_lot["row_label"] == "C"
    assert storage.find_lot(seed_lot["key"])["count"] == 8
    assert storage.find_lot("x|y") is None
    # am leichtesten erreichbar: der kleine Stapel C-02 von oben, die halbe zuerst
    taken = storage.take_from_lot(seed_lot, 3, seed["admin"])
    db.session.commit()
    assert [b.fill for b in taken] == [0, 0, 0] and not any(b.is_stored for b in taken)
    assert Movement.query.filter_by(action="ausgelagert").count() == 3
    lot = storage.find_lot(seed_lot["key"])
    assert (lot["count"], lot["label"], lot["stacks"]) == (5, "5", ["C-01"])
    with pytest.raises(StorageError, match="nur 5"):
        storage.pick_from_lot(lot, 6)
    # „Inhalt bleibt“: Kiste ist raus, aber nicht leer
    kept = storage.take_from_lot(lot, 1, seed["admin"], empty=False)
    assert kept[0].fill == 100 and not kept[0].is_stored
    # leere Kisten werden beim nächsten Einlagern wieder genommen
    again = store(seed, 2)
    assert {b.id for b in again} <= {b.id for b in taken}


def test_foreign_box_on_top_is_avoided(db, seed):
    store(seed, 2, slot=1)                                               # C-01: 2 Belana Saat
    store(seed, 1, usage="speise", slot=1)                               # obendrauf Speise
    store(seed, 1, slot=5)                                               # C-05: 1 Belana Saat
    db.session.commit()
    lot = next(l for l in storage.lots(seed["wh"]) if l["usage"] == "pflanzgut")
    assert [b.location for b in storage.pick_from_lot(lot, 2)] == ["C-05-1", "C-01-2"]


def test_pages(as_admin, as_worker, as_viewer, db, seed):
    form = {"sorte": seed["belana"].id, "schlag": seed["plot"].id, "verwendung": "pflanzgut", "lager": seed["wh"].id,
            "reihe": ROW_C, "anzahl": "10", "rest": "50", "datum": "2026-09-20"}
    assert as_viewer.get("/einlagern").status_code == 403
    html = as_worker.get("/einlagern").get_data(as_text=True)
    assert "Volle Kisten" in html and "Weiter zur Vorschau" in html
    html = as_worker.post("/einlagern", data=form).get_data(as_text=True)          # Vorschau, noch nichts gebucht
    assert "Vorschau" in html and "C-01" in html and "C-03" in html and "10½ Kisten Belana" in html
    assert Box.query.count() == 0
    r = as_worker.post("/einlagern", data=form | {"bestaetigt": "1"})
    assert r.status_code == 302 and "/einlagern" in r.headers["Location"]
    assert Box.query.filter(Box.warehouse_id.isnot(None)).count() == 11
    assert "10½ Kisten Belana in Reihe C eingelagert (C-01, C-02, C-03)." in as_worker.get(r.headers["Location"]).get_data(as_text=True)

    html = as_viewer.get("/partien").get_data(as_text=True)
    assert "Reihe C" in html and "10½ Kisten" in html and "Auslagern" not in html
    lot = storage.lots(seed["wh"])[0]
    assert as_viewer.post("/partie/auslagern", data={"partie": lot["key"], "anzahl": 1}).status_code == 403
    as_worker.post("/partie/auslagern", data={"partie": lot["key"], "anzahl": 2})
    assert storage.lots(seed["wh"])[0]["label"] == "9"                 # die halbe und eine volle

    # Lieferschein: aus Partie verladen
    customer = Customer(name="Saatzucht Nord")
    db.session.add(customer)
    db.session.commit()
    delivery = sales.create_delivery(customer, date.today(), seed["admin"])
    db.session.commit()
    html = as_admin.get(f"/lieferschein/{delivery.id}").get_data(as_text=True)
    assert "Aus Partie verladen" in html and "Reihe C – Belana" in html
    as_admin.post(f"/lieferschein/{delivery.id}/partie", data={"partie": lot["key"], "anzahl": 4})
    delivery = db.session.get(Delivery, delivery.id)
    assert delivery.box_count == 4 and storage.lots(seed["wh"])[0]["count"] == 5
    # leere Kisten auf dem offenen Lieferschein werden beim Einlagern nicht wiederverwendet
    loaded = {i.box_id for i in delivery.items}
    again = store(seed, 3)
    assert not loaded & {b.id for b in again}
