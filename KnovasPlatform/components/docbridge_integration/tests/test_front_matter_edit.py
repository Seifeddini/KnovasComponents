"""MVP-22: a write changes the named values in a Markdown file's front matter and nothing else.

Every case checks the round trip byte for byte: the bytes before and after the replaced value are the original's,
the body (which may name the same key again), key order, comments, line endings and BOM are unchanged, the edited key
reads exactly the new value as brain-runner reads front matter, and every other key reads what it read before.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

SYNC_SRC = Path(__file__).resolve().parents[2] / "sync_component" / "src"
if str(SYNC_SRC) not in sys.path:
    sys.path.insert(0, str(SYNC_SRC))

from sync_component.front_matter_edit import EditError, edit, shown  # noqa: E402

# The runner's own reading of a Markdown file (brain_runner/extractors/markdown.py, sources/base.py decode_text).
_FM = re.compile(r"\A﻿?---\r?\n(.*?)\r?\n---\r?\n", re.S)


def runner_reads(data: bytes) -> tuple[dict, str]:
    text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    m = _FM.match(text)
    assert m, "the runner finds the front matter"
    return (yaml.safe_load(m.group(1)) or {}), text[m.end():]


def changed_span(before: bytes, after: bytes) -> tuple[bytes, bytes]:
    """The one stretch of bytes that differs: (what it was, what it is)."""
    i = 0
    while i < min(len(before), len(after)) and before[i] == after[i]:
        i += 1
    j = 0
    while j < min(len(before), len(after)) - i and before[-1 - j] == after[-1 - j]:
        j += 1
    return before[i:len(before) - j], after[i:len(after) - j]


def check(before: bytes, values: dict) -> bytes:
    after = edit(before, values)
    fm_before, body_before = runner_reads(before)
    fm_after, body_after = runner_reads(after)
    assert body_after == body_before
    assert after.split(b"\n---", 1)[1].split(b"\n", 1)[1] == before.split(b"\n---", 1)[1].split(b"\n", 1)[1], \
        "the body's bytes, line endings included"
    for k, v in values.items():
        assert shown(fm_after[k]) == shown(v), k
    assert {k: v for k, v in fm_after.items() if k not in values} == {k: v for k, v in fm_before.items() if k not in values}
    assert [k for k in fm_after if k in fm_before] == list(fm_before), "key order"
    assert after.startswith(b"\xef\xbb\xbf") == before.startswith(b"\xef\xbb\xbf")
    return after


class TestOnlyTheValueChanges:
    def test_a_plain_value_and_its_comment(self):
        before = b"---\nid: P-1\nsummary: Old text.   # checked yearly\nowner: ops\n---\n# P-1\n"
        after = check(before, {"summary": "New text."})
        assert changed_span(before, after) == (b"Old", b"New")
        assert after == b"---\nid: P-1\nsummary: New text.   # checked yearly\nowner: ops\n---\n# P-1\n"

    def test_crlf_line_endings_stay_crlf(self):
        before = b"---\r\nid: P-1\r\nsummary: Old.\r\nowner: ops\r\n---\r\nBody line one.\r\nBody line two.\r\n"
        after = check(before, {"summary": "New."})
        assert after == before.replace(b"summary: Old.", b"summary: New.")
        assert after.count(b"\r\n") == before.count(b"\r\n") and b"\n" not in after.replace(b"\r\n", b"")

    def test_mixed_line_endings_stay_as_they_are(self):
        before = b"---\nid: P-1\r\nsummary: Old.\n---\r\nbody\r\n"
        assert check(before, {"summary": "New."}) == before.replace(b"Old.", b"New.")

    def test_a_bom_stays(self):
        before = b"\xef\xbb\xbf---\nsummary: Old.\n---\nbody\n"
        after = check(before, {"summary": "New."})
        assert after == b"\xef\xbb\xbf---\nsummary: New.\n---\nbody\n"

    def test_comments_on_their_own_lines_and_key_order_stay(self):
        before = (b"---\n# Who owns it\nowner: ops\n\n# What it says\nsummary: Old.\n  # an indented note\n"
                  b"reviewed_on: 2026-01-10 # last check\n---\nbody\n")
        after = check(before, {"summary": "New.", "reviewed_on": "2026-11-05"})
        assert after == before.replace(b"summary: Old.", b"summary: New.").replace(b"2026-01-10", b"2026-11-05")

    def test_the_key_repeated_in_the_body_is_not_touched(self):
        before = b"---\nsummary: Old.\n---\nsummary: Old.\n\n```yaml\nsummary: Old.\n```\n"
        after = check(before, {"summary": "New."})
        assert after == b"---\nsummary: New.\n---\nsummary: Old.\n\n```yaml\nsummary: Old.\n```\n"

    def test_a_nested_key_of_the_same_name_is_not_the_key(self):
        before = b"---\nmeta:\n  summary: inner\nsummary: Old.\n---\n"
        after = check(before, {"summary": "New."})
        assert after == b"---\nmeta:\n  summary: inner\nsummary: New.\n---\n"


class TestQuotedValues:
    def test_single_quotes_stay_single(self):
        before = b"---\nsummary: 'Old, really.'  # note\n---\nbody\n"
        assert check(before, {"summary": "It's new."}) == b"---\nsummary: 'It''s new.'  # note\n---\nbody\n"

    def test_double_quotes_stay_double_and_escape(self):
        before = b'---\nsummary: "Old \\"one\\"."\n---\nbody\n'
        assert check(before, {"summary": 'A "new" one.'}) == b'---\nsummary: "A \\"new\\" one."\n---\nbody\n'

    def test_a_plain_value_that_would_read_differently_is_quoted(self):
        before = b"---\nsummary: Old.\nnext: x\n---\n"
        after = check(before, {"summary": "Rule: always # check"})
        assert after == b'---\nsummary: "Rule: always # check"\nnext: x\n---\n'

    def test_a_value_that_looks_like_another_type_is_quoted(self):
        before = b"---\nsummary: Old.\n---\n"
        assert check(before, {"summary": "yes"}) == b'---\nsummary: "yes"\n---\n'
        assert check(before, {"summary": "2026-11-05T10:00:00"}) == b'---\nsummary: "2026-11-05T10:00:00"\n---\n'

    def test_a_date_is_written_as_the_runner_reads_a_date(self):
        before = b"---\nreviewed_on: 2026-01-10\n---\n"
        assert check(before, {"reviewed_on": "2026-11-05"}) == b"---\nreviewed_on: 2026-11-05\n---\n"

    def test_numbers_and_booleans(self):
        before = b"---\nlevel: 2\npublic: false\n---\n"
        assert check(before, {"level": 3, "public": True}) == b"---\nlevel: 3\npublic: true\n---\n"


class TestMultiLineValues:
    def test_a_folded_block_keeps_its_header_comment_and_the_blank_line_after_it(self):
        before = b"---\r\nsummary: >- # one sentence\r\n  Old text that\r\n  runs on.\r\n\r\nowner: ops\r\n---\r\nbody\r\n"
        after = check(before, {"summary": "New text."})
        assert after == b"---\r\nsummary: >- # one sentence\r\n  New text.\r\n\r\nowner: ops\r\n---\r\nbody\r\n"

    def test_a_literal_block_keeps_its_indentation(self):
        before = b"---\nsummary: |\n    line one\n    line two\nowner: ops\n---\n"
        after = check(before, {"summary": "first\nsecond"})
        assert after == b"---\nsummary: |-\n    first\n    second\nowner: ops\n---\n"

    def test_a_plain_value_over_several_lines(self):
        before = b"---\nsummary: Old text\n  that runs on.\nowner: ops\n---\n"
        assert check(before, {"summary": "New."}) == b"---\nsummary: New.\nowner: ops\n---\n"

    def test_a_double_quoted_value_over_several_lines(self):
        before = b'---\nsummary: "Old text\n  that runs on."\nowner: ops\n---\n'
        assert check(before, {"summary": "New."}) == b'---\nsummary: "New."\nowner: ops\n---\n'


class TestAValueThatDoesNotExistYet:
    def test_the_key_is_added_at_the_end_of_the_front_matter(self):
        before = b"---\nid: P-1\nsummary: Old.  # note\n---\n# P-1\nreviewed_on: in the body\n"
        after = check(before, {"reviewed_on": "2026-11-05"})
        assert after == b"---\nid: P-1\nsummary: Old.  # note\nreviewed_on: 2026-11-05\n---\n# P-1\nreviewed_on: in the body\n"

    def test_with_the_files_own_line_ending(self):
        before = b"\xef\xbb\xbf---\r\nid: P-1\r\n---\r\nbody\r\n"
        assert check(before, {"summary": "A new one."}) == b"\xef\xbb\xbf---\r\nid: P-1\r\nsummary: A new one.\r\n---\r\nbody\r\n"

    def test_after_a_trailing_comment(self):
        before = b"---\nid: P-1\n# the end\n---\n"
        assert check(before, {"owner": "ops"}) == b"---\nid: P-1\n# the end\nowner: ops\n---\n"

    def test_an_empty_value_gets_one(self):
        before = b"---\nsummary:   # to fill in\nowner: ops\n---\n"
        assert check(before, {"summary": "Filled."}) == b"---\nsummary: Filled.   # to fill in\nowner: ops\n---\n"

    def test_a_null_value_is_replaced(self):
        before = b"---\nsummary: ~\n---\n"
        assert check(before, {"summary": "Filled."}) == b"---\nsummary: Filled.\n---\n"


class TestWhatItRefuses:
    @pytest.mark.parametrize("data", [
        b"# no front matter\nsummary: x\n",
        b"---\n---\nbody\n",                         # the runner reads no block here either
        b"---\nsummary: x\n---",                    # the closing line needs a line end
        b"text first\n---\nsummary: x\n---\n",
        b"---\nsummary: x\n",                       # never closed
    ])
    def test_no_front_matter_as_the_runner_reads_it(self, data):
        with pytest.raises(EditError) as refused:
            edit(data, {"summary": "y"})
        assert refused.value.code == "front_matter_missing"

    @pytest.mark.parametrize("data", [
        b"---\nsummary:\n  - a\n  - b\n---\n",                  # a list
        b"---\nsummary:\n  inner: x\n---\n",                     # a mapping
        b"---\nbase: &s Old.\nsummary: *s\n---\n",               # an alias
        b"---\nsummary: &s Old.\n---\n",                         # an anchor
        b"---\nsummary: !!str Old.\n---\n",                      # a tag
        b"---\nsummary: [a, b]\n---\n",                          # a flow list
        b"---\nsummary: Old.\nsummary: Older.\n---\n",           # the key twice
        b"---\n- a\n- b\n---\n",                                 # not a mapping
        b"---\nsummary: \xff\n---\n",                            # not UTF-8
        b"---\nsummary: [unclosed\n---\n",                       # not YAML
    ])
    def test_a_value_it_cannot_replace_safely(self, data):
        with pytest.raises(EditError) as refused:
            edit(data, {"summary": "New."})
        assert refused.value.code == "edit_unsupported"

    @pytest.mark.parametrize("values", [{}, {"summary": ""}, {"summary": "   "}, {"summary": None}, {"": "x"},
                                        {"summary": ["a"]}, {"a\nb": "x"}])
    def test_no_value_or_no_plain_value(self, values):
        with pytest.raises(EditError):
            edit(b"---\nsummary: Old.\n---\n", values)

    def test_a_refusal_changes_nothing_it_was_given(self):
        data = b"---\nsummary: Old.\nsummary: Older.\n---\n"
        with pytest.raises(EditError):
            edit(data, {"summary": "New."})
        assert data == b"---\nsummary: Old.\nsummary: Older.\n---\n"


def test_several_values_at_once_each_in_place():
    before = (b"\xef\xbb\xbf---\r\nid: POLICY-travel\r\nsummary: 'Book the cheapest fare.'  # yearly\r\n"
              b"reviewed_on: 2026-01-10\r\nstate: active\r\n---\r\n# Travel\r\n\r\nsummary: the body says it again.\r\n")
    after = check(before, {"summary": "Book trains for trips under six hours.", "reviewed_on": "2026-11-05", "owner": "ops"})
    assert after == (b"\xef\xbb\xbf---\r\nid: POLICY-travel\r\nsummary: 'Book trains for trips under six hours.'  # yearly\r\n"
                     b"reviewed_on: 2026-11-05\r\nstate: active\r\nowner: ops\r\n---\r\n# Travel\r\n\r\n"
                     b"summary: the body says it again.\r\n")
