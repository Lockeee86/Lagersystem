import base64
import io
from datetime import date, datetime, timedelta, timezone

import pytest
from PIL import Image, ImageDraw

from app import machines, push, team
from app.models import Handover, InstructionRecord, Machine, Plot, PushSubscription, Task
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



def _pptx():
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("ppt/presentation.xml", "<p/>")
    buf.seek(0)
    return buf


def test_instruction_documents(app, as_admin, as_worker, db, seed):
    import os
    from app import uploads
    from app.models import InstructionDocument
    topic = team.save_topic(None, "Stapler", 12, None, True, [])
    db.session.commit()
    url = f"/unterweisung/{topic.id}/unterlage"
    assert as_worker.post(url, data={"file": (io.BytesIO(b"%PDF-1.4 x"), "a.pdf")},
                          content_type="multipart/form-data").status_code == 403
    as_admin.post(url, data={"file": (io.BytesIO(b"%PDF-1.4 merkblatt"), "Merkblatt Stapler.pdf")},
                  content_type="multipart/form-data")
    as_admin.post(url, data={"title": "Präsentation BG", "file": (_pptx(), "stapler.pptx")},
                  content_type="multipart/form-data")
    # falsche Dateien: Endung passt nicht zum Inhalt, beliebiges ZIP, Text
    for name, data in (("fake.pptx", io.BytesIO(b"PK\x03\x04kaputt")), ("x.zip", _pptx()),
                       ("notiz.txt", io.BytesIO(b"hallo"))):
        r = as_admin.post(url, data={"file": (data, name)}, content_type="multipart/form-data", follow_redirects=True)
        assert "kein PDF, Bild oder keine Präsentation" in r.get_data(as_text=True)
    pdf, pptx = InstructionDocument.query.order_by(InstructionDocument.id).all()
    assert pdf.title == "Merkblatt Stapler" and pdf.viewable and pptx.extension == "pptx" and not pptx.viewable

    html = as_admin.get(f"/unterweisung/{topic.id}").get_data(as_text=True)
    assert "Merkblatt Stapler" in html and "Präsentation BG" in html and "zum Herunterladen" in html
    r = as_admin.get(f"/unterweisung/unterlage/{pdf.id}")
    assert r.mimetype == "application/pdf" and "attachment" not in r.headers.get("Content-Disposition", "")
    assert "sandbox" in r.headers["Content-Security-Policy"]
    r = as_admin.get(f"/unterweisung/unterlage/{pptx.id}")
    assert "attachment" in r.headers["Content-Disposition"] and "Pr" in r.headers["Content-Disposition"]
    assert as_worker.get(f"/unterweisung/unterlage/{pdf.id}").status_code == 403
    # Unterlagen stehen auf dem Nachweis
    pdf_bytes = as_admin.get(f"/unterweisung/{topic.id}/nachweis.pdf").data
    assert pdf_bytes[:4] == b"%PDF"

    # löschen entfernt die Datei; Thema löschen nimmt die übrigen mit
    path = os.path.join(uploads.document_dir(), pdf.filename)
    assert os.path.exists(path)
    as_admin.post(f"/unterweisung/unterlage/{pdf.id}/loeschen")
    assert not os.path.exists(path)
    other = os.path.join(uploads.document_dir(), pptx.filename)
    as_admin.post(f"/unterweisung/{topic.id}/loeschen")
    assert not os.path.exists(other) and InstructionDocument.query.count() == 0


def test_workshop_screen(app, as_admin, as_worker, db, seed):
    from datetime import date
    from app import screen, tasks
    assert app.test_client().get("/werkstatt/anzeige/falsch").status_code == 404
    assert as_worker.post("/admin/einstellungen/werkstatt-anzeige").status_code == 403
    as_admin.post("/admin/einstellungen/werkstatt-anzeige")
    token = screen.token()
    assert token and f"/werkstatt/anzeige/{token}" in as_admin.get("/admin/einstellungen").get_data(as_text=True)
    tractor = Machine(code="M-21", name="Schlepper 21", category="schlepper")
    db.session.add(tractor)
    db.session.flush()
    machines.add_event(tractor, "stoerung", seed["admin"], text="Licht defekt")
    team.add_plan_entry(date.today(), seed["worker"], "Kartoffeln sortieren", seed["admin"], machine_id=tractor.id)
    tasks.create_task(seed["admin"], title="Werkstatt aufräumen", due_on=date.today())
    team.add_handover(seed["admin"], "Hoftor klemmt")
    motor = Machine(code="M-22", name="Gasmotor 9", category="bhkw", counter_unit="h", counter=1200, power_kw=250)
    db.session.add(motor)
    db.session.commit()
    r = app.test_client().get(f"/werkstatt/anzeige/{token}")           # ohne Anmeldung
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and 'http-equiv="refresh"' in html
    for text in ("Kartoffeln sortieren", "Schlepper 21", "Licht defekt", "Werkstatt aufräumen", "Hoftor klemmt",
                 "Biogasanlage", "Gasmotor 9"):
        assert text in html, text
    as_admin.post("/admin/einstellungen/werkstatt-anzeige")                  # neuer Link: alter ungültig
    assert app.test_client().get(f"/werkstatt/anzeige/{token}").status_code == 404


def test_workshop_screen_price(app, as_admin, db, seed, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import biogas, screen
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
    series = [(start + timedelta(minutes=15 * i), 80.0 + (i % 40) * 5) for i in range(96)]
    monkeypatch.setattr(biogas, "live", lambda now=None, cached_only=False: {"price_series": series, "gas": None,
                                                                              "price_now": series[24][1]})
    as_admin.post("/admin/einstellungen/werkstatt-anzeige")
    html = app.test_client().get(f"/werkstatt/anzeige/{screen.token()}").get_data(as_text=True)
    assert "Strompreis" in html and 'class="price-bar"' in html and "price-mark" in html and "€/MWh" in html


# --- Aufgaben: Tagesplan, Offen, Startseite ------------------------------------

def test_plan_logic(db, seed):
    admin, worker, viewer = seed["admin"], seed["worker"], seed["viewer"]
    tractor = Machine(code="M-01", name="Schlepper 1", category="schlepper")
    db.session.add(tractor)
    db.session.flush()
    monday, tuesday = date(2026, 10, 26), date(2026, 10, 27)
    south = Plot(name="Wiese Süd")
    db.session.add(south)
    db.session.flush()
    team.add_plan_entry(monday, worker, "Roden", admin, machine_id=tractor.id, plot_ids=[south.id, seed["plot"].id])
    team.add_plan_entry(monday, worker, "Kisten fahren", admin)
    team.add_plan_entry(monday, admin, "Büro", admin)
    with pytest.raises(StorageError, match="Mitarbeiter"):
        team.add_plan_entry(monday, viewer, "x", admin)          # Betrachter stehen nicht im Plan
    with pytest.raises(StorageError):
        team.add_plan_entry(monday, worker, " ", admin)
    plan = team.plan_for(monday, today=tuesday)
    assert [e.title for e in plan[worker.id]] == ["Roden", "Kisten fahren"] and plan[worker.id][0].machine == tractor
    assert [p.name for p in plan[worker.id][0].plots] == ["Acker Nord", "Wiese Süd"]
    assert Task.query.count() == 3                               # ein Tagesplan-Eintrag ist eine Aufgabe

    # übernehmen: Erledigtes als neue Aufgaben, nur für Leute ohne Plan; Unerledigtes wandert von selbst mit
    for e in plan[worker.id] + plan[admin.id]:
        e.done_at = e.created_at
    team.add_plan_entry(monday, worker, "Liegt noch", admin)
    team.add_plan_entry(tuesday, admin, "Lieferung", admin)
    assert team.last_planned_day(tuesday) == monday
    assert team.copy_plan(monday, tuesday, admin) == 2
    assert [p.name for p in team.plan_for(tuesday, exact=True)[worker.id][0].plots] == ["Acker Nord", "Wiese Süd"]
    assert [e.title for e in team.plan_for(tuesday, exact=True)[admin.id]] == ["Lieferung"]
    with pytest.raises(StorageError):
        team.copy_plan(None, tuesday, admin)


def test_left_over_stays_and_open_list(db, seed):
    """Liegengebliebenes steht heute weiter bei der Person; „Offen“ = nicht eingeplant (ohne Hühner, bis 7 Tage)."""
    from app import tasks
    admin, worker = seed["admin"], seed["worker"]
    today = date.today()
    old = team.add_plan_entry(today - timedelta(days=2), worker, "Zaun flicken", admin)
    done = team.add_plan_entry(today - timedelta(days=1), worker, "Hof fegen", admin)
    done.done_at = done.created_at
    note = team.add_open_task("Dachrinne säubern", worker)
    urgent = tasks.create_task(admin, title="Tor ölen", due_on=today - timedelta(days=1), important=True)
    tasks.create_task(admin, title="Später", due_on=today + timedelta(days=20))
    tasks.create_task(admin, title="Eier zählen").area = "huehner"
    mine = tasks.create_task(admin, title="Für Max", assignees=[worker])
    db.session.commit()
    assert [t.title for t in team.plan_for(today)[worker.id]] == ["Zaun flicken"]
    assert worker.id not in team.plan_for(today, exact=True)
    assert [t.title for t in team.open_tasks(today, admin)] == ["Tor ölen", "Dachrinne säubern", "Für Max"]
    assert [t.title for t in team.open_tasks(today, worker)] == ["Tor ölen", "Dachrinne säubern"]   # nur für alle
    # „Meine Aufgaben heute“: Liegengebliebenes und mir Zugeteiltes, das fällig ist
    mine.due_on = today
    db.session.commit()
    assert [t.title for t in team.my_today(worker)] == ["Zaun flicken", "Für Max"]
    # einplanen: Mitarbeiter nur für sich, nicht was anderen gehört
    team.plan_task(note, today, worker, worker)
    assert note.plan_user == worker and note.plan_day == today
    with pytest.raises(StorageError):
        team.plan_task(urgent, today, admin, worker)
    team.unplan_task(note)
    assert note.plan_day is None and note.assignees == []
    assert old.id in {t.id for t in team.my_today(worker)}


def test_plan_pages_and_push(as_admin, as_worker, as_viewer, db, seed):
    worker = seed["worker"]
    today = date.today()
    assert as_worker.post("/tagesplan/eintrag", data={"datum": today.isoformat(), "user_id": worker.id,
                                                      "text": "x"}).status_code == 403
    r = as_admin.post("/tagesplan/eintrag", data={"datum": today.isoformat(), "user_id": worker.id,
                                                  "text": "Sortieren Halle 2", "plot_ids": [seed["plot"].id]})
    assert r.headers["Location"].endswith(f"/tagesplan?datum={today.isoformat()}#p{worker.id}")
    html = as_worker.get(f"/tagesplan?datum={today.isoformat()}").get_data(as_text=True)
    assert "Sortieren Halle 2" in html and "Acker Nord" in html and 'action="/tagesplan/eintrag"' not in html
    assert "Heute" in html and "Offen" in html and "Alle" in html                       # Reiter
    assert "Sortieren Halle 2" in as_viewer.get(f"/tagesplan?datum={today.isoformat()}").get_data(as_text=True)
    # Startseite: „Meine Aufgaben heute“
    html = as_worker.get("/").get_data(as_text=True)
    assert "Meine Aufgaben heute" in html and "Sortieren Halle 2" in html
    assert "Für dich ist heute nichts eingeteilt." in as_admin.get("/").get_data(as_text=True)

    tomorrow = today + timedelta(days=1)
    html = as_admin.get(f"/tagesplan?datum={tomorrow.isoformat()}").get_data(as_text=True)
    assert 'action="/tagesplan/eintrag"' in html and '<option value="Sortieren Halle 2">' in html and "übernehmen" in html
    Task.query.filter_by(title="Sortieren Halle 2").one().done_at = datetime.now(timezone.utc)
    db.session.commit()
    as_admin.post("/tagesplan/uebernehmen", data={"datum": tomorrow.isoformat()})
    assert len(team.plan_for(tomorrow)[worker.id]) == 1

    subscribe(db, worker)
    as_admin.post("/tagesplan/verschicken", data={"datum": tomorrow.isoformat()})
    assert [(m["user_id"], m["body"]) for m in push.outbox] == [(worker.id, "Sortieren Halle 2")]
    assert push.outbox[0]["title"] == f"Dein Plan für {tomorrow.strftime('%d.%m.')}"
    assert "verschickt" in as_admin.get(f"/tagesplan?datum={tomorrow.isoformat()}").get_data(as_text=True)

    task = Task.query.filter_by(plan_day=tomorrow).one()
    as_admin.post(f"/aufgabe/{task.id}/zurueck")                                         # zurück nach „Offen“
    assert not team.plan_for(tomorrow, exact=True) and task.plan_day is None
    assert "Sortieren Halle 2" in as_admin.get("/aufgaben/offen").get_data(as_text=True)


def test_plan_check(as_admin, as_worker, as_viewer, db, seed):
    """Eingeteilte Person (oder Planer) hakt ab; wieder öffnen geht."""
    worker, admin = seed["worker"], seed["admin"]
    today = date.today()
    sort = team.add_plan_entry(today, worker, "Sortieren Halle 2", admin)
    own = team.add_plan_entry(today, admin, "Büro", admin)
    db.session.commit()
    assert as_viewer.post(f"/tagesplan/eintrag/{sort.id}/erledigt").status_code == 403
    as_worker.post(f"/tagesplan/eintrag/{own.id}/erledigt")                  # nicht seiner
    assert own.done_at is None
    r = as_worker.post(f"/tagesplan/eintrag/{sort.id}/erledigt", data={"back": "/#meinplan"})
    assert r.headers["Location"].endswith("/#meinplan") and sort.done_at and sort.done_by_id == worker.id
    html = as_worker.get(f"/tagesplan?datum={today.isoformat()}").get_data(as_text=True)
    assert "is-done" in html and "Wieder öffnen" in html
    as_worker.post(f"/tagesplan/eintrag/{sort.id}/erledigt")                 # wieder öffnen
    assert sort.done_at is None
    as_admin.post(f"/tagesplan/eintrag/{sort.id}/erledigt")                  # Admin hakt für ihn ab
    assert sort.done_by_id == admin.id


def test_plan_editor(as_admin, as_worker, db, seed):
    """Admin erlaubt einem Mitarbeiter in der Benutzerverwaltung, den Tagesplan zu pflegen."""
    worker, admin = seed["worker"], seed["admin"]
    today = date.today()
    data = {"datum": today.isoformat(), "user_id": admin.id, "text": "Büro aufräumen"}
    assert as_worker.post("/tagesplan/eintrag", data=data).status_code == 403
    as_admin.post("/admin/benutzer", data={"id": worker.id, "username": "max", "role": "mitarbeiter", "active": "1",
                                           "can_plan": "1"})
    assert worker.can_plan and worker.is_planner
    as_worker.post("/tagesplan/eintrag", data=data)
    task = Task.query.filter_by(title="Büro aufräumen").one()
    html = as_worker.get(f"/tagesplan?datum={today.isoformat()}").get_data(as_text=True)
    assert 'action="/tagesplan/eintrag"' in html and "Plan aufs Handy schicken" in html
    as_worker.post(f"/tagesplan/eintrag/{task.id}/erledigt")               # darf auch für andere abhaken
    assert task.done_by_id == worker.id
    worker.role = "betrachter"                                               # nur zusammen mit Bearbeiten-Recht
    db.session.commit()
    assert not worker.is_planner


def test_open_notes_and_claim(app, as_admin, as_worker, as_viewer, db, seed):
    admin, worker = seed["admin"], seed["worker"]
    today = date.today()
    day = {"datum": today.isoformat()}
    as_admin.post("/aufgaben/notieren", data=day | {"text": "Hoftor ölen", "important": "1"})
    as_worker.post("/aufgaben/notieren", data=day | {"text": "Werkzeugwand aufräumen"})
    assert as_viewer.post("/aufgaben/notieren", data=day | {"text": "x"}).status_code == 403
    gate = Task.query.filter_by(title="Hoftor ölen").one()
    wall = Task.query.filter_by(title="Werkzeugwand aufräumen").one()
    assert [t.title for t in team.open_tasks(today, worker)] == ["Hoftor ölen", "Werkzeugwand aufräumen"]
    html = as_worker.get("/aufgaben/offen").get_data(as_text=True)
    assert "Hoftor ölen" in html and "Mach ich" in html
    assert "Offen für alle" in as_worker.get("/").get_data(as_text=True)

    # Mitarbeiter nimmt sich selbst etwas – für andere einplanen darf er nicht
    as_worker.post(f"/aufgabe/{gate.id}/einplanen", data=day | {"user_id": worker.id})
    assert [t.title for t in team.plan_for(today)[worker.id]] == ["Hoftor ölen"]
    as_worker.post(f"/aufgabe/{wall.id}/einplanen", data=day | {"user_id": admin.id})
    assert admin.id not in team.plan_for(today)
    as_admin.post(f"/aufgabe/{wall.id}/einplanen", data=day | {"user_id": worker.id})
    assert team.open_tasks(today, admin) == []
    # abgehakt = erledigt, auch unter „Alle“
    as_worker.post(f"/tagesplan/eintrag/{gate.id}/erledigt")
    assert gate.is_done
    # „hat sich erledigt“ / löschen: nur Planer oder wer es notiert hat
    extra = team.add_open_task("Kaffee kaufen", admin)
    db.session.commit()
    assert as_worker.post(f"/aufgabe/{extra.id}/weg").status_code == 403
    as_admin.post(f"/aufgabe/{extra.id}/hat-sich-erledigt")
    assert extra.is_done
    other = team.add_open_task("Doppelt notiert", worker)
    db.session.commit()
    as_worker.post(f"/aufgabe/{other.id}/weg")
    assert db.session.get(Task, other.id) is None


def test_plan_suggestions(as_admin, as_worker, db, seed):
    from app.models import MachineEvent, MaintenanceTask
    admin, worker = seed["admin"], seed["worker"]
    today = date.today()
    tractor = Machine(code="M-07", name="Schlepper 7", category="schlepper", counter_unit="h", counter=1000)
    db.session.add(tractor)
    db.session.flush()
    oil = MaintenanceTask(machine=tractor, name="Ölwechsel", interval_months=12,
                          last_done_on=today - timedelta(days=400))
    later = MaintenanceTask(machine=tractor, name="Bremsen prüfen", interval_months=12,
                            last_done_on=today - timedelta(days=200))      # erst in Monaten fällig
    db.session.add_all([oil, later])
    fault = machines.add_event(tractor, "stoerung", admin, text="Hydraulik leckt")
    db.session.commit()
    keys = [s["key"] for s in team.suggestions(today, admin)]
    assert keys == [f"stoerung-{fault.id}", f"wartung-{oil.id}"]
    html = as_admin.get(f"/tagesplan?datum={today.isoformat()}").get_data(as_text=True)
    assert "Hydraulik leckt" in html and "Ölwechsel" in html
    assert "Hydraulik leckt" not in as_worker.get("/aufgaben/offen").get_data(as_text=True)    # nur für Planer
    for key in keys:
        as_admin.post("/tagesplan/vorschlag", data={"datum": today.isoformat(), "user_id": worker.id, "key": key})
    assert team.suggestions(today, admin) == []
    entries = team.plan_for(today)[worker.id]
    assert len(entries) == 2 and all(e.machine == tractor for e in entries)
    by_kind = {("wartung" if e.maintenance_task_id else "stoerung"): e for e in entries}
    # abhaken erledigt die Störung mit, wieder öffnen nimmt es zurück; die Wartung wird an der Maschine eingetragen
    as_worker.post(f"/tagesplan/eintrag/{by_kind['stoerung'].id}/erledigt")
    assert db.session.get(MachineEvent, fault.id).resolved_at is not None
    as_worker.post(f"/tagesplan/eintrag/{by_kind['stoerung'].id}/erledigt")
    assert db.session.get(MachineEvent, fault.id).resolved_at is None
    as_worker.post(f"/tagesplan/eintrag/{by_kind['wartung'].id}/erledigt")
    assert oil.last_done_on == today

