from app import machines
from app.models import MaintenanceTask


def test_copy_tasks(as_admin, as_worker, db, seed):
    admin = seed["admin"]
    source = machines.create_machine("BHKW Test 1", "bhkw", admin, counter_unit="h")
    twin = machines.create_machine("BHKW Test 2", "bhkw", admin, counter_unit="h", with_templates=False)
    pump = machines.create_machine("Pumpe Test", "biogas", admin, with_templates=False)       # ohne Zähler
    db.session.commit()
    oil = next(t for t in source.tasks if t.name == "Ölwechsel")
    oil.interval_hours, oil.note = 1500, "laut Ölanalyse"
    twin.tasks.append(MaintenanceTask(name="ölwechsel", interval_hours=2000, last_done_counter=500))
    db.session.commit()

    stats = machines.copy_tasks(source, [twin, pump, source], {oil.id, next(t.id for t in source.tasks
                                                                            if t.name.startswith("Kühlwasser"))})
    db.session.commit()
    assert stats == {"created": 2, "updated": 1, "skipped": 1}
    twin_oil = next(t for t in twin.tasks if t.name.casefold() == "ölwechsel")
    assert (twin_oil.interval_hours, twin_oil.note, twin_oil.last_done_counter) == (1500, "laut Ölanalyse", 500)
    assert [t.name for t in pump.tasks] == ["Kühlwasser / Frostschutz prüfen"]      # Stunden-Wartung übersprungen

    # über die Seite: nur Admin, ohne Auswahl ein Hinweis, „anpassen“ abwählbar
    html = as_admin.get(f"/maschine/{source.id}").get_data(as_text=True)
    assert 'id="copyTasksModal"' in html and "BHKW Test 2" in html
    url = f"/maschine/{source.id}/wartungen/kopieren"
    assert as_worker.post(url, data={"tasks": [oil.id], "targets": [twin.id]}).status_code == 403
    r = as_admin.post(url, data={"tasks": [oil.id]}, follow_redirects=True)
    assert "mindestens eine Maschine" in r.get_data(as_text=True)
    oil.interval_hours = 1000
    db.session.commit()
    r = as_admin.post(url, data={"tasks": [oil.id], "targets": [twin.id]}, follow_redirects=True)
    assert "0 neu, 0 angepasst" in r.get_data(as_text=True) and twin_oil.interval_hours == 1500
    r = as_admin.post(url, data={"tasks": [oil.id], "targets": [twin.id, pump.id], "update": "1"},
                      follow_redirects=True)
    assert "1 Wartung nach Zähler übersprungen" in r.get_data(as_text=True)
    assert db.session.get(MaintenanceTask, twin_oil.id).interval_hours == 1000
