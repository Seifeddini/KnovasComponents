"""Both customer images: one knovas-extract pin, the extras of the spec, and
never pymupdf-layout.

Both Dockerfiles install knovas-extract from the same two build args --
KNOVAS_EXTRACT_VERSION, and KNOVAS_EXTRACT_GIT_REF (a full commit sha until
the version is on PyPI, then empty) -- so the Connector and the Platform
extract with one library, and a pin bump changes the Dockerfile text (no old
``main`` survives in Docker's layer cache). knovas-extract 0.4's [pdf] extra
is clean, so the CI step that asserts pymupdf-layout (PolyForm-NC) is absent
gates the build: a step with ``continue-on-error`` would let a later bump
ship the non-redistributable package with a green job.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CI = REPO / ".github" / "workflows" / "ci.yml"
RC_DOCKERFILE = REPO / "RemoteController" / "Dockerfile"
PYPROJECT = REPO / "RemoteController" / "pyproject.toml"
PLATFORM = REPO / "KnovasPlatform" / "components" / "docbridge_integration"
LICENSE_STEPS = (
    "- name: No pymupdf-layout (PolyForm-NC) in the Connector image",
    "- name: No pymupdf-layout (PolyForm-NC) in the Platform image",
)
PIN_STEP = 'bash scripts/ci/check_knovas_extract_pin.sh >> "$GITHUB_ENV"'
ASSERT_SCRIPT = "scripts/ci/assert_knovas_extract_pin.py"
LIBRARY_GIT = "git+https://github.com/Seifeddini/knovas-extract-python.git"
#: Spec 9: the Connector's extras; the Platform adds `markdown` (preview).
IMAGES = {
    "connector": (RC_DOCKERFILE, "pdf,ocr,docx,msg,html,rtf,sentences"),
    "platform": (PLATFORM / "Dockerfile", "pdf,ocr,docx,msg,html,rtf,markdown,sentences"),
}
OLD_BUILD_ARGS = ("KNOVAS_EXTRACT_FROM_GIT", "KNOVAS_EXTRACT_REF", "KNOVAS_EXTRACT_EXTRAS")

needs_ci = pytest.mark.skipif(not CI.is_file(), reason="the workflow is not in this checkout")
needs_platform = pytest.mark.skipif(not PLATFORM.is_dir(), reason="the Platform is not in this checkout")


def _step(text: str, step: str) -> str:
    start = text.index(step)
    indent = text[:start].rsplit("\n", 1)[1]
    rest = text[start + len(step):]
    end = rest.find("\n" + indent + "- ")
    return rest if end < 0 else rest[:end]


def _arg(dockerfile: Path, name: str) -> str:
    found = re.findall(rf"^ARG {name}=(.*?)\r?$", dockerfile.read_text(encoding="utf-8"), re.MULTILINE)
    assert len(found) == 1, f"{dockerfile}: ARG {name} is defined {len(found)} times"
    return found[0]


@needs_ci
@pytest.mark.parametrize("name", LICENSE_STEPS)
def test_the_license_step_blocks_the_build_of_each_image(name):
    step = _step(CI.read_text(encoding="utf-8"), name)
    assert "pip show pymupdf-layout" in step
    assert "continue-on-error" not in step


@needs_ci
def test_both_test_jobs_and_both_images_are_held_to_the_pin():
    ci = CI.read_text(encoding="utf-8")
    assert ci.count(PIN_STEP) == 2, "the Platform job and the Connector job"
    # Both test jobs after their requirements, and inside both built images.
    assert ci.count(ASSERT_SCRIPT) == 4
    assert "KNOVAS_EXTRACT_SHA" not in ci


def test_the_connector_pins_a_release_and_at_most_a_full_sha():
    assert re.fullmatch(r"\d+(\.\d+)*((a|b|rc)\d+)?", _arg(RC_DOCKERFILE, "KNOVAS_EXTRACT_VERSION"))
    ref = _arg(RC_DOCKERFILE, "KNOVAS_EXTRACT_GIT_REF")
    assert ref == "" or re.fullmatch(r"[0-9a-f]{40}", ref), "a full sha or PyPI, never a branch"


@needs_platform
def test_both_images_pin_the_same_build():
    platform = IMAGES["platform"][0]
    for name in ("KNOVAS_EXTRACT_VERSION", "KNOVAS_EXTRACT_GIT_REF"):
        assert _arg(RC_DOCKERFILE, name) == _arg(platform, name), name


@pytest.mark.parametrize("image", sorted(IMAGES))
def test_each_image_installs_the_pin_with_its_extras(image):
    dockerfile, extras = IMAGES[image]
    if not dockerfile.is_file():
        pytest.skip(f"{dockerfile} is not in this checkout")
    text = dockerfile.read_text(encoding="utf-8")
    assert f'"knovas-extract[{extras}] @ {LIBRARY_GIT}@${{KNOVAS_EXTRACT_GIT_REF}}"' in text
    assert f'"knovas-extract[{extras}]==${{KNOVAS_EXTRACT_VERSION}}"' in text
    # The build names what it installed and refuses a PyPI install that is
    # not the pin.
    assert "knovas_extract.__version__" in text and 'raise SystemExit(' in text
    # Gone: the fallbacks that could not resolve (>=0.3) or pulled 0.2.0 with
    # pymupdf-layout (>=0.2), the moving `main`, and the old build args.
    for gone in (">=0.3", ">=0.2", "@main"):
        assert gone not in text, gone
    for name in OLD_BUILD_ARGS:
        assert not re.search(rf"\b{name}\b", text), name


def test_the_floors_carry_the_release_and_the_rtf_extra():
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    assert '"knovas-extract[pdf,docx,msg,html,rtf,sentences]>=0.4.0a1",' in pyproject
    for name in OLD_BUILD_ARGS:
        assert not re.search(rf"\b{name}\b", pyproject), name
    requirements = PLATFORM / "requirements.txt"
    if requirements.is_file():
        lines = requirements.read_text(encoding="utf-8").splitlines()
        assert [line for line in lines if line.startswith("knovas-extract")] == [
            "knovas-extract[pdf,docx,msg,html,rtf,markdown,sentences]>=0.4.0a1"
        ]
