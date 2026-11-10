"""Meldungen zum Nachlesen (Glocke), Schriftgröße, Erste Schritte, Farbwahl der Lagerkarte, große Tasten."""
from datetime import timedelta

from app import push, storage
from app.models import Notice, PushSubscription, utcnow
from app.push import Msg


def test_notice_for_everyone_who_wants_topic(app, db, seed, as_worker):
    """Auch ohne Push am Gerät landet die Nachricht unter der Glocke – in der Sprache des Empfängers."""
    worker, admin = seed["worker"], seed["admin"]
    worker.language = "pl"
    db.session.add(PushSubscription(user=admin, endpoint="https://push.example/a", p256dh="a", auth="b"))
    db.session.commit()
    push.outbox.clear()
    with app.test_request_context():
        sent = push.notify([worker, admin], None, Msg("Neue Aufgabe"), "Hoftor ölen", "/aufgaben")
    assert sent == 1 and len(push.outbox) == 1                    # Push nur ans Gerät mit Abo
    assert Notice.query.count() == 2
    mine = Notice.query.filter_by(user_id=worker.id).one()
    assert mine.title == "Nowe zadanie" and mine.body == "Hoftor ölen" and mine.url == "/aufgaben"
    assert push.unread_count(worker) == 1
    home = as_worker.get("/").get_data(as_text=True)
    assert 'class="count">1<' in home
    page = as_worker.get("/meldungen").get_data(as_text=True)
    assert "Nowe zadanie" in page and "is-new" in page
    assert push.unread_count(worker) == 0                          # Öffnen = gelesen
    assert "is-new" not in as_worker.get("/meldungen").get_data(as_text=True)
    push.outbox.clear()


def test_notice_cleanup(app, db, seed):
    worker = seed["worker"]
    db.session.add(Notice(user_id=worker.id, title="alt", created_at=utcnow() - timedelta(days=push.NOTICE_DAYS + 1)))
    db.session.add(Notice(user_id=worker.id, title="neu"))
    db.session.commit()
    assert push.cleanup_notices() == 1
    assert [n.title for n in push.notices(worker)] == ["neu"]


def test_font_size_and_onboarding(app, db, seed, as_worker):
    worker = seed["worker"]
    home = as_worker.get("/").get_data(as_text=True)
    assert 'id="welcome"' in home and "data-font" not in home.split("<head>")[0]
    assert as_worker.post("/konto/erste-schritte").status_code == 204
    assert worker.onboarded and 'id="welcome"' not in as_worker.get("/").get_data(as_text=True)
    as_worker.post("/konto/schrift", data={"font_size": "gross"})
    assert worker.font_size == "gross"
    assert 'data-font="gross"' in as_worker.get("/").get_data(as_text=True)
    as_worker.post("/konto/schrift", data={"font_size": "riesig"})         # Unbekanntes → normal
    assert worker.font_size == ""


def test_map_color_data_and_thumb_bar(app, db, seed, as_worker, as_viewer):
    (box,) = storage.create_boxes(1, seed["bt"].id, seed["admin"])
    db.session.commit()
    page = as_worker.get(f"/kiste/{box.code}").get_data(as_text=True)
    assert 'class="thumb-bar' in page and 'href="#lagerplatz"' in page and 'id="fuellstand"' in page
    assert 'class="thumb-bar' not in as_viewer.get(f"/kiste/{box.code}").get_data(as_text=True)
    karte = as_worker.get("/lager").get_data(as_text=True)
    assert 'id="mapColor"' in karte and 'data-color="alter"' in karte and "mapColorLegend" in karte
