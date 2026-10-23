"""Seiten der Schlagkartei: Schlagkarte (alle Umrisse), Schlag mit Fruchtfolge, Pflanzung, Rodung und Ertrag."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from . import fields
from .extensions import db
from .i18n import gettext as _
from .models import Box, Plot, PlotYear, Variety
from .utils import editor_required, form_date, form_int, form_str

bp = Blueprint("fields", __name__)


def _year_arg():
    years = fields.plot_years()
    default = date.today().year if date.today().year in years or not years else years[0]
    year = request.args.get("jahr", type=int) or default
    return year, sorted(set(years) | {year, date.today().year}, reverse=True)


@bp.route("/schlagkarte")
@login_required
def field_map():
    year, years = _year_arg()
    only_potatoes = request.args.get("kartoffeln") == "1"
    plots = Plot.query.filter_by(active=True).order_by(Plot.name).all()
    if only_potatoes:
        plots = [p for p in plots if (y := p.year(year)) and y.is_potato]
    yields = fields.yields(year)
    rows = []
    for p in plots:
        y = p.year(year)
        kg = yields.get(p.id, {}).get("kg")
        rows.append({"plot": p, "year": y, "kg": kg, "t_ha": fields.t_per_ha(kg, p.area_ha),
                     "color": fields.crop_color(y.crop if y else None, y.crop_eppo if y else None)})
    return render_template(
        "fields/map.html", year=year, years=years, rows=rows, only_potatoes=only_potatoes,
        map_data=fields.map_data(plots, year), legend=fields.crop_legend(plots, year),
        total_ha=sum(float(p.area_ha or 0) for p in plots),
    )


@bp.route("/schlag/<int:plot_id>")
@login_required
def field_detail(plot_id):
    plot = db.session.get(Plot, plot_id)
    if plot is None:
        abort(404)
    this_year = date.today().year
    history = []
    for y in sorted({py.year for py in plot.years} | {this_year}, reverse=True):
        entry = plot.year(y)
        kg = fields.yields(y).get(plot.id, {})
        history.append({"year": y, "entry": entry, "kg": kg.get("kg"), "boxes": kg.get("boxes"),
                        "varieties": kg.get("varieties", {}), "t_ha": fields.t_per_ha(kg.get("kg"), plot.area_ha)})
    outline_ha = sum(fields.ring_area_ha(r) for r in plot.rings) if plot.rings else None
    stored = Box.query.filter(Box.plot_id == plot.id, Box.fill > 0).count()
    return render_template(
        "fields/detail.html", plot=plot, history=history, this_year=this_year, outline_ha=outline_ha,
        stored=stored, map_data=fields.map_data([plot], this_year),
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
