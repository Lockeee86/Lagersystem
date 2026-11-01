"""Werkstatt-Bildschirm: Vollbild für einen Fernseher in Werkstatt oder Büro (Full HD, auf andere Größen skaliert).

Tagesplan, offene Störungen, fällige Wartungen, Aufgaben, Übergabe-Notizen und Wetter auf einen Blick; lädt jede
Minute neu. Öffnet ohne Anmeldung über einen geheimen Link (`settings` `display_workshop_token`, Admin erzeugt bzw.
erneuert ihn unter Verwaltung → Hof). Nur lesend; committet nicht.
"""
import hmac
import secrets
from datetime import datetime

from . import machines, tasks, team, weather
from .extensions import db
from .models import Setting
from .utils import local_tz

TOKEN_KEY = "display_workshop_token"
REFRESH = 60                 # Sekunden
LIMITS = {"faults": 7, "due": 8, "tasks": 6, "handovers": 2, "per_person": 6}


def token():
    setting = db.session.get(Setting, TOKEN_KEY)
    return setting.value if setting and setting.value else None


def new_token():
    value = secrets.token_urlsafe(18)
    Setting.put(TOKEN_KEY, value)
    return value


def check_token(value):
    current = token()
    return bool(value and current and hmac.compare_digest(current, value))


def _cut(items, limit):
    items = list(items)
    return items[:limit], max(len(items) - limit, 0)


def data(now=None):
    now = now or datetime.now(local_tz())
    today = now.date()
    plan = team.plan_for(today)
    people = []
    for user in team.team_members():
        entries = plan.get(user.id, [])
        if entries:
            shown, more = _cut(entries, LIMITS["per_person"])
            people.append({"user": user, "entries": shown, "more": more,
                           "done": sum(1 for e in entries if e.done_at), "total": len(entries)})
    faults, faults_more = _cut(machines.open_faults(), LIMITS["faults"])
    due, due_more = _cut(machines.due_tasks(("overdue", "soon")), LIMITS["due"])
    open_tasks, tasks_more = _cut([t for t in tasks.due_now() if t.area != "huehner"], LIMITS["tasks"])
    handovers, _more = _cut(team.open_handovers(), LIMITS["handovers"])
    w = weather.get_weather()
    if not w or w.get("error"):
        w = None
    return {"now": now, "today": today, "people": people, "faults": faults, "faults_more": faults_more,
            "due": due, "due_more": due_more, "tasks": open_tasks, "tasks_more": tasks_more,
            "handovers": handovers, "weather": w, "refresh": REFRESH}
