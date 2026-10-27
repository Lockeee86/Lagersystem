"""Push-Nachrichten (Web Push) an Handys und Rechner.

- VAPID-Schlüssel werden beim ersten Gebrauch erzeugt und in `settings` gespeichert
  (oder kommen aus der Umgebungsvariable VAPID_PRIVATE_KEY).
- `notify()` wird **nach** dem Commit aufgerufen: Texte werden je Empfänger in seiner Sprache gebaut,
  verschickt wird im Hintergrund. Abgelaufene Abos (404/410) werden dabei gelöscht.
- `daily()` läuft aus dem Zeitplan (`scheduler.py`): morgens Aufgaben/Wartungen/Krautfäule, nachmittags Frost.
"""
import base64
import json
import os
import threading
from contextlib import nullcontext
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context, has_request_context, request, url_for

from .extensions import db
from .i18n import force_locale, gettext, ngettext
from .models import PushSubscription, Setting, User, utcnow

TTL = 12 * 3600                      # so lange hält der Push-Dienst eine Nachricht für ein ausgeschaltetes Handy
MORNING_HOUR = 6
FROST_HOUR = 16
EVENING_HOUR = 19                     # Ernte-Tagesbericht

outbox = []                          # in Tests landen die Nachrichten hier statt beim Push-Dienst


class Msg:
    """Text, der erst für den jeweiligen Empfänger übersetzt wird. Parameter dürfen selbst `Msg` sein."""

    def __init__(self, text, plural=None, count=None, **params):
        self.text, self.plural, self.count, self.params = text, plural, count, params

    def render(self):
        params = {k: v.render() if isinstance(v, Msg) else v for k, v in self.params.items()}
        if self.plural is not None:
            return ngettext(self.text, self.plural, self.count, **params)
        return gettext(self.text, **params) if params else gettext(self.text)


def _render(value):
    return value.render() if isinstance(value, Msg) else value


# --- Schlüssel ----------------------------------------------------------------

def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _vapid():
    from py_vapid import Vapid
    pem = os.environ.get("VAPID_PRIVATE_KEY")
    if not pem:
        setting = db.session.get(Setting, "vapid_private_key")
        if setting is None or not setting.value:
            key = Vapid()
            key.generate_keys()
            Setting.put("vapid_private_key", key.private_pem().decode())
            db.session.commit()
            setting = db.session.get(Setting, "vapid_private_key")
        pem = setting.value
    return Vapid.from_pem(pem.encode())


def public_key():
    """Öffentlicher Schlüssel für `pushManager.subscribe()` (Base64-URL, unkomprimierter Punkt)."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    return _b64url(_vapid().public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))


def _contact():
    """„sub“ für VAPID: die Adresse der App (Apple verlangt eine gültige Angabe)."""
    setting = db.session.get(Setting, "push_contact")
    if setting and setting.value:
        return setting.value
    email = Setting.all().get("farm_email")
    return f"mailto:{email}" if email else "mailto:lagersystem@localhost"


def remember_contact():
    """Beim Einschalten die öffentliche Adresse (https://…) merken – nur über HTTPS sinnvoll."""
    if has_request_context() and request.scheme == "https":
        url = request.host_url.rstrip("/")
        setting = db.session.get(Setting, "push_contact")
        if setting is None or setting.value != url:
            Setting.put("push_contact", url)


# --- Abos -----------------------------------------------------------------------

def device_label(user_agent):
    ua = user_agent or ""
    system = next((name for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                          ("Windows", "Windows"), ("Macintosh", "Mac"), ("Linux", "Linux"))
                   if key in ua), "")
    browser = next((name for key, name in (("Edg/", "Edge"), ("Firefox", "Firefox"), ("Chrome", "Chrome"),
                                           ("Safari", "Safari")) if key in ua), "")
    return " · ".join(p for p in (system, browser) if p) or None


def subscribe(user, data, user_agent=None):
    """Abo aus `PushSubscription.toJSON()` speichern (ein Gerät gehört immer dem zuletzt angemeldeten Benutzer)."""
    endpoint = (data or {}).get("endpoint") or ""
    keys = (data or {}).get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        return None
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first() or PushSubscription(endpoint=endpoint)
    sub.user_id = user.id
    sub.p256dh, sub.auth = keys["p256dh"][:200], keys["auth"][:100]
    sub.device = (device_label(user_agent) or sub.device or "")[:120] or None
    db.session.add(sub)
    remember_contact()
    db.session.flush()
    return sub


def unsubscribe(user, endpoint):
    PushSubscription.query.filter_by(user_id=user.id, endpoint=endpoint or "").delete()


# --- Senden ---------------------------------------------------------------------

def _recipients(users, topic, exclude):
    seen, result = set(), []
    excluded = {u.id for u in (exclude if isinstance(exclude, (list, tuple, set)) else [exclude]) if u is not None}
    for user in users:
        if user is None or user.id in seen or user.id in excluded or not user.active:
            continue
        seen.add(user.id)
        if (topic is None or user.wants_push(topic)) and user.push_subscriptions:
            result.append(user)
    return result


def notify(users, topic, title, body=None, url=None, tag=None, exclude=None):
    """Nachricht an `users` schicken, die das Thema `topic` nicht abgewählt haben (topic=None: immer).
    `title`/`body` als `Msg`, damit jeder sie in seiner Sprache bekommt. Gibt die Zahl der Empfänger zurück.
    Fehler beim Benachrichtigen dürfen das eigentliche Speichern nie stören – sie landen nur im Log."""
    try:
        return _notify(users, topic, title, body, url, tag, exclude)
    except Exception:                            # noqa: BLE001
        if current_app.config.get("TESTING"):
            raise
        current_app.logger.exception("Push-Nachricht „%s“", topic)
        db.session.rollback()
        return 0


def _notify(users, topic, title, body, url, tag, exclude):
    jobs = []
    recipients = _recipients(users, topic, exclude)
    for user in recipients:
        with force_locale(user.language):
            payload = json.dumps({"title": _render(title), "body": _render(body) or "", "url": url or "/",
                                  "tag": tag or topic or "lager"})
        for sub in user.push_subscriptions:
            jobs.append((sub.id, {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                         payload, user.id))
    if not jobs:
        return 0
    app = current_app._get_current_object()
    if app.config.get("TESTING"):
        outbox.extend({"user_id": uid, "sub_id": sid, **json.loads(payload)} for sid, _info, payload, uid in jobs)
        return len(recipients)
    claims_sub, vapid = _contact(), _vapid()
    threading.Thread(target=_send, args=(app, jobs, vapid, claims_sub), name="push", daemon=True).start()
    return len(recipients)


def _send(app, jobs, vapid, claims_sub):
    from pywebpush import WebPushException, webpush
    gone, delivered = [], []
    for sub_id, info, payload, _uid in jobs:
        try:
            webpush(info, data=payload, vapid_private_key=vapid, vapid_claims={"sub": claims_sub},
                    ttl=TTL, timeout=10)
            delivered.append(sub_id)
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                gone.append(sub_id)              # Gerät abgemeldet oder App gelöscht
            else:
                app.logger.warning("Push an Abo %s fehlgeschlagen: %s", sub_id, e)
        except Exception as e:                   # noqa: BLE001 – Netzfehler dürfen nichts kaputt machen
            app.logger.warning("Push an Abo %s fehlgeschlagen: %s", sub_id, e)
    if not gone and not delivered:
        return
    with (nullcontext() if has_app_context() else app.app_context()):
        try:
            if gone:
                PushSubscription.query.filter(PushSubscription.id.in_(gone)).delete(synchronize_session=False)
            if delivered:
                PushSubscription.query.filter(PushSubscription.id.in_(delivered)) \
                    .update({"last_sent_at": utcnow()}, synchronize_session=False)
            db.session.commit()
        except Exception:                        # noqa: BLE001
            db.session.rollback()
            app.logger.exception("Push-Abos aktualisieren")


def editors():
    return [u for u in User.query.filter_by(active=True) if u.can_edit]


def everyone():
    return User.query.filter_by(active=True).all()


# --- Auslöser aus den Seiten (nach dem Commit aufrufen) ----------------------------------

def task_assigned(task, actor, users=None):
    """Neue Aufgabe: an die Zuständigen (ohne Zuteilung: an alle Mitarbeiter)."""
    targets = users if users is not None else (task.assignees or editors())
    body = task.title if not task.due_on else Msg("{title} · fällig {date}", title=task.title,
                                                    date=task.due_on.strftime("%d.%m."))
    return notify(targets, "tasks_new", Msg("Neue Aufgabe"), body,
                  url_for("tasks.task_detail", task_id=task.id), tag=f"task-{task.id}", exclude=actor)


def fault_reported(event, actor, exclude=()):
    from .machines import can_see
    return notify([u for u in editors() if can_see(event.machine, u)], "fault",
                  Msg("Störung: {machine} (läuft weiter)", machine=event.machine.name) if event.still_running
                  else Msg("Störung: {machine} steht", machine=event.machine.name),
                  (event.text or "").strip().splitlines()[0][:150] if (event.text or "").strip() else None,
                  url_for("machines.machine_detail", machine_id=event.machine_id) + "#logbuch",
                  tag=f"fault-{event.id}", exclude=[actor, *exclude])


def purchase_requested(req, actor):
    from .purchases import buyers
    return notify(buyers(), "purchase_new", Msg("Neuer Bestellwunsch") if not req.urgent
                  else Msg("Dringender Bestellwunsch"), req.title,
                  url_for("purchases.purchase_detail", req_id=req.id), tag=f"purchase-{req.id}", exclude=actor)


def purchase_handed_over(req, actor, target):
    return notify([target], "purchase_new", Msg("{name} hat dir eine Bestellung gegeben", name=actor.name),
                  req.title, url_for("purchases.purchase_detail", req_id=req.id), tag=f"purchase-{req.id}",
                  exclude=actor)


def purchase_status(req, actor):
    from .models import PURCHASE_STATUSES
    label = PURCHASE_STATUSES.get(req.status, (req.status,))[0]
    body = req.title
    if req.status == "bestellt" and req.expected_on:
        body = Msg("{title} · Lieferung {date}", title=req.title, date=req.expected_on.strftime("%d.%m."))
    elif req.status == "abgelehnt" and req.buyer_note:
        body = Msg("{title}: {note}", title=req.title, note=req.buyer_note[:120])
    return notify([req.requested_by], "purchase_status", Msg("Bestellung: {status}", status=Msg(label)), body,
                  url_for("purchases.purchase_detail", req_id=req.id), tag=f"purchase-{req.id}", exclude=actor)


def tank_low(before, after):
    """Hoftank fällt unter die Warnschwelle → Besteller benachrichtigen (nur beim Unterschreiten)."""
    from . import fuel
    warn = fuel.settings()["warn"]
    if warn is None or not (before > warn >= after):
        return 0
    return notify([u for u in everyone() if u.is_buyer], "tank",
                  Msg("Hoftank: noch {liters} l", liters=f"{after:.0f}"),
                  Msg("Unter der Warnschwelle von {warn} l – Diesel bestellen.", warn=warn),
                  url_for("fuel.tank"), tag="tank")


# --- Tägliche Nachrichten -------------------------------------------------------

def _job_done(name, day):
    setting = db.session.get(Setting, f"push_job_{name}")
    return setting is not None and setting.value == day.isoformat()


def _mark_job(name, day):
    Setting.put(f"push_job_{name}", day.isoformat())
    db.session.commit()


def _morning_tasks(today):
    from . import tasks
    sent = 0
    for user in editors():
        due = [t for t in tasks.due_now(user, today) if t.due_on is not None]
        if not due:
            continue
        overdue = sum(1 for t in due if t.due_on < today)
        title = Msg("Heute {n} Aufgabe", "Heute {n} Aufgaben", len(due))
        names = ", ".join(t.title for t in due[:3]) + (" …" if len(due) > 3 else "")
        body = Msg("{names} ({n} überfällig)", names=names, n=overdue) if overdue else names
        sent += notify([user], "tasks_due", title, body, url_for("tasks.task_list"), tag="tasks-today")
    return sent


def _maintenance(today):
    """Nur Wartungen melden, die neu fällig geworden sind (sonst käme jeden Morgen dieselbe Nachricht)."""
    from . import machines
    due = machines.due_tasks(("overdue", "soon"))
    keys = {f"{t.id}:{s['due_on']}:{s['due_counter']}": (t, s) for t, s in due}
    setting = db.session.get(Setting, "push_maintenance_seen")
    seen = set(json.loads(setting.value)) if setting and setting.value else set()
    new = [keys[k] for k in keys if k not in seen]
    Setting.put("push_maintenance_seen", json.dumps(sorted(keys)))
    db.session.commit()
    if not new:
        return 0
    # BHKW-Wartungen nur an die, die die Rubrik Biogas sehen
    with_biogas = [u for u in editors() if u.sees_biogas]
    without = [u for u in editors() if not u.sees_biogas]
    sent = _maintenance_msg(with_biogas, new)
    return sent + _maintenance_msg(without, [(t, s) for t, s in new if t.machine.category != machines.BIOGAS_CATEGORY])


def _maintenance_msg(users, new):
    if not users or not new:
        return 0
    if len(new) == 1:
        task, _status = new[0]
        title = Msg("Wartung fällig: {machine}", machine=task.machine.name)
        body, url = task.name, url_for("machines.machine_detail", machine_id=task.machine_id) + "#wartungen"
    else:
        title = Msg("{n} Wartung wird fällig", "{n} Wartungen werden fällig", len(new))
        body = ", ".join(f"{t.machine.name}: {t.name}" for t, _s in new[:4]) + (" …" if len(new) > 4 else "")
        url = url_for("machines.machine_list")
    return notify(users, "maintenance", title, body, url, tag="maintenance")


def _weather_check(now, kinds):
    from . import weather
    data = weather.get_weather(now)
    if not data or data.get("error"):
        return 0
    sent = 0
    if "blight" in kinds and weather.blight_in_season(now.date()) and data["blight"]["level"] == "high":
        # nur beim Umspringen auf Rot (nicht an jedem roten Tag erneut)
        last = db.session.get(Setting, "push_blight_last")
        yesterday = (now.date() - timedelta(days=1)).isoformat()
        if not (last and last.value in (yesterday, now.date().isoformat())):
            sent += notify(everyone(), "blight", Msg("Krautfäule-Ampel: Rot"),
                           Msg("Hutton-Kriterien erfüllt – Bestände kontrollieren."),
                           url_for("main.weather_page"), tag="blight")
        Setting.put("push_blight_last", now.date().isoformat())
        db.session.commit()
    frost = data.get("frost")
    if "frost" in kinds and frost and frost["level"] == "frost" and frost["from"] <= now + timedelta(hours=24):
        at = frost["at"].astimezone(now.tzinfo)
        sent += notify(everyone(), "frost", Msg("Frostwarnung: bis {temp} °C", temp=f"{frost['min']:.0f}"),
                       Msg("Tiefster Wert am {date} gegen {time} Uhr.", date=at.strftime("%d.%m."),
                           time=at.strftime("%H")), url_for("main.weather_page"), tag="frost")
    return sent


def _harvest_report(today, tz):
    """Abends: was heute befüllt wurde – nur, wenn überhaupt geerntet wurde."""
    from . import storage
    report = storage.harvest_report(today, tz)
    if not report["count"]:
        return 0
    tons = f"{report['kg'] / 1000:.1f}".replace(".", ",")
    title = Msg("Ernte heute: {n} Kiste (≈ {t} t)", "Ernte heute: {n} Kisten (≈ {t} t)", report["count"], t=tons)
    body = ", ".join(f"{v['label'] or '–'} {v['boxes']}" for v in report["varieties"][:4])
    return notify(editors(), "harvest_report", title, body,
                  url_for("main.harvest_report_page", datum=today.isoformat()), tag="harvest-report")


def daily(app, now=None):
    """Aus dem Zeitplan alle paar Minuten aufgerufen; jede Aufgabe läuft höchstens einmal am Tag."""
    # Anfrage-Kontext nur für url_for() (die Links in den Nachrichten sind relativ);
    # er bringt einen App-Kontext mit, falls noch keiner offen ist
    with app.test_request_context("/"):
        tz = ZoneInfo(app.config.get("TIMEZONE", "Europe/Berlin"))
        now = now or datetime.now(tz)
        today = now.date()
        if now.hour >= MORNING_HOUR and not _job_done("morning", today):
            _mark_job("morning", today)          # vorher markieren: lieber eine Nachricht verpassen als doppelt
            _morning_tasks(today)
            _maintenance(today)
            _weather_check(now, {"blight"})
            from . import biogas, rounds
            rounds.morning_due(today)
            if today.weekday() == 0:             # montags: Wochenbericht der Biogasanlage
                biogas.week_push(today)
        if now.hour >= FROST_HOUR and not _job_done("frost", today):
            _mark_job("frost", today)
            _weather_check(now, {"frost"})
        if now.hour >= EVENING_HOUR and not _job_done("harvest", today):
            _mark_job("harvest", today)
            _harvest_report(today, tz)
        from . import chickens
        chickens.evening_check(now, _job_done, _mark_job)     # Eier eintragen? Hühnerklappe zu?
        from . import climate
        climate.check(now, _job_done, _mark_job)              # Lagerklima außerhalb der Grenzwerte

