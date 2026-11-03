"""Rubrik Bestattungswald: Übersicht, Karte, Bäume, Beisetzungen, Verträge (Logik in forest.py)."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import forest, rounds
from .extensions import db
from .i18n import N_, gettext as _
from .models import FOREST_KINDS, FOREST_PLACE_STATES, ForestBurial, ForestContract, ForestPlace, ForestTree
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


def _tree(number):
    return ForestTree.query.filter_by(number=number).first() or abort(404)


@bp.route("/wald")
@login_required
def overview():
    stations = rounds.overview(area=forest.AREA)
    return _page("forest/overview.html", "uebersicht", data=forest.overview(), stations=stations,
                 suggestions=STATION_SUGGESTIONS, ending=forest.ending_contracts() if current_user.is_admin else [])


@bp.route("/wald/suche")
@login_required
def find():
    tree = forest.tree_by_code(request.args.get("baum", "")) or \
        (ForestTree.query.filter_by(number=request.args.get("baum", type=int)).first()
         if request.args.get("baum", "").strip().isdigit() else None)
    if tree is None:
        flash_error(_("Keinen Baum mit dieser Nummer gefunden."))
        return redirect(request.referrer or url_for("forest.trees"))
    return redirect(url_for("forest.tree_detail", number=tree.number))


@bp.route("/wald/karte")
@login_required
def map_page():
    from .fields_views import aerial_options
    return _page("forest/map.html", "karte", items=forest.map_data(), aerial=aerial_options(),
                 missing=ForestTree.query.filter(ForestTree.lat.is_(None)).count())


@bp.route("/wald/baeume")
@login_required
def trees():
    section = request.args.get("abschnitt") or None
    only_free = request.args.get("frei") == "1"
    return _page("forest/trees.html", "baeume", rows=forest.tree_rows(section, only_free), sections=forest.sections(),
                 section=section, only_free=only_free, kinds=FOREST_KINDS, default_places=forest.DEFAULT_PLACES)


@bp.route("/wald/baeume/anlegen", methods=["POST"])
@admin_required
def trees_create():
    try:
        n = forest.create_trees(form_int("first"), form_int("last"), form_int("places", forest.DEFAULT_PLACES),
                                form_str("species", 60), form_str("section", 60), form_str("kind", 20))
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


@bp.route("/wald/baum/<int:number>")
@login_required
def tree_detail(number):
    tree = _tree(number)
    return _page("forest/tree.html", "baeume", tree=tree, kinds=FOREST_KINDS, states=FOREST_PLACE_STATES)


@bp.route("/wald/baum/<int:number>", methods=["POST"])
@admin_required
def tree_save(number):
    tree = _tree(number)
    try:
        forest.save_tree(tree, form_int("number"), form_str("species", 60), form_str("section", 60),
                         form_str("kind", 20), form_int("places", forest.DEFAULT_PLACES), form_str("lat", 20),
                         form_str("lon", 20), form_str("note", 2000), active=bool(request.form.get("active")))
        db.session.commit()
        flash(_("Gespeichert."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
    return redirect(url_for("forest.tree_detail", number=tree.number))


# --- Beisetzungen ------------------------------------------------------------------------------------

@bp.route("/wald/beisetzungen")
@login_required
def burial_page():
    year = request.args.get("jahr", type=int)
    return _page("forest/burials.html", "beisetzungen", upcoming=forest.upcoming(days=3650),
                 past=[b for b in forest.burials(year or date.today().year) if b.day < date.today()],
                 year=year or date.today().year, tree=request.args.get("baum", type=int),
                 place=request.args.get("platz", type=int))


def _place_from_form():
    tree = ForestTree.query.filter_by(number=form_int("tree", 0)).first()
    if tree is None:
        raise StorageError(_("Keinen Baum mit dieser Nummer gefunden."))
    place = next((p for p in tree.places if p.number == form_int("place", 0)), None)
    if place is None:
        raise StorageError(_("Baum {n} hat keinen Platz {p}.", n=tree.number, p=form_int("place", 0)))
    return place


@bp.route("/wald/beisetzung", methods=["POST"])
@bp.route("/wald/beisetzung/<int:burial_id>", methods=["POST"])
@editor_required
def burial_save(burial_id=None):
    burial = db.session.get(ForestBurial, burial_id) if burial_id else None
    if burial_id and burial is None:
        abort(404)
    try:
        burial = forest.save_burial(burial, current_user, _place_from_form(), form_str("deceased", 150),
                                    form_date("day"), form_str("time", 5), form_str("undertaker", 150),
                                    form_str("note", 2000))
        db.session.commit()
        flash(_("Beisetzung eingetragen – „Grab ausheben“ und „Grab schließen“ stehen unter Aufgaben."), "success")
    except StorageError as e:
        db.session.rollback()
        flash_error(str(e))
        return redirect(request.referrer or url_for("forest.burial_page"))
    return redirect(url_for("forest.burial_page") + f"#b{burial.id}")


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
    tree = ForestTree.query.filter_by(number=source.get("baum", type=int)).first()
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
    if contract is not None and not places:
        places = list(contract.places)
        tree = places[0].tree if places else None
    return _page("forest/contract.html", "vertraege", contract=contract, tree=tree, places=places,
                 term_years=forest.TERM_YEARS)


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
                                        form_str("note", 2000))
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
