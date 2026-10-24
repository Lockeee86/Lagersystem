from datetime import datetime, timedelta, timezone

from app import audit, storage
from app.models import AuditEntry, Plot, Setting, Variety


def entries(table=None):
    query = AuditEntry.query.order_by(AuditEntry.id)
    return query.filter_by(table=table).all() if table else query.all()


def test_changes_and_deletes_are_logged(app, as_admin, as_worker, as_viewer, db, seed):
    plot = seed["plot"]
    # Änderung über die Oberfläche: Benutzer, vorher/nachher
    as_admin.post("/admin/schlaege", data={"id": plot.id, "name": "Acker Nord", "area_ha": "12,5", "active": "1",
                                           "note": "Pacht"})
    (e,) = entries("plots")
    assert e.action == "geaendert" and e.user_id == seed["admin"].id and e.label == "Acker Nord"
    assert e.changes["area_ha"] == [None, "12.5"] and e.changes["note"] == [None, "Pacht"]
    # nur der Zeilenumbruch anders (Formular: \r\n) → keine Änderung
    plot.note = "Zeile 1\nZeile 2"
    db.session.commit()
    plot.note = "Zeile 1\r\nZeile 2"
    db.session.commit()
    assert len(entries("plots")) == 2

    # Löschen: Momentaufnahme der Werte
    v = Variety(name="Testsorte Gelb", color="#eeee00")
    db.session.add(v)
    db.session.commit()
    as_admin.post(f"/admin/sorten/{v.id}/loeschen")
    deleted = [x for x in entries("varieties") if x.action == "geloescht"]
    assert deleted and deleted[0].label == "Testsorte Gelb" and deleted[0].changes["color"] == "#eeee00"

    # Passwort nur als „geändert“, ohne Hash; persönliche Einstellungen gar nicht
    seed["worker"].set_password("neuesPasswort123")
    seed["worker"].tiles_add = "wetter"
    db.session.commit()
    (u,) = entries("users")
    assert u.changes == {"password_hash": ["…", "…"]} and u.user_id is None        # ohne Anmeldung: System

    # Kisten: Inhalt und Platz stehen im Verlauf, nicht im Protokoll
    (box,) = storage.create_boxes(1, seed["bt"].id, seed["admin"])
    db.session.commit()
    storage.update_contents(box, seed["admin"], seed["belana"].id, None, None, None, 100, None)
    storage.store_box(box, seed["wh"].id, 1, 1, seed["admin"])
    db.session.commit()
    assert not entries("boxes") and not entries("movements")

    # interne Werte (Push-Zeitpunkte) nicht, echte Einstellungen schon
    Setting.put("push_job_morning", "2026-10-23")
    Setting.put("farm_name", "Gut Musterhof")
    db.session.commit()
    assert not entries("settings")                                   # Neuanlage zählt nicht
    Setting.put("push_job_morning", "2026-10-24")
    Setting.put("farm_name", "Gut Beispielhof")
    db.session.commit()
    (s,) = entries("settings")
    assert s.label == "farm_name" and s.changes == {"value": ["Gut Musterhof", "Gut Beispielhof"]}

    # Seite: nur Admin, mit Filter und lesbaren Werten
    assert as_viewer.get("/admin/protokoll").status_code == 403
    html = as_admin.get("/admin/protokoll").get_data(as_text=True)
    assert "Acker Nord" in html and "Fläche" in html and "12.5" in html and "Testsorte Gelb" in html
    html = as_admin.get("/admin/protokoll?aktion=geloescht").get_data(as_text=True)
    assert "Testsorte Gelb" in html and "Pacht" not in html
    assert "Acker Nord" in as_admin.get("/admin/protokoll?q=nord").get_data(as_text=True)


def test_paused_import_and_cleanup(app, db, seed):
    with audit.paused():
        seed["plot"].note = "aus dem Import"
        db.session.flush()
    audit.note("plots", "schlaege.kml", {"created": 0, "updated": 1})
    db.session.commit()
    assert [(e.action, e.label) for e in entries("plots")] == [("import", "schlaege.kml")]
    # nach der Pause wird wieder protokolliert
    seed["plot"].note = "von Hand"
    db.session.commit()
    assert entries("plots")[-1].changes == {"note": ["aus dem Import", "von Hand"]}

    old = AuditEntry(action="geaendert", table="plots", label="alt",
                     created_at=datetime.now(timezone.utc) - timedelta(days=audit.KEEP_DAYS + 5))
    db.session.add(old)
    db.session.commit()
    assert audit.cleanup(app) == 1 and audit.cleanup(app) == 0                     # einmal am Tag
    assert not AuditEntry.query.filter_by(label="alt").count()
    assert Plot.query.count() == 1
