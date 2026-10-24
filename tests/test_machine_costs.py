from datetime import date
from decimal import Decimal

from app import machines
from app.models import FuelEntry, MachineEvent, PurchaseRequest


def test_costs_per_year(as_admin, as_worker, db, seed):
    m = machines.create_machine("Testschlepper", "schlepper", seed["admin"], counter_unit="h")
    db.session.flush()
    admin = seed["admin"]
    db.session.add_all([
        # Diesel: Lieferungen 2026 zu 1,40 und 1,60 → Mittel nach Menge 1,50
        FuelEntry(kind="lieferung", date=date(2026, 2, 1), liters=Decimal("1000"), price_per_liter=Decimal("1.400")),
        FuelEntry(kind="lieferung", date=date(2026, 6, 1), liters=Decimal("1000"), price_per_liter=Decimal("1.600")),
        FuelEntry(kind="tanken", date=date(2025, 12, 20), liters=Decimal("50"), machine_id=m.id, counter=900),
        FuelEntry(kind="tanken", date=date(2026, 3, 1), liters=Decimal("100"), machine_id=m.id, counter=950),
        FuelEntry(kind="tanken", date=date(2026, 9, 1), liters=Decimal("100"), machine_id=m.id, counter=1000),
        MachineEvent(machine_id=m.id, kind="reparatur", date=date(2026, 5, 3), cost=Decimal("250.00"), user_id=admin.id),
        MachineEvent(machine_id=m.id, kind="reparatur", date=date(2025, 5, 3), cost=Decimal("999.00"), user_id=admin.id),
        PurchaseRequest(title="Filter", machine_id=m.id, price=Decimal("50.00"), status="geliefert",
                        delivered_on=date(2026, 4, 1), requested_by_id=admin.id),
        PurchaseRequest(title="Abgelehnt", machine_id=m.id, price=Decimal("500.00"), status="abgelehnt",
                        requested_by_id=admin.id),
    ])
    db.session.commit()
    assert machines.diesel_price(2026) == Decimal("1.500")
    assert machines.diesel_price(2027) == Decimal("1.600")                # letzter bekannter Preis
    c = machines.costs(m, 2026)
    assert c["liters"] == Decimal("200") and c["diesel"] == Decimal("300.00")
    assert c["repairs"] == Decimal("250.00") and c["purchases"] == Decimal("50.00") and c["total"] == Decimal("600.00")
    # 900 h (letzter Stand vor dem Jahr) → 1000 h = 100 h → 6 €/h
    assert c["used"] == 100 and c["per_unit"] == Decimal("6.00") and c["unit"] == "€/h"

    # Seiten: nur Admins
    assert as_worker.get("/maschinen/kosten").status_code == 403
    html = as_admin.get("/maschinen/kosten?jahr=2026").get_data(as_text=True)
    assert "Testschlepper" in html and "600,00 €" in html and "6,00 €" in html and "1,500 €" in html
    html = as_admin.get(f"/maschine/{m.id}?kosten=2026").get_data(as_text=True)
    assert "Kosten 2026" in html and "300,00 €" in html and "je Betriebsstunde" in html
    assert "Kosten 2026" not in as_worker.get(f"/maschine/{m.id}?kosten=2026").get_data(as_text=True)
