"""Kennzahlen-Leiste der Startseite: je Rubrik eine große Zahl und ein Verlauf der letzten 14 Tage."""
from datetime import datetime, time, timedelta, UTC

from flask import url_for
from sqlalchemy import func

from . import db
from .i18n import _, ngettext
from .models import ChpDay, EggEntry, ForestBurial, ForestPlace, ForestTree, Movement
from .utils import local_tz

DAYS = 14


def _days(today):
    return [today - timedelta(days=DAYS - 1 - i) for i in range(DAYS)]


def _labels(today):
    return [d.strftime("%d.%m.") for d in _days(today)]


def _movement_series(today):
    days = _days(today)
    tz = local_tz()
    start = datetime.combine(days[0], time.min, tz).astimezone(UTC)
    counts = dict.fromkeys(days, 0)
    for (stamp,) in db.session.query(Movement.created_at).filter(Movement.created_at >= start):
        day = (stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)).astimezone(tz).date()
        if day in counts:
            counts[day] += 1
    return [counts[d] for d in days]


def _by_day(column, value, today, *filters):
    days = _days(today)
    rows = dict(db.session.query(column, func.sum(value)).filter(column >= days[0], column <= today, *filters)
                .group_by(column).all())
    return [float(rows.get(d) or 0) for d in days]


def _num(value, digits=0):
    text = f"{value:,.{digits}f}"
    return text.replace(",", " ").replace(".", ",").replace(" ", ".")


def cards(user, today, stats, occupancy):
    out = []
    if not user.restricted:
        capacity = sum(o["capacity"] for o in occupancy)
        stored = sum(o["stored"] for o in occupancy)
        percent = round(100 * stored / capacity) if capacity else 0
        out.append({"key": "lager", "icon": "bi-box-seam", "label": _("Kartoffellager"),
                    "value": _num(stats["kg"] / 1000, 1), "unit": "t",
                    "sub": _("{n} Kisten · {p} % belegt", n=stats["filled"], p=percent),
                    "series": _movement_series(today), "series_label": _("Bewegungen je Tag"),
                    "labels": _labels(today), "series_unit": "",
                    "url": url_for("main.warehouse_map")})
    if user.sees_biogas:
        hours = _by_day(ChpDay.day, ChpDay.hours, today)
        kwh = _by_day(ChpDay.day, ChpDay.kwh, today)
        if any(hours[:-1]):
            last = max(i for i, h in enumerate(hours[:-1]) if h)       # letzter Tag mit Laufzeit (heute zählt noch nicht)
            day = _days(today)[last]
            out.append({"key": "bhkw", "icon": "bi-lightning-charge",
                        "label": _("BHKW gestern") if last == DAYS - 2 else _("BHKW am {date}", date=day.strftime("%d.%m.")),
                        "value": _num(hours[last], 1), "unit": "h",
                        "sub": _("{mwh} MWh erzeugt", mwh=_num(kwh[last] / 1000, 1)),
                        "series": hours[:-1], "series_label": _("Laufstunden je Tag"),
                        "labels": _labels(today)[:-1], "series_unit": "h",
                        "url": url_for("biogas.analysis_page")})
    if user.chickens:
        eggs = _by_day(EggEntry.day, EggEntry.eggs, today, EggEntry.imported.is_(False))
        week = sum(eggs[-7:])
        out.append({"key": "huehner", "icon": "bi-egg", "label": _("Eier heute"),
                    "value": _num(eggs[-1]), "unit": "",
                    "sub": ngettext("{n} Ei in 7 Tagen", "{n} Eier in 7 Tagen", int(week)),
                    "series": eggs, "series_label": _("Eier je Tag"),
                    "labels": _labels(today), "series_unit": "", "url": url_for("chickens.overview")})
    if user.sees_forest:
        total = db.session.query(func.count(ForestPlace.id)).join(ForestTree).filter(ForestTree.active.is_(True)).scalar() or 0
        used = db.session.query(ForestBurial.place_id).distinct()
        occupied = db.session.query(func.count(ForestPlace.id)).filter(ForestPlace.id.in_(used)).scalar() or 0
        reserved = db.session.query(func.count(ForestPlace.id)).filter(
            ForestPlace.contract_id.isnot(None), ForestPlace.id.notin_(used)).scalar() or 0
        if total:
            out.append({"key": "wald", "icon": "bi-tree", "label": _("Freie Plätze"),
                        "value": _num(total - occupied - reserved), "unit": "",
                        "sub": _("{r} reserviert · {b} belegt", r=reserved, b=occupied),
                        "bar": round(100 * (occupied + reserved) / total), "url": url_for("forest.overview")})
    return out
