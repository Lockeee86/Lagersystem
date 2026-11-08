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
