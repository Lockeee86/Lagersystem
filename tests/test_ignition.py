"""Zündung je Zylinder: Wechsel, Tausch zwischen Zylindern, Laufzeit, Fälligkeit, Seiten."""
from datetime import date

import pytest

from app import ignition, machines
from app.storage import StorageError


@pytest.fixture
def motor(db, seed):
    m = machines.create_machine("Motor Test", "bhkw", seed["admin"], counter_unit="h", power_kw=250)
    machines.set_counter(m, 10000, seed["admin"])
    ignition.save_settings(m, 12, 2, {"kerze": 2000, "stecker": 6000, "spule": None})
    db.session.commit()
    return m


def cell(machine, cylinder, kind):
    rows = [r for bank in ignition.grid(machine) for r in bank["rows"]]
    return rows[cylinder - 1]["cells"][kind]


def test_change_swap_and_life(db, seed, motor):
    admin = seed["admin"]
    assert ignition.label(motor, 1) == "A1" and ignition.label(motor, 7) == "B1" and len(ignition.grid(motor)) == 2
    assert cell(motor, 3, "kerze") is None                                   # noch nichts erfasst
    # Stand bei der Erfassung: Kerzen auf allen Zylindern bei 9000 h eingebaut, Stecker auf 3 und 9
    ignition.change(motor, admin, range(1, 13), ["kerze"], date(2026, 1, 10), 9000)
    ignition.change(motor, admin, [3, 9], ["stecker"], date(2026, 1, 10), 9000)
    db.session.commit()
    assert cell(motor, 3, "kerze")["hours"] == 1000 and cell(motor, 3, "kerze")["state"] == "ok"
    assert motor.counter == 10000                                            # älterer Stand senkt den Zähler nicht
    # Kerze auf Zylinder 3 fällt aus → neues Teil, altes lief 1000 h; Ausfall zählt am Zylinder
    events = ignition.change(motor, admin, [3], ["kerze"], reason="ausfall", note="Elektrode abgebrannt")
    db.session.commit()
    assert events[0].life_hours == 1000 and cell(motor, 3, "kerze")["hours"] == 0
    assert [r for b in ignition.grid(motor) for r in b["rows"]][2]["failures"] == 1
    # Tausch: neue Kerze von 3 mit der alten von 9 – die Laufzeit wandert mit dem Teil
    ignition.swap(motor, admin, ["kerze"], 3, 9)
    db.session.commit()
    assert cell(motor, 3, "kerze")["hours"] == 1000 and cell(motor, 9, "kerze")["hours"] == 0
    # Tausch mit einem leeren Platz: Stecker von 3 nach 4 (dort war keiner erfasst)
    ignition.swap(motor, admin, ["stecker"], 3, 4)
    db.session.commit()
    assert cell(motor, 3, "stecker") is None and cell(motor, 4, "stecker")["hours"] == 1000
    # Zähler läuft weiter → bald fällig (90 %) und überfällig
    machines.set_counter(motor, 10850, admin)
    db.session.commit()
    assert cell(motor, 1, "kerze")["state"] == "soon" and cell(motor, 9, "kerze")["state"] == "ok"
    machines.set_counter(motor, 11100, admin)
    db.session.commit()
    due = ignition.due(motor)
    assert due[0][5] == "overdue" and due[0][4] == -100 and ignition.summary(motor)["overdue"] == 11
    # Logbuch: je Aktion ein Eintrag; Auswertung der ausgebauten Teile
    texts = [e.text for e in motor.events.filter_by(kind="wartung")]
    assert any("Zündkerze: Zyl. A3 (Ausfall) – Elektrode abgebrannt" in t for t in texts)
    assert any("Zyl. A3 ↔ B3 getauscht" in t for t in texts)
    stat = ignition.stats(motor)[0]
    assert stat["count"] == 1 and stat["avg"] == 1000 and stat["failures"] == 1


def test_errors_and_settings(db, seed, motor):
    admin = seed["admin"]
    with pytest.raises(StorageError):
        ignition.change(motor, admin, [13], ["kerze"])                        # Zylinder gibt es nicht
    with pytest.raises(StorageError):
        ignition.change(motor, admin, [1], [])                                # nichts gewählt
    with pytest.raises(StorageError):
        ignition.swap(motor, admin, ["kerze"], 2, 2)
    with pytest.raises(StorageError):
        ignition.swap(motor, admin, ["kerze"], 1, 2)                          # auf beiden nichts erfasst
    ignition.change(motor, admin, [12], ["kerze"], counter=9500)
    with pytest.raises(StorageError):
        ignition.change(motor, admin, [12], ["kerze"], counter=9000)          # vor dem Einbau des jetzigen Teils
    with pytest.raises(StorageError):
        ignition.save_settings(motor, 8, 2, {})                               # auf Zylinder 12 sitzt ein Teil
    ignition.save_settings(motor, 12, 1, {"kerze": 1500})
    assert motor.cylinder_banks is None and motor.ign_plug_hours == 1500 and motor.ign_cap_hours is None
    assert ignition.label(motor, 7) == "7" and len(ignition.grid(motor)) == 1


def test_pages(app, as_admin, as_worker, as_viewer, db, seed, motor):
    url = f"/maschine/{motor.id}"
    html = as_admin.get(url).get_data(as_text=True)
    assert "Zündung je Zylinder" in html and "Bank A" in html and 'name="cylinders"' in html
    r = as_worker.post(f"{url}/zuendung/wechsel", data={"zyl": ["1", "2"], "teil": ["kerze", "spule"], "counter": "10000",
                                                        "grund": "plan"}, follow_redirects=True)
    assert "4 Teile gewechselt." in r.get_data(as_text=True)
    r = as_worker.post(f"{url}/zuendung/tausch", data={"teil": ["kerze"], "von": "1", "nach": "7", "counter": "10000"},
                       follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Tausch eingetragen." in html and "Zyl. A1 ↔ B1 getauscht" in html
    r = as_worker.post(f"{url}/zuendung/wechsel", data={"teil": ["kerze"], "counter": "10000"}, follow_redirects=True)
    assert "Bitte mindestens einen Zylinder wählen." in r.get_data(as_text=True)
    assert as_viewer.post(f"{url}/zuendung/wechsel", data={"zyl": "1", "teil": "kerze", "counter": "1"}).status_code in (302, 403)
    assert motor.ignition_events.count() == 5
    # Grunddaten über das Formular; ohne Zylinderzahl verschwindet der Reiter
    as_admin.post(f"{url}/bearbeiten", data={"name": motor.name, "category": "bhkw", "counter_unit": "h", "bhkw_form": "1",
                                              "cylinders": "12", "cylinder_banks": "1", "ign_plug_hours": "1800"})
    db.session.refresh(motor)
    assert motor.ign_plug_hours == 1800 and motor.cylinder_banks is None
    other = machines.create_machine("Motor ohne", "bhkw", seed["admin"], counter_unit="h")
    db.session.commit()
    assert "Zündung je Zylinder" not in as_admin.get(f"/maschine/{other.id}").get_data(as_text=True)


def test_due_parts_show_up_like_maintenance(app, as_admin, db, seed, motor):
    """Fällige Zündungsteile: Maschinenliste, Ampel, Startseite, Werkstatt-Bildschirm, Push, Wartungsübersicht."""
    from app import biogas, push, screen, team
    admin = seed["admin"]
    ignition.change(motor, admin, [3, 7], ["kerze"], counter=7900)            # 2100 h alt → überfällig (Soll 2000)
    ignition.change(motor, admin, [1], ["kerze"], counter=8150)               # 1850 h → bald fällig
    ignition.change(motor, admin, [2], ["kerze", "stecker"], counter=9900)    # frisch
    db.session.commit()
    rows = [(t, s) for t, s in machines.due_tasks() if getattr(t, "is_ignition", False)]
    assert len(rows) == 1                                                     # ein Eintrag je Motor und Teileart
    task, status = rows[0]
    assert task.name == "Zündkerze Zyl. A1, A3, B1" and status["state"] == "overdue" and status["left_counter"] == -100
    assert machines.machine_summary(motor)[0] == "overdue"
    assert not [r for r in team.suggestions(admin) if r["machine"].id == motor.id]   # keine verknüpfte Wartungs-Aufgabe
    forecast = biogas.maintenance_forecast(motor)
    assert any(getattr(f["task"], "is_ignition", False) and f["left"] == -100 for f in forecast)
    html = as_admin.get("/maschinen").get_data(as_text=True)
    assert "Zündkerze Zyl. A1, A3, B1" in html and f"/maschine/{motor.id}#zuendung" in html
    assert "Zündkerze Zyl. A1, A3, B1" in as_admin.get("/?alle=1").get_data(as_text=True)
    screen.new_token()
    db.session.commit()
    assert "Zündkerze Zyl. A1, A3, B1" in as_admin.get(f"/werkstatt/anzeige/{screen.token()}").get_data(as_text=True)
    assert "Zündkerze Zyl. A1, A3, B1" in as_admin.get("/biogas/wartung").get_data(as_text=True)
    # Push: einmal melden, am nächsten Morgen nicht noch einmal
    from app.models import Notice
    with app.test_request_context():
        push._maintenance(date.today())
        first = Notice.query.filter(Notice.body.contains("Zündkerze")).count()
        assert first
        push._maintenance(date.today())
        assert Notice.query.filter(Notice.body.contains("Zündkerze")).count() == first
    # nach dem Wechsel ist der Eintrag weg
    ignition.change(motor, admin, [1, 3, 7], ["kerze"])
    db.session.commit()
    assert not [t for t, _s in machines.due_tasks() if getattr(t, "is_ignition", False)]
    assert machines.machine_summary(motor)[0] != "overdue"
