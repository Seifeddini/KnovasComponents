"""Deutsche Wortformen auf eine gemeinsame Form bringen.

Die Suche vergleicht Wörter der Anfrage mit Wörtern im Dokument. Ohne
Normalisierung ist das ein Zeichenvergleich, und dann ist "abgerechnet" etwas
anderes als "Abrechnung": auf die Frage "Wie wird die Alpenblick Mandantin
abgerechnet?" galt der Satz "3. Honorar. Abrechnung nach Zeitaufwand" als
Stelle ohne jedes gesuchte Wort -- also weder markiert noch als Fundstelle
vorgeschlagen, während Sätze, die bloß den Mandantennamen wiederholen, als
Treffer zählten.

Snowball (deutsch) erledigt den Großteil, lässt aber das eingeschobene "ge" des
Partizips stehen: "abgerechnet" -> "abgerechn", "Abrechnung" -> "abrechn". Bei
einem trennbaren Präfix wird es deshalb zusätzlich entfernt.

Ein führendes "ge" wird NICHT entfernt. Es sähe nach derselben Regel aus, trifft
aber "Gesetz" -> "setz", "Gericht" -> "richt" und "Gebühr" -> "bühr". Der Preis
dafür ist, dass "gekündigt" und "Kündigung" nicht zusammenfinden.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Iterable, List, Sequence

logger = logging.getLogger(__name__)

try:  # pragma: no cover - hängt von der Installation ab, nicht vom Code
    import snowballstemmer

    _STEMMER = snowballstemmer.stemmer("german")
except Exception as exc:  # noqa: BLE001
    # Fehlt das Paket, bleibt es beim Zeichenvergleich. Eine Suche, die
    # Wortformen nicht zusammenbringt, ist schlechter -- aber sie funktioniert.
    _STEMMER = None
    logger.warning(
        "Kein deutscher Wortstammer (%s): die Suche vergleicht Zeichen. "
        "'abgerechnet' und 'Abrechnung' gelten dann als verschiedene Woerter. "
        "snowballstemmer aus requirements.txt installieren.", exc,
    )

#: Ob die Wortformen zusammengefuehrt werden. Fuer scripts/doctor.sh und die
#: Tests: ohne diese Angabe sieht eine stillschweigend schlechtere Suche exakt
#: so aus wie eine, in der die Aenderung nie ausgeliefert wurde.
STEMMING_ACTIVE = _STEMMER is not None

# Trennbare Präfixe, hinter denen das Partizip sein "ge" einschiebt:
# ab|ge|rechnet, ein|ge|reicht, auf|ge|hoben.
_SEPARABLE = (
    "ab", "an", "auf", "aus", "bei", "durch", "ein", "her", "hin", "los",
    "mit", "nach", "über", "um", "unter", "vor", "weg", "zu", "zurück",
)
_GE_INFIX = re.compile(
    r"^(" + "|".join(_SEPARABLE) + r")ge(?=[a-zäöüß]{3,})", re.IGNORECASE
)

# Kürzer normalisiert die Wortform nicht mehr sinnvoll; "AG" oder "Nr" würden
# sonst zu Stämmen, die überall passen.
MIN_STEM_LENGTH = 4

# Die Verbendung, die nicht jede Stemmer-Fassung entfernt. snowballstemmer 2.2.0
# macht aus "abrechnet" nichts, 3.x "abrechn" -- und "Abrechnung" wird in beiden
# zu "abrechn". Ohne diesen Schritt haengt es an der installierten Fassung, ob
# die beiden zusammenfinden, und das faellt niemandem auf: die Suche liefert
# weiter Ergebnisse, nur die falschen.
_VERB_END = re.compile(r"et$")


@lru_cache(maxsize=8192)
def stem(word: str) -> str:
    """Die vergleichbare Form eines Wortes. Leer, wenn es keine gibt."""
    lowered = str(word or "").strip().lower()
    if not lowered:
        return ""
    without_ge = _GE_INFIX.sub(r"\1", lowered)
    stemmed = _STEMMER.stemWord(without_ge) if _STEMMER is not None else without_ge
    # Das "ge" kann auch erst nach dem Stemmen sichtbar werden
    # ("abgerechnet" -> "abgerechn").
    stemmed = _GE_INFIX.sub(r"\1", stemmed)
    shortened = _VERB_END.sub("", stemmed)
    if len(shortened) >= MIN_STEM_LENGTH:
        stemmed = shortened
    return stemmed if len(stemmed) >= MIN_STEM_LENGTH else lowered


def stems(words: Iterable[str]) -> List[str]:
    """Die Stämme, ohne Dopplungen, in der Reihenfolge des Auftretens."""
    out: List[str] = []
    for word in words:
        value = stem(word)
        if value and value not in out:
            out.append(value)
    return out


_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def text_stems(text: str) -> set:
    """Die Stämme aller Wörter eines Textes."""
    return {stem(word) for word in _WORD.findall(str(text or "")) if word}


def contains_stem(text: str, wanted: str) -> bool:
    """Ob in `text` ein Wort steht, das auf `wanted` zurückgeht."""
    if not wanted:
        return False
    return wanted in text_stems(text)


def highlight_prefixes(terms: Sequence[str]) -> List[str]:
    """Womit ein Wort im Dokument beginnen muss, um markiert zu werden.

    Die Oberfläche kann nicht stemmen, deshalb bekommt sie Wortanfänge: der
    Stamm "abrechn" markiert "Abrechnung" und "abrechnen", die ursprüngliche
    Form "abgerechnet" sich selbst. Beides wird gebraucht -- der Stamm steht in
    der eingegebenen Form oft gar nicht drin.
    """
    out: List[str] = []
    for term in terms:
        for candidate in (str(term or "").strip().lower(), stem(term)):
            if len(candidate) >= 3 and candidate not in out:
                out.append(candidate)
    return out
