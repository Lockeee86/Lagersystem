"""Technische Plätze: feste Orte im Betrieb als Baum (Biogasanlage → BHKW-Raum, Halle 2 → Kühlung …).

Maschinen sind die Equipments; sie sind optional an einem Platz eingebaut (`Machine.place_id`). Ein Platz zeigt alles,
was in ihm und darunter eingebaut ist: offene Störungen, fällige Wartungen und Kosten. Wie `storage.py` ändern die
Funktionen nur die Session, committen muss der Aufrufer.
"""
import re
from decimal import Decimal

from . import machines
from .extensions import db
from .i18n import gettext as _
from .models import Machine, TechPlace, Warehouse
from .storage import StorageError

CODE_RE = re.compile(r"^TP-?(\d+)$")
# Arten, die fest eingebaut werden (ohne Platz → Hinweis auf der Platz-Übersicht)
STATIONARY = ("lagertechnik", "kuehlung", "lueftung", "bhkw", "biogas", "waermenetz", "sortieranlage")


def normalize_code(raw):
    if not raw:
        return None
    m = CODE_RE.match(raw.strip().upper().rstrip("/").rsplit("/", 1)[-1])
    return f"TP-{int(m.group(1)):03d}" if m else None


def next_code():
    numbers = [int(m.group(1)) for (c,) in db.session.query(TechPlace.code) if (m := CODE_RE.match(c))]
    return f"TP-{max(numbers, default=0) + 1:03d}"


def can_see(place, user):
    return place is None or not place.in_biogas or not machines.hides_biogas(user)


def tree(user=None, include_inactive=False):
    """Alle Plätze als [(platz, tiefe)] in Baumreihenfolge (Kinder direkt unter dem Vater)."""
    query = TechPlace.query if include_inactive else TechPlace.query.filter_by(active=True)
    places = query.order_by(TechPlace.name).all()
    by_parent = {}
    for p in places:
        by_parent.setdefault(p.parent_id, []).append(p)
    ids = {p.id for p in places}
    rows = []

    def walk(parent_id, depth):
        for p in by_parent.get(parent_id, []):
            if not can_see(p, user):
                continue
            rows.append((p, depth))
            walk(p.id, depth + 1)

    walk(None, 0)
    for p in places:                     # Kinder eines inaktiven Vaters: oben anzeigen statt verschwinden
        if p.parent_id is not None and p.parent_id not in ids and can_see(p, user):
            rows.append((p, 0))
            walk(p.id, 1)
    return rows


def descendant_ids(place):
    """Platz und alle darunter (auch inaktive)."""
    ids, todo = [], [place]
    while todo:
        node = todo.pop()
        if node.id in ids:
            continue
        ids.append(node.id)
        todo.extend(node.children)
    return ids


def equipments(place, user=None, deep=True, include_inactive=False):
    ids = descendant_ids(place) if deep else [place.id]
    query = Machine.query.filter(Machine.place_id.in_(ids))
    if not include_inactive:
        query = query.filter(Machine.active.is_(True))
    return machines.visible(query, user).order_by(Machine.name).all()


def summary(place, user=None, year=None):
    """Was in einem Platz (samt Unterplätzen) eingebaut ist und wie es darum steht."""
    items = equipments(place, user)
    ids = {m.id for m in items}
    due = [(t, s) for t, s in machines.due_tasks(user=user) if t.machine_id in ids]
    faults = [e for e in machines.open_faults(user) if e.machine_id in ids]
    result = {"machines": items, "due": due, "faults": faults,
              "overdue": sum(1 for _t, s in due if s["state"] == "overdue"),
              "state": "fault" if faults else "overdue" if any(s["state"] == "overdue" for _t, s in due)
              else "soon" if due else "ok"}
    if year is not None:
        price = machines.diesel_price(year)
        rows = [{"machine": m, **machines.costs(m, year, price)} for m in items]
        rows.sort(key=lambda r: -r["total"])
        result["costs"] = rows
        result["cost_total"] = sum((r["total"] for r in rows), Decimal(0))
    return result


def save_place(place, name, parent_id=None, site_id=None, warehouse_id=None, biogas=False, note=None, active=True):
    """Anlegen (`place=None`) oder ändern. Gibt den Platz zurück."""
    name = (name or "").strip()[:100]
    if not name:
        raise StorageError(_("Bitte einen Namen angeben."))
    parent = db.session.get(TechPlace, parent_id) if parent_id else None
    if place is None:
        place = TechPlace(code=next_code())
        db.session.add(place)
    elif parent is not None and parent.id in descendant_ids(place):
        raise StorageError(_("Ein Platz kann nicht unter sich selbst hängen."))
    place.name, place.parent = name, parent
    place.site_id = site_id
    place.warehouse_id = warehouse_id if warehouse_id and db.session.get(Warehouse, warehouse_id) else None
    place.biogas = bool(biogas)
    place.note = (note or "").strip()[:2000] or None
    place.active = bool(active)
    db.session.flush()
    return place


def delete_place(place):
    if place.children:
        raise StorageError(_("Erst die Unterplätze verschieben oder löschen."))
    for m in list(place.machines):          # Equipments bleiben, sind dann nur nirgends eingebaut
        m.place_id = None
    db.session.delete(place)
    db.session.flush()


def install(machine, place):
    """Maschine an einem Platz einbauen (`place=None` = ausbauen). Die Änderung landet im Änderungsprotokoll."""
    machine.place = place
    db.session.flush()


def for_warehouse(warehouse, user=None):
    """Plätze, die zu einem Kartoffellager gehören, mit ihrem Stand (für die Lagerseite)."""
    return [(p, summary(p, user)) for p in TechPlace.query.filter_by(warehouse_id=warehouse.id, active=True)
            .order_by(TechPlace.name) if can_see(p, user)]


def choices(user=None, exclude=None):
    """[(id, Einrückung + Name)] für Auswahllisten; `exclude` (ein Platz) samt Unterplätzen fehlt."""
    skip = set(descendant_ids(exclude)) if exclude is not None else set()
    return [(p.id, "  " * depth + ("› " if depth else "") + p.name)
            for p, depth in tree(user) if p.id not in skip]


def group_machines(items):
    """Maschinen für Auswahllisten nach Platz gruppieren: [(Pfad oder None, [Maschinen])] in Baumreihenfolge,
    ohne festen Platz zum Schluss. Ohne Plätze eine einzige Gruppe mit `None` (dann ohne Überschrift anzeigen)."""
    items = list(items)
    if not any(m.place_id for m in items):
        return [(None, items)] if items else []
    order = {p.id: i for i, (p, _depth) in enumerate(tree(include_inactive=True))}
    groups = {}
    for m in items:
        groups.setdefault(m.place, []).append(m)
    rows = sorted(((p, ms) for p, ms in groups.items() if p is not None), key=lambda r: order.get(r[0].id, 1e9))
    result = [(p.label, ms) for p, ms in rows]
    if None in groups:
        result.append((_("Ohne festen Platz"), groups[None]))
    return result


def place_from_site(site, parent_id=None, biogas=False):
    """Betriebsstätte, die eigentlich ein Standort einer Anlage ist (z. B. BHKW-Standort), in einen technischen Platz
    umwandeln: Platz gleichen Namens anlegen (oder vorhandenen unter demselben Vater nehmen), ihre Maschinen dort
    einbauen (bereits eingebaute bleiben, wo sie sind) und die Zuordnung zur Betriebsstätte lösen. Die Betriebsstätte
    wird gelöscht, wenn nichts anderes mehr an ihr hängt, sonst deaktiviert.
    Gibt (Platz, Zahl der Maschinen, gelöscht?) zurück."""
    from . import sites
    parent = db.session.get(TechPlace, parent_id) if parent_id else None
    place = TechPlace.query.filter_by(name=site.name, parent_id=parent.id if parent else None).first()
    if place is None:
        place = save_place(None, site.name, parent_id=parent.id if parent else None, biogas=biogas, note=site.note)
    elif biogas:
        place.biogas = True
    moved = 0
    for m in Machine.query.filter_by(site_id=site.id):
        if m.place_id is None:
            m.place = place
        m.site_id = None
        moved += 1
    for p in TechPlace.query.filter_by(site_id=site.id):
        p.site_id = None
    db.session.flush()
    deleted = sites.delete_site(site)
    return place, moved, deleted
