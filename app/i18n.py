"""Mehrsprachigkeit (Deutsch / Polnisch).

Bewusst schlank gehalten: Die deutschen Texte sind gleichzeitig die Schlüssel,
die Übersetzungen liegen als Python-Wörterbuch in `app/translations/`.
Fehlt eine Übersetzung, wird der deutsche Text angezeigt (ein Test prüft die Vollständigkeit).

Verwendung:
    _("{code} gespeichert.", code=box.code)
    ngettext("{n} Kiste", "{n} Kisten", n)
    N_("voll")   # nur markieren (für Konstanten), übersetzt wird später mit _()
"""
import threading
from contextlib import contextmanager

from flask import g, has_request_context, request
from flask_login import current_user

from .translations import pl

DEFAULT_LANGUAGE = "de"
LANGUAGES = {"de": "Deutsch", "pl": "Polski"}
LANGUAGE_FLAGS = {"de": "🇩🇪", "pl": "🇵🇱"}
CATALOGS = {"pl": pl.MESSAGES}
LANG_COOKIE = "lang"


_forced = threading.local()


@contextmanager
def force_locale(lang):
    """Texte für jemand anderen übersetzen (z. B. Push-Nachricht in der Sprache des Empfängers)."""
    previous = getattr(_forced, "lang", None)
    _forced.lang = lang if lang in LANGUAGES else DEFAULT_LANGUAGE
    try:
        yield
    finally:
        _forced.lang = previous


def get_locale():
    """Sprache der aktuellen Anfrage: Benutzereinstellung → Cookie → Browser → Deutsch."""
    if getattr(_forced, "lang", None):
        return _forced.lang
    if not has_request_context():
        return DEFAULT_LANGUAGE
    lang = g.get("_lang")
    if lang:
        return lang
    lang = None
    if current_user and current_user.is_authenticated:
        lang = current_user.language
    if lang not in LANGUAGES:
        lang = request.cookies.get(LANG_COOKIE)
    if lang not in LANGUAGES:
        lang = request.accept_languages.best_match(list(LANGUAGES), DEFAULT_LANGUAGE)
    g._lang = lang
    return lang


def set_request_locale(lang):
    """Sprache für den Rest dieser Anfrage festlegen (z. B. direkt nach dem Umschalten)."""
    if lang in LANGUAGES:
        g._lang = lang


def _translate(text, lang=None):
    lang = lang or get_locale()
    if lang == DEFAULT_LANGUAGE:
        return text
    return CATALOGS.get(lang, {}).get(text, text)


def gettext(text, **params):
    result = _translate(text)
    return result.format(**params) if params else result


def ngettext(singular, plural, count, **params):
    """Plural nach `count`; der Platzhalter {n} ist `count`, außer er wird ausdrücklich übergeben."""
    params.setdefault("n", count)
    lang = get_locale()
    forms = CATALOGS.get(lang, {}).get(plural) if lang != DEFAULT_LANGUAGE else None
    if isinstance(forms, tuple):
        result = forms[_plural_index_pl(count)]
    else:
        result = singular if count == 1 else plural
    return result.format(**params)


def _plural_index_pl(n):
    """Polnische Pluralformen: 1 skrzynia, 2–4 skrzynie, 5+ skrzyń (aber 22 skrzynie)."""
    if n == 1:
        return 0
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return 1
    return 2


def N_(text):
    """Markiert einen Text zur Übersetzung, ohne ihn sofort zu übersetzen."""
    return text


_ = gettext
