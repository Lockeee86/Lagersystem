import io
import urllib.parse

import pytest

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
    aerial.clear_capabilities()
    calls = []

    def fake(url):
        if "GetCapabilities" in url:
            return b"kein xml", "text/plain"          # Capabilities nicht lesbar → EPSG:4326 versuchen
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
    assert as_worker.get("/luftbild?bbox=10.0,52.3,10.1,52.35&w=800&h=400").status_code == 424   # kein 502 (Cloudflare)
    r = as_admin.get("/luftbild/test")
    assert r.status_code == 200 and "Layer unbekannt" in r.get_data(as_text=True)
    assert as_worker.get("/luftbild/test").status_code == 403
    monkeypatch.setattr(aerial, "_resolve", lambda host, port: ["192.0.2.10"])
    monkeypatch.setattr(aerial, "_connect", lambda host, port: None)
    r = as_admin.get("/luftbild/test?info=1")
    text = r.get_data(as_text=True)
    assert r.status_code == 200 and "DNS opendata.lgln.niedersachsen.de: in Ordnung" in text and "192.0.2.10" in text
    assert "GetCapabilities: nicht lesbar" in text and "Grund: AerialError" in text
    assert "Testbild am Hof-Standort: FEHLER" in text and "Layer unbekannt" in text

    # Server erreicht den Dienst nicht → Diagnose bricht früh ab
    def refused(host, port):
        raise ConnectionRefusedError(111, "Connection refused")
    monkeypatch.setattr(aerial, "_connect", refused)
    text = as_admin.get("/luftbild/test?info=1").get_data(as_text=True)
    assert "Verbindung Port 443: FEHLER" in text and "erreicht den Dienst nicht" in text and "Testbild" not in text

    # Netzfehler beim Bild → Dienst 2 Minuten nicht erneut fragen
    aerial.clear_capabilities()
    tries = []

    def timeout(url):
        tries.append(url)
        raise TimeoutError("timed out")
    monkeypatch.setattr(aerial, "_download", timeout)
    assert as_worker.get("/luftbild?bbox=10.0,52.31,10.1,52.35&w=800&h=400").status_code == 424
    n = len(tries)
    assert as_worker.get("/luftbild?bbox=10.0,52.32,10.1,52.35&w=800&h=400").status_code == 424
    assert len(tries) == n                                      # gesperrt, kein neuer Versuch
    assert "neuer Versuch in" in as_admin.get("/luftbild/test").get_data(as_text=True)
    aerial.clear_capabilities()

    # Aus: keine Bilder, Karte ohne Luftbild-Schalter
    Setting.put("aerial_source", "-")
    db.session.add(Plot(name="Acker Süd", boundary="[[[10.1, 52.3], [10.2, 52.3], [10.2, 52.35], [10.1, 52.3]]]"))
    db.session.commit()
    assert as_worker.get("/luftbild?bbox=10.1,52.3,10.2,52.35&w=800&h=400").status_code == 424
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


CAPS = b"""<?xml version="1.0"?><WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms">
<Capability><Request><GetMap><Format>image/png</Format><Format>image/jpeg</Format></GetMap></Request>
<Layer><Title>DOP</Title><CRS>EPSG:25832</CRS><CRS>EPSG:25833</CRS>
  <Layer><Name>ni_dop20</Name><Title>DOP20</Title></Layer><Layer><Name>ni_dop20_info</Name><CRS>EPSG:4326</CRS></Layer>
</Layer></Capability></WMS_Capabilities>"""


def test_projections():
    # Bezugswerte aus pyproj (EPSG:4326 → 25832/3857), Abweichung < 1 cm
    x, y = aerial._utm(10.5, 52.4, 32)
    assert abs(x - 602052.508) < 0.01 and abs(y - 5806587.366) < 0.01
    x, y = aerial._utm(9.0, 52.0, 32)
    assert abs(x - 500000) < 0.01
    x, y = aerial._mercator(10.5, 52.4)
    assert abs(x - 1168854.653) < 0.01 and abs(y - 6872776.254) < 0.01


def test_capabilities_choose_crs(app, db, seed, monkeypatch, tmp_path):
    app.config["AERIAL_CACHE_DIR"] = str(tmp_path / "cache")
    aerial.clear_capabilities()
    caps = aerial._parse_capabilities(CAPS, "ni_dop20")
    assert caps == {"crs": ["EPSG:25832", "EPSG:25833"], "formats": ["image/png", "image/jpeg"], "found": True,
                    "version": "1.3.0"}
    assert aerial._parse_capabilities(CAPS, "ni_dop20_info")["crs"][-1] == "EPSG:4326"     # geerbt + eigenes
    assert not aerial._parse_capabilities(CAPS, "gibtsnicht")["found"]

    urls = []

    def fake(url):
        urls.append(url)
        if "GetCapabilities" in url:
            return CAPS, "text/xml"
        buf = io.BytesIO()
        Image.new("RGB", (int(url.split("WIDTH=")[1].split("&")[0]), int(url.split("HEIGHT=")[1].split("&")[0])),
                  (60, 120, 40)).save(buf, "PNG")
        return buf.getvalue(), "image/png"
    monkeypatch.setattr(aerial, "_download", fake)
    assert aerial.request_plan(dict(aerial.SOURCES["lgln"], key="lgln")) == ("EPSG:25832", "proj", "image/jpeg", "1.3.0")
    data = aerial.image((10.49, 52.42, 10.51, 52.43), 800, 500)
    getmap = [u for u in urls if "GetMap" in u]
    assert len(getmap) == 1 and "CRS=EPSG%3A25832" in getmap[0] and "FORMAT=image%2Fjpeg" in getmap[0]
    box = [float(v) for v in urllib.parse.unquote(getmap[0].split("BBOX=")[1].split("&")[0]).split(",")]
    assert 600000 < box[0] < box[2] < 603000 and 5806000 < box[1] < box[3] < 5811000     # Rechts-/Hochwert
    with Image.open(io.BytesIO(data)) as img:
        assert img.size == (800, 500) and img.format == "JPEG"                         # entzerrt auf den Ausschnitt
    assert len([u for u in urls if "GetCapabilities" in u]) == 1                      # Capabilities vorgehalten
    Setting.put("aerial_source", "lgln")
    db.session.commit()
    monkeypatch.setattr(aerial, "_resolve", lambda host, port: ["192.0.2.10"])
    monkeypatch.setattr(aerial, "_connect", lambda host, port: None)
    text = aerial.diagnose()
    assert "Layer gefunden: ja" in text and "EPSG:25832 (wird entzerrt), image/jpeg" in text

    # Layer fehlt / kein passendes System → verständliche Meldung
    with pytest.raises(aerial.AerialError, match="gibt es in diesem Dienst nicht"):
        aerial.request_plan({"kind": "wms", "url": "https://x.example/wms", "layer": "gibtsnicht"})
    aerial.clear_capabilities()
    monkeypatch.setattr(aerial, "_download", lambda url: (CAPS.replace(b"EPSG:25832", b"EPSG:31467")
                                                          .replace(b"EPSG:25833", b"EPSG:2398"), "text/xml"))
    with pytest.raises(aerial.AerialError, match="kein passendes Koordinatensystem"):
        aerial.request_plan({"kind": "wms", "url": "https://y.example/wms", "layer": "ni_dop20"})
    aerial.clear_capabilities()


CAPS_111 = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE WMT_MS_Capabilities SYSTEM "http://schemas.opengis.net/wms/1.1.1/WMS_MS_Capabilities.dtd"
 [ <!ELEMENT VendorSpecificCapabilities EMPTY> ]>
<WMT_MS_Capabilities version="1.1.1"><Capability><Request><GetMap><Format>image/jpeg</Format></GetMap></Request>
<Layer><SRS>EPSG:25832 EPSG:4326</SRS><Layer><Name>ni_dop20</Name></Layer></Layer></Capability></WMT_MS_Capabilities>"""


def test_wms_111(app, db, seed, monkeypatch, tmp_path):
    app.config["AERIAL_CACHE_DIR"] = str(tmp_path / "cache")
    aerial.clear_capabilities()
    caps = aerial._parse_capabilities(CAPS_111, "ni_dop20")
    assert caps["version"] == "1.1.1" and caps["crs"] == ["EPSG:25832", "EPSG:4326"] and caps["found"]
    with pytest.raises(aerial.AerialError):
        aerial._parse_capabilities(b'<!DOCTYPE x [<!ENTITY a "b">]><x/>', "x")
    urls = []

    def fake(url):
        urls.append(url)
        return (CAPS_111, "text/xml") if "GetCapabilities" in url else (jpeg(), "image/jpeg")
    monkeypatch.setattr(aerial, "_download", fake)
    source = dict(aerial.SOURCES["lgln"], key="lgln")
    assert aerial.request_plan(source) == ("EPSG:4326", "lonlat", "image/jpeg", "1.1.1")
    aerial.image((10.1, 52.3, 10.2, 52.35), 800, 400)
    getmap = next(u for u in urls if "GetMap" in u)
    assert "VERSION=1.1.1" in getmap and "SRS=EPSG%3A4326" in getmap and "BBOX=10.1%2C52.3%2C10.2%2C52.35" in getmap
    aerial.clear_capabilities()
