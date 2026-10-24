from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import push, storage
from app.models import PushSubscription

BERLIN = ZoneInfo("Europe/Berlin")


def test_report_and_evening_push(app, as_worker, as_viewer, db, seed):
    from datetime import date
    today = date.today()
    boxes = storage.create_boxes(3, seed["bt"].id, seed["admin"])
    db.session.commit()
    for b in boxes[:2]:
        storage.harvest_box(b, seed["worker"], seed["belana"], seed["plot"].id, today, warehouse=seed["wh"])
    storage.harvest_box(boxes[2], seed["worker"], seed["belana"], None, today, fill=50)
    db.session.commit()
    # Nachsortieren einer vollen Kiste zählt nicht als Ernte
    storage.update_contents(boxes[0], seed["admin"], seed["belana"].id, today, seed["plot"].id, "35–55 mm", 75, None)
    db.session.commit()

    r = storage.harvest_report(today, BERLIN)
    assert r["count"] == 3 and r["kg"] == 400 + 400 + 200
    assert r["varieties"] == [{"label": "Belana", "boxes": 3, "kg": 1000}]
    assert {e["label"]: e["boxes"] for e in r["plots"]} == {"Acker Nord": 2, None: 1}
    assert {e["label"]: e["boxes"] for e in r["rows"]} == {"Kartoffellager · A": 2, None: 1}
    assert r["users"][0]["label"] == seed["worker"].name

    html = as_viewer.get("/ernte/bericht").get_data(as_text=True)
    assert "Ernte-Tagesbericht" in html and "Acker Nord" in html and "ohne Schlag" in html and "1,0 t" in html
    assert "keine Kisten befüllt" in as_viewer.get("/ernte/bericht?datum=2000-01-01").get_data(as_text=True)

    # abends Push – von sich aus nur für Admins
    for u in (seed["admin"], seed["worker"]):
        db.session.add(PushSubscription(user_id=u.id, endpoint=f"https://push.example/{u.username}", p256dh="B",
                                        auth="a", device="Test"))
    db.session.commit()
    push.outbox.clear()
    now = datetime.now(BERLIN).replace(hour=19, minute=5)
    push.daily(app, now)
    msgs = [m for m in push.outbox if m["tag"] == "harvest-report"]
    assert [m["user_id"] for m in msgs] == [seed["admin"].id]
    assert msgs[0]["title"] == "Ernte heute: 3 Kisten (≈ 1,0 t)" and msgs[0]["body"] == "Belana 3"
    push.daily(app, now.replace(hour=21))
    assert len([m for m in push.outbox if m["tag"] == "harvest-report"]) == 1     # nur einmal am Tag
