"""Der Korpusgenerator ist ein Werkzeug, kein Teil des RemoteControllers.

Seine Abhaengigkeiten stehen in ``scripts/demo_kanzlei/requirements.txt`` und
nicht in denen des Pakets. Fehlt eines davon, scheiterte bisher schon das
EINSAMMELN dieser beiden Dateien -- und ein Sammelfehler bricht den ganzen
Lauf ab: 166 Tests, die mit dem Generator nichts zu tun haben, liefen nicht
mehr. Genau daran war die CI seit Einfuehrung des Generators rot.

Also: fehlen die Pakete, werden diese Tests uebersprungen und der Rest laeuft.
Damit das in der CI keine stille Luecke wird, macht ``DEMO_CORPUS_REQUIRED``
daraus einen Fehler -- dieselbe Vorgehensweise wie ``PLATFORM_DB_REQUIRED``
in der Plattform.
"""
import os
import sys
import warnings
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

#: Was der Generator braucht: Modulname -> Paketname in requirements.txt.
_REQUIRED = {
    "msgforge": "msgforge",
    "PIL": "Pillow",
    "docx": "python-docx",
    "pymupdf": "pymupdf",
}


def _missing() -> list:
    import importlib.util

    return sorted(
        paket for modul, paket in _REQUIRED.items()
        if importlib.util.find_spec(modul) is None
    )


_MISSING = _missing()

_FEHLT = ", ".join(_MISSING)
_ANLEITUNG = "pip install -r scripts/demo_kanzlei/requirements.txt"
_HINWEIS = (
    f"Korpusgenerator-Tests uebersprungen, es fehlt: {_FEHLT}. {_ANLEITUNG}"
) if _MISSING else ""

if _MISSING:
    if os.getenv("DEMO_CORPUS_REQUIRED", "").strip().lower() in ("1", "true", "yes", "on"):
        raise RuntimeError(
            f"DEMO_CORPUS_REQUIRED ist gesetzt, aber es fehlt: {_FEHLT}. "
            f"{_ANLEITUNG}"
        )
    # Der dokumentierte Weg, in einer conftest das Einsammeln zu unterbinden.
    # Nicht pytest.skip: das wirft beim Laden der conftest und faellt je nach
    # Fassung wieder auf einen Sammelfehler zurueck -- also auf genau das,
    # was hier vermieden werden soll.
    collect_ignore_glob = ["*.py"]
    # Als Warnung, damit es in der Zusammenfassung steht. pytest_report_header
    # ruft pytest nur in den Wurzel-conftests auf, hier unten nicht -- und
    # lautlos uebersprungene Tests sind kaum besser als gar keine.
    warnings.warn(_HINWEIS, stacklevel=1)
