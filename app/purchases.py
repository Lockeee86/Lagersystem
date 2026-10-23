"""Bestellungen: Mitarbeiter schicken Bestellwünsche, der Besteller (Admin oder „darf bestellen“) bearbeitet sie.

Ablauf: neu → (beim Lieferanten angefragt) → bestellt → geliefert → erledigt (oder abgelehnt, mit Begründung).
„Angefragt“ ist optional – wer gleich bestellt, überspringt es.
Wie `storage.py` verändern die Funktionen nur die Session, committen muss der Aufrufer.
"""
from datetime import date

from sqlalchemy import or_

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
                   expected_on=None, delivered_on=None, buyer_note=None, inquired_on=None):
    """Besteller trägt Status und Bestelldaten ein. Datumsfelder werden beim Statuswechsel sinnvoll vorbelegt."""
    if status not in PURCHASE_STATUSES:
        raise StorageError(_("Unbekannter Status."))
    if status == "abgelehnt" and not buyer_note:
        raise StorageError(_("Bitte begründen, warum nicht bestellt wird."))
    req.supplier, req.order_number, req.price = supplier or None, order_number or None, price
    req.inquired_on = inquired_on
    req.ordered_on, req.expected_on, req.delivered_on = ordered_on, expected_on, delivered_on
    req.buyer_note = buyer_note or None
    if status == "angefragt" and req.inquired_on is None:
        req.inquired_on = date.today()
    if status in ("bestellt", "geliefert", "erledigt") and req.ordered_on is None:
        req.ordered_on = date.today()
    if status in ("geliefert", "erledigt") and req.delivered_on is None:
        req.delivered_on = date.today()
    req.status = status
    if req.assignee_id is None and user is not None:
        req.assignee_id = user.id                     # wer zuerst bearbeitet, hat übernommen
    req.handled_by_id = user.id if user else None
    req.updated_at = utcnow()
    db.session.flush()


def assign(req, user, assignee):
    """Übernehmen (assignee = user) oder an einen anderen Besteller weitergeben."""
    if assignee is None or not assignee.is_buyer or not assignee.active:
        raise StorageError(_("Nur an Besteller weitergeben."))
    if not req.is_open:
        raise StorageError(_("Diese Bestellung ist abgeschlossen."))
    req.assignee_id = assignee.id
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

def _mine_or_free(user):
    return or_(PurchaseRequest.assignee_id.is_(None), PurchaseRequest.assignee_id == user.id)


def counts(user):
    """Für den Besteller-Zähler: noch nicht übernommene Wünsche, ihm zugeteilte und noch nicht bestellte,
    überfällige Lieferungen (seine und freie)."""
    fresh = PurchaseRequest.query.filter_by(status="neu")
    new = fresh.filter(PurchaseRequest.assignee_id.is_(None)).count()
    mine = fresh.filter(PurchaseRequest.assignee_id == user.id).count()
    late = PurchaseRequest.query.filter(PurchaseRequest.status == "bestellt", _mine_or_free(user),
                                        PurchaseRequest.expected_on < date.today()).count()
    return {"new": new, "mine": mine, "late": late}


def open_requests(buyer=None):
    """Offene Wünsche; mit `buyer` nur seine und noch nicht übernommene (freie zuerst)."""
    query = PurchaseRequest.query.filter(PurchaseRequest.status.in_(PURCHASE_OPEN))
    if buyer is not None:
        query = query.filter(_mine_or_free(buyer))
    items = query.order_by(PurchaseRequest.urgent.desc(), PurchaseRequest.created_at).all()
    return sorted(items, key=lambda r: (r.assignee_id is not None, not r.urgent))


def arrived_for(user):
    """Für den Anfragenden: geliefert, aber noch nicht als erledigt markiert."""
    return PurchaseRequest.query.filter_by(status="geliefert", requested_by_id=user.id)\
        .order_by(PurchaseRequest.delivered_on.desc()).all()


def for_machine(machine_id):
    return PurchaseRequest.query.filter(PurchaseRequest.machine_id == machine_id,
                                        PurchaseRequest.status.in_(PURCHASE_OPEN))\
        .order_by(PurchaseRequest.created_at.desc()).all()
