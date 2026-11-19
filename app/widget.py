"""iPhone-Widget (App „Scriptable“): kleine JSON-Zusammenfassung je Benutzer und das fertige Skript dazu.

Das Widget holt `/widget/<schlüssel>.json` ohne Anmeldung – der persönliche Schlüssel (`User.widget_token`) reicht,
wie beim Kalender-Abo. Inhalt richtet sich nach den Rechten des Benutzers: Biogas nur mit `sees_biogas`, Hühner nur
mit `sees_chickens`, Fütterung mit `sees_feeding`. Keine Euro-Beträge, keine Kunden, keine Lieferscheine; Lager ist
bewusst nicht dabei. Texte kommen fertig übersetzt in der Sprache des Benutzers (Schlüssel `t`).
Committet nicht (außer über die Views).
"""
import os
import secrets
from datetime import datetime
from urllib.parse import urljoin

from flask import request, url_for

from .extensions import db
from .i18n import gettext as _, ngettext
from .models import Setting, User
from .utils import local_tz

SMALL_KINDS = ("huehner", "biogas", "aufgaben", "wichtig", "projekte")      # Parameter des kleinen Widgets in Scriptable
SCRIPT_FILE = os.path.join(os.path.dirname(__file__), "widget_script.js")
DOOR_WORDS = {"zu": "door_closed", "offen": "door_open", "halb": "door_half"}


# --- Schlüssel ----------------------------------------------------------------------

def ensure_token(user):
    if not user.widget_token:
        user.widget_token = secrets.token_urlsafe(24)
    return user.widget_token


def renew_token(user):
    user.widget_token = secrets.token_urlsafe(24)
    return user.widget_token


def user_by_token(token):
    if not token or len(token) > 64:
        return None
    user = User.query.filter_by(widget_token=token).first()
    return user if user and user.active else None


def feed_url(user):
    return url_for("widget.feed", token=user.widget_token, _external=True) if user.widget_token else None


def script(user):
    """Das Scriptable-Skript mit eingetragener Adresse (inkl. Schlüssel)."""
    with open(SCRIPT_FILE, encoding="utf-8") as f:
        text = f.read()
    return text.replace("__WIDGET_URL__", feed_url(user) or "")


# --- Daten --------------------------------------------------------------------------

def _abs(url):
    return urljoin(request.url_root, url) if url else None


def _labels():
    return {
        "eggs_today": _("Eier heute"), "eggs": _("Eier"), "stock": _("Vorrat"), "stall": _("Stall"),
        "door_closed": _("Klappe zu"), "door_open": _("Klappe offen"), "door_half": _("Klappe hängt"),
        "door_alert": _("Klappe offen!"), "since": _("seit"), "chickens": _("Hühner"), "biogas": _("Biogas"),
        "tasks": _("Aufgaben"), "my_tasks": _("Meine Aufgaben"), "today_mine": _("heute für mich"),
        "important": _("Jetzt wichtig"), "all_good": _("Alles in Ordnung"), "price": _("Strompreis"),
        "next_feeding": _("Fütterung"), "more": _("weitere"), "open": _("offen"), "stand": _("Stand"),
        "offline": _("Keine Verbindung – letzter Stand"), "no_data": _("Noch keine Daten"),
        "projects": _("Projekte"), "next_step": _("Als Nächstes:"), "no_projects": _("Keine laufenden Projekte"),
        "steps": _("Schritte"),
    }


def _important(user, today):
    """„Jetzt wichtig“ wie auf der Startseite – ohne Lieferscheine, Bestellungen, Lager und „ist da“-Meldungen."""
    from . import backup, irrigation, machines, rounds, tasks, team, views
    items = views._important(
        tasks.due_now(None if user.is_admin else user), machines.open_faults(user), machines.due_tasks(user=user),
        [], [], backup.needs_attention() if user.is_admin else None, today, (),
        team.due_count(today) if user.is_admin else 0, irrigation.alerts() if user.is_admin else (),
        rounds.overview() if user.can_edit and user.sees_biogas and rounds.has_stations() else None)
    from . import plant_feeding
    if (plant_feeding.is_member(user) or plant_feeding.is_planner(user)) and plant_feeding.active():
        items = items + views._feeding_important(today)
    rows = [{"level": i["level"], "text": f"{i['title']}: {i['sub']}" if i.get("sub") else i["title"],
             "url": _abs(i.get("url"))} for i in items if i["level"] in ("danger", "warning")]
    order = {"danger": 0, "warning": 1}
    return sorted(rows, key=lambda r: order[r["level"]])


def _tasks(user, today):
    from . import team
    open_ = [t for t in team.my_today(user, today) if not t.is_done]
    return {"open": len(open_), "items": [t.title for t in open_[:4]],
            "url": _abs(url_for("team.day_plan"))}


def _projects(user, today):
    """Laufende Projekte des Benutzers (wie „Meine Projekte“ auf der Startseite) – ohne Kosten."""
    from . import projects
    rows = []
    for c in projects.mine(user, today, limit=3):
        p, nxt = c["project"], c["next"]
        rows.append({"name": p.name, "done": c["progress"]["done"], "total": c["progress"]["total"],
                     "pct": c["progress"]["pct"], "health": c["health"], "reason": c["reason"],
                     "next": nxt.title if nxt else None,
                     "next_due": nxt.due_on.strftime("%d.%m.") if nxt and nxt.due_on else None,
                     "next_late": bool(nxt and nxt.due_on and nxt.due_on < today),
                     "target": p.target_on.strftime("%d.%m.%Y") if p.target_on else None,
                     "url": _abs(url_for("projects.project_detail", project_id=p.id))})
    return rows


def _bands(strip):
    from .plant_display import _price_bands
    merged = []
    for band in _price_bands(strip):
        if merged and merged[-1]["level"] == band["level"]:
            merged[-1]["width"] += band["width"]
        else:
            merged.append(dict(band))
    for band in merged:
        band["width"] = round(band["width"], 2)
    return merged


def _biogas(today):
    from . import biogas, machines
    plant = biogas.dashboard(today)
    if not plant or not plant.get("rows"):
        return None
    cached = biogas.live(cached_only=True) or {}
    if cached.get("error"):
        cached = {}
    faulty = {e.machine_id for e in machines.open_faults() if not e.still_running}
    motors = []
    for row in plant["rows"]:
        now = row["now"] or {}
        state = "laeuft" if now.get("running") else "stoerung" if row["machine"].id in faulty \
            else "steht" if row["now"] else "unbekannt"
        motors.append({"name": row["machine"].name, "state": state,
                       "kw": round(now["kw_now"]) if now.get("kw_now") is not None else None})
    strip = biogas.price_strip(cached.get("price_series")) if cached.get("price_series") else None
    best = None
    if strip and strip.get("best"):
        when = strip["best"][0].strftime("%H:%M")
        best = _("teuerste {time}", time=when) if strip["view"] == "erzeuger" else _("günstigste {time}", time=when)
    price = cached.get("price_now")
    return {"kw": round(plant.get("kw") or 0), "running": plant.get("running", 0), "total": len(motors),
            "motors": motors, "price": round(price) if price is not None else None, "bands": _bands(strip),
            "now_pct": strip.get("now_pct") if strip else None, "best": best,
            "running_text": _("{n} von {total} Motoren laufen", n=plant.get("running", 0), total=len(motors)),
            "url": _abs(url_for("biogas.overview"))}


def _feeding(today):
    from . import plant_feeding
    from .plant_display import _feeding_line
    if not plant_feeding.active():
        return None
    line = _feeding_line(plant_feeding.day_amounts(today))
    return {"text": line, "url": _abs(url_for("biogas.feeding_page"))} if line else None


def _chickens(now):
    from . import chickens, coop
    today = now.date()
    sensor = coop.status()
    door = since = temp = None
    alert = False
    if sensor and not sensor["offline"]:
        door = sensor["state"]
        if sensor.get("state_since"):
            since = sensor["state_since"].astimezone(now.tzinfo).strftime("%H:%M")
        temp = sensor.get("temperature")
        deadline, _reason = coop.close_deadline(today, now.tzinfo, chickens.farm_sunset(today))
        alert = door in ("offen", "halb") and deadline is not None and now >= deadline
    return {"eggs": chickens.eggs_on(today)["eggs"], "stock": chickens.stock(),
            "door": DOOR_WORDS.get(door), "door_since": since, "door_alert": alert,
            "temp": round(temp, 1) if temp is not None else None, "url": _abs(url_for("chickens.overview"))}


def data(user, now=None):
    """Alles fürs Widget dieses Benutzers."""
    tz = local_tz()
    now = now or datetime.now(tz)
    today = now.date()
    out = {"v": 1, "stand": now.strftime("%H:%M"), "farm": Setting.all().get("farm_name") or _("Hof"),
           "url": _abs(url_for("main.dashboard")), "t": _labels(),
           "important": _important(user, today), "tasks": _tasks(user, today)}
    if user.sees_biogas:
        out["biogas"] = _biogas(today)
    if user.sees_feeding:
        out["feeding"] = _feeding(today)
    if user.sees_chickens:
        out["chickens"] = _chickens(now)
    if not user.restricted:
        out["projects"] = _projects(user, today)
    out["important_text"] = ngettext("{n} wichtig", "{n} wichtig", len(out["important"])) if out["important"] else ""
    db.session.rollback()                       # nur gelesen
    return out
