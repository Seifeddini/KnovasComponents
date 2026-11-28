"""PMVP-20 context: finding the hit's chunk in a document's text and cutting the context around it (pure).

Work order docs/platform-mvp/work-orders/PMVP-20-context.md, "What to build" 2 and 3: the snippet with its white space
normalised and a leading or trailing ellipsis dropped; first the whole snippet, else the longest run of at least eight
consecutive words of it. Around it ASSISTANT_PASSAGE_CHARS characters, cut at word boundaries, the chunk inside and
centred where the document allows. And the three settings, read per question.
"""
from __future__ import annotations

import logging

import pytest

import knovas_client
from assistant import context as ctx
from assistant.context import MIN_RUN_WORDS, cut_around, find_chunk, tidy

ELL = "\u2026"


def _flat(text):
    return tidy(text).replace("\n", " ")


def _at(snippet, text):
    found = find_chunk(snippet, tidy(text))
    return None if found is None else _flat(text)[found[0]:found[1]]


# --- tidy -------------------------------------------------------------------------------------------------------


def test_white_space_runs_become_one_character_and_line_breaks_stay():
    assert tidy("  a \t b\n\n  c\r\nd\u2028e\xa0f  ") == "a b\nc\nd\ne f"
    assert len(tidy("x  \n  y")) == len(_flat("x  \n  y")) == 3


# --- finding the chunk ------------------------------------------------------------------------------------------


TEXT = (
    "Mietvertrag fuer Wohnraum\n\n"
    "Paragraph 1 Mietobjekt. Vermietet wird die Wohnung im zweiten Stock an der Seefeldstrasse 48 in Zuerich.\n"
    "Paragraph 4 Kuendigung. Die Kuendigungsfrist betraegt drei Monate   zum Ende eines Kalendermonats.\n"
    "Die Kuendigung bedarf der Schriftform und muss der anderen Partei vor Beginn der Frist zugehen.\n"
    "Paragraph 5 Mietzins. Der Mietzins betraegt CHF 2'150 pro Monat und ist im Voraus zahlbar.\n"
)


def test_the_whole_snippet_is_found_with_its_white_space_normalised():
    assert _at("Die Kuendigungsfrist betraegt drei Monate zum Ende eines Kalendermonats.", TEXT) == (
        "Die Kuendigungsfrist betraegt drei Monate zum Ende eines Kalendermonats.")


def test_a_leading_or_trailing_ellipsis_is_dropped():
    assert _at(ELL + "Kuendigungsfrist betraegt drei Monate zum Ende" + ELL, TEXT) == (
        "Kuendigungsfrist betraegt drei Monate zum Ende")
    assert _at("Die Kuendigung bedarf der Schriftform " + ELL, TEXT) == "Die Kuendigung bedarf der Schriftform"


def test_else_the_longest_run_of_at_least_eight_words():
    changed = ("Die Kuendigung bedarf der Schriftform und muss der anderen Partei vor Beginn der Frist zugehen, "
               "sonst ist sie ungueltig.")
    assert _at(changed, TEXT) == (
        "Die Kuendigung bedarf der Schriftform und muss der anderen Partei vor Beginn der Frist")
    # the longest run wins over an earlier, shorter one
    two = ("Vermietet wird die Wohnung im zweiten Stock an XYZ Die Kuendigung bedarf der Schriftform und muss der "
           "anderen Partei vor")
    assert _at(two, TEXT) == "Die Kuendigung bedarf der Schriftform und muss der anderen Partei vor"


def test_fewer_than_eight_words_in_a_row_is_not_found():
    seven = "Die Kuendigung bedarf der Schriftform und muss ANDERS"
    assert MIN_RUN_WORDS == 8
    assert _at(seven, TEXT) is None
    assert _at("Ein Satz, der in diesem Dokument gar nicht vorkommt und auch sonst nirgends steht.", TEXT) is None


def test_a_run_never_spans_the_ellipsis_that_joins_two_snippets():
    joined = "Vermietet wird die Wohnung im zweiten Stock " + ELL + " an der Seefeldstrasse 48 in Zuerich."
    # each side has fewer than eight words: nothing is found, although the words stand together in the text
    assert _at(joined, TEXT) is None
    longer = ("Vermietet wird die Wohnung im zweiten Stock an " + ELL
              + " betraegt drei Monate zum Ende eines Kalendermonats. Die Kuendigung")
    # eight words on one side, nine on the other: the longer run
    assert _at(longer, TEXT) == "betraegt drei Monate zum Ende eines Kalendermonats. Die Kuendigung"


def test_a_match_does_not_begin_or_end_inside_a_word():
    text = "Siehe Art. 55 und danach Art. 5."
    assert find_chunk("Art. 5", tidy(text)) == (text.index("Art. 5."), text.index("Art. 5.") + 6)
    assert find_chunk("mietvertrag fuer", tidy("Untermietvertrag fuer Wohnraum")) is None
    assert find_chunk("vertrag fuer Wohnraum", tidy("Untermietvertrag fuer Wohnraum")) is None
    # a mark beside the match is no word
    assert find_chunk("Die Frist gilt", tidy("(Die Frist gilt)")) == (1, 15)


def test_the_first_of_several_places_counts():
    text = "Die Frist gilt. Mehr Text dazwischen. Die Frist gilt."
    assert find_chunk("Die Frist gilt.", tidy(text)) == (0, 15)


def test_empty_snippet_or_text_is_not_found():
    assert find_chunk("", tidy(TEXT)) is None
    assert find_chunk(ELL, tidy(TEXT)) is None
    assert find_chunk("Die Kuendigungsfrist betraegt", "") is None


def test_a_snippet_of_many_words_is_found_quickly():
    import time

    words = [f"w{i}" for i in range(400)]
    text = " ".join(words) + " " + " ".join(f"v{i}" for i in range(3000))
    snippet = " ".join(words[:200]) + " X " + " ".join(words[201:400])
    started = time.monotonic()
    found = find_chunk(snippet, tidy(text))
    assert time.monotonic() - started < 2.0
    assert found is not None and _flat(text)[found[0]:found[1]] == " ".join(words[:200])   # 200 words, not 199


# --- cutting the context ------------------------------------------------------------------------------------------


def _words(n, prefix="w"):
    return " ".join(f"{prefix}{i:04d}" for i in range(n))


def test_the_context_is_cut_at_word_boundaries_with_the_chunk_centred():
    before, after = _words(300, "a"), _words(300, "b")
    chunk = "Die Kuendigungsfrist betraegt drei Monate."
    text = f"{before} {chunk} {after}"
    start = text.index(chunk)
    out = cut_around(text, start, start + len(chunk), 300)
    assert chunk in out and len(out) <= 300
    assert out.split(" ")[0] in before.split(" ") and out.split(" ")[-1] in after.split(" ")
    left, right = out.index(chunk), len(out) - out.index(chunk) - len(chunk)
    assert abs(left - right) <= 12                             # centred, give or take a word
    assert len(out) >= 300 - 2 * 6                             # at most a word lost at each edge


def test_at_the_start_or_end_of_the_document_the_window_takes_more_from_the_other_side():
    chunk = "Die Kuendigungsfrist betraegt drei Monate."
    text = f"{chunk} {_words(300)}"
    out = cut_around(text, 0, len(chunk), 300)
    assert out.startswith(chunk) and 280 <= len(out) <= 300
    text = f"{_words(300)} {chunk}"
    start = text.index(chunk)
    out = cut_around(text, start, len(text), 300)
    assert out.endswith(chunk) and 280 <= len(out) <= 300


def test_a_short_document_is_returned_whole():
    text = "Kurz. Die Kuendigungsfrist betraegt drei Monate. Ende."
    start = text.index("Die")
    assert cut_around(text, start, start + len("Die Kuendigungsfrist betraegt drei Monate."), 1200) == text


def test_an_edge_inside_a_word_never_moves_into_the_chunk():
    text = "Vorlaufenderlangeswortohneende" + "Chunk mit Text" + "Nachlaufenderlangeswortohneende"
    start = text.index("Chunk")
    out = cut_around(text, start, start + len("Chunk mit Text"), 30)
    assert "Chunk mit Text" in out and len(out) <= 30


def test_a_chunk_as_long_as_the_limit_comes_alone():
    text = "a " * 50 + "x" * 120 + " b" * 50
    start = text.index("x")
    assert cut_around(text, start, start + 120, 100) == "x" * 120


def test_line_breaks_inside_the_context_stay():
    text = tidy("Ziffer 2\n\n500 Franken sind geschuldet.\nDie Kuendigungsfrist betraegt drei Monate.\nEnde")
    found = find_chunk("Die Kuendigungsfrist betraegt drei Monate.", text)
    out = cut_around(text, found[0], found[1], 1200)
    assert out == "Ziffer 2\n500 Franken sind geschuldet.\nDie Kuendigungsfrist betraegt drei Monate.\nEnde"


# --- the settings ---------------------------------------------------------------------------------------------------


def test_the_passage_limit_is_the_clients_and_the_part_is_the_cores_most():
    assert ctx.DEFAULT_PASSAGE_CHARS == knovas_client.DEFAULT_PASSAGE_CHARS == 1_200
    assert ctx.MIN_PASSAGE_CHARS == knovas_client.MIN_PASSAGE_CHARS
    assert ctx.MAX_PASSAGE_CHARS == knovas_client.MAX_PASSAGE_CHARS == 2_000
    assert ctx.PART_CHARS == knovas_client.DOCUMENT_TEXT_MAX_PART_CHARS == 20_000
    assert (ctx.DEFAULT_CONTEXT_DOCS, ctx.DEFAULT_CONTEXT_SECONDS, ctx.MAX_PARTS) == (8, 8.0, 3)
    assert (ctx.MAX_CONTEXT_DOCS, ctx.MAX_CONTEXT_SECONDS) == (8, 8.0)


def test_the_settings_are_read_per_question(monkeypatch):
    for name in ("ASSISTANT_CONTEXT_DOCS", "ASSISTANT_CONTEXT_SECONDS", "ASSISTANT_PASSAGE_CHARS"):
        monkeypatch.delenv(name, raising=False)
    assert (ctx.context_docs(), ctx.context_seconds(), ctx.passage_chars()) == (8, 8.0, 1200)
    monkeypatch.setenv("ASSISTANT_CONTEXT_DOCS", "3")
    monkeypatch.setenv("ASSISTANT_CONTEXT_SECONDS", "2.5")
    monkeypatch.setenv("ASSISTANT_PASSAGE_CHARS", "600")
    assert (ctx.context_docs(), ctx.context_seconds(), ctx.passage_chars()) == (3, 2.5, 600)
    monkeypatch.setenv("ASSISTANT_CONTEXT_DOCS", "0")
    assert ctx.context_docs() == 0


@pytest.mark.parametrize("name, wrong, default", [
    ("ASSISTANT_CONTEXT_DOCS", "-1", 8), ("ASSISTANT_CONTEXT_DOCS", "51", 8), ("ASSISTANT_CONTEXT_DOCS", "acht", 8),
    ("ASSISTANT_CONTEXT_DOCS", "\u0663", 8),
    ("ASSISTANT_CONTEXT_SECONDS", "0", 8.0), ("ASSISTANT_CONTEXT_SECONDS", "61", 8.0),
    ("ASSISTANT_CONTEXT_SECONDS", "nan", 8.0), ("ASSISTANT_CONTEXT_SECONDS", "1e3", 8.0),
])
def test_a_wrong_setting_gives_the_default_and_one_warning_that_names_it(monkeypatch, caplog, name, wrong, default):
    monkeypatch.setenv(name, wrong)
    read = ctx.context_docs if name.endswith("DOCS") else ctx.context_seconds
    with caplog.at_level(logging.DEBUG, logger="assistant"):
        assert read() == default
    (line,) = [r.getMessage() for r in caplog.records]
    assert name in line


def test_a_wrong_passage_limit_is_the_default_without_a_second_warning(monkeypatch, caplog):
    monkeypatch.setenv("ASSISTANT_PASSAGE_CHARS", "99")
    with caplog.at_level(logging.DEBUG, logger="assistant"):
        assert ctx.passage_chars() == 1200
    assert caplog.records == []


@pytest.mark.parametrize("name, above, most", [
    ("ASSISTANT_CONTEXT_DOCS", "9", 8), ("ASSISTANT_CONTEXT_DOCS", "50", 8), ("ASSISTANT_CONTEXT_DOCS", "73519", 8),
    ("ASSISTANT_CONTEXT_SECONDS", "8.5", 8.0), ("ASSISTANT_CONTEXT_SECONDS", "60", 8.0),
    ("ASSISTANT_CONTEXT_SECONDS", "731.519", 8.0),
])
def test_a_value_above_the_most_is_cut_to_it_with_a_line_that_holds_no_value(monkeypatch, caplog, name, above,
                                                                              most):
    """The verification: the settings allowed 50 documents and 60 seconds; the founder's limits are 8 and 8."""
    monkeypatch.setenv(name, above)
    read = ctx.context_docs if name.endswith("DOCS") else ctx.context_seconds
    with caplog.at_level(logging.DEBUG, logger="assistant"):
        assert read() == most
    (line,) = [r.getMessage() for r in caplog.records]
    assert name in line and above not in line and "7351" not in line


@pytest.mark.parametrize("value, used", [("2000", 2000), ("2001", 2000), ("22118", 2000), ("100000000", 2000),
                                         ("100", 100)])
def test_the_passage_length_is_at_most_2000(monkeypatch, caplog, value, used):
    """A passage of 22,118 characters was seen in the verification; the client's search says so once."""
    monkeypatch.setenv("ASSISTANT_PASSAGE_CHARS", value)
    with caplog.at_level(logging.DEBUG):
        assert ctx.passage_chars() == used
        assert knovas_client._passage_chars() == used
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == (1 if int(value) > 2000 else 0)
    assert all("ASSISTANT_PASSAGE_CHARS" in line and value not in line for line in lines)


@pytest.mark.parametrize("name", ["ASSISTANT_CONTEXT_DOCS", "ASSISTANT_CONTEXT_SECONDS", "ASSISTANT_PASSAGE_CHARS"])
def test_a_wrong_value_is_not_written_to_the_log(monkeypatch, caplog, name):
    monkeypatch.setenv(name, "Kuendigungsfrist Mueller")
    with caplog.at_level(logging.DEBUG):
        {"ASSISTANT_CONTEXT_DOCS": ctx.context_docs, "ASSISTANT_CONTEXT_SECONDS": ctx.context_seconds,
         "ASSISTANT_PASSAGE_CHARS": knovas_client._passage_chars}[name]()
    (line,) = [r.getMessage() for r in caplog.records]
    assert name in line and "Kuendigung" not in line and "Mueller" not in line
