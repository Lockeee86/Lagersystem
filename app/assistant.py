"""Daten-Agent: Fragen in Alltagssprache an die Zahlen des Hofs (Claude über die Anthropic-API).

Der Agent darf **nur lesen**. Er bekommt Werkzeuge, die vorhandene Auswertungen als Zahlen liefern (Kartoffellager,
Erträge, Biogas, Maschinenkosten, Diesel, Bestattungswald, Hühner) – ausdrücklich ohne personenbezogene Daten (keine
Namen, Anschriften, Kunden, Verträge, Haushalte, Mitarbeiter). Jede Frage wird mit den abgerufenen Werkzeugen, dem
Verbrauch und den Kosten in `AssistantQuery` festgehalten; ein Monatslimit (`settings` `assistant_limit_usd`) bremst.

Ablauf: `start()` legt die Frage an und rechnet im Hintergrund-Thread (`run()`), die Seite fragt den Stand ab.
In Tests `assistant._client` ersetzen (kein Netz) – dort läuft `run()` sofort statt im Thread.
"""
import json
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask import current_app
from sqlalchemy import func

from .extensions import db
from .i18n import N_, gettext as _
from .models import AssistantQuery, Setting
from .storage import StorageError

MODEL = "claude-opus-5-5"
# US-Dollar je 1 Mio. Tokens (Claude Opus 5.5): Eingabe, Ausgabe, aus dem Zwischenspeicher gelesen, hineingeschrieben
PRICES = {"input": Decimal("4"), "output": Decimal("20"), "cache_read": Decimal("0.20"), "cache_write": Decimal("5")}
MAX_STEPS = 10                 # so viele Runden (Werkzeug abrufen → weiterdenken) höchstens je Frage
MAX_COST_PER_QUESTION = Decimal("1.50")
DEFAULT_LIMIT_USD = Decimal("20")
STALE_MINUTES = 15             # „läuft“ länger als das → abgebrochen (z. B. Neustart des Servers)
LIMIT_KEY = "assistant_limit_usd"
# Fehler werden als Kennung gespeichert und erst bei der Anzeige übersetzt
ERRORS = {
    "refusal": N_("Die Frage wurde vom Modell abgelehnt."),
    "too_costly": N_("Abgebrochen: Die Frage wurde zu aufwendig."),
    "too_many_steps": N_("Abgebrochen: Zu viele Schritte."),
    "auth": N_("Der API-Schlüssel wurde abgelehnt."),
    "rate": N_("Gerade zu viele Anfragen – bitte gleich noch einmal versuchen."),
    "connection": N_("Keine Verbindung zu Anthropic."),
    "api": N_("Fehler bei Anthropic."),
    "unexpected": N_("Unerwarteter Fehler."),
    "stale": N_("Abgebrochen (keine Antwort)."),
    "empty": N_("Keine Antwort erhalten."),
}


# --- Einstellungen und Kosten --------------------------------------------------------------------

def enabled():
    return bool(current_app.config.get("ANTHROPIC_API_KEY"))


def limit_usd():
    setting = db.session.get(Setting, LIMIT_KEY)
    try:
        return Decimal(setting.value) if setting and setting.value else DEFAULT_LIMIT_USD
    except ArithmeticError:
        return DEFAULT_LIMIT_USD


def save_limit(value):
    try:
        amount = Decimal(str(value).replace(",", ".").strip())
    except ArithmeticError:
        raise StorageError(_("Bitte einen Betrag angeben, z. B. 20."))
    if amount < 0 or amount > 10000:
        raise StorageError(_("Bitte einen Betrag angeben, z. B. 20."))
    Setting.put(LIMIT_KEY, str(amount.quantize(Decimal("0.01"))))


def month_cost(today=None):
    today = today or date.today()
    start = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    total = db.session.query(func.coalesce(func.sum(AssistantQuery.cost_usd), 0)) \
        .filter(AssistantQuery.created_at >= start).scalar()
    return Decimal(total or 0)


def cost_of(usage):
    """Kosten (USD) eines API-Aufrufs aus `response.usage`."""
    parts = {
        "input": getattr(usage, "input_tokens", 0) or 0,
        "output": getattr(usage, "output_tokens", 0) or 0,
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }
    cost = sum(Decimal(n) * PRICES[k] for k, n in parts.items()) / Decimal(1_000_000)
    return parts, cost


# --- Werkzeuge (nur lesen, ohne Personen) ----------------------------------------------------------

def _num(value, digits=1):
    if value is None:
        return None
    if isinstance(value, Decimal):
        value = float(value)
    return round(value, digits) if isinstance(value, float) else value


def tool_lager():
    """Kartoffellager und Schüttlager: Bestand nach Lager, Sorte, Verwendung, Stufe, Größe."""
    from . import bulk
    from .models import Box, BulkStore
    groups = {}
    boxes = Box.query.filter(Box.warehouse_id.isnot(None)).all()
    empty = 0
    for b in boxes:
        if b.is_empty or not b.variety:
            empty += 1
            continue
        key = (b.warehouse.name, b.variety.name, b.usage_label, b.seed_class.name if b.seed_class else None, b.grade)
        g = groups.setdefault(key, {"kisten": 0, "kg": 0, "fill_sum": 0, "aelteste_ernte": None})
        g["kisten"] += 1
        g["kg"] += b.weight_kg
        g["fill_sum"] += b.fill
        if b.harvest_date and (g["aelteste_ernte"] is None or b.harvest_date < g["aelteste_ernte"]):
            g["aelteste_ernte"] = b.harvest_date
    rows = [{"lager": k[0], "sorte": k[1], "verwendung": k[2], "stufe": k[3], "groesse": k[4],
             "kisten": g["kisten"], "kg_geschaetzt": g["kg"], "fuellstand_mittel_prozent": round(g["fill_sum"] / g["kisten"]),
             "aelteste_ernte": g["aelteste_ernte"].isoformat() if g["aelteste_ernte"] else None}
            for k, g in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1]))]
    stores = []
    for s in BulkStore.query.order_by(BulkStore.name):
        st = bulk.stock(s)
        stores.append({"lagerort": s.name, "art": s.kind_label, "ware": s.ware or None, "kg": st["kg"],
                       "fuellgrad_prozent": st["percent"]})
    return {"kartoffelkisten": rows, "leere_kisten_im_lager": empty,
            "kisten_gesamt": len(boxes), "schuettlager": stores,
            "hinweis": "kg aus Füllstand × Gewicht des Kistentyps (geschätzt)."}


def tool_ertraege(jahre=None):
    """Erträge je Schlag und Jahr (t/ha, Kultur, Sorten) und je Kartoffelsorte."""
    from . import fields
    from .models import Plot
    years = sorted({int(y) for y in (jahre or []) if 1990 <= int(y) <= 2100}) or fields.plot_years()[:3]
    plots = Plot.query.filter_by(active=True).order_by(Plot.name).all()
    by_plot, by_variety, _best, _best_v = fields.yield_history(plots, years)
    schlaege = []
    for p in plots:
        per_year = {str(y): {"kultur": c["crop"], "kg": c["kg"], "t_ha": c["t_ha"], "sorten": c["varieties"]}
                    for y, c in by_plot.get(p.id, {}).items() if c["crop"] or c["kg"]}
        if per_year:
            schlaege.append({"schlag": p.name, "hektar": _num(p.area_ha, 2), "jahre": per_year})
    sorten = {name: {str(y): {"t_ha": v["t_ha"], "kg": v["kg"], "hektar": round(v["ha"], 2), "schlaege": v["plots"]}
                     for y, v in per_year.items()} for name, per_year in by_variety.items()}
    return {"jahre": years, "schlaege": schlaege, "kartoffelsorten": sorten,
            "hinweis": "Kartoffeln aus den Kisten (höchster Füllstand, geschätzt), Getreide aus gewogenen Fuhren."}


def tool_biogas(jahr=None):
    """Jahresbudget je Einspeisestelle (Höchstbemessungsleistung) und Verfügbarkeit der BHKW."""
    from . import biogas, feedin
    year = int(jahr) if jahr else date.today().year
    points = []
    for s in feedin.overview(year):
        points.append({
            "einspeisestelle": s["point"].name, "jahr": s["year"], "budget_kwh": _num(s["budget"], 0),
            "bisher_kwh": _num(s["used"], 0), "bisher_prozent": s["used_pct"], "jahr_vergangen_prozent": s["year_pct"],
            "prognose_jahresende_kwh": _num(s["projected"], 0), "abweichung_kwh": _num(s["diff"], 0),
            "rest_kwh": _num(s["remaining"], 0), "rest_volllaststunden": _num(s["full_hours_left"]),
            "stunden_je_tag_fuer_rest": _num(s["hours_per_day"]), "ampel": s["level"],
            "monate": [{"monat": r["month"].isoformat() if hasattr(r["month"], "isoformat") else r["month"],
                        "kwh": _num(r["kwh"], 0), "quelle": r["source"]} for r in s["months"]],
        })
    motors = []
    for row in biogas.availability_overview():
        motors.append({
            "bhkw": row["machine"].name,
            "verfuegbarkeit_30_tage_prozent": row["recent"]["percent"],
            "stoerung_stunden_30_tage": _num(row["recent"]["fault_h"]),
            "stoerung_stunden_jahr": _num(row["fault_h_year"]),
            "ausfallkosten_jahr_eur": _num(row["loss_year"], 0),
            "monate": [{"monat": m.isoformat(), "verfuegbarkeit_prozent": a["percent"], "stoerungen": a["faults"]}
                       for m, a in row["months"]],
        })
    return {"einspeisestellen": points, "bhkw": motors}


def tool_maschinen(jahr=None):
    """Kosten je Maschine im Jahr (Diesel, Reparaturen, Bestellungen) und Verbrauch, dazu der Hoftank."""
    from . import fuel, machines
    from .models import FuelEntry, Machine
    year = int(jahr) if jahr else date.today().year
    rows, total, price = machines.cost_overview(year, Machine.query.filter_by(active=True).all())
    items = []
    for r in rows:
        m = r["machine"]
        use = fuel.consumption(m)
        if not r["total"] and not use:
            continue
        items.append({"maschine": m.name, "art": m.category, "diesel_liter": _num(r["liters"]),
                      "diesel_eur": _num(r["diesel"], 2), "reparaturen_eur": _num(r["repairs"], 2),
                      "bestellungen_eur": _num(r["purchases"], 2), "summe_eur": _num(r["total"], 2),
                      "einsatz": _num(r["used"]), "kosten_je_einheit": _num(r["per_unit"], 2), "einheit": r["unit"],
                      "verbrauch": {"wert": _num(use["value"], 2), "einheit": use["unit"]} if use else None})
    monthly = (db.session.query(func.extract("month", FuelEntry.date), func.sum(FuelEntry.liters))
               .filter(FuelEntry.kind == "tanken", FuelEntry.date >= date(year, 1, 1), FuelEntry.date <= date(year, 12, 31))
               .group_by(func.extract("month", FuelEntry.date)).all())
    tank = fuel.status()
    return {"jahr": year, "maschinen": items, "summe_eur": _num(total, 2), "dieselpreis_eur_je_liter": _num(price, 3),
            "getankt_je_monat_liter": {int(m): _num(l) for m, l in monthly},
            "hoftank": {"liter": _num(tank["liters"]), "prozent": tank["percent"], "warnung": tank["low"]}}


def tool_wald():
    """Bestattungswald: Bäume und Plätze (frei/reserviert/belegt) nach Abschnitt, Art und Baumart – ohne Personen."""
    from . import forest
    from .models import FOREST_KINDS, ForestBurial
    data = forest.overview()
    by_kind, by_species = {}, {}
    for row in forest.tree_rows():
        t = row["tree"]
        for target, key in ((by_kind, _(FOREST_KINDS.get(t.kind, t.kind))), (by_species, t.species or "–")):
            e = target.setdefault(key, {"baeume": 0, "frei": 0, "reserviert": 0, "belegt": 0})
            e["baeume"] += 1
            for k in ("frei", "reserviert", "belegt"):
                e[k] += row[k]
    per_year = dict(db.session.query(func.extract("year", ForestBurial.day), func.count(ForestBurial.id))
                    .group_by(func.extract("year", ForestBurial.day)).all())
    return {"baeume": data["trees"], "plaetze": data["places"], "frei": data["frei"], "reserviert": data["reserviert"],
            "belegt": data["belegt"], "abschnitte": {name or "–": v for name, v in data["sections"]},
            "nach_art": by_kind, "nach_baumart": by_species,
            "beisetzungen_je_jahr": {int(y): n for y, n in per_year.items()},
            "beisetzungen_naechste_30_tage": len(data["upcoming"])}


def tool_huehner():
    """Hühner: Eier, Legeleistung, Futter und Verluste je Jahr und der letzten Monate – ohne Haushalte."""
    from . import chickens
    data = chickens.analysis()
    if data is None:
        return {"hinweis": "Noch keine Legedaten."}
    years = [{"jahr": y["year"], "eier": y["eggs"], "knick": y["cracked"], "legeleistung_prozent": y["rate"],
              "richtwert_prozent": y["expected"], "huehner_mittel": _num(y["hens"]), "eier_je_huhn": y["per_hen"],
              "futter_kg": _num(y["feed_kg"]), "futterkosten_eur": _num(y["feed_cost"], 2),
              "futterkosten_je_ei_eur": _num(y["per_egg"], 3), "verluste": y["dead"], "verlustgruende": y["losses"]}
             for y in data["years"]]
    months = [{"monat": m["month"].isoformat(), "eier": m["eggs"], "legeleistung_prozent": m["rate"],
               "futter_kg": _num(m["feed_kg"])} for m in data["months"][:12]]
    return {"jahre": years, "letzte_monate": months}


TOOLS = {
    "lager": (tool_lager, "Kartoffellager (Kisten nach Lager, Sorte, Verwendung, Stufe, Größe, geschätzte kg, älteste "
                          "Ernte) und Schüttlager (kg je Lagerort). Aktueller Stand.", {}),
    "ertraege": (tool_ertraege, "Erträge je Schlag und Jahr (Kultur, kg, t/ha, Sorten) und je Kartoffelsorte (t/ha). "
                                "Ohne Angabe die letzten drei Jahre.",
                 {"jahre": {"type": "array", "items": {"type": "integer"}, "description": "Erntejahre, z. B. [2024, 2025]"}}),
    "biogas": (tool_biogas, "Biogas: Jahresbudget je Einspeisestelle (bisher, Prognose, Rest, Monate) und Verfügbarkeit "
                            "der BHKW (letzte 30 Tage, Monate, Störungsstunden, Ausfallkosten).",
               {"jahr": {"type": "integer", "description": "Jahr, Standard: dieses Jahr"}}),
    "maschinen": (tool_maschinen, "Kosten je Maschine im Jahr (Diesel, Reparaturen, Bestellungen, je Stunde/100 km), "
                                  "Verbrauch, getankte Liter je Monat, Hoftank.",
                  {"jahr": {"type": "integer", "description": "Jahr, Standard: dieses Jahr"}}),
    "wald": (tool_wald, "Bestattungswald: Bäume und Plätze frei/reserviert/belegt nach Abschnitt, Art und Baumart, "
                        "Beisetzungen je Jahr. Keine Personen.", {}),
    "huehner": (tool_huehner, "Hühner: Eier, Legeleistung, Futter, Futterkosten je Ei, Verluste je Jahr und die letzten "
                              "12 Monate.", {}),
}


def tool_definitions():
    defs = []
    for name, (_fn, description, props) in TOOLS.items():
        defs.append({"name": name, "description": description, "strict": True,
                     "input_schema": {"type": "object", "properties": props, "required": [],
                                      "additionalProperties": False}})
    return defs


def call_tool(name, args):
    """Werkzeug ausführen → JSON-Text. Fehler gehen als Text zurück (der Agent kann damit umgehen)."""
    if name not in TOOLS:
        return json.dumps({"fehler": f"unbekanntes Werkzeug {name}"}), True
    fn = TOOLS[name][0]
    try:
        result = fn(**{k: v for k, v in (args or {}).items() if k in TOOLS[name][2]})
    except Exception as e:                      # noqa: BLE001 – der Agent soll weiterarbeiten können
        current_app.logger.warning("Daten-Agent: Werkzeug %s fehlgeschlagen: %s", name, e)
        db.session.rollback()
        return json.dumps({"fehler": "Daten konnten nicht gelesen werden."}), True
    finally:
        db.session.rollback()                   # nur lesen: nichts, was ein Werkzeug angefasst hat, bleibt stehen
    return json.dumps(result, ensure_ascii=False, default=str), False


# --- Agent --------------------------------------------------------------------------------------

def system_prompt(today=None, lang="de"):
    today = today or date.today()
    farm = Setting.all().get("farm_name") or ""
    language = "Polnisch" if lang == "pl" else "Deutsch"
    return (
        f"Du beantwortest Fragen zu den Zahlen eines landwirtschaftlichen Betriebs{' (' + farm + ')' if farm else ''}: "
        "Kartoffellager, Erträge, Biogasanlage, Maschinen, Diesel, Bestattungswald, Hühner. Heute ist "
        f"{today.isoformat()}. Hole dir die Zahlen mit den Werkzeugen – rate nie und erfinde keine Werte. Fehlen "
        "Daten, sag das deutlich und was man eintragen müsste. Rechne nachvollziehbar und nenne Einheiten. "
        "Du kannst nichts ändern, nur lesen. "
        f"Antworte auf {language}, kurz und klar für Praktiker, gern mit einer kleinen Tabelle (Markdown). "
        "Personenbezogene Daten gibt es hier nicht – frag nicht danach."
    )


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"], timeout=120.0, max_retries=2)


def start(question, user, lang="de"):
    """Frage anlegen und (im Hintergrund) beantworten. Committet. Liefert die `AssistantQuery`."""
    question = (question or "").strip()
    if not question:
        raise StorageError(_("Bitte eine Frage eingeben."))
    if len(question) > 2000:
        raise StorageError(_("Die Frage ist zu lang (höchstens 2000 Zeichen)."))
    if not enabled():
        raise StorageError(_("Der Daten-Agent ist nicht eingerichtet (ANTHROPIC_API_KEY fehlt)."))
    if month_cost() >= limit_usd():
        raise StorageError(_("Das Monatslimit für den Daten-Agenten ist erreicht."))
    query = AssistantQuery(question=question, user_id=user.id if user else None, status="laeuft")
    db.session.add(query)
    db.session.commit()
    app = current_app._get_current_object()
    if app.config.get("TESTING"):
        run(query.id, lang)
    else:
        threading.Thread(target=_run_in_app, args=(app, query.id, lang), daemon=True).start()
    return query


def _run_in_app(app, query_id, lang):
    with app.app_context():
        run(query_id, lang)


def run(query_id, lang="de"):
    """Die Schleife: Claude fragt Werkzeuge an, wir liefern die Zahlen, bis die Antwort steht."""
    import anthropic
    query = db.session.get(AssistantQuery, query_id)
    if query is None:
        return
    messages = [{"role": "user", "content": query.question}]
    used_tools, totals, cost = [], {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}, Decimal(0)
    answer, error = None, None
    try:
        client = _client()
        for _step in range(MAX_STEPS):
            response = client.beta.messages.create(
                model=MODEL, max_tokens=16000, system=system_prompt(lang=lang), tools=tool_definitions(),
                messages=messages, output_config={"effort": "medium"}, cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
            parts, step_cost = cost_of(response.usage)
            for k, v in parts.items():
                totals[k] += v
            cost += step_cost
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                error = "refusal"
                break
            uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if response.stop_reason != "tool_use" or not uses:
                answer = "\n\n".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
                if response.stop_reason == "max_tokens":
                    answer = (answer + " …").strip()
                break
            if cost >= MAX_COST_PER_QUESTION:
                error = "too_costly"
                break
            results = []
            for use in uses:
                used_tools.append({"name": use.name, "input": dict(use.input or {})})
                content, is_error = call_tool(use.name, use.input)
                results.append({"type": "tool_result", "tool_use_id": use.id, "content": content,
                                **({"is_error": True} if is_error else {})})
            messages.append({"role": "user", "content": results})
        else:
            error = "too_many_steps"
    except anthropic.AuthenticationError:
        error = "auth"
    except anthropic.RateLimitError:
        error = "rate"
    except anthropic.APIConnectionError:
        error = "connection"
    except anthropic.APIStatusError as e:
        current_app.logger.warning("Daten-Agent: API-Fehler %s: %s", e.status_code, e.message)
        error = "api"
    except Exception:                            # noqa: BLE001
        current_app.logger.exception("Daten-Agent: unerwarteter Fehler")
        error = "unexpected"
    query = db.session.get(AssistantQuery, query_id)
    query.answer = answer
    query.error = error
    if not answer and not error:
        error = "empty"
    query.status = "fertig" if answer and not error else "fehler"
    query.tools = json.dumps(used_tools, ensure_ascii=False)
    query.input_tokens, query.output_tokens = totals["input"], totals["output"]
    query.cache_read_tokens, query.cache_write_tokens = totals["cache_read"], totals["cache_write"]
    query.cost_usd = cost.quantize(Decimal("0.0001"))
    query.finished_at = datetime.now(timezone.utc)
    db.session.commit()


def recent(limit=30):
    expire_stale()
    return AssistantQuery.query.order_by(AssistantQuery.created_at.desc()).limit(limit).all()


def expire_stale(now=None):
    """Fragen, die nach einem Neustart nie fertig werden, als abgebrochen markieren. Committet."""
    now = now or datetime.now(timezone.utc)
    stale = AssistantQuery.query.filter(AssistantQuery.status == "laeuft",
                                        AssistantQuery.created_at < now - timedelta(minutes=STALE_MINUTES)).all()
    for q in stale:
        q.status, q.error = "fehler", "stale"
    if stale:
        db.session.commit()


# --- Anzeige: die Antworten kommen als einfaches Markdown --------------------------------------------

def render_markdown(text):
    """Kleines, sicheres Markdown → HTML (Überschriften, Listen, Tabellen, fett, Code). Alles wird zuerst maskiert."""
    import re
    from markupsafe import Markup, escape

    def inline(s):
        s = str(escape(s))
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return s

    def cells(line):
        return [c.strip() for c in line.strip().strip("|").split("|")]

    out, lines, i = [], (text or "").replace("\r", "").split("\n"), 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)) + 3, 6)          # # → h4 … (kleiner als die Seitenüberschrift)
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip()):
            head = cells(stripped)
            i += 2
            body = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append(cells(lines[i]))
                i += 1
            rows = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
            out.append('<div class="table-responsive"><table class="table table-sm align-middle"><thead><tr>'
                       + "".join(f"<th>{inline(c)}</th>" for c in head) + f"</tr></thead><tbody>{rows}</tbody></table></div>")
            continue
        if re.match(r"^([-*]|\d+[.)])\s+", stripped):
            ordered = bool(re.match(r"^\d", stripped))
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+[.)])\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*]|\d+[.)])\s+", "", lines[i]))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(t)}</li>" for t in items) + f"</{tag}>")
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,4}\s|\||\s*([-*]|\d+[.)])\s)", lines[i].strip()):
            para.append(inline(lines[i].strip()))
            i += 1
        out.append("<p>" + "<br>".join(para) + "</p>")
    return Markup("".join(out))
