"""The admin upload extracts with the Knovas Connector's text settings.

knovas.env reaches the Connector through RemoteController/.env.generated;
the Platform's container gets only its own .env.generated, where no RC_* key
may appear. The settings that shape the extracted text -- text modes, OCR
engine, DPI, languages -- therefore come to docbridge-web through compose's
environment block, from the same knovas.env, empty meaning each side's
default. Time and size limits do not: the Platform's requests end at 180 s.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parents[1]
REPO = COMPONENT.parents[2]
COMPOSE = REPO / "docker-compose.yml"
SHARED = ("RC_PDF_TEXT_MODE", "RC_DOCX_TEXT_MODE", "RC_OCR_ENGINE", "RC_OCR_DPI", "RC_TESSERACT_LANG")
PLATFORM_OWN = ("RC_EXTRACT_TIMEOUT_SECONDS", "RC_OCR_TIME_BUDGET_SECONDS", "RC_OCR_PAGE_TIMEOUT_SECONDS",
                "RC_OCR_MAX_PAGES", "RC_OCR_WORKERS", "RC_EXTRACT_RLIMIT_AS_MB")


def _docbridge_web_block() -> str:
    if not COMPOSE.is_file():
        pytest.skip("repository-root docker-compose.yml is not in this checkout")
    text = COMPOSE.read_text(encoding="utf-8")
    start = text.index("\n  docbridge-web:\n")
    end = text.index("\n  docbridge-web-nginx:", start)
    return text[start:end]


@pytest.mark.parametrize("name", SHARED)
def test_the_text_settings_come_from_knovas_env_with_the_default_when_unset(name):
    assert re.search(rf"^\s+{name}: \$\{{{name}:-\}}\s*$", _docbridge_web_block(), re.MULTILINE), name


@pytest.mark.parametrize("name", PLATFORM_OWN)
def test_time_and_size_limits_stay_the_platforms_own(name):
    assert not re.search(rf"^\s+{name}:", _docbridge_web_block(), re.MULTILINE), name


def test_the_platform_reads_every_shared_setting():
    source = (COMPONENT / "src" / "knovas_extract_upload.py").read_text(encoding="utf-8")
    for name in SHARED:
        assert f'"{name}"' in source, name
