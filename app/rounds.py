"""Rundgang (Eigenkontrolle): Stationen mit QR-Code `R-01` und Checkliste, Kontrollen mit Ergebnis je Prüfpunkt.

Scannen öffnet die Checkliste der Station (`/scan` erkennt den Code). Wie die übrigen Logik-Module ändern die
Funktionen nur die Session; Push-Nachrichten (`issue_reported`, `morning_due`) erst nach dem Commit aufrufen.
"""
import re
from datetime import timedelta

from flask import url_for

from . import push
from .extensions import db
from .i18n import N_, gettext as _
from .models import CheckStation, StationCheck, utcnow
from .push import Msg
from .storage import StorageError

CODE_RE = re.compile(r"^R-?0*(\d{1,4})$")

# Vorschläge für die ersten Stationen einer Biogasanlage (Name, Prüfpunkte, alle n Tage)
SUGGESTIONS = [
    (N_("Fermenter"), [N_("Rührwerke laufen"), N_("Temperatur im Sollbereich"), N_("Keine Leckage / kein Schaum"),
                       N_("Überdrucksicherung frei")], 1),
    (N_("Nachgärer / Gärrestlager"), [N_("Rührwerk läuft"), N_("Füllstand in Ordnung"), N_("Keine Leckage")], 1),
    (N_("BHKW-Raum"), [N_("Ölstand in Ordnung"), N_("Keine Undichtigkeit (Öl, Kühlwasser, Gas)"),
                       N_("Keine Störmeldung"), N_("Lüftung frei")], 1),
    (N_("Pumpenhaus"), [N_("Pumpen laufen ruhig"), N_("Keine Leckage"), N_("Schieber in richtiger Stellung")], 1),
    (N_("Feststoffeintrag"), [N_("Eintrag läuft"), N_("Keine Fremdkörper"), N_("Wiegezellen plausibel")], 1),
    (N_("Gasfackel / Gasstrecke"), [N_("Fackel betriebsbereit"), N_("Kondensatschacht entleert"),
                                    N_("Gaswarnanlage ohne Meldung")], 7),
]


def normalize_code(raw):
    """„R-01“, „r1“ oder eine URL, die auf den Code endet → „R-01“; sonst None."""
    if not raw:
        return None
    m = CODE_RE.match(raw.strip().upper().rstrip("/").rsplit("/", 1)[-1])
    return f"R-{int(m.group(1)):02d}" if m else None


def next_code():
    numbers = [int(m.group(1)) for (c,) in db.session.query(CheckStation.code) if (m := CODE_RE.match(c))]
    return f"R-{max(numbers, default=0) + 1:02d}"


def _items(text):
    lines = [re.sub(r"\s+", " ", line).strip()[:150] for line in (text or "").replace("\r\n", "\n").split("\n")]
    return "\n".join(line for line in lines if line)[:3000] or None


def save_station(station, name, items, interval_days, active=True):
    name = re.sub(r"\s+", " ", name or "").strip()[:100]
    if not name:
        raise StorageError(_("Bitte einen Namen für die Station angeben."))
    if not interval_days or not 1 <= interval_days <= 365:
        raise StorageError(_("Kontrolle bitte alle 1 bis 365 Tage."))
    if station is None:
        station = CheckStation(code=next_code(),
                               position=(db.session.query(db.func.max(CheckStation.position)).scalar() or 0) + 1)
        db.session.add(station)
    station.name, station.items, station.interval_days, station.active = name, _items(items), interval_days, active
    db.session.flush()
    return station


def record_check(station, user, failed_items, note=None, photo_names=()):
    """Kontrolle speichern. `failed_items`: Indizes der Prüfpunkte, die nicht in Ordnung sind."""
    items = station.item_list
    failed = set(failed_items or ())
    note = (note or "").replace("\r\n", "\n").strip()[:2000] or None
    results = [[item, i not in failed] for i, item in enumerate(items)]
    ok = all(r[1] for r in results)
    if not ok and not note and not photo_names:
        raise StorageError(_("Bitte kurz beschreiben, was nicht in Ordnung ist (Notiz oder Foto)."))
    check = StationCheck(station=station, user_id=user.id if user else None, results=results, ok=ok, note=note)
    from .models import Photo
    for name in photo_names or ():
        check.photos.append(Photo(filename=name, user_id=user.id if user else None))
    db.session.add(check)
    db.session.flush()
    return check


def resolve(check, user):
    if check.ok or check.resolved_at:
        raise StorageError(_("Dieser Mangel ist schon erledigt."))
    check.resolved_at, check.resolved_by_id = utcnow(), user.id


def last_check(station):
    return station.checks.first()


def status(station, now=None):
    """{"last", "due_at", "overdue", "open": offene Mängel (neueste zuerst)}."""
    now = now or utcnow()
    last = last_check(station)
    due_at = (last.created_at + timedelta(days=station.interval_days)) if last else None
    if due_at is not None and due_at.tzinfo is None:          # SQLite liefert ohne Zeitzone
        due_at = due_at.replace(tzinfo=now.tzinfo)
    open_issues = station.checks.filter(StationCheck.ok.is_(False), StationCheck.resolved_at.is_(None)).all()
    return {"last": last, "due_at": due_at, "overdue": last is None or due_at <= now, "open": open_issues}


def overview(now=None):
    stations = CheckStation.query.filter_by(active=True).order_by(CheckStation.position, CheckStation.code).all()
    rows = [{"station": s, **status(s, now)} for s in stations]
    return {"rows": rows, "overdue": sum(1 for r in rows if r["overdue"]),
            "issues": sum(len(r["open"]) for r in rows)}


def has_stations():
    return db.session.query(CheckStation.id).filter_by(active=True).first() is not None


def issue_reported(check):
    """Push bei einem Mangel (Thema „Rundgang: Mangel“, von sich aus an Admins)."""
    items = ", ".join(check.failed)[:150]
    return push.notify(push.editors(), "round_issue", Msg("Rundgang: {station}", station=check.station.name),
                       items or (check.note or "")[:150], url_for("rounds.station", code=check.station.code),
                       tag=f"check-{check.id}", exclude=check.user)


def morning_due(today):
    """Aus dem Zeitplan (morgens): Stationen, deren Kontrolle überfällig ist."""
    data = overview()
    names = [r["station"].name for r in data["rows"] if r["overdue"]]
    if not names:
        return 0
    return push.notify(push.editors(), "round_due",
                       Msg("Rundgang: {n} Station nicht kontrolliert", "Rundgang: {n} Stationen nicht kontrolliert",
                           len(names)), ", ".join(names)[:180],
                       url_for("rounds.overview"), tag=f"rounds-{today.isoformat()}")
