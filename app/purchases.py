"""Bestellungen: Mitarbeiter schicken Bestellwünsche, der Besteller (Admin oder „darf bestellen“) bearbeitet sie.

Ablauf: angefragt → bestellt → geliefert → erledigt (oder abgelehnt, mit Begründung).
Wie `storage.py` verändern die Funktionen nur die Session, committen muss der Aufrufer.
"""
from datetime import date

from .extensions import db
from .i18n import gettext as _
from .models import PURCHASE_OPEN, PURCHASE_STATUSES, Photo, PurchaseRequest, User, utcnow
from .storage import StorageError


def buyers():
    return [u for u in User.query.filter_by(active=True).order_by(User.username) if u.is_buyer]


def create_request(user, title, details=None, quantity=None, urgent=False, machine_id=None, machine_event_id=None,
                   photo_names=None):
    title = (title or "").strip()
    if not title:
        raise StorageError(_("Bitte angeben, was gebraucht wird."))
    req = PurchaseRequest(title=title[:200], details=details or None, quantity=quantity or None, urgent=bool(urgent),
                          machine_id=machine_id, machine_event_id=machine_event_id,
                          requested_by_id=user.id if user else None)
    for name in photo_names or []:
        req.photos.append(Photo(filename=name, user_id=user.id if user else None))
    db.session.add(req)
    db.session.flush()
    return req


def update_request(req, user, status, supplier=None, order_number=None, price=None, ordered_on=None,
                   expected_on=None, delivered_on=None, buyer_note=None):
    """Besteller trägt Status und Bestelldaten ein. Datumsfelder werden beim Statuswechsel sinnvoll vorbelegt."""
    if status not in PURCHASE_STATUSES:
        raise StorageError(_("Unbekannter Status."))
    if status == "abgelehnt" and not buyer_note:
        raise StorageError(_("Bitte begründen, warum nicht bestellt wird."))
    req.supplier, req.order_number, req.price = supplier or None, order_number or None, price
    req.ordered_on, req.expected_on, req.delivered_on = ordered_on, expected_on, delivered_on
    req.buyer_note = buyer_note or None
    if status in ("bestellt", "geliefert", "erledigt") and req.ordered_on is None:
        req.ordered_on = date.today()
    if status in ("geliefert", "erledigt") and req.delivered_on is None:
        req.delivered_on = date.today()
    req.status = status
    req.handled_by_id = user.id if user else None
    req.updated_at = utcnow()
    db.session.flush()


def mark_done(req, user):
    """Teil ist da und eingebaut/verbraucht – darf auch der Anfragende."""
    if req.status != "geliefert":
        raise StorageError(_("Erledigt werden kann nur, was geliefert ist."))
    req.status = "erledigt"
    req.updated_at = utcnow()
    db.session.flush()


# --- Abfragen ---------------------------------------------------------------

def counts():
    """Für Besteller: neue Wünsche und überfällige Lieferungen."""
    new = PurchaseRequest.query.filter_by(status="angefragt").count()
    late = PurchaseRequest.query.filter(PurchaseRequest.status == "bestellt",
                                        PurchaseRequest.expected_on < date.today()).count()
    return {"new": new, "late": late}


def open_requests(user=None):
    query = PurchaseRequest.query.filter(PurchaseRequest.status.in_(PURCHASE_OPEN))
    if user is not None:
        query = query.filter(PurchaseRequest.requested_by_id == user.id)
    return query.order_by(PurchaseRequest.urgent.desc(), PurchaseRequest.created_at).all()


def arrived_for(user):
    """Für den Anfragenden: geliefert, aber noch nicht als erledigt markiert."""
    return PurchaseRequest.query.filter_by(status="geliefert", requested_by_id=user.id)\
        .order_by(PurchaseRequest.delivered_on.desc()).all()


def for_machine(machine_id):
    return PurchaseRequest.query.filter(PurchaseRequest.machine_id == machine_id,
                                        PurchaseRequest.status.in_(PURCHASE_OPEN))\
        .order_by(PurchaseRequest.created_at.desc()).all()
