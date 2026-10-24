from datetime import date
from decimal import Decimal

from app import fields, storage
from app.models import Plot, PlotYear, Variety


def test_yield_history(as_viewer, db, seed):
    plot = seed["plot"]
    plot.area_ha = Decimal("2")
    other = Plot(name="Sonnenacker", area_ha=Decimal("1"))
    rot = Variety(name="Testsorte Rot", color="#c00000")
    db.session.add_all([other, rot, PlotYear(plot=plot, year=2025, crop="Kartoffel")])
    db.session.flush()
    boxes = storage.create_boxes(8, seed["bt"].id, seed["admin"])
    # 2025: Acker Nord 5 Kisten Belana (2,0 t auf 2 ha → 1,0 t/ha)
    for b in boxes[:5]:
        storage.update_contents(b, seed["admin"], seed["belana"].id, date(2025, 9, 1), plot.id, None, 100, None)
    # 2025: Sonnenacker gemischt (Belana + Rot) → zählt nicht je Sorte
    storage.update_contents(boxes[5], seed["admin"], seed["belana"].id, date(2025, 9, 2), other.id, None, 100, None)
    storage.update_contents(boxes[6], seed["admin"], rot.id, date(2025, 9, 2), other.id, None, 100, None)
    db.session.commit()

    by_plot, by_variety, best, best_v = fields.yield_history([plot, other], [2025, 2026])
    assert by_plot[plot.id][2025]["kg"] == 2000 and by_plot[plot.id][2025]["t_ha"] == 1.0
    assert by_plot[other.id][2025]["t_ha"] == 0.8 and by_plot[plot.id][2026]["kg"] == 0
    assert by_variety["Belana"][2025] == {"kg": 2000, "ha": 2.0, "plots": 1, "t_ha": 1.0}
    assert "Testsorte Rot" not in by_variety and best == 1.0

    html = as_viewer.get("/ertraege").get_data(as_text=True)
    assert "Erträge" in html and "Acker Nord" in html and "Sonnenacker" in html and "1,0" in html
    assert "Je Sorte" in html and "Belana" in html
