import io

from PIL import Image

from app import aerial
from app.models import Plot, Setting


def jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (64, 40), (60, 120, 40)).save(buf, "JPEG")
    return buf.getvalue()


def test_urls():
    lgln = dict(aerial.SOURCES["lgln"], key="lgln")
    url = aerial._url(lgln, (10.1, 52.3, 10.2, 52.35), 800, 400)
    assert "VERSION=1.3.0" in url and "CRS=EPSG%3A4326" in url and "BBOX=52.3%2C10.1%2C52.35%2C10.2" in url
    assert "LAYERS=ni_dop20" in url and "WIDTH=800" in url
    esri = dict(aerial.SOURCES["esri"], key="esri")
    assert "bbox=10.1%2C52.3%2C10.2%2C52.35" in aerial._url(esri, (10.1, 52.3, 10.2, 52.35), 800, 400)
    assert aerial.parse_bbox("10.1,52.3,10.2,52.35") == (10.1, 52.3, 10.2, 52.35)
    assert aerial.parse_bbox("10.2,52.3,10.1,52.35") is None and aerial.parse_bbox("1,2,3") is None
    assert aerial.parse_bbox("0,0,5,5") is None                    # zu großer Ausschnitt


def test_proxy_and_cache(app, as_worker, as_admin, db, seed, monkeypatch, tmp_path):
    app.config["AERIAL_CACHE_DIR"] = str(tmp_path / "cache")
    calls = []

    def fake(url):
        calls.append(url)
        return jpeg(), "image/jpeg"
    monkeypatch.setattr(aerial, "_download", fake)
    r = as_worker.get("/luftbild?bbox=10.1,52.3,10.2,52.35&w=800&h=400")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and "max-age" in r.headers["Cache-Control"]
    as_worker.get("/luftbild?bbox=10.1,52.3,10.2,52.35&w=800&h=400")
    assert len(calls) == 1                                          # zweites Mal aus dem Zwischenspeicher
    assert as_worker.get("/luftbild?bbox=kaputt&w=1&h=1").status_code == 400
    assert app.test_client().get("/luftbild?bbox=10.1,52.3,10.2,52.35&w=800&h=400").status_code == 302   # Login

    # Dienst meldet Fehler als XML → 502, nichts gespeichert
    monkeypatch.setattr(aerial, "_download", lambda url: (b"<ServiceException>Layer unbekannt</ServiceException>" * 3, "text/xml"))
    assert as_worker.get("/luftbild?bbox=10.0,52.3,10.1,52.35&w=800&h=400").status_code == 502
    r = as_admin.get("/luftbild/test")
    assert r.status_code == 502 and "Layer unbekannt" in r.get_data(as_text=True)
    assert as_worker.get("/luftbild/test").status_code == 403

    # Aus: keine Bilder, Karte ohne Luftbild-Schalter
    Setting.put("aerial_source", "-")
    db.session.add(Plot(name="Acker Süd", boundary="[[[10.1, 52.3], [10.2, 52.3], [10.2, 52.35], [10.1, 52.3]]]"))
    db.session.commit()
    assert as_worker.get("/luftbild?bbox=10.1,52.3,10.2,52.35&w=800&h=400").status_code == 502
    assert 'id="aerialToggle"' not in as_worker.get("/schlagkarte").get_data(as_text=True)
    Setting.put("aerial_source", "lgln")
    db.session.commit()
    assert 'id="aerialToggle"' in as_worker.get("/schlagkarte").get_data(as_text=True)


def test_settings(as_admin, db, seed):
    data = {"farm_name": "Hof", "holiday_state": "NI", "blight_from_month": "5", "blight_to_month": "9",
            "aerial_source": "wms", "aerial_wms_url": "https://geo.example/dop?", "aerial_wms_layer": "dop20"}
    as_admin.post("/admin/einstellungen", data=data)
    source = aerial.config()
    assert source["key"] == "wms" and source["layer"] == "dop20"
    assert "https://geo.example/dop?&SERVICE=WMS" in aerial._url(source, (10, 52, 10.1, 52.1), 10, 10)
    as_admin.post("/admin/einstellungen", data=data | {"aerial_wms_url": "javascript:alert(1)"})
    assert aerial.config() is None                                  # ungültige Adresse → aus
