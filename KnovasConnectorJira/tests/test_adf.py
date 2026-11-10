"""J-4: a rich-text field (Atlassian Document Format, the description) reaches a record as plain text (adf.py): blocks,
lists, headings and code as text, a link as its text, no HTML, no person's name, bounded. The samples are ADF as Jira
Cloud's REST API v3 gives it, written by hand; no test calls a real Jira."""
from __future__ import annotations

import pytest

from harness import SOURCE, JiraHarness
from knovas_connector_jira import adf
from knovas_connector_jira.records import field_value, scalar
from knovas_connector_kit import contracts


def doc(*content):
    return {"type": "doc", "version": 1, "content": list(content)}


def text(value, *marks):
    node = {"type": "text", "text": value}
    if marks:
        node["marks"] = list(marks)
    return node


def para(*inline):
    return {"type": "paragraph", "content": list(inline)}


def item(*blocks):
    return {"type": "listItem", "content": list(blocks)}


LINK = {"type": "link", "attrs": {"href": "https://tracker.example.invalid/secret-path"}}

DESCRIPTION = doc(
    {"type": "heading", "attrs": {"level": 2}, "content": [text("Goal")]},
    para(text("Ship the "), text("export", {"type": "strong"}), text(" before the "),
         text("review", LINK), text(".")),
    {"type": "bulletList", "content": [
        item(para(text("first"))),
        item(para(text("second")), {"type": "orderedList", "attrs": {"order": 3}, "content": [
            item(para(text("nested a"))), item(para(text("nested b")))]}),
    ]},
    {"type": "codeBlock", "attrs": {"language": "python"}, "content": [text("def f():\n    return 1")]},
    para(text("line one"), {"type": "hardBreak"}, text("line two")),
)


class TestPlainText:
    def test_blocks_lists_headings_and_code_become_text_without_markup(self):
        assert adf.to_text(DESCRIPTION) == (
            "Goal\n\n"
            "Ship the export before the review.\n\n"
            "- first\n"
            "- second\n"
            "  3. nested a\n"
            "  4. nested b\n\n"
            "def f():\n    return 1\n\n"
            "line one\nline two")

    def test_a_link_is_its_text_never_its_address_and_nothing_is_html(self):
        got = adf.to_text(DESCRIPTION)
        assert "review" in got and "tracker.example.invalid" not in got
        assert "<" not in got and "**" not in got and "```" not in got

    def test_a_mention_is_the_account_id_never_the_name(self):
        mention = {"type": "mention", "attrs": {"id": "acct-7", "text": "@Erika Muster", "accessLevel": ""}}
        assert adf.to_text(doc(para(text("ask "), mention))) == "ask @acct-7"

    def test_cards_emoji_dates_and_status_give_their_text(self):
        got = adf.to_text(doc(
            para({"type": "inlineCard", "attrs": {"url": "https://docs.example.invalid/page"}},
                 text(" "), {"type": "emoji", "attrs": {"shortName": ":tada:", "text": "🎉"}},
                 text(" due "), {"type": "date", "attrs": {"timestamp": "1763251200000"}},
                 text(" "), {"type": "status", "attrs": {"text": "BLOCKED", "color": "red"}})))
        assert got == "https://docs.example.invalid/page 🎉 due 2025-11-16 BLOCKED"

    def test_a_task_list_a_table_a_quote_and_a_panel(self):
        got = adf.to_text(doc(
            {"type": "taskList", "attrs": {"localId": "t"}, "content": [
                {"type": "taskItem", "attrs": {"state": "DONE"}, "content": [text("draft")]},
                {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [text("review")]}]},
            {"type": "table", "content": [
                {"type": "tableRow", "content": [
                    {"type": "tableHeader", "content": [para(text("Step"))]},
                    {"type": "tableHeader", "content": [para(text("Owner"))]}]},
                {"type": "tableRow", "content": [
                    {"type": "tableCell", "content": [para(text("Build"))]},
                    {"type": "tableCell", "content": [para(text("Ops"))]}]}]},
            {"type": "blockquote", "content": [para(text("quoted"))]},
            {"type": "panel", "attrs": {"panelType": "info"}, "content": [para(text("note"))]}))
        assert got == "- [x] draft\n- [ ] review\n\nStep | Owner\nBuild | Ops\n\nquoted\n\nnote"

    def test_attachments_media_extensions_and_rules_are_left_out(self):
        got = adf.to_text(doc(
            para(text("before")),
            {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "m", "type": "file",
                                                                            "collection": "c"}}]},
            {"type": "extension", "attrs": {"extensionKey": "x", "parameters": {"secret": "s"}}},
            {"type": "rule"},
            para(text("after"))))
        assert got == "before\n\nafter"

    def test_an_unknown_node_gives_its_contents_text(self):
        assert adf.to_text(doc({"type": "layoutSection", "content": [
            {"type": "layoutColumn", "content": [para(text("left"))]},
            {"type": "layoutColumn", "content": [para(text("right"))]}]})) == "left\n\nright"

    def test_control_characters_trailing_spaces_and_blank_runs_are_tidied(self):
        got = adf.to_text(doc(para(text("a\x00b\x07c   ")), para(text("")), para(text("\n\n\nd"))))
        assert got == "abc\n\nd"

    @pytest.mark.parametrize("empty", [doc(), doc(para()), doc({"type": "rule"})])
    def test_a_document_without_text_is_null(self, empty):
        assert adf.to_text(empty) is None

    def test_the_same_document_gives_the_same_text(self):
        assert adf.to_text(DESCRIPTION) == adf.to_text(DESCRIPTION)


class TestBounded:
    """At most MAX_TEXT_BYTES bytes of UTF-8, cut on a character boundary (verifier KC-3, note 5: the bound was in
    characters, so CJK text reached three times the bytes its comment claimed)."""

    @staticmethod
    def size(text):
        return len(text.encode("utf-8"))

    def test_a_long_text_is_cut_to_the_bound_and_ends_with_an_ellipsis(self):
        got = adf.to_text(doc(para(text("x" * (adf.MAX_TEXT_BYTES + 500)))))
        assert self.size(got) == adf.MAX_TEXT_BYTES and got.endswith("…") and got.startswith("xxx")

    def test_text_at_the_bound_is_kept_whole(self):
        assert adf.to_text(doc(para(text("y" * adf.MAX_TEXT_BYTES)))) == "y" * adf.MAX_TEXT_BYTES
        exactly = "日" * (adf.MAX_TEXT_BYTES // 3) + "ab"           # 3 bytes each, then two: exactly the bound
        assert self.size(exactly) == adf.MAX_TEXT_BYTES and adf.to_text(doc(para(text(exactly)))) == exactly

    @pytest.mark.parametrize("char", ["日", "ü", "😀", "𝄞"])
    def test_multi_byte_text_is_bounded_in_bytes_and_cut_between_characters(self, char):
        long = "a" + char * (adf.MAX_TEXT_BYTES // len(char.encode("utf-8")) + 50)
        got = adf.to_text(doc(para(text(long))))
        assert self.size(got) <= adf.MAX_TEXT_BYTES and got.endswith("…")
        assert got[:-1] == long[:len(got) - 1], "a start of the text, every character whole"
        assert got.encode("utf-8").decode("utf-8") == got and "\ufffd" not in got
        assert self.size(got) > adf.MAX_TEXT_BYTES - len(char.encode("utf-8")) - len("…".encode("utf-8"))

    def test_mixed_scripts_across_blocks_stay_within_the_bound(self):
        blocks = [para(text(("Straße 東京 ✓ " * 40) + str(n))) for n in range(400)]
        got = adf.to_text(doc(*blocks))
        assert self.size(got) <= adf.MAX_TEXT_BYTES and got.endswith("…")

    def test_many_nodes_stop_the_walk_with_an_ellipsis(self):
        got = adf.to_text(doc(*[para(text("w")) for _ in range(adf.MAX_NODES + 10)]))
        assert got.endswith("…") and self.size(got) <= adf.MAX_TEXT_BYTES

    def test_deep_nesting_stops_at_the_depth_bound(self):
        node = para(text("deep"))
        for _ in range(adf.MAX_DEPTH + 20):
            node = {"type": "blockquote", "content": [node]}
        assert adf.to_text(doc(node)) == "…"

    def test_the_bound_is_half_of_one_protocol_value_in_bytes(self):
        assert adf.MAX_TEXT_BYTES * 2 == 64 * 1024


class TestInARecord:
    def test_scalar_renders_a_document_and_leaves_every_other_value_as_before(self):
        assert scalar(DESCRIPTION).startswith("Goal\n\nShip the export")
        assert scalar({"accountId": "acct-1", "displayName": "Person 1"}) == "acct-1"
        assert scalar({"type": "doc"}) is None                       # no content list: not a document
        assert field_value("plain text from API v2") == "plain text from API v2"

    def test_the_description_reaches_the_record_as_text_when_the_settings_name_it(self):
        h = JiraHarness(dict(SOURCE, settings=dict(SOURCE["settings"],
                                                   fields=["summary", "description", "status"])))
        h.jira.issues["DEMO-1"]["fields"]["description"] = DESCRIPTION
        h.jira.issues["DEMO-2"]["fields"]["description"] = None
        stream = h.connector.read(h.source, None)
        assert all(contracts.valid("stream-message.schema.json", m) for m in stream)
        got = {m["external_id"]: m["payload"]["fields"] for m in stream if m["type"] == "record"}
        assert got["DEMO-1"]["description"] == adf.to_text(DESCRIPTION)
        assert got["DEMO-2"]["description"] is None
        assert "tracker.example.invalid" not in str(stream)

    def test_the_description_is_a_default_field_and_absent_when_the_settings_name_fields_without_it(self):
        h = JiraHarness()                                            # no fields in the settings: the defaults
        h.jira.issues["DEMO-1"]["fields"]["description"] = DESCRIPTION
        one = next(m for m in h.connector.read(h.source, None) if m.get("external_id") == "DEMO-1")
        assert one["payload"]["fields"]["description"] == adf.to_text(DESCRIPTION)
        h = JiraHarness(dict(SOURCE, settings=dict(SOURCE["settings"], fields=["summary", "status"])))
        h.jira.issues["DEMO-1"]["fields"]["description"] = DESCRIPTION
        one = next(m for m in h.connector.read(h.source, None) if m.get("external_id") == "DEMO-1")
        assert "description" not in one["payload"]["fields"]
