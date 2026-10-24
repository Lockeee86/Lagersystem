import base64
import io
from datetime import date, timedelta

import pytest
from PIL import Image, ImageDraw

from app import push, team
from app.models import Handover, InstructionRecord, Machine, PlanEntry, PushSubscription
from app.storage import StorageError


@pytest.fixture(autouse=True)
def _clear_outbox():
    push.outbox.clear()
    yield
    push.outbox.clear()


def drawing():
    img = Image.new("RGBA", (300, 120), (0, 0, 0, 0))
    ImageDraw.Draw(img).line([(20, 90), (100, 30), (180, 100), (280, 40)], fill=(0, 0, 0, 255), width=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def subscribe(db, user):
    db.session.add(PushSubscription(user_id=user.id, endpoint=f"https://push.example/{user.username}", p256dh="k",
                                    auth="a"))
    db.session.commit()


# --- Übergabe ----------------------------------------------------------------

def test_handover(as_admin, as_worker, as_viewer, db, seed):
    assert as_viewer.post("/uebergabe", data={"text": "x"}).status_code == 403
    subscribe(db, seed["admin"])
    seed["admin"].push_on = "handover"                     # Thema ist standardmäßig aus
    db.session.commit()
    r = as_worker.post("/uebergabe", data={"text": "Lüftung Halle 1 auf Stufe 2\r\nStapler laden",
                                          "back": "/#uebergabe"})
    assert r.headers["Location"].endswith("/#uebergabe")
    note = Handover.query.one()
    assert note.text == "Lüftung Halle 1 auf Stufe 2\nStapler laden" and note.user_id == seed["worker"].id
    assert [m["title"] for m in push.outbox] == ["Übergabe von max"]          # nicht an den Verfasser
    html = as_admin.get("/").get_data(as_text=True)
    assert 'id="uebergabe"' in html and "Stapler laden" in html
    assert "Stapler laden" in as_viewer.get("/").get_data(as_text=True)        # lesen dürfen alle
    as_admin.post(f"/uebergabe/{note.id}/erledigt")
    assert db.session.get(Handover, note.id).done_by_id == seed["admin"].id
    assert "Stapler laden" not in as_admin.get("/").get_data(as_text=True)
    assert "erledigt von admin" in as_admin.get("/uebergabe").get_data(as_text=True)
    with pytest.raises(StorageError):
        team.add_handover(seed["admin"], "  ")


def test_old_handovers_leave_dashboard(db, seed):
    old = team.add_handover(seed["admin"], "alt")
    old.created_at = old.created_at - timedelta(days=team.HANDOVER_DAYS + 1)
    new = team.add_handover(seed["admin"], "neu")
    db.session.commit()
    assert team.open_handovers() == [new]


# --- Tagesplan ---------------------------------------------------------------

def test_plan_logic(db, seed):
    admin, worker, viewer = seed["admin"], seed["worker"], seed["viewer"]
    tractor = Machine(code="M-01", name="Schlepper 1", category="schlepper")
    db.session.add(tractor)
    db.session.flush()
    monday, tuesday = date(2026, 10, 26), date(2026, 10, 27)
    team.add_plan_entry(monday, worker, "Roden", admin, machine_id=tractor.id, plot_id=seed["plot"].id)
    team.add_plan_entry(monday, worker, "Kisten fahren", admin)
    team.add_plan_entry(monday, admin, "Büro", admin)
    with pytest.raises(StorageError, match="Mitarbeiter"):
        team.add_plan_entry(monday, viewer, "x", admin)          # Betrachter stehen nicht im Plan
    with pytest.raises(StorageError):
        team.add_plan_entry(monday, worker, " ", admin)
    plan = team.plan_for(monday)
    assert [e.text for e in plan[worker.id]] == ["Roden", "Kisten fahren"] and plan[worker.id][0].machine == tractor

    # übernehmen: nur für Leute ohne Plan
    team.add_plan_entry(tuesday, admin, "Lieferung", admin)
    assert team.last_planned_day(tuesday) == monday
    assert team.copy_plan(monday, tuesday, admin) == 2
    assert [e.text for e in team.plan_for(tuesday)[admin.id]] == ["Lieferung"]
    with pytest.raises(StorageError):
        team.copy_plan(None, tuesday, admin)


def test_plan_pages_and_push(as_admin, as_worker, as_viewer, db, seed):
    worker = seed["worker"]
    today = date.today()
    assert as_worker.post("/tagesplan/eintrag", data={"datum": today.isoformat(), "user_id": worker.id,
                                                      "text": "x"}).status_code == 403
    r = as_admin.post("/tagesplan/eintrag", data={"datum": today.isoformat(), "user_id": worker.id,
                                                  "text": "Sortieren Halle 2", "plot_id": seed["plot"].id})
    assert r.headers["Location"].endswith(f"/tagesplan?datum={today.isoformat()}#p{worker.id}")
    html = as_worker.get(f"/tagesplan?datum={today.isoformat()}").get_data(as_text=True)
    assert "Sortieren Halle 2" in html and "Acker Nord" in html and 'name="text"' not in html
    assert "Sortieren Halle 2" in as_viewer.get(f"/tagesplan?datum={today.isoformat()}").get_data(as_text=True)
    # Startseite: eigener Plan; andere sehen den Hinweis
    assert "Dein Plan für heute" in as_worker.get("/").get_data(as_text=True)
    assert "Für dich ist heute nichts eingeteilt." in as_admin.get("/").get_data(as_text=True)

    # Admin-Seite mit Formularen, Vorschlägen und „übernehmen“
    tomorrow = today + timedelta(days=1)
    html = as_admin.get(f"/tagesplan?datum={tomorrow.isoformat()}").get_data(as_text=True)
    assert 'name="text"' in html and '<option value="Sortieren Halle 2">' in html and "übernehmen" in html
    as_admin.post("/tagesplan/uebernehmen", data={"datum": tomorrow.isoformat()})
    assert len(team.plan_for(tomorrow)[worker.id]) == 1

    # verschicken: nur an Eingeteilte mit Gerät
    subscribe(db, worker)
    as_admin.post("/tagesplan/verschicken", data={"datum": tomorrow.isoformat()})
    assert [(m["user_id"], m["body"]) for m in push.outbox] == [(worker.id, "Sortieren Halle 2")]
    assert push.outbox[0]["title"] == f"Dein Plan für {tomorrow.strftime('%d.%m.')}"
    assert "verschickt" in as_admin.get(f"/tagesplan?datum={tomorrow.isoformat()}").get_data(as_text=True)

    entry = PlanEntry.query.filter_by(day=tomorrow).one()
    as_admin.post(f"/tagesplan/eintrag/{entry.id}/loeschen")
    assert not team.plan_for(tomorrow)


# --- Unterweisungen ----------------------------------------------------------

def test_instruction_status(db, seed):
    admin, worker = seed["admin"], seed["worker"]
    topic = team.save_topic(None, "Stapler", 12, "Lasten aufnehmen", False, [worker.id, seed["viewer"].id])
    assert team.participants(topic) == [worker]                 # Betrachter zählen nicht
    with pytest.raises(StorageError, match="gibt es schon"):
        team.save_topic(None, "stapler", 12, None, True, [])
    with pytest.raises(StorageError):
        team.save_topic(None, "Neu", 0, None, True, [])
    today = date(2026, 10, 24)
    assert team.status(topic, worker, today=today)["level"] == "never"
    with pytest.raises(StorageError, match="gehört nicht"):
        team.sign_instruction(topic, admin, date(2026, 1, 5), drawing(), admin)
    with pytest.raises(StorageError, match="Zukunft"):
        team.sign_instruction(topic, worker, date.today() + timedelta(days=1), drawing(), admin)
    record = team.sign_instruction(topic, worker, date(2025, 11, 10), drawing(), admin)
    assert record.instructor_name == "admin" and record.signed_name == "max"
    s = team.status(topic, worker, today=today)
    assert s["level"] == "soon" and s["due_on"] == date(2026, 11, 10)
    assert team.status(topic, worker, today=date(2026, 11, 10))["level"] == "due"
    team.sign_instruction(topic, worker, date(2026, 10, 20), drawing(), admin)
    assert team.status(topic, worker, today=today)["level"] == "ok"
    assert [r.day.year for r in team.records_in_year(topic, 2026)] == [2026]
    # für alle: Admin und Mitarbeiter
    general = team.save_topic(None, "Arbeitsschutz", 12, None, True, [])
    assert {u.id for u in team.participants(general)} == {admin.id, worker.id}
    db.session.commit()
    assert team.due_count(today) == 2


def test_instruction_pages(as_admin, as_worker, db, seed):
    assert as_worker.get("/unterweisungen").status_code == 403
    html = as_admin.get("/unterweisungen").get_data(as_text=True)
    assert "Stapler / Teleskoplader" in html and "Vorschläge" in html
    r = as_admin.post("/unterweisungen/neu", data={"vorschlag": "1"})
    topic_url = r.headers["Location"]
    html = as_admin.get(topic_url).get_data(as_text=True)
    assert "Stapler / Teleskoplader" in html and "Lasten aufnehmen" in html and 'id="signModal"' in html
    assert 'name="vorschlag" value="1"' not in as_admin.get("/unterweisungen").get_data(as_text=True)
    # „Jetzt wichtig“ zeigt die Fälligen
    assert "Unterweisungen" in as_admin.get("/").get_data(as_text=True)

    topic_id = int(topic_url.rsplit("/", 1)[1])
    worker = seed["worker"]
    r = as_admin.post(f"/unterweisung/{topic_id}/unterschrift/{worker.id}",
                      data={"bild": drawing(), "datum": date.today().isoformat(), "name": "egal"})
    assert r.headers["Location"].endswith("#personen")
    record = InstructionRecord.query.one()
    assert record.signed_name == "max"                          # Name aus dem Konto, nicht aus dem Formular
    assert as_admin.get(f"/unterweisung/nachweis/{record.id}.png").mimetype == "image/png"
    pdf = as_admin.get(f"/unterweisung/{topic_id}/nachweis.pdf?jahr={date.today().year}")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF" and b"/Image" in pdf.data

    # bearbeiten, nur für Ausgewählte, löschen erst ohne Nachweise
    as_admin.post(topic_url, data={"name": "Stapler", "interval_months": "24", "for_all": "0",
                                   "user_ids": [str(worker.id)], "active": "1"})
    html = as_admin.get(topic_url).get_data(as_text=True)
    assert "alle 24 Monate" in html and "1 Person" in html
    as_admin.post(f"/unterweisung/{topic_id}/loeschen")
    assert db.session.get(type(record.topic), topic_id) is not None
    as_admin.post(f"/unterweisung/nachweis/{record.id}/loeschen")
    as_admin.post(f"/unterweisung/{topic_id}/loeschen")
    assert db.session.get(type(record.topic), topic_id) is None

