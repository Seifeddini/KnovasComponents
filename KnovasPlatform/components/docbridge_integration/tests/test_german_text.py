"""Wortformen, die dasselbe Wort sind.

"Wie wird die Alpenblick Mandantin abgerechnet?" fand den Satz "3. Honorar.
Abrechnung nach Zeitaufwand" nicht: verglichen wurden Zeichen, und
"abgerechnet" ist nun einmal nicht "Abrechnung". Der Satz galt damit als
Stelle ohne jedes gesuchte Wort -- weder markiert noch als Fundstelle
vorgeschlagen, waehrend Saetze, die bloss den Mandantennamen wiederholten,
als Treffer zaehlten.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from german_text import (  # noqa: E402
    contains_stem,
    highlight_prefixes,
    stem,
    stems,
    text_stems,
)


def test_the_participle_and_the_noun_are_the_same_word():
    assert stem("abgerechnet") == stem("Abrechnung") == stem("abrechnen")


def test_case_and_plural_do_not_matter():
    assert stem("Honorar") == stem("Honorare")


def test_a_word_that_only_looks_like_a_participle_keeps_its_meaning():
    """"Gesetz" ist kein Partizip von "setzen", "Gericht" keines von "richten".
    Ein fuehrendes "ge" wird deshalb nicht entfernt -- der Preis dafuer steht
    im Modul."""
    assert stem("Gesetz") != stem("setzen")
    assert stem("Gericht") != stem("richten")
    assert stem("Gebühr") != stem("bühren")


def test_a_name_survives_unchanged():
    assert stem("Alpenblick") == "alpenblick"


def test_a_short_token_is_left_alone():
    """"AG" oder "Nr" duerfen nicht zu Staemmen werden, die ueberall passen."""
    assert stem("AG") == "ag"


def test_a_sentence_is_found_through_the_stem():
    sentence = "3. Honorar. Abrechnung nach Zeitaufwand, Ansatz CHF 350 zzgl. MwSt."
    assert contains_stem(sentence, stem("abgerechnet"))
    assert not contains_stem(sentence, stem("Kuendigung"))


def test_stems_drops_duplicates_and_keeps_the_order():
    assert stems(["Abrechnung", "abgerechnet", "Honorar"]) == [
        stem("Abrechnung"), stem("Honorar"),
    ]


def test_text_stems_ignores_numbers_and_punctuation():
    assert text_stems("CHF 350, 2019-031.") == {"chf"}


def test_the_interface_gets_both_the_typed_word_and_the_stem():
    """Der Stamm markiert "Abrechnung", die getippte Form sich selbst. Der
    Stamm allein reicht nicht: "abgerechnet" faengt nicht mit "abrechn" an."""
    prefixes = highlight_prefixes(["abgerechnet"])
    assert "abgerechnet" in prefixes
    assert any(p != "abgerechnet" and "Abrechnung".lower().startswith(p) for p in prefixes)


def test_empty_input_is_not_an_error():
    assert stem("") == ""
    assert stems([]) == []
    assert highlight_prefixes([]) == []
    assert contains_stem("irgendein Text", "") is False


def test_the_stemmer_is_actually_installed():
    """Ohne Paket vergleicht die Suche wieder Zeichen -- und das sieht in der
    Oberflaeche genau so aus, als waere die Aenderung nie ausgeliefert worden.
    Lieber hier ein roter Test als im Betrieb eine stille Verschlechterung."""
    from german_text import STEMMING_ACTIVE

    assert STEMMING_ACTIVE, (
        "snowballstemmer fehlt. requirements.txt installieren -- sonst finden "
        "'abgerechnet' und 'Abrechnung' nicht zusammen."
    )


def test_the_verb_ending_does_not_depend_on_the_stemmer_version():
    """snowballstemmer 2.2.0 laesst "abrechnet" stehen, 3.x kuerzt auf
    "abrechn" -- und "Abrechnung" wird in beiden zu "abrechn". Ohne eigenen
    Schritt haengt es an der installierten Fassung, ob die Suche den Satz
    findet. Genau so ist die Aenderung einmal wirkungslos ausgeliefert worden:
    getestet gegen 3.1.1, gepinnt auf 2.2.0."""
    assert stem("abrechnet") == stem("Abrechnung") == "abrechn"
