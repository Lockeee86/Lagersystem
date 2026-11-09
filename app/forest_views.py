"""Rubrik Bestattungswald: Übersicht, Karte, Bäume, Beisetzungen, Verträge (Logik in forest.py)."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import forest, push, rounds
from .extensions import db
from .i18n import N_, gettext as _, ngettext
from .models import (FOREST_KINDS, FOREST_PLACE_STATES, FOREST_SELECTION_STATES, ForestBurial, ForestContract,
                     ForestPlace, ForestSelection, ForestTree, User)
from .storage import StorageError
from .utils import admin_required, editor_required, flash_error, form_date, form_int, form_str

bp = Blueprint("forest", __name__)

# Vorschläge für die Baumkontrolle (Rundgang-Stationen der Rubrik)
STATION_SUGGESTIONS = {
    "baeume": (N_("Baumkontrolle"), [N_("Totholz und lose Äste über Wegen und Plätzen"),
                                     N_("Standsicherheit (Schiefstand, Wurzelteller, Risse)"),
                                     N_("Pilzbefall oder Fäule am Stamm"), N_("Baumschilder vollständig und lesbar")], 180),
    "wege": (N_("Wege und Plätze"), [N_("Wege frei und begehbar"), N_("Parkplatz und Zufahrt in Ordnung"),
                                     N_("Grabschmuck und Müll entfernt"), N_("Bänke und Andachtsplatz in Ordnung")], 30),
}


@bp.before_request
def _only_with_forest():
    """Rubrik je Benutzer (User.forest)."""
    if current_user.is_authenticated and not current_user.sees_forest:
        abort(404)


def _page(template, tab, **context):
    return render_template(template, tab=tab, forest_name=forest.name(), today=date.today(), **context)


def _tree(key):
    found = forest.split_key(key)
    tree = ForestTree.query.filter_by(field=found[0], number=found[1]).first() if found else None
    return tree or abort(404)


@bp.route("/wald")
@login_required
def overview():
    stations = rounds.overview(area=forest.AREA)
    return _page("forest/overview.html", "uebersicht", data=forest.overview(), stations=stations,
                 selections=forest.upcoming_selections(),
                 suggestions=STATION_SUGGESTIONS, ending=forest.ending_contracts() if current_user.is_admin else [])


@bp.route("/wald/suche")
@login_required
def find():
    raw = request.args.get("baum", "").strip()
    selection = request.args.get("auswahl", type=int)
    hits = forest.search_trees(ForestTree.query.filter_by(active=True), raw).limit(2).all() if raw else []
    if len(hits) == 1:                                  # eindeutig → gleich den Baum öffnen
        return redirect(url_for("forest.tree_detail", key=hits[0].key, auswahl=selection))
    if not hits:
        tree = forest.find_tree(raw)                    # z. B. inaktiver Baum
        if tree is not None:
            return redirect(url_for("forest.tree_detail", key=tree.key, auswahl=selection))
        flash_error(_("Keinen Baum mit dieser Nummer gefunden."))
    return redirect(url_for("forest.trees", q=raw, auswahl=selection))


@bp.route("/wald/karte")
@login_required
def map_page():
    from .fields_views import aerial_options
    background, legend = forest.map_background()
    species = [s for s in request.args.getlist("baumart") if s]
    kinds = [k for k in request.args.getlist("art") if k in FOREST_KINDS]
    only_free = request.args.get("frei") == "1"
    single = db.session.get(ForestTree, request.args.get("baum", type=int)) if request.args.get("baum") else None
    if single is not None:                               # „Lage öffnen“: nur dieser Baum, ohne Filter
        species, kinds, only_free = [], [], False
    items = forest.map_data(species, kinds, only_free, tree_id=single.id if single else None)
    target = None
    if single is not None:                               # Ziel fürs Hinführen: Baum bzw. Mitte der Fläche
        points = single.outline_points
        if single.lat is not None and single.lon is not None:
            target = [single.lon, single.lat]
        elif points:
            target = [sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)]
    return _page("forest/map.html", "karte", items=items, aerial=aerial_options(),
                 background=background, legend=legend, single=single, target=target, guide=request.args.get("hin") == "1",
                 kinds=FOREST_KINDS, kinds_used=forest.kinds_in_use(), chosen_kinds=kinds,
                 kind_labels=[_(FOREST_KINDS[k]) for k in kinds], chosen_species=species, only_free=only_free,
                 species_items=[{"id": s, "pick_label": s} for s in forest.species_list()],
                 missing=ForestTree.query.filter(ForestTree.lat.is_(None)).count())


@bp.route("/wald/baeume")
@login_required
def trees():
    section = request.args.get("abschnitt") or None
    only_free = request.args.get("frei") == "1"
    species = [s for s in request.args.getlist("baumart") if s]
    kinds = [k for k in request.args.getlist("art") if k in FOREST_KINDS]
    q = request.args.get("q", "").strip()
    from .models import ForestShape
    return _page("forest/trees.html", "baeume", shape_count=ForestShape.query.count(),
                 rows=forest.tree_rows(section, only_free, species, kinds, q), sections=forest.sections(),
                 section=section, only_free=only_free, kinds=FOREST_KINDS, default_places=forest.DEFAULT_PLACES,
                 species_all=forest.species_list(), kinds_used=forest.kinds_in_use(), chosen_species=species,
                 species_items=[{"id": s, "pick_label": s} for s in forest.species_list()],
                 kind_labels=[_(FOREST_KINDS[k]) for k in kinds],
                 chosen_kinds=kinds, q=q, selection_id=request.args.get("auswahl", type=int))


@bp.route("/wald/baeume/anlegen", methods=["POST"])
@admin_required
def trees_create():
    try:
        n = forest.create_trees(form_int("first"), form_int("last"), form_int("places", forest.DEFAULT_PLACES),
                                form_str("species", 60), form_str("section", 60), form_str("kind", 20),
                                field=form_str("field", 10))
        db.session.commit()
        flash(_("{n} Bäume angelegt.", n=n), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("forest.trees"))


@bp.route("/wald/baeume/import", methods=["POST"])
@admin_required
def trees_import():
    upload = request.files.get("file")
    try:
        if not upload or not upload.filename:
            raise StorageError(_("Bitte eine Datei auswählen."))
        added, changed = forest.apply_trees(forest.parse_trees_csv(upload.read()))
        db.session.commit()
        flash(_("Bäume übernommen: {added} neu, {changed} geändert.", added=added, changed=changed), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("forest.trees"))


@bp.route("/wald/baeume/altsystem", methods=["POST"])
@admin_required
def legacy_import():
    """Tabellenexport aus dem bisherigen Programm (Excel-XML/.xlsx oder ein ZIP mit Tabellen und Shapefile der
    Baumpunkte): „Prüfen“ = Vorschau (Rollback), „Übernehmen“ = Commit."""
    from . import audit, forest_import
    upload = request.files.get("file")
    commit = request.form.get("action") == "apply"
    try:
        if not upload or not upload.filename:
            raise StorageError(_("Bitte eine Datei auswählen."))
        found = forest_import.read_upload(upload.read())
        parsed, points, shapes = found["tables"], found["points"], found["shapes"]
        parts, stats = [], {}
        with audit.paused():
            if shapes:
                drawn = forest_import.apply_shapes(shapes, upload.filename)
                stats = {**stats, **drawn}
                parts.append(_("Grundkarte: {n} Formen in {layers} Ebenen", n=drawn["shapes"], layers=drawn["layers"]))
            if parsed:
                stats = forest_import.apply(parsed, current_user, with_people=bool(request.form.get("with_people")))
                parts.append(_("Bäume: {new} neu, {known} vorhanden · neue Plätze: {places} · Verträge: {contracts} · Beisetzungen: {burials} · vorgesehene Personen: {persons} · schon vergeben, übersprungen: {skipped}",
                               new=stats.get("trees_new", 0), known=stats.get("trees_known", 0),
                               places=stats.get("places_new", 0), contracts=stats.get("contracts", 0),
                               burials=stats.get("burials", 0), persons=stats.get("persons", 0),
                               skipped=stats.get("skipped_places", 0) + stats.get("skipped_burials", 0)))
            if points is not None:
                located = forest_import.apply_points(points)
                stats = {**stats, **located}
                parts.append(_("Lage gesetzt: {n} Bäume · ohne passenden Baum: {unknown}", n=located["located"],
                               unknown=located["unknown"]))
                if located["areas"]:
                    parts.append(ngettext("Umriss übernommen: {n} Fläche", "Umriss übernommen: {n} Flächen",
                                          located["areas"]))
        text = " · ".join(parts)
        if commit:
            audit.note("forest_trees", _("Übernahme aus dem Altsystem"), stats)
            db.session.commit()
            flash(_("Übernommen. {details}", details=text), "success")
        else:
            db.session.rollback()
            flash(_("Vorschau – noch nichts gespeichert. {details}", details=text), "info")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("forest.trees") + "#altsystem")


@bp.route("/wald/karte/grundkarte/loeschen", methods=["POST"])
@admin_required
def shapes_delete():
    from .models import ForestShape
    for shape in ForestShape.query.all():
        db.session.delete(shape)
    db.session.commit()
    flash(_("Grundkarte gelöscht."), "success")
    return redirect(url_for("forest.trees") + "#altsystem")


@bp.route("/wald/baum/id/<int:tree_id>")
@login_required
def tree_by_id(tree_id):
    """Für die Karte (Schlagkarte ersetzt die 0 durch die id)."""
    tree = db.session.get(ForestTree, tree_id) or abort(404)
    return redirect(url_for("forest.tree_detail", key=tree.key))


@bp.route("/wald/baum/<key>")
@login_required
def tree_detail(key):
    tree = _tree(key)
    # Plätze, die eine Baumauswahl schon vorgemerkt hat (noch ohne Vertrag/Beisetzung)
    marks = {}
    for sel in ForestSelection.query.filter_by(tree_id=tree.id, status="ausgesucht"):
        for n in sel.numbers:
            marks.setdefault(n, sel)
    selection = _selection_arg()
    if selection is not None and (selection.status == "abgesagt" or not current_user.can_edit):
        selection = None
    return _page("forest/tree.html", "baeume", tree=tree, kinds=FOREST_KINDS, states=FOREST_PLACE_STATES, marks=marks,
                 wheel=forest.place_wheel(tree, marks),
                 selection=selection, free=[p.number for p in forest.free_for(selection, tree)] if selection else [])


def _selection_arg(source=None):
    source = source if source is not None else request.args
    raw = source.get("auswahl") or source.get("selection_id")
    return db.session.get(ForestSelection, int(raw)) if raw and str(raw).isdigit() else None


# --- Baumauswahl -------------------------------------------------------------------------------------

def _guides():
    return [u for u in forest.members() if u.can_edit]


@bp.route("/wald/baumauswahl")
@login_required
def selection_page():
    return _page("forest/selections.html", "auswahl", data=forest.selections(), kinds=FOREST_KINDS,
                 guides=_guides(), selection=None)


@bp.route("/wald/baumauswahl/<int:selection_id>")
@login_required
def selection_detail(selection_id):
    selection = db.session.get(ForestSelection, selection_id) or abort(404)
    return _page("forest/selection.html", "auswahl", selection=selection, kinds=FOREST_KINDS, guides=_guides(),
                 states=FOREST_SELECTION_STATES, next_place=forest.next_place(selection))


@bp.route("/wald/baumauswahl", methods=["POST"])
@bp.route("/wald/baumauswahl/<int:selection_id>", methods=["POST"])
@editor_required
def selection_save(selection_id=None):
    selection = db.session.get(ForestSelection, selection_id) if selection_id else None
    if selection_id and selection is None:
        abort(404)
    guide = db.session.get(User, form_int("guide_id", 0) or 0)
    try:
        selection = forest.save_selection(selection, current_user, form_str("contact", 150), form_date("day"),
                                          form_str("time", 5), form_str("phone", 60), form_str("email", 120),
                                          form_str("deceased", 150), form_int("places_wanted", 1),
                                          form_str("kind_wanted", 20), guide, form_str("note", 2000))
        db.session.commit()
        if selection.task is not None and selection_id is None:
            push.task_assigned(selection.task, current_user, users=selection.task.assignees or _guides())
        flash(_("Baumauswahl eingetragen – die Aufgabe steht beim Begleiter."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(request.referrer or url_for("forest.selection_page"))
    return redirect(url_for("forest.selection_detail", selection_id=selection.id))


@bp.route("/wald/baumauswahl/<int:selection_id>/baum", methods=["POST"])
@editor_required
def selection_choose(selection_id):
    selection = db.session.get(ForestSelection, selection_id) or abort(404)
    tree = forest.find_tree(form_str("tree", 20))
    numbers = [int(v) for v in request.form.getlist("platz") if v.isdigit()]
    numbers += [int(v) for v in (request.form.get("places") or "").replace(";", ",").replace(" ", ",").split(",")
                if v.strip().isdigit()]
    try:
        forest.choose_tree(selection, current_user, tree, numbers)
        db.session.commit()
        flash(_("Ausgesucht: {places}.", places=selection.places_label), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(request.referrer or url_for("forest.selection_detail", selection_id=selection_id))
    return redirect(url_for("forest.selection_detail", selection_id=selection_id))


@bp.route("/wald/baumauswahl/<int:selection_id>/absagen", methods=["POST"])
@editor_required
def selection_cancel(selection_id):
    selection = db.session.get(ForestSelection, selection_id) or abort(404)
    try:
        forest.cancel_selection(selection)
        db.session.commit()
        flash(_("Termin abgesagt."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("forest.selection_detail", selection_id=selection_id))


@bp.route("/wald/baumauswahl/<int:selection_id>/neu-planen", methods=["POST"])
@editor_required
def selection_reopen(selection_id):
    selection = db.session.get(ForestSelection, selection_id) or abort(404)
    if selection.burials or selection.contract_id:
        flash_error(_("Zu dieser Baumauswahl gibt es schon einen Vertrag oder eine Beisetzung."))
    else:
        forest.reopen_selection(selection, current_user)
        db.session.commit()
        flash(_("Wieder auf „Termin geplant“ gesetzt."), "success")
    return redirect(url_for("forest.selection_detail", selection_id=selection_id))


@bp.route("/wald/baumauswahl/<int:selection_id>/loeschen", methods=["POST"])
@admin_required
def selection_delete(selection_id):
    selection = db.session.get(ForestSelection, selection_id) or abort(404)
    try:
        forest.delete_selection(selection)
        db.session.commit()
        flash(_("Baumauswahl gelöscht."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("forest.selection_detail", selection_id=selection_id))
    return redirect(url_for("forest.selection_page"))


@bp.route("/wald/baum/<key>", methods=["POST"])
@admin_required
def tree_save(key):
    tree = _tree(key)
    try:
        forest.save_tree(tree, form_int("number"), form_str("species", 60), form_str("section", 60),
                         form_str("kind", 20), form_int("places", forest.DEFAULT_PLACES), form_str("lat", 20),
                         form_str("lon", 20), form_str("note", 2000), active=bool(request.form.get("active")),
                         field=form_str("field", 10), category=form_str("category", 10) or "",
                         trunk_cm=form_int("trunk_cm") or 0)
        db.session.commit()
        flash(_("Gespeichert."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("forest.tree_detail", key=tree.key))


# --- Beisetzungen ------------------------------------------------------------------------------------

@bp.route("/wald/beisetzungen")
@login_required
def burial_page():
    year = request.args.get("jahr", type=int)
    selection = _selection_arg()
    tree, place = request.args.get("baum") or None, request.args.get("platz", type=int)
    if selection is not None and tree is None:
        nxt = forest.next_place(selection)
        tree, place = (nxt.tree.name, nxt.number) if nxt else (None, None)
    return _page("forest/burials.html", "beisetzungen", upcoming=forest.upcoming(days=3650),
                 past=[b for b in forest.burials(year or date.today().year) if b.day < date.today()],
                 year=year or date.today().year, tree=tree, place=place, selection=selection,
                 grave_team=forest.grave_team(), guides=_guides())


@bp.route("/wald/grab-team", methods=["POST"])
@admin_required
def grave_team_save():
    try:
        forest.save_grave_team(db.session.get(User, form_int("grave_person", 0) or 0),
                               db.session.get(User, form_int("grave_deputy", 0) or 0))
        db.session.commit()
        flash(_("Gespeichert."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("forest.burial_page") + "#grab-team")


def _place_from_form():
    tree = forest.find_tree(form_str("tree", 20))
    if tree is None:
        raise StorageError(_("Keinen Baum mit dieser Nummer gefunden."))
    place = next((p for p in tree.places if p.number == form_int("place", 0)), None)
    if place is None:
        raise StorageError(_("Baum {n} hat keinen Platz {p}.", n=tree.name, p=form_int("place", 0)))
    return place


@bp.route("/wald/beisetzung", methods=["POST"])
@bp.route("/wald/beisetzung/<int:burial_id>", methods=["POST"])
@editor_required
def burial_save(burial_id=None):
    burial = db.session.get(ForestBurial, burial_id) if burial_id else None
    if burial_id and burial is None:
        abort(404)
    team = None
    if "grave_person" in request.form:                 # im Formular gewählt (sonst Standard)
        team = [db.session.get(User, form_int(key, 0) or 0) for key in ("grave_person", "grave_deputy")]
    new = burial is None
    try:
        burial = forest.save_burial(burial, current_user, _place_from_form(), form_str("deceased", 150),
                                    form_date("day"), form_str("time", 5), form_str("undertaker", 150),
                                    form_str("note", 2000), selection=_selection_arg(request.form), team=team,
                                    bells=bool(request.form.get("bells")))
        db.session.commit()
        if new and burial.dig_task is not None:
            push.task_assigned(burial.dig_task, current_user, users=burial.dig_task.assignees or _guides())
        flash(_("Beisetzung eingetragen – „Grab ausheben“ und „Grab schließen“ stehen unter Aufgaben."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(request.referrer or url_for("forest.burial_page"))
    return redirect(url_for("forest.burial_page") + f"#b{burial.id}")


@bp.route("/wald/beisetzung/<int:burial_id>/glocken", methods=["POST"])
@editor_required
def burial_bells(burial_id):
    burial = db.session.get(ForestBurial, burial_id) or abort(404)
    forest.set_bells(burial, current_user, request.form.get("bells") == "1")
    db.session.commit()
    flash(_("Kirchenglocken gewünscht.") if burial.bells else _("Ohne Kirchenglocken."), "success")
    return redirect(url_for("forest.burial_page", jahr=burial.day.year) + f"#b{burial.id}")


@bp.route("/wald/beisetzung/<int:burial_id>/loeschen", methods=["POST"])
@admin_required
def burial_delete(burial_id):
    burial = db.session.get(ForestBurial, burial_id) or abort(404)
    forest.delete_burial(burial)
    db.session.commit()
    flash(_("Beisetzung gelöscht."), "success")
    return redirect(url_for("forest.burial_page"))


# --- Verträge (nur Admin – Personendaten) -------------------------------------------------------------

@bp.route("/wald/vertraege")
@admin_required
def contracts_page():
    return _page("forest/contracts.html", "vertraege", contracts=forest.contracts(request.args.get("q")),
                 q=request.args.get("q", ""), ending=forest.ending_contracts())


def _places_from_args(source):
    tree = forest.find_tree(source.get("baum", ""))
    if tree is None:
        return None, []
    wanted = {int(v) for v in source.getlist("platz") if str(v).isdigit()}
    if source.get("alle"):
        wanted = {p.number for p in tree.places if p.contract_id is None}
    return tree, [p for p in tree.places if p.number in wanted]


@bp.route("/wald/vertrag/neu")
@bp.route("/wald/vertrag/<int:contract_id>")
@admin_required
def contract_detail(contract_id=None):
    contract = db.session.get(ForestContract, contract_id) if contract_id else None
    if contract_id and contract is None:
        abort(404)
    tree, places = _places_from_args(request.args)
    selection = _selection_arg()
    if selection is not None and not places:
        tree, places = selection.tree, selection.places
    if contract is not None and not places:
        places = list(contract.places)
        tree = places[0].tree if places else None
    return _page("forest/contract.html", "vertraege", contract=contract, tree=tree, places=places,
                 term_years=forest.TERM_YEARS, selection=selection,
                 default_end=contract.ends_on if contract else forest.default_end(date.today(), places))


@bp.route("/wald/vertrag", methods=["POST"])
@bp.route("/wald/vertrag/<int:contract_id>", methods=["POST"])
@admin_required
def contract_save(contract_id=None):
    contract = db.session.get(ForestContract, contract_id) if contract_id else None
    if contract_id and contract is None:
        abort(404)
    ids = [int(v) for v in request.form.getlist("place_id") if v.isdigit()]
    places = ForestPlace.query.filter(ForestPlace.id.in_(ids)).all() if ids else []
    try:
        contract = forest.save_contract(contract, current_user, form_str("holder", 150), form_date("signed_on"), places,
                                        form_int("years", forest.TERM_YEARS), form_str("address", 1000),
                                        form_str("phone", 60), form_str("email", 120), form_str("price", 20),
                                        form_str("note", 2000), selection=_selection_arg(request.form),
                                        ends_on=form_date("ends_on"), holder_birthday=form_date("holder_birthday"))
        db.session.commit()
        flash(_("Vertrag {number} gespeichert.", number=contract.number), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(request.referrer or url_for("forest.contracts_page"))
    return redirect(url_for("forest.contract_detail", contract_id=contract.id))


@bp.route("/wald/vertrag/<int:contract_id>/loeschen", methods=["POST"])
@admin_required
def contract_delete(contract_id):
    contract = db.session.get(ForestContract, contract_id) or abort(404)
    try:
        forest.delete_contract(contract)
        db.session.commit()
        flash(_("Vertrag gelöscht – die Plätze sind wieder frei."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(url_for("forest.contract_detail", contract_id=contract_id))
    return redirect(url_for("forest.contracts_page"))
