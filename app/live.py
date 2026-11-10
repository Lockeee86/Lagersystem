"""Live-Aktualisierung: zählt je Thema mit, wenn sich etwas geändert hat. Offene Seiten fragen `/api/version` ab und
laden sich neu, sobald ihr Thema weitergezählt hat (z. B. ein Kollege hakt eine Aufgabe ab).

Die Zähler liegen im Speicher – das genügt, weil Gunicorn mit einem Worker läuft; nach einem Neustart beginnen sie
bei null (offene Seiten laden dann einmal neu). Massen-`query.update()` läuft an den Zählern vorbei."""
import threading

from sqlalchemy import event
from sqlalchemy.orm import Session

# Tabelle → Thema (in `base.html` steht je Seite, auf welche Themen sie hört: <main data-live="…">)
TOPICS = {"tasks": "tasks", "handovers": "handover", "feeding_shifts": "feeding", "feeding_days": "feeding",
          "purchase_requests": "purchases"}
# Seite → Themen ("movement" = Kistenbewegungen, wie bisher)
PAGES = {
    "main.dashboard": "movement,tasks,feeding,handover,purchases",
    "team.day_plan": "tasks", "team.open_page": "tasks", "tasks.task_list": "tasks",
    "team.handovers": "handover", "biogas.feeding_page": "feeding", "purchases.purchase_list": "purchases",
}

_lock = threading.Lock()
_counters = dict.fromkeys(set(TOPICS.values()), 0)


def stamps():
    with _lock:
        return dict(_counters)


def _touched(session):
    topics = set()
    for obj in list(session.new) + list(session.deleted) + [o for o in session.dirty if session.is_modified(o)]:
        topic = TOPICS.get(getattr(obj, "__tablename__", None))
        if topic:
            topics.add(topic)
    return topics


@event.listens_for(Session, "before_flush")
def _collect(session, _context, _instances):
    topics = _touched(session)
    if topics:
        session.info.setdefault("live_topics", set()).update(topics)


@event.listens_for(Session, "after_commit")
def _bump(session):
    topics = session.info.pop("live_topics", None)
    if topics:
        with _lock:
            for topic in topics:
                _counters[topic] += 1


@event.listens_for(Session, "after_rollback")
def _forget(session):
    session.info.pop("live_topics", None)
