from datetime import date

from app import machines, storage


def fill(db, seed, n, harvest, pos):
    boxes = storage.create_boxes(n, seed["bt"].id, seed["admin"])
    for b in boxes:
        storage.update_contents(b, seed["admin"], seed["belana"].id, harvest, None, None, 100, None)
        storage.store_box(b, seed["wh"].id, *pos, seed["admin"])
    db.session.commit()
    return boxes


def test_age_levels_and_oldest(as_admin, as_worker, as_viewer, db, seed):
    today = date.today()
    old = fill(db, seed, 2, machines.add_months(today, -10), (1, 1))      # rot (ab 9 Monaten)
    warn = fill(db, seed, 1, machines.add_months(today, -7), (1, 2))      # gelb (ab 6)
    fresh = fill(db, seed, 1, today, (1, 3))
    assert storage.age_level(old[0]) == "old" and storage.age_level(warn[0]) == "warn"
    assert storage.age_level(fresh[0]) is None
    rows = storage.oldest_boxes()
    assert {r["box"].id for r in rows[:2]} == {old[0].id, old[1].id} and rows[-1]["box"].id == fresh[0].id
    on_top = {r["box"].id: r["on_top"] for r in rows}
    assert on_top[old[1].id] and not on_top[old[0].id]                     # die obere ist greifbar

    # Karte, Liste, Stapel, Kiste
    html = as_viewer.get(f"/lager/{seed['wh'].id}?ansicht=karte").get_data(as_text=True)
    assert "age-old" in html and "age-warn" in html and 'data-filter="old"' in html and "Älteste Ware zuerst" in html
    html = as_viewer.get(f"/lager/{seed['wh'].id}?ansicht=liste").get_data(as_text=True)
    assert "age-icon-old" in html
    html = as_viewer.get(f"/lager/{seed['wh'].id}/stapel/1/1?partial=1").get_data(as_text=True)
    assert "age-icon-old" in html
    assert "lange gelagert" in as_viewer.get(f"/kiste/{old[0].code}").get_data(as_text=True)
    html = as_viewer.get("/lager/aelteste").get_data(as_text=True)
    assert html.index(old[0].code) < html.index(warn[0].code) < html.index(fresh[0].code) and "oben" in html
    assert "3 Kisten lange gelagert" in as_viewer.get("/lager/uebersicht").get_data(as_text=True)

    # Schwellen in der Verwaltung: nur Admin, zweite ≥ erste
    assert as_worker.post("/admin/lager/lagerdauer", data={"warn": "12", "old": "18"}).status_code == 403
    as_admin.post("/admin/lager/lagerdauer", data={"warn": "8", "old": "6"})
    assert storage.age_limits() == (6, 9)
    as_admin.post("/admin/lager/lagerdauer", data={"warn": "12", "old": "18"})
    assert storage.age_limits() == (12, 18) and storage.age_level(old[0]) is None
