"""Statische Prüfungen an app.js.

Die Datei wird von keinem Python-Test ausgeführt, und der Browser meldet
nichts: eine zweite Methode gleichen Namens im selben Klassenkörper ist
gültiges JavaScript. Die spätere gewinnt, die frühere ist tot. Genau so ist
die satzweise Markierung der Trefferstellen einmal unbemerkt ausgefallen --
die neue Fassung stand oben, eine vergessene alte weiter unten, und im
Dokument wurde wieder der ganze Absatz markiert.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "src/web_interface/static/js/app.js"

# Methodenkopf auf Klassenebene: genau vier Leerzeichen Einrückung, Name,
# Klammer. Schließt `if (...)`/`for (...)` aus (Schlüsselwörter) und alles
# tiefer Eingerückte (verschachtelte Funktionen, Objektliterale).
_METHOD = re.compile(r"^    (?:static\s+)?(?:async\s+)?([A-Za-z_$][\w$]*)\s*\(", re.MULTILINE)
_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "constructor"}


def _method_names() -> list[str]:
    source = APP_JS.read_text(encoding="utf-8")
    return [name for name in _METHOD.findall(source) if name not in _KEYWORDS]


def test_app_js_defines_every_method_once():
    duplicates = sorted(n for n, count in Counter(_method_names()).items() if count > 1)
    assert not duplicates, (
        "Diese Methoden sind in app.js mehrfach definiert; die letzte Fassung "
        f"überschreibt die vorherigen stillschweigend: {duplicates}"
    )
