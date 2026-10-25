"""Hühner: Eier-Buch, Verteilung an Haushalte, Herde, Futter/Kosten, Stallbuch, Stallkontrolle.

Die Rubrik sieht nur, wer in der Benutzerverwaltung freigeschaltet ist (`User.chickens`); eintragen dürfen
freigeschaltete Mitarbeiter (`User.keeps_chickens`). Wie die übrigen Logik-Module ändern die Funktionen nur die
Session; Push-Nachrichten (`evening_check`) laufen aus dem Zeitplan.
Die Stallkontrolle nutzt den Rundgang (`CheckStation.area == "huehner"`), Aufgaben tragen `Task.area = "huehner"`.
"""
import math
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from flask import url_for
from sqlalchemy import func

from . import push
from .extensions import db
from .i18n import N_, gettext as _
from .models import (FLOCK_REASONS, CheckStation, EggEntry, EggShare, FeedEntry, FlockEvent, FlockTreatment,
                     Household, Setting, Task, User)
from .push import Msg
from .storage import StorageError

AREA = "huehner"
EGGS_MAX = 500                                   # Plausibilität je Eintrag
EGG_REMINDER_HOUR = 18                           # abends erinnern, wenn noch keine Eier eingetragen sind
COOP_DELAY = timedelta(minutes=15)               # „Klappe zu?“ kurz nach Sonnenuntergang
FEED_DAYS = 90                                   # Futterverbrauch über diesen Zeitraum rechnen

# Vorschläge (Titel, Wiederholung) für wiederkehrende Aufgaben
TASK_SUGGESTIONS = [
    (N_("Hühnerstall ausmisten"), "woechentlich"),
    (N_("Hühner: Milben kontrollieren"), "monatlich"),
    (N_("Hühner: Wurmkur"), "vierteljaehrlich"),
    (N_("Hühner: Impfung"), "halbjaehrlich"),
]
STATION_SUGGESTION = (N_("Hühnerstall"), [N_("Wasser sauber und voll"), N_("Futter aufgefüllt"),
                                          N_("Nester sauber"), N_("Tiere munter, keine Auffälligkeiten"),
                                          N_("Zaun und Klappe in Ordnung")], 1)


def keepers():
    return [u for u in User.query.filter_by(active=True) if u.keeps_chickens]


def _count(value, minimum=0, maximum=EGGS_MAX, message=None):
    if value is None or not minimum <= value <= maximum:
        raise StorageError(message or _("Bitte eine Anzahl zwischen {a} und {b} angeben.", a=minimum, b=maximum))
    return value


def _note(text, length=200):
    return " ".join((text or "").split())[:length] or None


def _day(day):
    if day is None or day > date.today():
        raise StorageError(_("Bitte ein Datum angeben (nicht in der Zukunft)."))
    return day


# --- Eier ---------------------------------------------------------------------------------

def add_eggs(day, eggs, cracked, note, user):
    eggs = _count(eggs, 1)
    cracked = _count(cracked or 0, 0, eggs, _("Kaputte Eier können nicht mehr sein als eingesammelte."))
    entry = EggEntry(day=_day(day), eggs=eggs, cracked=cracked, note=_note(note), user_id=user.id if user else None)
    db.session.add(entry)
    db.session.flush()
    return entry


def give_eggs(day, household, eggs, note, user):
    if household is None or not household.active:
        raise StorageError(_("Bitte einen Haushalt wählen."))
    share = EggShare(day=_day(day), household_id=household.id, eggs=_count(eggs, 1), note=_note(note),
                     user_id=user.id if user else None)
    db.session.add(share)
    db.session.flush()
    return share


def eggs_on(day):
    """{"eggs", "cracked"} für einen Tag."""
    eggs, cracked = (db.session.query(func.coalesce(func.sum(EggEntry.eggs), 0),
                                      func.coalesce(func.sum(EggEntry.cracked), 0))
                     .filter(EggEntry.day == day).one())
    return {"eggs": int(eggs), "cracked": int(cracked)}


def stock():
    """Eier im Vorrat: gute Eier insgesamt minus verteilte (nie unter 0)."""
    good = db.session.query(func.coalesce(func.sum(EggEntry.eggs - EggEntry.cracked), 0)).scalar()
    given = db.session.query(func.coalesce(func.sum(EggShare.eggs), 0)).scalar()
    return max(int(good) - int(given), 0)


# --- Haushalte ----------------------------------------------------------------------------

def save_household(household, name, note=None, active=True):
    name = " ".join((name or "").split())[:100]
    if not name:
        raise StorageError(_("Bitte einen Namen angeben."))
    other = Household.query.filter(func.lower(Household.name) == name.lower()).first()
    if other is not None and other is not household:
        raise StorageError(_("{name} gibt es schon.", name=name))
    if household is None:
        household = Household(position=(db.session.query(func.max(Household.position)).scalar() or 0) + 1)
        db.session.add(household)
    household.name, household.note, household.active = name, _note(note), bool(active)
    db.session.flush()
    return household


def households(active_only=True):
    query = Household.query
    if active_only:
        query = query.filter_by(active=True)
    return query.order_by(Household.position, Household.name).all()


def share_summary(start, end):
    """Verteilte Eier je Haushalt im Zeitraum [start, end]: [(Haushalt, Eier)] absteigend."""
    rows = (db.session.query(Household, func.sum(EggShare.eggs))
            .join(EggShare, EggShare.household_id == Household.id)
            .filter(EggShare.day >= start, EggShare.day <= end)
            .group_by(Household.id).order_by(func.sum(EggShare.eggs).desc()).all())
    return [(h, int(n)) for h, n in rows]


def fair_order(today=None, days=30):
    """Aktive Haushalte, wer am längsten nichts bekommen hat zuerst (noch nie → ganz vorn, bei Gleichstand wer
    in den letzten `days` Tagen weniger bekam). Je Haushalt: last_day, last_eggs, days_since, recent, year."""
    today = today or date.today()
    last_rows = {}
    for share in (EggShare.query.join(Household).filter(Household.active.is_(True))
                  .order_by(EggShare.day.desc(), EggShare.id.desc())):
        last_rows.setdefault(share.household_id, share)
    recent = {h.id: n for h, n in share_summary(today - timedelta(days=days - 1), today)}
    year = {h.id: n for h, n in share_summary(today.replace(month=1, day=1), today)}
    rows = []
    for household in households():
        last = last_rows.get(household.id)
        rows.append({"household": household, "last_day": last.day if last else None,
                     "last_eggs": last.eggs if last else None,
                     "days_since": (today - last.day).days if last else None,
                     "recent": recent.get(household.id, 0), "year": year.get(household.id, 0)})
    rows.sort(key=lambda r: (r["last_day"] is not None, r["last_day"] or date.min, r["recent"], r["household"].position))
    return rows


# --- Herde --------------------------------------------------------------------------------

def add_flock_event(day, reason, count, user, breed=None, hatched_on=None, note=None):
    if reason not in FLOCK_REASONS:
        raise StorageError(_("Bitte einen Grund wählen."))
    count = _count(count, 1, 1000)
    sign = FLOCK_REASONS[reason][0]
    if sign < 0 and count > flock_size(day):
        raise StorageError(_("So viele Hühner sind nicht im Bestand."))
    event = FlockEvent(day=_day(day), change=sign * count, reason=reason, breed=_note(breed, 80) if sign > 0 else None,
                       hatched_on=hatched_on if sign > 0 else None, note=_note(note),
                       user_id=user.id if user else None)
    db.session.add(event)
    db.session.flush()
    return event


def flock_size(day=None):
    query = db.session.query(func.coalesce(func.sum(FlockEvent.change), 0))
    if day is not None:
        query = query.filter(FlockEvent.day <= day)
    return int(query.scalar())


def groups():
    """Zugänge mit Rasse/Schlupf als „Gruppen“ (für Alter und Rasse), neueste zuerst."""
    return FlockEvent.query.filter(FlockEvent.change > 0).order_by(FlockEvent.day.desc()).all()


def _hen_days(start, end):
    """Summe der Hühner je Tag im Zeitraum (für Legeleistung und Futter je Huhn)."""
    events = FlockEvent.query.filter(FlockEvent.day <= end).order_by(FlockEvent.day).all()
    size = sum(e.change for e in events if e.day < start)
    by_day = {}
    for e in events:
        if e.day >= start:
            by_day[e.day] = by_day.get(e.day, 0) + e.change
    total, day = 0, start
    while day <= end:
        size += by_day.get(day, 0)
        total += max(size, 0)
        day += timedelta(days=1)
    return total


def laying_rate(eggs, hen_days):
    return round(100 * eggs / hen_days) if hen_days else None


# --- Futter -------------------------------------------------------------------------------

def add_feed(day, kg, cost, product, note, user):
    if kg is None or not Decimal("0.1") <= kg <= Decimal("5000"):
        raise StorageError(_("Bitte die Menge in kg angeben."))
    entry = FeedEntry(day=_day(day), kg=kg, cost=cost, product=_note(product, 100), note=_note(note),
                      user_id=user.id if user else None)
    db.session.add(entry)
    db.session.flush()
    return entry


def feed_per_hen(today=None, days=FEED_DAYS):
    """Gramm Futter je Huhn und Tag über die letzten `days` Tage (gekauft ÷ Hühnertage) – grob."""
    today = today or date.today()
    start = today - timedelta(days=days - 1)
    kg = db.session.query(func.coalesce(func.sum(FeedEntry.kg), 0)).filter(FeedEntry.day >= start).scalar()
    hen_days = _hen_days(start, today)
    return round(float(kg) * 1000 / hen_days) if hen_days and kg else None


# --- Stallbuch ----------------------------------------------------------------------------

def add_treatment(day, product, reason, withdrawal_days, note, user):
    product = _note(product, 120)
    if not product:
        raise StorageError(_("Bitte das Mittel angeben."))
    withdrawal_days = _count(withdrawal_days or 0, 0, 365, _("Wartezeit bitte zwischen 0 und 365 Tagen."))
    entry = FlockTreatment(day=_day(day), product=product, reason=_note(reason), withdrawal_days=withdrawal_days,
                           note=_note(note, 300), user_id=user.id if user else None)
    db.session.add(entry)
    db.session.flush()
    return entry


def blocked(today=None):
    """Laufende Wartezeit: die Behandlung, die am längsten sperrt (bis einschl. `blocked_until`) – sonst None."""
    today = today or date.today()
    running = [t for t in FlockTreatment.query.filter(FlockTreatment.withdrawal_days > 0,
                                                      FlockTreatment.day >= today - timedelta(days=365))
               if t.blocked_until >= today]
    return max(running, key=lambda t: t.blocked_until) if running else None


# --- Auswertung ---------------------------------------------------------------------------

def _month_start(day):
    return day.replace(day=1)


def _month_end(day):
    nxt = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def _monthly(day_col, aggregate, first):
    """[(JJJJMM, Wert …)] je Monat ab `first`."""
    key = (func.extract("year", day_col) * 100 + func.extract("month", day_col)).label("ym")
    aggregates = aggregate if isinstance(aggregate, tuple) else (aggregate,)
    rows = db.session.query(key, *aggregates).filter(day_col >= first).group_by(key).all()
    return [(int(row[0]), *row[1:]) for row in rows]


def months(today=None, count=12):
    """Je Monat (neuester zuerst): Eier, kaputt, verteilt, Ø am Tag, Legeleistung, Futter kg/€, € je Ei."""
    today = today or date.today()
    first = _month_start(today)
    for _i in range(count - 1):
        first = _month_start(first - timedelta(days=1))
    eggs = {k: int(v) for k, v in _monthly(EggEntry.day, func.sum(EggEntry.eggs), first)}
    cracked = {k: int(v) for k, v in _monthly(EggEntry.day, func.sum(EggEntry.cracked), first)}
    given = {k: int(v) for k, v in _monthly(EggShare.day, func.sum(EggShare.eggs), first)}
    feed = {k: (kg, cost) for k, kg, cost in _monthly(FeedEntry.day, (func.sum(FeedEntry.kg), func.sum(FeedEntry.cost)),
                                                       first)}
    rows, start = [], first
    while start <= today:
        end = min(_month_end(start), today)
        key = start.year * 100 + start.month
        n, days = eggs.get(key, 0), (end - start).days + 1
        kg, cost = feed.get(key, (None, None))
        rows.append({"month": start, "eggs": n, "cracked": cracked.get(key, 0), "given": given.get(key, 0),
                     "per_day": n / days, "rate": laying_rate(n, _hen_days(start, end)),
                     "feed_kg": kg, "feed_cost": cost,
                     "per_egg": (Decimal(cost) / n) if cost and n else None})
        start = _month_end(start) + timedelta(days=1)
    # erst ab dem ersten Monat mit Einträgen
    while rows and not (rows[0]["eggs"] or rows[0]["given"] or rows[0]["feed_kg"]):
        rows.pop(0)
    return list(reversed(rows))


def recent_days(today=None, days=30):
    """[(Tag, Eier)] der letzten Tage (für die Kurve), ältester zuerst."""
    today = today or date.today()
    start = today - timedelta(days=days - 1)
    totals = dict(db.session.query(EggEntry.day, func.sum(EggEntry.eggs)).filter(EggEntry.day >= start)
                  .group_by(EggEntry.day).all())
    return [(start + timedelta(days=i), int(totals.get(start + timedelta(days=i), 0))) for i in range(days)]


def stations():
    return (CheckStation.query.filter_by(area=AREA, active=True)
            .order_by(CheckStation.position, CheckStation.code).all())


def open_tasks():
    from .tasks import _sort_key
    return sorted(Task.query.filter(Task.area == AREA, Task.done_at.is_(None)), key=_sort_key)


def summary(today=None):
    """Kurzfassung für Startseite und Übersicht."""
    from . import rounds
    today = today or date.today()
    week_start = today - timedelta(days=6)
    week_eggs = db.session.query(func.coalesce(func.sum(EggEntry.eggs), 0)).filter(EggEntry.day >= week_start).scalar()
    size = flock_size(today)
    checks = [{"station": s, **rounds.status(s)} for s in stations()]
    from . import coop
    order = fair_order(today)
    return {"today": eggs_on(today), "flock": size, "stock": stock(), "blocked": blocked(today),
            "next": order[0] if order else None, "sensor": coop.status(),
            "week_eggs": int(week_eggs), "week_rate": laying_rate(int(week_eggs), _hen_days(week_start, today)),
            "checks": checks, "overdue": sum(1 for c in checks if c["overdue"]),
            "issues": sum(len(c["open"]) for c in checks)}


# --- Sonnenuntergang und Push -------------------------------------------------------------

def sunset(day, lat, lon):
    """Sonnenuntergang (UTC) nach der NOAA-Näherung; None bei Polartag/-nacht."""
    n = day.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) - 0.006758 * math.cos(2 * gamma)
            + 0.000907 * math.sin(2 * gamma) - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    lat_r = math.radians(lat)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(decl))) - math.tan(lat_r) * math.tan(decl)
    if not -1 <= cos_ha <= 1:
        return None
    minutes = 720 - 4 * (lon - math.degrees(math.acos(cos_ha))) - eqtime
    return datetime.combine(day, time(), tzinfo=timezone.utc) + timedelta(minutes=minutes)


def farm_sunset(day):
    from .weather import parse_location
    location = parse_location(Setting.all().get("farm_location"))
    return sunset(day, *location) if location else None


def evening_check(now, done, mark):
    """Aus `push.daily`: abends an fehlende Eier erinnern, nach Sonnenuntergang an die Klappe.
    `done(key, day)` / `mark(key, day)` merken sich, was heute schon verschickt wurde."""
    today = now.date()
    if now.hour >= EGG_REMINDER_HOUR and not done("eggs", today):
        mark("eggs", today)
        if flock_size(today) > 0 and not EggEntry.query.filter_by(day=today).first():
            push.notify(keepers(), "eggs_missing", Msg("Hühner: noch keine Eier eingetragen"),
                        Msg("Bitte die Eier von heute eintragen."), url_for("chickens.overview", eier=1),
                        tag=f"eggs-{today.isoformat()}")
    from . import coop
    sensor = coop.status()
    known = sensor is not None and not sensor["offline"] and sensor["door"] is not None
    set_at = farm_sunset(today)
    if set_at and now >= set_at + COOP_DELAY and not done("coop", today):
        local = set_at.astimezone(now.tzinfo)
        if known and not sensor["door"]:
            mark("coop", today)                    # Sensor meldet „zu“ – keine Erinnerung nötig
        elif known:
            # Sensor meldet „offen“: dringend, und von sich aus an alle Hühner-Leute (auch ohne „Klappe zu?“)
            mark("coop", today)
            push.notify(keepers(), "coop_open", Msg("Hühnerklappe ist noch offen!"),
                        Msg("Sonnenuntergang war um {time} Uhr.", time=local.strftime("%H:%M")),
                        url_for("chickens.overview"), tag=f"coop-{today.isoformat()}")
        else:
            mark("coop", today)
            if flock_size(today) > 0:
                push.notify(keepers(), "coop_close", Msg("Hühnerklappe zu?"),
                            Msg("Sonnenuntergang war um {time} Uhr.", time=local.strftime("%H:%M")),
                            url_for("chickens.overview"), tag=f"coop-{today.isoformat()}")
    coop.alerts(now, done, mark, keepers())
    if not done("coop_cleanup", today):
        mark("coop_cleanup", today)
        coop.cleanup()
        db.session.commit()


def issue_reported(check):
    """Mangel an einer Station im Hühnerstall → an die Hühner-Leute."""
    items = ", ".join(check.failed)[:150]
    return push.notify(keepers(), "coop_issue", Msg("Stallkontrolle: {station}", station=check.station.name),
                       items or (check.note or "")[:150], url_for("rounds.station", code=check.station.code),
                       tag=f"check-{check.id}", exclude=check.user)
