from app import tiles


def test_defaults_and_personal(db, seed):
    admin, worker, viewer = seed["admin"], seed["worker"], seed["viewer"]
    assert tiles.for_user(worker)[:2] == ["tanken", "aufgaben"]              # Mitarbeiter: Tanken vorne
    assert "kalender" in tiles.for_user(admin) and "scan" not in tiles.for_user(viewer)
    assert tiles.quick(["scan", "tanken", "aufgaben", "lager", "kisten", "maschinen"]) == ["tanken", "aufgaben", "lager",
                                                                                             "kisten"]

    # Mitarbeiter nimmt Kalender dazu, blendet Bestellungen aus; Admin-Kacheln lassen sich nicht erschleichen
    tiles.save_user(worker, ["tanken", "aufgaben", "maschinen", "kalender", "verwaltung"])
    assert worker.tiles_add == "kalender" and worker.tiles_hide == "bestellungen"
    assert "kalender" in tiles.for_user(worker) and "bestellungen" not in tiles.for_user(worker)
    assert "verwaltung" not in tiles.for_user(worker)

    # Admin ändert die Voreinstellung → eigene Abweichungen bleiben, neue Voreinstellung kommt an
    tiles.save_role_default("mitarbeiter", ["tanken", "aufgaben", "wetter", "schlaege"])
    mine = tiles.for_user(worker)
    assert "schlaege" in mine and "kalender" in mine and "bestellungen" not in mine and "maschinen" not in mine
    tiles.save_role_default("betrachter", [])                                # bewusst leer
    assert tiles.for_user(viewer) == []
    tiles.reset_user(worker)
    assert tiles.for_user(worker) == ["tanken", "aufgaben", "schlaege", "wetter"]   # Katalog-Reihenfolge


def test_pages(as_admin, as_worker, as_viewer, db, seed):
    html = as_worker.get("/").get_data(as_text=True)
    # Schnellzugriff am Handy: die ersten 4 Kacheln der Auswahl
    assert 'data-tile="tanken"' in html and "?tanken=1" in html and "/startseite/kacheln" in html
    assert 'data-tile="maschinen"' in html and 'data-tile="scan"' not in html                 # Scannen ist unten in der Leiste
    as_worker.post("/startseite/kacheln", data={"tile": ["tanken", "kalender"]})
    html = as_worker.get("/").get_data(as_text=True)
    assert 'data-tile="kalender"' in html and 'data-tile="maschinen"' not in html
    assert "Voreinstellung wiederherstellen" in as_worker.get("/startseite/kacheln").get_data(as_text=True)
    as_worker.post("/startseite/kacheln", data={"reset": "1"})
    assert seed["worker"].tiles_add == "" and seed["worker"].tiles_hide == ""

    assert as_worker.get("/admin/startseite").status_code == 403
    as_admin.post("/admin/startseite", data={"tiles_mitarbeiter": ["tanken", "lieferanten"], "tiles_admin": ["kunden"],
                                             "tiles_betrachter": ["wetter"]})
    html = as_worker.get("/").get_data(as_text=True)
    assert 'data-tile="lieferanten"' in html and 'data-tile="maschinen"' not in html
    assert "Tanken" in as_worker.get("/tankbuch?tanken=1").get_data(as_text=True)
