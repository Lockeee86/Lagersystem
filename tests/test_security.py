"""Sicherheit: Weiterleitungen nur auf eigene Seiten, Excel-Export ohne Formeln, XML ohne DTD, KMZ-Größe."""
import io
import zipfile

import pytest
from openpyxl import Workbook

from app import fields, forest_import
from app.storage import StorageError
from app.utils import _is_local_path, excel_safe


@pytest.mark.parametrize("target", ["//fremd.de", "/\\\\fremd.de", "/\\fremd.de", "https://fremd.de", "", "fremd"])
def test_back_rejects_foreign_targets(target):
    assert not _is_local_path(target)


def test_back_allows_local():
    assert _is_local_path("/biogas/fuetterung?woche=2026-11-09#t2026-11-10")


def test_feeding_back_is_local_only(app, db, seed, as_admin):
    r = as_admin.post("/biogas/fuetterung/eintragen", data={"tag": "2000-01-01", "slot": "1", "back": "/\\fremd.de"})
    assert r.status_code == 302 and "fremd.de" not in r.headers["Location"]


def test_excel_never_writes_formulas():
    wb = Workbook()
    wb.active.append(["=HYPERLINK(\"http://fremd.de\",\"Klick\")", "normal", 3])
    excel_safe(wb)
    buf = io.BytesIO()
    wb.save(buf)
    assert b"<f>" not in zipfile.ZipFile(buf).read("xl/worksheets/sheet1.xml")


def test_xml_with_late_doctype_is_rejected():
    data = b"<?xml version='1.0'?>" + b"<!-- " + b"x" * 5000 + b" -->" + b"<!doctype a [<!entity e 'x'>]><a>&e;</a>"
    with pytest.raises(StorageError):
        forest_import._rows_spreadsheetml(data)


def test_kmz_too_big(monkeypatch):
    monkeypatch.setattr(fields, "KML_MAX_BYTES", 10)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("doc.kml", "<kml>" + " " * 100 + "</kml>")
    with pytest.raises(fields.FieldImportError):
        fields._kml_bytes("a.kmz", buf.getvalue())


def test_csp_nonce_and_no_inline_handlers(app, db, seed, as_admin):
    """Skripte laufen nur mit dem Einmal-Wert der Antwort; Inline-Handler gibt es in keiner Vorlage."""
    import glob
    import re
    r = as_admin.get("/")
    csp = r.headers["Content-Security-Policy"]
    nonce = re.search(r"script-src 'self' 'nonce-([\w-]+)'", csp).group(1)
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "object-src 'none'" in csp and "base-uri 'self'" in csp and "form-action 'self'" in csp
    page = r.get_data(as_text=True)
    inline = re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)([^>]*)>", page)
    assert inline and all(f'nonce="{nonce}"' in attrs for attrs in inline)
    assert nonce != re.search(r"'nonce-([\w-]+)'", as_admin.get("/").headers["Content-Security-Policy"]).group(1)
    for path in glob.glob("app/templates/**/*.html", recursive=True):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert not re.search(r"\son[a-z]+\s*=\s*[\"']", text), f"Inline-Handler in {path}"
        assert "javascript:" not in text, path
        for attrs in re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)([^>]*)>", text):
            assert "csp_nonce()" in attrs, f"<script> ohne nonce in {path}"
