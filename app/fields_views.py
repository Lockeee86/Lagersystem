"""Seiten der Schlagkartei: Schlagkarte (alle Umrisse), Schlag mit Fruchtfolge, Pflanzung, Rodung und Ertrag."""
from datetime import date

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from . import aerial, fielddoc, fields, sites, tasks, weather
from .extensions import db
from .i18n import gettext as _
from .models import FIELD_MEASURE_KINDS, Box, Plot, PlotYear, Setting, Variety
from .utils import admin_required, editor_required, form_date, form_int, form_str

bp = Blueprint("fields", __name__)


def _year_arg():
    years = fields.plot_years()
    default = date.today().year if date.today().year in years or not years else years[0]
    year = request.args.get("jahr", type=int) or default
    return year, sorted(set(years) | {year, date.today().year}, reverse=True)


def aerial_options(on=True):
    """Optionen für Schlagkarte.create(…, {aerial: …}) – None, wenn kein Luftbild eingestellt ist."""
    source = aerial.config()
    if source is None:
        return None
    return {"url": url_for("fields.aerial_image"), "credit": source.get("credit") or "", "on": on,
            "failed": _("Luftbild nicht verfügbar.")}


@bp.route("/schlagkarte")
@login_required
def field_map():
    year, years = _year_arg()
    only_potatoes = request.args.get("kartoffeln") == "1"
    plots = sites.apply(Plot.query.filter_by(active=True), Plot).order_by(Plot.name).all()
    if only_potatoes:
        plots = [p for p in plots if (y := p.year(year)) and "kartoffel" in (y.shown_crop or "").lower()]
    yields = fields.yields(year)
    rows = []
    for p in plots:
        y = p.year(year)
        kg = yields.get(p.id, {}).get("kg")
        rows.append({"plot": p, "year": y, "kg": kg, "t_ha": fields.t_per_ha(kg, p.area_ha),
                     "color": fields.crop_color(y.shown_crop if y else None, y.crop_eppo if y and y.crop else None)})
    return render_template(
        "fields/map.html", year=year, years=years, rows=rows, only_potatoes=only_potatoes,
        map_data=fields.map_data(plots, year), legend=fields.crop_legend(plots, year), aerial=aerial_options(),
        total_ha=sum(float(p.area_ha or 0) for p in plots),
        planned=sum(1 for r in rows if r["year"] and not r["year"].crop and r["year"].shown_crop),
    )


@bp.route("/schlag/<int:plot_id>")
@login_required
def field_detail(plot_id):
    plot = db.session.get(Plot, plot_id)
    if plot is None:
        abort(404)
    this_year = date.today().year
    known = fielddoc.products()
    history = []
    for y in sorted({py.year for py in plot.years} | {this_year}, reverse=True):
        entry = plot.year(y)
        kg = fields.yields(y).get(plot.id, {})
        history.append({"year": y, "entry": entry, "kg": kg.get("kg"), "boxes": kg.get("boxes"),
                        "bulk_kg": kg.get("bulk_kg"),
                        "varieties": kg.get("varieties", {}), "t_ha": fields.t_per_ha(kg.get("kg"), plot.area_ha),
                        "doc": fielddoc.summary(entry), "protection": fielddoc.protection(entry, known)})
    outline_ha = sum(fields.ring_area_ha(r) for r in plot.rings) if plot.rings else None
    stored = Box.query.filter(Box.plot_id == plot.id, Box.fill > 0).count()
    return render_template(
        "fields/detail.html", plot=plot, history=history, this_year=this_year, outline_ha=outline_ha,
        stored=stored, map_data=fields.map_data([plot], this_year), open_tasks=tasks.for_plot(plot.id),
        aerial=aerial_options(), kinds=FIELD_MEASURE_KINDS,
        today=date.today(),
        varieties=Variety.query.filter(or_(Variety.active.is_(True),
                                           Variety.id.in_([py.variety_id for py in plot.years if py.variety_id])))
        .order_by(Variety.name).all(),
    )


@bp.route("/schlag/<int:plot_id>/jahr", methods=["POST"])
@editor_required
def field_year_save(plot_id):
    """Pflanzung und Rodung eines Anbaujahrs erfassen (Mitarbeiter und Admin)."""
    plot = db.session.get(Plot, plot_id)
    if plot is None:
        abort(404)
    year = form_int("year", minimum=2000, maximum=2100)
    if year is None:
        abort(400)
    entry = plot.year(year)
    if entry is None:
        entry = PlotYear(year=year)
        plot.years.append(entry)
    variety_id = form_int("variety_id")
    entry.variety_id = variety_id if variety_id and db.session.get(Variety, variety_id) else None
    entry.planted_on = form_date("planted_on")
    entry.harvested_on = form_date("harvested_on")
    entry.seed_note = form_str("seed_note", 200) or None
    entry.note = form_str("note", 2000) or None
    if current_user.is_admin:
        crop = form_str("crop", 100)
        if crop != (entry.crop or ""):
            entry.crop, entry.crop_eppo = crop or None, None
        if entry.variety_id and not entry.crop:
            entry.crop, entry.crop_eppo = "Kartoffel", "SOLTU"   # Datenwert wie aus der Schlagkartei
    db.session.commit()
    flash(_("{plot}: Anbaujahr {year} gespeichert.", plot=plot.name, year=year), "success")
    return redirect(url_for("fields.field_detail", plot_id=plot.id) + "#jahr-" + str(year))


# --- Fruchtfolge ----------------------------------------------------------------------

@bp.route("/fruchtfolge")
@login_required
def rotation_page():
    """Fruchtfolge über die Jahre: tatsächliche Kultur, sonst geplant (Fruchtfolgeplan), Hinweis auf Anbaupausen."""
    this_year = date.today().year
    # nur Jahre, in denen wirklich eine Kultur steht (tatsächlich oder geplant)
    data_years = sorted({y for (y,) in db.session.query(PlotYear.year).filter(or_(
        PlotYear.crop.isnot(None), PlotYear.planned_crop.notin_(["-", ""]))).distinct()})
    last = max([this_year + 1, *data_years])
    first = request.args.get("von", type=int) or max(min([this_year, *data_years]), this_year - 3, last - 9)
    first = max(2000, min(first, last))
    years = list(range(first, last + 1))
    plots = Plot.query.filter_by(active=True).order_by(Plot.name).all()
    if request.args.get("kartoffeln") == "1":
        plots = [p for p in plots if any("kartoffel" in (py.shown_crop or "").lower() for py in p.years)]
    rows, summary = fielddoc.rotation(plots, years)
    return render_template("fields/rotation.html", rows=rows, summary=summary, years=years, this_year=this_year,
                           first=first, only_potatoes=request.args.get("kartoffeln") == "1",
                           first_options=list(range(min([this_year - 10, *data_years]), last + 1)))


# --- Pflanzenschutz -------------------------------------------------------------------

@bp.route("/pflanzenschutz")
@login_required
def protection():
    """Pflanzenschutz im Erntejahr über alle Schläge: laufende Wartezeiten und eingesetzte Mittel."""
    year, years = _year_arg()
    rows, used = fielddoc.protection_overview(year)
    if request.args.get("laufend") == "1":
        rows = [r for r in rows if r["running"]]
    return render_template("fields/protection.html", year=year, years=years, rows=rows, used=used,
                           only_running=request.args.get("laufend") == "1", today=date.today())


@bp.route("/pflanzenschutz/mittel", methods=["POST"])
@admin_required
def protection_product():
    """Wartezeit eines Mittels pflegen (gilt, wenn die Bemerkung in Myfarm24 keine nennt)."""
    name = form_str("name", 150)
    covered = request.form.get("wait", "").strip().upper() == "F"
    days = None if covered else form_int("wait", minimum=0, maximum=365)
    try:
        fielddoc.save_product(name, days, covered)
    except fields.FieldImportError as e:
        flash(str(e), "danger")
    else:
        db.session.commit()
        flash(_("Wartezeit für „{name}“ gespeichert.", name=name), "success")
    year = form_int("year", minimum=2000, maximum=2100)
    return redirect(url_for("fields.protection", jahr=year) + "#mittel")


# --- Luftbild -----------------------------------------------------------------------

def _image_response(data):
    mimetype = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    response = Response(data, mimetype=mimetype)
    response.headers["Cache-Control"] = "private, max-age=2592000"
    return response


@bp.route("/luftbild")
@login_required
def aerial_image():
    """Luftbild für den sichtbaren Ausschnitt der Schlagkarte (bbox = Länge1,Breite1,Länge2,Breite2)."""
    bbox = aerial.parse_bbox(request.args.get("bbox"))
    width, height = request.args.get("w", type=int), request.args.get("h", type=int)
    if bbox is None or not width or not height:
        abort(400)
    try:
        return _image_response(aerial.image(bbox, width, height))
    except aerial.AerialError as e:
        current_app.logger.warning("Luftbild nicht verfügbar: %s", e)
        # bewusst kein 502/504: die ersetzt ein Proxy wie Cloudflare durch seine eigene Fehlerseite
        return Response(_("Luftbild nicht verfügbar."), status=424, mimetype="text/plain",
                        headers={"Cache-Control": "no-store"})


@bp.route("/luftbild/test")
@admin_required
def aerial_test():
    """Für die Verwaltung: Luftbild rund um den Hof-Standort (oder Fehlermeldung des Dienstes)."""
    location = weather.parse_location(Setting.all().get("farm_location")) or (52.4, 10.5)
    lat, lon = location
    bbox = (lon - 0.008, lat - 0.005, lon + 0.008, lat + 0.005)
    if request.args.get("info"):
        return Response(aerial.diagnose(bbox), mimetype="text/plain")
    try:
        return _image_response(aerial.image(bbox, 800, 500))
    except aerial.AerialError as e:
        return Response(_("Luftbild nicht verfügbar.") + "\n\n" + str(e), mimetype="text/plain",
                        headers={"Cache-Control": "no-store"})
