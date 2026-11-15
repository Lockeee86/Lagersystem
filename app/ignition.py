"""Zündung eines Gasmotors je Zylinder: Kerzen, Kerzenstecker und Zündspulen.

Jedes Teil ist ein `IgnitionPart` mit seiner Laufzeit (Zählerstand beim Einbau als Neuteil). Ein Wechsel baut das alte
Teil aus und ein neues ein; ein Tausch setzt zwei Teile auf den jeweils anderen Zylinder – die Laufzeit wandert mit.
Zylinderzahl, Bänke und Soll-Laufzeiten stehen in den Grunddaten der Maschine. Jede Aktion steht im Verlauf
(`IgnitionEvent`) und als ein Eintrag im Logbuch der Maschine. Committet nicht.
"""
from datetime import date

from .extensions import db
from .i18n import N_, gettext as _
from .models import IgnitionEvent, IgnitionPart
from .storage import StorageError

KINDS = (("kerze", N_("Zündkerze"), "ign_plug_hours"), ("stecker", N_("Kerzenstecker"), "ign_cap_hours"),
         ("spule", N_("Zündspule"), "ign_coil_hours"))
KIND_LABELS = {key: label for key, label, _field in KINDS}
KIND_SHORT = {"kerze": N_("Kerze"), "stecker": N_("Stecker"), "spule": N_("Spule")}
REASONS = (("plan", N_("planmäßig")), ("ausfall", N_("Ausfall")))
SOON = 0.9                      # ab 90 % der Soll-Laufzeit „bald fällig“
MAX_CYLINDERS = 24


def active(machine):
    return bool(machine.cylinders)


def interval(machine, kind):
    field = next((f for key, _label, f in KINDS if key == kind), None)
    return getattr(machine, field) if field else None


def label(machine, cylinder):
    """„7“ bzw. bei zwei Bänken „A3“ / „B3“ (Bank A = erste Hälfte)."""
    if machine.cylinder_banks == 2 and machine.cylinders:
        half = (machine.cylinders + 1) // 2
        return f"A{cylinder}" if cylinder <= half else f"B{cylinder - half}"
    return str(cylinder)


def save_settings(machine, cylinders, banks, hours):
    """Grunddaten: Zylinderzahl (leer = keine Zündung verfolgen), Bänke 1/2, Soll-Laufzeiten {kind: h}."""
    if cylinders is not None and not 1 <= cylinders <= MAX_CYLINDERS:
        raise StorageError(_("Bitte 1 bis {n} Zylinder angeben.", n=MAX_CYLINDERS))
    if cylinders and machine.ignition_parts.filter(IgnitionPart.cylinder > cylinders).first():
        raise StorageError(_("Auf den höheren Zylindern sind noch Teile eingetragen – die Zylinderzahl lässt sich so nicht verkleinern."))
    machine.cylinders = cylinders or None
    machine.cylinder_banks = 2 if banks == 2 and cylinders and cylinders % 2 == 0 else None
    for key, _label, field in KINDS:
        value = hours.get(key)
        setattr(machine, field, value if value and value > 0 else None)


def _state(hours, limit):
    if not limit:
        return "none"
    return "overdue" if hours >= limit else "soon" if hours >= limit * SOON else "ok"


def _installed(machine):
    return {(p.kind, p.cylinder): p for p in machine.ignition_parts.filter(IgnitionPart.cylinder.isnot(None))}


def grid(machine):
    """Je Bank die Zylinder mit ihren Teilen: [{"name", "rows": [{"cylinder", "label", "failures", "cells": {kind:
    {"part", "hours", "state", "left"} | None}}]}]."""
    parts = _installed(machine)
    counter = machine.counter or 0
    failures = {}
    for e in machine.ignition_events.filter_by(action="wechsel", reason="ausfall"):
        failures[e.cylinder] = failures.get(e.cylinder, 0) + 1
    rows = []
    for cyl in range(1, (machine.cylinders or 0) + 1):
        cells = {}
        for kind, _label, _field in KINDS:
            part = parts.get((kind, cyl))
            if part is None:
                cells[kind] = None
                continue
            hours = max(counter - part.installed_counter, 0)
            limit = interval(machine, kind)
            cells[kind] = {"part": part, "hours": hours, "state": _state(hours, limit),
                           "left": limit - hours if limit else None}
        rows.append({"cylinder": cyl, "label": label(machine, cyl), "cells": cells, "failures": failures.get(cyl, 0)})
    if machine.cylinder_banks == 2:
        half = len(rows) // 2
        return [{"name": _("Bank A"), "rows": rows[:half]}, {"name": _("Bank B"), "rows": rows[half:]}]
    return [{"name": None, "rows": rows}]


def due(machine):
    """Teile, die fällig oder bald fällig sind: [(kind, cylinder, label, hours, left, state)] – überfällige zuerst."""
    result = []
    for bank in grid(machine):
        for row in bank["rows"]:
            for kind, cell in row["cells"].items():
                if cell and cell["state"] in ("overdue", "soon"):
                    result.append((kind, row["cylinder"], row["label"], cell["hours"], cell["left"], cell["state"]))
    return sorted(result, key=lambda r: r[4])


def summary(machine):
    """Kurzfassung für Listen: {"overdue": n, "soon": n} oder None ohne Zündungs-Verfolgung."""
    if not active(machine):
        return None
    items = due(machine)
    return {"overdue": sum(1 for r in items if r[5] == "overdue"), "soon": sum(1 for r in items if r[5] == "soon")}


def _check(machine, cylinders, kinds, day, counter):
    if not active(machine):
        raise StorageError(_("Für diese Maschine ist keine Zylinderzahl eingetragen (Grunddaten)."))
    if not cylinders or any(not 1 <= c <= machine.cylinders for c in cylinders):
        raise StorageError(_("Bitte mindestens einen Zylinder wählen."))
    if not kinds or any(k not in KIND_LABELS for k in kinds):
        raise StorageError(_("Bitte wählen, was gewechselt wurde: Kerze, Stecker oder Spule."))
    if counter is None or counter < 0:
        raise StorageError(_("Bitte einen gültigen Zählerstand angeben."))
    if day and day > date.today():
        raise StorageError(_("Das Datum liegt in der Zukunft."))


def _log(machine, user, day, counter, text, cost=None):
    from . import machines
    machines._raise_counter(machine, counter)
    return machines._event(machine, "wartung", user, day, counter=counter, task_name="Zündung", text=text, cost=cost)


def _list(machine, cylinders):
    return ", ".join(label(machine, c) for c in sorted(cylinders))


def change(machine, user, cylinders, kinds, day=None, counter=None, reason="plan", note=None, cost=None):
    """Neue Teile einbauen (alte ausbauen). Gibt die Verlaufs-Einträge zurück."""
    day = day or date.today()
    counter = machine.counter if counter is None else counter
    cylinders, kinds = sorted(set(cylinders)), [k for k, _l, _f in KINDS if k in set(kinds)]
    _check(machine, cylinders, kinds, day, counter)
    if reason not in dict(REASONS):
        reason = "plan"
    note = (note or "").strip()[:200] or None
    parts = _installed(machine)
    events = []
    for kind in kinds:
        for cyl in cylinders:
            old = parts.get((kind, cyl))
            life = None
            if old is not None:
                if counter < old.installed_counter:
                    raise StorageError(_("Der Zählerstand liegt vor dem Einbau des jetzigen Teils (Zylinder {cyl}).",
                                         cyl=label(machine, cyl)))
                life = counter - old.installed_counter
                old.cylinder, old.last_cylinder = None, cyl
                old.removed_on, old.removed_counter, old.removed_reason = day, counter, reason
            db.session.add(IgnitionPart(machine_id=machine.id, kind=kind, cylinder=cyl, installed_on=day,
                                        installed_counter=counter))
            event = IgnitionEvent(machine_id=machine.id, day=day, counter=counter, action="wechsel", kind=kind,
                                  cylinder=cyl, reason=reason, life_hours=life, note=note,
                                  user_id=user.id if user else None)
            db.session.add(event)
            events.append(event)
    text = " · ".join(f"{KIND_LABELS[k]}: Zyl. {_list(machine, cylinders)}" for k in kinds)
    if reason == "ausfall":
        text += " (Ausfall)"
    if note:
        text += f" – {note}"
    _log(machine, user, day, counter, text, cost)
    db.session.flush()
    return events


def swap(machine, user, kinds, first, second, day=None, counter=None, note=None):
    """Teile zweier Zylinder untereinander tauschen – die Laufzeit wandert mit dem Teil."""
    day = day or date.today()
    counter = machine.counter if counter is None else counter
    kinds = [k for k, _l, _f in KINDS if k in set(kinds)]
    _check(machine, [first, second], kinds, day, counter)
    if first == second:
        raise StorageError(_("Bitte zwei verschiedene Zylinder wählen."))
    note = (note or "").strip()[:200] or None
    parts = _installed(machine)
    events = []
    for kind in kinds:
        a, b = parts.get((kind, first)), parts.get((kind, second))
        if a is None and b is None:
            raise StorageError(_("{kind}: Auf beiden Zylindern ist noch kein Teil eingetragen – erst einen Wechsel eintragen.",
                                 kind=_(KIND_LABELS[kind])))
        if a is not None:
            a.cylinder = second
        if b is not None:
            b.cylinder = first
        event = IgnitionEvent(machine_id=machine.id, day=day, counter=counter, action="tausch", kind=kind,
                              cylinder=first, other_cylinder=second, note=note, user_id=user.id if user else None)
        db.session.add(event)
        events.append(event)
    text = " · ".join(f"{KIND_LABELS[k]}: Zyl. {label(machine, first)} ↔ {label(machine, second)} getauscht" for k in kinds)
    if note:
        text += f" – {note}"
    _log(machine, user, day, counter, text)
    db.session.flush()
    return events


def history(machine, limit=25):
    """Verlauf, neueste zuerst. Wechsel derselben Aktion (Tag, Zählerstand, Teil, Grund, Notiz, Person) stehen in einer
    Zeile: [{"event" (der erste), "cylinders": [Zylinder…], "lives": [Laufzeiten der alten Teile]}]."""
    events = machine.ignition_events.order_by(IgnitionEvent.day.desc(), IgnitionEvent.id.desc()).limit(400).all()
    groups, index = [], {}
    for e in events:
        key = (e.day, e.counter, e.action, e.kind, e.reason, e.note, e.user_id) if e.action == "wechsel" else ("t", e.id)
        group = index.get(key)
        if group is None:
            if len(groups) >= limit:
                continue
            group = index[key] = {"event": e, "cylinders": [], "lives": []}
            groups.append(group)
        group["cylinders"].append(e.cylinder)
        if e.life_hours is not None:
            group["lives"].append(e.life_hours)
    for group in groups:
        group["cylinders"].sort()
    return groups


def stats(machine):
    """Je Teileart: wie viele gewechselt, mittlere Laufzeit der ausgebauten Teile, Ausfälle."""
    result = []
    for kind, kind_label, _field in KINDS:
        removed = machine.ignition_parts.filter(IgnitionPart.kind == kind, IgnitionPart.removed_counter.isnot(None)).all()
        lives = [p.removed_counter - p.installed_counter for p in removed if p.removed_counter > p.installed_counter]
        if not removed:
            continue
        result.append({"kind": kind, "label": kind_label, "count": len(removed),
                       "avg": sum(lives) / len(lives) if lives else None,
                       "failures": sum(1 for p in removed if p.removed_reason == "ausfall"),
                       "limit": interval(machine, kind)})
    return result
