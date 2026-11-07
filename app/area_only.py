"""Benutzer „nur eine Rubrik“ (`User.chickens_only` bzw. `User.forest_only` → `User.only_area`): sehen
ausschließlich ihre Rubrik (Hühner bzw. Bestattungswald), deren Aufgaben (`Task.area` = Rubrik oder ihnen selbst
zugeteilt) und den Rundgang der Rubrik (Stallkontrolle bzw. Baumkontrolle). Alles andere ist für sie 404.

`allowed()` prüft jede Anfrage (aus `create_app`, `before_request`), `task_visible()` die einzelne Aufgabe.
Neue Seiten einer Rubrik gehen über den Blueprint-Präfix automatisch; andere hier freigeben.
"""
from flask import request
from sqlalchemy import or_

from .extensions import db
from .i18n import N_
from .models import CheckStation, Photo, StationCheck, Task, task_assignees

# je Rubrik: Blueprint-Präfix, Startseite, Beschriftung, zweiter Platz der unteren Leiste, Push-Gruppe
AREAS = {
    "huehner": {"prefix": "chickens.", "home": "chickens.overview", "label": N_("Hühner"), "icon": "bi-egg",
                "tab": {"key": "eier", "label": N_("Eier"), "icon": "bi-basket2", "endpoint": "chickens.eggs"},
                "push_group": "Hühner"},
    "wald": {"prefix": "forest.", "home": "forest.overview", "label": N_("Wald"), "icon": "bi-tree",
             "tab": {"key": "beisetzungen", "label": N_("Beisetzungen"), "icon": "bi-calendar-event",
                     "endpoint": "forest.burial_page"},
             "push_group": "Bestattungswald"},
}
# Endpunkte, die immer gehen
OPEN_PREFIXES = ("auth.", "push.", "api.")
OPEN_ENDPOINTS = {"static", "branding.logo", "main.scan", "tasks.task_list", "tasks.task_create"}
TASK_ENDPOINTS = {"tasks.task_detail", "tasks.task_edit", "tasks.task_done", "tasks.task_reopen", "tasks.task_delete"}
STATION_ENDPOINTS = {"rounds.station", "rounds.station_check"}


def config(user):
    return AREAS.get(user.only_area)


def filter_tasks(query, user):
    """Aufgaben, die ein solcher Benutzer sieht: aus seiner Rubrik oder ihm zugeteilt."""
    mine = db.session.query(task_assignees.c.task_id).filter(task_assignees.c.user_id == user.id)
    return query.filter(or_(Task.area == user.only_area, Task.id.in_(mine)))


def task_visible(task, user):
    return task.area == user.only_area or user in task.assignees


def _station_ok(code, area):
    from .rounds import normalize_code
    station = CheckStation.query.filter_by(code=normalize_code(code or "") or "").first()
    return station is not None and station.area == area


def allowed(user, endpoint, view_args):
    """Darf dieser Benutzer die Seite öffnen?"""
    area = user.only_area
    endpoint = endpoint or ""
    args = view_args or {}
    if endpoint in OPEN_ENDPOINTS or endpoint.startswith(OPEN_PREFIXES) or endpoint.startswith(AREAS[area]["prefix"]):
        return True
    if endpoint == "main.area":
        return args.get("key") in (area, "mehr")
    if endpoint in TASK_ENDPOINTS:
        task = db.session.get(Task, args.get("task_id") or 0)
        return task is None or task_visible(task, user)          # None → die Seite selbst meldet 404
    if endpoint in STATION_ENDPOINTS:
        return _station_ok(args.get("code"), area)
    if endpoint == "rounds.check_resolve":
        check = db.session.get(StationCheck, args.get("check_id") or 0)
        return check is not None and check.station.area == area
    if endpoint == "machines.photo":
        photo = db.session.get(Photo, args.get("photo_id") or 0)
        if photo is None:
            return False
        if photo.check_id:
            check = db.session.get(StationCheck, photo.check_id)
            return check is not None and check.station.area == area
        if photo.task_id:
            task = db.session.get(Task, photo.task_id)
            return task is not None and task_visible(task, user)
        return bool(photo.forest_tree_id) and area == "wald"
    if endpoint == "main.photos_add":                     # Fotoleiste an der Baumseite
        return area == "wald" and args.get("kind") == "baum"
    if endpoint == "main.photo_delete":
        photo = db.session.get(Photo, args.get("photo_id") or 0)
        return photo is not None and bool(photo.forest_tree_id) and area == "wald"
    return False


def current_allowed(user):
    return allowed(user, request.endpoint, request.view_args)
