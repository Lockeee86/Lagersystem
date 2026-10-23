import io

from PIL import Image

from app.models import Setting


def png(size=(800, 300), color=(200, 30, 30, 255)):
    """Breites Logo mit durchsichtigem Rand – soll ohne Abschneiden quadratisch werden."""
    img = Image.new("RGBA", (size[0] + 100, size[1] + 100), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", size, color), (50, 50))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


def test_default_logo(app, as_admin, db, seed):
    html = as_admin.get("/").get_data(as_text=True)
    assert "/static/img/icon.svg?v=" in html and "/static/img/icon-180.png?v=" in html
    r = app.test_client().get("/logo/icon-192.png")
    assert r.status_code == 302 and r.headers["Location"].endswith("/static/img/icon-192.png")
    assert app.test_client().get("/logo/irgendwas.png").status_code == 404


def test_upload_and_reset(app, as_admin, as_worker, db, seed, tmp_path):
    assert as_worker.post("/admin/logo", data={"logo": (png(), "logo.png")},
                          content_type="multipart/form-data").status_code == 403
    as_admin.post("/admin/logo", data={"logo": (png(), "logo.png")}, content_type="multipart/form-data")
    version = db.session.get(Setting, "logo_version").value
    assert version

    html = as_admin.get("/").get_data(as_text=True)
    assert f"/logo/logo-64.png?v={version}" in html and f"/logo/icon-180.png?v={version}" in html
    login = app.test_client().get("/login").get_data(as_text=True)
    assert f"/logo/logo-512.png?v={version}" in login
    manifest = app.test_client().get("/manifest.webmanifest").get_json()
    assert manifest["icons"][0]["src"] == f"/logo/icon-192.png?v={version}"

    # Größen: quadratisch, nichts abgeschnitten (Rand weg, Breite füllt aus), App-Icon mit weißem Grund
    logo = Image.open(io.BytesIO(app.test_client().get("/logo/logo-512.png").data))
    assert logo.size == (512, 512) and logo.mode == "RGBA"
    assert logo.getpixel((2, 256))[3] > 0 and logo.getpixel((256, 10))[3] == 0
    icon = Image.open(io.BytesIO(app.test_client().get("/logo/icon-192.png").data))
    assert icon.size == (192, 192) and icon.getpixel((2, 2)) == (255, 255, 255)

    # Kein Bild / zu klein → Fehlermeldung, altes Logo bleibt
    as_admin.post("/admin/logo", data={"logo": (io.BytesIO(b"kein bild"), "x.png")}, content_type="multipart/form-data")
    as_admin.post("/admin/logo", data={"logo": (png((20, 20)), "x.png")}, content_type="multipart/form-data")
    assert db.session.get(Setting, "logo_version").value == version

    as_admin.post("/admin/logo/standard")
    assert db.session.get(Setting, "logo_version").value is None
    assert app.test_client().get("/logo/icon-192.png").status_code == 302
