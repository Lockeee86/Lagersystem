from app import tiles


def test_defaults_and_personal(db, seed):
    admin, worker, viewer = seed["admin"], seed["worker"], seed["viewer"]
    assert tiles.for_user(worker)[:2] == ["scan", "tanken"]                  # Mitarbeiter: Tanken vorne
    assert "verwaltung" in tiles.for_user(admin) and "scan" not in tiles.for_user(viewer)

    # Mitarbeiter nimmt Kalender dazu, blendet Wetter aus; Admin-Kacheln lassen sich nicht erschleichen
    tiles.save_user(worker, ["scan", "tanken", "aufgaben", "lager", "kisten", "maschinen", "bestellungen",
                             "kalender", "verwaltung"])
    assert worker.tiles_add == "kalender" and worker.tiles_hide == "wetter"
    assert "kalender" in tiles.for_user(worker) and "wetter" not in tiles.for_user(worker)
    assert "verwaltung" not in tiles.for_user(worker)

    # Admin ändert die Voreinstellung → eigene Abweichungen bleiben, neue Voreinstellung kommt an
    tiles.save_role_default("mitarbeiter", ["tanken", "aufgaben", "wetter", "schlaege"])
    mine = tiles.for_user(worker)
    assert "schlaege" in mine and "kalender" in mine and "wetter" not in mine and "scan" not in mine
    tiles.save_role_default("betrachter", [])                                # bewusst leer
    assert tiles.for_user(viewer) == []
    tiles.reset_user(worker)
    assert tiles.for_user(worker) == ["tanken", "aufgaben", "schlaege", "wetter"]   # Katalog-Reihenfolge


def test_pages(as_admin, as_worker, as_viewer, db, seed):
    html = as_worker.get("/").get_data(as_text=True)
    assert "Tanken" in html and "?tanken=1" in html and "Liter eintragen" in html and "Kacheln wählen" in html
    as_worker.post("/startseite/kacheln", data={"tile": ["tanken", "kalender"]})
    html = as_worker.get("/").get_data(as_text=True)
    assert "Termine in 14 Tagen" in html and "Kiste oder Maschine" not in html
    assert "Voreinstellung wiederherstellen" in as_worker.get("/startseite/kacheln").get_data(as_text=True)
    as_worker.post("/startseite/kacheln", data={"reset": "1"})
    assert seed["worker"].tiles_add == "" and seed["worker"].tiles_hide == ""

    assert as_worker.get("/admin/startseite").status_code == 403
    as_admin.post("/admin/startseite", data={"tiles_mitarbeiter": ["tanken", "lieferanten"], "tiles_admin": ["kunden"],
                                             "tiles_betrachter": ["wetter"]})
    html = as_worker.get("/").get_data(as_text=True)
    assert "Lieferanten" in html and "Kiste oder Maschine" not in html
    assert "Tanken" in as_worker.get("/tankbuch?tanken=1").get_data(as_text=True)
