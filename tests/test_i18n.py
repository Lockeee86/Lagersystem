"""Prüft, dass jeder übersetzbare Text eine polnische Übersetzung hat."""
import ast
import re
from pathlib import Path

import pytest

from app.i18n import _plural_index_pl
from app.translations.pl import MESSAGES

ROOT = Path(__file__).resolve().parent.parent / "app"
STR = r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')'
SINGLE = re.compile(r"(?<![\w.])(?:_|N_|gettext)\(\s*" + STR)
PLURAL = re.compile(r"(?<![\w.])ngettext\(\s*" + STR + r"\s*,\s*" + STR)
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def collect():
    """{deutscher Text: "single" | "plural"} aus allen .py- und .html-Dateien der App."""
    found = {}
    for path in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.html")):
        if "translations" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for m in PLURAL.finditer(text):
            found[ast.literal_eval(m.group(2))] = "plural"
            found.setdefault(ast.literal_eval(m.group(1)), "singular-of-plural")
        for m in SINGLE.finditer(text):
            found.setdefault(ast.literal_eval(m.group(1)), "single")
    return found


def test_all_texts_translated():
    found = collect()
    missing = [t for t, kind in found.items() if kind != "singular-of-plural" and t not in MESSAGES]
    assert not missing, "Fehlende polnische Übersetzungen:\n" + "\n".join(missing)


def test_plurals_have_three_forms():
    for text, kind in collect().items():
        if kind == "plural":
            assert isinstance(MESSAGES[text], tuple) and len(MESSAGES[text]) == 3, text


def test_placeholders_match():
    for de, pl in MESSAGES.items():
        for form in (pl if isinstance(pl, tuple) else (pl,)):
            assert set(PLACEHOLDER.findall(de)) >= set(PLACEHOLDER.findall(form)), (de, form)


def test_no_unused_translations():
    found = collect()
    unused = [t for t in MESSAGES if t not in found]
    assert not unused, "Nicht mehr verwendete Übersetzungen:\n" + "\n".join(unused)


@pytest.mark.parametrize("n,index", [(1, 0), (2, 1), (4, 1), (5, 2), (12, 2), (14, 2), (22, 1), (25, 2), (0, 2), (101, 2)])
def test_polish_plural_rule(n, index):
    assert _plural_index_pl(n) == index
