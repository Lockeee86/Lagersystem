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
from .machines import add_months
from .storage import StorageError

AREA = "huehner"
EGGS_MAX = 500                                   # Plausibilität je Eintrag
# Erwartete Legeleistung (% je Huhn und Tag) nach Alter in Wochen – gerundete Richtwerte nach den veröffentlichten
# Leistungsdaten der Zuchtunternehmen für braune Legehybriden in Boden-/Freilandhaltung (z. B. Lohmann Brown-Classic:
# 50 % mit gut 20 Wochen, Spitze 94–96 %). Unter Idealbedingungen – Winter ohne Licht, Mauser, Hobbyhaltung liegen
# meist darunter. Rassehühner legen deutlich weniger und bekommen keine Kurve.
LAYING_CURVE = [(18, 0), (19, 9), (20, 37), (21, 53), (22, 73), (23, 81), (24, 87), (25, 90), (26, 93), (28, 95),
                (32, 95), (36, 94), (40, 92.5), (45, 91), (50, 89), (55, 87), (60, 85), (65, 83), (70, 80.5),
                (75, 78), (80, 75.5), (85, 73), (90, 70.5), (100, 66), (110, 62)]
HYBRID_WORDS = ("lohmann", "isa", "dekalb", "hy-line", "hyline", "bovans", "novogen", "tetra", "h&n", "shaver",
                "hisex", "babcock", "hybrid")
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
    good = (db.session.query(func.coalesce(func.sum(EggEntry.eggs - EggEntry.cracked), 0))
            .filter(EggEntry.imported.is_(False)).scalar())       # alte Listen: längst verteilt
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
    rows.sort(key=lambda r: (r["last_day"] is not None, r["last_day"] or date.min, r["recent"],
                             r["household"].position))
    return rows


# --- Herde --------------------------------------------------------------------------------

def add_flock_event(day, reason, count, user, breed=None, hatched_on=None, note=None, age_months=None):
    """Zu-/Abgang. Bei Zugängen das Alter: `age_months` (Alter am Tag des Zugangs) oder `hatched_on`;
    selbst geschlüpfte Küken ohne Angabe sind 0 Monate alt."""
    if reason not in FLOCK_REASONS:
        raise StorageError(_("Bitte einen Grund wählen."))
    count = _count(count, 1, 1000)
    sign = FLOCK_REASONS[reason][0]
    if age_months is not None:
        age_months = _count(age_months, 0, 120, _("Alter bitte zwischen 0 und 120 Monaten."))
        hatched_on = add_months(_day(day), -age_months)
    elif hatched_on is None and reason == "geschluepft":
        hatched_on = _day(day)
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


def age_months(hatched_on, day):
    """Alter in vollen Monaten."""
    months = (day.year - hatched_on.year) * 12 + day.month - hatched_on.month
    return max(months - (day.day < hatched_on.day), 0)


def cohorts(day=None):
    """Gruppen mit dem, was am Tag `day` noch da ist: [{"event", "left", "age", "start_age"}], neueste zuerst.
    Abgänge hängen an keiner Gruppe – sie werden grob den ältesten Hühnern abgezogen (Gruppen ohne Alter zuerst)."""
    day = day or date.today()
    events = (FlockEvent.query.filter(FlockEvent.day <= day)
              .order_by(FlockEvent.day, FlockEvent.id).all())
    groups_, left = [], {}
    for e in events:
        if e.change > 0:
            groups_.append(e)
            left[e.id] = e.change
            continue
        missing = -e.change
        for g in sorted(groups_, key=lambda g: (g.hatched_on is not None, g.hatched_on or g.day, g.id)):
            take = min(left[g.id], missing)
            left[g.id] -= take
            missing -= take
            if not missing:
                break
    return [{"event": g, "left": left[g.id], "age": age_months(g.hatched_on, day) if g.hatched_on else None,
             "start_age": age_months(g.hatched_on, g.day) if g.hatched_on else None,
             "expected": round(expected_rate((day - g.hatched_on).days))
             if g.hatched_on and is_hybrid(g.breed) else None}
            for g in reversed(groups_)]


def is_hybrid(breed):
    """Legehybride (bekannte Zuchtlinien) – nur für sie gibt es die erwartete Legeleistung."""
    lower = (breed or "").lower()
    return any(word in lower for word in HYBRID_WORDS)


def expected_rate(age_days):
    """Richtwert Legeleistung in % für ein Alter in Tagen (zwischen den Stützpunkten linear)."""
    weeks = age_days / 7
    if weeks <= LAYING_CURVE[0][0]:
        return 0.0
    for (w0, r0), (w1, r1) in zip(LAYING_CURVE, LAYING_CURVE[1:]):
        if weeks <= w1:
            return r0 + (r1 - r0) * (weeks - w0) / (w1 - w0)
    return float(LAYING_CURVE[-1][1])


def expected_laying(start, end):
    """Erwartete Legeleistung der Herde im Zeitraum (Mitte), gewichtet nach Hühnern – nur wenn mindestens 80 % der
    Hühner Legehybriden mit bekanntem Alter sind, sonst None."""
    mid = start + (end - start) / 2
    present = [c for c in cohorts(mid) if c["left"] > 0]
    total = sum(c["left"] for c in present)
    known = [c for c in present if c["event"].hatched_on and is_hybrid(c["event"].breed)]
    hens = sum(c["left"] for c in known)
    if not total or hens < 0.8 * total:
        return None
    return round(sum(c["left"] * expected_rate((mid - c["event"].hatched_on).days) for c in known) / hens)


def flock_age(day=None):
    """Durchschnittsalter der Herde in Monaten (nur Hühner mit bekanntem Alter) oder None."""
    day = day or date.today()
    known = [(c["left"], (day - c["event"].hatched_on).days) for c in cohorts(day)
             if c["left"] > 0 and c["event"].hatched_on]
    hens = sum(n for n, _d in known)
    return round(sum(n * d for n, d in known) / hens / 30.44, 1) if hens else None


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
        hen_days = _hen_days(start, end)
        rows.append({"month": start, "eggs": n, "cracked": cracked.get(key, 0), "given": given.get(key, 0),
                     "days": days, "hen_days": hen_days,
                     "per_day": n / days, "rate": laying_rate(n, hen_days), "age": flock_age(end),
                     "expected": expected_laying(start, end),
                     "feed_kg": kg, "feed_cost": cost,
                     "per_egg": (Decimal(cost) / n) if cost and n else None})
        start = _month_end(start) + timedelta(days=1)
    # erst ab dem ersten Monat mit Einträgen
    while rows and not (rows[0]["eggs"] or rows[0]["given"] or rows[0]["feed_kg"]):
        rows.pop(0)
    return list(reversed(rows))


# --- Auswertung über alle Jahre -----------------------------------------------------------

MONTH_NAMES = (N_("Jan"), N_("Feb"), N_("Mär"), N_("Apr"), N_("Mai"), N_("Jun"), N_("Jul"), N_("Aug"), N_("Sep"),
               N_("Okt"), N_("Nov"), N_("Dez"))
CHART_W, CHART_H = 1000, 220


def first_day():
    days = [d for d in (db.session.query(func.min(EggEntry.day)).scalar(),
                        db.session.query(func.min(FlockEvent.day)).scalar()) if d]
    return min(days) if days else None


def _daily_totals():
    return {d: int(n) for d, n in db.session.query(EggEntry.day, func.sum(EggEntry.eggs)).group_by(EggEntry.day)}


def _records(daily, start, today):
    """Bester Tag, beste Woche (7 Tage), längste Pause ohne Eier (zwischen dem ersten und letzten Eintrag)."""
    if not daily:
        return None
    best_day = max(daily.items(), key=lambda kv: (kv[1], kv[0]))
    first, last = min(daily), max(daily)
    window, best_week, best_week_end = 0, 0, None
    gap = longest = 0
    gap_end = longest_end = None
    day = first
    while day <= last:
        window += daily.get(day, 0) - daily.get(day - timedelta(days=7), 0)
        if window > best_week:
            best_week, best_week_end = window, day
        if daily.get(day, 0) == 0:
            gap += 1
            gap_end = day
            if gap > longest:
                longest, longest_end = gap, gap_end
        else:
            gap = 0
        day += timedelta(days=1)
    return {"best_day": best_day, "best_week": best_week,
            "best_week_from": best_week_end - timedelta(days=6) if best_week_end else None,
            "best_week_to": best_week_end, "longest_gap": longest,
            "gap_from": longest_end - timedelta(days=longest - 1) if longest_end else None, "gap_to": longest_end}


def _line(values, top):
    """[(x, y)] für eine Linie im Diagramm (None = Lücke)."""
    count = len(values)
    return [(round(CHART_W * i / max(count - 1, 1), 1), round(CHART_H - CHART_H * v / top, 1) if v is not None else None)
            for i, v in enumerate(values)]


def _rolling(daily, start, end, days=7):
    """Gleitender Durchschnitt der Eier am Tag über `days` Tage."""
    values, window, day = [], 0, start
    while day <= end:
        window += daily.get(day, 0) - daily.get(day - timedelta(days=days), 0)
        values.append((day, window / days))
        day += timedelta(days=1)
    return values


# --- Durchgänge, Prognose, Warnung --------------------------------------------------------
# Der Stall wird vor jedem Zugang geleert: ein Durchgang reicht vom Einstallen (Bestand 0 → >0) bis der Bestand
# wieder 0 ist. Durchgänge sind unterschiedlich gut – Prognose und Warnung beziehen sich deshalb auf den laufenden
# Durchgang selbst, nicht aufs Vorjahr (andere Herde).

DEATHS = ("verendet", "raubtier")   # Verluste; abgegeben/geschlachtet/sonstiger Abgang zählen extra
DROP_DAYS = 14            # die letzten zwei Wochen …
DROP_BEFORE = 28          # … gegen die vier Wochen davor
DROP_FACTOR = 0.75        # weniger als 75 % davon …
DROP_POINTS = 10          # … und mindestens 10 Prozentpunkte weniger
DROP_SETTLE_DAYS = 70     # nicht in den ersten 10 Wochen eines Durchgangs (Legebeginn)


def _eggs_between(start, end):
    return int(db.session.query(func.coalesce(func.sum(EggEntry.eggs), 0))
               .filter(EggEntry.day >= start, EggEntry.day <= end).scalar())


def rate_between(start, end):
    return laying_rate(_eggs_between(start, end), _hen_days(start, end))


def batches(today=None):
    """Durchgänge, neuester zuerst: {start, end (None = läuft), end_reason, added, breeds, age_start,
    losses {Grund: n} (ohne das Leeren am Ende), lost, lost_pct, eggs, hen_days, rate, per_hen, per_hen_year,
    best_month, days, expected}."""
    today = today or date.today()
    events = FlockEvent.query.order_by(FlockEvent.day, FlockEvent.id).all()
    result, size, current = [], 0, None
    for e in events:
        if size <= 0 and e.change > 0:
            current = {"start": e.day, "end": None, "end_reason": None, "added": 0, "breeds": [], "age_start": None,
                       "losses": {}, "removed": {}, "events": []}
            result.append(current)
        size += e.change
        if current is None:
            continue
        current["events"].append(e)
        if e.change > 0:
            current["added"] += e.change
            if e.breed and e.breed not in current["breeds"]:
                current["breeds"].append(e.breed)
            if current["age_start"] is None and e.hatched_on:
                current["age_start"] = age_months(e.hatched_on, e.day)
        elif size > 0 or e.reason in DEATHS:   # während des Durchgangs (das Leeren am Ende zählt nicht)
            target = current["losses"] if e.reason in DEATHS else current["removed"]
            target[e.reason_label] = target.get(e.reason_label, 0) - e.change
        if size <= 0:
            current["end"], current["end_reason"] = e.day, e.reason_label
            current = None
            size = 0
    for b in result:
        end = b["end"] or today
        b["days"] = (end - b["start"]).days + 1
        b["eggs"] = _eggs_between(b["start"], end)
        b["hen_days"] = _hen_days(b["start"], end)
        b["rate"] = laying_rate(b["eggs"], b["hen_days"])
        b["per_hen"] = round(b["eggs"] / b["added"]) if b["added"] else None
        b["per_hen_year"] = round(b["eggs"] / (b["hen_days"] / 365)) if b["hen_days"] else None
        b["lost"] = sum(b["losses"].values())
        b["removed_n"] = sum(b["removed"].values())
        b["lost_pct"] = round(100 * b["lost"] / b["added"]) if b["added"] else None
        b["hens"] = b["hen_days"] / b["days"] if b["days"] else 0
        b["expected"] = expected_laying(b["start"], end)
        best, month = None, b["start"].replace(day=1)
        while month <= end:
            m_end = min(_month_end(month), end)
            rate = rate_between(max(month, b["start"]), m_end)
            if rate is not None and (best is None or rate > best[1]):
                best = (month, rate)
            month = m_end + timedelta(days=1)
        b["best_month"] = best
        b.pop("events")
    return list(reversed(result))


def current_batch(today=None):
    today = today or date.today()
    found = batches(today)
    return found[0] if found and found[0]["end"] is None and flock_size(today) > 0 else None


def forecast(today=None):
    """Erwartete Eier für den nächsten Monat: Legeleistung der letzten 4 Wochen dieses Durchgangs × Hühner × Tage,
    angepasst um den üblichen Unterschied zwischen diesem und dem nächsten Monat (aus den Vorjahren).
    None ohne laufenden Durchgang oder zu wenig Daten."""
    today = today or date.today()
    batch = current_batch(today)
    if batch is None:
        return None
    recent_start = max(batch["start"], today - timedelta(days=27))
    if (today - recent_start).days < 13:
        return None
    base = rate_between(recent_start, today)
    hens = flock_size(today)
    if base is None or not hens:
        return None
    start = _month_end(today) + timedelta(days=1)
    end = _month_end(start)
    ratios = []
    for back in range(1, 6):                       # Jahreszeit: wie ändert sich die Legeleistung meist von Monat zu Monat?
        this = today.replace(year=today.year - back, day=1)
        nxt = start.replace(year=start.year - back)
        a = rate_between(this, _month_end(this))
        b = rate_between(nxt, _month_end(nxt))
        if a and b:
            ratios.append(b / a)
    season = sum(ratios) / len(ratios) if ratios else 1.0
    season = min(max(season, 0.5), 1.5)
    rate = min(base * season, 100)
    days = (end - start).days + 1
    eggs = round(hens * days * rate / 100)
    return {"month": start, "eggs": eggs, "low": round(eggs * 0.85), "high": round(eggs * 1.15), "rate": round(rate),
            "base": base, "season": round(season, 2), "years": len(ratios), "hens": hens}


def laying_drop(today=None):
    """Legeleistung der letzten zwei Wochen deutlich unter den vier Wochen davor (im laufenden Durchgang)?
    {"recent", "before", "since"} oder None."""
    today = today or date.today()
    batch = current_batch(today)
    if batch is None or (today - batch["start"]).days < DROP_SETTLE_DAYS:
        return None
    recent_start = today - timedelta(days=DROP_DAYS - 1)
    before_end = recent_start - timedelta(days=1)
    before_start = before_end - timedelta(days=DROP_BEFORE - 1)
    if before_start < batch["start"]:
        return None
    recent, before = rate_between(recent_start, today), rate_between(before_start, before_end)
    if recent is None or not before:
        return None
    if recent < before * DROP_FACTOR and before - recent >= DROP_POINTS:
        return {"recent": recent, "before": before, "since": recent_start}
    return None


def analysis(today=None):
    """Alles für die Seite Auswertung: Jahre, Monate, Jahreszeiten-Kurven, Legeleistung, Rekorde, Verluste, Haushalte."""
    today = today or date.today()
    start = first_day()
    if start is None:
        return None
    count = (today.year - start.year) * 12 + today.month - start.month + 1
    month_rows = months(today, count)                               # neuester zuerst
    years = {}
    for m in month_rows:
        y = years.setdefault(m["month"].year, {"year": m["month"].year, "eggs": 0, "cracked": 0, "given": 0,
                                               "hen_days": 0, "days": 0, "feed_kg": 0.0, "feed_cost": 0.0,
                                               "expected": [], "dead": 0, "losses": {}})
        for key in ("eggs", "cracked", "given", "hen_days", "days"):
            y[key] += m[key]
        y["feed_kg"] += float(m["feed_kg"] or 0)
        y["feed_cost"] += float(m["feed_cost"] or 0)
        if m["expected"] is not None:
            y["expected"].append((m["expected"], m["hen_days"]))
    for event in FlockEvent.query.filter(FlockEvent.change < 0, FlockEvent.reason.in_(DEATHS)):
        y = years.get(event.day.year)
        if y is not None:
            y["dead"] += -event.change
            y["losses"][event.reason_label] = y["losses"].get(event.reason_label, 0) - event.change
    for y in years.values():
        weight = sum(h for _e, h in y["expected"])
        y["expected"] = round(sum(e * h for e, h in y["expected"]) / weight) if weight else None
        y["rate"] = laying_rate(y["eggs"], y["hen_days"])
        y["hens"] = y["hen_days"] / y["days"] if y["days"] else 0                # Ø Hühner im Jahr
        y["per_hen"] = round(y["eggs"] / (y["hen_days"] / 365)) if y["hen_days"] else None   # Eier je Huhn und Jahr
        y["per_day"] = y["eggs"] / y["days"] if y["days"] else 0
        y["cracked_pct"] = round(100 * y["cracked"] / y["eggs"], 1) if y["eggs"] else None
        y["per_egg"] = y["feed_cost"] / (y["eggs"] - y["cracked"]) if y["feed_cost"] and y["eggs"] > y["cracked"] else None
        y["feed_per_hen"] = round(y["feed_kg"] * 1000 / y["hen_days"]) if y["feed_kg"] and y["hen_days"] else None
    year_rows = sorted(years.values(), key=lambda y: y["year"], reverse=True)

    # Jahreszeiten: Ø Eier am Tag je Monat, eine Linie je Jahr
    by_month = {(m["month"].year, m["month"].month): m for m in month_rows}
    top = max([m["per_day"] for m in month_rows] + [1])
    season = [{"year": y["year"], "points": _line([by_month[(y["year"], mo)]["per_day"]
                                                   if (y["year"], mo) in by_month else None for mo in range(1, 13)],
                                                  top * 1.1)} for y in sorted(year_rows, key=lambda y: y["year"])]
    # Legeleistung und Richtwert je Monat (ältester zuerst)
    chrono = list(reversed(month_rows))
    rates = [m["rate"] for m in chrono]
    expected = [m["expected"] for m in chrono]
    rate_chart = {"rate": _line(rates, 100), "expected": _line(expected, 100),
                  "labels": [(i, m["month"]) for i, m in enumerate(chrono) if m["month"].month == 1 or i == 0],
                  "count": len(chrono)}
    daily = _daily_totals()
    rolling = _rolling(daily, max(start, min(daily) if daily else start), today) if daily else []
    roll_top = max([v for _d, v in rolling] + [1]) * 1.1
    trend = {"points": _line([v for _d, v in rolling], roll_top), "top": roll_top,
             "first": rolling[0][0] if rolling else None, "last": today}
    shares = (db.session.query(func.extract("year", EggShare.day).label("y"), Household.name, func.sum(EggShare.eggs))
              .join(Household).group_by("y", Household.name).all())
    households = {}
    for year, name, eggs in shares:
        households.setdefault(int(year), []).append((name, int(eggs)))
    for rows in households.values():
        rows.sort(key=lambda r: -r[1])
    return {"years": year_rows, "months": month_rows, "season": season, "season_top": top * 1.1,
            "batches": batches(today), "forecast": forecast(today), "drop": laying_drop(today),
            "rate_chart": rate_chart, "trend": trend, "records": _records(daily, start, today),
            "households": households, "start": start}


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
    return {"today": eggs_on(today), "flock": size, "age": flock_age(today), "stock": stock(), "blocked": blocked(today),
            "drop": laying_drop(today),
            "next": order[0] if order else None, "sensor": coop.status(),
            "week_eggs": int(week_eggs), "week_rate": laying_rate(int(week_eggs), _hen_days(week_start, today)),
            "week_expected": expected_laying(week_start, today),
            "checks": checks, "overdue": sum(1 for c in checks if c["overdue"]),
            "issues": sum(len(c["open"]) for c in checks)}


# --- Sonnenuntergang und Push -------------------------------------------------------------

def _sun(day, lat, lon, rising):
    """Sonnenaufgang bzw. -untergang (UTC) nach der NOAA-Näherung; None bei Polartag/-nacht."""
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
    ha = math.degrees(math.acos(cos_ha))
    minutes = 720 - 4 * (lon + ha if rising else lon - ha) - eqtime
    return datetime.combine(day, time(), tzinfo=timezone.utc) + timedelta(minutes=minutes)


def sunset(day, lat, lon):
    return _sun(day, lat, lon, rising=False)


def sunrise(day, lat, lon):
    return _sun(day, lat, lon, rising=True)


def _location():
    from .weather import parse_location
    return parse_location(Setting.all().get("farm_location"))


def farm_sunrise(day):
    location = _location()
    return sunrise(day, *location) if location else None


def farm_sunset(day):
    location = _location()
    return sunset(day, *location) if location else None


def evening_check(now, done, mark):
    """Aus `push.daily`: abends an fehlende Eier erinnern, nach Sonnenuntergang an die Klappe.
    `done(key, day)` / `mark(key, day)` merken sich, was heute schon verschickt wurde."""
    today = now.date()
    week = today - timedelta(days=today.weekday())
    if now.hour >= EGG_REMINDER_HOUR and not done("laying_drop", week):
        drop = laying_drop(today)
        if drop:                                   # höchstens einmal je Woche
            mark("laying_drop", week)
            push.notify([u for u in User.query.filter_by(active=True) if u.sees_chickens], "laying_drop",
                        Msg("Hühner: Legeleistung fällt ab"),
                        Msg("Letzte 2 Wochen {recent} %, davor {before} % – Mauser, Licht, Milben oder Futter prüfen.",
                            recent=drop["recent"], before=drop["before"]),
                        url_for("chickens.analysis"), tag=f"laying-drop-{week.isoformat()}")
    if now.hour >= EGG_REMINDER_HOUR and not done("eggs", today):
        mark("eggs", today)
        if flock_size(today) > 0 and not EggEntry.query.filter_by(day=today).first():
            push.notify(keepers(), "eggs_missing", Msg("Hühner: noch keine Eier eingetragen"),
                        Msg("Bitte die Eier von heute eintragen."), url_for("chickens.overview", eier=1),
                        tag=f"eggs-{today.isoformat()}")
    from . import coop
    sensor = coop.status()
    known = sensor is not None and not sensor["offline"] and sensor["state"] is not None
    set_at = farm_sunset(today)
    if set_at and now >= set_at + COOP_DELAY and not done("coop", today):
        local = set_at.astimezone(now.tzinfo)
        if known and sensor["state"] == "zu":
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
    if flock_size(today) > 0:
        coop.door_watch(now, done, mark, keepers(), sunrise=farm_sunrise(today))
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
