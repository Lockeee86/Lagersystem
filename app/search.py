"""Suche über alle Bereiche: Kisten, Schläge, Maschinen, Ersatzteile, Lieferscheine, Kunden, Aufgaben, Lieferanten."""
from sqlalchemy import or_

from . import machines, storage
from .models import Box, Customer, Delivery, Machine, MachineDocument, MachinePart, Plot, PurchaseRequest, Supplier, Task

LIMIT = 20


def direct_hit(q):
    """Genauer Kisten- oder Maschinencode → (Art, Objekt) für die direkte Weiterleitung."""
    code = machines.normalize_code(q)
    if code and (m := Machine.query.filter_by(code=code).first()):
        return "machine", m
    if q.strip().upper().startswith("K") or q.strip().isdigit():
        code = storage.normalize_code(q)
        if code and (b := Box.query.filter_by(code=code).first()):
            return "box", b
    d = Delivery.query.filter(Delivery.number.ilike(q.strip())).first()
    if d:
        return "delivery", d
    return None


def search(q, user):
    q = q.strip()
    like = f"%{q}%"
    results = {
        "boxes": Box.query.filter(or_(Box.code.ilike(like), Box.note.ilike(like), Box.grade.ilike(like)))
        .order_by(Box.code).limit(LIMIT).all(),
        "plots": Plot.query.filter(or_(Plot.name.ilike(like), Plot.number == q, Plot.note.ilike(like)))
        .order_by(Plot.active.desc(), Plot.name).limit(LIMIT).all(),
        "machines": Machine.query.filter(or_(Machine.name.ilike(like), Machine.code.ilike(like), Machine.plate.ilike(like),
                                             Machine.manufacturer.ilike(like), Machine.model.ilike(like)))
        .order_by(Machine.active.desc(), Machine.name).limit(LIMIT).all(),
        "deliveries": Delivery.query.join(Customer).filter(or_(Delivery.number.ilike(like), Customer.name.ilike(like),
                                                               Delivery.vehicle.ilike(like)))
        .order_by(Delivery.date.desc()).limit(LIMIT).all(),
        "tasks": Task.query.filter(or_(Task.title.ilike(like), Task.description.ilike(like)))
        .order_by(Task.done_at.isnot(None), Task.due_on.asc().nulls_last()).limit(LIMIT).all(),
        "purchases": PurchaseRequest.query.filter(or_(PurchaseRequest.title.ilike(like), PurchaseRequest.details.ilike(like),
                                                      PurchaseRequest.order_number.ilike(like),
                                                      PurchaseRequest.supplier.ilike(like)))
        .order_by(PurchaseRequest.created_at.desc()).limit(LIMIT).all(),
        "parts": MachinePart.query.filter(or_(MachinePart.name.ilike(like), MachinePart.part_number.ilike(like),
                                              MachinePart.manufacturer.ilike(like), MachinePart.note.ilike(like)))
        .order_by(MachinePart.name).limit(LIMIT).all(),
        "documents": MachineDocument.query.filter(or_(MachineDocument.title.ilike(like),
                                                      MachineDocument.original_name.ilike(like)))
        .order_by(MachineDocument.created_at.desc()).limit(LIMIT).all(),
        "suppliers": Supplier.query.filter(or_(Supplier.name.ilike(like), Supplier.contact.ilike(like),
                                               Supplier.customer_no.ilike(like)))
        .order_by(Supplier.active.desc(), Supplier.name).limit(LIMIT).all(),
    }
    if user.is_admin:
        results["customers"] = Customer.query.filter(or_(Customer.name.ilike(like), Customer.number.ilike(like)))\
            .order_by(Customer.name).limit(LIMIT).all()
    return results
