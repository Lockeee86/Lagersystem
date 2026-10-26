"""Benutzer „nur Hühner“ (`User.chickens_only`): sehen ausschließlich die Rubrik Hühner, deren Aufgaben
(`Task.area == "huehner"` oder ihnen selbst zugeteilt) und die Stallkontrolle. Alles andere ist für sie 404.

`allowed()` prüft jede Anfrage (aus `create_app`, `before_request`), `task_visible()` die einzelne Aufgabe.
"""
from flask import request
from sqlalchemy import or_

from .extensions import db
from .models import CheckStation, Photo, StationCheck, Task, task_assignees

AREA = "huehner"
# ganze Blueprints bzw. einzelne Endpunkte, die immer gehen
OPEN_PREFIXES = ("chickens.", "auth.", "push.", "api.")
OPEN_ENDPOINTS = {"static", "branding.logo", "main.scan", "tasks.task_list", "tasks.task_create"}
TASK_ENDPOINTS = {"tasks.task_detail", "tasks.task_edit", "tasks.task_done", "tasks.task_reopen", "tasks.task_delete"}
STATION_ENDPOINTS = {"rounds.station", "rounds.station_check"}


def filter_tasks(query, user):
    """Aufgaben, die ein Hühner-Benutzer sieht: aus der Rubrik Hühner oder ihm zugeteilt."""
    mine = db.session.query(task_assignees.c.task_id).filter(task_assignees.c.user_id == user.id)
    return query.filter(or_(Task.area == AREA, Task.id.in_(mine)))


def task_visible(task, user):
    return task.area == AREA or user in task.assignees


def _station_ok(code):
    from .rounds import normalize_code
    station = CheckStation.query.filter_by(code=normalize_code(code or "") or "").first()
    return station is not None and station.area == AREA


def allowed(user, endpoint, view_args):
    """Darf ein Hühner-Benutzer diese Seite öffnen?"""
    endpoint = endpoint or ""
    args = view_args or {}
    if endpoint in OPEN_ENDPOINTS or endpoint.startswith(OPEN_PREFIXES):
        return True
    if endpoint == "main.area":
        return args.get("key") in (AREA, "mehr")
    if endpoint in TASK_ENDPOINTS:
        task = db.session.get(Task, args.get("task_id") or 0)
        return task is None or task_visible(task, user)          # None → die Seite selbst meldet 404
    if endpoint in STATION_ENDPOINTS:
        return _station_ok(args.get("code"))
    if endpoint == "rounds.check_resolve":
        check = db.session.get(StationCheck, args.get("check_id") or 0)
        return check is not None and check.station.area == AREA
    if endpoint == "machines.photo":
        photo = db.session.get(Photo, args.get("photo_id") or 0)
        if photo is None:
            return False
        if photo.check_id:
            check = db.session.get(StationCheck, photo.check_id)
            return check is not None and check.station.area == AREA
        if photo.task_id:
            task = db.session.get(Task, photo.task_id)
            return task is not None and task_visible(task, user)
        return False
    return False


def current_allowed(user):
    return allowed(user, request.endpoint, request.view_args)
