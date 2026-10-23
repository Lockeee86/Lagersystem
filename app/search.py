"""Suche über alle Bereiche: Kisten, Schläge, Maschinen, Lieferscheine, Kunden, Aufgaben."""
from sqlalchemy import or_

from . import machines, storage
from .models import Box, Customer, Delivery, Machine, Plot, Task

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
    }
    if user.is_admin:
        results["customers"] = Customer.query.filter(or_(Customer.name.ilike(like), Customer.number.ilike(like)))\
            .order_by(Customer.name).limit(LIMIT).all()
    return results
