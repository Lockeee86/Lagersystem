"""Fütterungsplan der Biogasanlage: je Tag 1× oder 2× füttern, Mitarbeiter tragen sich selbst ein.

- Standard je Wochentag und für Feiertage in `settings` `feeding_defaults` („1,1,1,1,1,2,2,2“ = Mo–So, Feiertag).
- `FeedingDay` nur für Tage, die vom Standard abweichen (oder eine Notiz haben).
- `FeedingShift` je Platz (1 = Früh bzw. die einzige Fütterung, 2 = Abend) mit Person und Abhaken.
- Umstellen 1×/2×: Planer/Admins jederzeit, am Tag selbst auch wer an dem Tag eingetragen ist.
- Eintragen bis `WEEKS_AHEAD` Wochen im Voraus; eine Person darf beide Plätze eines Tages haben.
Committet nicht (außer `check()` aus dem Zeitplan); Push nach dem Commit über die `notify_*`-Funktionen.
"""
from datetime import date, datetime, timedelta, timezone

from flask import url_for

from . import push
from .extensions import db
from .i18n import gettext as _
from .models import FEEDING_SLOTS, FeedingDay, FeedingShift, Setting, User
from .push import Msg
from .storage import StorageError

WEEKS_AHEAD = 8
DEFAULTS_KEY = "feeding_defaults"
WEEKDAY_KEYS = 8                    # Mo–So und Feiertag
# Erinnerungen (Uhrzeit, ab der der Zeitplan sie verschickt – es gibt bewusst keine Fütterungszeiten)
FREE_HOUR = 16                      # morgen ist noch ein Platz frei → Biogas-Team
REMIND_HOUR = 19                    # morgen fütterst du → Eingetragene
MISSED_HOUR = 12                    # Früh/einzige Fütterung heute nicht abgehakt → Planer
MISSED_EVENING_HOUR = 8             # Abend-Fütterung von gestern nicht abgehakt → Planer


# --- Standard je Wochentag ------------------------------------------------------------------------

def defaults():
    """[Mo, Di, Mi, Do, Fr, Sa, So, Feiertag] → 1 oder 2."""
    setting = db.session.get(Setting, DEFAULTS_KEY)
    values = []
    for part in ((setting.value if setting and setting.value else "") or "").split(","):
        values.append(2 if part.strip() == "2" else 1)
    return (values + [1] * WEEKDAY_KEYS)[:WEEKDAY_KEYS]


def active():
    """In Gebrauch, sobald der Admin den Standard gespeichert hat oder sich jemand eingetragen hat – vorher keine
    Hinweise auf der Startseite und keine Erinnerungen (sonst sähe jeder ständig „Fütterung noch frei“)."""
    from .utils import request_cache
    cache = request_cache()
    if cache is not None and "feeding_active" in cache:
        return cache["feeding_active"]
    result = db.session.get(Setting, DEFAULTS_KEY) is not None or FeedingShift.query.first() is not None
    if cache is not None:
        cache["feeding_active"] = result
    return result


def save_defaults(values):
    values = [2 if str(v).strip() == "2" else 1 for v in values][:WEEKDAY_KEYS]
    if len(values) != WEEKDAY_KEYS:
        raise StorageError(_("Bitte für jeden Wochentag angeben, wie oft gefüttert wird."))
    Setting.put(DEFAULTS_KEY, ",".join(str(v) for v in values))


def _holidays(start, end):
    from . import agenda
    days = {}
    for year in range(start.year, end.year + 1):
        days.update(agenda.holidays(year))
    return {d: name for d, name in days.items() if start <= d <= end}


def default_times(day, holidays=None, values=None):
    values = values or defaults()
    holidays = holidays if holidays is not None else _holidays(day, day)
    return values[7] if day in holidays else values[day.weekday()]


def times_on(day):
    entry = db.session.get(FeedingDay, day)
    return entry.times if entry is not None else default_times(day)


def slots_for(times):
    """Plätze eines Tages: bei 1× nur „Fütterung“, bei 2× Früh und Abend."""
    return [1] if times == 1 else [1, 2]


def slot_label(slot, times):
    return _("Fütterung") if times == 1 else _(FEEDING_SLOTS[slot])


# --- Rechte ---------------------------------------------------------------------------------------

def is_member(user):
    """Füttert die Anlage: Haken „BGA füttern“ in der Benutzerverwaltung (nicht nur Betrachter) – die Rubrik Biogas
    braucht es dafür nicht; ohne sie ist unter Biogas nur der Fütterungsplan zu sehen."""
    return bool(user and user.is_authenticated and user.feeds_bga)


def is_planner(user):
    """Plant die Fütterung (andere eintragen, 1×/2× im Voraus) – auch ohne selbst zu füttern."""
    return bool(user and user.is_authenticated and user.plans_feeding)


def members():
    return [u for u in User.query.filter_by(active=True, feeds_plant=True).order_by(User.id) if u.feeds_bga]


def last_day(today=None):
    return (today or date.today()) + timedelta(weeks=WEEKS_AHEAD)


def can_switch(user, day, today=None):
    """1×/2× umstellen: Planer/Admins jederzeit (nicht rückwirkend), sonst nur am Tag selbst, wer dann füttert."""
    today = today or date.today()
    if day < today:
        return False
    if is_planner(user):
        return True
    if not is_member(user):
        return False
    return day == today and FeedingShift.query.filter_by(day=day, user_id=user.id).first() is not None


# --- Ansicht --------------------------------------------------------------------------------------

def monday_of(day):
    return day - timedelta(days=day.weekday())


def week(monday, user=None, today=None):
    """7 Tage ab `monday`: {day, times, holiday, note, slots: [{slot, label, shift}], free, can_switch}."""
    today = today or date.today()
    sunday = monday + timedelta(days=6)
    entries = {d.day: d for d in FeedingDay.query.filter(FeedingDay.day >= monday, FeedingDay.day <= sunday)}
    shifts = {}
    for s in FeedingShift.query.filter(FeedingShift.day >= monday, FeedingShift.day <= sunday):
        shifts[(s.day, s.slot)] = s
    holidays = _holidays(monday, sunday)
    values = defaults()
    days = []
    for i in range(7):
        day = monday + timedelta(days=i)
        entry = entries.get(day)
        times = entry.times if entry else default_times(day, holidays, values)
        slots = [{"slot": slot, "label": slot_label(slot, times), "shift": shifts.get((day, slot))}
                 for slot in slots_for(times)]
        days.append({
            "day": day, "times": times, "holiday": holidays.get(day), "note": entry.note if entry else None,
            "slots": slots, "free": sum(1 for s in slots if s["shift"] is None),
            "past": day < today, "today": day == today, "open": today <= day <= last_day(today),
            "can_switch": can_switch(user, day, today) if user is not None else False,
        })
    return days


def mine(user, start, end):
    """Eigene Fütterungen im Zeitraum (für Startseite und Kalender)."""
    return (FeedingShift.query.filter(FeedingShift.user_id == user.id, FeedingShift.day >= start,
                                      FeedingShift.day <= end)
            .order_by(FeedingShift.day, FeedingShift.slot).all())


def day_summary(day):
    """Wer füttert an `day`: [(Bezeichnung, Name oder None, erledigt?)] – für Displays und Startseite."""
    times = times_on(day)
    shifts = {s.slot: s for s in FeedingShift.query.filter_by(day=day)}
    return [(slot_label(slot, times), shifts[slot].user.name if slot in shifts else None,
             bool(slot in shifts and shifts[slot].done_at)) for slot in slots_for(times)]


def free_slots(day):
    times = times_on(day)
    taken = {s.slot for s in FeedingShift.query.filter_by(day=day)}
    return [slot for slot in slots_for(times) if slot not in taken]


# --- Ändern (committet nicht) ---------------------------------------------------------------------

def take(day, slot, actor, user=None, today=None):
    """Platz übernehmen – für sich selbst, Planer/Admins auch für andere. Liefert die `FeedingShift`."""
    today = today or date.today()
    user = user or actor
    if user.id != actor.id and not is_planner(actor):
        raise StorageError(_("Andere eintragen dürfen nur Planer und Admins."))
    if not is_member(user):
        raise StorageError(_("Eintragen lassen sich nur Mitarbeiter mit dem Haken „BGA füttern“ (Benutzerverwaltung)."))
    if day < today:
        raise StorageError(_("Vergangene Tage lassen sich nicht mehr belegen."))
    if day > last_day(today):
        raise StorageError(_("Eintragen geht höchstens {n} Wochen im Voraus.", n=WEEKS_AHEAD))
    if slot not in slots_for(times_on(day)):
        raise StorageError(_("An diesem Tag gibt es diese Fütterung nicht."))
    existing = FeedingShift.query.filter_by(day=day, slot=slot).first()
    if existing is not None:
        if existing.user_id == user.id:
            return existing
        raise StorageError(_("Diese Fütterung hat schon {name} übernommen.", name=existing.user.name))
    shift = FeedingShift(day=day, slot=slot, user_id=user.id)
    db.session.add(shift)
    db.session.flush()
    return shift


def give_up(shift, actor, today=None):
    """Platz abgeben (eigener oder als Planer). Liefert (Tag, Platz, frühere Person) für die Push-Nachricht."""
    today = today or date.today()
    if not (shift.user_id == actor.id or is_planner(actor)):
        raise StorageError(_("Abgeben kann nur, wer eingetragen ist – oder ein Planer."))
    if shift.done_at:
        raise StorageError(_("Diese Fütterung ist schon abgehakt."))
    if shift.day < today:
        raise StorageError(_("Vergangene Tage lassen sich nicht mehr ändern."))
    info = (shift.day, shift.slot, shift.user)
    db.session.delete(shift)
    db.session.flush()
    return info


def set_times(day, times, actor, today=None):
    """Tag auf 1× oder 2× stellen. Bei 2 → 1 bleibt die Früh-Person; war nur Abend belegt, rückt sie auf.
    Liefert die Person, deren Fütterung dadurch wegfällt (oder None)."""
    today = today or date.today()
    if times not in (1, 2):
        raise StorageError(_("Bitte 1× oder 2× wählen."))
    if not can_switch(actor, day, today):
        raise StorageError(_("Umstellen dürfen Planer und Admins – am Tag selbst auch, wer dann füttert."))
    removed = None
    if times == 1:
        shifts = {s.slot: s for s in FeedingShift.query.filter_by(day=day)}
        evening = shifts.get(2)
        if evening is not None:
            if 1 not in shifts:
                evening.slot = 1                    # einzige Person rückt auf die (einzige) Fütterung
            else:
                if evening.user_id != shifts[1].user_id:
                    removed = evening.user
                db.session.delete(evening)
    with db.session.no_autoflush:
        entry = db.session.get(FeedingDay, day)
        if times == default_times(day) and (entry is None or not entry.note):
            if entry is not None:
                db.session.delete(entry)
        else:
            if entry is None:
                entry = FeedingDay(day=day)
                db.session.add(entry)
            entry.times = times
            entry.updated_by_id = actor.id
    db.session.flush()
    return removed


def mark_done(shift, actor, note=None, now=None, today=None):
    today = today or date.today()
    if not (shift.user_id == actor.id or is_planner(actor)):
        raise StorageError(_("Abhaken kann nur, wer eingetragen ist – oder ein Planer."))
    if shift.day > today:
        raise StorageError(_("Erst am Tag der Fütterung abhaken."))
    shift.done_at = now or datetime.now(timezone.utc)
    shift.done_by_id = actor.id
    if note is not None:
        shift.note = note.strip()[:300] or None
    db.session.flush()


def undo_done(shift, actor):
    if not (actor.id in (shift.done_by_id, shift.user_id) or is_planner(actor)):
        raise StorageError(_("Zurücknehmen kann nur, wer abgehakt hat – oder ein Planer."))
    shift.done_at = None
    shift.done_by_id = None
    db.session.flush()


def absent_warning(user, day):
    """Hinweis, wenn die Person an dem Tag laut Kalender abwesend ist."""
    from . import agenda
    return agenda.absent(user, day)


# --- Push (nach dem Commit) -----------------------------------------------------------------------

def _day_label(day):
    return day.strftime("%d.%m.")


def _slots_msg(slots, times):
    """„Fütterung“, „Früh“, „Abend“ oder „Früh und Abend“ – in der Sprache des Empfängers."""
    if times == 1:
        return Msg("Fütterung")
    if len(slots) == 2:
        return Msg("Früh und Abend")
    return Msg(FEEDING_SLOTS[slots[0]])


def _where(day):
    return url_for("biogas.feeding_page", woche=monday_of(day).isoformat()) + f"#t{day.isoformat()}"


def notify_free(day, slot, actor, previous=None):
    """Abgegeben oder durch Umstellen frei geworden: an das Biogas-Team (ohne den Auslöser). Hat ein Planer
    jemand anderen ausgetragen, bekommt diese Person Bescheid und das Team erfährt, wer frei geworden ist."""
    if day < date.today():
        return 0
    label = _slots_msg([slot], times_on(day))
    sent = 0
    if previous is not None and previous.id != actor.id:
        sent += push.notify([previous], "feeding_mine", Msg("Fütterung am {day} ausgetragen", day=_day_label(day)),
                            Msg("{name} hat dich für {slot} ausgetragen.", name=actor.name, slot=label),
                            _where(day), tag=f"feeding-removed-{day.isoformat()}-{slot}")
        body = Msg("Die Fütterung von {name} ist frei geworden – wer übernimmt?", name=previous.name)
        team = [u for u in members() if u.id != previous.id]
    else:
        body = Msg("{name} hat die Fütterung abgegeben – wer übernimmt?", name=actor.name)
        team = members()
    return sent + push.notify(team, "feeding_free", Msg("Fütterung frei: {day} {slot}", day=_day_label(day), slot=label),
                              body, _where(day), tag=f"feeding-free-{day.isoformat()}-{slot}", exclude=actor)


def notify_removed(user, day, actor):
    if user is None or user.id == actor.id:
        return 0
    return push.notify([user], "feeding_mine", Msg("Fütterung am {day} entfällt", day=_day_label(day)),
                       Msg("{name} hat den Tag auf einmal füttern gestellt – deine Abend-Fütterung entfällt.",
                           name=actor.name), _where(day), tag=f"feeding-removed-{day.isoformat()}")


def check(now, done, mark):
    """Aus `push.daily`: freie Plätze morgen (16 Uhr), Erinnerung für morgen (19 Uhr), nicht abgehakt (12 / 8 Uhr)."""
    today = now.date()
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)
    sent = 0
    if not active():
        return 0
    if now.hour >= MISSED_EVENING_HOUR and not done("feeding_missed_evening", today):
        mark("feeding_missed_evening", today)
        if times_on(yesterday) == 2:
            sent += _missed(yesterday, 2)
    if now.hour >= MISSED_HOUR and not done("feeding_missed", today):
        mark("feeding_missed", today)
        sent += _missed(today, 1)
    if now.hour >= FREE_HOUR and not done("feeding_free", today):
        mark("feeding_free", today)
        free = free_slots(tomorrow)
        if free:
            labels = _slots_msg(free, times_on(tomorrow))
            sent += push.notify(members(), "feeding_free",
                                Msg("Fütterung morgen noch frei"),
                                Msg("{day}: {slots} – bitte eintragen.", day=_day_label(tomorrow), slots=labels),
                                _where(tomorrow), tag=f"feeding-free-{tomorrow.isoformat()}")
    if now.hour >= REMIND_HOUR and not done("feeding_remind", today):
        mark("feeding_remind", today)
        times = times_on(tomorrow)
        by_user = {}
        for s in FeedingShift.query.filter_by(day=tomorrow).order_by(FeedingShift.slot):
            by_user.setdefault(s.user, []).append(s.slot)
        for user, slots in by_user.items():
            sent += push.notify([user], "feeding_mine", Msg("Morgen fütterst du"),
                                Msg("{day}: {slots}", day=_day_label(tomorrow), slots=_slots_msg(slots, times)),
                                _where(tomorrow), tag=f"feeding-remind-{tomorrow.isoformat()}")
    return sent


def _missed(day, slot):
    """Planer benachrichtigen, wenn die Fütterung (Platz `slot`) an `day` nicht abgehakt oder gar nicht belegt ist."""
    shift = FeedingShift.query.filter_by(day=day, slot=slot).first()
    if shift is not None and shift.done_at:
        return 0
    label = _slots_msg([slot], times_on(day))
    body = Msg("{day} {slot}: {name} hat noch nicht abgehakt.", day=_day_label(day), slot=label, name=shift.user.name) \
        if shift is not None else Msg("{day} {slot}: Es ist niemand eingetragen.", day=_day_label(day), slot=label)
    planners = [u for u in User.query.filter_by(active=True) if is_planner(u)]
    return push.notify(planners, "feeding_missed", Msg("Fütterung nicht abgehakt"), body, _where(day),
                       tag=f"feeding-missed-{day.isoformat()}-{slot}")


# --- Kalender ---------------------------------------------------------------------------------------

def calendar_events(start, end, event, user=None):
    """Fütterungen im Zeitraum für den Kalender (agenda): jede belegte Fütterung, eigene als „meine“."""
    rows = (FeedingShift.query.filter(FeedingShift.day >= start, FeedingShift.day <= end)
            .order_by(FeedingShift.day, FeedingShift.slot).all())
    times = {}
    for s in rows:
        times.setdefault(s.day, times_on(s.day))
    return [event(s.day, "fuetterung", f"fuetterung-{s.id}",
                  _("Fütterung ({slot}): {name}", slot=slot_label(s.slot, times[s.day]), name=s.user.name),
                  "bi-basket2", "warning", _where(s.day), "", mine=user is None or s.user_id == user.id)
            for s in rows]
