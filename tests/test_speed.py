"""Schneller laden: Versionierte statische Dateien lange zwischenspeichern, Text mit gzip packen."""
import gzip


def test_static_cache_and_gzip(app, as_admin):
    html = as_admin.get("/").get_data(as_text=True)
    assert "/static/vendor/bootstrap/bootstrap.min.css?v=" in html          # Version hängt automatisch an
    url = html.split('href="/static/vendor/bootstrap/bootstrap.min.css')[1].split('"')[0]
    r = app.test_client().get("/static/vendor/bootstrap/bootstrap.min.css" + url, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and "immutable" in r.headers["Cache-Control"]
    assert r.headers["Content-Encoding"] == "gzip" and gzip.decompress(r.data).startswith(b"@charset")
    again = app.test_client().get("/static/vendor/bootstrap/bootstrap.min.css" + url, headers={"Accept-Encoding": "gzip"})
    assert again.data == r.data                                              # aus dem Speicher
    plain = app.test_client().get("/static/js/app.js")                       # ohne Version: ein Tag, ungepackt
    assert "max-age=86400" in plain.headers["Cache-Control"] and "Content-Encoding" not in plain.headers
    page = as_admin.get("/", headers={"Accept-Encoding": "gzip"})
    assert page.headers["Content-Encoding"] == "gzip" and b"<!DOCTYPE" in gzip.decompress(page.data)
    assert "Accept-Encoding" in page.headers["Vary"]
