import io
import json

from PIL import Image

from app import fields
from app.models import FieldNote, Plot, PlotYear

# erfundenes Quadrat (Länge, Breite)
SQUARE = [[[10.000, 50.000], [10.010, 50.000], [10.010, 50.010], [10.000, 50.010], [10.000, 50.000]]]


def jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), (90, 140, 60)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def test_locate(db, seed):
    plot = seed["plot"]
    plot.boundary = json.dumps(SQUARE)
    pointed = Plot(name="Bachwiese", lat=50.020, lon=10.020)
    db.session.add(pointed)
    db.session.commit()
    assert fields.locate(50.005, 10.005) == (plot, 0)                       # im Umriss
    p, d = fields.locate(50.0115, 10.005)                                   # knapp daneben (~170 m)
    assert p == plot and 100 < d < 300
    assert fields.locate(50.0201, 10.0201)[0] == pointed                    # Punkt aus der Schlagdokumentation
    assert fields.locate(51.0, 11.0) == (None, None)


def test_notes_pages(app, as_admin, as_worker, as_viewer, db, seed):
    plot = seed["plot"]
    plot.boundary = json.dumps(SQUARE)
    db.session.commit()
    j = as_worker.get("/schlag/ort?lat=50.005&lon=10.005").get_json()
    assert j["ok"] and j["plot_id"] == plot.id and "notiz=1" in j["url"] and "lat=50.005000" in j["url"]
    assert not as_worker.get("/schlag/ort?lat=51&lon=11").get_json()["ok"]
    assert not as_worker.get("/schlag/ort?lat=abc&lon=11").get_json()["ok"]
    html = as_worker.get(j["url"].split("#")[0]).get_data(as_text=True)
    assert 'class="collapse card-body border-bottom show" id="noteForm"' in html and 'value="50.005000"' in html

    assert as_viewer.post(f"/schlag/{plot.id}/notiz", data={"text": "x"}).status_code == 403
    r = as_worker.post(f"/schlag/{plot.id}/notiz", content_type="multipart/form-data",
                       data={"text": "Krautfäule am Vorgewende", "lat": "50.005", "lon": "10.005",
                             "photos": (jpeg(), "a.jpg")})
    assert r.headers["Location"].endswith(f"/schlag/{plot.id}#notizen")
    note = FieldNote.query.one()
    assert note.plot_year.year == PlotYear.query.one().year and float(note.lat) == 50.005 and len(note.photos) == 1
    html = as_viewer.get(f"/schlag/{plot.id}").get_data(as_text=True)
    assert "Krautfäule am Vorgewende" in html and "photo-thumb" in html and "Notiz löschen" not in html
    # leere Notiz abgewiesen; löschen nur Admin oder Verfasser
    as_worker.post(f"/schlag/{plot.id}/notiz", data={"text": " "})
    assert FieldNote.query.count() == 1
    assert as_viewer.post(f"/notiz/{note.id}/loeschen").status_code == 403
    as_admin.post(f"/notiz/{note.id}/loeschen")
    assert FieldNote.query.count() == 0
    # Schlagkarte: Knopf „Ich bin hier“
    assert 'id="hereBtn"' in as_worker.get("/schlagkarte").get_data(as_text=True)
    assert 'id="hereBtn"' not in as_viewer.get("/schlagkarte").get_data(as_text=True)
