"""Views by description: Claude's operations tried on a copy, the checks read back, the kept ones applied as one batch.

The Claude API is a stand-in that answers from a script, so the loop, the tool, the route and the undo are tested
without a network or a key.
"""
import json
from types import SimpleNamespace

import pytest

from builder import assistant, package, views
from builder_harness import SAMPLE_PACKAGE, MemoryWorkspaceStore, StandInUser, make_app

ROLES = ["admin", "approver", "ingestion_manager", "member"]


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _use(ops, n=1, **extra):
    return SimpleNamespace(type="tool_use", id=f"tu_{n}", name=assistant.TOOL, input={"operations": ops, **extra})


def _response(*blocks, stop=None):
    stop = stop or ("tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn")
    return SimpleNamespace(content=list(blocks), stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=10, cache_read_input_tokens=0, output_tokens=5))


class ScriptedClaude:
    """``client.with_options(...).beta.messages.create(**kw)``, answering from a list; it keeps every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def with_options(self, **_):
        return self

    def _create(self, **kw):
        self.requests.append(json.loads(json.dumps(kw, default=lambda o: o.__dict__)))
        return self.responses.pop(0)


SPINE = {"op": "spine.create", "key": "SPINE-mandanten", "name": "Mandanten", "types": ["Organization"],
         "level_name": "Kunden"}
LEVEL = {"op": "level.add", "spine": "SPINE-mandanten",
         "level": {"name": "Mandate", "types": ["Mandate"], "link": {"relation": "client_of", "direction": "in"}}}


@pytest.fixture
def model():
    return package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model


def _results(request):
    """The tool results the last request carried back to Claude."""
    return [json.loads(block["content"]) for block in request["messages"][-1]["content"]]


class TestTheLoop:
    def test_operations_are_tried_and_the_kept_ones_returned(self, model):
        claude = ScriptedClaude(_response(_use([SPINE, LEVEL])), _response(_text("Die Hierarchie Mandanten steht.")))
        answer = assistant.answer(claude, "claude-opus-5-5", model, "Kunden mit ihren Mandaten", roles=ROLES)
        assert answer.reply == "Die Hierarchie Mandanten steht." and answer.rounds == 2
        assert [op["op"] for op in answer.operations] == ["spine.create", "level.add"]
        back = _results(claude.requests[1])[0]
        assert [r.get("accepted") for r in back["results"]] == [True, True]
        assert not [f for f in back["checks"]["findings"] if f.get("item") == "SPINE-mandanten"]
        # the draft itself is untouched: the route applies the operations
        assert not any(s["key"] == "SPINE-mandanten" for s in views.spines_of(model))

    def test_a_refused_operation_is_said_and_not_kept(self, model):
        wrong = {"op": "level.add", "spine": "SPINE-nirgends", "level": {"name": "x", "types": ["Mandate"]}}
        foreign = {"op": "type.create", "key": "Invoice"}
        claude = ScriptedClaude(_response(_use([SPINE, wrong, foreign])), _response(_use([LEVEL], n=2)),
                                _response(_text("Fertig.")))
        answer = assistant.answer(claude, "claude-opus-5-5", model, "Kunden und Mandate", roles=ROLES)
        first = _results(claude.requests[1])[0]["results"]
        assert first[0]["accepted"] and "gibt es nicht" in first[1]["refused"]
        assert first[2]["refused"] == "not an operation of the views assistant"
        assert [op["op"] for op in answer.operations] == ["spine.create", "level.add"]

    def test_the_checks_come_back_for_claude_to_fix(self, model):
        no_link = {"op": "level.add", "spine": "SPINE-mandanten", "level": {"name": "Mandate", "types": ["Mandate"]}}
        claude = ScriptedClaude(_response(_use([SPINE, no_link])), _response(_text("…")))
        assistant.answer(claude, "claude-opus-5-5", model, "x", roles=ROLES)
        checks = _results(claude.requests[1])[0]["checks"]
        assert checks["critical"] + checks["warnings"] >= 1
        assert any(f["item"] == "SPINE-mandanten" for f in checks["findings"])

    def test_reset_starts_over_from_the_draft(self, model):
        claude = ScriptedClaude(_response(_use([SPINE])), _response(_use([], n=2, reset=True)), _response(_text("Nichts.")))
        assert assistant.answer(claude, "claude-opus-5-5", model, "x", roles=ROLES).operations == []

    def test_what_claude_is_sent(self, model):
        claude = ScriptedClaude(_response(_text("Was meinen Sie mit Übersicht?")))
        assistant.answer(claude, "claude-opus-5-5", model, "eine Übersicht", roles=ROLES,
                         history=[{"role": "assistant", "text": "dropped: a history starts with the person"},
                                  {"role": "user", "text": "vorher"}, {"role": "assistant", "text": "ja"}])
        request = claude.requests[0]
        assert request["model"] == "claude-opus-5-5" and request["fallbacks"] == "default"
        assert [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]
        instructions, defs = request["system"][0]["text"], json.loads(request["system"][1]["text"].split("\n", 1)[1])
        assert '{"op": "spine.create"' in instructions and "{tool}" not in instructions and "{{" not in instructions
        assert {t["key"] for t in defs["types"]} >= {"Organization", "Mandate", "Deadline"}
        assert {"key", "domain", "range"} <= set(defs["relations"][0])

    def test_an_empty_description_is_refused_before_any_call(self, model):
        claude = ScriptedClaude()
        with pytest.raises(assistant.AssistantError) as caught:
            assistant.answer(claude, "claude-opus-5-5", model, "  ")
        assert caught.value.status == 400 and claude.requests == []

    def test_out_of_time_keeps_what_was_checked(self, model):
        now = iter([0.0, 0.0, 200.0])
        claude = ScriptedClaude(_response(_use([SPINE])))
        answer = assistant.answer(claude, "claude-opus-5-5", model, "x", roles=ROLES, clock=lambda: next(now))
        assert "Zeit" in answer.reply and [op["op"] for op in answer.operations] == ["spine.create"]


class TestTheRoute:
    @pytest.fixture
    def setup(self):
        user = StandInUser({"admin"}, "Alex Beispiel")
        store = MemoryWorkspaceStore({str(user.id): user.display_name})
        claude = ScriptedClaude()
        app = make_app(user, store, tenant="t-1", roles=ROLES, assistant=(claude, "claude-opus-5-5"))
        model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
        wid = store.create(name="Beispiel AG", model=model, actor_id=user.id, source={"kind": "zip"}, tenant="t-1")
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["builder_workspace"] = wid
        return SimpleNamespace(c=c, store=store, wid=wid, claude=claude, app=app)

    def test_the_answer_is_one_batch_and_one_undo_takes_it_back(self, setup):
        s = setup
        s.claude.responses += [_response(_use([SPINE, LEVEL])), _response(_text("Steht."))]
        body = s.c.post(f"/builder/api/w/{s.wid}/views/assistant", json={"message": "Kunden mit Mandaten"}).get_json()
        assert body["success"] and body["reply"] == "Steht." and body["operations"] == 2
        assert body["history"]["undo"] == 1
        assert any(sp["key"] == "SPINE-mandanten" for sp in body["views"]["spines"])
        undone = s.c.post(f"/builder/api/w/{s.wid}/undo?view=views").get_json()
        assert undone["success"] and not any(sp["key"] == "SPINE-mandanten" for sp in undone["views"]["spines"])

    def test_a_question_back_changes_nothing(self, setup):
        s = setup
        s.claude.responses.append(_response(_text("Für wen ist die Ansicht?")))
        body = s.c.post(f"/builder/api/w/{s.wid}/views/assistant", json={"message": "eine Ansicht"}).get_json()
        assert body["success"] and body["changes"] == 0 and body["history"]["undo"] == 0

    def test_the_page_offers_it_and_says_when_it_is_not_set_up(self, setup):
        assert "Bauen lassen" in setup.c.get("/builder/ansichten").get_data(as_text=True)
        user = StandInUser({"admin"})
        store = MemoryWorkspaceStore({str(user.id): user.display_name})
        app = make_app(user, store, tenant="t-1", roles=ROLES)
        wid = store.create(name="X", model=package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model,
                           actor_id=user.id, source={"kind": "zip"}, tenant="t-1")
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["builder_workspace"] = wid
        assert "Schlüssel für die Claude API" in c.get("/builder/ansichten").get_data(as_text=True)
        assert c.post(f"/builder/api/w/{wid}/views/assistant", json={"message": "x"}).status_code == 503

    def test_without_a_builder_role_nothing(self, setup):
        s = setup
        s.app.extensions["builder_gate"].user = StandInUser({"member"})
        assert s.c.post(f"/builder/api/w/{s.wid}/views/assistant", json={"message": "x"}).status_code == 403
        assert s.claude.requests == []
