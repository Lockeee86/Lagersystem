from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import machines, push, purchases, tasks
from app.models import MaintenanceTask, PushSubscription, Setting

from .conftest import make_user

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture(autouse=True)
def _clear_outbox():
    push.outbox.clear()
    yield
    push.outbox.clear()


def add_device(db, user, n=1):
    sub = PushSubscription(user_id=user.id, endpoint=f"https://push.example/{user.username}/{n}",
                           p256dh="BKey", auth="auth", device="Android · Chrome")
    db.session.add(sub)
    db.session.commit()
    return sub


def sent_to(user):
    return [m for m in push.outbox if m["user_id"] == user.id]


def test_topics_by_role(db, seed):
    admin, worker, viewer = seed["admin"], seed["worker"], seed["viewer"]
    assert "purchase_new" in admin.push_topics() and "purchase_new" not in worker.push_topics()
    assert viewer.push_topics() == ["frost", "blight"]                 # Betrachter: nur Wetter
    worker.push_off = "fault,frost"
    assert not worker.wants_push("fault") and worker.wants_push("tasks_new")
    assert not viewer.wants_push("tasks_new")
    # Wetter-Warnungen: von sich aus nur für Admins an, andere schalten sie ein
    assert admin.wants_push("frost") and admin.wants_push("blight")
    assert not worker.wants_push("frost") and not viewer.wants_push("blight")
    viewer.set_push_topics({"blight"})
    assert viewer.wants_push("blight") and not viewer.wants_push("frost") and viewer.push_on == "blight"
    admin.set_push_topics(set(admin.push_topics()) - {"frost", "handover"})
    assert not admin.wants_push("frost") and admin.push_off == "frost" and admin.push_on == ""


def test_subscribe_and_settings(app, as_worker, db, seed):
    key = as_worker.get("/konto/push/schluessel").get_json()["key"]
    assert len(key) == 87 and as_worker.get("/konto/push/schluessel").get_json()["key"] == key   # bleibt gleich
    data = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "BPk", "auth": "au"}}
    r = as_worker.post("/konto/push/abo", json=data,
                       headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36"})
    assert r.get_json()["ok"]
    sub = PushSubscription.query.one()
    assert sub.user == seed["worker"] and sub.device == "Android · Chrome"
    assert as_worker.post("/konto/push/abo", json={"endpoint": "http://unsicher"}).status_code == 400

    html = as_worker.get("/konto").get_data(as_text=True)
    assert "Android · Chrome" in html and 'value="fault"' in html and 'value="purchase_new"' not in html
    assert 'value="frost"' in html and "Wetter-Warnungen" in html
    as_worker.post("/konto/push/themen", data={"topic": ["tasks_new", "tasks_due", "frost"]})
    assert set(seed["worker"].push_off.split(",")) == {"purchase_status", "fault", "maintenance", "day_plan"}
    assert seed["worker"].push_on == "frost"

    as_worker.post("/konto/push/test")
    assert sent_to(seed["worker"])[0]["title"] == "Testnachricht"     # Test kommt unabhängig von den Themen

    # Gerät meldet sich ab; fremde Geräte lassen sich nicht löschen
    other = add_device(db, seed["admin"])
    assert as_worker.post(f"/konto/push/geraet/{other.id}/loeschen").status_code == 404
    as_worker.post("/konto/push/abmelden", json={"endpoint": data["endpoint"]})
    assert PushSubscription.query.filter_by(user_id=seed["worker"].id).count() == 0


def test_service_worker_and_manifest(app, db, seed):
    client = app.test_client()
    r = client.get("/sw.js")
    assert r.status_code == 200 and "showNotification" in r.get_data(as_text=True)
    Setting.put("farm_name", "Gut Lindenhof")
    db.session.commit()
    m = client.get("/manifest.webmanifest").get_json()
    assert m["name"] == "Gut Lindenhof" and m["short_name"] == "Lindenhof"
    assert any(i["sizes"] == "512x512" for i in m["icons"])


def test_task_and_fault(as_admin, as_worker, db, seed):
    admin, worker = seed["admin"], seed["worker"]
    add_device(db, admin)
    add_device(db, worker)
    worker.language = "pl"
    db.session.commit()

    as_admin.post("/aufgaben/neu", data={"title": "Lüfter Lager 2 prüfen", "assignee_ids": [str(worker.id)],
                                         "due_on": "2026-10-24"})
    msgs = sent_to(worker)
    assert len(msgs) == 1 and msgs[0]["title"] == "Nowe zadanie" and "Lüfter Lager 2 prüfen" in msgs[0]["body"]
    assert msgs[0]["url"].startswith("/aufgabe/") and not sent_to(admin)    # nicht an sich selbst

    # abgewählt → nichts
    push.outbox.clear()
    worker.push_off = "tasks_new"
    db.session.commit()
    as_admin.post("/aufgaben/neu", data={"title": "Noch was", "assignee_ids": [str(worker.id)]})
    assert not push.outbox

    # Störung vom Mitarbeiter mit Aufgabe an den Admin: der Admin bekommt nur die Aufgabe, nicht doppelt
    m = machines.create_machine("Fendt 724", "schlepper", admin)
    db.session.commit()
    push.outbox.clear()
    as_worker.post(f"/maschine/{m.id}/eintrag", data={"kind": "stoerung", "text": "Hydraulik leckt\nLinks", "as_task": "1"})
    assert [x["title"] for x in sent_to(admin)] == ["Neue Aufgabe"]
    push.outbox.clear()
    as_worker.post(f"/maschine/{m.id}/eintrag", data={"kind": "stoerung", "text": "Licht defekt"})
    assert sent_to(admin)[0]["title"] == "Störung: Fendt 724 steht" and sent_to(admin)[0]["body"] == "Licht defekt"
    assert not sent_to(worker)


def test_purchases(app, as_admin, as_worker, db, seed):
    admin, worker = seed["admin"], seed["worker"]
    add_device(db, admin)
    add_device(db, worker)
    as_worker.post("/bestellungen/neu", data={"title": "Keilriemen", "urgent": "1"})
    assert sent_to(admin)[0]["title"] == "Dringender Bestellwunsch" and not sent_to(worker)
    req = purchases.open_requests()[0]
    push.outbox.clear()
    as_admin.post(f"/bestellung/{req.id}/bearbeiten", data={"status": "bestellt", "expected_on": "2026-10-28"})
    msg = sent_to(worker)[0]
    assert msg["title"] == "Bestellung: Bestellt" and msg["body"] == "Keilriemen · Lieferung 28.10."
    push.outbox.clear()
    as_admin.post(f"/bestellung/{req.id}/bearbeiten", data={"status": "bestellt", "supplier": "Meyer",
                                                            "expected_on": "2026-10-28"})
    assert not push.outbox                                             # nur bei Statuswechsel

    # Weitergeben an einen anderen Besteller
    anna = make_user("anna", "mitarbeiter")
    anna.can_order = True
    db.session.commit()
    add_device(db, anna)
    as_admin.post(f"/bestellung/{req.id}/uebernehmen", data={"assignee_id": str(anna.id)})
    assert sent_to(anna)[0]["title"] == "admin hat dir eine Bestellung gegeben"


def test_daily(app, db, seed, monkeypatch):
    admin, worker = seed["admin"], seed["worker"]
    add_device(db, admin)
    add_device(db, worker)
    viewer = seed["viewer"]
    add_device(db, viewer)                                       # Betrachter: Wetter-Warnungen nicht eingeschaltet
    worker.push_on = "frost"
    today = date(2026, 10, 23)
    tasks.create_task(admin, title="Kisten zählen", due_on=today - timedelta(days=1), assignees=[worker])
    tasks.create_task(admin, title="Hof fegen", due_on=today, assignees=[worker])
    m = machines.create_machine("Kipper", "anhaenger", admin)
    db.session.add(MaintenanceTask(machine=m, name="TÜV", interval_months=24,
                                   last_done_on=date.today() - timedelta(days=800)))   # überfällig
    db.session.commit()

    from app import weather
    frost_at = datetime(2026, 10, 24, 5, tzinfo=BERLIN)
    fake = {"frost": {"level": "frost", "min": -2.6, "at": frost_at, "from": frost_at - timedelta(hours=3)},
            "blight": {"level": "low"}}
    monkeypatch.setattr(weather, "get_weather", lambda now=None: fake)

    push.daily(app, datetime(2026, 10, 23, 5, 30, tzinfo=BERLIN))
    assert not push.outbox                                       # vor 6 Uhr passiert nichts
    push.daily(app, datetime(2026, 10, 23, 6, 10, tzinfo=BERLIN))
    titles = [m["title"] for m in sent_to(worker)]
    assert "Heute 2 Aufgaben" in titles
    digest = next(m for m in sent_to(worker) if m["title"] == "Heute 2 Aufgaben")
    assert digest["body"] == "Kisten zählen, Hof fegen (1 überfällig)"
    assert [x["body"] for x in sent_to(admin) if x["title"] == "Wartung fällig: Kipper"] == ["TÜV"]
    count = len(push.outbox)
    push.daily(app, datetime(2026, 10, 23, 9, 0, tzinfo=BERLIN))
    assert len(push.outbox) == count                             # heute nicht noch einmal
    push.daily(app, datetime(2026, 10, 23, 16, 5, tzinfo=BERLIN))
    frost = [m for m in push.outbox if m["tag"] == "frost"]
    assert {m["user_id"] for m in frost} == {admin.id, worker.id} and frost[0]["title"] == "Frostwarnung: bis -3 °C"
    assert not sent_to(viewer)

    # nächster Morgen: dieselbe Wartung wird nicht erneut gemeldet
    push.outbox.clear()
    push.daily(app, datetime(2026, 10, 24, 6, 10, tzinfo=BERLIN))
    assert not [m for m in push.outbox if m["tag"] == "maintenance"]

    # Krautfäule springt auf Rot (in der Saison): nur einmal, an alle mit eingeschaltetem Thema
    fake["blight"] = {"level": "high"}
    viewer.push_on = "blight"
    db.session.commit()
    monkeypatch.setattr(weather, "blight_in_season", lambda day: True)
    push.daily(app, datetime(2026, 10, 25, 6, 10, tzinfo=BERLIN))
    blight = [m for m in push.outbox if m["tag"] == "blight"]
    assert {m["user_id"] for m in blight} == {admin.id, viewer.id} and blight[0]["title"] == "Krautfäule-Ampel: Rot"
    push.daily(app, datetime(2026, 10, 26, 6, 10, tzinfo=BERLIN))
    assert len([m for m in push.outbox if m["tag"] == "blight"]) == 2     # am Folgetag rot geblieben: keine neue


def test_expired_subscription_is_removed(app, db, seed, monkeypatch):
    import pywebpush

    class Gone:
        status_code = 410
    sub = add_device(db, seed["worker"])
    keep = add_device(db, seed["admin"])

    def fake_webpush(info, **kwargs):
        if info["endpoint"] == sub.endpoint:
            raise pywebpush.WebPushException("gone", response=Gone())
    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    jobs = [(s.id, {"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}}, "{}", s.user_id)
            for s in (sub, keep)]
    gone_id, keep_id = sub.id, keep.id
    push._send(app, jobs, vapid=None, claims_sub="mailto:x@y")
    db.session.expire_all()
    assert db.session.get(PushSubscription, gone_id) is None
    assert db.session.get(PushSubscription, keep_id).last_sent_at is not None


def test_device_label():
    assert push.device_label("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) Version/17.4 Mobile/15E148 Safari/604.1") == "iPhone · Safari"
    assert push.device_label("Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36 Edg/130.0") == "Windows · Edge"
    assert push.device_label("") is None
