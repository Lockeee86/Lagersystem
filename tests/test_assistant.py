"""Daten-Agent: Schleife mit nachgebautem Claude-Client (kein Netz, keine Kosten), Limit, Rechte, Datenschutz."""
import json
from datetime import date, datetime, timedelta, UTC
from decimal import Decimal
from types import SimpleNamespace

from app import assistant, chickens, forest, storage
from app.models import AssistantQuery, Household, EggShare


def usage(inp=1000, out=200, read=0, write=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=read,
                           cache_creation_input_tokens=write)


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool(name, tid="t1", **args):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=args)


class FakeClient:
    """Liefert vorbereitete Antworten der Reihe nach und merkt sich jede Anfrage."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def fake(monkeypatch, responses):
    client = FakeClient(responses)
    monkeypatch.setattr(assistant, "_client", lambda: client)
    return client


def test_disabled_without_key(app, as_admin, as_worker):
    app.config["ANTHROPIC_API_KEY"] = ""
    page = as_admin.get("/fragen").get_data(as_text=True)
    assert "Noch nicht eingerichtet" in page and "ANTHROPIC_API_KEY" in page
    as_admin.post("/fragen", data={"frage": "Wie viel liegt im Lager?"})
    assert AssistantQuery.query.count() == 0
    assert as_worker.get("/fragen").status_code == 403                  # nur Admins


def test_question_with_tools(app, db, seed, as_admin, monkeypatch):
    app.config["ANTHROPIC_API_KEY"] = "test"
    (box,) = storage.create_boxes(1, seed["bt"].id, seed["admin"])
    db.session.commit()
    client = fake(monkeypatch, [
        SimpleNamespace(content=[text("Ich schaue nach."), tool("lager")], stop_reason="tool_use", usage=usage()),
        SimpleNamespace(content=[text("Im Lager liegen:\n\n| Sorte | Kisten |\n|---|---|\n| Belana | 23 |\n\n**Fertig.**")],
                        stop_reason="end_turn", usage=usage(500, 300, read=900)),
    ])
    r = as_admin.post("/fragen", data={"frage": "Wie viel liegt im Lager?"})
    q = AssistantQuery.query.one()
    assert r.headers["Location"].endswith(f"/fragen/{q.id}")
    assert q.status == "fertig" and "Belana" in q.answer and q.tool_list == [{"name": "lager", "input": {}}]
    assert q.input_tokens == 1500 and q.output_tokens == 500 and q.cache_read_tokens == 900
    # 1500 × 4 $ + 500 × 20 $ + 900 × 0,20 $ je 1 Mio. Tokens
    assert q.cost_usd == Decimal("0.0162")
    # Anfrage: Modell, Werkzeuge nur lesen, Fallback, Zwischenspeicher
    first = client.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert {t["name"] for t in first["tools"]} == set(assistant.TOOLS)
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in first["tools"])
    # zweite Runde: Werkzeug-Ergebnis als JSON zurück
    result = client.calls[1]["messages"][2]["content"][0]          # Frage, Antwort 1, Werkzeug-Ergebnis
    assert result["type"] == "tool_result" and result["tool_use_id"] == "t1"
    assert "kartoffelkisten" in json.loads(result["content"])
    page = as_admin.get(f"/fragen/{q.id}").get_data(as_text=True)
    assert "<table" in page and "<strong>Fertig.</strong>" in page and "Kartoffellager" in page
    assert "Wie viel liegt im Lager?" in as_admin.get("/fragen").get_data(as_text=True)
    assert as_admin.get(f"/fragen/{q.id}/stand").get_json() == {"status": "fertig"}


def test_refusal_limit_and_errors(app, db, seed, as_admin, monkeypatch):
    app.config["ANTHROPIC_API_KEY"] = "test"
    fake(monkeypatch, [SimpleNamespace(content=[], stop_reason="refusal", usage=usage(100, 0))])
    as_admin.post("/fragen", data={"frage": "Etwas Seltsames"})
    q = AssistantQuery.query.one()
    assert q.status == "fehler" and q.error == "refusal" and "abgelehnt" in q.error_label

    # Monatslimit erreicht → keine neue Frage
    as_admin.post("/fragen/limit", data={"limit": "0,01"})
    assert assistant.limit_usd() == Decimal("0.01")
    q.cost_usd = Decimal("0.02")
    db.session.commit()
    as_admin.post("/fragen", data={"frage": "Noch eine"})
    assert AssistantQuery.query.count() == 1

    # zu viele Schritte
    as_admin.post("/fragen/limit", data={"limit": "20"})
    fake(monkeypatch, [SimpleNamespace(content=[tool("wald", f"t{i}")], stop_reason="tool_use", usage=usage(10, 10))
                       for i in range(assistant.MAX_STEPS)])
    as_admin.post("/fragen", data={"frage": "Endlos"})
    last = AssistantQuery.query.order_by(AssistantQuery.id.desc()).first()
    assert last.error == "too_many_steps" and len(last.tool_list) == assistant.MAX_STEPS

    # hängengebliebene Frage nach einem Neustart
    stuck = AssistantQuery(question="alt", status="laeuft",
                           created_at=datetime.now(UTC) - timedelta(minutes=assistant.STALE_MINUTES + 1))
    db.session.add(stuck)
    db.session.commit()
    as_admin.get("/fragen")
    assert stuck.status == "fehler" and stuck.error == "stale"


def test_tools_read_only_and_no_personal_data(app, db, seed):
    """Alle Werkzeuge laufen, ändern nichts und geben keine Namen von Personen heraus."""
    admin = seed["admin"]
    forest.create_trees(1, 3, places=4, species="Buche", section="Nordhang")
    tree = forest.find_tree("1")
    forest.save_contract(None, admin, "Erika Musterfrau", date(2026, 3, 2), tree.places[:1], years=99)
    chickens.add_eggs(date.today(), 8, 0, None, admin)
    house = Household(name="Familie Beispielmann")
    db.session.add(house)
    db.session.flush()
    db.session.add(EggShare(day=date.today(), household_id=house.id, eggs=6))
    db.session.commit()
    before = AssistantQuery.query.count()
    for name in assistant.TOOLS:
        out, is_error = assistant.call_tool(name, {})
        assert not is_error, (name, out)
        json.loads(out)
        for secret in ("Erika", "Musterfrau", "Beispielmann", admin.username):
            assert secret not in out, (name, secret)
    assert AssistantQuery.query.count() == before and not db.session.dirty and not db.session.new
    assert assistant.call_tool("loeschen", {})[1] is True                 # unbekanntes Werkzeug


def test_markdown_is_escaped():
    html = str(assistant.render_markdown("# Titel\n<script>alert(1)</script>\n\n- **a**\n- b\n\n1. x\n2. y"))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<h4>Titel</h4>" in html and "<ul><li><strong>a</strong></li><li>b</li></ul>" in html and "<ol>" in html
