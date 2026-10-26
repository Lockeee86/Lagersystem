"""Untere Leiste am Handy: Heute · [frei] · Scannen · [frei] · Mehr.

Die zwei freien Plätze sind Rubriken aus `navigation.AREAS` (`CHOICES`). Voreinstellung je Rolle in `settings`
`tabbar_<rolle>` (Verwaltung → Startseite), eigene Wahl in `User.tabbar` (Mein Konto). Rubriken, die ein Benutzer
nicht sieht (z. B. Hühner), fallen weg und werden aufgefüllt. Rubriken in der Leiste fehlen dafür unter „Mehr“.
"""
from flask import url_for

from . import navigation
from .extensions import db
from .models import Setting

CHOICES = ("lager", "feld", "biogas", "huehner", "sensoren", "verkauf")
DEFAULT = ("lager", "feld")
SLOTS = 2


def _split(value):
    return [k for k in (value or "").split(",") if k in CHOICES]


def _area(key):
    return next(a for a in navigation.AREAS if a["key"] == key)


def available(user):
    """Rubriken, die der Benutzer sieht (und in denen er mindestens eine Seite öffnen darf)."""
    return [k for k in CHOICES if navigation._area_shown(_area(k), user)
            and navigation._visible(_area(k)["items"], user)]


def role_default(role):
    keys = _split((db.session.get(Setting, f"tabbar_{role}") or Setting()).value)
    return keys[:SLOTS] if len(keys) >= SLOTS else list(DEFAULT)


def save_role_default(role, keys):
    Setting.put(f"tabbar_{role}", ",".join(_clean(keys)))


def _clean(keys):
    result = []
    for key in keys:
        if key in CHOICES and key not in result:
            result.append(key)
    return result[:SLOTS]


def save_user(user, keys):
    """Eigene Wahl; gleich wie die Voreinstellung der Rolle (oder unvollständig) = keine eigene Wahl."""
    keys = _clean(keys)
    user.tabbar = ",".join(keys) if len(keys) == SLOTS and keys != role_default(user.role) else ""


def keys_for(user):
    """Die zwei Rubriken des Benutzers – eigene Wahl, sonst Voreinstellung; Unsichtbares wird aufgefüllt."""
    seen = available(user)
    wanted = _split(user.tabbar) or role_default(user.role)
    keys = [k for k in wanted if k in seen][:SLOTS]
    for key in list(DEFAULT) + seen:
        if len(keys) >= SLOTS:
            break
        if key in seen and key not in keys:
            keys.append(key)
    return keys


def url(key, user):
    """Lager hat eine eigene Übersicht, Rubriken mit nur einer Seite öffnen diese, sonst die Bereichsseite."""
    if key == "lager":
        return url_for("main.storage_home")
    items = [it for it in navigation._visible(_area(key)["items"], user) if it["endpoint"] != "main.scan"]
    if len(items) == 1:
        return url_for(items[0]["endpoint"])
    return url_for("main.area", key=key)


def entries(user):
    return [{"key": k, "label": _area(k)["label"], "icon": _area(k)["icon"], "url": url(k, user)}
            for k in keys_for(user)]


def labels():
    return {k: _area(k)["label"] for k in CHOICES}
