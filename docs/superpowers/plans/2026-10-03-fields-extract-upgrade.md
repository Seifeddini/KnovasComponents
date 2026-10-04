# Document fields 1.5.0 and knovas-extract 0.4.0a1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the Knovas Platform and the Knovas Connector (`KnovasConnector/`) up to the Knovas 1.5.0 Document-fields documentation and to a released, pinned knovas-extract 0.4.0a1, using the library's output fully and fixing the extraction defects found on `main`.

**Architecture:** Two repositories. Part A changes the library (`knovas-extract-python`): CI fixes, DOCX tables in the text, HTML-only e-mails, fail-soft sentence cap, release hygiene, then a signed PyPI release after the user's OK. Part B works in the KnovasComponents worktree branch `worktree-fields-extract-upgrade`: merge the document-fields branch and the rename onto `main` (INT), fix extraction (EXT), pin the library and add operations data (PIN), complete the field features in the Platform (FLD) and the Connector (RCF), add re-extraction after an extractor upgrade (REX), and verify end to end (VER). Connector and Platform mirror code change together in the same task.

**Tech Stack:** Python 3.11–3.13, Flask + gunicorn, SQLite (Connector state), PostgreSQL 15 (Platform), Jinja2 + vanilla JS, pytest (+ node for JS harnesses), Docker / docker compose, knovas-extract (PyMuPDF, python-docx, mammoth, pysbd, tesserocr, extract-msg, striprtf).

**Spec:** `docs/superpowers/specs/2026-10-03-fields-extract-upgrade-design.md` (approved 2026-10-03). Executors read both documents.

## Global Constraints

- knovas-extract in the images: `knovas-extract[…]==0.4.0a1` from PyPI once released; until then the git ref = the full 40-character merge commit of the library PR (`b5d45404a6df0aa5fb2b934c8ae4efab9fe764a1` stands in until Task A6 records the real one).
- Floors in `KnovasConnector/pyproject.toml` and `docbridge_integration/requirements.txt`: `knovas-extract[…]>=0.4.0a1` (open upper bound).
- Extras: Connector `pdf,ocr,docx,msg,html,rtf,sentences`; Platform `pdf,ocr,docx,msg,html,rtf,markdown,sentences`.
- `pip show pymupdf-layout` must fail in both images (PolyForm-Noncommercial licence).
- User-facing name "Knovas Connector"; unchanged machine names: folder `KnovasConnector/`, service `knovas-connector`, `RC_*` settings, `knovas_connector.*` keys, code identifiers, comments, `docs/superpowers/` records, released release-note sections.
- UI strings German, in the existing tone; docs keep their language.
- Privacy: no field value, document text, snippet, path or e-mail address in logs, metric labels, errors or audit entries — counts, codes, field keys and versions only.
- Document fields (1.5.0): on for every account, Knovas can switch it off; check `fields.staged` after uploads and `where.applied` after searches/lists; never show results as filtered without `"where": {"applied": true}`.
- `where` limits: at most 8 keys, at most 50 values per list.
- Re-sends per cycle: fields `RC_FIELDS_REUPLOAD_PER_CYCLE` (default 100); re-extraction `RC_REEXTRACT_PER_CYCLE` (default 100, range 1..10000).
- Re-extraction of existing documents only after an administrator confirms it (every upload is billed).
- Timeouts: Platform gunicorn `--timeout=${DOCBRIDGE_WEB_TIMEOUT:-180}`, nginx `proxy_read_timeout 180s`; Connector gunicorn `-k gthread --threads 4 --timeout ${RC_GUNICORN_TIMEOUT:-120}`.
- No push, PR, merge or tag without the user's OK — except pushing the library branch `release/0.4.0a1` and opening its PR (approved); merging and tagging it need an explicit "yes".
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; PR bodies end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- After code changes, refresh the knowledge graph from `E:\Knovas`: `graphify update .` (E:\Knovas\CLAUDE.md).

## Review Focus

1. **An existing installation upgrades** (thousands of tracked documents, no extraction stamps): the state-DB migration is idempotent and fast, every document shows as outdated, and nothing re-uploads until an administrator confirms — pinned in REX-2/REX-3.
2. **Mixed versions** (new Platform with an old Connector, or the reverse): missing `/sync/reextract/requeue` or a status without the `extraction` block must show "Knovas Connector zu alt – bitte aktualisieren" / "unbekannt", never an error page — pinned in REX-5 and PIN-4.
3. **Hostile or very large documents** in the new library paths: a 2 000-row DOCX table and HTML e-mails full of unclosed markup must stay linear and within `Limits` — pinned in A2 (hostile markup) and A4 (large table test added below).
4. **Swiss-format filter values typed by people** ("CHF 1'000", "15.03.2024", "GJ 2024") go to Knovas as written, and a server refusal (`invalid_value`) keeps the filter and shows the message instead of dropping the filter — pinned in FLD (filter-rail task).
5. **Concurrent Connector requests under gthread** (a long `POST /sync`, status polls, config writes, a re-extraction requeue during a running cycle): no corrupt config file, no double upload — pinned in EXT (web-server task) and REX-4.

## Execution environment

```bash
WT=/e/Knovas/KnovasComponents/.claude/worktrees/fields-extract-upgrade
SP=/c/Users/siran/AppData/Local/Temp/claude/E--Knovas-KnovasComponents/c6f4754c-3eb8-4feb-9b99-b78ab17ffd1c/scratchpad
LIB=$SP/lib-fix   # library clone, branch release/0.4.0a1
```

`$SP/setup-venvs.sh` (Task SETUP) builds:
- `$SP/venvs/rc` — Connector: the library editable from `$LIB` with `[pdf,ocr,docx,msg,html,rtf,sentences]` plus the Connector's dependencies and `[dev]` extras (not the Connector package itself: an editable install would leave an untracked `knovas_connector.egg-info` in the worktree; the tests import from `src` through `pythonpath`).
- `$SP/venvs/pf` — Platform: `requirements.txt` without the knovas-extract line, `pyodbc>=5.2` on Python 3.13, pytest, the mock API's requirements, and the library editable with `[…,markdown,…]`.
- Docker container `kc-plan-pg` (PostgreSQL 15, 127.0.0.1:55433, platform/testpw, `knovas_platform_test`) — conftest's default `PLATFORM_DB_TEST_DSN`.

Command shorthands used in the tasks:
```bash
rc-pytest() { (cd "$WT/KnovasConnector" && RC_SKIP_CONFIG_VALIDATION=true TESTING=true RC_RATE_LIMIT_ENABLED=true RC_MTLS_DEV_BYPASS=true RC_MTLS_DEV_EMPLOYEE_ID=11111111-1111-1111-1111-111111111111 KNOVAS_INTERNAL_API_URL=http://internal-api:5000 RC_INSTANCE_TOKEN=test-token RC_CLIENT_ID=22222222-2222-2222-2222-222222222222 RC_WATCH_ROOTS=/tmp SEMANTIX_SECURE_BASE_URL=https://knovas:8443 SEMANTIX_CLIENT_CERT_PATH=/certs/client.pem SEMANTIX_CLIENT_KEY_PATH=/certs/client.key SEMANTIX_CA_CERT_PATH=/certs/ca.pem PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "$SP/venvs/rc/Scripts/python" -m pytest -p no:cacheprovider "$@"); }
pf-pytest() { (cd "$WT/KnovasPlatform/components/docbridge_integration" && PYTHONUTF8=1 PLATFORM_DB_REQUIRED=true PYTHONIOENCODING=utf-8 "$SP/venvs/pf/Scripts/python" -m pytest -p no:cacheprovider --ignore=tests/test_experiments_runner_client.py "$@"); }
mock-pytest() { (cd "$WT/KnovasPlatform/mock_knovas_api" && PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "$SP/venvs/pf/Scripts/python" -m pytest -p no:cacheprovider "$@"); }
lib-pytest() { (cd "$LIB" && PYTHONIOENCODING=utf-8 "$SP/lib-ci/venv-full/Scripts/python" -m pytest -p no:cacheprovider "$@"); }
```

Known Windows-only failures (they pass on Linux CI; ignore locally, VER-1 runs the suites on Linux): Connector `test_m365_inventory::test_state_survives_restart_and_resumes_from_the_saved_link`, `test_m365_sync::test_first_cycle_indexes_everything_and_keeps_nothing`, `test_ocr_cache::test_created_with_mode_0600`; Platform `test_identity_broker_key::test_private_key_is_created_readable_only_by_its_owner`, `test_preview_endpoint::test_preview_content_carries_eml_mail_headers`, `test_experiments_frontend::TestNumberFormattingParity::test_format_value_and_diff`, `test_experiments_schema::test_deep_nesting_in_text_is_a_validation_error`; library `test_sentence_line_scaling.py::test_large_weakly_punctuated_text_stays_linear` on a loaded host.

## Order

1. Part A (library) runs in parallel with Part B; Part B needs the library only through the editable install, and the final pin (PIN) after Task A6.
2. Part B: SETUP → INT-1 … INT-6 → EXT → PIN → RCF → FLD → REX → VER. RCF and FLD are independent of EXT/PIN and may run in parallel with them; REX needs EXT and PIN.

### Task SETUP: Test environments

**Files:** none in the repository (`$SP/setup-venvs.sh`, `$SP/setup-venvs.log`).

- [ ] **Step 1: Build the environments**

Run: `bash "$SP/setup-venvs.sh" > "$SP/setup-venvs.log" 2>&1`
Expected: the log ends with `rc 0.4.0a1 …lib-fix…`, `pf 0.4.0a1` and `SETUP DONE`; `docker ps --filter name=kc-plan-pg` shows the container.

- [ ] **Step 2: Baseline**

Run: `rc-pytest -q` and `pf-pytest -q`
Expected: only the known Windows-only failures plus, before INT-2, the 9 merge-caused Platform failures listed in INT-2.

Note: `PYTHONUTF8=1` is required on Windows — without it the node-based frontend harness output is
decoded as cp1252 and two `test_experiments_frontend.py::TestSearchHitCards` tests fail with mojibake.
`pf-pytest` ignores `tests/test_experiments_runner_client.py` on Windows: it needs
`socketserver.UnixStreamServer`, and its collection error would abort the whole run. VER-2 runs it on Linux.

---

## Progress (kept current while executing)

| Task | State | Commit |
|---|---|---|
| A1 library CI green | done | `2767bd0` on `release/0.4.0a1` |
| A2–A4 library (e-mail HTML, sentence cap, DOCX layout) | done | `82f63ee`, `b3f76f6`, `a0383c4` on `release/0.4.0a1` |
| A7 library (MSG categories), release hygiene, selectolax below 1.0 | done | `26c36ef`, `bd16695`, `23f30cc` on `release/0.4.0a1` (pushed) |
| A5, A6 library merge and pin | done: the owner merged the library PR (#20, merge `2c95cbc`, tree equal to `23f30cc`); both Dockerfiles pin it (no PyPI release for now) | `bfac394` |
| SETUP | done (`$SP/setup-venvs.sh`; per-section PostgreSQL containers) | — |
| INT-1 merge, INT-2, INT-4 | done | `ac878c3`, `731dbae`, `3a5443c` |
| INT-3a–d, INT-5, INT-6 | done | merge `de09209` |
| RCF-1 … RCF-8 | done | merge `5589b29` |
| EXT-1 … EXT-15 | done | merges `d122e0b` (lane 1), `a653680` (lane 2) |
| REX-1 … REX-5b | done | merge `9adfb69` |
| PIN-1, PIN-2, PIN-4 | done; the pin stays `b5d4540` (the interim pin `d473496` to the unmerged library branch is left out) | merge `f3d5f03` |
| FLD-1 … FLD-13 | done; the section-end review's findings fixed (`ad10a11`, and `2c48709` … `3f60d18` merged in `f88e8a0`) | merges `acc57f6`, `93747d4`, `f88e8a0` |
| VER-1 | done | `6017d0c` |
| VER-2 … VER-4 | done: Linux suites at the pin and with the library release branch; both images (licence, pin, HTML check, gthread); end to end at the pin and with the release branch; the upgrade path pin → release | — |
| VER-5 | review done (five areas, each finding verified by a second agent); every confirmed finding fixed with a test; push and PR wait for the owner's OK | fixes `7ca8c61` … `e4c0e41` (re-extraction fixes merged in `74110bb`) |
| Beyond the plan | selectolax cap in both components and an HTML check in CI (`d517609`); `email_date` sends the day at the firm (`eeacf27`, `64b2d3c`); the library's git commit in the stamp and the System tab (`a78e2f6`); the library's sentence cap drops citations, not the file (`bc67f65`); the admin upload gets the Connector's text settings (`cf5f712`); no PyMuPDF layout hint in the logs (`23a7b0f`); a registry read Knovas does not answer is remembered for 30 s, for everyone, and the last registry used meanwhile; request threads read it in one 10 s attempt (`0c09718`, `d6b3166`); the preview's value editor keeps stored values with a semicolon (`6893db6`, merged in `5b9d23c`) | — |

Execution model: each section runs in its own git worktree (`$SP/wt-<section>`, branch `sec/<section>`)
from the integration tip `3a5443c`, with its own PostgreSQL container; a test-first implementer commits
each task, three reviewers (conformance, adversarial, full-suite regression) check it, a fixer commits
confirmed fixes. Finished section branches are merged into `worktree-fields-extract-upgrade` in the order
INT → EXT/PIN/REX → RCF → FLD, conflicts resolved by hand, full suites after every merge.

---

## Part A — knovas-extract 0.4.0a1 (library repository)

All Part A tasks run in the library clone `$LIB` (`$SP/lib-fix`, a clone of
`github.com/Seifeddini/knovas-extract-python`, `origin/main` = `b5d4540`). Its working tree already
holds the validated CI probe (7 files, uncommitted, on local branch `probe/ci-green`).

**Session note.** While the session is isolated in the KnovasComponents worktree, git commands in
another repository are refused. Before A1, ask the user to let the session leave the worktree
(`ExitWorktree` with `action: "keep"`); after A5, re-enter it with `EnterWorktree` and
`path: "E:\Knovas\KnovasComponents\.claude\worktrees\fields-extract-upgrade"`. File edits and test
runs in `$LIB` work either way.

Library test environments (created during the audits, reused here):
- `lib-pytest` = `$SP/lib-ci/venv-full/Scripts/python -m pytest` with the library installed editable
  from `$LIB` with all extras (run from `$LIB`). If the venv points at another checkout, re-run
  `$SP/lib-ci/venv-full/Scripts/python -m pip install --no-deps -e "$LIB"`.
- `lib-pytest-default` = `$SP/lib-ci/venv-default/Scripts/python -m pytest` (no extras — the CI
  "unit" leg); re-point it the same way.
- Linux gates (lint, bandit, golden, OCR with live Tesseract) run in Docker exactly like the probe:
  `docker run --rm -v "<LIB as Windows path>:/src:ro" python:3.12-slim bash -c '…'` (command in A1).

### Task A1: CI green on the library

**Files:**
- Modify: `tests/unit/test_ocr_decision.py` (imports)
- Modify: `tests/golden/test_layout_golden.py` (imports)
- Modify: `tests/golden/test_prose_regression.py` (imports)
- Modify: `tests/unit/test_ocr_backend.py` (class `TestCliInvocation`)
- Modify: `src/knovas_extract/_ocr/preprocess.py` (function `pgm_bytes`)
- Modify: `src/knovas_extract/_ocr/pipeline.py` (process-pool `submit`)
- Modify: `src/knovas_extract/_layout/page.py` (function `_ensure_doc`)

**Interfaces:**
- Consumes: nothing.
- Produces: branch `release/0.4.0a1` in `$LIB` whose CI gates pass; later tasks commit on it.

- [ ] **Step 1: Create the branch and confirm the probe is the only change**

```bash
cd "$LIB"
git switch -c release/0.4.0a1
git diff --stat
```
Expected: exactly the 7 files above, `24 insertions(+), 12 deletions(-)`.

- [ ] **Step 2: Confirm each change is the intended one** (the edits, for review)

`tests/unit/test_ocr_decision.py`
```python
# old
import fitz  # type: ignore[import-untyped]
import pytest

from knovas_extract import extract
# new
import pytest

from knovas_extract import extract

fitz = pytest.importorskip("fitz")
```

`tests/golden/test_layout_golden.py`
```python
# old
import pytest
import yaml

from knovas_extract import _layout as L
from knovas_extract._layout import check_invariants
from tests.eval import metrics as M
# new
import pytest

yaml = pytest.importorskip("yaml")
pytest.importorskip("rapidfuzz")

from knovas_extract import _layout as L  # noqa: E402
from knovas_extract._layout import check_invariants  # noqa: E402
from tests.eval import metrics as M  # noqa: E402
```

`tests/golden/test_prose_regression.py`
```python
# old
from knovas_extract import extract
from tests.eval.metrics import bow

pytest.importorskip("fitz")
from tests.synth.pdf_docs import LawFirmDoc, lawfirm_corpus  # noqa: E402
# new
from knovas_extract import extract

pytest.importorskip("fitz")
pytest.importorskip("rapidfuzz")
from tests.eval.metrics import bow  # noqa: E402
from tests.synth.pdf_docs import LawFirmDoc, lawfirm_corpus  # noqa: E402
```

`tests/unit/test_ocr_backend.py`
```python
# old
class TestCliInvocation:
    @pytest.fixture
    def cli(self, monkeypatch, tessdata) -> CliBackend:
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
# new
_FAKE_TESSERACT = os.path.abspath("/usr/bin/tesseract")  # absolute on every host OS


class TestCliInvocation:
    @pytest.fixture
    def cli(self, monkeypatch, tessdata) -> CliBackend:
        monkeypatch.setattr(shutil, "which", lambda name: _FAKE_TESSERACT)
```
and in `test_argv_is_a_fixed_list`:
```python
# old
        assert argv[0] == "/usr/bin/tesseract" and os.path.isabs(argv[0])
# new
        assert argv[0] == _FAKE_TESSERACT and os.path.isabs(argv[0])
```

`src/knovas_extract/_ocr/preprocess.py`
```python
# old
    h, w = arr.shape
    return b"P5\n%d %d\n255\n" % (w, h) + np.ascontiguousarray(arr).tobytes()
# new
    h, w = arr.shape
    data: bytes = np.ascontiguousarray(arr).tobytes()
    return b"P5\n%d %d\n255\n" % (w, h) + data
```

`src/knovas_extract/_ocr/pipeline.py`
```python
# old
        def submit(executor: Executor, i: int) -> Future[Any]:
            ...
            return executor.submit(_process_task, spec, pi, options.retry_psm4, key_suffix(pi.dpi))

    run = run_ocr_schedule(
# new
        def _submit_process(executor: Executor, i: int) -> Future[Any]:
            ...
            return executor.submit(_process_task, spec, pi, options.retry_psm4, key_suffix(pi.dpi))

        submit = _submit_process

    run = run_ocr_schedule(
```

`src/knovas_extract/_layout/page.py`
```python
# old
        DocumentLayoutPass(layout.opts).fit([layout])
    assert layout.doc is not None
    return layout.doc
# new
        DocumentLayoutPass(layout.opts).fit([layout])
    doc = layout.doc
    if doc is None:  # fit() always sets it; checked explicitly so -O cannot drop it
        raise RuntimeError("layout: document statistics missing after fit()")
    return doc
```

- [ ] **Step 3: Run every CI gate**

Windows, extras-free leg (the CI `unit` step):
```bash
cd "$LIB" && PYTHONIOENCODING=utf-8 $SP/lib-ci/venv-default/Scripts/python -m pytest -m unit -q -p no:cacheprovider
```
Expected: `161 passed, 30 skipped` (no collection errors).

Windows, all extras:
```bash
cd "$LIB" && PYTHONIOENCODING=utf-8 $SP/lib-ci/venv-full/Scripts/python -m pytest tests/unit tests/golden -q -p no:cacheprovider
```
Expected: `613 passed` (± the timing test `test_sentence_line_scaling.py::test_large_weakly_punctuated_text_stays_linear`, which fails only on a loaded host; re-run it alone on an idle host).

Linux (lint, bandit, golden, OCR live) — `LIBW` is `$LIB` as a Windows path:
```bash
LIBW='C:\Users\siran\AppData\Local\Temp\claude\E--Knovas-KnovasComponents\c6f4754c-3eb8-4feb-9b99-b78ab17ffd1c\scratchpad\lib-fix'
MSYS_NO_PATHCONV=1 docker run --rm -v "${LIBW}:/src:ro" python:3.12-slim bash -c '
apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends git libmagic1 nodejs npm tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng >/dev/null 2>&1
cp -r /src /work && cd /work && git config --global --add safe.directory /work
pip install -q --root-user-action=ignore hatch
hatch run test -m unit -q -p no:cacheprovider | tail -1
hatch -e lint run all | tail -4
hatch -e sec run run-bandit | grep -E "No issues|Issue"
hatch -e golden run run -q -p no:cacheprovider | tail -1
python -m venv /o && /o/bin/pip install -q --only-binary tesserocr ".[pdf,ocr]" "pytest>=8.3" "pytest-socket>=0.7.0" "hypothesis>=6.115"
/o/bin/pip show pymupdf-layout >/dev/null 2>&1 && echo "LICENCE GATE FAIL" || echo "licence gate ok"
OMP_THREAD_LIMIT=1 /o/bin/python -m pytest tests/unit/test_ocr_decision.py tests/unit/test_ocr_scheduler.py tests/unit/test_ocr_backend.py tests/unit/test_ocr_preprocess.py -q -p no:cacheprovider | tail -1
OMP_THREAD_LIMIT=1 /o/bin/python -m pytest tests -m "needs_tesseract or needs_tesserocr" -q -p no:cacheprovider | tail -1'
```
Expected: `161 passed`; ruff/format/mypy clean and `0 errors` from pyright; `No issues identified.`; `224 passed`; `licence gate ok`; `80 passed`; `5 passed`.

- [ ] **Step 4: Commit**

```bash
cd "$LIB"
git add tests/unit/test_ocr_decision.py tests/golden/test_layout_golden.py tests/golden/test_prose_regression.py tests/unit/test_ocr_backend.py src/knovas_extract/_ocr/preprocess.py src/knovas_extract/_ocr/pipeline.py src/knovas_extract/_layout/page.py
git commit -F - <<'EOF'
fix(ci): green again — guarded optional imports, portable paths, mypy/pyright/bandit

CI on main failed in every leg for test hygiene, not product code: three
test modules imported fitz, rapidfuzz or yaml unguarded, so collection
broke in the extras-free unit leg and in the OCR job's marker run; one test
assumed POSIX paths; mypy saw Any from numpy-less pgm_bytes; pyright
flagged the process-pool submit redeclaration; bandit flagged an assert
that -O would remove. All gates pass on Linux and Windows.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task A2: HTML-only e-mail bodies keep their words and lines

**Files:**
- Create: `src/knovas_extract/_html_text.py`
- Modify: `src/knovas_extract/extractors/eml.py` (`_strip_html`, `_TAG`, `_WS`, callers in `_extract_body`)
- Modify: `src/knovas_extract/extractors/msg.py` (`_strip_html`, `_TAG`, `_WS`, caller)
- Test: `tests/unit/test_html_text.py`, `tests/unit/test_extractors_eml.py`

**Interfaces:**
- Consumes: `knovas_extract._metadata._BIDI_OVERRIDES` (existing frozenset).
- Produces: `knovas_extract._html_text.html_to_text(s: str) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_html_text.py`:
```python
"""HTML-only e-mail bodies → text (EML/MSG): entities, structure, hostile input."""

from __future__ import annotations

import pytest

from knovas_extract._html_text import html_to_text

pytestmark = [pytest.mark.unit]


def test_entities_are_decoded() -> None:
    s = "Gr&uuml;ezi Herr M&uuml;ller, anbei die Offerte &amp; der Vertrag."
    assert html_to_text(s) == "Grüezi Herr Müller, anbei die Offerte & der Vertrag."


def test_paragraphs_and_breaks_keep_their_lines() -> None:
    assert html_to_text("<p>Eins</p><p>Zwei</p>") == "Eins\n\nZwei"
    assert html_to_text("a<br>b<br/>c<BR />d") == "a\nb\nc\nd"
    assert html_to_text("<div>Kopf</div><div>Fuss</div>") == "Kopf\n\nFuss"


def test_table_rows_become_lines_with_cells_joined() -> None:
    s = (
        "<table><tr><td>Betrag</td><td>CHF 12.50</td></tr>"
        "<tr><th>MWST</th><td>8.1%</td></tr></table>"
    )
    assert html_to_text(s) == "Betrag | CHF 12.50\nMWST | 8.1%"


def test_script_style_head_and_comments_are_dropped_with_their_content() -> None:
    s = (
        "<head><title>T</title><style>p{color:red}</style></head>"
        "<!-- intern --><script>alert(1)</script><p>Text</p>"
    )
    assert html_to_text(s) == "Text"


def test_control_and_bidi_characters_from_references_are_dropped() -> None:
    assert html_to_text("a&#x202E;b&#1;c&#x2066;d") == "abcd"


def test_whitespace_is_collapsed_per_line_and_nbsp_is_a_space() -> None:
    assert html_to_text("<p>  viel   &nbsp;&nbsp; Raum  </p>") == "viel Raum"


def test_blank_lines_collapse_to_one() -> None:
    assert html_to_text("<p>a</p><p></p><p></p><p>b</p>") == "a\n\nb"


def test_large_body_stays_linear() -> None:
    body = "<p>" + "Zeile mit Text &amp; Zahl 1'234.50<br>" * 20000 + "</p>"
    out = html_to_text(body)
    assert out.count("\n") == 19999
    assert "&amp;" not in out


@pytest.mark.parametrize(
    "hostile",
    [
        "<!--" * 50000 + "x",  # unterminated comments
        "<script>" * 50000 + "x",  # unclosed script blocks
        "<" * 200000 + "Text",  # angle brackets without a closing ">"
        "a" + " " * 200000 + "|",  # long runs of Unicode spaces before a separator
    ],
)
def test_hostile_markup_is_linear(hostile: str) -> None:
    import time

    t0 = time.perf_counter()
    html_to_text(hostile)
    assert time.perf_counter() - t0 < 2.0
```

Append to `tests/unit/test_extractors_eml.py`:
```python
@pytest.mark.unit
def test_html_only_eml_decodes_entities_and_keeps_lines() -> None:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "Absender <absender@example.ch>"
    msg["To"] = "empfaenger@example.ch"
    msg["Subject"] = "Offerte"
    msg.set_content(
        "<html><body><p>Gr&uuml;ezi Herr M&uuml;ller</p><p>Anbei die Offerte &amp; der Vertrag.</p>"
        "</body></html>",
        subtype="html",
    )
    r = extract(bytes(msg), mime="message/rfc822")
    assert r.content.text == "Grüezi Herr Müller\n\nAnbei die Offerte & der Vertrag."
    assert r.metadata.extra["eml:body_source"] == "text/html"
```
(Check the file's existing imports: it already imports `pytest` and `extract`; add nothing else.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "$LIB" && lib-pytest tests/unit/test_html_text.py tests/unit/test_extractors_eml.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'knovas_extract._html_text'`, and the EML test fails on `Gr&uuml;ezi` / missing line break.

- [ ] **Step 3: Write the implementation**

`src/knovas_extract/_html_text.py`:
```python
"""HTML-only e-mail bodies → plain text (EML and MSG).

A deliberately small converter without an HTML parser, so hostile markup is
never built into a tree (SECURITY.md, e-mail extractors). Block ends become
line breaks, table cells become `` | ``, entities are decoded, and control or
bidi-override characters that a numeric character reference can smuggle in
are dropped (Trojan Source, SECURITY.md promise #7). Linear in the input.
"""

from __future__ import annotations

import html
import re

from knovas_extract._metadata import _BIDI_OVERRIDES

_BLOCK_OPEN = re.compile(r"<(script|style|head|title)\b", re.I)
_PARA_END = re.compile(r"</(?:p|div|table|blockquote|ul|ol|h[1-6])\s*>", re.I)
_LINE_END = re.compile(r"<br\s*/?>|</(?:tr|li)\s*>", re.I)
_CELL_END = re.compile(r"</t[dh]\s*>", re.I)
# `[^<>]*` (not `[^>]+`): an attempt stops at the next "<", so input full of
# unclosed brackets stays linear instead of rescanning to the end each time.
_TAG = re.compile(r"<[^<>]*>")
_SPACES = re.compile(r"[^\S\n]+")  # every whitespace run except newlines, NBSP and U+2003 included
_BLANKS = re.compile(r"\n{3,}")


def _drop_comments(s: str) -> str:
    """Remove ``<!-- \u2026 -->``; an unterminated comment swallows the rest (linear)."""
    out: list[str] = []
    pos = 0
    while True:
        start = s.find("<!--", pos)
        if start < 0:
            out.append(s[pos:])
            return "".join(out)
        out.append(s[pos:start])
        end = s.find("-->", start + 4)
        if end < 0:
            return "".join(out)
        pos = end + 3


def _drop_blocks(s: str) -> str:
    """Remove script/style/head/title elements with their content; an unclosed
    one swallows the rest. One forward scan, no backtracking."""
    low = s.lower()
    out: list[str] = []
    pos = 0
    while True:
        m = _BLOCK_OPEN.search(s, pos)
        if m is None:
            out.append(s[pos:])
            return "".join(out)
        out.append(s[pos : m.start()])
        end = low.find("</" + m.group(1).lower(), m.end())
        if end < 0:
            return "".join(out)
        close = s.find(">", end)
        pos = len(s) if close < 0 else close + 1


def _allowed(c: str) -> bool:
    if c in "\t\n":
        return True
    return c >= " " and c != "\x7f" and c not in _BIDI_OVERRIDES


def _clean_line(line: str) -> str:
    line = _SPACES.sub(" ", line).strip()
    while line.startswith("|"):
        line = line[1:].lstrip()
    while line.endswith("|"):
        line = line[:-1].rstrip()
    return line


def html_to_text(s: str) -> str:
    """Plain text of an HTML e-mail body: words, paragraphs, lines and cells kept."""
    s = _drop_blocks(_drop_comments(s))
    s = _CELL_END.sub(" | ", s)
    s = _PARA_END.sub("\n\n", s)
    s = _LINE_END.sub("\n", s)
    s = _TAG.sub("", s)
    s = html.unescape(s)
    s = "".join(c for c in s if _allowed(c))
    return _BLANKS.sub("\n\n", "\n".join(_clean_line(ln) for ln in s.split("\n"))).strip()
```

`src/knovas_extract/extractors/eml.py` — remove the regex helper and use the new one:
```python
# old
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _strip_html(s: str) -> str:
    """Very small HTML→text — for emails where only text/html is available."""
    s = _TAG.sub(" ", s)
    return _WS.sub(" ", s).strip()
# new
from knovas_extract._html_text import html_to_text as _strip_html
```
(keep the import block sorted: move the new import up into the `knovas_extract` imports; drop `import re` only if nothing else in the module uses it — `grep -n "re\." src/knovas_extract/extractors/eml.py` first.) Update the module docstring sentence "we strip tags with a deliberately-tiny regex" to "we convert it with the small regex converter in `_html_text` (no HTML parser): entities decoded, line structure kept".

`src/knovas_extract/extractors/msg.py` — the same replacement:
```python
# old
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _strip_html(s: str) -> str:
    s = _TAG.sub(" ", s)
    return _WS.sub(" ", s).strip()
# new
from knovas_extract._html_text import html_to_text as _strip_html
```
(same import-order and `re` rule as for eml.py.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "$LIB" && lib-pytest tests/unit/test_html_text.py tests/unit/test_extractors_eml.py tests/unit/test_extractors_msg.py tests/unit/test_metadata_sanitization.py -q`
Expected: PASS. Then `lib-pytest tests/unit -q` — no new failures.

- [ ] **Step 5: Commit**

```bash
cd "$LIB"
git add src/knovas_extract/_html_text.py src/knovas_extract/extractors/eml.py src/knovas_extract/extractors/msg.py tests/unit/test_html_text.py tests/unit/test_extractors_eml.py
git commit -F - <<'EOF'
fix(email): HTML-only bodies decode entities and keep their lines

EML and MSG bodies that only exist as HTML were reduced to one line of text
with entities left in (`Gr&uuml;ezi`): words a search must find were
misspelled and every paragraph boundary was gone. One small regex converter
for both extractors now keeps paragraphs, line breaks and table cells,
decodes entities and drops control or bidi characters smuggled in through
character references. Still no HTML parser on hostile markup.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task A3: Sentence limit is fail-soft

**Files:**
- Modify: `src/knovas_extract/_sentences.py` (`split_sentences`, `split_sentences_for_pages`, docstrings)
- Test: `tests/unit/test_sentence_limit.py`

**Interfaces:**
- Consumes: `Limits.max_sentences` (existing, default 100 000).
- Produces: `split_sentences` / `split_sentences_for_pages` never raise for the sentence count; they
  keep the first `max_sentences` sentences and add one warning each time they stop:
  `sentences: {n} beyond max_sentences ({limit}) omitted` (single text or the page where the cap is
  reached) and `sentences: {p} pages after max_sentences ({limit}) not split` (later pages).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_sentence_limit.py`:
```python
"""`Limits.max_sentences` caps the output instead of failing the document."""

from __future__ import annotations

import pytest

pytest.importorskip("pysbd")

from knovas_extract import Limits, Page, extract  # noqa: E402
from knovas_extract._sentences import split_sentences, split_sentences_for_pages  # noqa: E402

pytestmark = [pytest.mark.unit]


def test_single_text_keeps_the_first_sentences_and_warns() -> None:
    warnings: list[str] = []
    out = split_sentences(
        "Eins ist hier. Zwei ist da. Drei folgt. Vier endet.", Limits(max_sentences=2), warnings=warnings
    )
    assert [s.text for s in out] == ["Eins ist hier.", "Zwei ist da."]
    assert "sentences: 2 beyond max_sentences (2) omitted" in warnings


def test_pages_stop_at_the_cap_and_count_the_pages_not_split() -> None:
    texts = ["Erste Seite eins. Erste Seite zwei.", "Zweite Seite eins.", "Dritte Seite eins."]
    doc = "\n\n".join(texts)
    pages = []
    line = 1
    for i, t in enumerate(texts):
        pages.append(Page(index=i, text=t, line_start=line, line_end=line))
        line += 2
    warnings: list[str] = []
    out = split_sentences_for_pages(pages, doc, Limits(max_sentences=2), warnings=warnings)
    assert len(out) == 2
    assert all(s.page_index == 0 for s in out)
    assert "sentences: 2 pages after max_sentences (2) not split" in warnings


def test_extract_no_longer_raises_and_contracts_hold() -> None:
    data = ("Satz eins. Satz zwei. Satz drei. Satz vier. Satz fünf.").encode("utf-8")
    r = extract(data, mime="text/plain", emit_sentences=True, limits=Limits(max_sentences=3))
    assert len(r.content.sentences) == 3
    assert [s.index for s in r.content.sentences] == [0, 1, 2]
    for s in r.content.sentences:
        assert r.content.text[s.char_start : s.char_end] == s.text
    assert any(w.startswith("sentences: 2 beyond max_sentences (3)") for w in r.warnings)
```
(`Page` is exported at the top level; check `from knovas_extract import Page` works — it is in `__all__` per `__init__.py`.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "$LIB" && lib-pytest tests/unit/test_sentence_limit.py -q`
Expected: FAIL with `ResourceExhaustedError: sentence count` (tests 1 and 3) and too many sentences in test 2.

- [ ] **Step 3: Write the implementation**

In `split_sentences`, before the loop add the budget and replace the raise:
```python
# old
    result: list[Sentence] = []
    cursor = 0
    missing = 0
# new
    result: list[Sentence] = []
    cursor = 0
    missing = 0
    # Fail-soft cap: keep the first `max_sentences` of the document (start_index
    # counts the sentences earlier pages already produced).
    budget = max(0, limits.max_sentences - start_index)
```
```python
# old
    for raw in raw_segments:
        segment = raw.strip()
        if not segment:
            continue
# new
    for pos, raw in enumerate(raw_segments):
        segment = raw.strip()
        if not segment:
            continue
        if len(result) >= budget:
            omitted = sum(1 for r in raw_segments[pos:] if r.strip())
            warnings.append(
                f"sentences: {omitted} beyond max_sentences ({limits.max_sentences}) omitted"
            )
            break
```
```python
# old
        if len(result) > limits.max_sentences:
            raise ResourceExhaustedError(
                "sentence count", limits.max_sentences, observed=len(result)
            )

# new
```
(delete those four lines; if `ResourceExhaustedError` is then unused in the module, drop it from the import.)

Docstring of `split_sentences`:
```python
# old
    Raises ``DependencyMissingError`` if pysbd is unavailable,
    ``ResourceExhaustedError`` on ``max_sentences`` overflow, and
    ``RuntimeError`` on a producer-side invariant violation.
# new
    Keeps at most ``limits.max_sentences - start_index`` sentences and adds one
    counted warning when it stops early (never the text). Raises
    ``DependencyMissingError`` if pysbd is unavailable and ``RuntimeError`` on a
    producer-side invariant violation.
```

In `split_sentences_for_pages`, at the top of the page loop:
```python
# old
    for page in pages:
        page_text = page.text
# new
    for pos, page in enumerate(pages):
        if len(result) >= limits.max_sentences:
            rest = sum(1 for p in pages[pos:] if p.text)
            if rest:
                warnings.append(
                    f"sentences: {rest} pages after max_sentences ({limits.max_sentences}) not split"
                )
            break
        page_text = page.text
```
Also update the module docstring line that says `Limits.max_sentences` "caps output" — keep it, add "(fail-soft since 0.4.0a1)".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "$LIB" && lib-pytest tests/unit/test_sentence_limit.py tests/unit/test_sentence_contracts.py tests/unit/test_sentence_field.py tests/property -q`
Expected: PASS (property tests may be slow; they must not regress).

- [ ] **Step 5: Commit**

```bash
cd "$LIB"
git add src/knovas_extract/_sentences.py tests/unit/test_sentence_limit.py
git commit -F - <<'EOF'
fix(sentences): max_sentences caps the output instead of failing the document

A document with more sentences than Limits.max_sentences raised
ResourceExhaustedError, and consumers treat that as unconvertible: the
whole document was dropped for want of citations past the cap. The first
max_sentences sentences are now kept and one counted warning says how many
were omitted (and how many pages were not split); the text is unaffected.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task A4: DOCX tables in the text (`text_mode="layout"` for DOCX)

**Files:**
- Modify: `src/knovas_extract/extractors/docx.py` (new `_unique_row_cells`, `_cell_text_deep`, `_layout_table_block`, `_extract_body_layout`; `DocxExtractor.extract` gains `text_mode`)
- Modify: `src/knovas_extract/dispatch.py` (pass `text_mode` to DOCX; warning text)
- Modify: `tests/unit/test_extractors_pdf_layout.py` (the "other formats" test, lines ~296-310)
- Create: `tests/unit/test_extractors_docx_layout.py`
- Modify: `docs/layout-text-mode.md` (DOCX section), `CHANGELOG.md` (0.4.0a1 entry)

**Interfaces:**
- Consumes: `knovas_extract._layout.LayoutOptions` (fields `pack_budget_tokens`, `row_max_tokens`,
  `row_max_chars`, `fold`, `sentence_guard`, `section_rows`), `knovas_extract._layout.tables.TableGrid`,
  `TableRow`, `render_table_parts` (existing; same renderer as PDF tables), `knovas_extract._layout.lint.clean_cell`, `lint_row_line`.
- Produces: `extract(docx_bytes, text_mode="layout")` renders tables in place; metadata
  `docx:text_mode = "layout"` and `docx:layout_tables = <int>` only in layout mode; warning for other
  formats: `text_mode='layout' is implemented for PDF and DOCX only; plain text emitted`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_extractors_docx_layout.py`:
```python
"""DOCX layout mode: tables rendered in place with the PDF row grammar."""

from __future__ import annotations

import io

import pytest

pytest.importorskip("docx")
import docx as _docx  # noqa: E402

from knovas_extract import extract  # noqa: E402

pytestmark = [pytest.mark.unit]

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _save(d: object) -> bytes:
    buf = io.BytesIO()
    d.save(buf)  # type: ignore[attr-defined]
    return buf.getvalue()


def _doc_with_table() -> bytes:
    d = _docx.Document()
    d.add_paragraph("Prosa davor.")
    t = d.add_table(rows=3, cols=3)
    for c, h in enumerate(["Position", "2023", "2022"]):
        t.rows[0].cells[c].text = h
    for c, v in enumerate(["Umsatz", "1'234.00", "1'100.00"]):
        t.rows[1].cells[c].text = v
    merged = t.rows[2].cells[0].merge(t.rows[2].cells[1])
    merged.text = "Total"
    t.rows[2].cells[2].text = "9'999.00"
    d.add_paragraph("Prosa danach.")
    return _save(d)


def test_without_tables_layout_is_byte_identical_to_plain() -> None:
    d = _docx.Document()
    d.add_heading("Vertrag", level=1)
    d.add_paragraph("Die Parteien vereinbaren Folgendes.")
    data = _save(d)
    plain = extract(data, mime=DOCX)
    layout = extract(data, mime=DOCX, text_mode="layout")
    assert layout.content.text == plain.content.text
    assert layout.content.sections == plain.content.sections
    assert layout.metadata.extra["docx:text_mode"] == "layout"
    assert layout.metadata.extra["docx:layout_tables"] == 0
    assert "docx:text_mode" not in plain.metadata.extra
    assert not any("text_mode=" in w for w in layout.warnings)


def test_table_is_rendered_in_place_with_fold_keys() -> None:
    r = extract(_doc_with_table(), mime=DOCX, text_mode="layout")
    assert r.content.text == (
        "Prosa davor.\n\n"
        "Position | 2023 | 2022\n"
        "Umsatz | 2023: 1'234.00 | 2022: 1'100.00\n"
        "Total | 2022: 9'999.00\n\n"
        "Prosa danach."
    )
    assert r.metadata.extra["docx:layout_tables"] == 1


def test_plain_mode_and_content_tables_are_unchanged() -> None:
    data = _doc_with_table()
    plain = extract(data, mime=DOCX)
    layout = extract(data, mime=DOCX, text_mode="layout")
    assert plain.content.text == "Prosa davor.\n\nProsa danach."
    assert layout.content.tables == plain.content.tables


def test_header_repeats_on_every_pack() -> None:
    d = _docx.Document()
    t = d.add_table(rows=41, cols=3)
    for c, h in enumerate(["Konto", "2023", "2022"]):
        t.rows[0].cells[c].text = h
    for i in range(1, 41):
        for c, v in enumerate([f"Konto {i}", f"{i}'000.00", f"{i}'500.00"]):
            t.rows[i].cells[c].text = v
    text = extract(_save(d), mime=DOCX, text_mode="layout").content.text
    packs = text.split("\n\n")
    assert len(packs) > 1
    assert all(p.split("\n")[0] == "Konto | 2023 | 2022" for p in packs)
    assert sum(len(p.split("\n")) - 1 for p in packs) == 40


def test_nested_table_text_is_kept() -> None:
    d = _docx.Document()
    t = d.add_table(rows=2, cols=2)
    t.rows[0].cells[0].text = "Feld"
    t.rows[0].cells[1].text = "Wert"
    t.rows[1].cells[0].text = "Adresse"
    inner = t.rows[1].cells[1].add_table(rows=1, cols=2)
    inner.rows[0].cells[0].text = "Bahnhofstrasse 1"
    inner.rows[0].cells[1].text = "8001 Zürich"
    text = extract(_save(d), mime=DOCX, text_mode="layout").content.text
    assert "Bahnhofstrasse 1" in text and "8001 Zürich" in text


def test_single_row_table_becomes_one_line() -> None:
    d = _docx.Document()
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "Muster AG"
    t.rows[0].cells[1].text = "Basel"
    assert extract(_save(d), mime=DOCX, text_mode="layout").content.text == "Muster AG | Basel"


def test_sections_point_at_their_heading_line_in_the_layout_text() -> None:
    d = _docx.Document()
    t = d.add_table(rows=2, cols=2)
    for c, v in enumerate(["A", "B"]):
        t.rows[0].cells[c].text = v
    for c, v in enumerate(["x", "1.00"]):
        t.rows[1].cells[c].text = v
    d.add_heading("Anhang", level=1)
    d.add_paragraph("Text im Anhang.")
    r = extract(_save(d), mime=DOCX, text_mode="layout", emit_sentences=True)
    sec = next(s for s in r.content.sections if s.heading == "Anhang")
    assert r.content.text.split("\n")[sec.line_start - 1] == "Anhang"
    assert r.content.sentences  # dispatch's sentence contracts held


def test_layout_output_is_deterministic() -> None:
    data = _doc_with_table()
    a = extract(data, mime=DOCX, text_mode="layout")
    b = extract(data, mime=DOCX, text_mode="layout")
    assert a.content.text == b.content.text


def test_other_formats_warn_and_stay_plain() -> None:
    r = extract(b"Nur Text.", mime="text/plain", text_mode="layout")
    assert "text_mode='layout' is implemented for PDF and DOCX only; plain text emitted" in r.warnings


def test_a_large_table_stays_linear_and_complete() -> None:
    # Review Focus 3: a 2 000-row table (built row by row; python-docx cell
    # access through table.rows[i] is quadratic) renders fast and completely.
    import time

    d = _docx.Document()
    t = d.add_table(rows=1, cols=3)
    for c, h in enumerate(["Konto", "2023", "2022"]):
        t.rows[0].cells[c].text = h
    for i in range(1, 2001):
        cells = t.add_row().cells
        for c, v in enumerate([f"Konto {i}", f"{i}'000.00", f"{i}'500.00"]):
            cells[c].text = v
    data = _save(d)
    t0 = time.perf_counter()
    r = extract(data, mime=DOCX, text_mode="layout")
    assert time.perf_counter() - t0 < 30.0
    assert r.metadata.extra["docx:layout_tables"] == 1
    assert "Konto 2000 | 2023: 2000'000.00 | 2022: 2000'500.00" in r.content.text
```

In `tests/unit/test_extractors_pdf_layout.py`, the test ending at
`assert extra == ["text_mode='layout' is implemented for PDF only; plain text emitted"]` (around
lines 296–310) builds a DOCX without tables. Change only its last assertion to the new contract:
```python
# old
    extra = [w for w in layout.warnings if w not in plain.warnings]
    assert extra == ["text_mode='layout' is implemented for PDF only; plain text emitted"]
# new
    extra = [w for w in layout.warnings if w not in plain.warnings]
    assert extra == []  # DOCX has a layout mode since 0.4.0a1; no table -> identical text
```
(its other assertions — identical text and sections, no `pdf:*` layout keys — stay.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "$LIB" && lib-pytest tests/unit/test_extractors_docx_layout.py tests/unit/test_extractors_pdf_layout.py -q`
Expected: FAIL — no `docx:text_mode` key, tables missing from the text, the warning still says "PDF only".

- [ ] **Step 3: Write the implementation**

`src/knovas_extract/extractors/docx.py` — add below `_extract_body_text`:
```python
def _unique_row_cells(row: Any) -> list[str]:
    """Cell texts of one row by grid column; a horizontally merged cell
    (python-docx repeats the same ``w:tc`` for every column it spans) keeps its
    text in its first column only."""
    out: list[str] = []
    prev: Any = None
    for cell in row.cells:
        out.append("" if cell._tc is prev else _cell_text_deep(cell))
        prev = cell._tc
    return out


def _cell_text_deep(cell: Any) -> str:
    """A cell's text including nested tables (python-docx's ``cell.text`` skips them)."""
    from docx.table import Table as _DocxTable

    parts: list[str] = []
    for block in cell.iter_inner_content():
        if isinstance(block, _DocxTable):
            for row in block.rows:
                parts.append(" ".join(t for t in _unique_row_cells(row) if t))
        else:
            parts.append(block.text or "")
    return " ".join(p.strip() for p in parts if p and p.strip())


def _layout_table_block(table: Any) -> str:
    """One DOCX table in the markdown-lite row grammar of PDF layout mode
    (docs/layout-text-mode.md): header repeated per pack, compact fold keys."""
    from knovas_extract._layout import LayoutOptions
    from knovas_extract._layout.lint import clean_cell, lint_row_line
    from knovas_extract._layout.tables import TableGrid, TableRow, render_table_parts

    rows = [_unique_row_cells(r) for r in table.rows]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    if len(rows) == 1:
        return lint_row_line(" | ".join(clean_cell(c) for c in rows[0] if c.strip()))
    ncols = max(len(r) for r in rows)
    grid = TableGrid(
        header=dict(enumerate(rows[0])),
        rows=[TableRow(cells=dict(enumerate(r))) for r in rows[1:]],
        ncols=ncols,
    )
    opts = LayoutOptions()
    parts = render_table_parts(
        grid,
        budget=opts.pack_budget_tokens,
        row_max_tokens=opts.row_max_tokens,
        row_max_chars=opts.row_max_chars,
        fold=opts.fold,
        sentence_guard=opts.sentence_guard,
        section_rows=opts.section_rows,
    )
    return "\n\n".join(p.text for p in parts)


def _extract_body_layout(data: bytes) -> tuple[str, int]:
    """Body text with every table rendered in place (``text_mode="layout"``).

    Paragraphs are taken exactly as in ``_extract_body_text``, so a document
    without tables is byte-identical to plain mode. Returns the text and the
    number of tables rendered.
    """
    import docx  # python-docx
    from docx.table import Table as _DocxTable

    try:
        document = docx.Document(BytesIO(data))
    except Exception as exc:
        raise CorruptDocumentError(f"python-docx could not parse: {exc}") from exc

    blocks: list[str] = []
    tables = 0
    for block in document.iter_inner_content():
        if isinstance(block, _DocxTable):
            rendered = _layout_table_block(block)
            if rendered:
                blocks.append(rendered)
                tables += 1
            continue
        text = (block.text or "").rstrip()
        if text:
            blocks.append(text)
    return "\n\n".join(blocks), tables
```
(add `from typing import Any` to the module imports if it is not imported yet — check with `grep -n "^from typing" src/knovas_extract/extractors/docx.py`.)

`DocxExtractor.extract` — signature and body text:
```python
# old
        emit_markdown: bool = False,
        emit_sentences: bool = False,
    ) -> ExtractionResult:
# new
        emit_markdown: bool = False,
        emit_sentences: bool = False,
        text_mode: str = "plain",
    ) -> ExtractionResult:
```
```python
# old
        warnings: list[str] = []
        counts: Counter[str] = Counter()
        zf = _guard_zip(data, limits)
# new
        warnings: list[str] = []
        counts: Counter[str] = Counter()
        layout_tables: int | None = None  # set only in layout mode
        zf = _guard_zip(data, limits)
```
```python
# old
            body = _extract_body_text(data)
            text = canonicalize_text(body)
# new
            if text_mode == "layout":
                body, layout_tables = _extract_body_layout(data)
            else:
                body = _extract_body_text(data)
            text = canonicalize_text(body)
```
and right before `finalize_warnings(counts, warnings)`:
```python
# old
        finalize_warnings(counts, warnings)
# new
        if layout_tables is not None:
            extra["docx:text_mode"] = "layout"
            extra["docx:layout_tables"] = layout_tables
        finalize_warnings(counts, warnings)
```

`src/knovas_extract/dispatch.py`:
```python
# old
        if detected_mime == "application/pdf":
            extract_kwargs["use_ocr"] = use_ocr
            extract_kwargs["ocr_language"] = ocr_language
            extract_kwargs["ocr"] = ocr
            extract_kwargs["text_mode"] = mode
# new
        if detected_mime == "application/pdf":
            extract_kwargs["use_ocr"] = use_ocr
            extract_kwargs["ocr_language"] = ocr_language
            extract_kwargs["ocr"] = ocr
            extract_kwargs["text_mode"] = mode
        elif detected_mime == _DOCX_MIME:
            extract_kwargs["text_mode"] = mode
```
```python
# old
    # text_mode is a PDF feature in 0.4.0: every other extractor runs in plain
    # mode and the caller is told once (counts, no content — GI-EXTRACT-04).
    if mode != "plain" and detected_mime != "application/pdf":
        result.warnings.append(
            f"text_mode={mode!r} is implemented for PDF only; plain text emitted"
        )
# new
    # text_mode is a PDF and DOCX feature: every other extractor runs in plain
    # mode and the caller is told once (counts, no content — GI-EXTRACT-04).
    if mode != "plain" and detected_mime not in _LAYOUT_MIMES:
        result.warnings.append(
            f"text_mode={mode!r} is implemented for PDF and DOCX only; plain text emitted"
        )
```
with, near the other module constants of `dispatch.py`:
```python
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_LAYOUT_MIMES = frozenset({"application/pdf", _DOCX_MIME})
```
Update the `extract()` docstring paragraph for `text_mode` (`dispatch.py` ~line 302): "PDF only" →
"PDF and DOCX (DOCX: tables rendered in place; metadata `docx:text_mode`, `docx:layout_tables`)".

`docs/layout-text-mode.md` — replace the bullet "**PDF only in 0.4.0.** …" with:
```markdown
* **PDF and DOCX.** For every other format (HTML, EML, …) the kwarg is accepted,
  the extractor runs in plain mode and exactly one warning is added:
  `text_mode='layout' is implemented for PDF and DOCX only; plain text emitted`.
* **DOCX**: the body is walked in document order; paragraphs are emitted exactly
  as in plain mode, and every table is rendered in place with the table grammar
  below (`render_table_parts`, the same renderer as PDF tables): header line on
  top of every pack of ≤ 150 estimated tokens, compact fold keys, `#### ` section
  rows, `(Forts.)` splits. The first row is the header (as in `content.tables`);
  a horizontally merged cell keeps its text in its first column; nested tables
  are flattened into their cell. A one-row table becomes one ` | ` line. A DOCX
  without tables is byte-identical to plain mode. Metadata: `docx:text_mode`,
  `docx:layout_tables`. `content.tables` is unchanged; `content.sections` (from
  the Word headings) carry line numbers into the layout text.
```
`CHANGELOG.md`, under `## [0.4.0a1]`, add a subsection:
```markdown
### Added — DOCX layout mode
- **`extract(docx, text_mode="layout")`** renders every table in place with the
  PDF row grammar (header per pack, fold keys, section rows); before, DOCX table
  text existed only in `content.tables`, which the Knovas server's part buffer
  does not keep — table content was not searchable. Plain mode is unchanged; a
  DOCX without tables is byte-identical in both modes. Metadata
  `docx:text_mode`, `docx:layout_tables`. The warning for other formats now reads
  `text_mode='layout' is implemented for PDF and DOCX only; plain text emitted`.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "$LIB" && lib-pytest tests/unit/test_extractors_docx_layout.py tests/unit/test_extractors_pdf_layout.py tests/unit/test_extractors_docx.py tests/unit/test_extractors_docx_tables.py tests/unit/test_dispatch.py -q`
Expected: PASS. Then `lib-pytest tests/unit tests/golden -q` — no new failures.

- [ ] **Step 5: Commit**

```bash
cd "$LIB"
git add src/knovas_extract/extractors/docx.py src/knovas_extract/dispatch.py tests/unit/test_extractors_docx_layout.py tests/unit/test_extractors_pdf_layout.py docs/layout-text-mode.md CHANGELOG.md
git commit -F - <<'EOF'
feat(docx): layout mode renders tables in place

DOCX table text lived only in content.tables, and the Knovas server's part
buffer drops the tables payload, so the content of Word tables was never
searchable. text_mode="layout" now walks the body in document order and
renders each table with the PDF row grammar (same renderer: header per
pack, compact fold keys, section rows). Plain mode and content.tables are
unchanged; a DOCX without tables is byte-identical in both modes.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task A7: Outlook categories reach `msg:categories`

(Found while drafting the Connector's `keywords` opt-in: `msg:categories` is never produced.
extract-msg 0.56.1 has no `categories` attribute on a `Message`; Outlook keeps categories in the
named property `Keywords` of `PS_PUBLIC_STRINGS` — MS-OXPROPS PidNameKeywords — which extract-msg's
own calendar classes read with `getNamedProp('Keywords', PS_PUBLIC_STRINGS)`. Runs after A2, which
also edits `msg.py`.)

**Files:**
- Modify: `src/knovas_extract/extractors/msg.py` (new `_categories`, the `msg:categories` entry)
- Create: `tests/unit/test_msg_categories.py`
- Modify: `CHANGELOG.md` (0.4.0a1 "Fixed")

**Interfaces:**
- Produces: `knovas_extract.extractors.msg._categories(msg) -> Any` (list of strings, a string, or None);
  `metadata.extra["msg:categories"]` set whenever Outlook categories exist (sanitised like every
  other `extra` value; lists are written as a JSON array by `sanitize_scalar`).

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_msg_categories.py`:

```python
"""Outlook categories come from the named property Keywords (PS_PUBLIC_STRINGS)."""

from __future__ import annotations

from typing import Any

import pytest

from knovas_extract.extractors.msg import _categories

pytestmark = [pytest.mark.unit]

PS_PUBLIC_STRINGS = "{00020329-0000-0000-C000-000000000046}"


class _Msg:
    def __init__(self, named: dict[tuple[str, str], Any]) -> None:
        self._named = named

    def getNamedProp(self, name: str, guid: str, default: Any = None) -> Any:  # noqa: N802 - extract-msg API
        return self._named.get((name, guid), default)


def test_categories_come_from_the_keywords_named_property() -> None:
    msg = _Msg({("Keywords", PS_PUBLIC_STRINGS): ["Mandat Muster AG", "Rechnung"]})
    assert _categories(msg) == ["Mandat Muster AG", "Rechnung"]


def test_an_attribute_wins_and_a_missing_property_is_none() -> None:
    class WithAttribute(_Msg):
        categories = ["A"]

    assert _categories(WithAttribute({})) == ["A"]
    assert _categories(_Msg({})) is None


def test_a_damaged_named_property_stream_never_fails_the_extraction() -> None:
    class Broken:
        def getNamedProp(self, *args: Any, **kwargs: Any) -> Any:  # noqa: N802
            raise ValueError("damaged stream")

    assert _categories(Broken()) is None
    assert _categories(object()) is None
```

- [ ] **Step 2: Run them** — `lib-pytest tests/unit/test_msg_categories.py -q` → FAIL: `ImportError: cannot import name '_categories'`.

- [ ] **Step 3: Implement** — in `src/knovas_extract/extractors/msg.py`, below the imports:

```python
#: MS-OXPROPS property set of PidNameKeywords, the Outlook categories.
_PS_PUBLIC_STRINGS = "{00020329-0000-0000-C000-000000000046}"


def _categories(msg: Any) -> Any:
    """Outlook categories of a message, or None.

    extract-msg has no ``categories`` attribute on a ``Message``; Outlook keeps
    them in the named property ``Keywords`` of PS_PUBLIC_STRINGS (extract-msg's
    calendar classes read it the same way). A damaged named-property stream
    must not fail the extraction, so any error reads as "no categories".
    """
    value = getattr(msg, "categories", None)
    if value:
        return value
    getter = getattr(msg, "getNamedProp", None)
    if getter is None:
        return None
    try:
        return getter("Keywords", _PS_PUBLIC_STRINGS)
    except Exception:
        return None
```
and in the `extra` loop:
```python
# old
                    ("msg:categories", getattr(msg, "categories", None)),
# new
                    ("msg:categories", _categories(msg)),
```
CHANGELOG `## [0.4.0a1]` → `### Fixed`: `- MSG: Outlook categories reach metadata.extra["msg:categories"] (read from the
named property Keywords); before, the key was never set.`

- [ ] **Step 4: Run** — `lib-pytest tests/unit/test_msg_categories.py tests/unit/test_extractors_msg.py tests/unit/test_metadata_sanitization.py -q` → PASS; `lib-pytest-default -m unit -q` → no collection error; ruff/mypy (`--platform linux`) clean; bandit clean.

- [ ] **Step 5: Commit** — `fix(msg): Outlook categories reach msg:categories` (body: why, as above; attribution line).

### Task A5: Release hygiene

**Files:**
- Modify: `pyproject.toml` (`[project.urls]`)
- Modify: `RELEASING.md` (Sigstore identity, SLSA source URI, release download URLs)
- Modify: `CHANGELOG.md` (CI fixes, e-mail and sentence entries, the stale `[Unreleased]` heading)
- Modify: `README.md` (status line, `spec_version`)

**Interfaces:**
- Consumes: A1–A4 commits.
- Produces: a branch ready for the PR.

- [ ] **Step 1: Write the failing check**

```bash
cd "$LIB"
grep -rn "github.com/knovas/knovas-extract-python" pyproject.toml RELEASING.md README.md SECURITY.md docs | head
grep -n "^## \[Unreleased\]" CHANGELOG.md
grep -n "0.1.0.dev\|Not yet on PyPI\|spec_version = 1.2.0\|1\.2\.0" README.md | head
```
Expected: matches in every file (the URLs 404 — `curl -s -o /dev/null -w "%{http_code}" https://github.com/knovas/knovas-extract-python` prints `404`).

- [ ] **Step 2: Apply the changes**

`pyproject.toml`:
```toml
[project.urls]
Homepage      = "https://github.com/Seifeddini/knovas-extract-python"
Documentation = "https://github.com/Seifeddini/knovas-extract-python#readme"
Repository    = "https://github.com/Seifeddini/knovas-extract-python"
Issues        = "https://github.com/Seifeddini/knovas-extract-python/issues"
Security      = "https://github.com/Seifeddini/knovas-extract-python/blob/main/SECURITY.md"
Changelog     = "https://github.com/Seifeddini/knovas-extract-python/blob/main/CHANGELOG.md"
```
`RELEASING.md`, `README.md`, `SECURITY.md`, `docs/` — replace every `github.com/knovas/knovas-extract-python`
with `github.com/Seifeddini/knovas-extract-python` (`sed -i 's#github.com/knovas/knovas-extract-python#github.com/Seifeddini/knovas-extract-python#g'` on exactly the files the grep listed; review the diff).

`CHANGELOG.md`:
- rename the heading `## [Unreleased]` (the block below `[0.4.0a1]`, which describes the 0.3.0 work)
  to `## [0.3.0] — not released separately; part of 0.4.0a1`;
- add, at the top of the `## [0.4.0a1]` entry:
```markdown
### Fixed
- HTML-only e-mail bodies (EML, MSG) decode entities and keep paragraphs, line
  breaks and table cells instead of collapsing to one line with `&uuml;` left in.
- `Limits.max_sentences` caps the output with a counted warning instead of
  raising `ResourceExhaustedError` (consumers dropped the whole document).
- CI: optional test dependencies are imported through `pytest.importorskip`;
  the OCR CLI test is portable; mypy, pyright and bandit findings fixed.
- Project URLs point at `github.com/Seifeddini/knovas-extract-python`.
```
`README.md`: the status line (`alpha (0.1.0.dev) … Not yet on PyPI`) becomes
`Status: alpha (0.4.0a1). Published on PyPI as knovas-extract.`, and the `spec_version` mention
`1.2.0` becomes `1.3.0`.

- [ ] **Step 3: Re-run the check and the unit suite**

```bash
cd "$LIB"
grep -rn "github.com/knovas/knovas-extract-python" . --include=*.md --include=*.toml | grep -v CHANGELOG.md | wc -l
lib-pytest tests/unit -q
```
Expected: `0`; unit suite passes. (`CHANGELOG.md`'s historical entries may keep old URLs.)

- [ ] **Step 4: Commit**

```bash
cd "$LIB"
git add pyproject.toml RELEASING.md README.md SECURITY.md docs CHANGELOG.md
git commit -F - <<'EOF'
docs: release hygiene for 0.4.0a1

The project URLs and the Sigstore identity named github.com/knovas/…, which
does not exist: a release would have carried dead links on PyPI and a
verification recipe that cannot succeed. The CHANGELOG names the 0.3.0 work
that ships inside 0.4.0a1, and the README states the real status.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

### Task A6: Pull request, merge and release (user checkpoints)

**Files:** none (repository operations).

**Interfaces:**
- Consumes: branch `release/0.4.0a1` (A1–A5).
- Produces: the merge commit SHA (`LIB_SHA`, 40 characters) and, after the tag, `knovas-extract==0.4.0a1` on PyPI. Part B's pin (PIN tasks) uses `LIB_SHA` until PyPI has the release, then the version.

- [ ] **Step 1: Final gates on the branch** — repeat A1 Step 3 (Windows + Linux) on the branch tip. Expected: all green.

- [ ] **Step 2: Push the branch** (approved: opening the PR is part of the agreed plan)

```bash
cd "$LIB" && git push -u origin release/0.4.0a1
```
Open the PR: if the GitHub connector works, create it with title
`0.4.0a1: CI green, DOCX tables in the text, e-mail HTML, fail-soft sentence cap` and a body listing
A1–A5 plus `🤖 Generated with [Claude Code](https://claude.com/claude-code)`; otherwise give the user
the compare link `https://github.com/Seifeddini/knovas-extract-python/compare/main...release/0.4.0a1?expand=1`.
Wait for the PR's CI run; every job must pass (check the Actions tab or
`curl -s "https://api.github.com/repos/Seifeddini/knovas-extract-python/actions/runs?branch=release/0.4.0a1&per_page=5"`).

- [ ] **Step 3: Ask the user before merging** — state the CI result and the commit list; merge only on
an explicit "yes" (squash or merge commit, as the user prefers). Record the merge commit:
`LIB_SHA=$(git ls-remote https://github.com/Seifeddini/knovas-extract-python refs/heads/main | cut -f1)`.

- [ ] **Step 4: Ask the user before tagging** — pushing `v0.4.0a1` runs `release.yml`, which signs and
publishes to PyPI (irreversible). On an explicit "yes":
```bash
cd "$LIB" && git fetch origin && git tag -s v0.4.0a1 -m "Release v0.4.0a1" "$LIB_SHA" && git push origin v0.4.0a1
```
(if the user has no signing key configured, use `git tag -a` and say so.)

- [ ] **Step 5: Verify the release**

```bash
py -3.13 -m pip download --no-deps "knovas-extract==0.4.0a1" -d "$SP/verify-0.4.0a1"
ls "$SP/verify-0.4.0a1"
curl -s https://pypi.org/pypi/knovas-extract/json | py -c "import sys,json; print(json.load(sys.stdin)['releases'].get('0.4.0a1') is not None)"
```
Expected: a `knovas_extract-0.4.0a1-py3-none-any.whl` and `True`. Then run PIN-1/PIN-2 again with
the PyPI version (empty `KNOVAS_EXTRACT_GIT_REF`).

---

## Part B1 — Integration (INT)

Order: INT-1 → INT-2 → INT-3a → INT-3b → INT-3c → INT-3d → INT-4 → INT-5 → INT-6, one commit each (INT-4 is the cherry-pick itself).
**INT-1 must run before every other Part B task**: every later section writes its code against the merged tree (`$MERGED`), and the Platform suite is green only after INT-2.
INT-4 must precede INT-5 and INT-6 (their anchors are the cherry-picked text); INT-5 precedes INT-6 (INT-6 quotes the renamed sentences).
Every step from INT-2 on was replayed in a scratch copy of `$FIX` (all anchors matched exactly once or as often as stated; every DB-free test named below failed and passed as stated; the PostgreSQL-backed ones were not run there).

Conventions for this part:

```bash
RENAME=$SP/audit-merge/wt-rename   # main + fields + 08228ba with the four conflicts resolved (LF); INT-4's source
```

- Line numbers are those of `$MERGED` (INT-1 to INT-3d) or of `$RENAME` (INT-4 to INT-6); every edit is anchored on the quoted text. In Platform steps, `src/…` and `tests/…` are relative to `KnovasPlatform/components/docbridge_integration/` (where `pf-pytest` runs); the **Files** lists give full paths.
- `.py` files here are ASCII-only (`scripts/check_ascii_py.py`) and spell umlauts and dashes as backslash-u escapes. Every `.py` edit below is anchored on an ASCII fragment and leaves those escapes untouched: never retype them. (Tool layers that decode backslash-u sequences in parameters turn them into the character; anchoring on ASCII avoids that.)
- Platform runs that include `tests/test_experiments_frontend.py` (and every full-suite run) set `PYTHONUTF8=1`: that file decodes node's UTF-8 output with the locale otherwise, and on Windows two `TestSearchHitCards` tests then fail on mojibake (the trial ran with `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`). Run one Platform pytest at a time against `kc-plan-pg` (concurrent runs collide on `CREATE EXTENSION citext` in the per-test schemas). Full-suite runs on Windows add `--ignore=tests/test_experiments_runner_client.py` (`socketserver.UnixStreamServer` does not exist there; the trial ran the same way).

---

### Task INT-1: Merge `claude/document-fields-integration` into the work branch

**Files:**
- Modify (merge): everything the branch's 10 commits touch (Connector, Platform, mock Knovas API, docs); five files conflict and are resolved from `$MERGED`:
  - `.github/workflows/ci.yml`
  - `docker-compose.yml`
  - `docs/client/README.md`
  - `KnovasPlatform/components/docbridge_integration/src/web_interface/app.py`
  - `KnovasPlatform/components/docbridge_integration/src/web_interface/static/js/app.js`
- Test: whole-tree comparison with `$MERGED`; quick checks; full suites as the post-merge baseline

**Interfaces:**
- Consumes: `origin/claude/document-fields-integration` @ `09063a5`; `$MERGED` (`audit/main-docfields` @ `2ca658e`, the trial merge with these resolutions).
- Produces: a tree identical to `$MERGED` except this branch's own `docs/superpowers/` files. Later tasks rely on: `doc_fields_routes.run_search(client, plan, *, query, limit, filters)`, `doc_fields_routes.attach(...)`, `web_interface.app._fetch_search_page(ask, split, page_limit, *, over_fetch=True) -> (answer, experiment_hits, has_more)`, `_search_fetch_size(page_limit) -> int`, `_SEARCH_LIMIT_MAX = 50`, `SEARCH_FETCH_CEILING`, and `experiments_search` (a `SearchIntegration`) in `create_app`.

The five resolutions (spec §5.2; they are exactly the files in `$MERGED`):

| File | Resolution |
|---|---|
| `.github/workflows/ci.yml` | After the Platform `Pytest` step both new steps: `main`'s "Experiments SDK (Python client)" (`python -m pytest experiments-sdk/python`), then the branch's "Mock Knovas API" (`pytest mock_knovas_api/tests`). The branch's Connector-job changes (`KNOVAS_EXTRACT_SHA` pin + Dockerfile grep, `RC_SYNC_STATE_PATH` with `git diff --exit-code -- .rc-sync-state.db`, the blocking `pymupdf-layout` gate) merge as they are; Part B's PIN tasks rewrite them. |
| `docker-compose.yml` | Comments only: `main`'s paragraph "--timeout defaults to 180 s, the same value as the image's CMD …", then the branch's "--access-logformat keeps that line free of the request URI …". The command merges to `--timeout=$${DOCBRIDGE_WEB_TIMEOUT:-180}` plus `--access-logformat='%(t)s %(m)s %(s)s %(b)s %(L)s'`. |
| `docs/client/README.md` | "Switches you may want": `main`'s four experiments rows (`EXPERIMENTS_ENABLED`, `EXPERIMENTS_ACCESS_GROUPS`, `EXPERIMENTS_INDEX_UNRESTRICTED`, `COMPOSE_PROFILES=experiments …`), then the branch's three rows (`DOC_FIELDS_UI=off`, `DOC_FIELDS_EDIT_ROLES`, `RC_DOC_FIELDS=off`). |
| `…/web_interface/app.py` | Three hunks. `create_app`: `main`'s `install_experiments(...)` block first, then the branch's `_apply_open_hints`, `_apply_onedrive_links`, `_enhance_listing_rows` and `doc_fields_routes.attach(...)`. `/api/search` parsing: `limit = min(_SEARCH_LIMIT_MAX, _search_page_limit(data.get('limit'), config.get_int('web.search.results_per_page', 20)))` (`main`'s tolerant parser, capped at 50) with the branch's `where` validation (400 `filter_invalid`) and its query-free log line. Over-fetch: `main`'s `ask(n)` / `_fetch_search_page` / experiment split / `has_more`, where `ask(n)` returns `doc_fields_routes.run_search(api_client, plan, query=query, limit=n, filters=knovas_filters)`, so `where` and `return_fields` go out with every question, the wider second one included. |
| `…/static/js/app.js` | Three hunks. The branch's `_syncPreviewSidebar()` followed by `main`'s `stepPreview` doc comment ("Experiment-Treffer haben keine Vorschau und werden uebersprungen.") and `_previewNeighbour`; `this.displayResults(data.results, data.total, data.semantix, data.has_more);` inside the branch's block that sets `_honesty` / `_documentFields` and calls `renderSearchState`; "Mehr laden": `main`'s comment and `const full = typeof hasMore === 'boolean' ? hasMore : results.length >= this._searchLimit;`, then the branch's hand-over to the listing under a filter and the `SEARCH_LIMIT_MAX` (50) cap. |

- [ ] **Step 1: Check the preconditions**

```bash
cd "$WT"
git status --porcelain                                   # expect: no output
git fetch origin claude/document-fields-integration cl/wonderful-ritchie-s07v1q
git rev-parse --short=7 origin/claude/document-fields-integration   # expect: 09063a5
git merge-base --is-ancestor 0e63cac HEAD && echo base-ok           # expect: base-ok
git cat-file -e '08228ba^{commit}' && echo pick-ok                  # expect: pick-ok (needed by INT-4)
git diff --name-only 0e63cac HEAD                        # expect: only docs/superpowers/ files (spec, plan)
```

If the branch is no longer at `09063a5`, stop and ask: the resolutions are for that commit.

- [ ] **Step 2: Merge and confirm the five conflicts**

```bash
git merge --no-ff origin/claude/document-fields-integration
git diff --name-only --diff-filter=U
```

Expected: the merge stops with conflicts, and the list is exactly the five files of the table (nothing under `KnovasConnector/`; 11 of the 16 files touched on both sides merge cleanly).

- [ ] **Step 3: Take the five resolved files from `$MERGED`**

```bash
cd "$WT"
for f in .github/workflows/ci.yml docker-compose.yml docs/client/README.md \
         KnovasPlatform/components/docbridge_integration/src/web_interface/app.py \
         KnovasPlatform/components/docbridge_integration/src/web_interface/static/js/app.js; do
  cp "$MERGED/$f" "$WT/$f" && git add -- "$f"
done
git diff --name-only --diff-filter=U                     # expect: no output
git grep -n -E '^(<<<<<<<|>>>>>>>) ' -- . ':!docs/superpowers'   # expect: no output
```

- [ ] **Step 4: Verify that the whole merged tree equals `$MERGED`**

Every tracked file except the branch's own `docs/superpowers/` files must equal `$MERGED` byte for byte after CRLF normalisation (the work tree uses `core.autocrlf=true`, `$MERGED` has LF), and `$MERGED` may hold nothing more than ignored test artefacts (`KnovasConnector/config/knovas_connector_sync.json`, caches).

```bash
cd "$WT"
git diff --name-only -z 0e63cac HEAD > "$SP/int1-own.bin"     # HEAD is still the pre-merge commit
git ls-files -z > "$SP/int1-tracked.bin"
py -3.13 - "$MERGED" "$SP/int1-tracked.bin" "$SP/int1-own.bin" <<'EOF'
import os, subprocess, sys
merged, tracked_list, own_list = sys.argv[1:4]

def names(path):
    return {p for p in open(path, "rb").read().decode("utf-8").split("\0") if p}

def norm(path):
    with open(path, "rb") as fh:
        return fh.read().replace(b"\r\n", b"\n")

tracked = names(tracked_list)   # the merge result as staged in $WT
own = names(own_list)           # what this branch had before the merge (spec, plan)
bad = []
for rel in sorted(tracked - own):
    other = os.path.join(merged, rel)
    if not os.path.isfile(other):
        bad.append(f"missing in MERGED: {rel}")
    elif norm(rel) != norm(other):
        bad.append(f"differs from MERGED: {rel}")
extra = []
for root, dirs, files in os.walk(merged):
    dirs[:] = [d for d in dirs if d != ".git"]
    for name in files:
        rel = os.path.relpath(os.path.join(root, name), merged).replace(os.sep, "/")
        if rel != ".git" and rel not in tracked:
            extra.append(rel)
ignored = subprocess.run(["git", "check-ignore", "--stdin", "-z"], capture_output=True,
                         input="\0".join(extra).encode("utf-8")).stdout.decode("utf-8")
bad += [f"only in MERGED: {rel}" for rel in sorted(set(extra) - set(ignored.split("\0")))]
print("\n".join(bad) or f"OK: {len(tracked - own)} files equal MERGED; own: {sorted(own)}")
sys.exit(1 if bad else 0)
EOF
```

Expected: `OK: … files equal MERGED; own: ['docs/superpowers/…']`, exit 0. Any `differs`/`missing`/`only in MERGED` line is a wrong resolution: fix it from `$MERGED` before committing.

- [ ] **Step 5: Run the quick checks of the merge audit**

```bash
cd "$WT"
git ls-files -z -- '*.py' > "$SP/int1-py.bin"
py -3.13 - "$SP/int1-py.bin" <<'EOF'
import ast, sys
paths = [p for p in open(sys.argv[1], "rb").read().decode("utf-8").split("\0") if p]
bad = []
for path in paths:
    try:
        ast.parse(open(path, encoding="utf-8").read(), path)
    except (SyntaxError, UnicodeDecodeError) as exc:
        bad.append(f"{path}: {exc}")
print(len(paths), "Python files parse; errors:", bad)
sys.exit(1 if bad else 0)
EOF
git ls-files -z -- '*.js' | xargs -0 -n1 node --check     # expect: no output, exit 0
git ls-files -z -- '*.sh' | xargs -0 -n1 bash -n          # expect: no output, exit 0
```

Expected: about 350 Python files parse, `errors: []`; `node --check` and `bash -n` silent.

- [ ] **Step 6: Commit the merge**

```bash
cd "$WT"
git commit -F - <<'EOF'
Merge origin/claude/document-fields-integration

Document fields for the Knovas Connector, the Platform and the mock
Knovas API: ten reviewed commits on f9d872c, merged with a merge commit
so they stay as they were reviewed (spec 5.1). Five files conflicted and
are resolved as in the trial merge (spec 5.2):

- ci.yml: both new Platform-job steps, the experiments SDK first, then
  the mock Knovas API; the Connector job takes the branch's changes
- docker-compose.yml: both comment paragraphs; the command keeps
  --timeout=$${DOCBRIDGE_WEB_TIMEOUT:-180} and gains the URI-free
  --access-logformat
- docs/client/README.md: the experiments rows, then the document-fields
  rows
- web_interface/app.py: the experiments wiring before the document-fields
  hooks; the search page limit is main's parser capped at 50, with the
  where validation and the query-free log line; every over-fetch question
  goes through doc_fields_routes.run_search
- static/js/app.js: the preview sidebar sync before main's preview
  stepping; has_more decides "Mehr laden", and a filtered search hands
  over to the listing, capped at 50

The merge breaks nine Platform tests (the over-fetch against Knovas'
limit of 50, and the frontend test's fake DOM); the next commit fixes
them.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git log -1 --format='%P'      # expect: two parents, the second starting with 09063a5
```

- [ ] **Step 7: Record the post-merge baseline (full suites, before any later commit; spec §14)**

```bash
rc-pytest tests
cd "$WT/KnovasPlatform/mock_knovas_api" && $SP/venvs/pf/Scripts/python -m pytest tests -q
cd "$WT/KnovasPlatform" && $SP/venvs/pf/Scripts/python -m pytest experiments-sdk/python -q
cd "$WT/KnovasPlatform/components/docbridge_integration" && PYTHONUTF8=1 PLATFORM_DB_REQUIRED=true \
  $SP/venvs/pf/Scripts/python -m pytest tests -q --ignore=tests/test_experiments_runner_client.py
```

Expected (trial numbers, Windows):
- Connector: ~856 passed, 4 skipped; failures only the Windows-only `test_m365_inventory::test_state_survives_restart_and_resumes_from_the_saved_link`, `test_m365_sync::test_first_cycle_indexes_everything_and_keeps_nothing`, `test_ocr_cache::test_created_with_mode_0600`.
- Mock: 209 passed.
- Experiments SDK: 108 passed; 3 failed `tests/test_readme.py::test_ci_script_judges_the_newest_run_of_this_push[…]` (`WinError 10106` in the child's environment), the same as on `main`.
- Platform (~45 min): ~3 690 passed; failures exactly the nine of INT-2 Step 1 plus the Windows-only ones (`test_identity_broker_key::test_private_key_is_created_readable_only_by_its_owner`, `test_preview_endpoint::test_preview_content_carries_eml_mail_headers`, `test_experiments_frontend::TestNumberFormattingParity::test_format_value_and_diff`, errors in `test_experiments_schema::test_deep_nesting_in_text_is_a_validation_error`). Any other failure is a merge problem: compare the file with `$MERGED` before going on.

No commit in this step.

---

### Task INT-2: Search over-fetch within Knovas' limit of 50, and the frontend test's fake DOM

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/app.py` (constant `SEARCH_FETCH_CEILING`)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_experiments_regressions_web.py` (`TestSearchPageHelpers::test_fetch_size`)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_web_search_doc_fields.py` (`TestFeatureOffParity::test_legacy_client_gets_todays_call_and_additive_keys`, `TestRows::test_limit_is_clamped`)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_experiments_frontend.py` (`_VM_PRELUDE`, class `FakeEl`)

**Interfaces:**
- Consumes: INT-1's `_SEARCH_LIMIT_MAX = 50`, `_search_fetch_size`, `_fetch_search_page`.
- Produces: `SEARCH_FETCH_CEILING == _SEARCH_LIMIT_MAX == 50`: `/api/search` never asks Knovas for more than 50; a page of 20 asks for 40; the answer carries `has_more`. The exact changes are the four uncommitted files of `$FIX`.

- [ ] **Step 1: Confirm the nine merge-caused failures**

```bash
PYTHONUTF8=1 pf-pytest "tests/test_web_search_doc_fields.py::TestFeatureOffParity::test_legacy_client_gets_todays_call_and_additive_keys" "tests/test_web_search_doc_fields.py::TestRows::test_limit_is_clamped" "tests/test_experiments_frontend.py::TestSearchHitCards::test_document_cards_are_unchanged"
```

Expected: 9 failed:
- `TestFeatureOffParity::test_legacy_client_gets_todays_call_and_additive_keys` — `search_requests` has `"limit": 40` where the test expects 20 (and the body has `has_more`)
- `TestRows::test_limit_is_clamped[100-50]`, `[51-50]`, `[50-50]` — `assert 70 == 50`
- `TestRows::test_limit_is_clamped[0-1]`, `[-5-1]` — `assert 2 == 1`
- `TestRows::test_limit_is_clamped[viele-20]`, `[None-20]` — `assert 40 == 20`
- `TestSearchHitCards::test_document_cards_are_unchanged` — node: `TypeError: card.querySelector is not a function`

- [ ] **Step 2: Write the failing test for the ceiling**

In `tests/test_experiments_regressions_web.py`, class `TestSearchPageHelpers`, replace

```python
        assert _search_fetch_size(20) == 40
        assert _search_fetch_size(100) == 120
        assert _search_fetch_size(190) == 200
        assert _search_fetch_size(500) == 500
```

with

```python
        assert _search_fetch_size(20) == 40
        # The ceiling is Knovas' own maximum (50, see _SEARCH_LIMIT_MAX).
        assert _search_fetch_size(30) == 50
        assert _search_fetch_size(50) == 50
        assert _search_fetch_size(100) == 100
        assert _search_fetch_size(190) == 190
        assert _search_fetch_size(500) == 500
```

- [ ] **Step 3: Run it to verify it fails**

Run: `pf-pytest tests/test_experiments_regressions_web.py::TestSearchPageHelpers::test_fetch_size`
Expected: FAIL with `assert 70 == 50` (`_search_fetch_size(50)` still reaches for the old ceiling of 200).

- [ ] **Step 4: Write the minimal implementation**

In `src/web_interface/app.py` replace

```python
#: The most /api/search asks Knovas for to make room -- the ceiling the
#: module's own search uses as well (ExperimentService.search). A page
#: larger than this is asked for as it is.
SEARCH_FETCH_CEILING = 200
```

with

```python
#: The most /api/search asks Knovas for to make room: /secured/query answers
#: 422 above 50 (_SEARCH_LIMIT_MAX; knovas_client clamps to the same), so a
#: larger question would only be cut down there and make has_more read
#: "no more" when Knovas could not say. A page larger than this is asked
#: for as it is.
SEARCH_FETCH_CEILING = _SEARCH_LIMIT_MAX
```

(`_SEARCH_LIMIT_MAX = 50` is defined above it in the same module.)

- [ ] **Step 5: Run the ceiling tests**

Run: `pf-pytest tests/test_experiments_regressions_web.py::TestSearchPageHelpers::test_fetch_size tests/test_web_search_doc_fields.py::TestRows::test_limit_is_clamped`
Expected: `test_fetch_size` and `test_limit_is_clamped[100-50]`, `[51-50]`, `[50-50]` PASS; `[0-1]`, `[-5-1]`, `[viele-20]`, `[None-20]` still FAIL (`assert 2 == 1`, `assert 40 == 20`): `main`'s margin is intended, so those expectations change in Step 6.

- [ ] **Step 6: Update the tests to the merged behaviour**

`tests/test_web_search_doc_fields.py`, `TestFeatureOffParity::test_legacy_client_gets_todays_call_and_additive_keys` — replace

```python
        assert knovas.search_requests == [{"query": "Kuendigungsfrist", "limit": 20,
```

with

```python
        # 40: the page of 20 plus the margin main asks for, so experiment hits
        # taken out never leave the page short (_fetch_search_page).
        assert knovas.search_requests == [{"query": "Kuendigungsfrist", "limit": 40,
```

and in the same test replace

```python
                             "highlight_prefixes", "total", "timestamp",
```

with

```python
                             "highlight_prefixes", "total", "has_more", "timestamp",
```

`TestRows::test_limit_is_clamped` — replace

```python
    @pytest.mark.parametrize("limit, sent", [(100, 50), (51, 50), (50, 50), (0, 1),
                                             (-5, 1), ("viele", 20), (None, 20)])
```

with

```python
    # ``sent`` is what Knovas is asked for: the clamped page plus the
    # experiments margin (_search_fetch_size), never more than 50.
    @pytest.mark.parametrize("limit, sent", [(100, 50), (51, 50), (50, 50), (0, 2),
                                             (-5, 2), ("viele", 40), (None, 40)])
```

`tests/test_experiments_frontend.py`, inside `_VM_PRELUDE`, class `FakeEl` — replace

```javascript
  get innerHTML() { return this._html !== null ? this._html
    : this._text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
}
const document = {
```

with

```javascript
  get innerHTML() { return this._html !== null ? this._html
    : this._text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  // createDocumentCard looks up its title and headline after the innerHTML
  // (document fields); markup set through innerHTML is not parsed here.
  querySelector() { return null; }
  querySelectorAll() { return []; }
}
const document = {
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
PYTHONUTF8=1 pf-pytest tests/test_web_search_doc_fields.py tests/test_experiments_regressions_web.py tests/test_experiments_frontend.py tests/test_experiments_search_integration.py tests/test_web_documents_find.py
```

Expected: PASS, except the Windows-only `TestNumberFormattingParity::test_format_value_and_diff`. Then the four files must equal `$FIX`:

```bash
for f in src/web_interface/app.py tests/test_experiments_regressions_web.py \
         tests/test_web_search_doc_fields.py tests/test_experiments_frontend.py; do
  diff -u --strip-trailing-cr "$FIX/KnovasPlatform/components/docbridge_integration/$f" \
                              "$WT/KnovasPlatform/components/docbridge_integration/$f"
done                                                     # expect: no output
```

- [ ] **Step 8: Commit**

```bash
cd "$WT"
git add KnovasPlatform/components/docbridge_integration/src/web_interface/app.py \
        KnovasPlatform/components/docbridge_integration/tests/test_experiments_regressions_web.py \
        KnovasPlatform/components/docbridge_integration/tests/test_web_search_doc_fields.py \
        KnovasPlatform/components/docbridge_integration/tests/test_experiments_frontend.py
git commit -F - <<'EOF'
platform: search over-fetch stays within Knovas' limit of 50

main's _fetch_search_page asks Knovas for the page plus a margin, up to
200, and twice as many when experiment hits took places. Since the merge
every question goes through the document-fields client, which clamps to
50 because /secured/query answers 422 above that: the clamp cut the
question silently, and has_more could read "no more" when Knovas had not
been asked. SEARCH_FETCH_CEILING is now _SEARCH_LIMIT_MAX (50).

The tests follow the merged behaviour: Knovas is asked for 40 for a page
of 20 and the answer carries has_more (parity test); a clamped limit asks
for at most 50 (2 for a page of 1, 40 for the default page); the fake DOM
of the frontend test gets querySelector/querySelectorAll, which
createDocumentCard now calls.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task INT-3a: The field listing drops experiment documents and grants none

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/doc_fields_routes.py` (`attach`, route `documents_find`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/app.py` (`create_app`: the `doc_fields_routes.attach(...)` call)
- Modify: `KnovasPlatform/docs/features/document-fields.md` (bullet **Liste anzeigen**)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_web_documents_find.py` (new class `TestExperimentDocuments`)

**Interfaces:**
- Consumes: `SearchIntegration.split(results) -> (results_without_experiment_hits, experiment_hits)` (`src/experiments/search.py`), already used by `/api/search` through `experiments_search.split`.
- Produces: `doc_fields_routes.attach(app, *, config, client_factory, identity_gate, grant, grant_check, enhance, split: Callable[[Any], Tuple[Any, List[Dict[str, Any]]]]) -> None` — `split` is a new required keyword. Any later edit of `documents_find` (FLD) keeps the `split` step before `enhance` and `grant`.

- [ ] **Step 1: Write the failing test**

In `tests/test_web_documents_find.py` replace

```python
from __future__ import annotations

import logging
```

with

```python
from __future__ import annotations

import json
import logging
```

replace

```python
import doc_fields_view as dfv  # noqa: E402


```

with

```python
import doc_fields_view as dfv  # noqa: E402

#: An experiment document as the Experimente module writes it to Knovas.
EXP_POINTER = "experiments/marketing/MKT-1"


```

and insert before `class TestNoValuesInLogs:`

```python
class TestExperimentDocuments:
    """Experiment documents (experiments/<domain>/<KEY>) reach nobody through
    the listing, as they reach nobody through search (SearchIntegration.split):
    Knovas cannot know who holds an Experimente role, and an experiment
    pointer is never a file grant. Today they carry no fields, but a folder
    default could give them values."""

    def test_the_listing_drops_them_and_grants_none(self, listing, identity_repo, tmp_path):
        from document_grants import DocumentGrantStore

        app, api = listing
        api.add_document(EXP_POINTER, fields={"doc_type": "invoice"}, in_search=False)
        client = signed_in(app, identity_repo, role="member")
        body = find(client, {"doc_type": "invoice"}).get_json()
        assert [d["doc_id"] for d in body["documents"]] == [INVOICE]
        assert "experiments/" not in json.dumps(body["documents"])
        member = identity_repo.get_by_email("anwalt@kanzlei.ch")
        grants = DocumentGrantStore(str(tmp_path / "grants.sqlite3"))
        assert grants.granted(str(member.id), INVOICE)
        assert not grants.granted(str(member.id), EXP_POINTER)
        assert not grants.granted(str(member.id), "/" + EXP_POINTER)

    def test_a_page_of_experiments_only_is_passed_on_empty(self, listing, identity_repo):
        """Dropped before the empty-state rules: a page with a successor
        still says nothing about the documents after it (H5, H8)."""
        app, api = listing
        api.scripted_pages = [
            {"documents": [{"pointer": EXP_POINTER, "title": "MKT-1"}],
             "next_after": "c1", "complete": False, "total_count": 2,
             "where": {"applied": True, "clauses": 1, "resolved": []}},
        ]
        client = signed_in(app, identity_repo, role="member")
        body = find(client, {"doc_type": "invoice"}).get_json()
        assert body["documents"] == [] and body["next_after"] == "c1"
        assert "empty_text" not in body


```

(`_identity_app` writes the grant store to `tmp_path / "grants.sqlite3"`; the `listing` fixture and the test share the same `tmp_path`.)

- [ ] **Step 2: Run it to verify it fails**

Run: `pf-pytest tests/test_web_documents_find.py::TestExperimentDocuments`
Expected: 2 FAIL — `assert ['experiments/marketing/MKT-1', 'rc-sync/Muster AG/GJ 2024/Rechnung_17.pdf'] == ['rc-sync/Muster AG/GJ 2024/Rechnung_17.pdf']`, and `assert [{…'doc_id': 'experiments/marketing/MKT-1'…}] == []`.

- [ ] **Step 3: Write the minimal implementation**

`src/web_interface/doc_fields_routes.py`, function `attach` — replace

```python
           enhance: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
    """Register the document-fields routes on ``app`` (spec 4.4).

    ``grant(rows)`` records what a listing handed the person, as search does;
    ``grant_check(doc_id)`` asks whether the person's own search or listing
    returned that document (``_readable_for_current_user``); ``enhance`` is
    the search path's row enrichment. Every POST here goes through the
    app-wide ``X-CSRF-Token`` gate.
    """
```

with

```python
           enhance: Callable[[Dict[str, Any]], Dict[str, Any]],
           split: Callable[[Any], Tuple[Any, List[Dict[str, Any]]]]) -> None:
    """Register the document-fields routes on ``app`` (spec 4.4).

    ``grant(rows)`` records what a listing handed the person, as search does;
    ``grant_check(doc_id)`` asks whether the person's own search or listing
    returned that document (``_readable_for_current_user``); ``enhance`` is
    the search path's row enrichment; ``split`` is the search path's
    experiment split (``SearchIntegration.split``), so experiment documents
    leave a listing page the way they leave every search answer. Every POST
    here goes through the app-wide ``X-CSRF-Token`` gate.
    """
```

In `documents_find`, replace the docstring lines

```python
        notice only on the last page (H5); rows are granted like search rows,
        so a listed document can be previewed.
        """
```

with

```python
        notice only on the last page (H5); rows are granted like search rows,
        so a listed document can be previewed. Experiment documents are taken
        out first, as in search: never listed, never granted.
        """
```

and replace

```python
                if isinstance(doc, Mapping) and doc.get("pointer")]
        rows = list((enhance({"results": rows}) or {}).get("results") or rows)
```

with

```python
                if isinstance(doc, Mapping) and doc.get("pointer")]
        # Experiment documents (experiments/<domain>/<KEY>) leave here as they
        # leave every search answer: before the enrichment, so they get no
        # open hints, and before grant(), so their pointer never becomes a
        # file grant. Only search brings them back, as experiment rows.
        rows = list((split({"results": rows})[0] or {}).get("results") or [])
        rows = list((enhance({"results": rows}) or {}).get("results") or rows)
```

(`Tuple` is already imported in this module.) `total_count` stays Knovas' count: the listing cannot know how many documents on later pages are experiments, and a count names no document.

`src/web_interface/app.py`, in `create_app` — replace

```python
        enhance=_enhance_listing_rows,
    )
```

with

```python
        enhance=_enhance_listing_rows,
        split=experiments_search.split,
    )
```

(`experiments_search` is created unconditionally earlier in `create_app`, before `doc_fields_routes.attach`.)

`KnovasPlatform/docs/features/document-fields.md` — replace

```markdown
  fields list every matching document visible to the person, sorted by a date
  field or the path, page by page.
```

with

```markdown
  fields list every matching document visible to the person, sorted by a date
  field or the path, page by page. Experiment documents (*Experimente*) never
  appear in it, as in the search.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pf-pytest tests/test_web_documents_find.py`
Expected: PASS (the neighbouring `PYTHONUTF8=1 pf-pytest tests/test_web_search_doc_fields.py tests/test_experiments_search_integration.py tests/test_web_document_fields.py` still pass).

- [ ] **Step 5: Commit**

```bash
cd "$WT"
git add KnovasPlatform/components/docbridge_integration/src/web_interface/doc_fields_routes.py \
        KnovasPlatform/components/docbridge_integration/src/web_interface/app.py \
        KnovasPlatform/components/docbridge_integration/tests/test_web_documents_find.py \
        KnovasPlatform/docs/features/document-fields.md
git commit -F - <<'EOF'
platform: the field listing drops experiment documents and grants none

Search takes every experiment document (experiments/<domain>/<KEY>) out
of every answer and never turns its pointer into a file grant; only
people with an Experimente role get it back, as an experiment row. The
listing (POST /api/documents/find) had no such step. Experiment
documents carry no fields today, but a folder default could give them
values, and the listing would then hand them to anyone and grant their
pointers. It now applies the search's own split
(SearchIntegration.split) before the enrichment and the grant.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task INT-3b: The experiments module's own Knovas search asks for at most 50 hits

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/experiments/service.py` (new constant `KNOVAS_SEARCH_MAX`, method `ExperimentService.search`)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_experiments_service.py` (new `test_search_asks_knovas_for_at_most_50`)

**Interfaces:**
- Consumes: `ExperimentService(conn, actor, settings, *, knovas_search: Callable[[str, int], Dict[str, Any]], …).search(q, limit=30)`.
- Produces: `experiments.service.KNOVAS_SEARCH_MAX = 50`; `search` asks `knovas_search(query, min(KNOVAS_SEARCH_MAX, limit * 5))`.

- [ ] **Step 1: Write the failing test**

In `tests/test_experiments_service.py`, insert after `test_search_merges_knovas_hits_with_the_database` (before `def test_list_experiments_filters_and_pages(w):`):

```python
def test_search_asks_knovas_for_at_most_50(w):
    """/secured/query answers 422 above 50: the default page (30) asked for
    150 and the largest (100) for 200, and the refusal sent the search to
    the database alone without a word."""
    calls = []

    def knovas(query, limit):
        calls.append(limit)
        return {"results": []}

    w.repo.set_access_groups(w.eva.id, ["g-exp"])
    svc = w.svc(w.eva, knovas_search=knovas)
    svc.search("Karussell")
    svc.search("Karussell", limit=100)
    svc.search("Karussell", limit=2)
    assert calls == [50, 50, 10]


```

- [ ] **Step 2: Run it to verify it fails**

Run: `pf-pytest tests/test_experiments_service.py::test_search_asks_knovas_for_at_most_50`
Expected: FAIL with `assert [150, 200, 10] == [50, 50, 10]`.

- [ ] **Step 3: Write the minimal implementation**

`src/experiments/service.py` — replace

```python
MAX_QUERY_CHARS = 200
```

with

```python
MAX_QUERY_CHARS = 200
#: The most the module's search asks Knovas for (it asks for five times the
#: page): /secured/query answers 422 above 50 and knovas_client cuts a
#: larger limit to the same, so asking for more only hid the refusal.
KNOVAS_SEARCH_MAX = 50
```

and in `ExperimentService.search` replace

```python
                answer = self.knovas_search(" ".join(words), min(200, limit * 5)) or {}
```

with

```python
                answer = self.knovas_search(" ".join(words),
                                            min(KNOVAS_SEARCH_MAX, limit * 5)) or {}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pf-pytest tests/test_experiments_service.py`
Expected: PASS (`test_search_merges_knovas_hits_with_the_database` still sees `("Karussell", 50)` for `limit=10`); `pf-pytest tests/test_experiments_search_integration.py` still passes.

- [ ] **Step 5: Commit**

```bash
cd "$WT"
git add KnovasPlatform/components/docbridge_integration/src/experiments/service.py \
        KnovasPlatform/components/docbridge_integration/tests/test_experiments_service.py
git commit -F - <<'EOF'
platform: the experiments search asks Knovas for at most 50 hits

ExperimentService.search asked for five times the page, up to 200 (150
for the default page of 30). /secured/query answers 422 above 50; the
module then fell back to its database search without a word, and with
the document-fields client the limit is cut to 50 anyway. Ask for what
Knovas answers.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task INT-3c: nginx in front of the Platform waits as long as gunicorn (180 s)

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/nginx/docbridge-web-local.conf` (the conf compose mounts), `KnovasPlatform/components/docbridge_integration/nginx/docbridge-web.conf`, `KnovasPlatform/deploy/host-nginx/knovas-platform.conf.example` (`proxy_read_timeout`)
- Modify: `KnovasPlatform/components/docbridge_integration/README.md` (section "Admin upload", paragraph **The guard.**), `RELEASE_NOTES.md` (new section)
- Create: `KnovasPlatform/components/docbridge_integration/tests/test_web_timeouts.py`

**Interfaces:**
- Consumes: compose's `--timeout=$${DOCBRIDGE_WEB_TIMEOUT:-180}` (INT-1); `knovas_extract_upload.DEFAULT_EXTRACT_TIMEOUT_SECONDS = 120`.
- Produces: `tests/test_web_timeouts.py` with `GUNICORN_TIMEOUT` (regex for both `$${…}` and `${…}`) and `_compose_timeout() -> int`; every nginx layer has `proxy_read_timeout 180s`. No test or doctor check pins 120 s today (checked: only the three confs carry `proxy_read_timeout`; `scripts/host-https.sh` copies the template).

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_timeouts.py`:

```python
"""Every timeout in front of the Platform outlasts the admin upload's extraction.

An admin upload extracts in a child that the request thread kills at
RC_EXTRACT_TIMEOUT_SECONDS (default 120 s) and then answers "extraction
timeout". gunicorn's --timeout (DOCBRIDGE_WEB_TIMEOUT, default 180) stays
above that ceiling, or the worker dies first (769881c). Every nginx in front
-- docbridge-web-nginx (both confs) and the host-nginx template -- must wait
at least as long as gunicorn: at 120 s it gave up in the same second the
ceiling fired, and the person got a 504 instead of the message.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parents[1]
REPO = COMPONENT.parents[2]
COMPOSE = REPO / "docker-compose.yml"
NGINX_CONFS = (
    COMPONENT / "nginx" / "docbridge-web-local.conf",
    COMPONENT / "nginx" / "docbridge-web.conf",
    REPO / "KnovasPlatform" / "deploy" / "host-nginx" / "knovas-platform.conf.example",
)
# compose escapes the dollar ($${...}); the image's sh -c does not (${...}).
GUNICORN_TIMEOUT = re.compile(r"--timeout=\$?\$\{DOCBRIDGE_WEB_TIMEOUT:-(\d+)\}")


def _compose_timeout() -> int:
    if not COMPOSE.is_file():
        pytest.skip("repository-root docker-compose.yml is not in this checkout")
    match = GUNICORN_TIMEOUT.search(COMPOSE.read_text(encoding="utf-8"))
    assert match, "docbridge-web's gunicorn has no DOCBRIDGE_WEB_TIMEOUT default"
    return int(match.group(1))


def test_gunicorn_outlasts_the_extraction_ceiling():
    from knovas_extract_upload import DEFAULT_EXTRACT_TIMEOUT_SECONDS

    assert _compose_timeout() > DEFAULT_EXTRACT_TIMEOUT_SECONDS


@pytest.mark.parametrize("conf", NGINX_CONFS, ids=lambda p: p.name)
def test_every_nginx_waits_at_least_as_long_as_gunicorn(conf):
    if not conf.is_file():
        pytest.skip(f"{conf.name} is not in this checkout")
    seconds = [int(v) for v in re.findall(r"^\s*proxy_read_timeout\s+(\d+)s\s*;",
                                          conf.read_text(encoding="utf-8"), re.M)]
    assert seconds, f"{conf.name} sets no proxy_read_timeout (nginx default: 60 s)"
    assert min(seconds) >= _compose_timeout(), (conf.name, seconds)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pf-pytest tests/test_web_timeouts.py`
Expected: 3 FAIL (`test_every_nginx_waits_at_least_as_long_as_gunicorn[docbridge-web-local.conf]`, `[docbridge-web.conf]`, `[knovas-platform.conf.example]`), each `assert 120 >= 180`; `test_gunicorn_outlasts_the_extraction_ceiling` PASSES.

- [ ] **Step 3: Write the minimal implementation**

In `nginx/docbridge-web-local.conf` and in `nginx/docbridge-web.conf` replace

```nginx
        proxy_read_timeout 120s;
```

with

```nginx
        # At least gunicorn's --timeout (DOCBRIDGE_WEB_TIMEOUT, default 180 s):
        # an admin upload may extract until its 120 s ceiling and must still
        # get its answer through, not a 504.
        proxy_read_timeout 180s;
```

In `KnovasPlatform/deploy/host-nginx/knovas-platform.conf.example` replace

```nginx
    proxy_read_timeout 120s;
```

with

```nginx
    # At least gunicorn's --timeout (DOCBRIDGE_WEB_TIMEOUT, default 180 s):
    # an admin upload may extract until its 120 s ceiling and must still
    # get its answer through, not a 504.
    proxy_read_timeout 180s;
```

`KnovasPlatform/components/docbridge_integration/README.md` — replace

```markdown
above `RC_EXTRACT_TIMEOUT_SECONDS`.
```

with

```markdown
above `RC_EXTRACT_TIMEOUT_SECONDS`; every nginx in front
(`nginx/docbridge-web*.conf`, the host-nginx template) waits at least that long
(`proxy_read_timeout 180s`, pinned by `tests/test_web_timeouts.py`).
```

`RELEASE_NOTES.md` — insert before the heading line ``## Dokumente in OneDrive und SharePoint (`KNOVAS_DOCUMENTS_URL`)``:

```markdown
## nginx wartet so lange wie gunicorn

Das mitgelieferte nginx (`docbridge-web-nginx`) und die Host-nginx-Vorlage
warten jetzt 180 s auf die Plattform (`proxy_read_timeout`), so lange wie
gunicorn. Mit 120 s gab nginx genau dann auf, wenn die Textextraktion eines
Admin-Uploads an ihrer 120-s-Grenze abbrach, und statt der Meldung kam ein
504. Bestehende Installationen erneuern die Host-nginx-Seite aus der Vorlage
(`./scripts/host-https.sh` erledigt das).

```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pf-pytest tests/test_web_timeouts.py tests/test_nginx_privacy_log.py tests/test_experiments_leftovers_deploydocs.py`
Expected: PASS (21 tests; the last file reads the same nginx confs).

- [ ] **Step 5: Commit**

```bash
cd "$WT"
git add KnovasPlatform/components/docbridge_integration/nginx/docbridge-web-local.conf \
        KnovasPlatform/components/docbridge_integration/nginx/docbridge-web.conf \
        KnovasPlatform/deploy/host-nginx/knovas-platform.conf.example \
        KnovasPlatform/components/docbridge_integration/tests/test_web_timeouts.py \
        KnovasPlatform/components/docbridge_integration/README.md RELEASE_NOTES.md
git commit -F - <<'EOF'
platform: nginx waits as long as gunicorn (180 s)

Both nginx layers in front of the Platform used proxy_read_timeout 120s,
the admin upload's extraction ceiling: nginx gave up in the same second
the request thread killed the extraction child, and the person got a 504
instead of "extraction timeout". 769881c moved gunicorn to 180 s for the
same race. docbridge-web-nginx (both confs) and the host-nginx template
now wait 180 s; a test ties every nginx layer to the gunicorn default and
that default to the extraction ceiling.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task INT-3d: The Platform image's own CMD runs gunicorn like compose

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/Dockerfile` (comment + `CMD`, the last lines)
- Modify: `KnovasPlatform/components/docbridge_integration/README.md` (paragraph **The guard.**), `RELEASE_NOTES.md` (section of INT-3c)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_nginx_privacy_log.py` (new `_image_command`, `test_image_gunicorn_access_log_has_no_uri`)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_web_timeouts.py` (new `test_the_image_reads_the_timeout_compose_sets`)

**Interfaces:**
- Consumes: INT-3c's `GUNICORN_TIMEOUT`, `_compose_timeout()`; `_gunicorn_format`, `GUNICORN_URI_ATOMS`, `GUNICORN_ALLOWED_ATOMS` in `test_nginx_privacy_log.py`.
- Produces: `tests/test_nginx_privacy_log.py::_image_command() -> str` (the Dockerfile CMD joined into one command line). The image's CMD reads `DOCBRIDGE_WEB_WORKERS` / `DOCBRIDGE_WEB_THREADS` / `DOCBRIDGE_WEB_TIMEOUT` with compose's defaults (2/4/180) and logs `%(t)s %(m)s %(s)s %(b)s %(L)s`. PIN tasks that rewrite this Dockerfile's `ARG`/`pip` lines keep this `CMD` block unchanged.

- [ ] **Step 1: Write the failing tests**

`tests/test_nginx_privacy_log.py` — replace the docstring lines

```python
The same holds behind and in front of them: gunicorn's own access log in
docker-compose.yml (its default format has the request line, path, query
string and referer) and the host nginx example in KnovasPlatform/deploy.
```

with

```python
The same holds behind and in front of them: gunicorn's own access log in
docker-compose.yml and in the image's CMD (its default format has the
request line, path, query string and referer) and the host nginx example
in KnovasPlatform/deploy.
```

replace

```python
import re
from pathlib import Path
```

with

```python
import json
import re
from pathlib import Path
```

replace

```python
HOST_NGINX = Path(__file__).resolve().parents[3] / "deploy" / "host-nginx"
```

with

```python
HOST_NGINX = Path(__file__).resolve().parents[3] / "deploy" / "host-nginx"
DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
```

and insert before `def test_host_nginx_example_logs_only_the_privacy_format():`

```python
def _image_command() -> str:
    """The Platform image's CMD as one command line (exec form, JSON)."""
    cmd = [line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
           if line.startswith("CMD ")]
    assert len(cmd) == 1, "the Platform image has exactly one CMD"
    return " ".join(json.loads(cmd[0][len("CMD "):]))


def test_image_gunicorn_access_log_has_no_uri():
    """A container started without compose (docker run, another
    orchestrator) runs the image's CMD: the same URI-free format as
    compose, so its access log carries no pointer or search word either."""
    command = _image_command()
    assert "--access-logfile=-" in command
    atoms = set(re.findall(r"%\(([^)]+)\)s", _gunicorn_format(command)))
    assert atoms, "an empty format would fall back to nothing useful"
    assert not atoms & GUNICORN_URI_ATOMS, atoms
    assert atoms <= GUNICORN_ALLOWED_ATOMS, atoms


```

`tests/test_web_timeouts.py` — insert before `def test_gunicorn_outlasts_the_extraction_ceiling():`

```python
def test_the_image_reads_the_timeout_compose_sets():
    """A container started without compose runs the image's CMD: it reads
    DOCBRIDGE_WEB_TIMEOUT with compose's default, so both stay above the
    extraction ceiling together."""
    from test_nginx_privacy_log import _image_command

    match = GUNICORN_TIMEOUT.search(_image_command())
    assert match, "the image's gunicorn ignores DOCBRIDGE_WEB_TIMEOUT"
    assert int(match.group(1)) == _compose_timeout()


```

- [ ] **Step 2: Run them to verify they fail**

Run: `pf-pytest tests/test_nginx_privacy_log.py tests/test_web_timeouts.py`
Expected: 2 FAIL — `test_image_gunicorn_access_log_has_no_uri` (`assert '--access-logfile=-' in 'gunicorn --workers=2 --threads=4 --timeout=180 --bind=0.0.0.0:5000 web_interface.wsgi:app'`) and `test_the_image_reads_the_timeout_compose_sets` ("the image's gunicorn ignores DOCBRIDGE_WEB_TIMEOUT").

- [ ] **Step 3: Write the minimal implementation**

`Dockerfile` — replace

```dockerfile
# Default command (can be overridden). --timeout must exceed the admin
# upload's extraction ceiling (RC_EXTRACT_TIMEOUT_SECONDS, default 120 s):
# the extraction child is killed by the request thread at that ceiling, and
# gunicorn must not kill the worker first. The root docker-compose.yml passes
# DOCBRIDGE_WEB_TIMEOUT for the same reason -- keep it above the ceiling.
CMD ["gunicorn", "--workers=2", "--threads=4", "--timeout=180", "--bind=0.0.0.0:5000", "web_interface.wsgi:app"]
```

with

```dockerfile
# Default command (can be overridden): the gunicorn of the root
# docker-compose.yml, so a container started without compose behaves the
# same. --timeout (DOCBRIDGE_WEB_TIMEOUT, default 180 s) must exceed the
# admin upload's extraction ceiling (RC_EXTRACT_TIMEOUT_SECONDS, default
# 120 s): the extraction child is killed by the request thread at that
# ceiling, and gunicorn must not kill the worker first. --access-logformat
# keeps the request line, path, query string and referer -- document
# pointers and search words -- out of the access log (knovas_privacy).
CMD ["sh", "-c", "exec gunicorn --workers=${DOCBRIDGE_WEB_WORKERS:-2} --threads=${DOCBRIDGE_WEB_THREADS:-4} --timeout=${DOCBRIDGE_WEB_TIMEOUT:-180} --bind=0.0.0.0:5000 --access-logfile=- --access-logformat='%(t)s %(m)s %(s)s %(b)s %(L)s' web_interface.wsgi:app"]
```

(`sh -c` expands the variables; `exec` keeps gunicorn as PID 1 for signals; the single quotes make the format one argument. compose still overrides the CMD with its own `command`.)

`README.md` — replace

```markdown
(Dockerfile `180`, compose `DOCBRIDGE_WEB_TIMEOUT`, default `180`) must stay
above `RC_EXTRACT_TIMEOUT_SECONDS`; every nginx in front
```

with

```markdown
(`DOCBRIDGE_WEB_TIMEOUT`, default `180`, in the image's CMD and in compose)
must stay above `RC_EXTRACT_TIMEOUT_SECONDS`; every nginx in front
```

`RELEASE_NOTES.md` (section "nginx wartet so lange wie gunicorn" of INT-3c) — replace

```markdown
(`./scripts/host-https.sh` erledigt das).

## Dokumente in OneDrive und SharePoint (`KNOVAS_DOCUMENTS_URL`)
```

with

```markdown
(`./scripts/host-https.sh` erledigt das). Das Image der Plattform startet
gunicorn wie compose (`DOCBRIDGE_WEB_TIMEOUT`, Zugriffsprotokoll ohne
Adressen), auch wenn es ohne compose laeuft.

## Dokumente in OneDrive und SharePoint (`KNOVAS_DOCUMENTS_URL`)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pf-pytest tests/test_nginx_privacy_log.py tests/test_web_timeouts.py tests/test_experiments_leftovers_deploydocs.py`
Expected: PASS (23 tests). The image itself is built and checked by Part B's image verification.

- [ ] **Step 5: Commit**

```bash
cd "$WT"
git add KnovasPlatform/components/docbridge_integration/Dockerfile \
        KnovasPlatform/components/docbridge_integration/tests/test_nginx_privacy_log.py \
        KnovasPlatform/components/docbridge_integration/tests/test_web_timeouts.py \
        KnovasPlatform/components/docbridge_integration/README.md RELEASE_NOTES.md
git commit -F - <<'EOF'
platform: the image's CMD runs gunicorn like compose

The Dockerfile's CMD had a fixed --timeout=180 and no access-log format.
compose overrides it, but a container started without compose (docker
run, another orchestrator) ignored DOCBRIDGE_WEB_TIMEOUT, and with an
access log switched on through GUNICORN_CMD_ARGS it would log request
lines with document pointers and search words. The CMD now reads
DOCBRIDGE_WEB_WORKERS/THREADS/TIMEOUT with compose's defaults and logs
the URI-free knovas_privacy format; tests pin both.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task INT-4: Cherry-pick `08228ba` ("Knovas Connector is now called Knovas Connector")

**Files:**
- Modify (cherry-pick): the 36 files of `08228ba` — `KnovasPlatform/README.md`, `…/web_interface/admin_ingestion.py`, `…/web_interface/admin_system.py`, `…/static/js/admin_ingestion.js`, `…/templates/admin_ingestion.html`, `KnovasPlatform/docs/deployment/checklist-host-nginx.md`, `KnovasPlatform/docs/deployment/host-nginx-internal.md`, `KnovasPlatform/docs/features/document-administration.md`, `KnovasPlatform/docs/setup.md`, `README.md`, `KnovasConnector/CHANGELOG.md`, `KnovasConnector/README.md`, `KnovasConnector/data/docs/hello.txt`, `KnovasConnector/docs/README.md`, `KnovasConnector/docs/SETUP.md`, `KnovasConnector/docs/configuration.md`, `KnovasConnector/docs/hosting/server_01_home-corpus-setup.md`, `KnovasConnector/docs/local-commands.md`, `KnovasConnector/docs/local-setup.md`, `KnovasConnector/docs/network-and-firewall.md`, `KnovasConnector/pyproject.toml`, `KnovasConnector/scripts/demo_corpus/README.md`, `KnovasConnector/scripts/demo_kanzlei/README.md`, `docker-compose.yml`, `docs/KnovasAPI/Client_Integration_Guide.md`, `docs/KnovasAPI/README.md`, `docs/certificates.md`, `docs/hosting-requirements.md`, `docs/microsoft-365.md`, `docs/search-ui-backlog.md`, `docs/specifications.md`, `docs/stopping-web-servers.md`, `knovas.env.example`, `scripts/doctor.sh`, `scripts/search-probe.sh`, `scripts/setup.sh`
- Conflicts (one hunk each), resolved from `$RENAME`: `KnovasPlatform/components/docbridge_integration/src/web_interface/admin_system.py`, `KnovasConnector/CHANGELOG.md`, `KnovasConnector/pyproject.toml`, `knovas.env.example`

**Interfaces:**
- Consumes: the tree after INT-3d (none of INT-2/INT-3 touches the 36 files, so the result equals `$RENAME` on all of them).
- Produces: the System tab's `rc` check labelled `"Knovas Connector"`; `KnovasConnector/pyproject.toml` `version = "0.3.0"`, `description = "Knovas Connector (customer-hosted) — discover and sync local files to Knovas"`; the CHANGELOG's rename bullet under `## Unreleased`. INT-5/INT-6 anchor on this text.

The four resolutions (all four files are copied from `$RENAME`; check them against this):
- `admin_system.py`: the fields branch's RC check (`suffix, rc_hint = _rc_doc_fields_note(answer, doc_fields_on)`, `OK if exc is None and not rc_hint else WARN`, `("antwortet" + suffix)`, `hint=rc_hint if exc is None else "Betrifft nur den Reiter Ingestion."`) with `08228ba`'s label in both checks: `Check("rc", "Knovas Connector", SKIP, "Nicht konfiguriert", …)` and `"rc", "Knovas Connector",`. The section comment `# ── Knovas Connector ───…` stays (comment).
- `KnovasConnector/CHANGELOG.md`: under `## Unreleased` first `08228ba`'s bullet `- Renamed to **Knovas Connector** in documentation, the Platform's screens and script output. The folder `KnovasConnector/`, the Docker service `knovas-connector`, the `RC_*` settings and the config keys keep their names, so existing installations upgrade unchanged.`, a blank line, then the fields branch's `### 0.3.0 — Knovas document fields (Dokumentfelder)` section (and `### 0.2.0 …` below it) unchanged.
- `KnovasConnector/pyproject.toml`: `version = "0.3.0"` (the fields branch) followed by `description = "Knovas Connector (customer-hosted) — discover and sync local files to Knovas"` (`08228ba`).
- `knovas.env.example`: `main`'s `PLATFORM_TRUSTED_PROXY_HOPS` comment block and `# PLATFORM_TRUSTED_PROXY_HOPS=2`, `#`, then `08228ba`'s `# Knovas Connector on the host. Inside Docker it stays 5001; only the published` (the rest of that block unchanged). Its three other hunks apply cleanly (`# The console reaches the firm's Knovas Connector here …`, `# it names: RC_* reaches Knovas Connector, …`, `#   # Ingestion writes these; the default is the volume Knovas Connector fills.`).

- [ ] **Step 1: Cherry-pick and confirm the four conflicts**

```bash
cd "$WT"
git cherry-pick -x 08228ba
git diff --name-only --diff-filter=U
```

Expected: the cherry-pick stops; the list is exactly the four files above.

- [ ] **Step 2: Take the four resolved files from `$RENAME`**

```bash
cd "$WT"
for f in KnovasPlatform/components/docbridge_integration/src/web_interface/admin_system.py \
         KnovasConnector/CHANGELOG.md KnovasConnector/pyproject.toml knovas.env.example; do
  cp "$RENAME/$f" "$WT/$f" && git add -- "$f"
done
git diff --name-only --diff-filter=U                     # expect: no output
git grep -n -E '^(<<<<<<<|>>>>>>>) ' -- . ':!docs/superpowers'   # expect: no output
git diff --cached -- KnovasConnector/pyproject.toml | grep -E '^[-+](version|description)'
```

Expected for the last command: only `-description = "Customer-hosted Knovas Connector — discover and sync local files to Semantix"` and `+description = "Knovas Connector (customer-hosted) — discover and sync local files to Knovas"`; `version` is unchanged at `0.3.0`.

- [ ] **Step 3: Finish the cherry-pick (08228ba's own message plus the `-x` line)**

```bash
cd "$WT"
git -c core.editor=true cherry-pick --continue
git log -1 --format=%B | tail -n 3        # expect: "(cherry picked from commit 08228ba…)"
```

This commit keeps `08228ba`'s reviewed message; it gets no extra trailer.

- [ ] **Step 4: Verify the 36 files against `$RENAME`, and run the checks**

```bash
cd "$WT"
git diff-tree --no-commit-id --name-only -r -z HEAD > "$SP/int4-files.bin"
py -3.13 - "$RENAME" "$SP/int4-files.bin" <<'EOF'
import sys
ref, listing = sys.argv[1:3]
paths = [p for p in open(listing, "rb").read().decode("utf-8").split("\0") if p]

def norm(path):
    with open(path, "rb") as fh:
        return fh.read().replace(b"\r\n", b"\n")

bad = [p for p in paths if norm(p) != norm(f"{ref}/{p}")]
print(len(paths), "files touched; differ from wt-rename:", bad)
sys.exit(0 if len(paths) == 36 and not bad else 1)
EOF
py -3.13 -c "import ast, tomllib; ast.parse(open('KnovasPlatform/components/docbridge_integration/src/web_interface/admin_system.py', encoding='utf-8').read()); tomllib.load(open('KnovasConnector/pyproject.toml', 'rb')); print('ok')"
bash -n scripts/setup.sh && bash -n scripts/doctor.sh && bash -n scripts/search-probe.sh
node --check KnovasPlatform/components/docbridge_integration/src/web_interface/static/js/admin_ingestion.js
pf-pytest tests/test_web_admin_system_doc_fields.py tests/test_web_admin_ingestion.py
cd "$WT/KnovasPlatform/mock_knovas_api" && $SP/venvs/pf/Scripts/python -m pytest tests/test_doctor_doc_fields_probe.py -q
rc-pytest tests
```

Expected: `36 files touched; differ from wt-rename: []`, `ok`, silent syntax checks, Platform and mock PASS, Connector as in INT-1 Step 7 (only the three Windows-only failures).

---

### Task INT-5: Say "Knovas Connector" wherever people read the name

Policy of `08228ba`, applied to everything it could not see: German texts say "der/den/dem Knovas Connector" (genitive "des Knovas Connectors"), compounds "Knovas-Connector-…"; the abbreviation "RC" stays; English prose says "the Knovas Connector". The four lines that PR #22 renames outside `08228ba` (found by diffing `main` against `main` + PR #22) get PR #22's exact text, so merging PR #22 later sees identical changes there.

**Files:**
- Modify (Platform UI): `KnovasPlatform/components/docbridge_integration/src/web_interface/templates/admin_ingestion.html`, `…/static/js/admin_ingestion.js`, `…/web_interface/admin_ingestion.py`, `…/src/identity/ingestion_compiler.py`, `…/web_interface/admin_system.py`, `…/web_interface/admin_doc_fields.py`, `…/src/knovas_connector_client.py`
- Modify (Connector): `KnovasConnector/src/sync/doc_fields_metrics.py`, `KnovasConnector/scripts/backfill_partial_ocr.py`
- Modify (docs): `KnovasPlatform/docs/features/document-fields.md`, `KnovasPlatform/components/docbridge_integration/README.md`, `KnovasConnector/docs/configuration.md`, `KnovasConnector/docs/operations.md`, `RELEASE_NOTES.md`, `docs/client/README.md`, `docs/client/document-fields.md`, `knovas.env.example`, `KnovasPlatform/docs/features/experiments.md`, `KnovasPlatform/components/experiments_runner/README.md`, `docs/search-ui-backlog.md`, `docker-compose.yml`, `KnovasPlatform/docs/README.md`
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_web_admin_ingestion.py`, `tests/test_web_admin_doc_fields.py`, `tests/test_web_admin_system_doc_fields.py`

**Interfaces:**
- Consumes: INT-4's renamed text.
- Produces: the strings below (later UI text uses the same naming), and `$SP/rc_leftovers.py`, a check any later task that adds user-facing text can rerun from `$WT`.

- [ ] **Step 1: Update the assertions that pin the old strings**

All anchors are ASCII; on lines that continue with a backslash-u escape, only the quoted prefix changes and the rest of the line stays as it is.

`tests/test_web_admin_ingestion.py`:

```diff
# TestStatusBar::test_a_quiet_block (line 1188)
-            "Dokumentfelder sind im Knovas Connector ausgeschaltet (RC_DOC_FIELDS=off); "
+            "Dokumentfelder sind im Knovas Connector ausgeschaltet (RC_DOC_FIELDS=off); "
# TestCheckProfileFields::test_a_knovas_connector_without_the_capability_refuses_the_save (line 1254): prefix only
-        assert "Knovas Connector zu alt 
+        assert "Der Knovas Connector ist zu alt 
# TestCheckProfileFields::test_the_preview_gets_the_problem_instead_of_an_exception (line 1351)
-        assert "Knovas Connector zu alt" in check.error
+        assert "Der Knovas Connector ist zu alt" in check.error
# TestLiveDocumentFields::test_an_old_knovas_connector_gets_nothing (line 1745)
-        assert "Knovas Connector zu alt" in r.data.decode("utf-8")
+        assert "Der Knovas Connector ist zu alt" in r.data.decode("utf-8")
# lines 1612, 1618, 1767 (all three; prefix only, "in html" / "not in html" unchanged)
-assert "Knovas Connector meldet keine Unterst
+assert "Der Knovas Connector meldet keine Unterst
# lines 1617 and 1766 (both)
-        assert "Knovas Connector ist nicht erreichbar" in html
+        assert "Der Knovas Connector ist nicht erreichbar" in html
```

(The 1254 anchor is `assert "Knovas Connector zu alt` followed by one space: it matches only that line.)

`tests/test_web_admin_doc_fields.py`:

```diff
# TestRegistryWrites::test_no_requeue_offer_for_a_knovas_connector_without_it (727) and
# TestRegistryWrites::test_an_older_knovas_connector_server_is_not_offered_requeue (742)
-        assert "Knovas Connector aktualisieren" in response.data.decode("utf-8")
+        assert "bitte den Knovas Connector aktualisieren" in response.data.decode("utf-8")
# TestRegistryWrites::test_an_unreachable_knovas_connector_is_not_called_too_old (757)
-        assert "nicht erreichbar" in body and "Knovas Connector aktualisieren" not in body
+        assert "nicht erreichbar" in body and "Knovas Connector aktualisieren" not in body
```

`tests/test_web_admin_system_doc_fields.py`, `TestKnovasConnector::test_an_old_knovas_connector_is_called_out_while_fields_are_on` (line 144):

```diff
-        assert "Knovas Connector aktualisieren" in check["hint"]
+        assert check["hint"] == ("Den Knovas Connector aktualisieren, damit die Ingestion "
+                                 "Feldwerte mitsenden kann.")
```

- [ ] **Step 2: Run them to verify they fail**

```bash
pf-pytest tests/test_web_admin_ingestion.py tests/test_web_admin_doc_fields.py tests/test_web_admin_system_doc_fields.py
```

Expected: 13 FAIL — `test_web_admin_ingestion.py::TestStatusBar::test_a_quiet_block`, `TestCheckProfileFields::test_a_knovas_connector_without_the_capability_refuses_the_save` (4 params), `TestCheckProfileFields::test_the_preview_gets_the_problem_instead_of_an_exception`, `TestTemplateFields::test_notes_when_knovas_or_knovas_connector_cannot_take_them`, `TestTemplateFields::test_an_unreachable_knovas_connector_is_not_called_too_old`, `TestLiveDocumentFields::test_an_old_knovas_connector_gets_nothing`, `TestLiveDocumentFields::test_a_status_that_fails_is_unreachable_not_too_old`; `test_web_admin_doc_fields.py::TestRegistryWrites::test_no_requeue_offer_for_a_knovas_connector_without_it`, `…::test_an_older_knovas_connector_server_is_not_offered_requeue`; `test_web_admin_system_doc_fields.py::TestKnovasConnector::test_an_old_knovas_connector_is_called_out_while_fields_are_on` — each because the page or constant still says "Knovas Connector".

- [ ] **Step 3: Rename the strings**

**Platform UI (the 14 strings of spec §5.3).** `src/web_interface/templates/admin_ingestion.html`:

```diff
# line 119
-        <p class="hint">Jedes erneut gesendete Dokument ist ein verrechneter Upload mit erneuter Texterkennung; Knovas Connector sendet höchstens die eingestellte Anzahl pro Durchlauf.</p>
+        <p class="hint">Jedes erneut gesendete Dokument ist ein verrechneter Upload mit erneuter Texterkennung; der Knovas Connector sendet höchstens die eingestellte Anzahl pro Durchlauf.</p>
# line 138
-                        <p class="msg error">Pfadvorlage {{ e.index }}: {{ e.text }} – Knovas Connector würde diesen Ordner überspringen.</p>
+                        <p class="msg error">Pfadvorlage {{ e.index }}: {{ e.text }} – der Knovas Connector würde diesen Ordner überspringen.</p>
# line 213
-                <p class="msg warn">Knovas Connector ist nicht erreichbar; ob er Dokumentfelder unterstützt, lässt sich gerade nicht prüfen. Ordner mit Feldern werden erst gespeichert, wenn er antwortet.</p>
+                <p class="msg warn">Der Knovas Connector ist nicht erreichbar; ob er Dokumentfelder unterstützt, lässt sich gerade nicht prüfen. Ordner mit Feldern werden erst gespeichert, wenn er antwortet.</p>
# line 215
-                <p class="msg error">Knovas Connector meldet keine Unterstützung für Dokumentfelder – bitte aktualisieren, bevor Ordner mit Feldern gespeichert werden.</p>
+                <p class="msg error">Der Knovas Connector meldet keine Unterstützung für Dokumentfelder – bitte aktualisieren, bevor Ordner mit Feldern gespeichert werden.</p>
```

`src/web_interface/static/js/admin_ingestion.js`:

```diff
# line 234
-                ' – Knovas Connector würde diesen Ordner überspringen.', 'msg error'));
+                ' – der Knovas Connector würde diesen Ordner überspringen.', 'msg error'));
```

`src/web_interface/admin_ingestion.py` (prefix anchors; the escapes after them stay):

```diff
# line 351 (reupload_text)
-    return (f"{head} Wie viele Dokumente es sind, meldet Knovas Connector erst nach einem "
+    return (f"{head} Wie viele Dokumente es sind, meldet der Knovas Connector erst nach einem "
# line 407 (doc_fields_status)
-        say("Dokumentfelder sind im Knovas Connector ausgeschaltet (RC_DOC_FIELDS=off); "
+        say("Dokumentfelder sind im Knovas Connector ausgeschaltet (RC_DOC_FIELDS=off); "
# line 1215 (requeue_doc_fields): anchor `"Knovas Connector sendet sie in den n`
-                             "Knovas Connector sendet sie in den n
+                             "der Knovas Connector sendet sie in den n
```

Renders as: "… vorgemerkt; der Knovas Connector sendet sie in den nächsten Durchläufen, je ein verrechneter Upload."

`src/identity/ingestion_compiler.py` (prefix anchors, each followed by one space):

```diff
# line 477
-RC_TOO_OLD = ("Knovas Connector zu alt 
+RC_TOO_OLD = ("Der Knovas Connector ist zu alt 
# line 480
-RC_UNREACHABLE = ("Knovas Connector nicht erreichbar 
+RC_UNREACHABLE = ("Der Knovas Connector ist nicht erreichbar 
```

Render as: "Der Knovas Connector ist zu alt – bitte aktualisieren: er meldet keine Unterstützung für Dokumentfelder." and "Der Knovas Connector ist nicht erreichbar – ob er Dokumentfelder unterstützt, lässt sich jetzt nicht prüfen. Bitte später erneut speichern."

`src/web_interface/admin_system.py` (`_rc_doc_fields_note`):

```diff
# line 145
-                "Knovas Connector aktualisieren, damit die Ingestion Feldwerte mitsenden kann.")
+                "Den Knovas Connector aktualisieren, damit die Ingestion Feldwerte mitsenden kann.")
```

`src/web_interface/admin_doc_fields.py`:

```diff
# line 186 (REQUEUE_UNSUPPORTED): anchor `    "Der Knovas Connector kennt das erneute Senden noch nicht ` (one trailing space)
-    "Der Knovas Connector kennt das erneute Senden noch nicht 
+    "Der Knovas Connector kennt das erneute Senden noch nicht 
# line 187
-    "Knovas Connector aktualisieren."
+    "den Knovas Connector aktualisieren."
# line 190 (REQUEUE_UNREACHABLE)
-    "Der Knovas Connector ist nicht erreichbar; es wurde nichts erneut gesendet. "
+    "Der Knovas Connector ist nicht erreichbar; es wurde nichts erneut gesendet. "
# line 1638 (doc_fields_requeue)
-            return _page(error="Der Knovas Connector hat das erneute Senden nicht angenommen.",
+            return _page(error="Der Knovas Connector hat das erneute Senden nicht angenommen.",
```

REQUEUE_UNSUPPORTED renders as: "Der Knovas Connector kennt das erneute Senden noch nicht – bitte den Knovas Connector aktualisieren."

**Operator log lines and metric help (spec §5.3).**

```diff
# KnovasPlatform/components/docbridge_integration/src/knovas_connector_client.py line 192
-            logger.info("Knovas-Connector-Faehigkeiten nicht abrufbar: %s", type(exc).__name__)
+            logger.info("Knovas-Connector-Faehigkeiten nicht abrufbar: %s", type(exc).__name__)
# KnovasPlatform/components/docbridge_integration/src/web_interface/admin_doc_fields.py lines 1170, 1179
-            logger.warning("Knovas Connector client unavailable: %s", type(exc).__name__)
+            logger.warning("Knovas Connector client unavailable: %s", type(exc).__name__)
-            logger.warning("Knovas Connector capabilities unavailable: %s", type(exc).__name__)
+            logger.warning("Knovas Connector capabilities unavailable: %s", type(exc).__name__)
# KnovasConnector/src/sync/doc_fields_metrics.py line 96 (rc_doc_fields_client_dropped_total help)
-    "Field values the Knovas Connector left out of an init before sending, by reason",
+    "Field values the Knovas Connector left out of an init before sending, by reason",
```

**Console messages that predate PR #22 and that `08228ba` missed** (the Ingestion tab shows them through `{exc}`; PR #22 does not touch these lines, so a later merge does not conflict):

```diff
# KnovasPlatform/components/docbridge_integration/src/knovas_connector_client.py line 138
-            raise PermissionError("Kein angemeldeter Benutzer; Knovas Connector wird nicht aufgerufen.")
+            raise PermissionError("Kein angemeldeter Benutzer; der Knovas Connector wird nicht aufgerufen.")
# line 151
-            raise KnovasConnectorError(f"Knovas Connector nicht erreichbar: {exc}", status=None) from exc
+            raise KnovasConnectorError(f"Der Knovas Connector ist nicht erreichbar: {exc}", status=None) from exc
# line 231
-                    "Knovas Connector hat die Sync-Konfigurations-API abgeschaltet "
+                    "Der Knovas Connector hat die Sync-Konfigurations-API abgeschaltet "
# KnovasPlatform/components/docbridge_integration/src/identity/ingestion_compiler.py line 381
-            "Knovas Connector refuses. This is a bug in the compiler, not in "
+            "the Knovas Connector refuses. This is a bug in the compiler, not in "
```

**Usage text** (`--help` prints the module docstring, `argparse.ArgumentParser(description=__doc__)`):

```diff
# KnovasConnector/scripts/backfill_partial_ocr.py line 20
-Run it inside the Knovas Connector container (same env, same volumes),
+Run it inside the Knovas Connector container (same env, same volumes),
```

**Docs the fields branch added.** `KnovasPlatform/docs/features/document-fields.md`:

```diff
# line 6
-shows only what Knovas confirms, and Knovas Connector can send values with
+shows only what Knovas confirms, and the Knovas Connector can send values with
# line 23
-noetig`). In a BROKERED tenant, entity values sent by Knovas Connector (for
+noetig`). In a BROKERED tenant, entity values sent by the Knovas Connector (for
# line 25
-upload with `401 assertion_rejected` and Knovas Connector indexes the document
+upload with `401 assertion_rejected` and the Knovas Connector indexes the document
# line 29 (label only; the path stays)
-Knovas Connector side: [`KnovasConnector/docs/configuration.md`](../../../KnovasConnector/docs/configuration.md#per-source-document-fields-dokumentfelder).
+Knovas Connector side: [`KnovasConnector/docs/configuration.md`](../../../KnovasConnector/docs/configuration.md#per-source-document-fields-dokumentfelder).
# line 74
-- **No echo, no "gespeichert".** Knovas Connector and the Ingestion tab say
+- **No echo, no "gespeichert".** The Knovas Connector and the Ingestion tab say
# line 148
-Minuten)"). The folder is picked from the Knovas Connector tree (or typed as
+Minuten)"). The folder is picked from the Knovas Connector's tree (or typed as
# lines 190-191 (quotes the UI text of RC_TOO_OLD)
-Knovas Connector does not report the needed capability ("Knovas Connector zu
-alt – bitte aktualisieren").
+the Knovas Connector does not report the needed capability ("Der Knovas
+Connector ist zu alt – bitte aktualisieren").
# line 202
-The status panel shows what Knovas Connector reports: whether Knovas takes
+The status panel shows what the Knovas Connector reports: whether Knovas takes
# line 211
-Knovas Connector clears those documents' upload values at Knovas on their next
+the Knovas Connector clears those documents' upload values at Knovas on their next
# line 237
-legacy search (its query text), and Knovas Connector's upload-failure,
+legacy search (its query text), and the Knovas Connector's upload-failure,
# line 240
-and Knovas Connector's container logs as confidential, like the documents.
+and the Knovas Connector's container logs as confidential, like the documents.
```

`KnovasPlatform/components/docbridge_integration/README.md` (line 70 from the fields branch, line 15 from `main`; lines 16 and 19 are paths and stay):

```diff
-`src/knovas_extract_upload.py`, which mirrors the Knovas Connector's sync
+`src/knovas_extract_upload.py`, which mirrors the Knovas Connector's sync
-tenant, Knovas Connector's entity values also need S1.
+tenant, the Knovas Connector's entity values also need S1.
```

`KnovasConnector/docs/configuration.md` (not line 282: `knovas-connector` there is the service):

```diff
# line 122
-tenant. Nothing here switches that on: the Knovas Connector sends fields when a
+tenant. Nothing here switches that on: the Knovas Connector sends fields when a
# line 188
-`assertion_rejected`), the Knovas Connector re-posts the init **once without
+`assertion_rejected`), the Knovas Connector re-posts the init **once without
# line 203
-the Knovas Connector).
+the Knovas Connector).
# line 207
-The Knovas Connector stores a digest of each document's governing field
+The Knovas Connector stores a digest of each document's governing field
# line 221
-digest equals an empty configuration, so upgrading the Knovas Connector does
+digest equals an empty configuration, so upgrading the Knovas Connector does
# line 252
-profile, the Knovas Connector sees an empty configuration for those sources
+profile, the Knovas Connector sees an empty configuration for those sources
```

`KnovasConnector/docs/operations.md`:

```diff
# line 70
-Configuration and costs: [configuration.md](configuration.md#per-source-document-fields-dokumentfelder). Document fields work only once Knovas has enabled them for the tenant; until then the Knovas Connector sends them where configured, the server ignores them, and nothing else changes.
+Configuration and costs: [configuration.md](configuration.md#per-source-document-fields-dokumentfelder). Document fields work only once Knovas has enabled them for the tenant; until then the Knovas Connector sends them where configured, the server ignores them, and nothing else changes.
# line 93
-- `capabilities` tells the Platform which sync-body keys this Knovas Connector understands; an older one reports none, and the Platform then refuses to push a profile that uses fields ("Knovas Connector zu alt").
+- `capabilities` tells the Platform which sync-body keys this Knovas Connector understands; an older one reports none, and the Platform then refuses to push a profile that uses fields ("Der Knovas Connector ist zu alt").
# line 125
-The SQLite `documents` table gains five columns (`fields_digest`, `fields_sent`, `fields_outcome`, `fields_warning_codes`, `fields_attempts`), added on first start; an older Knovas Connector ignores them. `fields_digest` is a hash of the configuration, never the values. Resetting the sync state (above) also forgets which documents had fields: the next full upload sends them again.
+The SQLite `documents` table gains five columns (`fields_digest`, `fields_sent`, `fields_outcome`, `fields_warning_codes`, `fields_attempts`), added on first start; an older Knovas Connector ignores them. `fields_digest` is a hash of the configuration, never the values. Resetting the sync state (above) also forgets which documents had fields: the next full upload sends them again.
```

`RELEASE_NOTES.md`, the unreleased "Dokumentfelder" section (line 87 changes the label only):

```diff
# line 26
-  brauchen Entitaetswerte von Knovas Connector (z.B. `client`) zusaetzlich
+  brauchen Entitaetswerte vom Knovas Connector (z.B. `client`) zusaetzlich
# line 28
-  ab, und Knovas Connector indexiert das Dokument ohne Felder.
+  ab, und der Knovas Connector indexiert das Dokument ohne Felder.
# line 42
-- **Was Knovas Connector sendet:** je Ordner der Ingestion feste Werte
+- **Was der Knovas Connector sendet:** je Ordner der Ingestion feste Werte
# line 46
-  indexiert, und eine Pfadvorlage, die Knovas Connector nicht uebersetzen
+  indexiert, und eine Pfadvorlage, die der Knovas Connector nicht uebersetzen
# line 50
-  sendet Knovas Connector alle seine Dokumente erneut -- jedes ein
+  sendet der Knovas Connector alle seine Dokumente erneut -- jedes ein
# line 72
-  Grenze: aeltere Logzeilen von Plattform und Knovas Connector nennen
+  Grenze: aeltere Logzeilen von Plattform und Knovas Connector nennen
# line 82
-  das Profil, loescht Knovas Connector die Upload-Werte der betroffenen
+  das Profil, loescht der Knovas Connector die Upload-Werte der betroffenen
# line 87
-Knovas Connector: [KnovasConnector/CHANGELOG.md](KnovasConnector/CHANGELOG.md) (0.3.0).
+Knovas Connector: [KnovasConnector/CHANGELOG.md](KnovasConnector/CHANGELOG.md) (0.3.0).
```

`RELEASE_NOTES.md`, the three unreleased lines (lines 100, 108, 129 in `$MERGED`) that PR #22 renames outside `08228ba` (its exact text, no article, so the later merge sees identical changes):

```diff
-- **Keine Kopie auf dem Server.** Knovas Connector fragt Microsoft Graph nach
+- **Keine Kopie auf dem Server.** Knovas Connector fragt Microsoft Graph nach
-- Das Client-Secret gelangt nur in den Knovas-Connector-Container, nie in die
+- Das Client-Secret gelangt nur in den Knovas-Connector-Container, nie in die
-Behoben dabei: Das Standardprofil von Knovas Connector uebernahm keine Dateien,
+Behoben dabei: Das Standardprofil von Knovas Connector uebernahm keine Dateien,
```

`RELEASE_NOTES.md`, the rename entry (wording of `a619d10`) — insert before the heading line ``## Dokumente in OneDrive und SharePoint (`KNOVAS_DOCUMENTS_URL`)``, i.e. after INT-3c's section:

```markdown
## Knovas Connector heisst jetzt Knovas Connector

Nur der Name, den man liest: Dokumentation, Oberflaeche und Ausgaben der
Skripte. Ordner `KnovasConnector/`, Docker-Dienst `knovas-connector`, die
`RC_*`-Einstellungen und die Konfigurationsschluessel bleiben, wie sie sind --
eine bestehende Installation wird ohne Aenderung an `knovas.env` aktualisiert.

```

(When PR #22 is merged later, it adds the same section next to its "Wissenstypen" section; keep one copy.)

`docs/client/README.md` and `docs/client/document-fields.md`:

```diff
# docs/client/README.md line 115
-| `RC_DOC_FIELDS=off` | Knovas Connector stops sending field values with uploads. |
+| `RC_DOC_FIELDS=off` | The Knovas Connector stops sending field values with uploads. |
# docs/client/document-fields.md line 23
-2. **Your folders**, sent by Knovas Connector with each upload (*Upload*): a
+2. **Your folders**, sent by the Knovas Connector with each upload (*Upload*): a
# docs/client/document-fields.md line 82
-be field values: keep the Platform's and Knovas Connector's logs as
+be field values: keep the Platform's and the Knovas Connector's logs as
```

`knovas.env.example` (comments 103, 114, 116 from the fields branch; 144 from `main`):

```diff
-#   # per-folder values Knovas Connector sends with each upload. They appear
+#   # per-folder values the Knovas Connector sends with each upload. They appear
-#   # Knovas Connector sends no field values with uploads (default on).
+#   # The Knovas Connector sends no field values with uploads (default on).
-#   # After a folder's field settings change, Knovas Connector re-sends its
+#   # After a folder's field settings change, the Knovas Connector re-sends its
-# only a few and Knovas Connector needs most of them.
+# only a few and the Knovas Connector needs most of them.
```

**Mentions `main` added after PR #22's base.**

```diff
# KnovasPlatform/docs/features/experiments.md line 178
-| `EXPERIMENTS_INDEX_PER_MINUTE` | `2` | Uploads pro Minute (1–60), über alle Prozesse zusammen. Der Mandant erlaubt nur wenige Dokument-Uploads pro Minute und teilt sie mit Knovas Connector. |
+| `EXPERIMENTS_INDEX_PER_MINUTE` | `2` | Uploads pro Minute (1–60), über alle Prozesse zusammen. Der Mandant erlaubt nur wenige Dokument-Uploads pro Minute und teilt sie mit dem Knovas Connector. |
# KnovasPlatform/docs/features/experiments.md line 1510
-  gehört sonst keinem Dienst des Stacks (Knovas Connector läuft als 10001).
+  gehört sonst keinem Dienst des Stacks (der Knovas Connector läuft als 10001).
# KnovasPlatform/components/experiments_runner/README.md line 122
-shared with no other image of the stack (Knovas Connector is 10001), because
+shared with no other image of the stack (the Knovas Connector is 10001), because
# docs/search-ui-backlog.md line 160
-- **Trefferkontext kommt ausschliesslich aus den Sidecars** des Knovas Connectors;
+- **Trefferkontext kommt ausschliesslich aus den Sidecars** des Knovas Connectors;
# docker-compose.yml line 271 (experiments-runner comment)
-    # Its own uid, shared with no other image (Knovas Connector is 10001): the
+    # Its own uid, shared with no other image (the Knovas Connector is 10001): the
```

(`docbridge_integration/README.md:15`, `knovas.env.example:144` and the backfill usage text are above.)

`KnovasPlatform/docs/README.md` line 26 (renamed by PR #22 outside `08228ba`; its exact text):

```diff
-| Index documents before search | [Knovas Connector](../../KnovasConnector/README.md) |
+| Index documents before search | [Knovas Connector](../../KnovasConnector/README.md) |
```

Not renamed, on purpose: `RC_*` names; the `knovas-connector` service and container names; `KnovasConnector/` paths and the directory name (`cd KnovasConnector`); identifiers (`KnovasConnectorClient`, `KnovasConnectorError`, `knovas_connector_client.py`, `rc_*` metric names); comments and docstrings in code (e.g. `admin_system.py`'s `# ── Knovas Connector ───` section comment, `config/config.yaml` comments); tests' own identifiers and fake error messages; `docs/superpowers/`; released sections (`RELEASE_NOTES.md` from `# v1.0.0`, `KnovasConnector/CHANGELOG.md` from `## 0.1.1`); `KnovasConnector/docker-compose.yml:1` and `KnovasConnector/docs/nginx-edge.example.conf:1` (comments in the Connector's own examples, which `08228ba` left); and the option value `Knovas Connector` in `src/experiments/packs/engineering.yaml` (lines 93, 155) and `tests/test_experiments_packs.py:349` — a stored choice value of recorded experiments; renaming it needs a data migration (follow-up if wanted).

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pf-pytest tests/test_web_admin_ingestion.py tests/test_web_admin_doc_fields.py tests/test_web_admin_system_doc_fields.py tests/test_knovas_connector_client.py tests/test_ingestion_compiler.py
rc-pytest tests/test_doc_fields_no_values_in_logs.py tests/unit/test_backfill_partial_ocr.py tests/unit/test_sync_executor_doc_fields.py
```

Expected: PASS.

- [ ] **Step 5: Check that no user-facing "Knovas Connector" is left**

Save the check (reusable by later tasks) and run it from `$WT`:

```bash
cat > "$SP/rc_leftovers.py" <<'EOF'
"""User-facing "Knovas Connector" left in the tree (the rename policy of 08228ba).

Prints path:line: text for every hit and exits 1 when there is one. Paths,
identifiers, service and container names, comments in code, docstrings,
tests, docs/superpowers/ and released release-note sections keep the name.
Run from the repository root; the file list comes from `git ls-files`.
"""
import io
import re
import subprocess
import sys
import tokenize

WORD = re.compile(r"Remote ?Controller", re.I)
ALLOWED_LINES = {
    "*Formerly Knovas Connector.* Only the name people read changed: the folder",
    "## Knovas Connector heisst jetzt Knovas Connector",
}


def visible(text):
    """The text without paths, identifiers and service names."""
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"\]\([^)]*\)", "]", text)
    text = re.sub(r"\S*KnovasConnector/\S*|\S*/KnovasConnector\b\S*", "", text)
    text = re.sub(r"\./KnovasConnector\b|\bcd (\S+/)?KnovasConnector\b", "", text)
    text = re.sub(r"Knovas Connector(Error|Client)\b|knovas[-_]connector|knovasconnector-", "", text)
    return text


def in_scope(path):
    if path.startswith("docs/superpowers/") or "/tests/" in path or path.startswith("tests/"):
        return False
    if path.endswith(".md") or path in ("knovas.env.example", "docker-compose.yml"):
        return True
    if path.startswith(("KnovasPlatform/components/docbridge_integration/src/",
                        "KnovasConnector/src/", "KnovasConnector/scripts/")):
        return path.endswith((".py", ".html", ".js"))
    return path.startswith("scripts/") and path.endswith(".sh") and "/lib/" not in path


def python_strings(source):
    """(line, text) of every string literal that is not a docstring."""
    out, prev = [], None
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.STRING or tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
            docstring = prev is None or prev.type in (tokenize.NEWLINE, tokenize.INDENT,
                                                       tokenize.DEDENT, tokenize.NL)
            if not docstring and tok.string.strip("\"'") != "Knovas Connector":
                out.append((tok.start[0], tok.string))
        if tok.type not in (tokenize.COMMENT, tokenize.NL):
            prev = tok
    return out


def text_lines(path, source):
    lines = source.split("\n")
    if path.endswith((".html", ".js")):
        blank = lambda m: "\n" * m.group(0).count("\n")
        source = re.sub(r"\{#.*?#\}|<!--.*?-->|/\*.*?\*/", blank, source, flags=re.S)
        lines = [re.sub(r"(^|\s)//.*$", "", line) for line in source.split("\n")]
    elif path.endswith(".sh"):
        lines = ["" if line.lstrip().startswith("#") else line for line in lines]
    elif path == "RELEASE_NOTES.md":
        stop = next((i for i, line in enumerate(lines) if re.match(r"# v\d", line)), len(lines))
        lines = lines[:stop]
    elif path == "KnovasConnector/CHANGELOG.md":
        stop = next((i for i, line in enumerate(lines) if re.match(r"## \d", line)), len(lines))
        lines = lines[:stop]
    return list(enumerate(lines, 1))


files = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
hits = []
for path in [p for p in files.decode("utf-8").split("\0") if p]:
    if not in_scope(path):
        continue
    try:
        source = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        continue
    if not WORD.search(source):
        continue
    pairs = python_strings(source) if path.endswith(".py") else text_lines(path, source)
    for number, text in pairs:
        if text.strip() not in ALLOWED_LINES and WORD.search(visible(text)):
            hits.append(f"{path}:{number}: {text.strip()[:120]}")
print("\n".join(hits) or "no user-facing Knovas Connector left")
sys.exit(1 if hits else 0)
EOF
cd "$WT" && PYTHONIOENCODING=utf-8 py -3.13 "$SP/rc_leftovers.py"
```

Expected: `no user-facing Knovas Connector left`, exit 0. (Run on the tree before this task it lists exactly the lines of Step 3, which is how the list was made.)

- [ ] **Step 6: Commit**

```bash
cd "$WT"
git add KnovasPlatform/components/docbridge_integration/src \
        KnovasPlatform/components/docbridge_integration/tests/test_web_admin_ingestion.py \
        KnovasPlatform/components/docbridge_integration/tests/test_web_admin_doc_fields.py \
        KnovasPlatform/components/docbridge_integration/tests/test_web_admin_system_doc_fields.py \
        KnovasPlatform/components/docbridge_integration/README.md \
        KnovasPlatform/components/experiments_runner/README.md \
        KnovasPlatform/docs/README.md KnovasPlatform/docs/features/document-fields.md \
        KnovasPlatform/docs/features/experiments.md \
        KnovasConnector/src/sync/doc_fields_metrics.py KnovasConnector/scripts/backfill_partial_ocr.py \
        KnovasConnector/docs/configuration.md KnovasConnector/docs/operations.md \
        RELEASE_NOTES.md docs/client/README.md docs/client/document-fields.md \
        docs/search-ui-backlog.md docker-compose.yml knovas.env.example
git diff --name-only; git ls-files --others --exclude-standard   # expect: no output (all staged)
git commit -F - <<'EOF'
rc+platform: say Knovas Connector wherever people read the name

08228ba renamed what existed at PR #22's base. This applies its policy to
what came later: the Platform strings, operator log lines, metric help
and docs the document-fields branch added; the mentions main added after
PR #22's base; four console messages 08228ba missed; and the four lines
PR #22 renames outside 08228ba, with its exact text, so a later merge of
PR #22 sees identical changes. The release-notes entry is a619d10's.

Folder, service, RC_* settings, config keys, identifiers, comments,
docstrings, released release-note sections and the stored options of
the engineering pack keep the old name.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
py -3.13 scripts/check_ascii_py.py HEAD~1..HEAD     # expect: 0 added line(s) with a byte > 0x7F
```

---

### Task INT-6: Document fields are on for every account (texts aligned with 1.5.0)

Knovas 1.5.0: "Document fields, including filtering by fields in search and in lists, is on for every account … an account can be switched off … an older Knovas release does not have them." Every statement below said "off by default" / "only once Knovas has enabled it" and now says that, plus that the Platform and the Connector check every answer and behave as before when it is off. The S2 caveats (a Knovas update reading the pointer from the request body) are removed: 1.5.0 documents it. The runtime messages stay (`doc_fields_view.py` "Knovas-Update nötig …", `doctor.sh`'s S2 probe, and "… bei Knovas nicht freigeschaltet" in `admin_doc_fields.py`, `doc_fields_routes.py`, `doc_fields_view.py`, `admin_documents.py`): they describe what Knovas answered. The S1 sentences (BROKERED tenants) stay as they are — see the open question in the report.

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/admin_system.py` (`_doc_fields_check`, hint of the off state), `scripts/doctor.sh` (Dokumentfelder probe)
- Modify (docs): `RELEASE_NOTES.md`, `KnovasPlatform/docs/features/document-fields.md`, `docs/client/document-fields.md`, `docs/client/README.md`, `KnovasConnector/docs/configuration.md`, `KnovasConnector/docs/operations.md`, `KnovasConnector/CHANGELOG.md`, `KnovasConnector/README.md`, `docs/specifications.md`, `knovas.env.example`, `KnovasPlatform/components/docbridge_integration/README.md`, `docs/KnovasAPI/README.md`, `docs/KnovasAPI/Secure_API.md`, `KnovasConnector/.env.example`
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_web_admin_system_doc_fields.py` (`TestFourStates::test_off`), `KnovasPlatform/mock_knovas_api/tests/test_doctor_doc_fields_probe.py` (`test_feature_off_probes_nothing_more`)

**Interfaces:**
- Consumes: INT-5's renamed sentences (quoted below as INT-5 leaves them).
- Produces: System tab hint for the `aus` state: "Knovas hat Dokumentfelder fuer diesen Mandanten ausgeschaltet, oder der Knovas-Server kennt sie noch nicht. Suche und Ingestion laufen wie bisher."; doctor line "info  Dokumentfelder: aus (…) -- switched off for this tenant at Knovas,". FLD's F6 texts (`listing_only`) are not touched here.

- [ ] **Step 1: Write the failing tests**

`tests/test_web_admin_system_doc_fields.py`, `TestFourStates::test_off` — replace

```python
        assert "nicht freigeschaltet" in check["hint"]
```

with

```python
        # Knovas 1.5.0 has document fields on for every account: "aus" means
        # Knovas switched them off, or the server predates them.
        assert check["hint"] == ("Knovas hat Dokumentfelder fuer diesen Mandanten ausgeschaltet, "
                                 "oder der Knovas-Server kennt sie noch nicht. Suche und "
                                 "Ingestion laufen wie bisher.")
```

`KnovasPlatform/mock_knovas_api/tests/test_doctor_doc_fields_probe.py`, `test_feature_off_probes_nothing_more` — replace

```python
    assert len(lines) == 1 and lines[0].startswith("info  Dokumentfelder: aus")
```

with

```python
    assert len(lines) == 1 and lines[0].startswith("info  Dokumentfelder: aus")
    # Knovas 1.5.0 has document fields on for every account: "aus" is a
    # switch Knovas turned (or an older server), not a step still to come.
    assert "switched off for this tenant at Knovas" in lines[0]
```

- [ ] **Step 2: Run them to verify they fail**

```bash
pf-pytest tests/test_web_admin_system_doc_fields.py::TestFourStates::test_off
cd "$WT/KnovasPlatform/mock_knovas_api" && $SP/venvs/pf/Scripts/python -m pytest tests/test_doctor_doc_fields_probe.py::test_feature_off_probes_nothing_more -q
```

Expected: both FAIL (the hint still says "nicht freigeschaltet"; the doctor line says "not enabled for this tenant at Knovas").

- [ ] **Step 3: Rewrite the code texts**

`src/web_interface/admin_system.py`, `_doc_fields_check` — replace

```python
            hint="Knovas hat Dokumentfelder fuer diesen Mandanten nicht freigeschaltet. "
                 "Suche und Ingestion laufen wie bisher."), False
```

with

```python
            hint="Knovas hat Dokumentfelder fuer diesen Mandanten ausgeschaltet, oder der "
                 "Knovas-Server kennt sie noch nicht. Suche und Ingestion laufen wie bisher."), False
```

`scripts/doctor.sh` — replace

```python
    print(f"   info  Dokumentfelder: aus ({seen}) -- not enabled for this tenant at Knovas,")
```

with

```python
    print(f"   info  Dokumentfelder: aus ({seen}) -- switched off for this tenant at Knovas,")
```

(the next line, "or the server predates them. The Platform shows no document-field UI.", stays).

- [ ] **Step 4: Rewrite the documentation statements**

Each block: the current text (as INT-5 leaves it) → the new text.

`RELEASE_NOTES.md`, "Dokumentfelder" section, the first six lines of the first bullet (lines 9-14; the bullet's last three lines, from "nennt die Stufe", stay):

```markdown
- **Voraussetzung: Knovas muss Document Fields fuer den Mandanten
  freischalten.** Bei Knovas ist die Funktion standardmaessig aus, und nichts
  in `knovas.env` schaltet sie ein. Die Plattform fragt Knovas, was es fuer
  den Mandanten anbietet, und zeigt nur das; ohne Freischaltung (oder mit einem
  aelteren Server) bleibt alles wie bisher, ohne neue Oberflaeche und ohne
  neue Schluessel in den Anfragen. *Verwaltung -> System -> Dokumentfelder*
```

→

```markdown
- **Bei Knovas fuer jeden Mandanten eingeschaltet.** Seit Knovas 1.5.0 sind
  Dokumentfelder, auch das Filtern nach Feldern in Suche und Listen, fuer
  jeden Mandanten eingeschaltet; nichts in `knovas.env` schaltet sie ein.
  Knovas kann sie fuer einen Mandanten abschalten, und aeltere Server kennen
  sie nicht: Plattform und Knovas Connector pruefen jede Antwort und bleiben,
  solange die Funktion aus ist, wie bisher, ohne neue Oberflaeche und ohne
  neue Schluessel in den Anfragen. *Verwaltung -> System -> Dokumentfelder*
```

`RELEASE_NOTES.md`, second bullet (the S2 caveat goes, the S1 sentence stays):

```markdown
- **Mindestens noetige Knovas-Version:** Feldbereich, *Felder* unter
  *Dokumente* und jede Wertbearbeitung brauchen eine Knovas-Version, die den
  Dokumentverweis von `GET /secured/graph/doc-values` aus dem Anfragekoerper
  liest (Aenderung S2; die Plattform setzt nie einen Verweis in eine Adresse).
  Gegen eine aeltere Version mit Dokumentfeldern zeigen diese Stellen
  *Knovas-Update noetig*, und nichts ist bearbeitbar; Filter und Listen sind
  nicht betroffen. `./scripts/doctor.sh` meldet den Fall
  (`FAIL Dokumentfelder: Knovas-Update noetig`). In einem BROKERED-Mandanten
  brauchen Entitaetswerte vom Knovas Connector (z.B. `client`) zusaetzlich
  Aenderung S1; vorher lehnt Knovas solche Uploads mit `assertion_rejected`
  ab, und der Knovas Connector indexiert das Dokument ohne Felder.
```

→

```markdown
- **BROKERED-Mandanten:** Entitaetswerte vom Knovas Connector (z.B. `client`)
  brauchen zusaetzlich Aenderung S1; vorher lehnt Knovas solche Uploads mit
  `assertion_rejected` ab, und der Knovas Connector indexiert das Dokument
  ohne Felder.
```

`KnovasPlatform/docs/features/document-fields.md`, lines 9-26:

```markdown
**Prerequisite: Knovas must enable Document Fields for the tenant.** It is
off by default on the Knovas side. Nothing in `knovas.env` switches it on —
the Platform asks Knovas what it serves and shows only that. Against a tenant
without the feature, or an older server, the Platform behaves exactly as
before: no new UI, and no new keys in its requests.

**Minimum Knovas release.** The field panel, the *Felder* drawer under
*Dokumente* and every value edit need a Knovas release whose
`GET /secured/graph/doc-values` reads the document pointer from the JSON body
(integration change S2; the Platform never puts a pointer in a URL). Against
an earlier release that has Document Fields, these places say *Knovas-Update
nötig: der Server liest den Dokumentverweis noch nicht aus dem Anfragekörper*,
and nothing can be edited; filters and lists are not affected.
`./scripts/doctor.sh` names this case (`FAIL Dokumentfelder: Knovas-Update
noetig`). In a BROKERED tenant, entity values sent by the Knovas Connector (for
example `client`) also need change S1; before it, Knovas refuses such an
upload with `401 assertion_rejected` and the Knovas Connector indexes the document
without its fields (`refused:assertion_rejected`).
```

→

```markdown
**On for every account since Knovas 1.5.0.** Document fields, including
filtering by fields in search and in lists, is on for every Knovas account.
Knovas can still switch it off for an account, and an older server does not
have it, so the Platform and the Knovas Connector check every answer: the
Platform asks Knovas what it serves and shows only that, and nothing in
`knovas.env` switches it on. Against an account with the feature off, or an
older server, the Platform behaves exactly as before: no new UI, and no new
keys in its requests.

In a BROKERED tenant, entity values sent by the Knovas Connector (for example
`client`) also need change S1; before it, Knovas refuses such an upload with
`401 assertion_rejected` and the Knovas Connector indexes the document without
its fields (`refused:assertion_rejected`).
```

and in the state table (line 41) replace the cell start

```markdown
| `aus` | feature off, tenant not enabled, knowledge graph off, older server
```

with

```markdown
| `aus` | feature switched off for the account, knowledge graph off, older server
```

(the rest of the row stays).

`docs/client/document-fields.md`, lines 3-9:

```markdown
**Available only once Knovas has enabled Document Fields for your tenant**,
on a Knovas release that reads the document reference of the field panel from
the request body (otherwise the panel and every value edit say *Knovas-Update
nötig*; filters and lists still work).
Until then the Platform shows none of what follows, and search works as
before. Ask Knovas to enable it; afterwards **Verwaltung → System →
Dokumentfelder** says which level your tenant has:
```

→

```markdown
**On for every Knovas account since Knovas 1.5.0**, including filtering by
fields in search and in lists. Knovas can still switch it off for an account,
and an older Knovas server does not have it; the Platform and the Knovas
Connector check every answer, and while it is off the Platform shows none of
what follows and search works as before. **Verwaltung → System →
Dokumentfelder** says which level your tenant has:
```

`docs/client/README.md` line 113 (`DOC_FIELDS_UI=off` row) — replace the sentence

```markdown
They appear only once Knovas has enabled them for your tenant; this switch can only turn them off.
```

with

```markdown
Knovas has them on for every account since 1.5.0, and the Platform shows them as far as Knovas serves them; this switch can only turn them off.
```

`KnovasConnector/docs/configuration.md`, lines 120-126:

```markdown
Knovas can keep typed **document fields** per document (Mandant, Zeitraum,
Dokumentart, …) — but only once Knovas has enabled *Document Fields* for the
tenant. Nothing here switches that on: the Knovas Connector sends fields when a
source is configured with them and reads from each init answer whether the
server took them. Against a server or tenant without the feature the fields are
ignored, the document is indexed exactly as before, and `/sync/status` says
`"server": "not_accepted"` ([operations.md](operations.md#document-fields)).
```

→

```markdown
Knovas keeps typed **document fields** per document (Mandant, Zeitraum,
Dokumentart, …); since Knovas 1.5.0 they are on for every account. Knovas can
still switch them off for an account, and an older server does not have them,
so nothing here switches them on: the Knovas Connector sends fields when a
source is configured with them and reads from each init answer whether the
server took them. Against a server or account without the feature the fields
are ignored, the document is indexed exactly as before, and `/sync/status`
says `"server": "not_accepted"` ([operations.md](operations.md#document-fields)).
```

`KnovasConnector/docs/operations.md` line 70 — replace the sentence

```markdown
Document fields work only once Knovas has enabled them for the tenant; until then the Knovas Connector sends them where configured, the server ignores them, and nothing else changes.
```

with

```markdown
Since Knovas 1.5.0, document fields are on for every account; where Knovas has them off (or the server is older), the Knovas Connector still sends them where configured, the server ignores them, and nothing else changes.
```

`KnovasConnector/CHANGELOG.md` line 9 (line 7 in `$MERGED`), under `### 0.3.0`:

```markdown
Takes effect only for a tenant where Knovas has enabled Document Fields. Against any other server the bodies, the outcomes and the indexing are as in 0.2.0.
```

→

```markdown
Knovas 1.5.0 has Document fields on for every account. Where Knovas has them switched off, or against an older server, the bodies, the outcomes and the indexing are as in 0.2.0.
```

`KnovasConnector/README.md` line 12 (line 7 in `$MERGED`) — replace the sentence

```markdown
They take effect only once Knovas has enabled Document Fields for the tenant; until then the server ignores them and `/sync/status` reports `not_accepted`.
```

with

```markdown
Knovas 1.5.0 has Document fields on for every account; where Knovas has them off (or the server is older), the server ignores them and `/sync/status` reports `not_accepted`.
```

`docs/specifications.md` line 196:

```markdown
**Document fields (optional; effective only once Knovas has enabled Document Fields for the tenant)**
```

→

```markdown
**Document fields (optional; on for every Knovas account since 1.5.0 — where Knovas has them off, or the server is older, the Connector behaves as before)**
```

`knovas.env.example`, lines 103-105:

```bash
#   # per-folder values the Knovas Connector sends with each upload. They appear
#   # only once Knovas has enabled them for the tenant; nothing here can turn
#   # them on. These switches only turn them off or say who may edit.
```

→

```bash
#   # per-folder values the Knovas Connector sends with each upload. Knovas has
#   # them on for every account since 1.5.0; where Knovas has them off, the
#   # Platform and the Connector work as before. Nothing here can turn them on:
#   # these switches only turn them off or say who may edit.
```

`KnovasPlatform/components/docbridge_integration/README.md`, section "Document fields" (the S2 caveat goes; line 85, "the mock's `pointer_in_body = False` plays a Knovas release before S2", describes the mock and stays):

```markdown
shown only as far as Knovas serves them for the tenant, in secured mode only.
The field panel, the *Felder* drawer and value edits need a Knovas release that
reads the `GET /secured/graph/doc-values` pointer from the JSON body (S2);
an earlier one answers `400 invalid_value` (`pointer`) and the Platform shows
*Knovas-Update nötig* (`./scripts/doctor.sh` says so too). In a BROKERED
tenant, the Knovas Connector's entity values also need S1.
```

→

```markdown
shown only as far as Knovas serves them for the tenant, in secured mode only.
Knovas 1.5.0 has them on for every account; the Platform checks every answer
and behaves as before where Knovas has them off. In a BROKERED tenant, the
Knovas Connector's entity values also need S1.
```

Three more statements of the same kind found by the grep of Step 5 (not in the spec's list):

`docs/KnovasAPI/README.md`, lines 25-26:

```markdown
→ *Document values and fields*). They take effect only for a tenant where
Knovas has enabled them.
```

→

```markdown
→ *Document values and fields*). Since Knovas 1.5.0 they are on for every
account; Knovas can still switch them off, so clients check every answer.
```

`docs/KnovasAPI/Secure_API.md` (the retired pointer page), lines 27-29:

```markdown
**Document fields** take effect only for a tenant where Knovas has enabled
them. On any other tenant the new request keys are ignored and the answers
carry none of the new keys.
```

→

```markdown
**Document fields** are on for every account since Knovas 1.5.0. Where Knovas
has them off, or on an older server, the new request keys are ignored and the
answers carry none of the new keys.
```

`KnovasConnector/.env.example`, lines 101-102:

```bash
# configured with fields send them on upload; Knovas takes them only once it
# has enabled Document Fields for the tenant (the status says so).
```

→

```bash
# configured with fields send them on upload; Knovas 1.5.0 takes them on every
# account unless Knovas has switched them off (the status says so).
```

- [ ] **Step 5: Run the tests and the text check**

```bash
pf-pytest tests/test_web_admin_system_doc_fields.py
cd "$WT/KnovasPlatform/mock_knovas_api" && $SP/venvs/pf/Scripts/python -m pytest tests -q
cd "$WT" && git grep -n -i -E "only once Knovas|has enabled (them|Document Fields)|enabled \*Document Fields\*|standardmaessig aus|Freischaltung|tenant not enabled|Ask Knovas to enable it|Knovas-Update n|Anfragek|S2\)" -- '*.md' knovas.env.example KnovasConnector/.env.example ':!docs/superpowers'
cd "$WT" && PYTHONIOENCODING=utf-8 py -3.13 "$SP/rc_leftovers.py"
```

Expected: System tab 13 passed, mock 209 passed; the `git grep` prints nothing; `no user-facing Knovas Connector left`.

- [ ] **Step 6: Commit**

```bash
cd "$WT"
git add KnovasPlatform/components/docbridge_integration/src/web_interface/admin_system.py scripts/doctor.sh \
        KnovasPlatform/components/docbridge_integration/tests/test_web_admin_system_doc_fields.py \
        KnovasPlatform/mock_knovas_api/tests/test_doctor_doc_fields_probe.py \
        RELEASE_NOTES.md KnovasPlatform/docs/features/document-fields.md docs/client/document-fields.md \
        docs/client/README.md KnovasConnector/docs/configuration.md KnovasConnector/docs/operations.md \
        KnovasConnector/CHANGELOG.md KnovasConnector/README.md docs/specifications.md knovas.env.example \
        KnovasPlatform/components/docbridge_integration/README.md docs/KnovasAPI/README.md \
        docs/KnovasAPI/Secure_API.md KnovasConnector/.env.example
git commit -F - <<'EOF'
rc+platform: Document fields are on for every account (Knovas 1.5.0)

Knovas 1.5.0 has Document fields, filtering included, on for every
account; Knovas can still switch them off, and older servers lack them.
Every text that said "off by default" or "only once Knovas has enabled
it" now says that, plus that the Platform and the Connector check every
answer and behave as before when it is off. The caveats that a Knovas
update (S2, the pointer in the request body) is still needed are gone:
1.5.0 documents it. The System tab and doctor.sh no longer call an
account with Document fields off "not enabled".

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

## Part B2 — Extraction fixes and library output (EXT)

This part covers spec §6 E1–E7 and §8 L2, L3 and L7: the Connector's extraction defects with knovas-extract 0.4.0a1, plus the library output the Connector and the Platform should use. Where the Platform mirrors Connector code (`knovas_extract_upload.py`, `knovas_transmit/*`), both change in the same task and both test suites move with it (spec D7). No test in either repository compares mirrored code byte for byte. The mirror tests are the parallel files `test_page_markers.py` / `test_knovas_extract_upload.py`, and they stay parallel.

**Dependencies.**
- Runs after INT-* (merge, rename sweep, 1.5.0 texts). Every anchor quotes code as it is in `$MERGED`.
- EXT tasks build on each other in order: EXT-n quotes the code as EXT-(n−1) left it.
- Nothing here waits for PIN, FLD or RCF.
- Other parts consume these names: `ocr_dpi()`, `ocr_workers()`, `sentence_emit_max_bytes()`, `docx_text_mode()` (and the existing `pdf_text_mode()`, `ocr_engine()`).
  - REX uses them for `current_extraction_stamp()`; PIN/L5 uses them for `set_build_info()`.
  - If REX or PIN is scheduled first, EXT-3, EXT-4, EXT-5 and EXT-13 must land before them.
- L3 (EXT-13) needs the Part A library (DOCX layout mode, metadata `docx:text_mode`) only at verification time. Its tests fake `extract`, and against a library without DOCX layout mode the code keeps today's behaviour.
- EXT-14 changes a dependency pin: re-install the rc venv as its steps say.

**Conventions in this part.**
- `rc-pytest` and `pf-pytest` as defined in the plan header.
- Shell test: `cd $WT && bash scripts/lib/test_rc_extraction_settings.sh`.
- Commits run from `$WT`.
- The uploader class in `KnovasConnector/src/sync/knovas_uploader.py` is `SemantixUploader`. The contract's `KnovasUploader` means this class.
- EXT-1 creates the subsection `### Extraction (knovas-extract 0.4.0a1)` directly below `## Unreleased` in `KnovasConnector/CHANGELOG.md`. Every later EXT task appends one bullet to it.

**Decisions taken in this part (flagged for review):**
1. The partial note loses `reason: ocr_backend_none`. It now carries counts and the backend name only (spec E1). The degraded-backend counter counts a note whose `ocr_backend` is `"none"`. That covers both the uncounted case and pages the library skipped for want of an engine, because 0.4 names the engine whenever OCR ran.
2. The backfill clears legacy notes `{"reason": "ocr_backend_none"}` without uploading.
   - All OCR keys arrived together in 0.4.0a1 (library CHANGELOG).
   - So the old rule only wrote that reason when `pdf:ocr_pages_skipped == 0`, i.e. for born-digital PDFs whose text is complete.
   - Re-sending them would be billed.
3. The Platform's `ocr_time_budget_seconds` gets the same E2 formula fix. The numbers are identical at its defaults (1 worker); only `RC_OCR_WORKERS > 1` on the Platform changes. The Platform keeps its workers=1 / 50 pages / 60 s defaults.
4. An invalid page timeout or page cap logs a warning and uses the default, as spec E5 says. The silent clamp of `_env_int(minimum=)` is not used. Numbers count only as ASCII digits, the same rule `doctor.sh` applies.
5. `extraction configuration invalid: …` is retried every cycle and never counts toward `RC_EXTRACT_MAX_RETRIES`. Otherwise every PDF would be recorded partial after 3 cycles and backfilled without OCR.
6. Three narrowly scoped locks instead of one: the config file, the body file, and start/stop. A stop holds its lock while it joins the worker for up to 120 s; one shared lock would block every config save for that long.
7. Whether a DOCX gets a `tables` payload is decided from the library's metadata `docx:text_mode == "layout"`, not from the env value. A library without DOCX layout mode keeps today's payload.
8. The Platform also validates `RC_TESSERACT_LANG` / `advanced.extraction.ocr_language`, the page timeout and the page cap. An invalid language there made every PDF upload carry only its path line.
9. The PyMuPDF pin is `1.28.0`: the Platform's pin, and nothing in the Connector needs newer.

| Task | Spec |
|---|---|
| EXT-1 | E1 partial rule (both), degraded metric |
| EXT-2 | E1 backfill: legacy notes |
| EXT-3 | E2 OCR budget and workers |
| EXT-4 | E3 resolution (both) |
| EXT-5 | E4 sentence gate default 0 |
| EXT-6 | E4 page numbers from pages (both) |
| EXT-7 | E5 settings validated, `CONFIG_INVALID_PREFIX` |
| EXT-8 | E5 `doctor.sh` check |
| EXT-9 | E6 locks on routes that write state |
| EXT-10 | E6 gunicorn gthread |
| EXT-11 | E7 small corrections |
| EXT-12 | L2 description |
| EXT-13 | L3 DOCX text mode (both) |
| EXT-14 | L7 PyMuPDF pin |
| EXT-15 | L7 preview fallback |

---

### Task EXT-1: One partial rule for knovas-extract 0.4 OCR metadata (Connector + Platform)

**Files:**
- Modify: `KnovasConnector/src/sync/document_text.py` (`partial_note_for`; new `_PARTIAL_NOTE_COUNTS`, `ocr_backend_missing`)
- Modify: `KnovasConnector/src/sync/knovas_uploader.py` (import, `UploadResult.partial` comment, degraded-metric condition in `SemantixUploader.upload_file`)
- Modify: `KnovasConnector/src/sync/sync_executor.py` (`record_upload_outcome` docstring)
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py` (module docstring bullet, `partial_note_for`, `_PARTIAL_NOTE_COUNTS`)
- Modify: `KnovasConnector/docs/operations.md`, `KnovasPlatform/components/docbridge_integration/README.md`, `KnovasConnector/CHANGELOG.md`
- Test: `KnovasConnector/tests/helpers.py`, `KnovasConnector/tests/unit/test_document_text.py`, `KnovasConnector/tests/unit/test_knovas_uploader.py`, `KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `sync.document_text.partial_note_for(doc: ExtractedDocument, *, expect_ocr: bool) -> Optional[dict[str, Any]]`. Keys, when the library reported them: `ocr_pages_skipped`, `ocr_pages_failed`, `ocr_pages`, `text_pages`, `ocr_backend`. No `reason`.
  - `sync.document_text.ocr_backend_missing(note: Optional[dict[str, Any]]) -> bool`
  - `knovas_extract_upload.partial_note_for(extra: Optional[dict[str, Any]], *, expect_ocr: bool) -> Optional[dict[str, Any]]` (same rule)
  - Test helper `tests.helpers.OCR_EXTRA_04: dict[str, dict]`

- [ ] **Step 1: Write the failing tests**

Append to `KnovasConnector/tests/helpers.py`:

```python
#: knovas-extract 0.4 OCR metadata as its PDF extractor reports it whenever
#: ``ocr=`` is passed (library ``extractors/pdf.py``, ``_OcrSummary``): every
#: key is present, and ``pdf:ocr_backend`` stays "none" unless OCR ran.
#: The Platform's tests/test_knovas_extract_upload.py carries the same table.
OCR_EXTRA_04 = {
    # no page needed OCR: complete, whether or not an engine is installed
    "born_digital": {"pdf:ocr_pages": 0, "pdf:text_pages": 12, "pdf:ocr_pages_skipped": 0,
                     "pdf:ocr_pages_failed": 0, "pdf:ocr_backend": "none"},
    # text pages and scanned pages, every scanned page OCR'd
    "mixed": {"pdf:ocr_pages": 3, "pdf:text_pages": 9, "pdf:ocr_pages_skipped": 0,
              "pdf:ocr_pages_failed": 0, "pdf:ocr_backend": "tesserocr",
              "pdf:ocr_backend_version": "5.3.0", "pdf:ocr_seconds": 4.2, "pdf:ocr_mean_conf": 91.5},
    # the time budget ran out on a long scan
    "starved": {"pdf:ocr_pages": 40, "pdf:text_pages": 0, "pdf:ocr_pages_skipped": 12,
                "pdf:ocr_pages_failed": 0, "pdf:ocr_backend": "tesserocr"},
    # one page timed out in Tesseract: an empty page
    "failed": {"pdf:ocr_pages": 9, "pdf:text_pages": 2, "pdf:ocr_pages_skipped": 0,
               "pdf:ocr_pages_failed": 1, "pdf:ocr_backend": "cli"},
    # no engine: 0.4 keeps the text layers and counts the scanned pages as skipped
    "no_engine": {"pdf:ocr_pages": 0, "pdf:text_pages": 2, "pdf:ocr_pages_skipped": 5,
                  "pdf:ocr_pages_failed": 0, "pdf:ocr_backend": "none"},
    # a library that does not count skipped pages
    "uncounted": {"pdf:ocr_pages": 0, "pdf:ocr_backend": "none"},
}
```

In `KnovasConnector/tests/unit/test_document_text.py`, replace the whole block from `# --- partial notes read defensively from metadata.extra ----------------------` through the end of `test_partial_note_for_reads_extra_defensively` with:

```python
# --- partial notes: one rule in both components (spec E1) --------------------


def test_partial_note_for_reads_extra_defensively():
    from sync.document_text import ExtractedDocument, partial_note_for

    assert partial_note_for(ExtractedDocument(text="x", sentences=None, extra={}), expect_ocr=True) is None
    assert partial_note_for(ExtractedDocument(text="x", sentences=None, extra=None), expect_ocr=True) is None
    as_strings = ExtractedDocument(
        text="x", sentences=None,
        extra={"pdf:ocr_pages_skipped": "7", "pdf:ocr_pages_failed": "0", "pdf:ocr_backend": " CLI "},
    )
    assert partial_note_for(as_strings, expect_ocr=True) == {
        "ocr_pages_skipped": 7, "ocr_pages_failed": 0, "ocr_backend": "cli",
    }
    zero = ExtractedDocument(text="x", sentences=None, extra={"pdf:ocr_pages_skipped": "0"})
    assert partial_note_for(zero, expect_ocr=True) is None


@pytest.mark.parametrize("case, expect_ocr, note, degraded", [
    ("born_digital", True, None, False),
    ("mixed", True, None, False),
    ("starved", True, {"ocr_pages_skipped": 12, "ocr_pages_failed": 0, "ocr_pages": 40,
                       "text_pages": 0, "ocr_backend": "tesserocr"}, False),
    ("failed", True, {"ocr_pages_skipped": 0, "ocr_pages_failed": 1, "ocr_pages": 9,
                      "text_pages": 2, "ocr_backend": "cli"}, False),
    ("no_engine", True, {"ocr_pages_skipped": 5, "ocr_pages_failed": 0, "ocr_pages": 0,
                         "text_pages": 2, "ocr_backend": "none"}, True),
    ("uncounted", True, {"ocr_pages": 0, "ocr_backend": "none"}, True),
    ("uncounted", False, None, False),
])
def test_partial_rule_on_the_0_4_key_combinations(case, expect_ocr, note, degraded):
    """Spec E1 on the metadata knovas-extract 0.4 really reports: a
    born-digital PDF extracted with ``ocr=`` is complete, a failed page makes
    a document partial, and only a missing engine is a degraded backend."""
    from tests.helpers import OCR_EXTRA_04

    from sync.document_text import ExtractedDocument, ocr_backend_missing, partial_note_for

    doc = ExtractedDocument(text="x", sentences=None, extra=dict(OCR_EXTRA_04[case]))
    got = partial_note_for(doc, expect_ocr=expect_ocr)
    assert got == note
    assert ocr_backend_missing(got) is degraded


def test_a_born_digital_pdf_extracted_with_ocr_options_is_complete(tmp_path, monkeypatch):
    """The audit's reproduction: with ``ocr=`` the library reports backend
    "none" and zero skipped pages for a PDF that needed no OCR -- whether or
    not Tesseract is installed. Before spec E1 every such PDF was partial."""
    fitz = pytest.importorskip("fitz")
    from sync import document_text

    if document_text.OcrOptions is None or not document_text.extract_accepts("ocr"):
        pytest.skip("needs knovas-extract >= 0.4 (ocr=)")
    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "true")
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    pdf = fitz.open()
    for i in range(2):
        pdf.new_page().insert_text((72, 72), f"Seite {i + 1}. Digitaler Text.")
    path = tmp_path / "digital.pdf"
    path.write_bytes(pdf.tobytes())
    pdf.close()

    doc = document_text.extract_document(path)

    assert doc.extra["pdf:ocr_pages_skipped"] == 0
    assert doc.extra["pdf:ocr_backend"] == "none"
    assert document_text.partial_note_for(doc, expect_ocr=True) is None
```

In `KnovasConnector/tests/unit/test_knovas_uploader.py`, replace the whole function `test_upload_result_partial_when_no_ocr_backend_but_ocr_expected` (it asserts `{"reason": "ocr_backend_none", "ocr_backend": "none"}`) with:

```python
class _CountingCounter:
    def __init__(self):
        self.count = 0

    def inc(self, amount=1):
        self.count += amount


@pytest.mark.parametrize("case, partial, degraded", [
    ("born_digital", None, False),
    ("starved", {"ocr_pages_skipped": 12, "ocr_pages_failed": 0, "ocr_pages": 40,
                 "text_pages": 0, "ocr_backend": "tesserocr"}, False),
    ("failed", {"ocr_pages_skipped": 0, "ocr_pages_failed": 1, "ocr_pages": 9,
                "text_pages": 2, "ocr_backend": "cli"}, False),
    ("no_engine", {"ocr_pages_skipped": 5, "ocr_pages_failed": 0, "ocr_pages": 0,
                   "text_pages": 2, "ocr_backend": "none"}, True),
    ("uncounted", {"ocr_pages": 0, "ocr_backend": "none"}, True),
])
def test_upload_partial_note_and_degraded_metric_follow_the_library_counts(
    mock_config, tmp_path, monkeypatch, case, partial, degraded
):
    """Spec E1: rc_ocr_backend_degraded_total counts a missing engine only --
    not a born-digital PDF (it counted every one), not a budget trip, not a
    failed page."""
    from tests.helpers import OCR_EXTRA_04

    from sync import ocr_metrics
    from sync.document_text import ExtractedDocument

    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "true")
    counter = _CountingCounter()
    monkeypatch.setattr(ocr_metrics, "OCR_BACKEND_DEGRADED", counter)
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf")
    doc = ExtractedDocument(text="Deckblatt.", sentences=None, extra=dict(OCR_EXTRA_04[case]))
    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req, patch(
        "sync.knovas_uploader.extract_document_guarded", return_value=doc
    ), patch("sync.knovas_uploader.write_context_sidecar", return_value=True):
        req.side_effect = [_ok_response(), _ok_response()]
        result = uploader.upload_file(pdf, "akten/scan.pdf", {"ingestion": {"identifier_prefix": "corpus"}})
    assert result.status == "ok"
    assert result.partial == partial
    assert counter.count == (1 if degraded else 0)
```

In `KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py`, replace the whole function `test_partial_note_for_reads_extra_defensively` with:

```python
# knovas-extract 0.4 OCR metadata as its PDF extractor reports it whenever
# ``ocr=`` is passed -- every key present, backend "none" unless OCR ran. The
# same table as KnovasConnector/tests/helpers.py OCR_EXTRA_04.
_OCR_EXTRA_04 = {
    "born_digital": {"pdf:ocr_pages": 0, "pdf:text_pages": 12, "pdf:ocr_pages_skipped": 0,
                     "pdf:ocr_pages_failed": 0, "pdf:ocr_backend": "none"},
    "mixed": {"pdf:ocr_pages": 3, "pdf:text_pages": 9, "pdf:ocr_pages_skipped": 0,
              "pdf:ocr_pages_failed": 0, "pdf:ocr_backend": "tesserocr",
              "pdf:ocr_backend_version": "5.3.0", "pdf:ocr_seconds": 4.2, "pdf:ocr_mean_conf": 91.5},
    "starved": {"pdf:ocr_pages": 40, "pdf:text_pages": 0, "pdf:ocr_pages_skipped": 12,
                "pdf:ocr_pages_failed": 0, "pdf:ocr_backend": "tesserocr"},
    "failed": {"pdf:ocr_pages": 9, "pdf:text_pages": 2, "pdf:ocr_pages_skipped": 0,
               "pdf:ocr_pages_failed": 1, "pdf:ocr_backend": "cli"},
    "no_engine": {"pdf:ocr_pages": 0, "pdf:text_pages": 2, "pdf:ocr_pages_skipped": 5,
                  "pdf:ocr_pages_failed": 0, "pdf:ocr_backend": "none"},
    "uncounted": {"pdf:ocr_pages": 0, "pdf:ocr_backend": "none"},
}


def test_partial_note_for_reads_extra_defensively():
    assert m.partial_note_for({}, expect_ocr=True) is None
    assert m.partial_note_for(None, expect_ocr=True) is None
    assert m.partial_note_for({"pdf:ocr_pages_skipped": "0"}, expect_ocr=True) is None
    assert m.partial_note_for({"pdf:ocr_pages_skipped": "7", "pdf:ocr_backend": "cli"}, expect_ocr=True) == {
        "ocr_pages_skipped": 7, "ocr_backend": "cli",
    }


@pytest.mark.parametrize("case, expect_ocr, note", [
    ("born_digital", True, None),
    ("mixed", True, None),
    ("starved", True, {"ocr_pages_skipped": 12, "ocr_pages_failed": 0, "ocr_pages": 40,
                       "text_pages": 0, "ocr_backend": "tesserocr"}),
    ("failed", True, {"ocr_pages_skipped": 0, "ocr_pages_failed": 1, "ocr_pages": 9,
                      "text_pages": 2, "ocr_backend": "cli"}),
    ("no_engine", True, {"ocr_pages_skipped": 5, "ocr_pages_failed": 0, "ocr_pages": 0,
                         "text_pages": 2, "ocr_backend": "none"}),
    ("uncounted", True, {"ocr_pages": 0, "ocr_backend": "none"}),
    ("uncounted", False, None),
])
def test_partial_rule_matches_the_connector(case, expect_ocr, note):
    """Spec E1, the Connector's rule: failed OCR pages make an upload partial;
    a born-digital PDF (backend "none", nothing skipped) never does."""
    assert m.partial_note_for(dict(_OCR_EXTRA_04[case]), expect_ocr=expect_ocr) == note
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rc-pytest tests/unit/test_document_text.py -k "partial or born_digital" tests/unit/test_knovas_uploader.py -k "partial or degraded"`

Expected: FAIL.
- `test_partial_rule_on_the_0_4_key_combinations`: `ImportError: cannot import name 'ocr_backend_missing'`.
- `test_partial_note_for_reads_extra_defensively`: the note has no `ocr_pages_failed`.
- `test_a_born_digital_pdf_extracted_with_ocr_options_is_complete`: `{'reason': 'ocr_backend_none', ...} is not None`.
- The uploader `born_digital` case: `partial` is not None and `counter.count == 1`.

Run: `pf-pytest tests/test_knovas_extract_upload.py -k partial`

Expected: FAIL.
- `failed` gives `None` instead of a note.
- `uncounted` carries `reason`.
- `starved` lacks `ocr_pages_failed`.

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/src/sync/document_text.py`, replace the whole function:

```python
def partial_note_for(doc: ExtractedDocument, *, expect_ocr: bool) -> Optional[dict[str, Any]]:
    """The partial note for a returned document, or None when it is complete.

    Read defensively: today's library sets none of the `pdf:ocr_*` keys.
    Partial when the library counted skipped OCR pages (budget trip), or when
    it reports `pdf:ocr_backend == "none"` although OCR was configured — the
    image pages were left empty for want of an engine, so the document must
    not be taken for complete (plan `[C-sec-8]`). Counts and identifiers only.
    """
    extra = doc.extra or {}
    skipped = _int_or_none(extra.get("pdf:ocr_pages_skipped"))
    ocr_pages = _int_or_none(extra.get("pdf:ocr_pages"))
    backend = extra.get("pdf:ocr_backend")
    backend_s = str(backend).strip().lower() if isinstance(backend, str) else ""
    note: dict[str, Any] = {}
    if skipped is not None and skipped > 0:
        note["ocr_pages_skipped"] = skipped
        if ocr_pages is not None:
            note["ocr_pages"] = ocr_pages
    elif expect_ocr and backend_s == "none":
        note["reason"] = "ocr_backend_none"
        if ocr_pages is not None:
            note["ocr_pages"] = ocr_pages
    else:
        return None
    if backend_s:
        note["ocr_backend"] = backend_s
    text_pages = _int_or_none(extra.get("pdf:text_pages"))
    if text_pages is not None:
        note["text_pages"] = text_pages
    return note
```

with:

```python
#: The library's OCR counts a partial note carries (counts only, never text):
#: note key -> metadata.extra key.
_PARTIAL_NOTE_COUNTS = (
    ("ocr_pages_skipped", "pdf:ocr_pages_skipped"),
    ("ocr_pages_failed", "pdf:ocr_pages_failed"),
    ("ocr_pages", "pdf:ocr_pages"),
    ("text_pages", "pdf:text_pages"),
)


def partial_note_for(doc: ExtractedDocument, *, expect_ocr: bool) -> Optional[dict[str, Any]]:
    """The partial note for a returned document, or None when it is complete.

    One rule in the Connector and the Platform (spec E1; the mirror is
    ``knovas_extract_upload.partial_note_for``). A document is partial when

    * the library counted skipped OCR pages (``pdf:ocr_pages_skipped > 0``):
      the page cap, the time budget, the pixel cap -- or no OCR engine, for
      which 0.4 counts every page that needed OCR as skipped;
    * or failed OCR pages (``pdf:ocr_pages_failed > 0``): a failed page is an
      empty page;
    * or -- only for a library that does not count skipped pages -- OCR was
      expected, ``pdf:ocr_backend == "none"`` and ``pdf:ocr_pages_skipped`` is
      absent.

    With ``ocr=`` passed, 0.4 reports every OCR key: a born-digital PDF says
    backend ``"none"`` with zero skipped pages -- nothing needed OCR, so it is
    complete wherever Tesseract is installed (the rule before counted it
    partial, metered a degraded backend and backfilled it on every run).
    The note carries the counts the library reported and the backend name.
    """
    extra = doc.extra or {}
    counts = {key: _int_or_none(extra.get(source)) for key, source in _PARTIAL_NOTE_COUNTS}
    skipped, failed = counts["ocr_pages_skipped"], counts["ocr_pages_failed"]
    backend = extra.get("pdf:ocr_backend")
    backend_s = str(backend).strip().lower() if isinstance(backend, str) else ""
    partial = (
        (skipped is not None and skipped > 0)
        or (failed is not None and failed > 0)
        or (expect_ocr and backend_s == "none" and skipped is None)
    )
    if not partial:
        return None
    note: dict[str, Any] = {key: value for key, value in counts.items() if value is not None}
    if backend_s:
        note["ocr_backend"] = backend_s
    return note


def ocr_backend_missing(note: Optional[dict[str, Any]]) -> bool:
    """Whether a partial note says pages went without OCR for want of an
    engine: the library reports ``ocr_backend == "none"``. 0.4 names the
    engine whenever OCR ran, so a partial note with ``none`` is either the
    uncounted case of ``partial_note_for`` or pages the library skipped
    because no backend was available. Feeds ``rc_ocr_backend_degraded_total``
    only; a budget trip or a failed page is not a degraded backend."""
    return bool(note) and note.get("ocr_backend") == "none"
```

In `KnovasConnector/src/sync/knovas_uploader.py`, replace:

```python
from sync.document_text import (
    ConversionError,
    ExtractedDocument,
    _env_flag,
    extract_document_guarded,
    partial_note_for,
    pdf_ocr_enabled,
)
```

with:

```python
from sync.document_text import (
    ConversionError,
    ExtractedDocument,
    _env_flag,
    extract_document_guarded,
    ocr_backend_missing,
    partial_note_for,
    pdf_ocr_enabled,
)
```

Replace:

```python
    #: Counts and reasons when the text landed only in part (OCR pages
    #: skipped on a budget trip, no OCR backend although one was configured);
    #: None for a complete document. Recorded by the executor (GI-EXTRACT-02).
    partial: Optional[dict[str, Any]] = None
```

with:

```python
    #: The library's OCR counts when the text landed only in part (pages
    #: skipped on a budget trip or for want of an engine, pages that failed
    #: OCR; ``document_text.partial_note_for``); None for a complete
    #: document. Recorded by the executor (GI-EXTRACT-02).
    partial: Optional[dict[str, Any]] = None
```

Replace:

```python
            if partial and partial.get("reason") == "ocr_backend_none":
                ocr_metrics.OCR_BACKEND_DEGRADED.inc()
```

with:

```python
            if ocr_backend_missing(partial):
                ocr_metrics.OCR_BACKEND_DEGRADED.inc()
```

In `KnovasConnector/src/sync/sync_executor.py` (docstring of `record_upload_outcome`), replace:

```
    * ``"partial"`` — the library returned, OCR pages were skipped (or no OCR
      backend was available): fingerprint stored so the next cycle does not
      re-upload the file, note kept for ``scripts/backfill_partial_ocr.py``;
```

with:

```
    * ``"partial"`` — the library returned, OCR pages were skipped or failed
      (or no OCR backend was available; ``document_text.partial_note_for``):
      fingerprint stored so the next cycle does not re-upload the file, note
      kept for ``scripts/backfill_partial_ocr.py``;
```

In `KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py`, replace in the module docstring:

```
* ``metadata.extra`` is read defensively for ``pdf:ocr_pages_skipped`` /
  ``pdf:ocr_backend``; a ``partial`` note (counts only) is surfaced in the
  upload result, the log and the sidecar (GI-EXTRACT-02).
```

with:

```
* ``metadata.extra`` is read defensively for the OCR counts, by the
  Connector's partial rule (``partial_note_for``, spec E1); a ``partial``
  note (counts only) is surfaced in the upload result, the log and the
  sidecar (GI-EXTRACT-02).
```

Then replace the whole function `partial_note_for` (from `def partial_note_for(extra: Optional[dict[str, Any]], *, expect_ocr: bool) -> Optional[dict[str, Any]]:` through its `return note`) with:

```python
#: The library's OCR counts a partial note carries (counts only, never text):
#: note key -> metadata.extra key. Same as the Connector.
_PARTIAL_NOTE_COUNTS = (
    ("ocr_pages_skipped", "pdf:ocr_pages_skipped"),
    ("ocr_pages_failed", "pdf:ocr_pages_failed"),
    ("ocr_pages", "pdf:ocr_pages"),
    ("text_pages", "pdf:text_pages"),
)


def partial_note_for(extra: Optional[dict[str, Any]], *, expect_ocr: bool) -> Optional[dict[str, Any]]:
    """The partial note for a returned document, or None when it is complete.

    The Connector's rule (``KnovasConnector/src/sync/document_text.py``
    ``partial_note_for``, spec E1): partial when the library counted skipped
    OCR pages (page cap, budget, pixel cap, or no OCR engine -- 0.4 counts
    every page that needed OCR as skipped then), or failed OCR pages (a
    failed page is an empty page), or -- only for a library that does not
    count skipped pages -- when OCR was expected and it reports
    ``pdf:ocr_backend == "none"`` without ``pdf:ocr_pages_skipped``. A
    born-digital PDF reports backend ``none`` with zero skipped pages: no
    page needed OCR, so it is complete. Counts and the backend name only.
    """
    extra = extra or {}
    counts = {key: _int_or_none(extra.get(source)) for key, source in _PARTIAL_NOTE_COUNTS}
    skipped, failed = counts["ocr_pages_skipped"], counts["ocr_pages_failed"]
    backend = extra.get("pdf:ocr_backend")
    backend_s = str(backend).strip().lower() if isinstance(backend, str) else ""
    partial = (
        (skipped is not None and skipped > 0)
        or (failed is not None and failed > 0)
        or (expect_ocr and backend_s == "none" and skipped is None)
    )
    if not partial:
        return None
    note: dict[str, Any] = {key: value for key, value in counts.items() if value is not None}
    if backend_s:
        note["ocr_backend"] = backend_s
    return note
```

In `KnovasConnector/docs/operations.md` (section "Partial documents and the backfill"), replace:

```
- the extraction child was killed on the wall-clock ceiling or died (`extractor died (exit -9)`) `RC_EXTRACT_MAX_RETRIES` times in a row — note `extract_retries_exhausted`;
- OCR is configured but the library reported no OCR backend (`ocr_backend_none`).
```

with:

```
- OCR failed on some pages (`ocr_pages_failed`) — those pages are empty;
- no OCR engine was available: the pages that needed OCR are counted as skipped and the note says `ocr_backend: none` (`rc_ocr_backend_degraded_total` counts these documents);
- the extraction child was killed on the wall-clock ceiling or died (`extractor died (exit -9)`) `RC_EXTRACT_MAX_RETRIES` times in a row — note `extract_retries_exhausted`.

A born-digital PDF is never partial: knovas-extract 0.4 reports `ocr_backend: none` with zero skipped pages for it, which only says that no page needed OCR. The note carries `ocr_pages_skipped`, `ocr_pages_failed`, `ocr_pages`, `text_pages` and `ocr_backend` as far as the library reported them.
```

In `KnovasPlatform/components/docbridge_integration/README.md`, replace:

```
**Partial uploads.** `metadata.extra` is read defensively: `pdf:ocr_pages_skipped > 0`
(budget trip), or `pdf:ocr_backend = "none"` with OCR configured and no
skipped-page count, makes the upload `partial`. The note — counts and the
backend name only, never text (GI-EXTRACT-04) — is logged, returned in the
sync result (`partial`) and written into the document's sidecar, from where
the search result carries it as `context_partial`.
```

with:

```
**Partial uploads.** The same rule as the Connector (spec E1):
`pdf:ocr_pages_skipped > 0` (budget or page cap, or no OCR engine —
knovas-extract 0.4 counts those pages as skipped), `pdf:ocr_pages_failed > 0`,
or — for a library that does not count skipped pages — `pdf:ocr_backend =
"none"` with OCR configured and no skipped-page count makes the upload
`partial`. A born-digital PDF (backend `none`, nothing skipped) is complete.
The note — counts and the backend name only, never text (GI-EXTRACT-04) — is
logged, returned in the sync result (`partial`) and written into the
document's sidecar, from where the search result carries it as
`context_partial`.
```

In `KnovasConnector/CHANGELOG.md`, insert directly below the line `## Unreleased`:

```markdown

### Extraction (knovas-extract 0.4.0a1)

- **Partial rule** (Connector and Platform, spec E1): a PDF is partial only when the library counted skipped OCR pages (budget, page or pixel cap, or no engine), failed OCR pages (new: a failed page is empty), or — for a library without skipped-page counts — reports no backend although OCR was expected. Born-digital PDFs are complete again: with knovas-extract 0.4 every one was recorded partial, counted in `rc_ocr_backend_degraded_total` and re-uploaded by the backfill. The note carries `ocr_pages_skipped`, `ocr_pages_failed`, `ocr_pages`, `text_pages`, `ocr_backend` (no `reason`); the degraded-backend counter counts notes with `ocr_backend: none` only.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/unit/test_document_text.py tests/unit/test_knovas_uploader.py tests/unit/test_sync_executor_partial.py tests/unit/test_backfill_partial_ocr.py`

Expected: PASS.

Run: `pf-pytest tests/test_knovas_extract_upload.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/document_text.py KnovasConnector/src/sync/knovas_uploader.py \
  KnovasConnector/src/sync/sync_executor.py KnovasConnector/tests/helpers.py \
  KnovasConnector/tests/unit/test_document_text.py KnovasConnector/tests/unit/test_knovas_uploader.py \
  KnovasConnector/docs/operations.md KnovasConnector/CHANGELOG.md \
  KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py \
  KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py \
  KnovasPlatform/components/docbridge_integration/README.md
git commit -F - <<'EOF'
rc+platform: one partial rule for knovas-extract 0.4 OCR metadata

With ocr= passed, knovas-extract 0.4 reports every OCR key and leaves
pdf:ocr_backend at "none" unless OCR ran. The Connector read that as "no
engine": every born-digital PDF was recorded partial, counted as a degraded
backend and re-uploaded by the backfill on every run. One rule now in both
components (spec E1): partial on skipped pages, on failed pages (a failed
page is empty; they were ignored), or - only for a library without skipped
counts - on backend "none" with OCR expected. The note carries counts only;
the degraded-backend counter counts notes with backend "none".

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-2: Backfill clears the old rule's notes for born-digital PDFs without uploading

**Files:**
- Modify: `KnovasConnector/scripts/backfill_partial_ocr.py` (docstring, `LEGACY_COMPLETE_REASON`, `_complete_at_knovas`, `main`)
- Modify: `KnovasConnector/docs/operations.md`, `KnovasConnector/CHANGELOG.md`
- Test: `KnovasConnector/tests/unit/test_backfill_partial_ocr.py`

**Interfaces:**
- Consumes: EXT-1. New notes never carry `reason: ocr_backend_none`, so that value marks a note the old rule wrote.
- Produces: the backfill's summary line gains `cleared=<n>`.

- [ ] **Step 1: Write the failing test**

Append to `KnovasConnector/tests/unit/test_backfill_partial_ocr.py`:

```python
def test_old_backend_none_notes_are_cleared_without_an_upload(tmp_path, monkeypatch, backfill):
    """Spec E1: the rule before it recorded every born-digital PDF partial
    with {"reason": "ocr_backend_none"} (knovas-extract 0.4 says backend
    "none" when no page needed OCR). Their text is complete at Knovas: the
    note goes, nothing is uploaded -- each upload is billed."""
    root, state_path = _setup(tmp_path, monkeypatch)
    (root / "Mandant" / "digital.pdf").write_bytes(b"%PDF-1.4 stub")
    state = SyncStateStore(str(state_path))
    state.record_partial(
        "Mandant/digital.pdf", "2026-01-01T00:00:00Z", 13, "tk-3",
        {"reason": "ocr_backend_none", "ocr_pages": 0, "ocr_backend": "none", "text_pages": 4},
    )
    state.close()
    body = {"mode": "incremental", "sources": [{"path": str(root), "recursive": True}],
            "filters": {}, "ingestion": {"identifier_prefix": "rc"}}
    uploaded: list = []

    def fake_upload(self, local_path, rel, sync_body, access_groups=()):
        uploaded.append(rel)
        return UploadResult(rel, "tk-new", 2, "ok", 3)

    with patch("sync.sync_scheduler.load_last_sync_body", return_value=body), patch(
        "sync.knovas_uploader.SemantixUploader.__init__", return_value=None
    ), patch("sync.knovas_uploader.SemantixUploader.upload_file", fake_upload):
        assert backfill.main(["--dry-run"]) == 0
        state = SyncStateStore(str(state_path))
        try:
            assert "Mandant/digital.pdf" in state.partial_paths(), "a dry run changes nothing"
        finally:
            state.close()
        assert backfill.main([]) == 0
    assert uploaded == ["Mandant/scan.pdf"], "the born-digital PDF is not re-sent"
    state = SyncStateStore(str(state_path))
    try:
        assert state.partial_paths() == ["Mandant/gone.pdf"]
        assert state.status_for("Mandant/digital.pdf", "2026-01-01T00:00:00Z", 13) == "synced", \
            "its fingerprint stays: the next cycle does not upload it either"
    finally:
        state.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `rc-pytest tests/unit/test_backfill_partial_ocr.py::test_old_backend_none_notes_are_cleared_without_an_upload`

Expected: FAIL: `assert ['Mandant/digital.pdf', 'Mandant/scan.pdf'] == ['Mandant/scan.pdf']`.

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/scripts/backfill_partial_ocr.py`, replace in the module docstring:

```
* an exhausted retry counter is re-extracted with OCR DISABLED, so at
  least the text pages land instead of the file looping on the hung page.
```

with:

```
* an exhausted retry counter is re-extracted with OCR DISABLED, so at
  least the text pages land instead of the file looping on the hung page;
* a note `{"reason": "ocr_backend_none"}` is cleared WITHOUT an upload: the
  rule before spec E1 wrote it for born-digital PDFs (knovas-extract 0.4
  reports backend "none" when no page needed OCR), whose text is complete
  at Knovas -- re-sending them would only be billed.
```

Replace:

```python
RETRIES_EXHAUSTED = "extract_retries_exhausted"
```

with:

```python
RETRIES_EXHAUSTED = "extract_retries_exhausted"
#: What the partial rule before spec E1 recorded for born-digital PDFs.
LEGACY_COMPLETE_REASON = "ocr_backend_none"
```

Directly after the function `_env_for`, insert:

```python
def _complete_at_knovas(note: dict[str, Any]) -> bool:
    """A note the rule before spec E1 wrote for a document that is complete.

    That rule recorded ``{"reason": "ocr_backend_none"}`` whenever the library
    said backend "none" -- and knovas-extract 0.4, the only release reporting
    the OCR keys (they arrived together), says so for every born-digital PDF.
    Pages a missing engine left without OCR it counts as skipped, which the
    old rule recorded as ``ocr_pages_skipped`` instead. The current rule
    never writes this reason.
    """
    return note.get("reason") == LEGACY_COMPLETE_REASON
```

In `main`, replace:

```python
        counts = {"synced": 0, "partial": 0, "skipped": 0, "retry": 0, "missing": 0}
        uploader = None if args.dry_run else SemantixUploader()
        started = time.monotonic()
        for rel in partial:
            note = state.partial_note(rel) or {}
            located = _locate(rel, sources)
```

with:

```python
        counts = {"synced": 0, "partial": 0, "skipped": 0, "retry": 0, "missing": 0, "cleared": 0}
        uploader = None if args.dry_run else SemantixUploader()
        started = time.monotonic()
        for rel in partial:
            note = state.partial_note(rel) or {}
            if _complete_at_knovas(note):
                # The fingerprint stays, so the cycle does not upload it either.
                counts["cleared"] += 1
                if not args.dry_run:
                    state.clear_partial(rel)
                continue
            located = _locate(rel, sources)
```

and replace:

```python
        logger.info(
            "Done in %s: synced=%d partial=%d skipped=%d retry=%d missing=%d",
            _duration(time.monotonic() - started), counts["synced"], counts["partial"],
            counts["skipped"], counts["retry"], counts["missing"],
        )
```

with:

```python
        if counts["cleared"]:
            logger.info(
                "%s %d note(s) the old partial rule wrote for born-digital PDFs (no upload)",
                "Would clear" if args.dry_run else "Cleared", counts["cleared"],
            )
        logger.info(
            "Done in %s: synced=%d partial=%d skipped=%d retry=%d missing=%d cleared=%d",
            _duration(time.monotonic() - started), counts["synced"], counts["partial"],
            counts["skipped"], counts["retry"], counts["missing"], counts["cleared"],
        )
```

In `KnovasConnector/docs/operations.md`, directly after the paragraph EXT-1 added ("A born-digital PDF is never partial: …"), add:

```
Notes `{"reason": "ocr_backend_none"}` were written by the rule before this release for exactly such born-digital PDFs; the backfill clears them without an upload (`cleared=` in its summary line).
```

Append to the CHANGELOG subsection `### Extraction (knovas-extract 0.4.0a1)`:

```markdown
- `scripts/backfill_partial_ocr.py` clears the old rule's notes `{"reason": "ocr_backend_none"}` (born-digital PDFs, complete at Knovas) without an upload; the summary line counts them as `cleared`.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/unit/test_backfill_partial_ocr.py tests/unit/test_sync_executor_doc_fields.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/scripts/backfill_partial_ocr.py KnovasConnector/tests/unit/test_backfill_partial_ocr.py \
  KnovasConnector/docs/operations.md KnovasConnector/CHANGELOG.md
git commit -F - <<'EOF'
rc: backfill clears the old rule's notes for born-digital PDFs without uploading

The backfill still selects every partial row. Hosts that ran the old rule
hold one {"reason": "ocr_backend_none"} note per born-digital PDF. With
knovas-extract 0.4 (all OCR keys arrived together) that reason was only
written with zero skipped pages, so the text at Knovas is complete. Each
re-upload is billed, so these notes are cleared without one.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-3: OCR budget no longer collapses; unset `RC_OCR_WORKERS` leaves the pool to the library

**Files:**
- Modify: `KnovasConnector/src/sync/document_text.py` (remove `_available_cores`; `ocr_workers`, `ocr_time_budget_seconds`, `ocr_options_kwargs`, `build_ocr_limits`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py` (`ocr_time_budget_seconds`, `ocr_options_kwargs`)
- Modify: `KnovasConnector/docs/configuration.md`, `KnovasPlatform/components/docbridge_integration/README.md`, `KnovasConnector/CHANGELOG.md`
- Test: `KnovasConnector/tests/unit/test_document_text.py`, `KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `sync.document_text.ocr_time_budget_seconds(timeout_seconds: int, page_timeout_seconds: int) -> int`
  - `sync.document_text.ocr_workers() -> Optional[int]` (None when unset)
  - `ocr_options_kwargs()["workers"]` may be None; `build_ocr_limits` skips None values.
  - Platform `knovas_extract_upload.ocr_time_budget_seconds(timeout_seconds: int, page_timeout_seconds: int) -> int`. Its `ocr_workers() -> int` is unchanged (default 1).

- [ ] **Step 1: Write the failing tests**

In `KnovasConnector/tests/unit/test_document_text.py`:

(a) In `test_text_mode_and_ocr_options_are_sent_when_accepted`, replace:

```python
    # min(240, 300 - 30) = 240, then never more than 300 - 2*60 - 10 = 170
    assert opts.kwargs["time_budget_seconds"] == 170
```

with:

```python
    # min(240, 300 - 30) = 240, then never more than 300 - 60 - 10 = 230
    # (the worker count no longer enters the cap)
    assert opts.kwargs["time_budget_seconds"] == 230
```

(b) In `test_ocr_budgets_reach_the_library_limits`, replace:

```python
    # min(240, 300 - 30) = 240, then never more than 300 - 2*45 - 10 = 200
    assert limits.ocr_time_budget_seconds == 200
```

with:

```python
    # min(240, 300 - 30) = 240, under the cap 300 - 45 - 10 = 245
    assert limits.ocr_time_budget_seconds == 240
```

(c) Replace the whole function `test_ocr_time_budget_derivation` with:

```python
_BUDGET_ENV = ("RC_EXTRACT_TIMEOUT_SECONDS", "RC_EXTRACT_TIMEOUT_PER_PAGE_SECONDS",
               "RC_EXTRACT_TIMEOUT_MAX_SECONDS", "RC_OCR_TIME_BUDGET_SECONDS")


@pytest.mark.parametrize("pages, timeout, page_timeout, expected", [
    # The audit's table (60 s page timeout, ceiling 300 s + 2 s/page): with
    # five or more OCR workers the old cap took these to the 10 s floor.
    (10, 300, 60, 230),
    (50, 300, 60, 230),
    (150, 300, 60, 230),
    (250, 500, 60, 240),
    (400, 800, 60, 240),
    (900, 1800, 60, 240),
    # short ceilings: one page timeout and the margin still fit
    (None, 120, 60, 50),
    (None, 90, 60, 20),
    (None, 120, 30, 80),
    (None, 60, 60, 10),   # never below the floor
    (None, 0, 60, 240),   # no ceiling: the default budget
])
def test_ocr_time_budget_derivation(monkeypatch, pages, timeout, page_timeout, expected):
    from sync.document_text import extract_timeout_seconds, ocr_time_budget_seconds

    for name in _BUDGET_ENV:
        monkeypatch.delenv(name, raising=False)
    if pages is not None:
        assert extract_timeout_seconds(pages) == timeout, "the ceiling the child derives for this PDF"
    assert ocr_time_budget_seconds(timeout, page_timeout) == expected


def test_ocr_time_budget_env_override_is_still_capped(monkeypatch):
    from sync.document_text import ocr_time_budget_seconds

    monkeypatch.setenv("RC_OCR_TIME_BUDGET_SECONDS", "900")
    assert ocr_time_budget_seconds(1800, 60) == 900
    assert ocr_time_budget_seconds(300, 60) == 230, "the env value is still capped by the kill"
    assert ocr_time_budget_seconds(0, 60) == 900, "no ceiling: the env value as is"


@pytest.mark.parametrize("workers", [None, "1", "5", "8"])
def test_the_budget_does_not_depend_on_the_worker_count(monkeypatch, workers):
    """7+ cores no longer matter: pages in flight finish in parallel."""
    from sync.document_text import ocr_options_kwargs

    for name in _BUDGET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("RC_OCR_PAGE_TIMEOUT_SECONDS", raising=False)
    if workers is None:
        monkeypatch.delenv("RC_OCR_WORKERS", raising=False)
    else:
        monkeypatch.setenv("RC_OCR_WORKERS", workers)
    assert ocr_options_kwargs(300)["time_budget_seconds"] == 230


def test_ocr_workers_env(monkeypatch, caplog):
    import logging

    from sync.document_text import ocr_workers

    monkeypatch.delenv("RC_OCR_WORKERS", raising=False)
    assert ocr_workers() is None, "unset: the library sizes the pool"
    for raw, expected in (("3", 3), ("64", 8), ("0", 1), ("-2", 1)):
        monkeypatch.setenv("RC_OCR_WORKERS", raw)
        assert ocr_workers() == expected, raw
    monkeypatch.setenv("RC_OCR_WORKERS", "many")
    with caplog.at_level(logging.WARNING, logger="sync.document_text"):
        assert ocr_workers() is None
    assert any("RC_OCR_WORKERS" in r.getMessage() for r in caplog.records)


def test_unset_workers_leave_the_pool_and_its_ceiling_to_the_library(monkeypatch):
    """OcrOptions(workers=None) -- the library's cgroup-aware default -- and
    Limits.max_ocr_workers at the library's 8: None there would break the
    library's min()."""
    from knovas_extract.result import Limits

    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=False, limits=True)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", _FakeOcrOptions)
    monkeypatch.delenv("RC_OCR_WORKERS", raising=False)
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert seen["ocr"].kwargs["workers"] is None
    assert seen["limits"].max_ocr_workers == Limits().max_ocr_workers


def test_the_library_takes_workers_none(monkeypatch):
    from sync import document_text

    if document_text.OcrOptions is None:
        pytest.skip("needs knovas-extract >= 0.4")
    monkeypatch.delenv("RC_OCR_WORKERS", raising=False)
    options = document_text.build_ocr_options(document_text.ocr_options_kwargs(300))
    limits = document_text.build_ocr_limits(document_text.ocr_options_kwargs(300))
    assert options.workers is None
    assert limits.max_ocr_workers == 8
```

In `KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py`:

(d) In `test_text_mode_and_ocr_options_are_sent_when_accepted`, replace:

```python
    # Platform defaults: min(60, 120 - 30) = 60, under the cap 120 - 1*30 - 10 = 80
```

with:

```python
    # Platform defaults: min(60, 120 - 30) = 60, under the cap 120 - 30 - 10 = 80
```

(e) Replace the whole function `test_ocr_time_budget_derivation` with:

```python
def test_ocr_time_budget_derivation(monkeypatch):
    assert m.ocr_time_budget_seconds(120, 30) == 60, "min(60, 90), under the cap 120 - 30 - 10"
    assert m.ocr_time_budget_seconds(60, 30) == 20, "min(60, 30), capped at 60 - 30 - 10"
    assert m.ocr_time_budget_seconds(40, 30) == 10, "never below the floor"
    assert m.ocr_time_budget_seconds(0, 30) == 60, "no ceiling: default budget"
    monkeypatch.setenv("RC_OCR_TIME_BUDGET_SECONDS", "900")
    assert m.ocr_time_budget_seconds(0, 30) == 900
    assert m.ocr_time_budget_seconds(120, 30) == 80, "the env value is still capped by the kill"


def test_more_ocr_workers_no_longer_collapse_the_budget(monkeypatch):
    """The cap subtracted workers x page timeout: RC_OCR_WORKERS=4 took the
    Platform's 60 s budget to the 10 s floor (120 - 4*30 - 10). The Platform
    keeps one worker by default; only the formula changes (spec E2)."""
    monkeypatch.setenv("RC_OCR_WORKERS", "4")
    assert m.ocr_options_kwargs()["time_budget_seconds"] == 60
    monkeypatch.delenv("RC_OCR_WORKERS")
    assert m.ocr_options_kwargs()["workers"] == 1, "the conservative default stays"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rc-pytest tests/unit/test_document_text.py -k "budget or workers or limits or accepted"`

Expected: FAIL.
- `test_ocr_time_budget_derivation`: `TypeError: ocr_time_budget_seconds() missing 1 required positional argument`.
- `test_the_budget_does_not_depend_on_the_worker_count[5]` / `[8]`: `10 == 230` fails.
- `test_ocr_workers_env`: returns an int when unset.
- `test_unset_workers_…` and `test_the_library_takes_workers_none`: `workers` is an int.

Run: `pf-pytest tests/test_knovas_extract_upload.py -k "budget"`

Expected: FAIL. `TypeError` in `test_ocr_time_budget_derivation`; `10 == 60` in `test_more_ocr_workers_no_longer_collapse_the_budget`.

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/src/sync/document_text.py`, replace:

```python
def _available_cores() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def ocr_workers() -> int:
    """`RC_OCR_WORKERS`, default `max(1, cores - 2)`, at most 8."""
    default = max(1, _available_cores() - 2)
    return max(1, min(DEFAULT_OCR_MAX_WORKERS, _env_int("RC_OCR_WORKERS", default, minimum=1)))


def ocr_time_budget_seconds(timeout_seconds: int, workers: int, page_timeout_seconds: int) -> int:
    """The OCR time budget the child hands the library.

    Default `min(240, timeout - 30)` (`RC_OCR_TIME_BUDGET_SECONDS` overrides),
    and never more than `timeout - workers × page_timeout - 10`: the pool
    stops SUBMITTING when the budget trips, but up to `workers` pages may
    still be running for a page timeout each, and the partial result has to
    reach the parent before the wall-clock kill (plan `[C-sec-0]`). With the
    ceiling disabled (`timeout == 0`) the env value or 240 s applies as is.
    """
    configured = _env_int("RC_OCR_TIME_BUDGET_SECONDS", -1, minimum=-1)
    if timeout_seconds <= 0:
        return configured if configured >= 0 else DEFAULT_OCR_TIME_BUDGET_SECONDS
    budget = configured if configured >= 0 else min(
        DEFAULT_OCR_TIME_BUDGET_SECONDS, timeout_seconds - _OCR_BUDGET_DEFAULT_HEADROOM_SECONDS
    )
    hard_cap = timeout_seconds - workers * page_timeout_seconds - _OCR_BUDGET_RENDER_MARGIN_SECONDS
    budget = min(budget, hard_cap)
    return max(MIN_OCR_TIME_BUDGET_SECONDS, budget)
```

with:

```python
def ocr_workers() -> Optional[int]:
    """`RC_OCR_WORKERS`. Unset (default): None, and the library sizes the pool
    itself -- `min(Limits.max_ocr_workers, CPUs - 1)`, the CPUs counted from
    the affinity mask AND the container's cgroup v2 CPU quota (the
    Connector's old default, cores - 2, saw neither the quota nor the
    library's ceiling). Set: that many, 1 to 8, as before; a value that is
    not a number logs one warning and counts as unset."""
    raw = (os.environ.get("RC_OCR_WORKERS") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid RC_OCR_WORKERS=%r; the library chooses the worker count", raw)
        return None
    return max(1, min(DEFAULT_OCR_MAX_WORKERS, value))


def ocr_time_budget_seconds(timeout_seconds: int, page_timeout_seconds: int) -> int:
    """The OCR time budget the child hands the library (spec E2).

    Default `min(240, timeout - 30)` (`RC_OCR_TIME_BUDGET_SECONDS`
    overrides), never more than `timeout - page_timeout - 10`, at least
    10 s. The pool stops SUBMITTING pages when the budget trips; the pages
    already running finish IN PARALLEL within one page timeout, and the
    partial result then needs the margin to reach the parent before the
    wall-clock kill (plan `[C-sec-0]`). The cap used to subtract
    `workers × page_timeout`, as if those pages ran one after another: from
    five workers on (7+ cores) the budget of every PDF up to ~150 pages fell
    to the 10 s floor and long scans came back partial. With the ceiling
    disabled (`timeout <= 0`) the env value or 240 s applies as is.
    """
    configured = _env_int("RC_OCR_TIME_BUDGET_SECONDS", -1, minimum=-1)
    if timeout_seconds <= 0:
        return configured if configured >= 0 else DEFAULT_OCR_TIME_BUDGET_SECONDS
    budget = configured if configured >= 0 else min(
        DEFAULT_OCR_TIME_BUDGET_SECONDS, timeout_seconds - _OCR_BUDGET_DEFAULT_HEADROOM_SECONDS
    )
    hard_cap = timeout_seconds - page_timeout_seconds - _OCR_BUDGET_RENDER_MARGIN_SECONDS
    return max(MIN_OCR_TIME_BUDGET_SECONDS, min(budget, hard_cap))
```

Replace the whole function `ocr_options_kwargs` (docstring and body, as quoted from `$MERGED`) with:

```python
def ocr_options_kwargs(timeout_seconds: Optional[int] = None) -> dict[str, Any]:
    """Keyword arguments for the library's `OcrOptions`, from the environment.

    `RC_OCR_ENGINE`, `RC_OCR_DPI`, `RC_OCR_WORKERS` (None while unset: the
    library sizes the pool), `RC_OCR_MAX_PAGES`, `RC_OCR_TIME_BUDGET_SECONDS`
    (derived from the extraction timeout when unset),
    `RC_OCR_PAGE_TIMEOUT_SECONDS`, `RC_TESSERACT_LANG`. The cache is added by
    the caller (`cache=`); its size is `RC_OCR_CACHE_MAX_MB` (see
    `sync.ocr_cache`).
    """
    timeout = extract_timeout_seconds() if timeout_seconds is None else int(timeout_seconds)
    page_timeout = _env_int("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, minimum=1)
    return {
        "engine": ocr_engine(),
        "dpi": _env_int("RC_OCR_DPI", DEFAULT_OCR_DPI, minimum=72),
        "workers": ocr_workers(),
        "max_ocr_pages": _env_int("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES),
        "time_budget_seconds": ocr_time_budget_seconds(timeout, page_timeout),
        "page_timeout_seconds": page_timeout,
        "language": tesseract_language(),
    }
```

In `build_ocr_limits`, replace:

```python
    accepted: dict[str, Any] = {}
    for ours, value in options.items():
        for name in _OCR_LIMIT_ALIASES.get(ours, ()):
```

with:

```python
    accepted: dict[str, Any] = {}
    for ours, value in options.items():
        if value is None:
            # RC_OCR_WORKERS unset: Limits.max_ocr_workers keeps the library's
            # ceiling (8); None there would break the library's min().
            continue
        for name in _OCR_LIMIT_ALIASES.get(ours, ()):
```

In `KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py`, replace the whole function `ocr_time_budget_seconds(timeout_seconds: int, workers: int, page_timeout_seconds: int)` with:

```python
def ocr_time_budget_seconds(timeout_seconds: int, page_timeout_seconds: int) -> int:
    """The OCR time budget the child hands the library.

    Default ``min(60, timeout - 30)`` (``RC_OCR_TIME_BUDGET_SECONDS``
    overrides), never more than ``timeout - page_timeout - 10``, at least
    10 s: the pool stops SUBMITTING when the budget trips, the pages already
    running finish in parallel within one page timeout, and the partial
    result has to reach the parent before the wall-clock kill (plan
    ``[C-sec-0]``; the Connector's rule, spec E2 -- the cap used to subtract
    ``workers × page_timeout``, which only the default of one worker kept
    harmless). With the ceiling disabled (``timeout == 0``) the env value or
    60 s applies as is.
    """
    configured = _env_int("RC_OCR_TIME_BUDGET_SECONDS", -1, minimum=-1)
    if timeout_seconds <= 0:
        return configured if configured >= 0 else DEFAULT_OCR_TIME_BUDGET_SECONDS
    budget = configured if configured >= 0 else min(
        DEFAULT_OCR_TIME_BUDGET_SECONDS, timeout_seconds - _OCR_BUDGET_DEFAULT_HEADROOM_SECONDS
    )
    hard_cap = timeout_seconds - page_timeout_seconds - _OCR_BUDGET_RENDER_MARGIN_SECONDS
    return max(MIN_OCR_TIME_BUDGET_SECONDS, min(budget, hard_cap))
```

In the Platform's `ocr_options_kwargs`, replace the body:

```python
    timeout = extract_timeout_seconds() if timeout_seconds is None else int(timeout_seconds)
    workers = ocr_workers()
    page_timeout = _env_int("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, minimum=1)
    return {
        "engine": ocr_engine(),
        "dpi": _env_int("RC_OCR_DPI", DEFAULT_OCR_DPI, minimum=72),
        "workers": workers,
        "max_ocr_pages": _env_int("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES),
        "time_budget_seconds": ocr_time_budget_seconds(timeout, workers, page_timeout),
        "page_timeout_seconds": page_timeout,
        "language": tesseract_language(language),
    }
```

with:

```python
    timeout = extract_timeout_seconds() if timeout_seconds is None else int(timeout_seconds)
    page_timeout = _env_int("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, minimum=1)
    return {
        "engine": ocr_engine(),
        "dpi": _env_int("RC_OCR_DPI", DEFAULT_OCR_DPI, minimum=72),
        "workers": ocr_workers(),
        "max_ocr_pages": _env_int("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES),
        "time_budget_seconds": ocr_time_budget_seconds(timeout, page_timeout),
        "page_timeout_seconds": page_timeout,
        "language": tesseract_language(language),
    }
```

In `KnovasConnector/docs/configuration.md`, replace these two rows:

```
| `RC_OCR_WORKERS` | `max(1, cores − 2)` | OCR pages in parallel, at most 8. |
```

```
| `RC_OCR_TIME_BUDGET_SECONDS` | `min(240, timeout − 30)` | OCR time budget per document; never more than `timeout − workers × page_timeout − 10` so the partial result reaches the parent before the wall-clock kill. |
```

with:

```
| `RC_OCR_WORKERS` | (unset) | OCR pages in parallel. Unset: the library decides — available CPUs − 1, counting the CPU affinity and the container's CPU quota, at most 8 (`Limits.max_ocr_workers`). Set: that many, 1–8. |
```

```
| `RC_OCR_TIME_BUDGET_SECONDS` | `min(240, timeout − 30)` | OCR time budget per document; never more than `timeout − page_timeout − 10` (the pages still running when it trips finish in parallel within one page timeout) and never below 10 s, so the partial result reaches the parent before the wall-clock kill. |
```

In `KnovasPlatform/components/docbridge_integration/README.md`, replace:

```
| `RC_OCR_TIME_BUDGET_SECONDS` | `min(60, timeout − 30)` | OCR time budget per upload; never more than `timeout − workers × page_timeout − 10` so the partial result reaches the request before the wall-clock kill. |
```

with:

```
| `RC_OCR_TIME_BUDGET_SECONDS` | `min(60, timeout − 30)` | OCR time budget per upload; never more than `timeout − page_timeout − 10` (pages still running finish in parallel within one page timeout), so the partial result reaches the request before the wall-clock kill. |
```

Append to the CHANGELOG subsection:

```markdown
- **OCR time budget** no longer collapses to its 10 s floor on hosts with 7+ cores: the cap is `timeout − page_timeout − 10`, because pages in flight finish in parallel (the Platform's formula too; its numbers are unchanged at one worker). **`RC_OCR_WORKERS` unset** sends `workers=None`: the library sizes the pool (CPUs − 1 with affinity and the cgroup CPU quota, at most 8); a set value is used as before.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/unit/test_document_text.py tests/unit/test_backfill_partial_ocr.py`

Expected: PASS.

Run: `pf-pytest tests/test_knovas_extract_upload.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/document_text.py KnovasConnector/tests/unit/test_document_text.py \
  KnovasConnector/docs/configuration.md KnovasConnector/CHANGELOG.md \
  KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py \
  KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py \
  KnovasPlatform/components/docbridge_integration/README.md
git commit -F - <<'EOF'
rc+platform: OCR budget cap counts one page timeout, workers left to the library

The cap subtracted workers x page_timeout although in-flight pages run in
parallel. On hosts with 7+ cores the budget of every PDF up to ~150 pages
fell to the 10 s floor and long scans came back partial. The cap is now
timeout - page_timeout - 10 in both components. Unset RC_OCR_WORKERS sends
workers=None so the library picks CPUs - 1 with the cgroup quota (the
Connector's cores - 2 ignored it). Limits.max_ocr_workers keeps the
library's 8. The Platform keeps its one-worker defaults.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-4: No forced dpi; `RC_OCR_DPI` only when set, 30–1200 (Connector + Platform)

**Files:**
- Modify: `KnovasConnector/src/sync/document_text.py` (module docstring, constants, new `_non_negative_int`, `ocr_dpi`, `ocr_options_kwargs`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py` (constants, `_non_negative_int`, `ocr_dpi`, `ocr_options_kwargs`)
- Modify: `KnovasConnector/docs/configuration.md`, `KnovasPlatform/components/docbridge_integration/README.md`, `KnovasConnector/CHANGELOG.md`
- Test: `KnovasConnector/tests/unit/test_document_text.py`, `KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py`

**Interfaces:**
- Consumes: EXT-3 `ocr_options_kwargs` (the code it quotes below).
- Produces:
  - `sync.document_text.ocr_dpi() -> Optional[int]`, `OCR_DPI_MIN = 30`, `OCR_DPI_MAX = 1200`, `_non_negative_int(raw: str) -> Optional[int]`.
  - Platform `knovas_extract_upload.ocr_dpi() -> Optional[int]` and `_non_negative_int`.
  - `DEFAULT_OCR_DPI` is removed from both components.

- [ ] **Step 1: Write the failing tests**

Append to `KnovasConnector/tests/unit/test_document_text.py`:

```python
# --- resolution: the library's native-resolution rule unless set (spec E3) ---


def test_ocr_dpi_env(monkeypatch, caplog):
    import logging

    from sync.document_text import ocr_dpi

    monkeypatch.delenv("RC_OCR_DPI", raising=False)
    assert ocr_dpi() is None, "unset: native resolution, never upsampled"
    for raw, expected in (("200", 200), ("30", 30), ("1200", 1200), (" 150 ", 150), ("0150", 150)):
        monkeypatch.setenv("RC_OCR_DPI", raw)
        assert ocr_dpi() == expected, raw
    # The same values scripts/lib/test_rc_extraction_settings.sh refuses.
    for raw in ("29", "1201", "0", "-300", "300dpi", "3e2"):
        monkeypatch.setenv("RC_OCR_DPI", raw)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="sync.document_text"):
            assert ocr_dpi() is None, raw
        assert [r.getMessage().split("=")[0] for r in caplog.records] == ["Invalid RC_OCR_DPI"], raw


def test_no_dpi_reaches_the_library_unless_configured(monkeypatch):
    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=False)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", _FakeOcrOptions)
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    monkeypatch.delenv("RC_OCR_DPI", raising=False)
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert "dpi" not in seen["ocr"].kwargs
    monkeypatch.setenv("RC_OCR_DPI", "150")
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert seen["ocr"].kwargs["dpi"] == 150


def test_the_library_default_dpi_applies_when_unset(monkeypatch):
    """With the real OcrOptions: no dpi keeps its None default -- native
    resolution capped at 300, never upsampled (a 150 dpi fax: CER 0.028
    native against 0.145 upsampled to 300)."""
    from sync import document_text

    if document_text.OcrOptions is None:
        pytest.skip("needs knovas-extract >= 0.4")
    monkeypatch.delenv("RC_OCR_DPI", raising=False)
    options = document_text.build_ocr_options(document_text.ocr_options_kwargs(300))
    assert options.dpi is None
```

Append to `KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py`:

```python
def test_ocr_dpi_env(monkeypatch, caplog):
    assert m.ocr_dpi() is None, "unset: native resolution, never upsampled"
    monkeypatch.setenv("RC_OCR_DPI", "200")
    assert m.ocr_dpi() == 200
    monkeypatch.setenv("RC_OCR_DPI", "1201")
    with caplog.at_level(logging.WARNING, logger="knovas_extract_upload"):
        assert m.ocr_dpi() is None
    assert any("RC_OCR_DPI" in r.getMessage() for r in caplog.records)


def test_no_dpi_is_sent_unless_configured(monkeypatch):
    assert "dpi" not in m.ocr_options_kwargs()
    monkeypatch.setenv("RC_OCR_DPI", "150")
    assert m.ocr_options_kwargs()["dpi"] == 150
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rc-pytest tests/unit/test_document_text.py -k dpi`

Expected: FAIL.
- `ImportError: cannot import name 'ocr_dpi'`.
- `test_no_dpi_reaches_the_library_unless_configured`: `'dpi' in kwargs` (300 forced).
- `test_the_library_default_dpi_applies_when_unset`: `300 is None` fails.

Run: `pf-pytest tests/test_knovas_extract_upload.py -k dpi`

Expected: FAIL: `AttributeError: module 'knovas_extract_upload' has no attribute 'ocr_dpi'`, and `'dpi'` is in the kwargs.

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/src/sync/document_text.py`, replace in the module docstring:

```
`RC_TESSERACT_LANG` (default `deu+eng`). With a knovas-extract that takes
`ocr=OcrOptions(...)` the OCR engine, dpi, workers, page cap, time budget
and the RC's disk cache (`sync.ocr_cache`) are passed along; with one that
```

with:

```
`RC_TESSERACT_LANG` (default `deu+eng`). With a knovas-extract that takes
`ocr=OcrOptions(...)` the OCR engine, the page cap, the time budget, the
RC's disk cache (`sync.ocr_cache`) and -- only when set -- the worker count
and the dpi are passed along (unset, the library decides both); with one that
```

Replace:

```python
DEFAULT_OCR_ENGINE = "auto"
OCR_ENGINES = ("auto", "tesserocr", "cli", "mupdf")
DEFAULT_OCR_DPI = 300
DEFAULT_OCR_MAX_PAGES = 500
```

with:

```python
DEFAULT_OCR_ENGINE = "auto"
OCR_ENGINES = ("auto", "tesserocr", "cli", "mupdf")
# RC_OCR_DPI: unset sends no dpi (the library's native-resolution rule); a
# set value must lie in the range OcrOptions accepts.
OCR_DPI_MIN = 30
OCR_DPI_MAX = 1200
DEFAULT_OCR_MAX_PAGES = 500
```

Directly after the function `_env_flag`, insert:

```python
def _non_negative_int(raw: str) -> Optional[int]:
    """`raw` as an int when it is ASCII digits only, else None -- the rule
    `scripts/doctor.sh` applies to the same settings (no sign, no `1_000`)."""
    return int(raw) if raw.isascii() and raw.isdigit() else None
```

Directly after the function `ocr_engine`, insert:

```python
def ocr_dpi() -> Optional[int]:
    """`RC_OCR_DPI` (spec E3). Unset (default): None, no `dpi` is passed and
    the library renders each page at its native resolution, at most 300 dpi,
    never upsampled -- a 150 dpi fax is OCR'd at 150 dpi. Set: every page is
    rendered at exactly that resolution (30-1200, the range `OcrOptions`
    accepts), so a lower-resolution scan IS upsampled. Anything else logs
    one warning and counts as unset."""
    raw = (os.environ.get("RC_OCR_DPI") or "").strip()
    if not raw:
        return None
    value = _non_negative_int(raw)
    if value is None or not OCR_DPI_MIN <= value <= OCR_DPI_MAX:
        logger.warning(
            "Invalid RC_OCR_DPI=%r (%d-%d); rendering at the native resolution",
            raw, OCR_DPI_MIN, OCR_DPI_MAX,
        )
        return None
    return value
```

Replace the body of `ocr_options_kwargs`, i.e. replace:

```python
    timeout = extract_timeout_seconds() if timeout_seconds is None else int(timeout_seconds)
    page_timeout = _env_int("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, minimum=1)
    return {
        "engine": ocr_engine(),
        "dpi": _env_int("RC_OCR_DPI", DEFAULT_OCR_DPI, minimum=72),
        "workers": ocr_workers(),
        "max_ocr_pages": _env_int("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES),
        "time_budget_seconds": ocr_time_budget_seconds(timeout, page_timeout),
        "page_timeout_seconds": page_timeout,
        "language": tesseract_language(),
    }
```

with:

```python
    timeout = extract_timeout_seconds() if timeout_seconds is None else int(timeout_seconds)
    page_timeout = _env_int("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, minimum=1)
    options: dict[str, Any] = {
        "engine": ocr_engine(),
        "workers": ocr_workers(),
        "max_ocr_pages": _env_int("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES),
        "time_budget_seconds": ocr_time_budget_seconds(timeout, page_timeout),
        "page_timeout_seconds": page_timeout,
        "language": tesseract_language(),
    }
    dpi = ocr_dpi()
    if dpi is not None:
        # Only when set: an explicit dpi is used as is and upsamples a 150 dpi
        # fax to 300 (CER 0.145 against 0.028 at its native resolution).
        options["dpi"] = dpi
    return options
```

In `KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py`, replace:

```python
DEFAULT_OCR_ENGINE = "auto"
OCR_ENGINES = ("auto", "tesserocr", "cli", "mupdf")
DEFAULT_OCR_DPI = 300
DEFAULT_OCR_WORKERS = 1
```

with:

```python
DEFAULT_OCR_ENGINE = "auto"
OCR_ENGINES = ("auto", "tesserocr", "cli", "mupdf")
# RC_OCR_DPI: unset sends no dpi (the library's native-resolution rule).
OCR_DPI_MIN = 30
OCR_DPI_MAX = 1200
DEFAULT_OCR_WORKERS = 1
```

Directly after the Platform's `_env_flag`, insert:

```python
def _non_negative_int(raw: str) -> Optional[int]:
    """``raw`` as an int when it is ASCII digits only, else None (the
    Connector's rule)."""
    return int(raw) if raw.isascii() and raw.isdigit() else None
```

Directly after the Platform's `ocr_engine`, insert:

```python
def ocr_dpi() -> Optional[int]:
    """``RC_OCR_DPI``, the Connector's rule (spec E3): unset sends no dpi --
    native resolution, at most 300, never upsampled; set must be 30-1200,
    anything else logs one warning and counts as unset."""
    raw = (os.environ.get("RC_OCR_DPI") or "").strip()
    if not raw:
        return None
    value = _non_negative_int(raw)
    if value is None or not OCR_DPI_MIN <= value <= OCR_DPI_MAX:
        logger.warning(
            "Invalid RC_OCR_DPI=%r (%d-%d); rendering at the native resolution",
            raw, OCR_DPI_MIN, OCR_DPI_MAX,
        )
        return None
    return value
```

In the Platform's `ocr_options_kwargs`, replace the body (as EXT-3 left it):

```python
    timeout = extract_timeout_seconds() if timeout_seconds is None else int(timeout_seconds)
    page_timeout = _env_int("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, minimum=1)
    return {
        "engine": ocr_engine(),
        "dpi": _env_int("RC_OCR_DPI", DEFAULT_OCR_DPI, minimum=72),
        "workers": ocr_workers(),
        "max_ocr_pages": _env_int("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES),
        "time_budget_seconds": ocr_time_budget_seconds(timeout, page_timeout),
        "page_timeout_seconds": page_timeout,
        "language": tesseract_language(language),
    }
```

with:

```python
    timeout = extract_timeout_seconds() if timeout_seconds is None else int(timeout_seconds)
    page_timeout = _env_int("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, minimum=1)
    options: dict[str, Any] = {
        "engine": ocr_engine(),
        "workers": ocr_workers(),
        "max_ocr_pages": _env_int("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES),
        "time_budget_seconds": ocr_time_budget_seconds(timeout, page_timeout),
        "page_timeout_seconds": page_timeout,
        "language": tesseract_language(language),
    }
    dpi = ocr_dpi()
    if dpi is not None:
        options["dpi"] = dpi
    return options
```

In `KnovasConnector/docs/configuration.md`, replace:

```
| `RC_OCR_DPI` | `300` | Render dpi ceiling; the library never upsamples a lower-resolution scan. |
```

with:

```
| `RC_OCR_DPI` | (unset) | Unset: no `dpi` is passed and the library renders each page at its native resolution, at most 300 dpi, never upsampled (a 150 dpi fax stays 150 dpi). Set (30–1200): every page is rendered at exactly this resolution, so a lower-resolution scan is upsampled. Any other value logs a warning and counts as unset. |
```

In `KnovasPlatform/components/docbridge_integration/README.md`, replace:

```
| `RC_OCR_DPI` | `300` | Render dpi ceiling; the library never upsamples a lower-resolution scan. |
```

with:

```
| `RC_OCR_DPI` | (unset) | Unset: no `dpi` is passed — native resolution, at most 300 dpi, never upsampled. Set (30–1200): every page at exactly this resolution (a lower-resolution scan is upsampled). Any other value logs a warning and counts as unset. |
```

Append to the CHANGELOG subsection:

```markdown
- **`RC_OCR_DPI` is unset by default** (Connector and Platform): no `dpi` is passed and the library renders each page at its native resolution (≤ 300 dpi, never upsampled; the forced 300 dpi upsampled faxes). A set value must be 30–1200, else one warning and it counts as unset.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/unit/test_document_text.py`

Expected: PASS.

Run: `pf-pytest tests/test_knovas_extract_upload.py`

Expected: PASS. Note: `test_text_mode_and_ocr_options_are_sent_when_accepted` in both suites sets `RC_OCR_DPI=200` and still sees 200.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/document_text.py KnovasConnector/tests/unit/test_document_text.py \
  KnovasConnector/docs/configuration.md KnovasConnector/CHANGELOG.md \
  KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py \
  KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py \
  KnovasPlatform/components/docbridge_integration/README.md
git commit -F - <<'EOF'
rc+platform: no forced 300 dpi; RC_OCR_DPI only when set (30-1200)

Both components passed dpi=300, which overrides the library's rule
(native resolution, at most 300, never upsampled) and upsampled
low-resolution scans: CER 0.145 instead of 0.028 for a 150 dpi fax. Unset
now sends no dpi. A set value must be 30-1200, the range OcrOptions
accepts; anything else logs one warning and counts as unset. The docs
called the setting a ceiling, which it never was.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-5: Sentences for every input (`RC_SENTENCE_EMIT_MAX_BYTES` default 0)

**Files:**
- Modify: `KnovasConnector/src/sync/document_text.py` (module docstring, `DEFAULT_SENTENCE_EMIT_MAX_BYTES`, `sentence_emit_max_bytes`, `_extract_bytes`)
- Modify: `KnovasConnector/docs/configuration.md`, `KnovasConnector/CHANGELOG.md`
- Test: `KnovasConnector/tests/unit/test_document_text.py`

**Interfaces:**
- Consumes: —
- Produces: `sync.document_text.sentence_emit_max_bytes() -> int` (0 = no gate) and `DEFAULT_SENTENCE_EMIT_MAX_BYTES = 0`.

- [ ] **Step 1: Write the failing tests**

In `KnovasConnector/tests/unit/test_document_text.py`, replace the whole function `test_sentence_emit_max_bytes_default` with:

```python
def test_sentence_emit_max_bytes_default(monkeypatch):
    from sync.document_text import DEFAULT_SENTENCE_EMIT_MAX_BYTES, sentence_emit_max_bytes

    monkeypatch.delenv("RC_SENTENCE_EMIT_MAX_BYTES", raising=False)
    assert DEFAULT_SENTENCE_EMIT_MAX_BYTES == 0, "no gate by default (spec E4)"
    assert sentence_emit_max_bytes() == 0


def test_sentences_are_emitted_for_large_inputs_by_default(monkeypatch):
    """The 2 MiB gate on raw file size switched off the citations -- and every
    part's page number -- of most multi-page scans. It guarded the quadratic
    line counting fixed in knovas-extract 0.3; a positive value restores it."""
    from sync import document_text

    seen = {}

    def extract_stub(raw, **kwargs):
        seen.update(kwargs)
        raise document_text.UnsupportedFormatError("stub")

    monkeypatch.setattr(document_text, "extract", extract_stub)
    large = b"x" * (3 * 1024 * 1024)
    monkeypatch.delenv("RC_SENTENCE_EMIT_MAX_BYTES", raising=False)
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(large, ".txt")
    assert seen["emit_sentences"] is True
    monkeypatch.setenv("RC_SENTENCE_EMIT_MAX_BYTES", "2097152")
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(large, ".txt")
    assert seen["emit_sentences"] is False, "a positive value is the old gate"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rc-pytest tests/unit/test_document_text.py -k sentence`

Expected: FAIL: `2097152 == 0`, and `emit_sentences` is False for 3 MiB.

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/src/sync/document_text.py`, replace in the module docstring:

```
Sentence emission is skipped for inputs larger than
`RC_SENTENCE_EMIT_MAX_BYTES` (default 2 MiB). `split_sentences` degrades
badly on large, weakly-punctuated text — tariff tables and similar
dumps — where it can occupy the single sync worker for many minutes per
file and stall ingestion. Text extraction and upload are unaffected;
only sentence-level citations and context previews are dropped.
```

with:

```
Sentences are emitted for every input (spec E4). The size gate
`RC_SENTENCE_EMIT_MAX_BYTES` (default `0`: no gate) guarded against the
quadratic line counting in `split_sentences` that knovas-extract 0.3 fixed;
measured on raw file size it switched off the citations -- and with them
every part's `page_number` -- of most multi-page scans. A positive value
restores it: above that many raw bytes only the citations and context
previews are dropped, the text is still uploaded.
```

Replace:

```python
# Inputs above this size skip sentence emission. Override with
# RC_SENTENCE_EMIT_MAX_BYTES; 0 disables sentence emission entirely.
DEFAULT_SENTENCE_EMIT_MAX_BYTES = 2 * 1024 * 1024
```

with:

```python
# RC_SENTENCE_EMIT_MAX_BYTES: 0 (default) emits sentences for every input; a
# positive value skips sentence emission above that many raw bytes.
DEFAULT_SENTENCE_EMIT_MAX_BYTES = 0
```

Replace:

```python
def sentence_emit_max_bytes() -> int:
    """Size ceiling for sentence emission (see module docstring)."""
    return _env_int("RC_SENTENCE_EMIT_MAX_BYTES", DEFAULT_SENTENCE_EMIT_MAX_BYTES)
```

with:

```python
def sentence_emit_max_bytes() -> int:
    """`RC_SENTENCE_EMIT_MAX_BYTES`: 0 (default) is no gate; a positive value
    skips sentence emission above that many raw bytes (see module docstring)."""
    return _env_int("RC_SENTENCE_EMIT_MAX_BYTES", DEFAULT_SENTENCE_EMIT_MAX_BYTES)
```

In `_extract_bytes`, replace:

```python
    max_sentence_bytes = sentence_emit_max_bytes()
    emit_sentences = len(raw) <= max_sentence_bytes
```

with:

```python
    max_sentence_bytes = sentence_emit_max_bytes()
    emit_sentences = max_sentence_bytes <= 0 or len(raw) <= max_sentence_bytes
```

In `KnovasConnector/docs/configuration.md`, replace:

```
| `RC_SENTENCE_EMIT_MAX_BYTES` | `2097152` | Inputs above this skip sentence emission (citations and context previews), the text is still uploaded. |
```

with:

```
| `RC_SENTENCE_EMIT_MAX_BYTES` | `0` | `0`: sentence citations for every input. A positive value skips them (and the context previews) above that many raw bytes; the text is still uploaded. |
```

Append to the CHANGELOG subsection:

```markdown
- **`RC_SENTENCE_EMIT_MAX_BYTES` defaults to `0`** (no gate): sentence citations for every input, including multi-page scans, which the 2 MiB gate on raw size used to cut off. A positive value restores the gate.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/unit/test_document_text.py tests/unit/test_knovas_uploader.py`

Expected: PASS. `test_large_text_skips_sentences_but_keeps_text` still passes, because it sets a positive gate.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/document_text.py KnovasConnector/tests/unit/test_document_text.py \
  KnovasConnector/docs/configuration.md KnovasConnector/CHANGELOG.md
git commit -F - <<'EOF'
rc: sentences for every input, RC_SENTENCE_EMIT_MAX_BYTES defaults to 0

The 2 MiB gate on raw file size turned off sentence citations - and every
part's page_number - for most multi-page scans, which are large on disk
and small in text. It guarded the quadratic line counting that
knovas-extract 0.3 fixed; the library's sentence cap becomes fail-soft in
0.4.0a1. 0 now means no gate, a positive value restores it. The Platform
never had the gate.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-6: Page numbers from `content.pages` when there are no sentences (Connector + Platform chunker)

**Files:**
- Modify: `KnovasConnector/src/sync/chunking.py` (import, `_page_number_starts`, `_page_for_offset`, `iter_text_chunks_with_location`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_transmit/chunking.py` (same)
- Modify: `KnovasConnector/docs/configuration.md`, `KnovasConnector/CHANGELOG.md`
- Test: `KnovasConnector/tests/unit/test_page_markers.py`, `KnovasConnector/tests/unit/test_knovas_uploader.py`, `KnovasPlatform/components/docbridge_integration/tests/test_page_markers.py`

**Interfaces:**
- Consumes: `page_markers.line_start_offsets(text: str) -> list[int]` (exists in both components).
- Produces: `iter_text_chunks_with_location(...)` and `build_transmission_parts(...)` yield or set `page_number` from `pages` when `sentences` is empty or None. `sentence_number` stays None in that case.

- [ ] **Step 1: Write the failing tests**

In `KnovasConnector/tests/unit/test_page_markers.py`, add `import pytest` below `from __future__ import annotations`, and append:

```python
class TestDeclaredPageWithoutSentences:
    """Spec E4: without sentences (the size gate, a sentence cap) a part's
    page_number comes from content.pages under the same contract -- the
    page of its first character, join whitespace to the preceding page."""

    @pytest.mark.parametrize("page_texts", [
        ["Seite eins.", "Seite zwei.", "Seite drei."],
        ["Seite eins. " * 20, None, "Seite drei. " * 20, "Seite vier. " * 20],
        [None, None, "Seite drei. " * 10, "Seite vier. " * 10],
        ["Zeile eins\nZeile zwei\nZeile drei", "Seite zwei.\nNoch eine Zeile."],
    ])
    @pytest.mark.parametrize("part_max", [7, 13, 40, 120, 10_000])
    def test_pages_give_the_same_page_numbers_as_sentences(self, page_texts, part_max):
        from sync.chunking import iter_text_chunks_with_location

        text, pages, sentences = _pages_and_text(page_texts)
        with_sentences = list(iter_text_chunks_with_location(text, part_max, sentences=sentences, pages=pages))
        from_pages = list(iter_text_chunks_with_location(text, part_max, pages=pages))
        assert [(t, o) for t, _p, _s, o in from_pages] == [(t, o) for t, _p, _s, o in with_sentences]
        assert [p for _t, p, _s, _o in from_pages] == [p for _t, p, _s, _o in with_sentences]
        assert all(s is None for _t, _p, s, _o in from_pages)

    def test_three_pages_get_page_numbers_one_to_three(self):
        from sync.chunking import iter_text_chunks_with_location

        text, pages, _ = _pages_and_text(["Seite eins.", "Seite zwei.", "Seite drei."])
        parts = list(iter_text_chunks_with_location(text, text.index("Seite zwei."), pages=pages))
        assert [p for _t, p, _s, _o in parts] == [1, 2, 3]

    def test_an_empty_page_keeps_the_numbering(self):
        from sync.chunking import iter_text_chunks_with_location

        text, pages, _ = _pages_and_text(["Seite eins.", None, "Seite drei."])
        parts = list(iter_text_chunks_with_location(text, text.index("Seite drei."), pages=pages))
        assert [p for _t, p, _s, _o in parts] == [1, 3]

    def test_no_pages_no_page_number(self):
        from sync.chunking import iter_text_chunks_with_location

        assert [p for _t, p, _s, _o in iter_text_chunks_with_location("Ohne Seiten.", 5)] == [None, None, None]
```

Append to `KnovasConnector/tests/unit/test_knovas_uploader.py`:

```python
def test_parts_carry_page_numbers_without_sentences(mock_config, tmp_path):
    """Spec E4: a PDF extracted without sentences still gets every part's
    page number, from content.pages."""
    from knovas_extract.result import Page

    from sync.document_text import ExtractedDocument

    text = "Seite eins.\n\nSeite zwei.\n\nSeite drei."
    pages = [Page(index=i, text=t, line_start=1 + 2 * i, line_end=1 + 2 * i)
             for i, t in enumerate(("Seite eins.", "Seite zwei.", "Seite drei."))]
    doc = ExtractedDocument(text=text, sentences=None, pages=pages)
    pdf = tmp_path / "drei.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf")
    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req, patch(
        "sync.knovas_uploader.extract_document_guarded", return_value=doc
    ), patch("sync.knovas_uploader.write_context_sidecar", return_value=True):
        req.side_effect = [_ok_response() for _ in range(4)]
        result = uploader.upload_file(
            pdf, "akten/drei.pdf", {"ingestion": {"identifier_prefix": "corpus", "part_max_chars": 13}},
        )
    assert result.status == "ok" and result.parts == 3
    bodies = [c.kwargs["json_body"] for c in req.call_args_list[1:]]
    assert [b["page_number"] for b in bodies] == [1, 2, 3]
    assert all("sentence_number" not in b for b in bodies)
```

Append to `KnovasPlatform/components/docbridge_integration/tests/test_page_markers.py` (`pytest` and `iter_text_chunks_with_location` are already imported at module level):

```python
class TestDeclaredPageWithoutSentences:
    """Spec E4, mirror of the Connector: without sentences the part's
    page_number comes from content.pages, same contract."""

    @pytest.mark.parametrize("page_texts", [
        ["Seite eins.", "Seite zwei.", "Seite drei."],
        ["Seite eins. " * 20, None, "Seite drei. " * 20, "Seite vier. " * 20],
        [None, None, "Seite drei. " * 10, "Seite vier. " * 10],
        ["Zeile eins\nZeile zwei\nZeile drei", "Seite zwei.\nNoch eine Zeile."],
    ])
    @pytest.mark.parametrize("part_max", [7, 13, 40, 120, 10_000])
    def test_pages_give_the_same_page_numbers_as_sentences(self, page_texts, part_max):
        text, pages, sentences = _pages_and_text(page_texts)
        with_sentences = list(iter_text_chunks_with_location(text, part_max, sentences=sentences, pages=pages))
        from_pages = list(iter_text_chunks_with_location(text, part_max, pages=pages))
        assert [(t, o) for t, _p, _s, o in from_pages] == [(t, o) for t, _p, _s, o in with_sentences]
        assert [p for _t, p, _s, _o in from_pages] == [p for _t, p, _s, _o in with_sentences]
        assert all(s is None for _t, _p, s, _o in from_pages)

    def test_three_pages_get_page_numbers_one_to_three(self):
        text, pages, _ = _pages_and_text(["Seite eins.", "Seite zwei.", "Seite drei."])
        parts = list(iter_text_chunks_with_location(text, text.index("Seite zwei."), pages=pages))
        assert [p for _t, p, _s, _o in parts] == [1, 2, 3]

    def test_an_empty_page_keeps_the_numbering(self):
        text, pages, _ = _pages_and_text(["Seite eins.", None, "Seite drei."])
        parts = list(iter_text_chunks_with_location(text, text.index("Seite drei."), pages=pages))
        assert [p for _t, p, _s, _o in parts] == [1, 3]

    def test_no_pages_no_page_number(self):
        assert [p for _t, p, _s, _o in iter_text_chunks_with_location("Ohne Seiten.", 5)] == [None, None, None]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rc-pytest tests/unit/test_page_markers.py tests/unit/test_knovas_uploader.py::test_parts_carry_page_numbers_without_sentences`

Expected: FAIL. The page numbers are `[None, None, None]` and not `[1, 2, 3]`; the equivalence cases fail on `None != 1`. `test_no_pages_no_page_number` passes already.

Run: `pf-pytest tests/test_page_markers.py`

Expected: FAIL for the same reason.

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/src/sync/chunking.py`, replace:

```python
from sync.page_markers import (
    PageStart,
    apply_page_markers,
    marker_count_inside,
    markers_inside,
    text_page_starts,
)
```

with:

```python
from sync.page_markers import (
    PageStart,
    apply_page_markers,
    line_start_offsets,
    marker_count_inside,
    markers_inside,
    text_page_starts,
)
```

Directly after the function `_location_for_offset`, insert:

```python
def _page_number_starts(pages: Optional[Sequence[Page]], text: str) -> Tuple[List[int], List[int]]:
    """Ascending first-character offsets of the TEXT pages, and their numbers.

    The source of ``page_number`` when a document has no sentences (spec E4:
    the size gate, a sentence cap, an extractor without ``[sentences]``).
    Same contract as ``_location_for_offset`` (``sync.page_markers``): a
    part's page is the page of its first character, and the join whitespace
    between two pages belongs to the preceding page -- a page's lines run
    from its ``line_start`` to the next text page's, so its ``line_end``
    adds nothing. Empty pages carry no ``line_start`` and are skipped; the
    next text page keeps its own number (``index + 1``).
    """
    if not pages:
        return [], []
    line_offsets = line_start_offsets(text)
    entries: List[Tuple[int, int]] = []
    for page in pages:
        if not page.text or page.line_start is None:
            continue
        line = int(page.line_start)
        if 1 <= line <= len(line_offsets):
            entries.append((line_offsets[line - 1], int(page.index) + 1))
    entries.sort()
    return [offset for offset, _ in entries], [number for _, number in entries]


def _page_for_offset(starts: Sequence[int], numbers: Sequence[int], offset: int) -> Optional[int]:
    """The number of the text page holding ``offset``; before the first text
    page (leading whitespace) the first one, as the first sentence is in
    ``_location_for_offset``."""
    if not starts:
        return None
    idx = bisect.bisect_right(starts, offset) - 1
    return numbers[max(idx, 0)]
```

In `iter_text_chunks_with_location`, replace in the docstring:

```
    `sentences` — `content.sentences` from knovas-extract (char offsets refer to
    `content.text`). When provided, each chunk's location uses binary search on
    `Sentence.char_start` for `page_number` and `index + 1` as `sentence_number`.
```

with:

```
    `sentences` — `content.sentences` from knovas-extract (char offsets refer to
    `content.text`). When provided, each chunk's location uses binary search on
    `Sentence.char_start` for `page_number` and `index + 1` as `sentence_number`.
    Without sentences `page_number` comes from `pages` (spec E4,
    `_page_number_starts`) and `sentence_number` is None.
```

Replace:

```python
    starts = [s.char_start for s in sentences] if sentences else []
    page_starts: list[PageStart] = text_page_starts(pages, text) if (page_markers and pages) else []
```

with:

```python
    starts = [s.char_start for s in sentences] if sentences else []
    page_offsets, page_numbers = ([], []) if sentences else _page_number_starts(pages, text)
    page_starts: list[PageStart] = text_page_starts(pages, text) if (page_markers and pages) else []
```

Replace:

```python
        if sentences:
            page_number, sentence_number = _location_for_offset(starts, sentences, start)
        else:
            page_number, sentence_number = None, None
```

with:

```python
        if sentences:
            page_number, sentence_number = _location_for_offset(starts, sentences, start)
        else:
            page_number, sentence_number = _page_for_offset(page_offsets, page_numbers, start), None
```

In `KnovasPlatform/components/docbridge_integration/src/knovas_transmit/chunking.py`, make the same changes.
- Add `line_start_offsets,` to the `from knovas_transmit.page_markers import (...)` list, after `apply_page_markers,`.
- Insert the two functions above directly after `_location_for_offset`. Use the same code, with the docstring reference written ``knovas_transmit.page_markers`` instead of ``sync.page_markers``.
- Replace the two `iter_text_chunks_with_location` blocks quoted above. They are identical in the Platform file.
- Replace its docstring paragraph:

```
    ``sentences`` — knovas-extract ``Sentence`` objects (char offsets into
    ``content.text``). When provided, each chunk's location uses binary search
    on ``Sentence.char_start`` for ``page_number`` and ``index + 1`` as
    ``sentence_number``.
```

with:

```
    ``sentences`` — knovas-extract ``Sentence`` objects (char offsets into
    ``content.text``). When provided, each chunk's location uses binary search
    on ``Sentence.char_start`` for ``page_number`` and ``index + 1`` as
    ``sentence_number``. Without sentences ``page_number`` comes from
    ``pages`` (spec E4, ``_page_number_starts``) and ``sentence_number`` is
    None.
```

In `KnovasConnector/docs/configuration.md`, replace:

```
Each chunk carries a `page_number` (PDFs only) and a `sentence_number` derived from `content.sentences` — every sentence has an exact `char_start` offset into `content.text`, guaranteed by a dispatcher post-condition.
```

with:

```
Each chunk carries a `page_number` (PDFs only) and a `sentence_number` derived from `content.sentences` — every sentence has an exact `char_start` offset into `content.text`, guaranteed by a dispatcher post-condition. Without sentences the `page_number` comes from `content.pages` (the page of the chunk's first character) and there is no `sentence_number`.
```

Append to the CHANGELOG subsection:

```markdown
- Without sentences, every part's `page_number` comes from `content.pages` (the page of its first character, as with sentences), so page numbers no longer depend on sentence splitting (Connector and Platform chunker).
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/unit/test_page_markers.py tests/unit/test_chunking_location.py tests/unit/test_chunking.py tests/unit/test_section_pages.py tests/unit/test_knovas_uploader.py tests/unit/test_table_payload.py`

Expected: PASS.

Run: `pf-pytest tests/test_page_markers.py tests/test_knovas_extract_upload.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/chunking.py KnovasConnector/tests/unit/test_page_markers.py \
  KnovasConnector/tests/unit/test_knovas_uploader.py KnovasConnector/docs/configuration.md KnovasConnector/CHANGELOG.md \
  KnovasPlatform/components/docbridge_integration/src/knovas_transmit/chunking.py \
  KnovasPlatform/components/docbridge_integration/tests/test_page_markers.py
git commit -F - <<'EOF'
rc+platform: page numbers from content.pages when there are no sentences

A part's page_number came only from the sentence at its first character.
Without sentences (the old size gate, a sentence cap) every part of a scan
lost its page. The chunker now derives it from Page.line_start under the
same contract: the page of the first character, join whitespace to the
preceding page, empty pages skipped. With sentences nothing changes; the
tests check that both paths give the same numbers.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-7: OCR settings validated; a setting the library refuses is a retryable configuration error

**Files:**
- Modify: `KnovasConnector/src/sync/document_text.py`. New `CONFIG_INVALID_PREFIX`, `_TESSERACT_LANG_RE`, `_SETTING_FOR_OPTION_FIELD`, `_OPTION_FIELD_RE`, `config_invalid_error`, `_env_int_at_least`. Changed: `_NEVER_UNCONVERTIBLE_PREFIXES`, `tesseract_language`, `ocr_options_kwargs`, `_pdf_extract_kwargs`.
- Modify: `KnovasConnector/src/sync/sync_executor.py` (import, `_is_configuration_error`, `record_upload_outcome`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py` (`_TESSERACT_LANG_RE`, `_env_int_at_least`, `tesseract_language`, `ocr_options_kwargs`)
- Modify: `KnovasConnector/docs/configuration.md`, `KnovasConnector/docs/operations.md`, `KnovasPlatform/components/docbridge_integration/README.md`, `KnovasConnector/CHANGELOG.md`
- Test: `KnovasConnector/tests/unit/test_document_text.py`, `KnovasConnector/tests/unit/test_sync_executor_partial.py`, `KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py`

**Interfaces:**
- Consumes: EXT-4 `_non_negative_int`, `ocr_options_kwargs` as EXT-4 left it.
- Produces:
  - `sync.document_text.CONFIG_INVALID_PREFIX = "extraction configuration invalid"`
  - `sync.document_text.config_invalid_error(exc: ValueError) -> ConversionError`, with message `f"{CONFIG_INVALID_PREFIX}: {setting}"`
  - `sync.document_text.tesseract_language() -> str` (validated)
  - `sync.document_text._env_int_at_least(name: str, default: int, minimum: int) -> int`
  - `sync.sync_executor._is_configuration_error(error: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `KnovasConnector/tests/unit/test_document_text.py`:

```python
# --- OCR settings validated (spec E5) ----------------------------------------


def test_tesseract_language_is_validated(monkeypatch, caplog):
    import logging

    from sync.document_text import tesseract_language

    monkeypatch.delenv("RC_TESSERACT_LANG", raising=False)
    assert tesseract_language() == "deu+eng"
    for good in ("deu", "deu+eng", "deu+fra+ita", "chi_sim+eng", "osd"):
        monkeypatch.setenv("RC_TESSERACT_LANG", good)
        assert tesseract_language() == good
    # The same values scripts/lib/test_rc_extraction_settings.sh refuses.
    for bad in ("deu eng", "deu,eng", "deu+", "+eng", "deu++eng", "../deu", "deu/eng", "dé"):
        monkeypatch.setenv("RC_TESSERACT_LANG", bad)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="sync.document_text"):
            assert tesseract_language() == "deu+eng", bad
        assert len(caplog.records) == 1 and "RC_TESSERACT_LANG" in caplog.records[0].getMessage(), bad


def test_ocr_page_timeout_and_page_cap_must_be_at_least_one(monkeypatch, caplog):
    import logging

    from sync.document_text import (
        DEFAULT_OCR_MAX_PAGES,
        DEFAULT_OCR_PAGE_TIMEOUT_SECONDS,
        ocr_options_kwargs,
    )

    monkeypatch.setenv("RC_OCR_PAGE_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("RC_OCR_MAX_PAGES", "none")
    with caplog.at_level(logging.WARNING, logger="sync.document_text"):
        opts = ocr_options_kwargs(300)
    assert opts["page_timeout_seconds"] == DEFAULT_OCR_PAGE_TIMEOUT_SECONDS
    assert opts["max_ocr_pages"] == DEFAULT_OCR_MAX_PAGES
    names = " ".join(r.getMessage() for r in caplog.records)
    assert "RC_OCR_PAGE_TIMEOUT_SECONDS" in names and "RC_OCR_MAX_PAGES" in names
    monkeypatch.setenv("RC_OCR_PAGE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("RC_OCR_MAX_PAGES", "1")
    opts = ocr_options_kwargs(300)
    assert (opts["page_timeout_seconds"], opts["max_ocr_pages"]) == (1, 1)


@pytest.mark.parametrize("message, setting", [
    ("OcrOptions.language must be a Tesseract language string like 'deu+eng'", "RC_TESSERACT_LANG"),
    ("OcrOptions.dpi must be between 30 and 1200", "RC_OCR_DPI"),
    ("OcrOptions.engine must be one of auto|tesserocr|cli|mupdf", "RC_OCR_ENGINE"),
    ("OcrOptions.workers must be >= 1", "RC_OCR_WORKERS"),
    ("Limits.max_ocr_pages must be >= 1", "RC_OCR_MAX_PAGES"),
    ("something the Connector does not know", "OCR options"),
])
def test_a_refused_ocr_setting_is_a_retryable_configuration_error(monkeypatch, message, setting):
    """The library still refuses the options: never "corrupt .pdf" (that
    parked every PDF for good), but a message naming the setting."""
    from sync import document_text

    class RefusingOcrOptions:
        def __init__(self, **kwargs):
            raise ValueError(message)

    extract_stub, seen = _signature_stub(ocr_options=True, text_mode=True, limits=True)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", RefusingOcrOptions)
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    with pytest.raises(document_text.ConversionError) as exc:
        document_text._extract_bytes(b"%PDF-1.4 stub", ".pdf")
    assert str(exc.value) == f"{document_text.CONFIG_INVALID_PREFIX}: {setting}"
    assert is_unconvertible_error(str(exc.value)) is False
    assert seen == {}, "extract() is never called with options the library refused"


def test_the_child_reports_a_refused_setting_as_configuration_not_corrupt(tmp_path, monkeypatch):
    import queue as queue_mod

    from sync import document_text

    class RefusingOcrOptions:
        def __init__(self, **kwargs):
            raise ValueError("OcrOptions.language must be a Tesseract language string like 'deu+eng'")

    extract_stub, _seen = _signature_stub(ocr_options=True, text_mode=True, limits=True)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.setattr(document_text, "OcrOptions", RefusingOcrOptions)
    # nice + RLIMIT_AS would hit the test process itself
    monkeypatch.setattr(document_text, "_apply_child_limits", lambda: None)
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    out: queue_mod.Queue = queue_mod.Queue()
    document_text._extract_child(str(pdf), out)
    messages = []
    while not out.empty():
        messages.append(out.get_nowait())
    assert messages[-1] == ("conversion", "extraction configuration invalid: RC_TESSERACT_LANG"), messages
```

In `KnovasConnector/tests/unit/test_sync_executor_partial.py`, add this method to `class TestRcRecordingMechanism`:

```python
    def test_a_refused_ocr_setting_never_uses_up_the_retries(self, state):
        """Spec E5: the library refused the Connector's OCR settings -- no
        PDF is at fault. Counting it would record every PDF partial after
        three cycles and backfill it without OCR."""
        from sync.document_text import CONFIG_INVALID_PREFIX
        from sync.sync_executor import MAX_EXTRACT_RETRIES, record_upload_outcome

        up = _upload("error", error=f"{CONFIG_INVALID_PREFIX}: RC_TESSERACT_LANG")
        outcomes = [record_upload_outcome(state, up.relative_path, "2026-10-01T00:00:00Z", 1234, up, "incremental")
                    for _ in range(MAX_EXTRACT_RETRIES + 2)]
        assert outcomes == ["retry"] * (MAX_EXTRACT_RETRIES + 2)
        assert state.retry_count(up.relative_path) == 0
        assert up.relative_path not in state.partial_paths()
```

In the same file, extend the parametrize list of `TestOcrBudgetMessagesAreNotUnconvertible.test_not_unconvertible`. Replace:

```python
    @pytest.mark.parametrize("msg", [
        "ocr budget exceeded: partial 12/40 pages",
        "extraction timeout after 300s (child killed)",
        "extractor died (exit -9)",
    ])
```

with:

```python
    @pytest.mark.parametrize("msg", [
        "ocr budget exceeded: partial 12/40 pages",
        "extraction timeout after 300s (child killed)",
        "extractor died (exit -9)",
        "extraction configuration invalid: RC_TESSERACT_LANG",
    ])
```

Append to `KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py`:

```python
def test_tesseract_language_is_validated(monkeypatch, caplog):
    """An invalid language made OcrOptions refuse every PDF upload, which then
    carried only its path line. The Connector's rule (spec E5)."""
    assert m.tesseract_language("deu+fra") == "deu+fra", "the config value"
    monkeypatch.setenv("RC_TESSERACT_LANG", "deu+ita")
    assert m.tesseract_language("deu+fra") == "deu+ita", "the env value wins"
    monkeypatch.setenv("RC_TESSERACT_LANG", "deu ita")
    with caplog.at_level(logging.WARNING, logger="knovas_extract_upload"):
        assert m.tesseract_language("deu+fra") == "deu+fra", "an invalid env value is skipped"
        assert m.tesseract_language("deu fra") == "deu+eng", "an invalid config value too"
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "RC_TESSERACT_LANG" in messages and "advanced.extraction.ocr_language" in messages


def test_ocr_page_timeout_and_page_cap_must_be_positive(monkeypatch, caplog):
    monkeypatch.setenv("RC_OCR_PAGE_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("RC_OCR_MAX_PAGES", "-5")
    with caplog.at_level(logging.WARNING, logger="knovas_extract_upload"):
        opts = m.ocr_options_kwargs()
    assert opts["page_timeout_seconds"] == 30 and opts["max_ocr_pages"] == 50
    names = " ".join(r.getMessage() for r in caplog.records)
    assert "RC_OCR_PAGE_TIMEOUT_SECONDS" in names and "RC_OCR_MAX_PAGES" in names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rc-pytest tests/unit/test_document_text.py -k "tesseract or page_cap or refused" tests/unit/test_sync_executor_partial.py`

Expected: FAIL.
- `tesseract_language()` returns `deu eng` unchanged.
- The page timeout `0` is clamped to 1 without a warning.
- `ValueError` escapes `_extract_bytes` instead of a `ConversionError`.
- The child reports `('conversion', "corrupt .pdf: OcrOptions.language …")`.
- `ImportError: cannot import name 'CONFIG_INVALID_PREFIX'`.

Run: `pf-pytest tests/test_knovas_extract_upload.py -k "tesseract or page_cap"`

Expected: FAIL: `deu ita` is accepted, and `0` / `-5` are clamped.

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/src/sync/document_text.py`, replace:

```python
EXTRACT_TIMEOUT_ERROR_PREFIX = "extraction timeout"
```

with:

```python
EXTRACT_TIMEOUT_ERROR_PREFIX = "extraction timeout"

#: Prefix of the ConversionError raised when the library refuses the
#: Connector's own OCR settings (``ValueError`` from ``OcrOptions`` /
#: ``Limits``, spec E5). No document is at fault: ``is_unconvertible_error``
#: never matches it and the executor never counts it toward the extraction
#: retries. It used to surface as "corrupt .pdf" and park every PDF for good.
CONFIG_INVALID_PREFIX = "extraction configuration invalid"
```

Replace:

```python
DEFAULT_TESSERACT_LANG = "deu+eng"
```

with:

```python
DEFAULT_TESSERACT_LANG = "deu+eng"
#: RC_TESSERACT_LANG: language packs joined by "+" (spec E5; doctor.sh applies
#: the same pattern). OcrOptions refuses spaces, slashes and NUL.
_TESSERACT_LANG_RE = re.compile(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*")
```

Replace:

```python
#: Error prefixes that are retryable whatever else the message says: the
#: RC's own wall-clock kill, a killed child (OOM / RLIMIT_AS) and an OCR
#: budget trip that leaked out of an older library as an error.
_NEVER_UNCONVERTIBLE_PREFIXES = (
    EXTRACT_TIMEOUT_ERROR_PREFIX,
    "extractor died",
    "ocr budget exceeded",
    "resource limit exceeded: ocr",
)
```

with:

```python
#: Error prefixes that are retryable whatever else the message says: the
#: RC's own wall-clock kill, OCR settings the library refused, a killed
#: child (OOM / RLIMIT_AS) and an OCR budget trip that leaked out of an
#: older library as an error.
_NEVER_UNCONVERTIBLE_PREFIXES = (
    EXTRACT_TIMEOUT_ERROR_PREFIX,
    CONFIG_INVALID_PREFIX,
    "extractor died",
    "ocr budget exceeded",
    "resource limit exceeded: ocr",
)
```

Directly after the class `ConversionError` (after its `__init__`), insert:

```python
#: The library's option field -> the Connector setting it is built from.
_SETTING_FOR_OPTION_FIELD = {
    "engine": "RC_OCR_ENGINE",
    "language": "RC_TESSERACT_LANG",
    "dpi": "RC_OCR_DPI",
    "workers": "RC_OCR_WORKERS",
    "max_ocr_workers": "RC_OCR_WORKERS",
    "max_ocr_pages": "RC_OCR_MAX_PAGES",
    "ocr_time_budget_seconds": "RC_OCR_TIME_BUDGET_SECONDS",
    "ocr_page_timeout_seconds": "RC_OCR_PAGE_TIMEOUT_SECONDS",
}
_OPTION_FIELD_RE = re.compile(r"\b(?:OcrOptions|Limits)\.([a-z_]+)")


def config_invalid_error(exc: ValueError) -> ConversionError:
    """The retryable error for OCR settings the library refused (spec E5).

    Names the setting when the library's message names the field
    (``OcrOptions.language must be …``), else "OCR options"; never the value
    -- the message is logged and stored in the sync state.
    """
    match = _OPTION_FIELD_RE.search(str(exc))
    setting = _SETTING_FOR_OPTION_FIELD.get(match.group(1), "OCR options") if match else "OCR options"
    return ConversionError(f"{CONFIG_INVALID_PREFIX}: {setting}", extension=".pdf")
```

Directly after the function `_non_negative_int` (added in EXT-4), insert:

```python
def _env_int_at_least(name: str, default: int, minimum: int) -> int:
    """A whole-number setting of at least ``minimum`` (spec E5). Anything else
    -- not ASCII digits, or below the minimum -- logs one warning naming the
    setting and uses the default; ``_env_int(minimum=)`` would clamp it
    silently (a page timeout of 0 became 1 s and failed every page)."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    value = _non_negative_int(raw)
    if value is None or value < minimum:
        logger.warning(
            "Invalid %s=%r (a whole number of at least %d); using default %d",
            name, raw, minimum, default,
        )
        return default
    return value
```

Replace:

```python
def tesseract_language() -> str:
    raw = (os.environ.get("RC_TESSERACT_LANG") or "").strip()
    return raw or DEFAULT_TESSERACT_LANG
```

with:

```python
def tesseract_language() -> str:
    """`RC_TESSERACT_LANG` (default `deu+eng`): language packs joined by `+`.
    Anything else (`deu eng`, `deu,eng`) logs one warning naming the setting
    and uses the default -- `OcrOptions` would refuse it (spec E5)."""
    raw = (os.environ.get("RC_TESSERACT_LANG") or "").strip()
    if not raw:
        return DEFAULT_TESSERACT_LANG
    if _TESSERACT_LANG_RE.fullmatch(raw):
        return raw
    logger.warning("Invalid RC_TESSERACT_LANG=%r; using %s", raw, DEFAULT_TESSERACT_LANG)
    return DEFAULT_TESSERACT_LANG
```

In `ocr_options_kwargs`, replace:

```python
    page_timeout = _env_int("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, minimum=1)
```

with:

```python
    page_timeout = _env_int_at_least("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, 1)
```

and replace:

```python
        "max_ocr_pages": _env_int("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES),
```

with:

```python
        "max_ocr_pages": _env_int_at_least("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES, 1),
```

In `_pdf_extract_kwargs`, replace:

```python
        cache = ocr_cache_for_document(document_key)
        ocr_kwargs = ocr_options_kwargs(timeout_seconds)
        options = build_ocr_options({**ocr_kwargs, "cache": cache})
        if options is not None:
            kwargs["ocr"] = options
        if extract_accepts("limits"):
            limits = build_ocr_limits(ocr_kwargs)
            if limits is not None:
                kwargs["limits"] = limits
```

with:

```python
        cache = ocr_cache_for_document(document_key)
        ocr_kwargs = ocr_options_kwargs(timeout_seconds)
        try:
            options = build_ocr_options({**ocr_kwargs, "cache": cache})
            limits = build_ocr_limits(ocr_kwargs) if extract_accepts("limits") else None
        except ValueError as exc:
            # The library refused the Connector's own settings: no document
            # is at fault (spec E5). Reported as a configuration error, never
            # as "corrupt .pdf", which parked every PDF for good.
            close = getattr(cache, "close", None)
            if callable(close):
                close()
            raise config_invalid_error(exc) from exc
        if options is not None:
            kwargs["ocr"] = options
        if limits is not None:
            kwargs["limits"] = limits
```

In `KnovasConnector/src/sync/sync_executor.py`, replace:

```python
from sync.document_text import (
    DEFAULT_INCLUDE_GLOBS,
    is_syncable_extension,
    is_unconvertible_error,
)
```

with:

```python
from sync.document_text import (
    CONFIG_INVALID_PREFIX,
    DEFAULT_INCLUDE_GLOBS,
    is_syncable_extension,
    is_unconvertible_error,
)
```

Directly after the function `_is_server_side_error`, insert:

```python
def _is_configuration_error(error: str) -> bool:
    """The library refused the Connector's own OCR settings (spec E5): no
    file is at fault, so the failure never counts toward the extraction
    retry cap -- after three cycles every PDF would otherwise be recorded
    partial and backfilled without OCR. Fixed by correcting the setting."""
    return error.lower().startswith(CONFIG_INVALID_PREFIX)
```

In `record_upload_outcome`, replace:

```python
    if _is_server_side_error(error):
        return "retry"
```

with:

```python
    if _is_server_side_error(error) or _is_configuration_error(error):
        return "retry"
```

In `KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py`, replace:

```python
DEFAULT_TESSERACT_LANG = "deu+eng"
```

with:

```python
DEFAULT_TESSERACT_LANG = "deu+eng"
#: Language packs joined by "+" (the Connector's rule, spec E5).
_TESSERACT_LANG_RE = re.compile(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*")
```

Directly after the Platform's `_non_negative_int`, insert:

```python
def _env_int_at_least(name: str, default: int, minimum: int) -> int:
    """A whole-number setting of at least ``minimum``; anything else logs one
    warning naming the setting and uses the default (the Connector's rule)."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    value = _non_negative_int(raw)
    if value is None or value < minimum:
        logger.warning(
            "Invalid %s=%r (a whole number of at least %d); using default %d",
            name, raw, minimum, default,
        )
        return default
    return value
```

Replace:

```python
def tesseract_language(default: str = DEFAULT_TESSERACT_LANG) -> str:
    """``RC_TESSERACT_LANG`` when set, else the caller's language (the
    Platform config's ``advanced.extraction.ocr_language``)."""
    raw = (os.environ.get("RC_TESSERACT_LANG") or "").strip()
    return raw or (default or "").strip() or DEFAULT_TESSERACT_LANG
```

with:

```python
def tesseract_language(default: str = DEFAULT_TESSERACT_LANG) -> str:
    """``RC_TESSERACT_LANG`` when set and valid, else the caller's language
    (the Platform config's ``advanced.extraction.ocr_language``) when valid,
    else ``deu+eng``. Valid is language packs joined by ``+``; anything else
    logs one warning naming the setting and is skipped -- ``OcrOptions``
    would refuse it and the upload would carry no text (spec E5)."""
    raw = (os.environ.get("RC_TESSERACT_LANG") or "").strip()
    if raw:
        if _TESSERACT_LANG_RE.fullmatch(raw):
            return raw
        logger.warning("Invalid RC_TESSERACT_LANG=%r; using the configured language", raw)
    configured = (default or "").strip()
    if not configured:
        return DEFAULT_TESSERACT_LANG
    if _TESSERACT_LANG_RE.fullmatch(configured):
        return configured
    logger.warning(
        "Invalid advanced.extraction.ocr_language=%r; using %s", configured, DEFAULT_TESSERACT_LANG
    )
    return DEFAULT_TESSERACT_LANG
```

In the Platform's `ocr_options_kwargs`, replace:

```python
    page_timeout = _env_int("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, minimum=1)
```

with:

```python
    page_timeout = _env_int_at_least("RC_OCR_PAGE_TIMEOUT_SECONDS", DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, 1)
```

and replace:

```python
        "max_ocr_pages": _env_int("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES),
```

with:

```python
        "max_ocr_pages": _env_int_at_least("RC_OCR_MAX_PAGES", DEFAULT_OCR_MAX_PAGES, 1),
```

In `KnovasConnector/docs/configuration.md`, replace these three rows:

```
| `RC_TESSERACT_LANG` | `deu+eng` | Tesseract language packs (at most two; `deu+fra`, `deu+ita` per tenant). |
```

```
| `RC_OCR_MAX_PAGES` | `500` | OCR page budget per document. Beyond it the remaining image pages are skipped and COUNTED; the document is uploaded and recorded `partial` for the backfill. |
```

```
| `RC_OCR_PAGE_TIMEOUT_SECONDS` | `60` | Ceiling for one page's OCR. |
```

with:

```
| `RC_TESSERACT_LANG` | `deu+eng` | Tesseract language packs joined by `+` (at most two; `deu+fra`, `deu+ita` per tenant). Anything else (`deu eng`, `deu,eng`) logs a warning and uses `deu+eng`. |
```

```
| `RC_OCR_MAX_PAGES` | `500` | OCR page budget per document. Beyond it the remaining image pages are skipped and COUNTED; the document is uploaded and recorded `partial` for the backfill. At least 1; anything else logs a warning and uses 500. |
```

```
| `RC_OCR_PAGE_TIMEOUT_SECONDS` | `60` | Ceiling for one page's OCR. At least 1; anything else logs a warning and uses 60. |
```

In `KnovasConnector/docs/operations.md`, directly after the paragraph under `## Scanned PDFs (OCR)` that begins `Image pages of PDFs are ingested via Tesseract`, insert:

```
**OCR settings the library refuses.** The Connector checks `RC_TESSERACT_LANG` (language packs joined by `+`), `RC_OCR_DPI` (30–1200), `RC_OCR_PAGE_TIMEOUT_SECONDS` and `RC_OCR_MAX_PAGES` (at least 1) itself and replaces an invalid value by its default, with one warning naming the setting. Should the library still refuse the OCR options, every PDF fails with `extraction configuration invalid: <setting>`: retried every cycle, never counted toward `RC_EXTRACT_MAX_RETRIES`, never parked or recorded partial. Correct the setting in `knovas.env`, then `./scripts/setup.sh && ./scripts/start.sh`.
```

In `KnovasPlatform/components/docbridge_integration/README.md`, replace:

```
| `RC_TESSERACT_LANG` | config `advanced.extraction.ocr_language` (`deu+eng`) | Tesseract language packs; the env value wins over the config value. OCR itself is switched by `advanced.extraction.use_ocr` (default on). |
```

with:

```
| `RC_TESSERACT_LANG` | config `advanced.extraction.ocr_language` (`deu+eng`) | Tesseract language packs joined by `+`; the env value wins over the config value. A value of another shape logs a warning and is skipped (then the config value, then `deu+eng`). OCR itself is switched by `advanced.extraction.use_ocr` (default on). |
```

and append ` At least 1; anything else logs a warning and uses the default.` to the end of the Meaning cell of the `RC_OCR_MAX_PAGES` and `RC_OCR_PAGE_TIMEOUT_SECONDS` rows. The new rows are:

```
| `RC_OCR_MAX_PAGES` | `50` | OCR page budget per upload. Beyond it the remaining image pages are skipped and COUNTED; the document is uploaded and reported `partial`. At least 1; anything else logs a warning and uses the default. |
```

```
| `RC_OCR_PAGE_TIMEOUT_SECONDS` | `30` | Ceiling for one page's OCR. At least 1; anything else logs a warning and uses the default. |
```

Append to the CHANGELOG subsection:

```markdown
- **OCR settings validated**: `RC_TESSERACT_LANG` must be language packs joined by `+`; `RC_OCR_PAGE_TIMEOUT_SECONDS` and `RC_OCR_MAX_PAGES` at least 1 — else one warning and the default (no silent clamp). Settings the library still refuses fail every PDF with `extraction configuration invalid: <setting>`, retried every cycle without using up `RC_EXTRACT_MAX_RETRIES` (before: `corrupt .pdf`, every PDF skipped for good).
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/unit/test_document_text.py tests/unit/test_sync_executor_partial.py tests/unit/test_sync_executor.py tests/unit/test_backfill_partial_ocr.py`

Expected: PASS.

Run: `pf-pytest tests/test_knovas_extract_upload.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/document_text.py KnovasConnector/src/sync/sync_executor.py \
  KnovasConnector/tests/unit/test_document_text.py KnovasConnector/tests/unit/test_sync_executor_partial.py \
  KnovasConnector/docs/configuration.md KnovasConnector/docs/operations.md KnovasConnector/CHANGELOG.md \
  KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py \
  KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py \
  KnovasPlatform/components/docbridge_integration/README.md
git commit -F - <<'EOF'
rc+platform: validate OCR settings; a refused setting is a retryable config error

RC_TESSERACT_LANG="deu eng" made OcrOptions raise ValueError. The child
reported it as "corrupt .pdf", so every PDF was skipped for good. The
Connector now validates the language (packs joined by +), the page
timeout and the page cap (at least 1). An invalid value logs one warning
naming the setting and uses the default. If the library still refuses the
options, the error is "extraction configuration invalid: <setting>":
never unconvertible, and it never uses up the extraction retries. The
Platform validates the env and config language and the two limits the
same way.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-8: `doctor.sh` checks the Connector's OCR settings by the same rules

**Files:**
- Create: `scripts/lib/rc_extraction_settings.sh`
- Create: `scripts/lib/test_rc_extraction_settings.sh`
- Modify: `scripts/doctor.sh` (source the lib; warnings at the top of the Connector section)
- Modify: `.github/workflows/ci.yml` (one step next to the other shell contracts)
- Modify: `KnovasConnector/docs/operations.md`, `KnovasConnector/CHANGELOG.md`

**Interfaces:**
- Consumes: EXT-4 and EXT-7 rules (the same value tables). `read_env_var` from `KnovasPlatform/scripts/lib/read_env.sh`.
- Produces: `knovas_rc_extraction_problems ENV_FILE` prints one line per invalid setting and nothing when all are valid.

- [ ] **Step 1: Write the failing test**

Create `scripts/lib/test_rc_extraction_settings.sh`:

```bash
#!/usr/bin/env bash
# doctor.sh's check of the Connector OCR settings (spec E5): the rules of
# KnovasConnector/src/sync/document_text.py (tesseract_language, ocr_dpi,
# _env_int_at_least). The value tables here and in the Connector's
# tests/unit/test_document_text.py are the same.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=rc_extraction_settings.sh
source "$ROOT_DIR/scripts/lib/rc_extraction_settings.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
ENV_FILE="$WORKDIR/knovas.env"

problems_for() {
  printf '%s\n' "$@" > "$ENV_FILE"
  knovas_rc_extraction_problems "$ENV_FILE"
}

# --- Unset or valid: nothing to say -------------------------------------------
[[ -z "$(problems_for 'KNOVAS_API_URL=https://api.test:8443')" ]] || fail "unset settings are valid"
for line in 'RC_TESSERACT_LANG=deu' 'RC_TESSERACT_LANG=deu+eng+fra' 'RC_TESSERACT_LANG="chi_sim+eng"' \
            'RC_OCR_DPI=30' 'RC_OCR_DPI=1200' 'RC_OCR_DPI=0150' \
            'RC_OCR_PAGE_TIMEOUT_SECONDS=1' 'RC_OCR_MAX_PAGES=1' 'RC_OCR_MAX_PAGES=5000'; do
  [[ -z "$(problems_for "$line")" ]] || fail "valid: $line"
done

# --- Each rule names its setting and what the Connector uses instead ----------
check() {  # $1 line, $2 setting, $3 fallback text
  local out
  out="$(problems_for "$1")"
  [[ "$out" == "$2="* && "$out" == *"$3"* ]] || fail "$1 -> '$out'"
}
for bad in 'deu eng' 'deu,eng' 'deu+' '+eng' 'deu++eng' '../deu' 'deu/eng' 'dé'; do
  check "RC_TESSERACT_LANG=$bad" RC_TESSERACT_LANG "uses deu+eng"
done
for bad in 29 1201 0 -300 300dpi 3e2; do
  check "RC_OCR_DPI=$bad" RC_OCR_DPI "native resolution"
done
for bad in 0 -1 sixty; do
  check "RC_OCR_PAGE_TIMEOUT_SECONDS=$bad" RC_OCR_PAGE_TIMEOUT_SECONDS "uses 60"
done
for bad in 0 -5 many; do
  check "RC_OCR_MAX_PAGES=$bad" RC_OCR_MAX_PAGES "uses 500"
done

# --- One line per invalid setting -----------------------------------------------
[[ "$(problems_for 'RC_OCR_DPI=5' 'RC_OCR_MAX_PAGES=0' 'RC_TESSERACT_LANG=deu+eng' | grep -c .)" == 2 ]] \
  || fail "two invalid settings, two lines"

echo "OK: Connector extraction settings contract"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd $WT && bash scripts/lib/test_rc_extraction_settings.sh`

Expected: FAIL: `scripts/lib/rc_extraction_settings.sh: No such file or directory`.

- [ ] **Step 3: Write the implementation**

Create `scripts/lib/rc_extraction_settings.sh`:

```bash
#!/usr/bin/env bash
# The Knovas Connector's OCR settings in knovas.env, checked by the rules the
# Connector applies (KnovasConnector/src/sync/document_text.py, spec E5). The
# Connector replaces an invalid value by its default and logs one warning per
# document -- in a log nobody reads while the sync seems to work. Before that
# check existed, RC_TESSERACT_LANG="deu eng" skipped every PDF for good.
#
#   knovas_rc_extraction_problems ENV_FILE
#
# prints one line per invalid setting (the setting, what is wrong, what the
# Connector uses instead) and nothing when every setting is valid or unset.
# Numbers are ASCII digits only, the Connector's own rule.

_RC_SETTINGS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../KnovasPlatform/scripts/lib/read_env.sh
source "$_RC_SETTINGS_LIB_DIR/../../KnovasPlatform/scripts/lib/read_env.sh"

# $1 value, $2 minimum, $3 maximum (empty: none). True when $1 is a whole
# number in range.
_rc_whole_number_in_range() {
  local value="$1" min="$2" max="${3:-}" digits
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  digits="${value#"${value%%[!0]*}"}"   # leading zeros off: 08 is eight, not octal
  digits="${digits:-0}"
  if (( ${#digits} > 9 )); then         # far beyond every limit here; no overflow
    [[ -z "$max" ]]
    return
  fi
  (( digits >= min )) || return 1
  [[ -z "$max" ]] || (( digits <= max ))
}

knovas_rc_extraction_problems() {
  local env_file="$1" value
  local LC_ALL=C   # [A-Za-z] means ASCII letters, as in the Connector
  value="$(read_env_var RC_TESSERACT_LANG "" "$env_file")"
  if [[ -n "$value" && ! "$value" =~ ^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$ ]]; then
    echo "RC_TESSERACT_LANG=$value is not a list of language packs joined by + (like deu+eng); the Connector uses deu+eng"
  fi
  value="$(read_env_var RC_OCR_DPI "" "$env_file")"
  if [[ -n "$value" ]] && ! _rc_whole_number_in_range "$value" 30 1200; then
    echo "RC_OCR_DPI=$value is not a resolution from 30 to 1200; the Connector renders every page at its native resolution"
  fi
  value="$(read_env_var RC_OCR_PAGE_TIMEOUT_SECONDS "" "$env_file")"
  if [[ -n "$value" ]] && ! _rc_whole_number_in_range "$value" 1; then
    echo "RC_OCR_PAGE_TIMEOUT_SECONDS=$value is not a whole number of seconds of at least 1; the Connector uses 60"
  fi
  value="$(read_env_var RC_OCR_MAX_PAGES "" "$env_file")"
  if [[ -n "$value" ]] && ! _rc_whole_number_in_range "$value" 1; then
    echo "RC_OCR_MAX_PAGES=$value is not a page count of at least 1; the Connector uses 500"
  fi
}
```

In `scripts/doctor.sh`, replace:

```bash
# shellcheck source=lib/stack_identity.sh
source "$ROOT_DIR/scripts/lib/stack_identity.sh"
```

with:

```bash
# shellcheck source=lib/stack_identity.sh
source "$ROOT_DIR/scripts/lib/stack_identity.sh"
# shellcheck source=lib/rc_extraction_settings.sh
source "$ROOT_DIR/scripts/lib/rc_extraction_settings.sh"
```

In the Connector section, insert directly above the line `if [[ "$RC_UP" != true ]]; then` (the first statement after the comment `… Every section above passes while it stands still.`):

```bash
# OCR settings the Connector would replace by its default (spec E5): it logs
# one warning per document and goes on, which nobody sees. Read from
# knovas.env, so this is said whether or not the container runs.
while IFS= read -r problem; do
  [[ -n "$problem" ]] && warn "$problem"
done < <(knovas_rc_extraction_problems "$KNOVAS_ENV")
```

In `.github/workflows/ci.yml`, replace:

```yaml
      - name: Multi-stack identity contract
        working-directory: .
        run: bash scripts/lib/test_stack_identity.sh
```

with:

```yaml
      - name: Multi-stack identity contract
        working-directory: .
        run: bash scripts/lib/test_stack_identity.sh

      - name: Connector extraction settings contract
        working-directory: .
        run: bash scripts/lib/test_rc_extraction_settings.sh
```

In `KnovasConnector/docs/operations.md`, append to the paragraph `**OCR settings the library refuses.** …` (from EXT-7):

```
`./scripts/doctor.sh` checks the same rules from `knovas.env` and names each invalid setting in its section about the Connector.
```

Append to the CHANGELOG subsection:

```markdown
- `./scripts/doctor.sh` warns about OCR settings in `knovas.env` that the Connector would replace by its default (same rules, `scripts/lib/rc_extraction_settings.sh`).
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd $WT && bash scripts/lib/test_rc_extraction_settings.sh && bash -n scripts/doctor.sh && bash scripts/lib/test_stack_identity.sh`

Expected: `OK: Connector extraction settings contract`. `bash -n` reports no syntax error, and the identity contract still passes.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add scripts/lib/rc_extraction_settings.sh scripts/lib/test_rc_extraction_settings.sh scripts/doctor.sh \
  .github/workflows/ci.yml KnovasConnector/docs/operations.md KnovasConnector/CHANGELOG.md
git commit -F - <<'EOF'
ci: doctor.sh checks the Connector's OCR settings by the Connector's rules

An invalid OCR setting is now replaced by its default with one warning
per document in the Connector's log, where nobody looks. doctor.sh reads
the same settings from knovas.env and prints a WARN per invalid one: the
language list, 30-1200 dpi, page timeout and page cap of at least 1. The
check is a sourced library with a shell contract test in CI that uses the
same value table as the Python tests.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-9: Serialise the Connector routes that write configuration or scheduler state

**Files:**
- Modify: `KnovasConnector/src/sync/sync_config.py` (`import threading`, `_CONFIG_FILE_LOCK`, `load_sync_config`, `save_sync_config`)
- Modify: `KnovasConnector/src/sync/sync_scheduler.py` (`_body_file_lock`, `_control_lock`, `save_last_sync_body`, `run_one_time`, `start_continuous`, `stop_continuous`)
- Create: `KnovasConnector/tests/integration/test_sync_route_concurrency.py`
- Test: `KnovasConnector/tests/unit/test_sync_scheduler.py`

**Interfaces:**
- Consumes: —
- Produces: `sync.sync_config._CONFIG_FILE_LOCK` (`threading.RLock`), `sync.sync_scheduler._body_file_lock` and `sync.sync_scheduler._control_lock` (`threading.Lock`). Every function signature is unchanged.

Review result (routes in `KnovasConnector/src/routes/`):

| Route | Writes | Problem under concurrent requests | Fix |
|---|---|---|---|
| `GET/POST /sync/config` | `knovas_connector_sync.json` | GET seeds a missing file by check-then-write and can overwrite a concurrent POST; on Windows two `os.replace` onto one file can fail | `_CONFIG_FILE_LOCK` |
| `POST /sync`, `/sync/body`, `/sync/start` | `.rc-sync-last-request.json` | atomic per write, but two renames can race | `_body_file_lock` |
| `POST /sync/start`, `/sync/stop` | `_worker_thread`, `_stop_event`, status | a start right after the old worker released `_scheduler_lock` is reported `not_running` by the finishing stop | `_control_lock` |
| `POST /sync` (one-time run) | `_stop_event` | starting while a stop finishes | `_control_lock` around acquire + clear only (a stop can still interrupt the run) |
| `POST /sync/doc-fields/requeue` | SQLite rows; `_fields_requeued_since_scan` under `_doc_fields_lock` | already serialised | none |
| `/discover`, `/health`, `/metrics`, `/m365/preview` | nothing shared | — | none |

The re-extraction route of REX writes SQLite only, like `/sync/doc-fields/requeue`, and needs no new lock.

- [ ] **Step 1: Write the failing tests**

Create `KnovasConnector/tests/integration/test_sync_route_concurrency.py`:

```python
"""Concurrent writes through the Connector's routes (spec E6).

The image runs gunicorn's gthread worker with four request threads, so
POST /sync/config, /sync/body, /sync/start and /sync/stop can run at the
same time. What they write must come out as one complete document, and a
GET that seeds a missing config file must never undo a POST.
"""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest

SYNC_BODY = {
    "mode": "incremental",
    "sources": [{"path": ".", "recursive": True}],
    "filters": {"include_globs": ["**/*.md"], "exclude_globs": []},
    "ingestion": {"identifier_prefix": "rc-sync", "part_max_chars": 50000},
}


def _config(interval: int) -> dict:
    return {
        "schema_version": 1,
        "enabled": True,
        "mode": "continuous",
        "window": {"start_local": "00:00", "end_local": "23:59"},
        "rate_limit": {"max_ingestion_requests_per_minute": 30, "burst": 5},
        "scan_interval_seconds": interval,
        "pause_policy": "finish_current_unit_then_pause",
    }


@pytest.fixture
def app_and_config_path(tmp_watch_root, tmp_path, monkeypatch):
    config_path = tmp_path / "config" / "sync.json"
    monkeypatch.setenv("RC_SYNC_CONFIG_API_ENABLED", "true")
    monkeypatch.setenv("RC_SYNC_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state" / ".rc-sync-state.json"))
    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    from app import create_app

    application = create_app(skip_validation=True)
    application.config["TESTING"] = True
    with patch("auth.knovas_verify_client.get_verify_client") as verify:
        verify.return_value.verify_operator.return_value = (True, "c", None)
        yield application, config_path


def _run_together(*targets) -> None:
    threads = [threading.Thread(target=target) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)


def test_a_get_that_seeds_the_config_never_overwrites_a_concurrent_post(
    app_and_config_path, auth_headers, monkeypatch
):
    """GET /sync/config writes a default file when there is none. It checked,
    then wrote: a POST landing in between was overwritten by the default and
    the console's "Speichern und uebertragen" was lost."""
    import sync.sync_config as sync_config

    application, config_path = app_and_config_path
    seeding, posted = threading.Event(), threading.Event()
    real_seed = sync_config.seed_from_env

    def slow_seed():
        seeding.set()
        posted.wait(timeout=2)  # with the lock the POST cannot finish in between
        return real_seed()

    monkeypatch.setattr(sync_config, "seed_from_env", slow_seed)
    stored = _config(600)
    statuses: dict = {}

    def get_config_doc():
        with application.test_client() as client:
            statuses["get"] = client.get("/sync/config", headers=auth_headers).status_code

    def post_config_doc():
        seeding.wait(timeout=5)
        with application.test_client() as client:
            statuses["post"] = client.post("/sync/config", json=stored, headers=auth_headers).status_code
        posted.set()

    _run_together(get_config_doc, post_config_doc)
    assert statuses == {"get": 200, "post": 200}
    assert json.loads(config_path.read_text(encoding="utf-8")) == stored, "the posted config survives"


def test_two_concurrent_config_writes_leave_one_complete_document(app_and_config_path, auth_headers):
    application, config_path = app_and_config_path
    bodies = [_config(60), _config(900)]
    barrier = threading.Barrier(len(bodies))
    statuses: list = []

    def post(body):
        def run():
            with application.test_client() as client:
                barrier.wait(timeout=5)
                statuses.append(client.post("/sync/config", json=body, headers=auth_headers).status_code)
        return run

    _run_together(*(post(body) for body in bodies))
    assert statuses == [200, 200]
    assert json.loads(config_path.read_text(encoding="utf-8")) in bodies, "one complete document, never a mix"
    assert not list(config_path.parent.glob("*.tmp")), "no temporary file is left behind"


def test_two_concurrent_body_writes_leave_one_complete_body(app_and_config_path, auth_headers):
    from sync.sync_scheduler import load_last_sync_body

    application, _config_path = app_and_config_path
    bodies = [
        {**SYNC_BODY, "ingestion": {**SYNC_BODY["ingestion"], "identifier_prefix": prefix}}
        for prefix in ("alpha", "beta")
    ]
    barrier = threading.Barrier(len(bodies))
    statuses: list = []

    def post(body):
        def run():
            with application.test_client() as client:
                barrier.wait(timeout=5)
                statuses.append(client.post("/sync/body", json=body, headers=auth_headers).status_code)
        return run

    _run_together(*(post(body) for body in bodies))
    assert statuses == [200, 200]
    assert load_last_sync_body() in bodies
```

Append to `KnovasConnector/tests/unit/test_sync_scheduler.py`:

```python
def test_a_start_that_follows_a_stop_is_not_reported_stopped(monkeypatch):
    """E6: with gunicorn's gthread worker, POST /sync/stop (which joins the
    worker for up to 120 s) and POST /sync/start can run at the same time.
    A start that slipped in after the stopped worker released the scheduler
    lock had its "running" overwritten by the stop's "not_running" while its
    own worker ran; the console then offered to start a running sync."""
    import threading
    import time

    from sync import sync_scheduler

    def idle_worker(_ctx):
        while not sync_scheduler._stop_event.is_set():
            time.sleep(0.01)

    monkeypatch.setattr(sync_scheduler, "_continuous_worker", idle_worker)
    ctx = SyncRunContext(sync_body={}, sync_config={})
    assert sync_scheduler.start_continuous(ctx) == "running"

    started_again = threading.Event()
    stopper: dict = {}

    def start_again():
        sync_scheduler.start_continuous(ctx)
        started_again.set()

    starter = threading.Thread(target=start_again)
    real_set_status = sync_scheduler._set_status

    def set_status(status):
        # The stop has joined the old worker and is about to report
        # "not_running": a start arrives exactly now.
        if status == "not_running" and threading.get_ident() == stopper.get("ident"):
            starter.start()
            started_again.wait(timeout=0.5)
        real_set_status(status)

    monkeypatch.setattr(sync_scheduler, "_set_status", set_status)

    def stop():
        stopper["ident"] = threading.get_ident()
        sync_scheduler.stop_continuous()

    stop_thread = threading.Thread(target=stop)
    stop_thread.start()
    stop_thread.join(timeout=30)
    starter.join(timeout=30)
    try:
        assert sync_scheduler._worker_thread is not None and sync_scheduler._worker_thread.is_alive()
        assert sync_scheduler._current_status == "running"
    finally:
        sync_scheduler.stop_continuous()
        sync_scheduler._stop_event.clear()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rc-pytest tests/integration/test_sync_route_concurrency.py tests/unit/test_sync_scheduler.py::test_a_start_that_follows_a_stop_is_not_reported_stopped`

Expected:
- `test_a_get_that_seeds_the_config_never_overwrites_a_concurrent_post`: FAIL, the file holds the seeded default (interval 60), not the posted 600.
- `test_a_start_that_follows_a_stop_is_not_reported_stopped`: FAIL with `'not_running' == 'running'`.
- The two write tests already pass on Linux (each write is an atomic rename). On Windows they may fail with a 500 from a concurrent `os.replace`. They are regression guards; the seeding test is the deterministic red one.

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/src/sync/sync_config.py`, replace:

```python
import hashlib
import json
import logging
import os
import tempfile
```

with:

```python
import hashlib
import json
import logging
import os
import tempfile
import threading
```

Replace:

```python
SCHEMA_FILE = "knovas_connector_sync_config.schema.json"
```

with:

```python
SCHEMA_FILE = "knovas_connector_sync_config.schema.json"

#: Serialises writes of the sync config file (spec E6). gunicorn's gthread
#: worker runs requests concurrently: a GET /sync/config that seeds a missing
#: file could otherwise overwrite the config a concurrent POST just stored
#: (check, then write), and two renames onto one file must not race.
#: Re-entrant: the seeding path saves under it.
_CONFIG_FILE_LOCK = threading.RLock()
```

Replace:

```python
    p = Path(path or cfg.rc_sync_config_path)
    if not p.exists():
        doc = seed_from_env()
        save_sync_config(doc, path=str(p))
        return doc
    doc = json.loads(p.read_text(encoding="utf-8"))
```

with:

```python
    p = Path(path or cfg.rc_sync_config_path)
    if not p.exists():
        with _CONFIG_FILE_LOCK:
            if not p.exists():
                doc = seed_from_env()
                save_sync_config(doc, path=str(p))
                return doc
    doc = json.loads(p.read_text(encoding="utf-8"))
```

Replace the body of `save_sync_config` after the validation:

```python
    p = Path(path or get_config().rc_sync_config_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

with:

```python
    p = Path(path or get_config().rc_sync_config_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _CONFIG_FILE_LOCK:
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2)
            os.replace(tmp, p)
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
```

In `KnovasConnector/src/sync/sync_scheduler.py`, replace:

```python
_wake_event = threading.Event()
```

with:

```python
_wake_event = threading.Event()
#: Serialises writes of ``.rc-sync-last-request.json`` (spec E6). POST /sync,
#: /sync/body and /sync/start all store the body, and gunicorn's gthread
#: worker runs them at once: each write is an atomic rename; the lock keeps
#: two renames onto one file from racing (on Windows the loser fails with
#: PermissionError) and makes "the last request wins" an order.
_body_file_lock = threading.Lock()
#: Serialises POST /sync/start, /sync/stop and the start of a one-time run
#: (spec E6). A start that slipped in after a stopped worker released
#: ``_scheduler_lock`` had its "running" overwritten by the stop's
#: "not_running" while its own worker ran. A stop holds it while it joins the
#: worker (up to 120 s); a one-time run holds it only to start, so a stop can
#: still interrupt the run.
_control_lock = threading.Lock()
```

Replace the whole function `save_last_sync_body` with:

```python
def save_last_sync_body(body: dict[str, Any]) -> None:
    p = _last_sync_body_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _body_file_lock:
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(body, f)
            os.replace(tmp, p)
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
```

Replace:

```python
def run_one_time(ctx: SyncRunContext) -> tuple[str, SyncRunResult]:
    if not _scheduler_lock.acquire(blocking=False):
        return "already_running", SyncRunResult()
    try:
        _stop_event.clear()
        result = _run_once(ctx)
        return _current_status, result
    finally:
        _scheduler_lock.release()
```

with:

```python
def run_one_time(ctx: SyncRunContext) -> tuple[str, SyncRunResult]:
    with _control_lock:
        if not _scheduler_lock.acquire(blocking=False):
            return "already_running", SyncRunResult()
        _stop_event.clear()
    try:
        result = _run_once(ctx)
        return _current_status, result
    finally:
        _scheduler_lock.release()
```

Replace the whole functions `start_continuous` and `stop_continuous` with:

```python
def start_continuous(ctx: SyncRunContext) -> str:
    global _worker_thread
    with _control_lock:
        if not _scheduler_lock.acquire(blocking=False):
            return "already_running"

        def _worker_wrapper() -> None:
            global _last_worker_error
            try:
                _continuous_worker(ctx)
            except Exception as exc:
                _last_worker_error = str(exc)
                logger.exception("Continuous sync worker crashed")
                _set_status("worker_crashed")
            finally:
                try:
                    _scheduler_lock.release()
                except RuntimeError:
                    pass
                if _current_status == "running":
                    _set_status("not_running")

        _stop_event.clear()
        _worker_thread = threading.Thread(target=_worker_wrapper, daemon=True)
        _worker_thread.start()
        _set_status("running")
        return "running"


def stop_continuous() -> str:
    with _control_lock:
        _stop_event.set()
        if _worker_thread and _worker_thread.is_alive():
            _worker_thread.join(timeout=120)
        _set_status("not_running")
        return "not_running"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/integration/test_sync_route_concurrency.py tests/unit/test_sync_scheduler.py tests/unit/test_sync_config.py tests/integration/test_sync_routes.py tests/unit/test_sync_wake.py`

Expected: PASS. The seeding test takes about 2 s, because the GET holds the lock while the patched seed waits.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/sync_config.py KnovasConnector/src/sync/sync_scheduler.py \
  KnovasConnector/tests/integration/test_sync_route_concurrency.py KnovasConnector/tests/unit/test_sync_scheduler.py
git commit -F - <<'EOF'
rc: serialise the routes that write configuration or scheduler state

Preparation for gunicorn's threaded worker (next commit), where requests
run concurrently. A GET /sync/config that seeds a missing file could
overwrite a concurrent POST; two body or config writes could race their
renames; a start right after a stop was reported not_running while its
worker ran. Each resource gets one narrow lock: config file, body file,
and start/stop (a one-time run takes the last only to start). The
doc-fields requeue route was already serialised.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-10: The Connector image runs gunicorn gthread with `RC_GUNICORN_TIMEOUT`

**Files:**
- Modify: `KnovasConnector/Dockerfile` (CMD and its comment)
- Modify: `.github/workflows/ci.yml` (boot step checks PID 1)
- Modify: `KnovasConnector/docs/configuration.md`, `KnovasConnector/docs/SETUP.md`, `KnovasConnector/docs/local-commands.md`, `KnovasConnector/docs/operations.md`, `KnovasConnector/CHANGELOG.md`
- Create: `KnovasConnector/tests/unit/test_image_gunicorn.py`

**Interfaces:**
- Consumes: EXT-9 (routes are safe under concurrent requests).
- Produces: env `RC_GUNICORN_TIMEOUT` (default 120), read at container start.

- [ ] **Step 1: Write the failing test**

Create `KnovasConnector/tests/unit/test_image_gunicorn.py`:

```python
"""The Connector image runs gunicorn's threaded worker (spec E6).

With the sync worker, a POST /sync longer than --timeout got the only
worker killed -- and the continuous-sync scheduler thread with it -- and
GET /sync/status could not answer while a one-time sync ran.
"""
from __future__ import annotations

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def _cmd() -> str:
    lines = [line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines() if line.startswith("CMD")]
    assert len(lines) == 1, lines
    return lines[0]


def test_the_image_runs_one_gthread_worker_with_four_threads():
    cmd = _cmd()
    assert cmd.startswith("CMD exec gunicorn "), "shell form with exec: gunicorn is PID 1 and gets SIGTERM"
    words = cmd.split()
    assert words[words.index("-w") + 1] == "1", "one process: the sync scheduler lives in it"
    assert words[words.index("-k") + 1] == "gthread"
    assert words[words.index("--threads") + 1] == "4"


def test_the_worker_timeout_is_configurable_with_the_old_default():
    assert '--timeout "${RC_GUNICORN_TIMEOUT:-120}"' in _cmd()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `rc-pytest tests/unit/test_image_gunicorn.py`

Expected: FAIL. The CMD is the exec-form `CMD ["gunicorn", …]`.

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/Dockerfile`, replace:

```dockerfile
# Single worker: continuous sync scheduler uses in-process locks (see docs/SETUP.md).
CMD ["gunicorn", "-b", "0.0.0.0:5001", "-w", "1", "--timeout", "120", "app:app"]
```

with:

```dockerfile
# One worker process -- the continuous sync scheduler and its locks live in it
# (docs/SETUP.md) -- with four request threads: gthread's main loop keeps
# reporting while a long POST /sync runs, so --timeout no longer kills the
# worker and the scheduler thread with it, and GET /sync/status answers
# meanwhile. Shell form so RC_GUNICORN_TIMEOUT (default 120 s) is read at
# start; exec keeps gunicorn PID 1 so it receives docker's SIGTERM.
CMD exec gunicorn -b 0.0.0.0:5001 -w 1 -k gthread --threads 4 --timeout "${RC_GUNICORN_TIMEOUT:-120}" app:app
```

In `.github/workflows/ci.yml` (Connector job, step `Boot with valid env and probe health`), replace:

```yaml
          docker exec rc-ci python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:5001/health'); assert r.status==200, r.status"
          docker stop rc-ci
```

with:

```yaml
          docker exec rc-ci python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:5001/health'); assert r.status==200, r.status"
          # E6: PID 1 is gunicorn itself (shell-form CMD with exec), one
          # gthread worker with four threads, the default 120 s timeout.
          pid1="$(docker exec rc-ci sh -c 'tr "\0" " " < /proc/1/cmdline')"
          echo "PID 1: $pid1"
          case "$pid1" in
            *gunicorn*"-w 1 -k gthread --threads 4 --timeout 120 "*) ;;
            *) echo "the image does not run the gthread gunicorn as PID 1"; exit 1 ;;
          esac
          docker stop rc-ci
```

In `KnovasConnector/docs/configuration.md`, insert directly above the heading `### Search context sidecars`:

```markdown
### RC_GUNICORN_TIMEOUT

Seconds gunicorn waits for a silent worker process before it kills and
restarts it (default `120`; read when the container starts). The image runs
one worker process with four request threads (`-k gthread --threads 4`): the
worker's main loop keeps reporting while a request thread works, so a long
`POST /sync` (a one-time sync runs inside the request) is no longer cut off at
this timeout, and `GET /sync/status` answers meanwhile. It bounds how long a
hung worker process goes unnoticed, not how long a request may take.
```

In `KnovasConnector/docs/SETUP.md`, replace:

```
**Gunicorn workers:** The image runs **one** worker (`-w 1`). Continuous sync uses in-process locks; multiple workers cause duplicate schedulers and conflicting state files. If you run Gunicorn manually, keep `-w 1`.
```

with:

```
**Gunicorn workers:** The image runs **one** worker process (`-w 1`) with four request threads (`-k gthread --threads 4`). Continuous sync uses in-process locks; multiple worker processes cause duplicate schedulers and conflicting state files. The threads let `GET /sync/status` answer while a long `POST /sync` runs, and the worker is no longer killed when such a request outlasts `--timeout` (`RC_GUNICORN_TIMEOUT`, default 120 s). If you run Gunicorn manually, keep `-w 1` and use the same `-k gthread --threads 4`.
```

In `KnovasConnector/docs/local-commands.md`, replace:

```bash
gunicorn -b 127.0.0.1:5001 -w 1 app:app
```

with:

```bash
gunicorn -b 127.0.0.1:5001 -w 1 -k gthread --threads 4 app:app
```

and replace:

```
Use **one** Gunicorn worker for continuous sync (`-w 1`).
```

with:

```
Use **one** Gunicorn worker process for continuous sync (`-w 1`), with `-k gthread --threads 4` as in the image.
```

In `KnovasConnector/docs/operations.md`, replace:

```
Use a **single** Gunicorn worker (`-w 1`) when running from source; multiple workers conflict on scheduler state.
```

with:

```
Use a **single** Gunicorn worker process (`-w 1`, with `-k gthread --threads 4` as in the image) when running from source; multiple worker processes conflict on scheduler state.
```

Append to the CHANGELOG subsection:

```markdown
- **Web server**: the image runs one gunicorn `gthread` worker with four threads and `--timeout ${RC_GUNICORN_TIMEOUT:-120}` (shell-form CMD, gunicorn stays PID 1). A long `POST /sync` no longer gets the worker — and the scheduler thread — killed, and `GET /sync/status` answers meanwhile. Config and body writes and start/stop are serialised (previous entry's commit).
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/unit/test_image_gunicorn.py tests/unit/test_image_license_gate.py`

Expected: PASS.

Optional local image check, Linux/Docker only:
- `docker build -t knovas-connector:ext10 $WT/KnovasConnector`
- `docker run --rm --entrypoint sh knovas-connector:ext10 -c 'grep "^CMD" -n /dev/null || true'`

The image boot itself is checked by the CI step above. The extraction child is still forked from the thread that runs the sync, as before; logging re-initialises its locks after fork.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/Dockerfile KnovasConnector/tests/unit/test_image_gunicorn.py .github/workflows/ci.yml \
  KnovasConnector/docs/configuration.md KnovasConnector/docs/SETUP.md KnovasConnector/docs/local-commands.md \
  KnovasConnector/docs/operations.md KnovasConnector/CHANGELOG.md
git commit -F - <<'EOF'
rc: gunicorn gthread worker, RC_GUNICORN_TIMEOUT

The image ran one sync worker with --timeout 120. The documented operator
call POST /sync runs a one-time sync inside the request, and when it took
longer the arbiter killed the only worker and the scheduler thread with
it. gthread's main loop keeps heartbeating while request threads work, and
GET /sync/status answers meanwhile. The CMD is now shell form with exec,
so RC_GUNICORN_TIMEOUT is read at start and gunicorn stays PID 1. CI
checks PID 1 in the booted image.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-11: Small corrections — docstrings, stale docs, no `engine → backend` alias

**Files:**
- Modify: `KnovasConnector/src/sync/document_text.py` (`pdf_text_mode` docstring, `extract_document` docstring, `_OCR_OPTION_ALIASES`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py` (`pdf_text_mode` docstring, `_OCR_OPTION_ALIASES`)
- Modify: `KnovasConnector/pyproject.toml` (comment on `[html]`), `KnovasConnector/docs/configuration.md`, `KnovasConnector/docs/operations.md`, `KnovasPlatform/components/docbridge_integration/README.md`
- Verify only: `.github/workflows/ci.yml` (the `[pdf]` comment)
- Test: `KnovasConnector/tests/unit/test_document_text.py`, `KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py`

**Interfaces:**
- Consumes: —
- Produces: `_OCR_OPTION_ALIASES["engine"] == ("engine",)` in both components.

- [ ] **Step 1: Write the failing tests**

Append to `KnovasConnector/tests/unit/test_document_text.py`:

```python
def test_an_engine_name_never_reaches_a_backend_slot(monkeypatch):
    """``OcrOptions.backend`` takes an injected IOcrBackend object. The alias
    engine -> backend would have passed the engine NAME there if a library
    dropped ``engine`` (spec E7)."""
    from sync import document_text

    class OnlyBackend:
        def __init__(self, backend=None, language="deu+eng", cache=None):
            self.backend, self.language, self.cache = backend, language, cache

    monkeypatch.setattr(document_text, "OcrOptions", OnlyBackend)
    built = document_text.build_ocr_options({"engine": "cli", "language": "deu", "cache": "c"})
    assert built.backend is None
    assert (built.language, built.cache) == ("deu", "c")
```

Append to `KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py`:

```python
def test_an_engine_name_never_reaches_a_backend_slot(monkeypatch):
    """Spec E7: ``backend`` takes an object; an engine name must never land
    there, it is simply not sent to a class without ``engine``."""

    class OnlyBackend:
        def __init__(self, backend=None, language="deu+eng", cache=None):
            self.backend, self.language, self.cache = backend, language, cache

    monkeypatch.setattr(m, "OcrOptions", OnlyBackend)
    options, leftovers = m.build_ocr_options({"engine": "cli", "language": "deu", "cache": None})
    assert options.backend is None
    assert leftovers == {"engine": "cli"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rc-pytest tests/unit/test_document_text.py::test_an_engine_name_never_reaches_a_backend_slot`

Expected: FAIL: `'cli' is None`.

Run: `pf-pytest tests/test_knovas_extract_upload.py::test_an_engine_name_never_reaches_a_backend_slot`

Expected: FAIL: `'cli' is None`.

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/src/sync/document_text.py`, replace:

```python
    "engine": ("engine", "backend"),
```

with:

```python
    # Not "backend": that slot takes an injected IOcrBackend object, and an
    # engine name there would be a type error at the first scanned page.
    "engine": ("engine",),
```

Replace:

```python
    """`RC_PDF_TEXT_MODE`: plain (default) | shadow | layout."""
```

with:

```python
    """`RC_PDF_TEXT_MODE`: layout (default) | plain | shadow."""
```

In `extract_document`, replace:

```
    Returns an `ExtractedDocument`. Raises `ConversionError` on any recoverable
    per-file failure (unsupported format, corrupt bytes, encrypted, resource
    cap exceeded, empty output). Lets `DependencyMissingError` bubble — that
    is a deploy misconfiguration, not a per-file issue.
```

with:

```
    Returns an `ExtractedDocument`. Raises `ConversionError` on any per-file
    failure (unsupported format, corrupt bytes, encrypted, resource cap
    exceeded, empty output) and also for a `DependencyMissingError`: a
    missing extra is a deploy misconfiguration, and its message ("missing
    optional dependency …") is not one `is_unconvertible_error` matches, so
    the file stays retryable (counted toward `RC_EXTRACT_MAX_RETRIES`) and is
    never parked as unconvertible.
```

In `KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py`, replace:

```python
    "engine": ("engine", "backend"),
```

with:

```python
    # Not "backend": that slot takes an injected IOcrBackend object.
    "engine": ("engine",),
```

and replace:

```python
    """``RC_PDF_TEXT_MODE``: plain (default) | shadow | layout."""
```

with:

```python
    """``RC_PDF_TEXT_MODE``: layout (default) | plain | shadow."""
```

In `KnovasConnector/pyproject.toml`, in the comment above the `knovas-extract` dependency, replace the lines:

```
    # pymupdf-layout into the customer image. [html] stays: EML/MSG HTML
    # bodies are converted through selectolax. The Docker image adds [ocr]
```

with:

```
    # pymupdf-layout into the customer image. [html] (selectolax) stays so CI
    # and the image install one extras set; EML/MSG HTML bodies do not use it,
    # knovas-extract converts them with its own HTML-to-text helper. The Docker image adds [ocr]
```

If PIN-* has already rewritten this comment block, apply the same correction to its sentence about `[html]`.

In `KnovasConnector/docs/configuration.md`, replace:

```
Keywords the installed `knovas-extract` does not take are withheld, so the same image runs against 0.3 (today) and 0.4 (`text_mode=`, `ocr=`).
```

with:

```
Keywords the installed `knovas-extract` does not take (`text_mode=`, `ocr=`, the `Limits` OCR fields) are withheld (`extract_accepts`), so the same source still runs against an older release; the image and CI install 0.4.0a1.
```

In `KnovasPlatform/components/docbridge_integration/README.md`, replace:

```
`ocr=`, the `Limits` OCR fields) are withheld, so the same image runs against
0.3 (today) and 0.4.
```

with:

```
`ocr=`, the `Limits` OCR fields) are withheld, so the same source still runs
against an older release; the image and CI install 0.4.0a1.
```

In `KnovasConnector/docs/operations.md`, replace the substring `when \`knovas-extract>=0.3\` and \`tesseract-ocr\` are present` with `when knovas-extract 0.4 (per-page OCR) and \`tesseract-ocr\` are present`. Skip this if PIN-* already changed the version mention.

Verify `.github/workflows/ci.yml`: `grep -n "drags\|\[pdf\] still" $WT/.github/workflows/ci.yml` shows only the corrected comment ("whose [pdf] extra is pymupdf alone … drags pymupdf-layout back in fails here"). The document-fields branch already fixed it, so no change is needed. If a later task re-introduced "[pdf] still drags", correct it the same way.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/unit/test_document_text.py`

Expected: PASS. `test_ocr_option_names_follow_the_library_signature` still passes, since its fake class has `engine`.

Run: `pf-pytest tests/test_knovas_extract_upload.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/document_text.py KnovasConnector/tests/unit/test_document_text.py \
  KnovasConnector/pyproject.toml KnovasConnector/docs/configuration.md KnovasConnector/docs/operations.md \
  KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py \
  KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py \
  KnovasPlatform/components/docbridge_integration/README.md
git commit -F - <<'EOF'
rc+platform: drop the engine->backend alias; correct stale docstrings and docs

OcrOptions.backend takes an injected IOcrBackend object, so the alias
would hand it an engine name as soon as a library dropped "engine". Also
corrected: the text mode defaults to layout, not plain; a
DependencyMissingError is converted, not passed through; 0.3 is no longer
"today"; EML/MSG HTML bodies are not converted through selectolax.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-12: Description from `docx:subject`, `pdf:subject`, `pdf:xmp_description`

**Files:**
- Modify: `KnovasConnector/src/sync/extract_content.py` (`description_from_metadata`)
- Modify: `KnovasConnector/CHANGELOG.md`
- Create: `KnovasConnector/tests/unit/test_extract_content.py`

**Interfaces:**
- Consumes: —
- Produces: `sync.extract_content.description_from_metadata(metadata) -> Optional[str]` reads `docx:subject`, then `pdf:subject`, then `pdf:xmp_description`. It caps at 2000 characters, as before.

- [ ] **Step 1: Write the failing tests**

Create `KnovasConnector/tests/unit/test_extract_content.py`:

```python
"""Description from file properties (spec L2): the library's keys, in order."""
from __future__ import annotations

from knovas_extract.result import Metadata

from sync.extract_content import description_from_metadata


def test_description_prefers_word_then_pdf_subject_then_xmp_description():
    every = Metadata(extra={"docx:subject": "Mandatsvertrag", "pdf:subject": "Ignoriert",
                            "pdf:xmp_description": "Auch ignoriert"})
    assert description_from_metadata(every) == "Mandatsvertrag"
    pdf = Metadata(extra={"pdf:subject": "Jahresrechnung 2023", "pdf:xmp_description": "Beschreibung"})
    assert description_from_metadata(pdf) == "Jahresrechnung 2023"
    xmp = Metadata(extra={"pdf:subject": "  ", "pdf:xmp_description": "Revisionsbericht"})
    assert description_from_metadata(xmp) == "Revisionsbericht", "a blank subject falls through"


def test_keys_the_library_never_produces_are_not_read():
    assert description_from_metadata(Metadata(extra={"subject": "x", "description": "y"})) is None
    assert description_from_metadata(Metadata(extra={})) is None
    assert description_from_metadata(None) is None


def test_description_is_capped_at_2000_characters():
    assert len(description_from_metadata(Metadata(extra={"pdf:subject": "a" * 5000}))) == 2000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rc-pytest tests/unit/test_extract_content.py`

Expected: FAIL.
- `None == 'Jahresrechnung 2023'` (the `pdf:` keys are not read).
- `'x' is None` (the `subject` key is read).

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/src/sync/extract_content.py`, replace:

```python
def description_from_metadata(metadata: Any) -> Optional[str]:
    if metadata is None:
        return None
    extra = getattr(metadata, "extra", None) or {}
    for key in ("docx:subject", "subject", "description"):
```

with:

```python
#: Where knovas-extract puts a document's own description, in order (spec
#: L2): Word's subject, the PDF Info subject, the PDF XMP description. The
#: keys read before ("subject", "description") are never produced.
DESCRIPTION_KEYS = ("docx:subject", "pdf:subject", "pdf:xmp_description")


def description_from_metadata(metadata: Any) -> Optional[str]:
    """The upload's description when the ingestion profile sets none: the
    first non-blank of ``DESCRIPTION_KEYS``, at most 2000 characters."""
    if metadata is None:
        return None
    extra = getattr(metadata, "extra", None) or {}
    for key in DESCRIPTION_KEYS:
```

Append to the CHANGELOG subsection:

```markdown
- **Description** from file properties when the profile sets none: `docx:subject`, then `pdf:subject`, then `pdf:xmp_description` (the keys read before were never produced, so PDFs had none).
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/unit/test_extract_content.py tests/unit/test_document_text.py tests/unit/test_knovas_uploader.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/extract_content.py KnovasConnector/tests/unit/test_extract_content.py \
  KnovasConnector/CHANGELOG.md
git commit -F - <<'EOF'
rc: description from docx:subject, pdf:subject, pdf:xmp_description

When the ingestion profile sets no description, the Connector looked for
docx:subject, "subject" and "description". The library never produces
the last two, so no PDF ever got a description. The keys it does produce
are now read in that order.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-13: DOCX layout mode — `RC_DOCX_TEXT_MODE`, no duplicate `tables` payload (Connector + Platform)

**Files:**
- Modify: `KnovasConnector/src/sync/document_text.py` (module docstring, `DOCX_TEXT_MODES`, `DEFAULT_DOCX_TEXT_MODE`, `docx_text_mode`, `docx_tables_in_text`, `_extract_bytes`)
- Modify: `KnovasConnector/src/sync/knovas_uploader.py` (import, tables decision in `upload_file`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py` (`docx_text_mode`, `docx_tables_in_text`, `_send_tables_payload`, `_extract_bytes`, `extract_parts_from_base64`)
- Modify: `KnovasConnector/docs/configuration.md`, `KnovasPlatform/components/docbridge_integration/README.md`, `KnovasConnector/CHANGELOG.md`
- Test: `KnovasConnector/tests/unit/test_document_text.py`, `KnovasConnector/tests/unit/test_knovas_uploader.py`, `KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py`

**Interfaces:**
- Consumes: the Part A library at verification time (DOCX layout mode; `docx:text_mode`, `docx:layout_tables`). The tests fake `extract`.
- Produces:
  - `sync.document_text.docx_text_mode() -> str` (`"layout"` / `"plain"`)
  - `sync.document_text.docx_tables_in_text(doc: ExtractedDocument) -> bool`
  - Platform `knovas_extract_upload.docx_text_mode() -> str` (reads `RC_DOCX_TEXT_MODE`)
  - Platform `knovas_extract_upload.docx_tables_in_text(extra: Optional[dict]) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `KnovasConnector/tests/unit/test_document_text.py`:

```python
# --- DOCX layout mode (spec L3) ----------------------------------------------


def test_docx_text_mode_env(monkeypatch, caplog):
    import logging

    from sync.document_text import docx_text_mode

    monkeypatch.delenv("RC_DOCX_TEXT_MODE", raising=False)
    assert docx_text_mode() == "layout", "tables in the text by default"
    monkeypatch.setenv("RC_DOCX_TEXT_MODE", " Plain ")
    assert docx_text_mode() == "plain"
    monkeypatch.setenv("RC_DOCX_TEXT_MODE", "shadow")
    with caplog.at_level(logging.WARNING, logger="sync.document_text"):
        assert docx_text_mode() == "layout", "shadow is a PDF mode: invalid here"
    assert any("RC_DOCX_TEXT_MODE" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("env, ext, sent", [
    (None, ".docx", "layout"),
    ("layout", ".docx", "layout"),
    ("plain", ".docx", None),
    (None, ".eml", None),
    (None, ".msg", None),
    (None, ".txt", None),
])
def test_docx_layout_mode_is_requested_for_docx_only(monkeypatch, env, ext, sent):
    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=False, text_mode=True)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    if env is None:
        monkeypatch.delenv("RC_DOCX_TEXT_MODE", raising=False)
    else:
        monkeypatch.setenv("RC_DOCX_TEXT_MODE", env)
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"PK stub", ext)
    # the stub records its own default ("plain") when nothing was sent
    assert seen["text_mode"] == (sent or "plain")


def test_docx_layout_mode_is_withheld_from_a_library_without_text_mode(monkeypatch):
    from sync import document_text

    extract_stub, seen = _signature_stub(ocr_options=False, text_mode=False)
    monkeypatch.setattr(document_text, "extract", extract_stub)
    monkeypatch.delenv("RC_DOCX_TEXT_MODE", raising=False)
    with pytest.raises(document_text.ConversionError):
        document_text._extract_bytes(b"PK stub", ".docx")
    assert "text_mode" not in seen


def test_docx_tables_in_text_reads_the_library_metadata():
    from sync.document_text import ExtractedDocument, docx_tables_in_text

    layout = ExtractedDocument(text="x", sentences=None, extra={"docx:text_mode": "layout", "docx:layout_tables": 2})
    assert docx_tables_in_text(layout) is True
    assert docx_tables_in_text(ExtractedDocument(text="x", sentences=None, extra={})) is False
    assert docx_tables_in_text(ExtractedDocument(text="x", sentences=None, extra=None)) is False
```

Append to `KnovasConnector/tests/unit/test_knovas_uploader.py`:

```python
def _docx_with_table(extra):
    from sync.document_text import ExtractedDocument

    return ExtractedDocument(
        text="Honorarnote\n\nPosition | Betrag\nBeratung | Betrag: 1'200.00",
        sentences=None,
        tables=[{"client_table_hint": "docx_t1", "headers": ["Position", "Betrag"],
                 "rows": [["Beratung", "1'200.00"]]}],
        extra=extra,
    )


@pytest.mark.parametrize("extra, payload", [
    ({"docx:text_mode": "layout", "docx:layout_tables": 1}, False),
    ({}, True),
    (None, True),
])
def test_docx_tables_payload_only_when_the_rows_are_not_in_the_text(mock_config, tmp_path, extra, payload):
    """Spec L3: in layout mode the library writes the table rows into the
    text; a payload as well would be indexed twice if the server ever stopped
    dropping it. A library without DOCX layout mode (no docx:text_mode) has
    no rows in the text, and the payload stays."""
    docx = tmp_path / "honorar.docx"
    docx.write_bytes(b"PK stub")
    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req, patch(
        "sync.knovas_uploader.extract_document_guarded", return_value=_docx_with_table(extra)
    ), patch("sync.knovas_uploader.write_context_sidecar", return_value=True):
        req.side_effect = [_ok_response(), _ok_response()]
        uploader.upload_file(docx, "akten/honorar.docx", {"ingestion": {"identifier_prefix": "corpus"}})
    part_json = req.call_args_list[1].kwargs["json_body"]
    assert "Beratung | Betrag: 1'200.00" in part_json["snippet"]
    assert ("tables" in part_json) is payload
```

In `KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py`, add `"RC_DOCX_TEXT_MODE",` to the `_ENV` tuple. Replace:

```python
    "RC_EXTRACT_RLIMIT_AS_MB", "SEARCH_CONTEXT_STORE_PATH",
)
```

with:

```python
    "RC_EXTRACT_RLIMIT_AS_MB", "SEARCH_CONTEXT_STORE_PATH", "RC_DOCX_TEXT_MODE",
)
```

and append:

```python
def test_docx_text_mode_env(monkeypatch):
    assert m.docx_text_mode() == "layout"
    monkeypatch.setenv("RC_DOCX_TEXT_MODE", "plain")
    assert m.docx_text_mode() == "plain"
    monkeypatch.setenv("RC_DOCX_TEXT_MODE", "rows")
    assert m.docx_text_mode() == "layout", "an invalid value falls back to the default"


def test_docx_layout_mode_is_requested_for_docx(monkeypatch):
    extract_stub, seen = _signature_stub(ocr_options=False, text_mode=True)
    monkeypatch.setattr(m, "extract", extract_stub)
    with pytest.raises(m.ExtractionError):
        m._extract_bytes(b"PK stub", ".docx")
    assert seen["text_mode"] == "layout"
    monkeypatch.setenv("RC_DOCX_TEXT_MODE", "plain")
    with pytest.raises(m.ExtractionError):
        m._extract_bytes(b"PK stub", ".docx")
    assert seen["text_mode"] == "plain", "nothing sent: the stub's default"
    monkeypatch.delenv("RC_DOCX_TEXT_MODE")
    with pytest.raises(m.ExtractionError):
        m._extract_bytes(b"stub", ".eml")
    assert seen["text_mode"] == "plain", "DOCX only"


def test_docx_tables_payload_only_when_the_rows_are_not_in_the_text(monkeypatch):
    table = {"client_table_hint": "docx_t1", "headers": ["Position", "Betrag"], "rows": [["Beratung", "1'200.00"]]}
    layout = ExtractedContent(
        text="Position | Betrag\nBeratung | Betrag: 1'200.00", tables=[table],
        extra={"docx:text_mode": "layout", "docx:layout_tables": 1},
    )
    monkeypatch.setattr(m, "extract_guarded", lambda *a, **k: layout)
    out = m.extract_parts_from_base64(_b64(b"PK stub"), "docx", write_sidecar=False)
    assert out.parts and all("tables" not in p for p in out.parts)
    plain = ExtractedContent(text="Honorarnote.", tables=[table])
    monkeypatch.setattr(m, "extract_guarded", lambda *a, **k: plain)
    out = m.extract_parts_from_base64(_b64(b"PK stub"), "docx", write_sidecar=False)
    assert out.parts[0]["tables"][0]["client_table_hint"] == "docx_t1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rc-pytest tests/unit/test_document_text.py -k docx tests/unit/test_knovas_uploader.py -k docx_tables_payload`

Expected: FAIL.
- `ImportError: cannot import name 'docx_text_mode'`.
- `seen["text_mode"] == "plain"` for `.docx` (nothing sent).
- The layout case still carries `tables`.

Run: `pf-pytest tests/test_knovas_extract_upload.py -k docx`

Expected: FAIL: `AttributeError: … 'docx_text_mode'`, and the DOCX payload is sent in layout mode.

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/src/sync/document_text.py`, replace in the module docstring:

```
cache object, so each page is OCR'd once; plan decision D13). Every new
```

with:

```
cache object, so each page is OCR'd once; plan decision D13), and
`RC_DOCX_TEXT_MODE` (`layout` | `plain`) asks for DOCX tables as rows in
the text (spec L3). Every new
```

Replace:

```python
TEXT_MODES = ("plain", "shadow", "layout")
DEFAULT_PDF_TEXT_MODE = "layout"
```

with:

```python
TEXT_MODES = ("plain", "shadow", "layout")
DEFAULT_PDF_TEXT_MODE = "layout"
DOCX_TEXT_MODES = ("layout", "plain")
DEFAULT_DOCX_TEXT_MODE = "layout"
```

Directly after the function `pdf_text_mode`, insert:

```python
def docx_text_mode() -> str:
    """`RC_DOCX_TEXT_MODE`: layout (default) | plain (spec L3). Layout asks
    knovas-extract to write Word tables into the text, in place, as
    markdown-lite rows -- the server drops the `tables` payload, so before
    this a DOCX table's content never reached the search index. Plain is
    paragraphs only. Invalid values log one warning and use the default."""
    raw = (os.environ.get("RC_DOCX_TEXT_MODE") or "").strip().lower()
    if not raw:
        return DEFAULT_DOCX_TEXT_MODE
    if raw in DOCX_TEXT_MODES:
        return raw
    logger.warning("Invalid RC_DOCX_TEXT_MODE=%r; using %s", raw, DEFAULT_DOCX_TEXT_MODE)
    return DEFAULT_DOCX_TEXT_MODE


def docx_tables_in_text(doc: ExtractedDocument) -> bool:
    """Whether knovas-extract wrote this DOCX's tables into its text: it
    reports `docx:text_mode == "layout"` (DOCX layout mode). Then the upload
    sends no `tables` payload -- the rows would be indexed twice if the
    server ever stopped dropping it. A library without DOCX layout mode
    reports nothing, its text has no rows, and the payload stays."""
    value = (doc.extra or {}).get("docx:text_mode")
    return isinstance(value, str) and value.strip().lower() == "layout"
```

In `_extract_bytes`, replace:

```python
    if ext == ".pdf":
        pdf_kwargs, shadow, cache = _pdf_extract_kwargs(
            timeout_seconds=timeout_seconds, document_key=document_key
        )
        extract_kwargs.update(pdf_kwargs)
```

with:

```python
    if ext == ".pdf":
        pdf_kwargs, shadow, cache = _pdf_extract_kwargs(
            timeout_seconds=timeout_seconds, document_key=document_key
        )
        extract_kwargs.update(pdf_kwargs)
    elif ext == ".docx" and docx_text_mode() == "layout" and extract_accepts("text_mode"):
        # Word tables in place, as markdown-lite rows, so their content is
        # searchable (spec L3); a library without DOCX layout mode returns the
        # plain text with one warning and the tables payload stays.
        extract_kwargs["text_mode"] = "layout"
```

In `KnovasConnector/src/sync/knovas_uploader.py`, replace:

```python
    _env_flag,
    extract_document_guarded,
```

with:

```python
    _env_flag,
    docx_tables_in_text,
    extract_document_guarded,
```

and replace:

```python
            tables = doc.tables
            if ext == ".pdf" and not send_pdf_tables_enabled():
                tables = None
```

with:

```python
            tables = doc.tables
            if ext == ".pdf" and not send_pdf_tables_enabled():
                tables = None
            elif ext == ".docx" and docx_tables_in_text(doc):
                # Layout mode wrote the rows into the text (spec L3): a
                # payload would be indexed twice if the server ever stopped
                # dropping it at its part buffer.
                tables = None
```

In `KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py`, replace:

```python
TEXT_MODES = ("plain", "shadow", "layout")
DEFAULT_PDF_TEXT_MODE = "layout"
```

with:

```python
TEXT_MODES = ("plain", "shadow", "layout")
DEFAULT_PDF_TEXT_MODE = "layout"
DOCX_TEXT_MODES = ("layout", "plain")
DEFAULT_DOCX_TEXT_MODE = "layout"
```

Directly after the Platform's `pdf_text_mode`, insert:

```python
def docx_text_mode() -> str:
    """``RC_DOCX_TEXT_MODE``: layout (default) | plain -- the Connector's
    setting (spec L3). Layout asks knovas-extract to write Word tables into
    the text, in place, as markdown-lite rows; plain is paragraphs only."""
    raw = (os.environ.get("RC_DOCX_TEXT_MODE") or "").strip().lower()
    if not raw:
        return DEFAULT_DOCX_TEXT_MODE
    if raw in DOCX_TEXT_MODES:
        return raw
    logger.warning("Invalid RC_DOCX_TEXT_MODE=%r; using %s", raw, DEFAULT_DOCX_TEXT_MODE)
    return DEFAULT_DOCX_TEXT_MODE


def docx_tables_in_text(extra: Optional[dict[str, Any]]) -> bool:
    """Whether knovas-extract wrote the DOCX tables into the text: it reports
    ``docx:text_mode == "layout"``. Then no ``tables`` payload is sent (the
    rows would be indexed twice if the server ever stopped dropping it); a
    library without DOCX layout mode reports nothing and the payload stays."""
    value = (extra or {}).get("docx:text_mode")
    return isinstance(value, str) and value.strip().lower() == "layout"


def _send_tables_payload(ext: str, extra: Optional[dict[str, Any]]) -> bool:
    """PDF: only with ``RC_SEND_PDF_TABLES``. DOCX: only when the rows are not
    already in the text (``docx_tables_in_text``). Everything else: yes."""
    if ext == ".pdf":
        return send_pdf_tables_enabled()
    if ext == ".docx":
        return not docx_tables_in_text(extra)
    return True
```

In the Platform's `_extract_bytes`, replace:

```python
    if ext == ".pdf":
        pdf_kwargs, shadow, cache, mode = _pdf_extract_kwargs(
            use_ocr=use_ocr, ocr_language=ocr_language, timeout_seconds=timeout_seconds
        )
        extract_kwargs.update(pdf_kwargs)
```

with:

```python
    if ext == ".pdf":
        pdf_kwargs, shadow, cache, mode = _pdf_extract_kwargs(
            use_ocr=use_ocr, ocr_language=ocr_language, timeout_seconds=timeout_seconds
        )
        extract_kwargs.update(pdf_kwargs)
    elif ext == ".docx" and docx_text_mode() == "layout" and extract_accepts("text_mode"):
        # Word tables in place, as markdown-lite rows (spec L3).
        extract_kwargs["text_mode"] = "layout"
        mode = "layout"
```

In `extract_parts_from_base64`, replace:

```python
    if content.tables and (dotted != ".pdf" or send_pdf_tables_enabled()):
```

with:

```python
    if content.tables and _send_tables_payload(dotted, content.extra):
```

In `KnovasConnector/docs/configuration.md`, insert directly below the `RC_PDF_TEXT_MODE` row:

```
| `RC_DOCX_TEXT_MODE` | `layout` | `layout` — Word tables are written into the text in place, as markdown-lite rows (one line per row, as for PDFs in layout mode), so their content is searchable; such a DOCX is sent without a `tables` payload (the rows would be indexed twice). Needs a knovas-extract with DOCX layout mode (it reports `docx:text_mode`); an older one returns the plain text and the payload stays. `plain` — paragraphs only, tables as payload. Invalid values log a warning and use `layout`. |
```

and in the `RC_SEND_PDF_TABLES` row replace `DOCX tables are still sent.` with `DOCX tables are sent unless the library wrote their rows into the text (\`RC_DOCX_TEXT_MODE=layout\`).`.

In `KnovasPlatform/components/docbridge_integration/README.md`, insert directly below the `RC_PDF_TEXT_MODE` row:

```
| `RC_DOCX_TEXT_MODE` | `layout` | The Connector's setting: `layout` asks knovas-extract to write Word tables into the text as markdown-lite rows (no `tables` payload for such a DOCX); `plain` is paragraphs only, tables as payload. Invalid values log a warning and use `layout`. |
```

and in its `RC_SEND_PDF_TABLES` row replace `DOCX tables are still sent.` with `DOCX tables are sent unless the library wrote their rows into the text (\`RC_DOCX_TEXT_MODE=layout\`).`.

Append to the CHANGELOG subsection:

```markdown
- **`RC_DOCX_TEXT_MODE`** (`layout` default, `plain`; the Platform reads it too): knovas-extract writes Word tables into the text as markdown-lite rows, so their content becomes searchable (the server drops the `tables` payload). A DOCX the library rendered that way (`docx:text_mode: layout`) is sent without a `tables` payload.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/unit/test_document_text.py tests/unit/test_knovas_uploader.py tests/unit/test_document_text_source_metadata.py tests/unit/test_metadata_fields.py`

Expected: PASS. Against the Part A library, `test_docx_conversion` also runs DOCX layout mode for real; a DOCX without tables is byte-identical to plain.

Run: `pf-pytest tests/test_knovas_extract_upload.py tests/test_preview_extract.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/document_text.py KnovasConnector/src/sync/knovas_uploader.py \
  KnovasConnector/tests/unit/test_document_text.py KnovasConnector/tests/unit/test_knovas_uploader.py \
  KnovasConnector/docs/configuration.md KnovasConnector/CHANGELOG.md \
  KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py \
  KnovasPlatform/components/docbridge_integration/tests/test_knovas_extract_upload.py \
  KnovasPlatform/components/docbridge_integration/README.md
git commit -F - <<'EOF'
rc+platform: DOCX layout mode (RC_DOCX_TEXT_MODE), no duplicate tables payload

The library kept DOCX table text out of content.text and the server drops
the tables payload, so DOCX tables never reached the search index.
RC_DOCX_TEXT_MODE=layout (default) now asks for the tables as rows in the
text. When the library reports docx:text_mode=layout, no payload is sent:
it would be indexed twice if the server ever kept it. A library without
DOCX layout mode keeps today's text and payload.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-14: One PyMuPDF version in both components (`pymupdf==1.28.0`)

**Files:**
- Modify: `KnovasConnector/pyproject.toml` (dependencies)
- Modify: `KnovasConnector/CHANGELOG.md`
- Create: `KnovasConnector/tests/unit/test_pymupdf_pin.py`

**Interfaces:**
- Consumes: `KnovasPlatform/components/docbridge_integration/requirements.txt` already pins `pymupdf==1.28.0`.
- Produces: `KnovasConnector/pyproject.toml` dependency `pymupdf==1.28.0`.

Version choice: **1.28.0**, the Platform's pin. No Connector code or test needs anything newer than the library's floor (1.24.0):
- `src/sync/document_text.py` uses `fitz.open(stream=…, filetype="pdf")`, `is_encrypted`, `authenticate`, `page_count`;
- the tests use `fitz.open`, `new_page`, `insert_text`, `tobytes`;
- `scripts/demo_kanzlei` uses `insert_htmlbox` (since 1.23.8) and requires `pymupdf>=1.24`;
- `scripts/build_context_sidecars.py` uses `pymupdf.set_messages`.

Step 4 runs the whole Connector suite on 1.28.0. If a failure comes from the version alone, raise the pin in BOTH files to the version that passes, in this commit.

- [ ] **Step 1: Write the failing test**

Create `KnovasConnector/tests/unit/test_pymupdf_pin.py`:

```python
"""One PyMuPDF under both components' extraction (spec L7).

The Connector was unpinned (it got whatever the library's ``pymupdf>=1.24``
resolved to) while the Platform pins one version; the two images and the
two CI jobs then ran different PDF parsers under the same knovas-extract.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
RC_PYPROJECT = REPO / "KnovasConnector" / "pyproject.toml"
PLATFORM_REQUIREMENTS = REPO / "KnovasPlatform" / "components" / "docbridge_integration" / "requirements.txt"


@pytest.mark.skipif(not PLATFORM_REQUIREMENTS.is_file(), reason="the Platform is not in this checkout")
def test_the_connector_pins_the_platforms_pymupdf():
    deps = tomllib.loads(RC_PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    connector = [d.replace(" ", "") for d in deps if d.replace(" ", "").lower().startswith("pymupdf==")]
    platform = re.findall(r"^pymupdf==(\S+)\s*$", PLATFORM_REQUIREMENTS.read_text(encoding="utf-8"), re.MULTILINE)
    assert len(connector) == 1, deps
    assert len(platform) == 1
    assert connector[0].split("==", 1)[1] == platform[0]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `rc-pytest tests/unit/test_pymupdf_pin.py`

Expected: FAIL: `assert 0 == 1` (the Connector has no `pymupdf==` dependency).

- [ ] **Step 3: Write the implementation**

In `KnovasConnector/pyproject.toml`, replace:

```toml
    "gunicorn>=22.0,<24",
```

with:

```toml
    "gunicorn>=22.0,<24",
    # The Platform's PyMuPDF (docbridge_integration/requirements.txt): one PDF
    # parser under both images and both CI jobs (spec L7). The Dockerfile
    # installs these dependencies before knovas-extract, whose
    # pymupdf>=1.24 floor this satisfies.
    "pymupdf==1.28.0",
```

Re-install the Connector into its venv so the pin takes effect locally:

```bash
$SP/venvs/rc/Scripts/python -m pip install -e "$WT/KnovasConnector[dev]"
$SP/venvs/rc/Scripts/python -c "import pymupdf; print(pymupdf.VersionBind)"   # expect 1.28.0
```

Append to the CHANGELOG subsection:

```markdown
- PyMuPDF pinned to `1.28.0` in `pyproject.toml`, the Platform's version: both images and both CI jobs run one PDF parser under knovas-extract.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rc-pytest tests/unit/test_pymupdf_pin.py`

Expected: PASS.

Run the whole Connector suite on the pinned version (`cd $WT/KnovasConnector && … -m pytest -q` with the rc-pytest env).

Expected: PASS, apart from the known Windows-only failures listed in the plan header.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/pyproject.toml KnovasConnector/tests/unit/test_pymupdf_pin.py KnovasConnector/CHANGELOG.md
git commit -F - <<'EOF'
rc: pin PyMuPDF 1.28.0, the Platform's version

The Connector got whatever pymupdf the library's >=1.24 floor resolved
to, while the Platform pins 1.28.0, so the two images ran different PDF
parsers under the same knovas-extract. Both now pin the same version, and
a test keeps the pins equal. Nothing in the Connector needs newer than
the library's floor.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task EXT-15: Platform preview shows the plain text when Markdown trips a library limit

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/preview.py` (`_markdown_limit`, `extract_markdown`)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_preview_extract.py`

**Interfaces:**
- Consumes: `knovas_extract.errors.ResourceExhaustedError.what` (`"markdown expansion ratio"`, `"markdown size"`).
- Produces: `web_interface.preview.extract_markdown(path) -> dict`. Same keys as before. On a Markdown limit, `"markdown"` holds the plain text and `"warnings"` starts with `"preview: markdown limit exceeded; plain text shown"`.

- [ ] **Step 1: Write the failing tests**

Append to `KnovasPlatform/components/docbridge_integration/tests/test_preview_extract.py`:

```python
def test_a_markdown_limit_falls_back_to_the_plain_text(tmp_path, monkeypatch):
    """L7: a DOCX whose tables make the Markdown many times its text trips
    the expansion guard (ResourceExhaustedError "markdown expansion ratio");
    the preview then shows the plain text instead of failing."""
    import docx
    import knovas_extract
    from knovas_extract.errors import ResourceExhaustedError

    target = tmp_path / "honorar.docx"
    document = docx.Document()
    document.add_paragraph("Honorarabrechnung Mandat 2024-001.")
    document.save(str(target))

    real_extract = knovas_extract.extract
    calls: list = []

    def guarded_extract(path, **kwargs):
        calls.append(dict(kwargs))
        if kwargs.get("emit_markdown"):
            raise ResourceExhaustedError("markdown expansion ratio", 3.0, observed=93.0)
        return real_extract(path, **kwargs)

    monkeypatch.setattr(knovas_extract, "extract", guarded_extract)
    result = extract_markdown(str(target))
    assert [c.get("emit_markdown", False) for c in calls] == [True, False]
    assert result["kind"] == "docx"
    assert "Honorarabrechnung Mandat 2024-001." in result["markdown"]
    assert result["warnings"][0] == "preview: markdown limit exceeded; plain text shown"
    assert result["meta"]["word_count"] > 0


def test_other_resource_limits_still_fail_the_preview(tmp_path, monkeypatch):
    import knovas_extract
    from knovas_extract.errors import ResourceExhaustedError

    target = tmp_path / "notiz.txt"
    target.write_text("Zeile.", encoding="utf-8")

    def too_big(path, **kwargs):
        raise ResourceExhaustedError("input size", 1, observed=2)

    monkeypatch.setattr(knovas_extract, "extract", too_big)
    with pytest.raises(PreviewFailed):
        extract_markdown(str(target))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pf-pytest tests/test_preview_extract.py::test_a_markdown_limit_falls_back_to_the_plain_text tests/test_preview_extract.py::test_other_resource_limits_still_fail_the_preview`

Expected: the first test FAILs with `PreviewFailed: resource limit exceeded: markdown expansion ratio > 3.0`. The second test passes already, as a guard.

- [ ] **Step 3: Write the implementation**

In `KnovasPlatform/components/docbridge_integration/src/web_interface/preview.py`, replace:

```python
def extract_markdown(path: str) -> Dict[str, Any]:
    """Extrahiert ``path`` nach sanitisiertem Markdown.

    PDF gehoert nicht hierher -- es wird im Browser nativ dargestellt.
    """
    kind = preview_kind(path)
    if kind is None or kind == "pdf":
        raise PreviewUnsupported(path)

    import knovas_extract
    from knovas_extract.errors import ExtractError
    from knovas_extract.result import Limits

    limits = Limits(max_input_bytes=MAX_INPUT_BYTES, max_text_bytes=MAX_TEXT_BYTES)
    try:
        result = knovas_extract.extract(path, limits=limits, emit_markdown=True)
    except (ExtractError, OSError, ValueError) as exc:
        # ExtractError covers the library's own typed hierarchy. ValueError
        # comes from its path validation (NUL bytes, control chars, etc.);
        # OSError covers filesystem races (missing file, permission denied)
        # from its internal, unguarded ``open()`` call. Widening the catch
        # keeps PreviewFailed a reliable contract for callers.
        raise PreviewFailed(str(exc)) from exc

    markdown = result.content.markdown or ""
    metadata = result.metadata
```

with:

```python
#: Warnung, wenn die Vorschau statt des Markdowns den reinen Text zeigt.
MARKDOWN_FALLBACK_WARNING = "preview: markdown limit exceeded; plain text shown"


def _markdown_limit(exc: Exception) -> bool:
    """Ob ``exc`` eine Markdown-Grenze der Bibliothek ist: der
    Expansionswaechter (``markdown expansion ratio``) oder die Groesse des
    Markdowns (``markdown size``). Der Text selbst laesst sich dann zeigen."""
    return str(getattr(exc, "what", "") or "").startswith("markdown")


def extract_markdown(path: str) -> Dict[str, Any]:
    """Extrahiert ``path`` nach sanitisiertem Markdown.

    PDF gehoert nicht hierher -- es wird im Browser nativ dargestellt.

    Loest das Markdown eine Markdown-Grenze der Bibliothek aus -- eine DOCX
    mit grossen Tabellen ergibt ein Vielfaches ihres Textes, und der
    Expansionswaechter bricht ab --, zeigt die Vorschau den reinen Text statt
    eines Fehlers (spec L7). Der Client escapt ihn wie jedes Markdown.
    """
    kind = preview_kind(path)
    if kind is None or kind == "pdf":
        raise PreviewUnsupported(path)

    import knovas_extract
    from knovas_extract.errors import ExtractError, ResourceExhaustedError
    from knovas_extract.result import Limits

    limits = Limits(max_input_bytes=MAX_INPUT_BYTES, max_text_bytes=MAX_TEXT_BYTES)
    warnings: List[str] = []
    try:
        try:
            result = knovas_extract.extract(path, limits=limits, emit_markdown=True)
            markdown = result.content.markdown or ""
        except ResourceExhaustedError as exc:
            if not _markdown_limit(exc):
                raise
            result = knovas_extract.extract(path, limits=limits)
            markdown = result.content.text or ""
            warnings.append(MARKDOWN_FALLBACK_WARNING)
    except (ExtractError, OSError, ValueError) as exc:
        # ExtractError covers the library's own typed hierarchy. ValueError
        # comes from its path validation (NUL bytes, control chars, etc.);
        # OSError covers filesystem races (missing file, permission denied)
        # from its internal, unguarded ``open()`` call. Widening the catch
        # keeps PreviewFailed a reliable contract for callers.
        raise PreviewFailed(str(exc)) from exc

    metadata = result.metadata
```

and replace:

```python
    return {
        "kind": kind,
        "markdown": markdown,
        "meta": meta,
        "warnings": list(result.warnings),
    }
```

with:

```python
    return {
        "kind": kind,
        "markdown": markdown,
        "meta": meta,
        "warnings": warnings + list(result.warnings),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pf-pytest tests/test_preview_extract.py tests/test_preview_endpoint.py tests/test_preview_rows.py tests/test_preview_anchors.py`

Expected: PASS, apart from the known Windows-only `test_preview_endpoint::test_preview_content_carries_eml_mail_headers`.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasPlatform/components/docbridge_integration/src/web_interface/preview.py \
  KnovasPlatform/components/docbridge_integration/tests/test_preview_extract.py
git commit -F - <<'EOF'
platform: preview shows the plain text when Markdown trips a library limit

The preview asks for Markdown. For a DOCX whose tables make the Markdown
many times its text, the library's expansion guard raised
ResourceExhaustedError and the dialog showed "Vorschau konnte nicht
erzeugt werden". On a Markdown limit ("markdown expansion ratio",
"markdown size") the preview now extracts once more without Markdown and
shows the plain text, with a warning. Other limits still fail as before.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

**Final check of this part:**
- Run the full Connector suite with the rc-pytest env.
- Run `pf-pytest tests` serially against `kc-plan-pg`.
- Run `bash scripts/lib/test_rc_extraction_settings.sh`.

Expected: green, apart from the known Windows-only failures in the plan header. Then run `graphify update .` (AST only).

---

## Part B3 — Version pin, CI and operations (PIN)

This part makes both images and both CI test jobs run one knovas-extract (spec §9), installs the `[rtf]` extra (L4) and adds the Connector's extraction metrics, the `extraction` block of `GET /sync/status` and the System-tab comparison (L5). It covers success criterion 2 (no `pymupdf-layout` in either image; the installed version equals the pin in both images and both CI jobs) and keeps criterion 4 (versions, setting names, classes and counts only in every new log line and metric label).

**Order.** PIN runs after EXT. PIN-4 reads `sync.document_text.docx_text_mode()` (EXT). EXT and INT have already changed other lines of files touched here: the `CMD` lines of both Dockerfiles (E6, §5.2 item 5), the `[html]` sentence in the comment of `KnovasConnector/pyproject.toml` (E7), and user-facing texts (rename sweep). No anchor below overlaps them. Where a step anchors next to text an earlier task may have moved, it says which token to change.

**Pin value.** PIN-1 writes `ARG KNOVAS_EXTRACT_GIT_REF=b5d45404a6df0aa5fb2b934c8ae4efab9fe764a1` in both Dockerfiles. Use the full 40-char SHA of the library PR's merge commit from Task A6; until then use `b5d45404a6df0aa5fb2b934c8ae4efab9fe764a1`. Task A6 replaces it: the section "After Task A6" at the end of this part re-runs PIN-1/PIN-2's checks with the merge commit, and again with an empty ref once 0.4.0a1 is on PyPI. No doc or test names the sha, so each switch is a two-line commit. Until A6, CI tests against b5d4540, which lacks the library PR (spec §4.2–§4.4). Tests that need it (EXT's DOCX layout mode, the fail-soft sentence cap) pass locally against the editable `$LIB`, and in CI only after A6. Nothing is pushed before that (D4).

**PIN-3 (L4 `[rtf]`) has no task of its own.** The extra is added to both images and both floors in PIN-1, and the licence note (striprtf, BSD-3-Clause) is in PIN-1's docs step. A Connector test of an RTF-only MSG body would need a binary fixture: msgforge, the only MSG writer the repository uses (`KnovasConnector/scripts/demo_kanzlei/render.py`), always writes a plain-text body next to the RTF one (`msgforge/_builder.py`, the `0x1000` property). The path being tested is also library code (`extractors/msg.py`, the `rtfBody` branch). The Connector's side of L4 is "the extra is installed", which PIN-1's test (Dockerfile extras and floors) and PIN-2's CI (the test jobs install the images' extras) pin.

**The `outdated` key** of the `extraction` block (contract: `"outdated": int`) is added later by the REX tasks, in `routes/sync_control.py::sync_status`, to the dict that PIN-4 puts there.

**Conventions.** Added `.py` lines stay ASCII (`scripts/check_ascii_py.py`): German UI strings use ae/oe/ue, as the files they go into do. `rc-pytest` and `pf-pytest` are as defined in the plan header. Shell tests run from `$WT` with `bash`. `docker-compose.yml` and `scripts/start.sh` need no change. Neither passes build args, so the pinned defaults apply, and a changed `ARG` default invalidates the cached pip layer, so `up -d --build` installs a new pin.

---

### Task PIN-1: One knovas-extract pin in both Dockerfiles, the `[rtf]` extra, the floors

**Files:**
- Modify: `KnovasConnector/Dockerfile` (builder stage: build args, install step, new version-check step)
- Modify: `KnovasPlatform/components/docbridge_integration/Dockerfile` (dependency install split in two, build args, install step, version-check step)
- Modify: `KnovasConnector/pyproject.toml` (the `knovas-extract` dependency and its comment)
- Modify: `KnovasPlatform/components/docbridge_integration/requirements.txt` (the `knovas-extract` line)
- Modify: `KnovasConnector/docs/configuration.md` ("Docker build" paragraph, format table), `docs/specifications.md` (§1.2, §3), `KnovasConnector/CHANGELOG.md`, `RELEASE_NOTES.md`
- Test: `KnovasConnector/tests/unit/test_image_license_gate.py` (rewritten)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: in each Dockerfile exactly one line `ARG KNOVAS_EXTRACT_VERSION=<version>` and one line `ARG KNOVAS_EXTRACT_GIT_REF=<empty or 40 lowercase hex>`, the format PIN-2's `scripts/ci/check_knovas_extract_pin.sh` parses. Install requirements are `knovas-extract[pdf,ocr,docx,msg,html,rtf,sentences]` (Connector) and `knovas-extract[pdf,ocr,docx,msg,html,rtf,markdown,sentences]` (Platform). Floors are `knovas-extract[pdf,docx,msg,html,rtf,sentences]>=0.4.0a1` (pyproject) and `knovas-extract[pdf,docx,msg,html,rtf,markdown,sentences]>=0.4.0a1` (requirements); `ocr` stays out of the floors, because tesserocr has Linux wheels only. The build args `KNOVAS_EXTRACT_FROM_GIT`, `KNOVAS_EXTRACT_REF` and `KNOVAS_EXTRACT_EXTRAS` are gone. `.github/workflows/ci.yml` still names the old ones until PIN-2, which follows directly; land both in the same push.

- [ ] **Step 1: Write the failing test**

Replace the whole content of `KnovasConnector/tests/unit/test_image_license_gate.py` with:

```python
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
RC_DOCKERFILE = REPO / "KnovasConnector" / "Dockerfile"
PYPROJECT = REPO / "KnovasConnector" / "pyproject.toml"
PLATFORM = REPO / "KnovasPlatform" / "components" / "docbridge_integration"
STEP = "- name: No pymupdf-layout (PolyForm-NC) in the customer image"
LIBRARY_GIT = "git+https://github.com/Seifeddini/knovas-extract-python.git"
#: Spec 9: the Connector's extras; the Platform adds `markdown` (preview).
IMAGES = {
    "connector": (RC_DOCKERFILE, "pdf,ocr,docx,msg,html,rtf,sentences"),
    "platform": (PLATFORM / "Dockerfile", "pdf,ocr,docx,msg,html,rtf,markdown,sentences"),
}
OLD_BUILD_ARGS = ("KNOVAS_EXTRACT_FROM_GIT", "KNOVAS_EXTRACT_REF", "KNOVAS_EXTRACT_EXTRAS")

needs_ci = pytest.mark.skipif(not CI.is_file(), reason="the workflow is not in this checkout")
needs_platform = pytest.mark.skipif(not PLATFORM.is_dir(), reason="the Platform is not in this checkout")


def _step(text: str) -> str:
    start = text.index(STEP)
    indent = text[:start].rsplit("\n", 1)[1]
    rest = text[start + len(STEP):]
    end = rest.find("\n" + indent + "- ")
    return rest if end < 0 else rest[:end]


def _arg(dockerfile: Path, name: str) -> str:
    found = re.findall(rf"^ARG {name}=(.*?)\r?$", dockerfile.read_text(encoding="utf-8"), re.MULTILINE)
    assert len(found) == 1, f"{dockerfile}: ARG {name} is defined {len(found)} times"
    return found[0]


@needs_ci
def test_the_license_step_blocks_the_build():
    step = _step(CI.read_text(encoding="utf-8"))
    assert "pip show pymupdf-layout" in step
    assert "continue-on-error" not in step


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_image_license_gate.py`
Expected: 5 FAIL. `test_the_connector_pins_a_release_and_at_most_a_full_sha` and `test_both_images_pin_the_same_build` fail with `AssertionError: …Dockerfile: ARG KNOVAS_EXTRACT_VERSION is defined 0 times`. Both `test_each_image_installs_the_pin_with_its_extras[…]` fail on the missing `"knovas-extract[…] @ git+https://github.com/Seifeddini/knovas-extract-python.git@${KNOVAS_EXTRACT_GIT_REF}"`. `test_the_floors_carry_the_release_and_the_rtf_extra` fails on the missing floor. `test_the_license_step_blocks_the_build` passes.

- [ ] **Step 3: Write minimal implementation**

3a. `KnovasConnector/Dockerfile`, builder stage. The build args move below the other dependencies, so a pin bump rebuilds only the knovas-extract layers. Replace:

```dockerfile
# knovas-extract is installed from git until the release the RC needs is on
# PyPI. KNOVAS_EXTRACT_REF selects the revision and defaults to a pinned sha
# (knovas-extract-python `main` on 2026-10-02): CI installs this same sha for
# the RC test job and fails when the two differ (.github/workflows/ci.yml,
# KNOVAS_EXTRACT_SHA), so the image and the tests run one extractor. Override
# per build with --build-arg KNOVAS_EXTRACT_REF=<tag or sha>. An empty
# KNOVAS_EXTRACT_FROM_GIT installs from PyPI instead:
#   docker compose build --build-arg KNOVAS_EXTRACT_FROM_GIT= knovas-connector
ARG KNOVAS_EXTRACT_FROM_GIT=github.com/Seifeddini/knovas-extract-python.git
ARG KNOVAS_EXTRACT_REF=11ec1c38053cbbc914207c9e2f24636cde709617
# Extras: [pdf,ocr,docx,msg,html,sentences]. No [markdown]: the RC never asks
# for emit_markdown any more (it cost ~8 s/document and parked mixed PDFs and
# large-table DOCX through the expansion guard), and the PolyForm-NC
# pymupdf-layout it pulled in must not be in the customer image (CI asserts
# `pip show pymupdf-layout` fails in the built image; a failure blocks it).
ARG KNOVAS_EXTRACT_EXTRAS=pdf,ocr,docx,msg,html,sentences
RUN python - <<'PY'
import subprocess
import tomllib

deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]
other = [d for d in deps if not d.lower().startswith("knovas-extract")]
if other:
    subprocess.check_call(["pip", "install", "--no-cache-dir", *other])
PY
RUN if [ -n "${KNOVAS_EXTRACT_FROM_GIT}" ]; then \
      pip install --no-cache-dir \
        "knovas-extract[${KNOVAS_EXTRACT_EXTRAS}] @ git+https://${KNOVAS_EXTRACT_FROM_GIT}@${KNOVAS_EXTRACT_REF}"; \
    else \
      pip install --no-cache-dir \
        "knovas-extract[${KNOVAS_EXTRACT_EXTRAS}]>=0.3"; \
    fi
```

with:

```dockerfile
RUN python - <<'PY'
import subprocess
import tomllib

deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]
other = [d for d in deps if not d.lower().startswith("knovas-extract")]
if other:
    subprocess.check_call(["pip", "install", "--no-cache-dir", *other])
PY

# knovas-extract: one pin for both customer images. Keep both defaults equal
# to those in KnovasPlatform/components/docbridge_integration/Dockerfile
# (scripts/ci/check_knovas_extract_pin.sh fails CI otherwise); the CI test
# jobs install exactly this pin. KNOVAS_EXTRACT_GIT_REF empty: PyPI
# knovas-extract==KNOVAS_EXTRACT_VERSION. Set: that revision from git --
# development, and as the default only a full 40-character commit sha, until
# the version is on PyPI. pyproject.toml carries only the floor (>=0.4.0a1).
# The ARGs come after the other dependencies, so a bump rebuilds only this.
#   docker compose build --build-arg KNOVAS_EXTRACT_GIT_REF=<sha> knovas-connector
#   docker compose build --build-arg KNOVAS_EXTRACT_GIT_REF= knovas-connector   # PyPI
ARG KNOVAS_EXTRACT_VERSION=0.4.0a1
ARG KNOVAS_EXTRACT_GIT_REF=b5d45404a6df0aa5fb2b934c8ae4efab9fe764a1
# Extras: [pdf,ocr,docx,msg,html,rtf,sentences]. No [markdown]: the RC never
# asks for emit_markdown (it cost ~8 s/document and parked mixed PDFs and
# large-table DOCX through the expansion guard). [ocr] is tesserocr
# (in-process Tesseract); [rtf] is striprtf (BSD-3-Clause), without which an
# Outlook mail whose only body is RTF extracts to empty text. The PolyForm-NC
# pymupdf-layout must not be in the customer image (CI asserts
# `pip show pymupdf-layout` fails in the built image; a failure blocks it).
RUN if [ -n "${KNOVAS_EXTRACT_GIT_REF}" ]; then \
      pip install --no-cache-dir \
        "knovas-extract[pdf,ocr,docx,msg,html,rtf,sentences] @ git+https://github.com/Seifeddini/knovas-extract-python.git@${KNOVAS_EXTRACT_GIT_REF}"; \
    else \
      pip install --no-cache-dir \
        "knovas-extract[pdf,ocr,docx,msg,html,rtf,sentences]==${KNOVAS_EXTRACT_VERSION}"; \
    fi
# What was installed goes into the build log; a PyPI install that is not
# exactly KNOVAS_EXTRACT_VERSION fails the build (a git ref is printed only).
RUN python - <<'PY'
import json
import os
from importlib.metadata import distribution

import knovas_extract

want = os.environ["KNOVAS_EXTRACT_VERSION"]
ref = os.environ.get("KNOVAS_EXTRACT_GIT_REF", "")
got = knovas_extract.__version__
url = json.loads(distribution("knovas-extract").read_text("direct_url.json") or "{}")
commit = (url.get("vcs_info") or {}).get("commit_id")
print(f"knovas-extract {got} ({'git ' + commit if commit else 'PyPI'})")
if not ref and got != want:
    raise SystemExit(f"knovas-extract {got} installed, KNOVAS_EXTRACT_VERSION is {want}")
PY
```

(Build args reach `RUN` steps, heredocs included, as environment variables. This was checked with Docker 28 / BuildKit, including `--build-arg KNOVAS_EXTRACT_GIT_REF=` giving an empty value. `import knovas_extract` loads no PDF, OCR or libmagic code, since those are imported lazily, so the check runs in the slim builder stage.)

3b. `KnovasPlatform/components/docbridge_integration/Dockerfile`. Replace:

```dockerfile
# knovas-extract >=0.3 (PDF-OCR) ist noch nicht auf PyPI veroeffentlicht --
# dort endet die Reihe bei 0.2.0. Es kommt deshalb per Default aus git,
# analog zu dem, was KnovasConnector/Dockerfile in 88d0c3c bereits tut.
# Sobald 0.3.0 auf PyPI liegt, mit leerem Build-Arg bauen:
#   docker compose build --build-arg KNOVAS_EXTRACT_FROM_GIT= docbridge-web
ARG KNOVAS_EXTRACT_FROM_GIT=github.com/Seifeddini/knovas-extract-python.git@main

# Install Python dependencies
# Extras: `ocr` (tesserocr, knovas-extract >= 0.4; an older ref without the
# extra only warns) for the admin upload, `markdown` is still needed by the
# DOCX/EML/MSG preview (web_interface/preview.py) -- the upload path itself
# never requests markdown (emit_markdown=False).
RUN grep -v '^knovas-extract' requirements.txt > /tmp/requirements-other.txt \
    && pip install --no-cache-dir -r /tmp/requirements-other.txt \
    && if [ -n "${KNOVAS_EXTRACT_FROM_GIT}" ]; then \
         pip install --no-cache-dir \
           "knovas-extract[pdf,ocr,docx,msg,html,markdown,sentences] @ git+https://${KNOVAS_EXTRACT_FROM_GIT}"; \
       else \
         pip install --no-cache-dir "$(grep '^knovas-extract' requirements.txt)"; \
       fi
```

with:

```dockerfile
# Install Python dependencies: everything but knovas-extract first, so a pin
# bump below rebuilds only the knovas-extract layer.
RUN grep -v '^knovas-extract' requirements.txt > /tmp/requirements-other.txt \
    && pip install --no-cache-dir -r /tmp/requirements-other.txt

# knovas-extract: one pin for both customer images. Keep both defaults equal
# to those in KnovasConnector/Dockerfile (scripts/ci/check_knovas_extract_pin.sh
# fails CI otherwise); the CI test job installs exactly this pin.
# KNOVAS_EXTRACT_GIT_REF empty: PyPI knovas-extract==KNOVAS_EXTRACT_VERSION.
# Set: that revision from git -- development, and as the default only a full
# 40-character commit sha, until the version is on PyPI. requirements.txt
# carries only the floor (>=0.4.0a1) for an install outside the image.
#   docker compose build --build-arg KNOVAS_EXTRACT_GIT_REF=<sha> docbridge-web
#   docker compose build --build-arg KNOVAS_EXTRACT_GIT_REF= docbridge-web   # PyPI
# Extras: `ocr` (tesserocr) for the admin upload, `rtf` (striprtf) for Outlook
# mails whose only body is RTF, `markdown` for the DOCX/EML/MSG preview
# (web_interface/preview.py) -- the upload path itself never requests
# markdown (emit_markdown=False). Never `pdf-markdown`: it pulls the
# PolyForm-NC pymupdf-layout, which CI refuses in the built image.
ARG KNOVAS_EXTRACT_VERSION=0.4.0a1
ARG KNOVAS_EXTRACT_GIT_REF=b5d45404a6df0aa5fb2b934c8ae4efab9fe764a1
RUN if [ -n "${KNOVAS_EXTRACT_GIT_REF}" ]; then \
      pip install --no-cache-dir \
        "knovas-extract[pdf,ocr,docx,msg,html,rtf,markdown,sentences] @ git+https://github.com/Seifeddini/knovas-extract-python.git@${KNOVAS_EXTRACT_GIT_REF}"; \
    else \
      pip install --no-cache-dir \
        "knovas-extract[pdf,ocr,docx,msg,html,rtf,markdown,sentences]==${KNOVAS_EXTRACT_VERSION}"; \
    fi
# What was installed goes into the build log; a PyPI install that is not
# exactly KNOVAS_EXTRACT_VERSION fails the build (a git ref is printed only).
RUN python - <<'PY'
import json
import os
from importlib.metadata import distribution

import knovas_extract

want = os.environ["KNOVAS_EXTRACT_VERSION"]
ref = os.environ.get("KNOVAS_EXTRACT_GIT_REF", "")
got = knovas_extract.__version__
url = json.loads(distribution("knovas-extract").read_text("direct_url.json") or "{}")
commit = (url.get("vcs_info") or {}).get("commit_id")
print(f"knovas-extract {got} ({'git ' + commit if commit else 'PyPI'})")
if not ref and got != want:
    raise SystemExit(f"knovas-extract {got} installed, KNOVAS_EXTRACT_VERSION is {want}")
PY
```

3c. `KnovasConnector/pyproject.toml`. Replace the dependency line

```toml
    "knovas-extract[pdf,docx,msg,html,sentences]>=0.2",
```

with:

```toml
    # [rtf]: striprtf, for Outlook mails whose only body is RTF. A floor
    # only: the images and CI install the exact pin (Dockerfile ARG
    # KNOVAS_EXTRACT_VERSION / KNOVAS_EXTRACT_GIT_REF).
    "knovas-extract[pdf,docx,msg,html,rtf,sentences]>=0.4.0a1",
```

In the comment above it, replace `see Dockerfile KNOVAS_EXTRACT_EXTRAS.` with `see the Dockerfile.`. If E7 already reworded that comment, change only the token `Dockerfile KNOVAS_EXTRACT_EXTRAS` and keep E7's sentences. The test refuses the old name in this file.

3d. `KnovasPlatform/components/docbridge_integration/requirements.txt`. Replace:

```text
# Document extraction (sentences, sections, tables for secured upload)
knovas-extract[pdf,docx,msg,markdown,html,sentences]>=0.2
```

with:

```text
# Document extraction (sentences, sections, tables for secured upload;
# markdown for the preview, rtf for Outlook mails whose only body is RTF).
# A floor only: the image and CI install the exact pin, with `ocr` on top
# (Dockerfile ARG KNOVAS_EXTRACT_VERSION / KNOVAS_EXTRACT_GIT_REF).
knovas-extract[pdf,docx,msg,html,rtf,markdown,sentences]>=0.4.0a1
```

(The Dockerfile's `grep -v '^knovas-extract'` still drops this line from the image's first install.)

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_image_license_gate.py`
Expected: PASS (6 passed).

Run: `cd $WT && grep -rnE 'KNOVAS_EXTRACT_(REF|FROM_GIT|SHA|EXTRAS)\b' . --exclude-dir=.git --exclude-dir=superpowers --exclude-dir=__pycache__ --exclude-dir=graphify-out`
Expected: only `.github/workflows/ci.yml` (rewired in PIN-2), `KnovasConnector/CHANGELOG.md` (the released 0.2.0 line, and after Step 5 the new bullet that names the replaced args) and `OLD_BUILD_ARGS` in the test. `docs/superpowers/` records stay as written.

Image check (needs Docker and network, a few minutes per image). It is verified again in CI (PIN-2) and by VER:

```bash
cd $WT
docker build --progress=plain -t knovas-connector:pin Knovas Connector 2>&1 | grep -E "knovas-extract 0\.4|ERROR"
docker build --progress=plain -t docbridge-web:pin KnovasPlatform/components/docbridge_integration 2>&1 | grep -E "knovas-extract 0\.4|ERROR"
for image in knovas-connector:pin docbridge-web:pin; do
  docker run --rm "$image" pip show striprtf | head -2
  docker run --rm "$image" pip show pymupdf-layout >/dev/null 2>&1 && echo "pymupdf-layout in $image" || echo "pymupdf-layout absent in $image"
done
docker image rm knovas-connector:pin docbridge-web:pin
```

Expected: `knovas-extract 0.4.0a1 (git b5d45404a6df0aa5fb2b934c8ae4efab9fe764a1)` in both build logs, `Name: striprtf` for both images, `pymupdf-layout absent in …` twice.

- [ ] **Step 5: Docs (licence note for L4, the pin)**

5a. `KnovasConnector/docs/configuration.md`. Replace the paragraph

```markdown
**Docker build:** the Dockerfile installs `knovas-extract` from git. `KNOVAS_EXTRACT_REF` selects the revision and defaults to a pinned sha (`11ec1c38053cbbc914207c9e2f24636cde709617`, knovas-extract 0.4.0a1) — the same sha CI installs for the tests (`KNOVAS_EXTRACT_SHA` in `.github/workflows/ci.yml`; the CI job fails when the two differ). Override per build: `docker compose build --build-arg KNOVAS_EXTRACT_REF=<tag or sha> knovas-connector`. `--build-arg KNOVAS_EXTRACT_FROM_GIT=` installs from PyPI instead. The image sets `TESSDATA_PREFIX` and `OMP_THREAD_LIMIT=1` and ships the `deu`, `eng`, `fra` and `ita` models; extraction performs no network I/O.
```

with:

```markdown
**Docker build:** the Dockerfile installs one pinned `knovas-extract`, the same as the Platform image: `ARG KNOVAS_EXTRACT_VERSION` (`0.4.0a1`) and `ARG KNOVAS_EXTRACT_GIT_REF`. With an empty ref the build installs `knovas-extract==<version>` from PyPI; with a ref, that revision from git — until 0.4.0a1 is on PyPI the default is a full commit sha of the library. The build log names the installed version and commit, and a PyPI install that is not exactly the version fails the build. Extras: `pdf,ocr,docx,msg,html,rtf,sentences` (`rtf` reads Outlook mails whose only body is RTF). Override per build: `docker compose build --build-arg KNOVAS_EXTRACT_GIT_REF=<sha> knovas-connector`, or `--build-arg KNOVAS_EXTRACT_GIT_REF=` for PyPI. A new pin changes the Dockerfile, so the next `docker compose up -d --build` installs it (Docker's layer cache cannot keep an older build). The image sets `TESSDATA_PREFIX` and `OMP_THREAD_LIMIT=1` and ships the `deu`, `eng`, `fra` and `ita` models; extraction performs no network I/O.
```

In the format table of the same file, replace the row `| `.msg` | `extract-msg` (subject → transmission title) |` with `| `.msg` | `extract-msg` (subject → transmission title); a body that exists only as RTF through `striprtf` (`[rtf]` extra) |`. In the sentence above the table, replace the dead link target `(https://github.com/knovas/knovas-extract-python)` with `(https://github.com/Seifeddini/knovas-extract-python)`.

5b. `docs/specifications.md` §1.2. Replace:

```markdown
- `flask`, `gunicorn`, `requests`, `cryptography`, `jsonschema`, `prometheus-client`
- `python-docx` — `.docx` parsing
- `pymupdf` — PDF text extraction
- `extract-msg` — Outlook `.msg` email parsing
```

with:

```markdown
- `flask`, `gunicorn`, `requests`, `cryptography`, `jsonschema`, `prometheus-client`
- `knovas-extract` — document extraction (PDF with OCR, DOCX, EML, MSG, text), one pinned version for both images; its extras and their licences: [3. Joint deployment requirements](#3-joint-deployment-requirements)
```

In §3, directly after the bullet that starts with `- **Logs and metrics.** Both components produce structured logs via `docker compose logs`.`, insert:

```markdown
- **Document extraction and third-party licences.** Both images install the same pinned `knovas-extract` (`ARG KNOVAS_EXTRACT_VERSION` / `ARG KNOVAS_EXTRACT_GIT_REF` in `KnovasConnector/Dockerfile` and `KnovasPlatform/components/docbridge_integration/Dockerfile`). Its extras bring these packages into the images (licences as listed in the library's `NOTICE`):

  | Extra | Packages | Licence |
  | --- | --- | --- |
  | `pdf` | PyMuPDF | AGPL-3.0 |
  | `ocr` | tesserocr (bundles Tesseract, Leptonica and image libraries), numpy, Pillow | MIT (Tesseract Apache-2.0, Leptonica BSD-2-Clause), BSD-3-Clause, MIT-CMU |
  | `docx` | python-docx, mammoth | MIT, BSD-2-Clause |
  | `msg` | extract-msg | GPL-3.0 |
  | `html` | selectolax | MIT |
  | `rtf` | striprtf (pure Python; Outlook mails whose only body is RTF) | BSD-3-Clause |
  | `sentences` | pysbd | MIT |
  | `markdown` (Platform only, document preview) | markdownify, selectolax | MIT |
  | (core) | python-magic, defusedxml, chardet | MIT, PSF-2.0, LGPL-2.1 |

  `pymupdf-layout` (PolyForm Noncommercial) must not be in either image; CI checks it.
```

5c. `KnovasConnector/CHANGELOG.md`, section `### 0.3.0`. Replace the bullet

```markdown
- Dockerfile `KNOVAS_EXTRACT_REF` defaults to the pinned sha CI tests (`11ec1c38…`, knovas-extract 0.4.0a1). Its `[pdf]` extra is clean, so the CI check that `pymupdf-layout` is absent from the image now blocks the build.
```

with:

```markdown
- Dockerfile: `knovas-extract` comes from one pin shared with the Platform image, `ARG KNOVAS_EXTRACT_VERSION` (`0.4.0a1`) and `ARG KNOVAS_EXTRACT_GIT_REF` (a full commit sha of the library until 0.4.0a1 is on PyPI, then empty: PyPI). They replace `KNOVAS_EXTRACT_FROM_GIT`, `KNOVAS_EXTRACT_REF` and `KNOVAS_EXTRACT_EXTRAS`; the PyPI fallback `>=0.3`, which could not resolve, is gone. The build prints the installed version and fails when a PyPI install is not the pin. New extra `[rtf]` (striprtf, BSD-3-Clause): an Outlook mail whose only body is RTF used to extract to empty text. `pyproject.toml` carries the floor `>=0.4.0a1`. The `[pdf]` extra of 0.4 is clean, so the CI check that `pymupdf-layout` is absent from the image blocks the build.
```

5d. `RELEASE_NOTES.md`. Directly below the line `# Unreleased` (and its blank line), insert:

```markdown
## Extraktor: knovas-extract 0.4.0a1, eine Version fuer beide Seiten

- **Plattform und Knovas Connector installieren dieselbe, fest gepinnte
  Version von knovas-extract** (`ARG KNOVAS_EXTRACT_VERSION` /
  `KNOVAS_EXTRACT_GIT_REF` in beiden Dockerfiles). Bisher kam der Extraktor
  aus dem beweglichen `main` des Bibliotheks-Repositorys, und ein Server mit
  Docker-Cache behielt einen alten Stand. Der naechste `./scripts/start.sh`
  baut beide Images neu.
- **Outlook-Mails, deren Text nur als RTF vorliegt**, werden gelesen (bisher
  leerer Text).

```

If an EXT task already opened an extraction section under `# Unreleased`, put these two bullets into it instead of a new heading.

- [ ] **Step 6: Commit**

```bash
cd $WT
git add KnovasConnector/Dockerfile KnovasPlatform/components/docbridge_integration/Dockerfile \
  KnovasConnector/pyproject.toml KnovasPlatform/components/docbridge_integration/requirements.txt \
  KnovasConnector/tests/unit/test_image_license_gate.py KnovasConnector/docs/configuration.md \
  docs/specifications.md KnovasConnector/CHANGELOG.md RELEASE_NOTES.md
git commit -F - <<'EOF'
rc+platform: one knovas-extract pin for both images, with [rtf]

Both images installed knovas-extract from the moving git main, so a host
with Docker's layer cache kept whatever main was at its first build, and the
PyPI fallbacks were broken (>=0.3 cannot resolve; >=0.2 installs 0.2.0,
whose [pdf] pulls the PolyForm-NC pymupdf-layout). Both Dockerfiles now take
ARG KNOVAS_EXTRACT_VERSION (0.4.0a1) and ARG KNOVAS_EXTRACT_GIT_REF (the
library's commit until the release is on PyPI, then empty), print what they
installed and fail when a PyPI install is not the pin. The build args sit
after the other dependencies, so a bump reinstalls only knovas-extract.

[rtf] (striprtf, BSD-3-Clause) joins both images: without it an Outlook
mail whose only body is RTF extracts to empty text, because the library
swallows the import error. pyproject.toml and requirements.txt carry the
floor >=0.4.0a1. The licence table in docs/specifications.md lists the
extras of both images.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task PIN-2: CI holds both test jobs and both images to the pin

**Files:**
- Create: `scripts/ci/check_knovas_extract_pin.sh`, `scripts/ci/test_check_knovas_extract_pin.sh`, `scripts/ci/assert_knovas_extract_pin.py`
- Modify: `.github/workflows/ci.yml` (`knovas-platform` job: pin step, install step, two image steps; `knovas-connector` job: job `env`, pin step, install step, image steps)
- Modify: `KnovasConnector/tests/unit/test_image_license_gate.py`
- Modify: `KnovasConnector/docs/local-commands.md`, `KnovasConnector/docs/configuration.md`, `KnovasConnector/CHANGELOG.md`
- Test: `scripts/ci/test_check_knovas_extract_pin.sh`, `KnovasConnector/tests/unit/test_knovas_extract_pin_scripts.py`, `KnovasConnector/tests/unit/test_image_license_gate.py`

**Interfaces:**
- Consumes: PIN-1's ARG lines (one `ARG KNOVAS_EXTRACT_VERSION=` and one `ARG KNOVAS_EXTRACT_GIT_REF=` per Dockerfile).
- Produces:
  - `bash scripts/ci/check_knovas_extract_pin.sh [CONNECTOR_DOCKERFILE PLATFORM_DOCKERFILE]`. On agreement, stdout is exactly `KNOVAS_EXTRACT_VERSION=<v>` and `KNOVAS_EXTRACT_GIT_REF=<sha or empty>` (for `>> "$GITHUB_ENV"` or `eval`), stderr names the pin, and the exit code is 0. Otherwise it exits 1 with a message and empty stdout.
  - `python scripts/ci/assert_knovas_extract_pin.py` reads those two variables and checks the installed library. Exit 0 when it is the pin, 1 when it is not, 2 when no pin is set. It also runs from stdin inside an image.
  - CI step names `No pymupdf-layout (PolyForm-NC) in the Connector image` and `No pymupdf-layout (PolyForm-NC) in the Platform image`.

Commits: one per step group (2A, 2B, 2C).

#### Step group 2A: the pin check script

- [ ] **Step 1: Write the failing test**

Create `scripts/ci/test_check_knovas_extract_pin.sh`:

```bash
#!/usr/bin/env bash
# The two customer images and both CI test jobs must run one knovas-extract.
# check_knovas_extract_pin.sh reads the ARG defaults of both Dockerfiles; this
# pins what it accepts, what it refuses, and what it prints.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHECK="$ROOT_DIR/scripts/ci/check_knovas_extract_pin.sh"
cd "$ROOT_DIR"

fail() { echo "FAIL: $*" >&2; exit 1; }

[[ -f "$CHECK" ]] || fail "$CHECK is missing"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
SHA=b5d45404a6df0aa5fb2b934c8ae4efab9fe764a1
OTHER_SHA=1c42621aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
RC="$WORKDIR/rc.Dockerfile"
PF="$WORKDIR/pf.Dockerfile"

dockerfile() {  # path version ref
  printf 'FROM python:3.12-slim\nARG KNOVAS_EXTRACT_VERSION=%s\nARG KNOVAS_EXTRACT_GIT_REF=%s\nRUN true\n' \
    "$2" "$3" > "$1"
}

# refused <what> <expected message part>: the check must exit non-zero with
# a message naming the problem, and print nothing on stdout.
refused() {
  if bash "$CHECK" "$RC" "$PF" >"$WORKDIR/out" 2>"$WORKDIR/err"; then
    fail "$1 was accepted"
  fi
  grep -qF "$2" "$WORKDIR/err" || fail "$1: the message lacks '$2': $(cat "$WORKDIR/err")"
  [[ ! -s "$WORKDIR/out" ]] || fail "$1: stdout must stay empty, got: $(cat "$WORKDIR/out")"
}

# --- The repository itself: one pin, printed as two KEY=value lines --------
out="$(bash "$CHECK" 2>/dev/null)" || fail "the two Dockerfiles of this repository disagree"
[[ "$(printf '%s\n' "$out" | wc -l)" -eq 2 ]] || fail "stdout must be exactly two lines: $out"
grep -qE '^KNOVAS_EXTRACT_VERSION=[0-9]' <<<"$out" || fail "no version printed: $out"
grep -qE '^KNOVAS_EXTRACT_GIT_REF=([0-9a-f]{40})?$' <<<"$out" || fail "no ref printed: $out"

# --- Equal pins pass, from git and from PyPI -------------------------------
dockerfile "$RC" 0.4.0a1 "$SHA"
dockerfile "$PF" 0.4.0a1 "$SHA"
out="$(bash "$CHECK" "$RC" "$PF" 2>"$WORKDIR/err")" || fail "equal git pins were refused"
[[ "$out" == "KNOVAS_EXTRACT_VERSION=0.4.0a1"$'\n'"KNOVAS_EXTRACT_GIT_REF=$SHA" ]] \
  || fail "unexpected output for a git pin: $out"
grep -qF "knovas-extract pin: 0.4.0a1 from git $SHA" "$WORKDIR/err" \
  || fail "the pin is not named on stderr: $(cat "$WORKDIR/err")"
dockerfile "$RC" 0.4.0a1 ""
dockerfile "$PF" 0.4.0a1 ""
out="$(bash "$CHECK" "$RC" "$PF" 2>/dev/null)" || fail "equal PyPI pins were refused"
[[ "$out" == "KNOVAS_EXTRACT_VERSION=0.4.0a1"$'\n'"KNOVAS_EXTRACT_GIT_REF=" ]] \
  || fail "unexpected output for a PyPI pin: $out"

# --- A Windows checkout (CRLF) reads the same pin --------------------------
printf 'ARG KNOVAS_EXTRACT_VERSION=0.4.0a1\r\nARG KNOVAS_EXTRACT_GIT_REF=%s\r\n' "$SHA" > "$RC"
dockerfile "$PF" 0.4.0a1 "$SHA"
out="$(bash "$CHECK" "$RC" "$PF" 2>/dev/null)" || fail "a CRLF Dockerfile was read differently"
[[ "$out" == "KNOVAS_EXTRACT_VERSION=0.4.0a1"$'\n'"KNOVAS_EXTRACT_GIT_REF=$SHA" ]] \
  || fail "CR left in the output: $(od -c <<<"$out" | head -3)"

# --- Refusals ----------------------------------------------------------------
dockerfile "$RC" 0.4.0a1 "$SHA"
dockerfile "$PF" 0.4.0a2 "$SHA"
refused "two versions" "pin different knovas-extract builds"
dockerfile "$PF" 0.4.0a1 ""
refused "git in one image and PyPI in the other" "pin different knovas-extract builds"
dockerfile "$PF" 0.4.0a1 "$OTHER_SHA"
refused "two git revisions" "pin different knovas-extract builds"
dockerfile "$RC" 0.4.0a1 main
dockerfile "$PF" 0.4.0a1 main
refused "a branch as the default ref" "full 40-character commit sha"
dockerfile "$RC" "" ""
dockerfile "$PF" "" ""
refused "an empty version" "is not a release version"
dockerfile "$RC" ">=0.4" ""
dockerfile "$PF" ">=0.4" ""
refused "a version range" "is not a release version"
printf 'FROM python:3.12-slim\nARG KNOVAS_EXTRACT_GIT_REF=\n' > "$RC"
dockerfile "$PF" 0.4.0a1 ""
refused "a Dockerfile without the version ARG" "no 'ARG KNOVAS_EXTRACT_VERSION=' line"
{ cat "$PF"; echo "ARG KNOVAS_EXTRACT_GIT_REF=$SHA"; } > "$RC"
refused "a ref defined twice" "appears more than once"
rm -f "$RC"
refused "a missing Dockerfile" "no such file"

echo "check_knovas_extract_pin smoke OK"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd $WT && bash scripts/ci/test_check_knovas_extract_pin.sh`
Expected: FAIL with `FAIL: …/scripts/ci/check_knovas_extract_pin.sh is missing`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/ci/check_knovas_extract_pin.sh`:

```bash
#!/usr/bin/env bash
# One knovas-extract for both customer images and both CI test jobs.
#
# Reads the defaults of ARG KNOVAS_EXTRACT_VERSION and ARG
# KNOVAS_EXTRACT_GIT_REF from the Knovas Connector's and the Platform's
# Dockerfile, fails when the two files disagree or a value is neither a
# release version nor (for the ref) empty or a full commit sha, and prints
# the pin as KEY=value lines on stdout -- for $GITHUB_ENV or eval:
#
#   bash scripts/ci/check_knovas_extract_pin.sh >> "$GITHUB_ENV"
#   eval "$(bash scripts/ci/check_knovas_extract_pin.sh)"
#
# Usage: check_knovas_extract_pin.sh [CONNECTOR_DOCKERFILE PLATFORM_DOCKERFILE]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RC_FILE="${1:-$ROOT_DIR/KnovasConnector/Dockerfile}"
PF_FILE="${2:-$ROOT_DIR/KnovasPlatform/components/docbridge_integration/Dockerfile}"
VERSION_RE='^[0-9]+(\.[0-9]+)*((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?$'
SHA_RE='^[0-9a-f]{40}$'

fail() { echo "check_knovas_extract_pin: $*" >&2; exit 1; }

# The default of `ARG <name>=` in <file>: exactly one such line, CR stripped
# (a Windows checkout may carry CRLF).
arg_default() {
  local file="$1" name="$2" lines
  [[ -f "$file" ]] || fail "$file: no such file"
  lines="$(tr -d '\r' < "$file" | grep -E "^ARG ${name}=" || true)"
  [[ -n "$lines" ]] || fail "$file: no 'ARG ${name}=' line"
  [[ "$(printf '%s\n' "$lines" | wc -l)" -eq 1 ]] || fail "$file: 'ARG ${name}=' appears more than once"
  printf '%s' "${lines#"ARG ${name}="}"
}

rc_version="$(arg_default "$RC_FILE" KNOVAS_EXTRACT_VERSION)"
rc_ref="$(arg_default "$RC_FILE" KNOVAS_EXTRACT_GIT_REF)"
pf_version="$(arg_default "$PF_FILE" KNOVAS_EXTRACT_VERSION)"
pf_ref="$(arg_default "$PF_FILE" KNOVAS_EXTRACT_GIT_REF)"

if [[ "$rc_version" != "$pf_version" || "$rc_ref" != "$pf_ref" ]]; then
  {
    echo "check_knovas_extract_pin: the two images pin different knovas-extract builds:"
    echo "  $RC_FILE: KNOVAS_EXTRACT_VERSION=$rc_version KNOVAS_EXTRACT_GIT_REF=$rc_ref"
    echo "  $PF_FILE: KNOVAS_EXTRACT_VERSION=$pf_version KNOVAS_EXTRACT_GIT_REF=$pf_ref"
    echo "Set the same defaults in both Dockerfiles; the CI test jobs install exactly that pin."
  } >&2
  exit 1
fi
[[ "$rc_version" =~ $VERSION_RE ]] \
  || fail "KNOVAS_EXTRACT_VERSION '$rc_version' is not a release version (e.g. 0.4.0a1)"
[[ -z "$rc_ref" || "$rc_ref" =~ $SHA_RE ]] \
  || fail "KNOVAS_EXTRACT_GIT_REF '$rc_ref' is neither empty (PyPI) nor a full 40-character commit sha"

if [[ -n "$rc_ref" ]]; then source_text="git $rc_ref"; else source_text="PyPI"; fi
echo "knovas-extract pin: $rc_version from $source_text (both Dockerfiles agree)" >&2
printf 'KNOVAS_EXTRACT_VERSION=%s\nKNOVAS_EXTRACT_GIT_REF=%s\n' "$rc_version" "$rc_ref"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd $WT && bash scripts/ci/test_check_knovas_extract_pin.sh && bash scripts/ci/check_knovas_extract_pin.sh`
Expected: `check_knovas_extract_pin smoke OK`, then `knovas-extract pin: 0.4.0a1 from git b5d45404a6df0aa5fb2b934c8ae4efab9fe764a1 (both Dockerfiles agree)` on stderr and the two `KEY=value` lines on stdout. In Git Bash the CRLF case cannot fail, because command substitution there drops CRs anyway; on Linux CI it guards the `tr -d '\r'`.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add --chmod=+x scripts/ci/check_knovas_extract_pin.sh scripts/ci/test_check_knovas_extract_pin.sh
git commit -F - <<'EOF'
ci: check that both Dockerfiles pin the same knovas-extract

scripts/ci/check_knovas_extract_pin.sh reads ARG KNOVAS_EXTRACT_VERSION and
ARG KNOVAS_EXTRACT_GIT_REF from the Connector's and the Platform's
Dockerfile, fails when they differ or the ref is not a full commit sha, and
prints the pin as KEY=value lines, so the CI test jobs can install exactly
what the images ship. The smoke test pins acceptance (git and PyPI pins,
CRLF), every refusal and the output format.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

#### Step group 2B: the installed-pin assertion

- [ ] **Step 1: Write the failing test**

Create `KnovasConnector/tests/unit/test_knovas_extract_pin_scripts.py`:

```python
"""scripts/ci/assert_knovas_extract_pin.py: CI holds both test jobs and both
images to the pin that check_knovas_extract_pin.sh prints.

The script runs here against the library this suite runs on, so the tests
hold in CI (a git install of the pin) and in a local editable install alike.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "ci" / "assert_knovas_extract_pin.py"

pytestmark = pytest.mark.skipif(
    not (REPO / "scripts" / "lib").is_dir(), reason="not a KnovasComponents checkout"
)


def _installed() -> tuple[str, str]:
    import knovas_extract

    url = json.loads(distribution("knovas-extract").read_text("direct_url.json") or "{}")
    return knovas_extract.__version__, (url.get("vcs_info") or {}).get("commit_id") or ""


def _run(**pin: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KNOVAS_EXTRACT_")}
    env.update(pin)
    return subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=120)


def test_the_installed_library_passes_as_its_own_pin():
    version, commit = _installed()
    done = _run(KNOVAS_EXTRACT_VERSION=version, KNOVAS_EXTRACT_GIT_REF=commit)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith(f"knovas-extract {version} (")


def test_another_version_fails_and_names_both():
    version, commit = _installed()
    done = _run(KNOVAS_EXTRACT_VERSION=version + ".post9", KNOVAS_EXTRACT_GIT_REF=commit)
    assert done.returncode == 1
    assert f"version {version}, the pin is {version}.post9" in done.stderr


def test_another_git_revision_fails():
    version, commit = _installed()
    other = "e" * 40 if commit == "f" * 40 else "f" * 40
    done = _run(KNOVAS_EXTRACT_VERSION=version, KNOVAS_EXTRACT_GIT_REF=other)
    assert done.returncode == 1
    assert f"the pin is {other}" in done.stderr


def test_without_a_pin_it_is_a_usage_error():
    done = _run()
    assert done.returncode == 2
    assert "KNOVAS_EXTRACT_VERSION is not set" in done.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_knovas_extract_pin_scripts.py`
Expected: 4 FAIL. Python exits 2 with `can't open file '…/scripts/ci/assert_knovas_extract_pin.py': [Errno 2] No such file or directory`, so the return-code and message asserts fail.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/ci/assert_knovas_extract_pin.py`:

```python
"""Fail unless the installed knovas-extract is exactly the pin.

The pin comes from the environment, as scripts/ci/check_knovas_extract_pin.sh
prints it: KNOVAS_EXTRACT_VERSION, and KNOVAS_EXTRACT_GIT_REF (empty for a
release from PyPI). CI runs this in both test jobs after their requirements
and inside both built images (``docker run -i ... python - < this file``),
so the tests and the images are held to the same two values.

Exit codes: 0 the pin is installed, 1 it is not, 2 no pin in the environment.
Prints the version and the git revision only.
"""
from __future__ import annotations

import json
import os
import sys
from importlib.metadata import PackageNotFoundError, distribution


def main() -> int:
    want = os.environ.get("KNOVAS_EXTRACT_VERSION", "").strip()
    ref = os.environ.get("KNOVAS_EXTRACT_GIT_REF", "").strip()
    if not want:
        print("KNOVAS_EXTRACT_VERSION is not set (scripts/ci/check_knovas_extract_pin.sh prints it)",
              file=sys.stderr)
        return 2
    try:
        dist = distribution("knovas-extract")
    except PackageNotFoundError:
        print("knovas-extract is not installed", file=sys.stderr)
        return 1
    import knovas_extract

    got = getattr(knovas_extract, "__version__", None)
    url = json.loads(dist.read_text("direct_url.json") or "{}")
    commit = (url.get("vcs_info") or {}).get("commit_id") or ""
    print(f"knovas-extract {got} ({'git ' + commit if commit else 'no git revision'})")
    problems = []
    if got != want:
        problems.append(f"version {got}, the pin is {want}")
    if commit != ref:
        problems.append(f"git revision {commit or 'none'}, the pin is {ref or 'none (PyPI)'}")
    for problem in problems:
        print(f"knovas-extract is not the pin: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_knovas_extract_pin_scripts.py`
Expected: PASS (4 passed). Locally the editable `$LIB` install has no git revision; in CI it is the git install of the pin. Both pass, because the test compares the script against what is installed.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add scripts/ci/assert_knovas_extract_pin.py KnovasConnector/tests/unit/test_knovas_extract_pin_scripts.py
git commit -F - <<'EOF'
ci: assert that the installed knovas-extract is the pin

One script for the four places CI must check: both test jobs after their
requirements (a later install could replace the pinned build) and inside
both built images (fed on stdin to the image's python). It compares
knovas_extract.__version__ and the git commit from direct_url.json with
KNOVAS_EXTRACT_VERSION / KNOVAS_EXTRACT_GIT_REF and prints versions only.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

#### Step group 2C: CI wiring, both license gates, the tesserocr smoke

- [ ] **Step 1: Write the failing test**

In `KnovasConnector/tests/unit/test_image_license_gate.py` replace

```python
STEP = "- name: No pymupdf-layout (PolyForm-NC) in the customer image"
```

with

```python
LICENSE_STEPS = (
    "- name: No pymupdf-layout (PolyForm-NC) in the Connector image",
    "- name: No pymupdf-layout (PolyForm-NC) in the Platform image",
)
PIN_STEP = 'bash scripts/ci/check_knovas_extract_pin.sh >> "$GITHUB_ENV"'
ASSERT_SCRIPT = "scripts/ci/assert_knovas_extract_pin.py"
```

replace

```python
def _step(text: str) -> str:
    start = text.index(STEP)
    indent = text[:start].rsplit("\n", 1)[1]
    rest = text[start + len(STEP):]
```

with

```python
def _step(text: str, step: str) -> str:
    start = text.index(step)
    indent = text[:start].rsplit("\n", 1)[1]
    rest = text[start + len(step):]
```

and replace

```python
@needs_ci
def test_the_license_step_blocks_the_build():
    step = _step(CI.read_text(encoding="utf-8"))
    assert "pip show pymupdf-layout" in step
    assert "continue-on-error" not in step
```

with

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_image_license_gate.py`
Expected: 3 FAIL. Both `test_the_license_step_blocks_the_build_of_each_image[…]` fail with `ValueError: substring not found`; `test_both_test_jobs_and_both_images_are_held_to_the_pin` fails with `assert 0 == 2`.

- [ ] **Step 3: Write minimal implementation**

All edits are in `.github/workflows/ci.yml`.

(1) `knovas-platform` job. Replace:

```yaml
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r components/docbridge_integration/requirements.txt
```

with:

```yaml
      # One knovas-extract for both images and both test jobs: the check fails
      # when the two Dockerfiles' ARG defaults differ and hands the pin
      # (KNOVAS_EXTRACT_VERSION, KNOVAS_EXTRACT_GIT_REF) to the next steps.
      # Its own test runs first.
      - name: knovas-extract pin
        working-directory: .
        run: |
          bash scripts/ci/test_check_knovas_extract_pin.sh
          bash scripts/ci/check_knovas_extract_pin.sh >> "$GITHUB_ENV"

      - name: Install dependencies
        # The pinned knovas-extract first, with the Platform image's extras:
        # the floor in requirements.txt (>=0.4.0a1) is then satisfied and left
        # alone. The last line fails the job if anything replaced it.
        run: |
          python -m pip install --upgrade pip
          extras="pdf,ocr,docx,msg,html,rtf,markdown,sentences"
          if [ -n "$KNOVAS_EXTRACT_GIT_REF" ]; then
            pip install "knovas-extract[${extras}] @ git+https://github.com/Seifeddini/knovas-extract-python.git@${KNOVAS_EXTRACT_GIT_REF}"
          else
            pip install "knovas-extract[${extras}]==${KNOVAS_EXTRACT_VERSION}"
          fi
          pip install -r components/docbridge_integration/requirements.txt
          python ../scripts/ci/assert_knovas_extract_pin.py
```

(2) `knovas-platform` job, after the step `Docker compose build` (keep it unchanged):

```yaml
      - name: Docker compose build
        working-directory: .
        run: |
          docker compose --env-file scripts/lib/fixtures/knovas.env.fixture config --quiet
          docker compose --env-file scripts/lib/fixtures/knovas.env.fixture build docbridge-web
```

insert:

```yaml

      - name: The Platform image carries the pinned knovas-extract
        working-directory: .
        run: |
          docker run --rm -i -e KNOVAS_EXTRACT_VERSION -e KNOVAS_EXTRACT_GIT_REF \
            docbridge-web-clientbundle:latest python - < scripts/ci/assert_knovas_extract_pin.py

      - name: No pymupdf-layout (PolyForm-NC) in the Platform image
        # Same gate as the Connector image's: the Platform adds [markdown]
        # (markdownify, selectolax) for the preview, never [pdf-markdown],
        # whose pymupdf4llm pulls pymupdf-layout. A failure blocks the build.
        working-directory: .
        run: |
          if docker run --rm docbridge-web-clientbundle:latest pip show pymupdf-layout >/dev/null 2>&1; then
            echo "pymupdf-layout is installed in the image"; exit 1
          fi
          echo "pymupdf-layout absent"
```

(`docbridge-web-clientbundle:latest` is the `image:` of the `docbridge-web` service in `docker-compose.yml`.)

(3) `knovas-connector` job header. Replace:

```yaml
    defaults:
      run:
        working-directory: KnovasConnector
    env:
      # The knovas-extract revision the tests run against. It must be the
      # KnovasConnector/Dockerfile default (ARG KNOVAS_EXTRACT_REF), so the
      # image ships the extractor CI tested; the install step fails when the
      # two differ. PyPI's knovas-extract 0.2 lacks the 0.4 `Limits` the OCR
      # budgets are sent through, which is what turned main red.
      KNOVAS_EXTRACT_SHA: 11ec1c38053cbbc914207c9e2f24636cde709617
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
```

with:

```yaml
    defaults:
      run:
        working-directory: KnovasConnector
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      # The pin both images install (scripts/ci/check_knovas_extract_pin.sh
      # fails when the two Dockerfiles disagree): the tests run against
      # exactly the extractor the images ship.
      - name: knovas-extract pin
        working-directory: .
        run: bash scripts/ci/check_knovas_extract_pin.sh >> "$GITHUB_ENV"
```

(4) `knovas-connector` job, step `Install dependencies`. Keep the German comment block above it. Replace:

```yaml
        # knovas-extract comes first, from the pinned sha with the extras of
        # pyproject.toml: `pip install -e` then finds it satisfying
        # `knovas-extract>=0.2` and leaves it in place instead of pulling 0.2
        # from PyPI. The last line fails the job if anything replaced it.
        run: |
          grep -qx "ARG KNOVAS_EXTRACT_REF=${KNOVAS_EXTRACT_SHA}" Dockerfile \
            || { echo "Dockerfile KNOVAS_EXTRACT_REF is not ${KNOVAS_EXTRACT_SHA}"; exit 1; }
          pip install "knovas-extract[pdf,docx,msg,html,sentences] @ git+https://github.com/Seifeddini/knovas-extract-python.git@${KNOVAS_EXTRACT_SHA}"
          pip install -e ".[dev]"
          pip install -r scripts/demo_kanzlei/requirements.txt
          python -c "import json, os; from importlib.metadata import distribution; d = distribution('knovas-extract'); u = json.loads(d.read_text('direct_url.json') or '{}'); sha = u.get('vcs_info', {}).get('commit_id'); assert sha == os.environ['KNOVAS_EXTRACT_SHA'], (d.version, sha); print('knovas-extract', d.version, sha)"
```

with:

```yaml
        # knovas-extract comes first: exactly the pin of the previous step,
        # with the Connector image's extras. `pip install -e` then finds it
        # satisfying the floor in pyproject.toml (>=0.4.0a1) and leaves it in
        # place. The last line fails the job if anything replaced it.
        run: |
          extras="pdf,ocr,docx,msg,html,rtf,sentences"
          if [ -n "$KNOVAS_EXTRACT_GIT_REF" ]; then
            pip install "knovas-extract[${extras}] @ git+https://github.com/Seifeddini/knovas-extract-python.git@${KNOVAS_EXTRACT_GIT_REF}"
          else
            pip install "knovas-extract[${extras}]==${KNOVAS_EXTRACT_VERSION}"
          fi
          pip install -e ".[dev]"
          pip install -r scripts/demo_kanzlei/requirements.txt
          python ../scripts/ci/assert_knovas_extract_pin.py
```

(The test job now installs `[ocr]` like the image, which means tesserocr; cp312 manylinux wheels exist. The suite's OCR keys come from stubs, and a born-digital PDF never constructs an OCR backend, so no test depends on the engine.)

(5) `knovas-connector` job, image steps. Replace:

```yaml
            tesseract --list-langs 2>&1 | tail -n +2 | sort | tr "\n" " "; echo'

      - name: No pymupdf-layout (PolyForm-NC) in the customer image
        # Gates the build (plan M0, [C-reg-9]): KNOVAS_EXTRACT_REF is pinned to
        # knovas-extract 0.4.0a1, whose [pdf] extra is pymupdf alone
        # (pymupdf4llm moved to the separate `pdf-markdown` extra). A later
        # bump or extra that drags pymupdf-layout back in fails here.
        run: |
```

with:

```yaml
            tesseract --list-langs 2>&1 | tail -n +2 | sort | tr "\n" " "; echo'
          # The [ocr] extra: OCR runs in-process through tesserocr, not the CLI.
          docker run --rm knovas-connector:ci python -c "from knovas_extract._ocr.backend import select_backend; name = select_backend('auto').name; print('OCR engine', name); assert name == 'tesserocr', name"

      - name: The Connector image carries the pinned knovas-extract
        run: |
          docker run --rm -i -e KNOVAS_EXTRACT_VERSION -e KNOVAS_EXTRACT_GIT_REF \
            knovas-connector:ci python - < ../scripts/ci/assert_knovas_extract_pin.py

      - name: No pymupdf-layout (PolyForm-NC) in the Connector image
        # Gates the build (plan M0, [C-reg-9]): both images install the pin of
        # scripts/ci/check_knovas_extract_pin.sh, knovas-extract 0.4.0a1, whose
        # [pdf] extra is pymupdf alone; pymupdf4llm, which pulls
        # pymupdf-layout, is in the separate [pdf-markdown] extra that neither
        # image installs. A bump or an extra that drags it back in fails here.
        run: |
```

This rewrites the stale comment of spec E7. The `if docker run … pip show pymupdf-layout …` body below it stays.

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_image_license_gate.py tests/unit/test_knovas_extract_pin_scripts.py`
Expected: PASS (12 passed).

Run: `cd $WT && $SP/venvs/pf/Scripts/python -c "import sys, yaml; wf = yaml.safe_load(open(sys.argv[1], encoding='utf-8')); [print(job, [s.get('name', s.get('uses')) for s in wf['jobs'][job]['steps']]) for job in ('knovas-platform', 'knovas-connector')]; assert 'env' not in wf['jobs']['knovas-connector']" .github/workflows/ci.yml`
Expected: the workflow parses. `knovas-platform` lists `knovas-extract pin`, `Install dependencies`, …, `Docker compose build`, `The Platform image carries the pinned knovas-extract`, `No pymupdf-layout (PolyForm-NC) in the Platform image`, `Compose contract (Microsoft 365 documents)`. `knovas-connector` lists `knovas-extract pin`, `Install Tesseract (OCR tests run against the real engine)`, `Install dependencies`, …, `Image carries Tesseract models and the OMP/TESSDATA env (GI-EXTRACT-05)`, `The Connector image carries the pinned knovas-extract`, `No pymupdf-layout (PolyForm-NC) in the Connector image`, `Boot with valid env and probe health`.

Run: `cd $WT && grep -rnE 'KNOVAS_EXTRACT_(REF|FROM_GIT|SHA|EXTRAS)\b' . --exclude-dir=.git --exclude-dir=superpowers --exclude-dir=__pycache__ --exclude-dir=graphify-out`
Expected: only `KnovasConnector/CHANGELOG.md` (the released 0.2.0 line and PIN-1's bullet naming the replaced args) and the two test constants (`OLD_BUILD_ARGS`, `"KNOVAS_EXTRACT_SHA" not in ci`).

- [ ] **Step 5: Docs**

5a. `KnovasConnector/docs/local-commands.md`, section "Python from source (dev/staging)". Replace:

```bash
cd KnovasConnector
cp .env.example .env
pip install -e ".[dev]"
```

with:

```bash
cd KnovasConnector
cp .env.example .env
# knovas-extract: the pin both images use. While it is a git revision (until
# 0.4.0a1 is on PyPI), install it first: the >=0.4.0a1 floor of
# pyproject.toml cannot be resolved from PyPI alone.
eval "$(bash ../scripts/ci/check_knovas_extract_pin.sh)"
[ -z "$KNOVAS_EXTRACT_GIT_REF" ] || pip install "knovas-extract[pdf,docx,msg,html,rtf,sentences] @ git+https://github.com/Seifeddini/knovas-extract-python.git@$KNOVAS_EXTRACT_GIT_REF"
pip install -e ".[dev]"
```

In section "Tests" of the same file, replace the block

```bash
pip install -e ".[dev]"
pytest
```

with:

```bash
eval "$(bash ../scripts/ci/check_knovas_extract_pin.sh)"   # the knovas-extract pin, see above
[ -z "$KNOVAS_EXTRACT_GIT_REF" ] || pip install "knovas-extract[pdf,docx,msg,html,rtf,sentences] @ git+https://github.com/Seifeddini/knovas-extract-python.git@$KNOVAS_EXTRACT_GIT_REF"
pip install -e ".[dev]"
pytest
```

5b. `KnovasConnector/docs/configuration.md`, in the "Docker build" paragraph of PIN-1. Replace `and a PyPI install that is not exactly the version fails the build.` with `and a PyPI install that is not exactly the version fails the build. CI fails when the two Dockerfiles' defaults differ (`scripts/ci/check_knovas_extract_pin.sh`), installs exactly this pin in both test jobs and checks it there and in both built images (`scripts/ci/assert_knovas_extract_pin.py`). A source install needs the pin first ([local-commands.md](local-commands.md#python-from-source-devstaging)).`

5c. `KnovasConnector/CHANGELOG.md`. Directly after PIN-1's bullet, which ends with `the CI check that `pymupdf-layout` is absent from the image blocks the build.`, insert:

```markdown
- CI holds both test jobs and both images to that pin: `scripts/ci/check_knovas_extract_pin.sh` fails when the two Dockerfiles pin different builds and hands the pin to the jobs, which install exactly it (with the images' extras, `ocr` included) before their requirements; `scripts/ci/assert_knovas_extract_pin.py` checks the installed version and commit in both jobs and inside both built images. The `pymupdf-layout` check blocks the Platform image too, and the Connector image must pick tesserocr as its OCR engine.
```

- [ ] **Step 6: Commit**

```bash
cd $WT
git add .github/workflows/ci.yml KnovasConnector/tests/unit/test_image_license_gate.py \
  KnovasConnector/docs/local-commands.md KnovasConnector/docs/configuration.md KnovasConnector/CHANGELOG.md
git commit -F - <<'EOF'
ci: both test jobs install exactly the pin; both images are gated

Both CI jobs read the pin from the two Dockerfiles
(check_knovas_extract_pin.sh into $GITHUB_ENV) and install exactly that
build, with the extras of their image, before their requirements; the
Platform job used to test PyPI 0.2.0, the Connector job a sha kept in step
with one Dockerfile only. assert_knovas_extract_pin.py checks the result in
both jobs and inside both built images. The pymupdf-layout gate now covers
the Platform image as well, the Connector image must choose tesserocr for
OCR, and the stale comment that [pdf] pulls pymupdf-layout is rewritten.
local-commands.md says how a source install gets the pin while it is a git
revision.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task PIN-4: Extraction metrics, the extractor in `/sync/status`, and the System tab (L5)

**Files:**
- Create: `KnovasConnector/src/sync/extract_metrics.py`
- Modify: `KnovasConnector/src/sync/document_text.py` (`ExtractedDocument`, `_extract_bytes`)
- Modify: `KnovasConnector/src/sync/knovas_uploader.py` (import, `SemantixUploader.upload_file`)
- Modify: `KnovasConnector/src/app.py` (import, `create_app`)
- Modify: `KnovasConnector/src/routes/sync_control.py` (import, `sync_status`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_connector_client.py` (new `extractor_version_from_status`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/admin_system.py` (new `platform_extractor_version`, `_extractor_check`; `collect`)
- Modify: `KnovasConnector/docs/operations.md`, `docs/specifications.md`, `KnovasConnector/CHANGELOG.md`, `RELEASE_NOTES.md`
- Test: `KnovasConnector/tests/unit/test_extract_metrics.py` (new), `KnovasConnector/tests/unit/test_document_text.py`, `KnovasConnector/tests/unit/test_knovas_uploader.py`, `KnovasConnector/tests/integration/test_sync_routes.py`, `KnovasPlatform/components/docbridge_integration/tests/test_web_admin_system_extractor.py` (new)

**Interfaces:**
- Consumes: `sync.document_text.docx_text_mode() -> str` (EXT) and the existing `pdf_text_mode() -> str`, `ocr_engine() -> str`, `pdf_ocr_enabled() -> bool | str`.
- Produces:
  - `ExtractedDocument.warnings: tuple[str, ...] = ()`, the library's `result.warnings` (counts and fixed messages, never logged).
  - `sync.extract_metrics`:
    - `RC_VERSION: str`, equal to pyproject's `[project].version`; a test pins it, so whoever bumps the version bumps both.
    - `WARNING_CLASSES = ("ocr", "layout", "metadata", "markdown", "tables", "sentences", "other")`.
    - `warning_class(text: str) -> str`.
    - `record_extraction(doc: ExtractedDocument) -> None`: once per extraction, in the parent, never raises. REX's skip-upload path keeps it, because the call sits directly after `extract_document_guarded`, before any upload decision.
    - `knovas_extract_version() -> Optional[str]`.
    - `extraction_info() -> dict[str, Optional[str]]`: exactly the keys `knovas_extract_version`, `pdf_text_mode`, `docx_text_mode`, `ocr_engine`. `ocr_engine` is `off` while `RC_PDF_OCR_ENABLED` is false; REX's stamp may reuse it.
    - `set_build_info() -> None` and `label_sets() -> dict[str, tuple[str, ...]]`.
    - Metrics `rc_build_info` (Gauge, labels `rc_version,knovas_extract_version,pdf_text_mode,docx_text_mode,ocr_engine`), `rc_ocr_pages_total{result}`, `rc_ocr_seconds_total` and `rc_extract_warnings_total{class}`.
  - `GET /sync/status` gains `status["extraction"] = extract_metrics.extraction_info()`. REX adds `"outdated"` to that dict in `sync_status`, not inside `extraction_info()`, whose four values also label `rc_build_info`.
  - Platform: `knovas_connector_client.extractor_version_from_status(status: Any) -> Optional[str]`, `admin_system.platform_extractor_version() -> str | None`, and the System check with key `"extractor"`.

Commits: one per step group (4A, 4B, 4C).

#### Step group 4A: `ExtractedDocument.warnings`, the metrics module, counting in the uploader

- [ ] **Step 1: Write the failing test**

Create `KnovasConnector/tests/unit/test_extract_metrics.py`. The warning texts are the library's own; they were collected from every `warnings.append(...)` in `$LIB/src/knovas_extract`, plus the two the library PR adds:

```python
"""Extraction metrics and the extractor's version (spec L5).

``/metrics`` is unauthenticated: labels are versions, configured settings and
closed classes -- never a warning's text. The extraction child's registry
dies with it, so the uploader counts each returned document once.
"""
from __future__ import annotations

import math
import tomllib
from pathlib import Path

import pytest

prometheus_client = pytest.importorskip("prometheus_client")

from sync import extract_metrics as em  # noqa: E402
from sync.document_text import ExtractedDocument  # noqa: E402

SENTINEL = "Mandant Sentinel Muster AG"

#: knovas-extract's own warning texts (``warnings.append`` in
#: src/knovas_extract, numbers filled in) and the class of each. The two
#: marked "PR" arrive with the library PR (spec 4.2, 4.4).
REAL_WARNINGS = [
    ("pdf: OCR applied to 7 of 13 pages via tesserocr", "ocr"),
    ("pdf: 2 pages skipped: OCR budget exhausted", "ocr"),
    ("pdf: 1 page skipped: image exceeds max_ocr_image_megapixels", "ocr"),
    ("pdf: 3 pages failed OCR", "ocr"),
    ("pdf: OCR backend unavailable; 4 pages left without OCR", "ocr"),
    ("first page produced no text (OCR may help for scanned PDFs)", "ocr"),
    ("pdf: layout words unavailable on 2 pages; emitted as plain text", "layout"),
    ("pdf: layout rendering failed (ValueError); plain text emitted", "layout"),
    ("text_mode='layout' is implemented for PDF only; plain text emitted", "layout"),
    ("text_mode='layout' is implemented for PDF and DOCX only; plain text emitted", "layout"),  # PR
    ("metadata: 2 values truncated", "metadata"),
    ("metadata: 1 values dropped for NUL / control / bidi-override characters", "metadata"),
    ("metadata: 1 values dropped as unserializable", "metadata"),
    ("pdf: xmp metadata exceeded max_xmp_bytes; skipped", "metadata"),
    ("pdf: xmp metadata unparseable; skipped", "metadata"),
    ("markdown: 2 <script> tags stripped", "markdown"),
    ("markdown: 1 on* event-handler attrs dropped", "markdown"),
    ("docx: mammoth conversion failed; content.markdown left null", "markdown"),
    ("pdf: content.markdown omitted for OCR output (no structure to preserve)", "markdown"),
    ("msg: only RTF body available; content.markdown left null", "markdown"),
    ("docx: tables[0].rows[3] padded from 2 to 3 cells", "tables"),
    ("docx: tables[1].rows[0].[2] truncated at 32768 chars", "tables"),
    ("docx: table extraction failed: KeyError", "tables"),
    ("html: table extraction stopped at 200 tables (spec cap)", "tables"),
    ("pdf: tables[4].rows[2] truncated at 32768 chars (page 12)", "tables"),
    ("pdf: table extraction stopped at 200 tables (spec cap)", "tables"),
    ("pdf: page 3 could not load for table scan (RuntimeError)", "tables"),
    ("pdf: table detection failed on page 2 (ValueError)", "tables"),
    ("pdf: structured table pass failed (ValueError)", "tables"),
    ("sentences: 3 segments could not be located", "sentences"),
    ("sentences: page text could not be aligned to document", "sentences"),
    ("sentences: 120 beyond max_sentences omitted", "sentences"),  # PR
    ("DOCX contains VBA macros; payload ignored (never executed)", "other"),
    ("PDF embedded JavaScript ignored (never executed)", "other"),
    ("Subject header contains embedded newline (header-injection attempt)", "other"),
    ("html: dropped 2 URL(s) with disallowed scheme from meta / link", "other"),
    ("page 4: could not load (cannot load page)", "other"),
]


def _value(name: str, **labels: str) -> float:
    return prometheus_client.REGISTRY.get_sample_value(name, labels) or 0.0


def _warnings_by_class() -> dict[str, float]:
    return {c: _value("rc_extract_warnings_total", **{"class": c}) for c in em.WARNING_CLASSES}


def _pages_by_result() -> dict[str, float]:
    return {r: _value("rc_ocr_pages_total", result=r) for r in ("ocr", "failed", "skipped")}


@pytest.mark.parametrize(("text", "expected"), REAL_WARNINGS)
def test_each_library_warning_has_its_class(text, expected):
    assert em.warning_class(text) == expected


def test_the_classes_are_closed_and_each_is_reached():
    assert em.WARNING_CLASSES == ("ocr", "layout", "metadata", "markdown", "tables", "sentences", "other")
    assert {expected for _, expected in REAL_WARNINGS} == set(em.WARNING_CLASSES)


@pytest.mark.parametrize("text", [SENTINEL, "", None, 42, "democracy is a stable notion"])
def test_anything_else_is_other(text):
    assert em.warning_class(text) == "other"


def test_one_document_is_counted_by_page_result_seconds_and_warning_class():
    pages, seconds, warnings = _pages_by_result(), _value("rc_ocr_seconds_total"), _warnings_by_class()
    em.record_extraction(ExtractedDocument(
        text="x",
        sentences=None,
        extra={"pdf:ocr_pages": 7, "pdf:ocr_pages_failed": 1, "pdf:ocr_pages_skipped": 2,
               "pdf:ocr_seconds": 12.5, "pdf:ocr_backend": "tesserocr", "pdf:text_pages": 3},
        warnings=(
            "pdf: OCR applied to 7 of 13 pages via tesserocr",
            "pdf: 2 pages skipped: OCR budget exhausted",
            "pdf: 1 page failed OCR",
            "pdf: table detection failed on page 3 (ValueError)",
            SENTINEL,
        ),
    ))
    assert {r: v - pages[r] for r, v in _pages_by_result().items()} == {"ocr": 7, "failed": 1, "skipped": 2}
    assert _value("rc_ocr_seconds_total") - seconds == pytest.approx(12.5)
    assert {c: v - warnings[c] for c, v in _warnings_by_class().items()} == {
        "ocr": 3, "layout": 0, "metadata": 0, "markdown": 0, "tables": 1, "sentences": 0, "other": 1,
    }


def test_values_that_are_not_counts_count_nothing():
    pages, seconds = _pages_by_result(), _value("rc_ocr_seconds_total")
    em.record_extraction(ExtractedDocument(text="x", sentences=None, extra={
        "pdf:ocr_pages": True, "pdf:ocr_pages_failed": "3", "pdf:ocr_pages_skipped": -2,
        "pdf:ocr_seconds": math.nan,
    }))
    em.record_extraction(ExtractedDocument(text="x", sentences=None, extra=None))
    em.record_extraction(object())  # not a document: nothing counted, nothing raised
    assert _pages_by_result() == pages
    assert _value("rc_ocr_seconds_total") == seconds


def test_no_label_carries_warning_text():
    em.record_extraction(ExtractedDocument(
        text="x", sentences=None, warnings=(SENTINEL, "metadata: 1 values truncated")))
    em.set_build_info()
    allowed = em.label_sets()
    seen = 0
    for metric in prometheus_client.REGISTRY.collect():
        if not metric.name.startswith(("rc_ocr_pages", "rc_ocr_seconds", "rc_extract_warnings", "rc_build_info")):
            continue
        for sample in metric.samples:
            for key, value in sample.labels.items():
                seen += 1
                assert SENTINEL not in value
                if key == "class":
                    assert value in allowed["rc_extract_warnings_total"]
                if key == "result":
                    assert value in allowed["rc_ocr_pages_total"]
    assert seen, "the metrics were exercised"


def test_extraction_info_names_the_library_and_the_settings(monkeypatch):
    import knovas_extract

    monkeypatch.setenv("RC_PDF_TEXT_MODE", "shadow")
    monkeypatch.setenv("RC_DOCX_TEXT_MODE", "plain")
    monkeypatch.setenv("RC_OCR_ENGINE", "cli")
    monkeypatch.delenv("RC_PDF_OCR_ENABLED", raising=False)
    assert em.extraction_info() == {
        "knovas_extract_version": knovas_extract.__version__,
        "pdf_text_mode": "shadow",
        "docx_text_mode": "plain",
        "ocr_engine": "cli",
    }
    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "false")
    assert em.extraction_info()["ocr_engine"] == "off"


def test_build_info_is_one_series_under_the_current_settings(monkeypatch):
    import knovas_extract

    monkeypatch.setenv("RC_PDF_TEXT_MODE", "plain")
    monkeypatch.setenv("RC_DOCX_TEXT_MODE", "layout")
    monkeypatch.setenv("RC_OCR_ENGINE", "tesserocr")
    monkeypatch.delenv("RC_PDF_OCR_ENABLED", raising=False)
    em.set_build_info()
    assert _value("rc_build_info", rc_version=em.RC_VERSION,
                  knovas_extract_version=knovas_extract.__version__, pdf_text_mode="plain",
                  docx_text_mode="layout", ocr_engine="tesserocr") == 1.0
    monkeypatch.setenv("RC_PDF_TEXT_MODE", "layout")
    em.set_build_info()
    series = [s for m in prometheus_client.REGISTRY.collect() if m.name == "rc_build_info" for s in m.samples]
    assert [s.labels["pdf_text_mode"] for s in series] == ["layout"], "an earlier label set never lingers"


def test_rc_version_is_the_pyproject_version():
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    assert em.RC_VERSION == tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
```

Append to `KnovasConnector/tests/unit/test_document_text.py`:

```python


def test_extracted_document_carries_the_library_warnings(tmp_path, monkeypatch):
    """The parent counts them by class (sync.extract_metrics); the texts
    travel with the document, through the extraction child's queue too."""
    import pickle

    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "false")
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    pdf.new_page()  # page 1 carries no text
    pdf.new_page().insert_text((72, 72), "Seite zwei mit Text.")
    p = tmp_path / "zwei.pdf"
    p.write_bytes(pdf.tobytes())
    pdf.close()

    doc = extract_document(p)

    assert isinstance(doc.warnings, tuple)
    assert "first page produced no text (OCR may help for scanned PDFs)" in doc.warnings
    # The child hands the document over a multiprocessing queue, which
    # pickles it; this round trip only re-reads the object built above.
    assert pickle.loads(pickle.dumps(doc)).warnings == doc.warnings


def test_a_document_without_library_warnings_has_none(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("Ein Satz. Noch einer.", encoding="utf-8")
    assert extract_document(p).warnings == ()
```

Append to `KnovasConnector/tests/unit/test_knovas_uploader.py`:

```python


def test_each_extraction_is_counted_once_in_the_parent(mock_config, tmp_path):
    """The extraction child's registry dies with it: the uploader counts the
    returned document (spec L5), even when the upload then fails."""
    from sync.document_text import ExtractedDocument

    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf")
    doc = ExtractedDocument(text="Deckblatt.", sentences=None, extra={"pdf:ocr_pages": 3},
                            warnings=("pdf: OCR applied to 3 of 4 pages via tesserocr",))
    failed_init = MagicMock()
    failed_init.status_code = 500
    failed_init.content = b""
    for answers in ([_ok_response(), _ok_response()], [failed_init]):
        uploader = SemantixUploader()
        with patch.object(uploader, "_request") as req, patch(
            "sync.knovas_uploader.extract_document_guarded", return_value=doc
        ), patch("sync.knovas_uploader.write_context_sidecar", return_value=True), patch(
            "sync.extract_metrics.record_extraction"
        ) as record:
            req.side_effect = answers
            uploader.upload_file(pdf, "akten/scan.pdf", {"ingestion": {"identifier_prefix": "corpus"}})
        record.assert_called_once_with(doc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_extract_metrics.py tests/unit/test_document_text.py tests/unit/test_knovas_uploader.py`
Expected: FAIL. Collecting `test_extract_metrics.py` errors with `ImportError: cannot import name 'extract_metrics' from 'sync'`. Running `rc-pytest tests/unit/test_document_text.py::test_extracted_document_carries_the_library_warnings tests/unit/test_document_text.py::test_a_document_without_library_warnings_has_none tests/unit/test_knovas_uploader.py::test_each_extraction_is_counted_once_in_the_parent` fails with `AttributeError: 'ExtractedDocument' object has no attribute 'warnings'` (twice) and `TypeError: ExtractedDocument.__init__() got an unexpected keyword argument 'warnings'`.

- [ ] **Step 3: Write minimal implementation**

3a. `KnovasConnector/src/sync/document_text.py`. Append as the last field of `ExtractedDocument`, which today follows `source_metadata` (if an EXT task appended a field there, put this after it):

```python
    source_metadata: dict[str, str] = field(default_factory=dict)
    #: The library's ``result.warnings``: counts and fixed messages, never
    #: document text (its contract). The parent counts them by class
    #: (``sync.extract_metrics``); they are never logged.
    warnings: tuple[str, ...] = ()
```

In `_extract_bytes`, add the keyword as the last argument of the `return ExtractedDocument(...)` call, which today ends with `source_metadata=source_metadata_from(result.metadata),`:

```python
        source_metadata=source_metadata_from(result.metadata),
        warnings=tuple(w for w in (getattr(result, "warnings", None) or ()) if isinstance(w, str)),
    )
```

3b. Create `KnovasConnector/src/sync/extract_metrics.py`:

```python
"""Prometheus metrics for extraction: which library, which settings, and what
it reported (spec L5).

``/metrics`` is unauthenticated (docs/operations.md), so no label carries
document data: versions, the configured text modes and OCR engine, and
closed sets. The library's warnings are mapped to a fixed set of classes
(``warning_class``); their text never becomes a label. Extraction runs in a
forked child whose registry dies with it, so the parent (the uploader) counts
each returned ``ExtractedDocument`` once, from its scalar ``extra``
(``pdf:ocr_*``) and its ``warnings``.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Optional

from sync.document_text import (
    ExtractedDocument,
    docx_text_mode,
    ocr_engine,
    pdf_ocr_enabled,
    pdf_text_mode,
)

logger = logging.getLogger(__name__)

#: This Knovas Connector's version: ``[project].version`` of pyproject.toml
#: (a test keeps the two equal). The image runs from ``src/`` without
#: installing the package, so there is no distribution metadata to read.
RC_VERSION = "0.3.0"

OTHER = "other"
#: ``rc_extract_warnings_total{class}``.
WARNING_CLASSES = ("ocr", "layout", "metadata", "markdown", "tables", "sentences", OTHER)
#: ``rc_ocr_pages_total{result}``, each with the ``metadata.extra`` key it counts.
OCR_PAGE_KEYS = (
    ("ocr", "pdf:ocr_pages"),
    ("failed", "pdf:ocr_pages_failed"),
    ("skipped", "pdf:ocr_pages_skipped"),
)
#: ``rc_build_info`` labels, in this order.
BUILD_INFO_LABELS = (
    "rc_version",
    "knovas_extract_version",
    "pdf_text_mode",
    "docx_text_mode",
    "ocr_engine",
)

#: Library warning -> class; the first match wins. Read off knovas-extract's
#: own texts (``warnings.append(...)`` in src/knovas_extract): markdown first
#: ("pdf: content.markdown omitted for OCR output" is about the markdown), the
#: prefixes before the keyword rules.
_WARNING_RULES = (
    ("markdown", re.compile(r"^markdown:|content\.markdown")),
    ("sentences", re.compile(r"^sentences:")),
    ("metadata", re.compile(r"^metadata:|^pdf: xmp metadata")),
    ("layout", re.compile(r"^text_mode=|^(?:pdf|docx): layout")),
    ("ocr", re.compile(r"(?<![a-z])ocr(?![a-z])")),
    ("tables", re.compile(r"(?<![a-z])tables?(?![a-z])")),
)


class _Noop:
    def labels(self, *args: Any, **kwargs: Any) -> "_Noop":  # noqa: ARG002
        return self

    def inc(self, amount: float = 1) -> None:  # noqa: ARG002 - mirrors prometheus_client
        return None

    def set(self, value: float) -> None:  # noqa: ARG002 - mirrors prometheus_client
        return None

    def clear(self) -> None:
        return None


def _metric(kind: str, name: str, documentation: str, labels: tuple[str, ...] = ()):
    try:
        import prometheus_client

        return getattr(prometheus_client, kind)(name, documentation, list(labels))
    except ImportError:
        return _Noop()
    except ValueError:
        # Already registered (module re-imported under a test runner).
        from prometheus_client import REGISTRY

        existing = getattr(REGISTRY, "_names_to_collectors", {}).get(name)
        return existing if existing is not None else _Noop()


BUILD_INFO = _metric(
    "Gauge",
    "rc_build_info",
    "Always 1: this Knovas Connector's version, its knovas-extract version, the "
    "PDF and DOCX text modes and the OCR engine (off when OCR is disabled)",
    BUILD_INFO_LABELS,
)
OCR_PAGES = _metric(
    "Counter",
    "rc_ocr_pages_total",
    "PDF pages by OCR result: ocr (text from OCR), failed (left empty), skipped "
    "(time budget, page or pixel cap, no OCR engine)",
    ("result",),
)
OCR_SECONDS = _metric(
    "Counter",
    "rc_ocr_seconds_total",
    "Wall-clock seconds knovas-extract spent on OCR",
)
EXTRACT_WARNINGS = _metric(
    "Counter",
    "rc_extract_warnings_total",
    "knovas-extract warnings by class (never their text)",
    ("class",),
)


def warning_class(text: str) -> str:
    """The class of one knovas-extract warning: one of ``WARNING_CLASSES``,
    ``other`` for anything the rules do not know."""
    if not isinstance(text, str):
        return OTHER
    lowered = text.strip().lower()
    for name, pattern in _WARNING_RULES:
        if pattern.search(lowered):
            return name
    return OTHER


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    return max(0, int(value))


def _seconds(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    seconds = float(value)
    return seconds if math.isfinite(seconds) and seconds > 0 else 0.0


def record_extraction(doc: ExtractedDocument) -> None:
    """Count one extracted document: OCR pages by result, OCR seconds and the
    library's warnings by class. Called once per extraction, in the process
    that serves ``/metrics``. Never raises."""
    try:
        extra = getattr(doc, "extra", None) or {}
        for result, key in OCR_PAGE_KEYS:
            pages = _count(extra.get(key))
            if pages:
                OCR_PAGES.labels(result).inc(pages)
        seconds = _seconds(extra.get("pdf:ocr_seconds"))
        if seconds:
            OCR_SECONDS.inc(seconds)
        for text in getattr(doc, "warnings", None) or ():
            EXTRACT_WARNINGS.labels(warning_class(text)).inc()
    except Exception as exc:  # noqa: BLE001 - a metric never fails an upload
        logger.debug("extraction metrics not counted: %s", type(exc).__name__)


def knovas_extract_version() -> Optional[str]:
    """``knovas_extract.__version__``; None when the library cannot say."""
    try:
        import knovas_extract
    except Exception:  # noqa: BLE001 - reported as unknown, never fatal
        return None
    version = getattr(knovas_extract, "__version__", None)
    return version if isinstance(version, str) and version else None


def extraction_info() -> dict[str, Optional[str]]:
    """The extractor and its settings: the ``extraction`` block of
    ``GET /sync/status`` and the labels of ``rc_build_info``. ``ocr_engine``
    is ``off`` while ``RC_PDF_OCR_ENABLED`` is false."""
    return {
        "knovas_extract_version": knovas_extract_version(),
        "pdf_text_mode": pdf_text_mode(),
        "docx_text_mode": docx_text_mode(),
        "ocr_engine": ocr_engine() if pdf_ocr_enabled() else "off",
    }


def set_build_info() -> None:
    """``rc_build_info`` = 1 under the current labels, replacing any earlier
    label set (one series per process). Called once at app start."""
    info = extraction_info()
    try:
        BUILD_INFO.clear()
        BUILD_INFO.labels(
            RC_VERSION,
            info["knovas_extract_version"] or "unknown",
            info["pdf_text_mode"],
            info["docx_text_mode"],
            info["ocr_engine"],
        ).set(1)
    except Exception as exc:  # noqa: BLE001 - a metric never stops the app
        logger.debug("rc_build_info not set: %s", type(exc).__name__)


def label_sets() -> dict[str, tuple[str, ...]]:
    """The closed label values of the counters (tests pin them)."""
    return {
        "rc_ocr_pages_total": tuple(result for result, _ in OCR_PAGE_KEYS),
        "rc_extract_warnings_total": WARNING_CLASSES,
    }
```

(The duplicate-registration fallback matches `ocr_metrics.py` / `doc_fields_metrics.py`; prometheus_client's `DuplicateTimeseries` is a `ValueError`. The `ocr` class includes the informational "OCR applied to N of M pages" line; failed and skipped pages are the trouble signal and have their own counter.)

3c. `KnovasConnector/src/sync/knovas_uploader.py`. Add `extract_metrics` to the `from sync import …` line, which today reads

```python
from sync import doc_fields_metrics, ocr_metrics
```

so that it reads

```python
from sync import doc_fields_metrics, extract_metrics, ocr_metrics
```

In `SemantixUploader.upload_file`, directly after

```python
            doc = extract_document_guarded(file_path, document_key=relative_path)
```

insert

```python
            # Counted here, in the process that serves /metrics: the
            # extraction child's registry died with it (spec L5).
            extract_metrics.record_extraction(doc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_extract_metrics.py tests/unit/test_document_text.py tests/unit/test_knovas_uploader.py tests/test_doc_fields_no_values_in_logs.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/extract_metrics.py KnovasConnector/src/sync/document_text.py \
  KnovasConnector/src/sync/knovas_uploader.py KnovasConnector/tests/unit/test_extract_metrics.py \
  KnovasConnector/tests/unit/test_document_text.py KnovasConnector/tests/unit/test_knovas_uploader.py
git commit -F - <<'EOF'
rc: count OCR pages, OCR seconds and extractor warnings by class

The extraction child's Prometheus registry dies with it, so the uploader
counts each returned document once, from the library's pdf:ocr_* scalars and
its warnings, which ExtractedDocument now carries (result.warnings, counts
and fixed messages only). Warnings map to a closed set of classes (ocr,
layout, metadata, markdown, tables, sentences, other) read off the
library's own texts; the text itself is never a label. rc_build_info and
extraction_info() are defined here and wired into the app in the next
commit.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

#### Step group 4B: build info at start, the `extraction` block of `/sync/status`

- [ ] **Step 1: Write the failing test**

Append to `KnovasConnector/tests/unit/test_extract_metrics.py`:

```python


def test_the_app_sets_the_build_info_at_start(tmp_watch_root, monkeypatch):
    import app as app_module  # builds its own app once, on first import

    calls = []
    monkeypatch.setattr(em, "set_build_info", lambda: calls.append(True))
    app_module.create_app(skip_validation=True)
    assert calls == [True]
```

Append to `KnovasConnector/tests/integration/test_sync_routes.py`. REX later adds `outdated` to the same block; this test compares only the four keys, so that stays green:

```python


def test_sync_status_reports_the_extractor(rc_client, auth_headers, monkeypatch):
    """Versions and setting names only; the Platform's System tab compares
    the version with its own (spec L5)."""
    import knovas_extract

    monkeypatch.setenv("RC_PDF_TEXT_MODE", "plain")
    monkeypatch.setenv("RC_DOCX_TEXT_MODE", "layout")
    monkeypatch.setenv("RC_OCR_ENGINE", "cli")
    monkeypatch.delenv("RC_PDF_OCR_ENABLED", raising=False)
    with patch("auth.knovas_verify_client.get_verify_client") as mock_client:
        mock_client.return_value.verify_operator.return_value = (True, "c", None)
        resp = rc_client.get("/sync/status", headers=auth_headers)
    assert resp.status_code == 200
    block = resp.get_json()["extraction"]
    assert {key: block[key] for key in ("knovas_extract_version", "pdf_text_mode",
                                        "docx_text_mode", "ocr_engine")} == {
        "knovas_extract_version": knovas_extract.__version__,
        "pdf_text_mode": "plain",
        "docx_text_mode": "layout",
        "ocr_engine": "cli",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_extract_metrics.py::test_the_app_sets_the_build_info_at_start tests/integration/test_sync_routes.py::test_sync_status_reports_the_extractor`
Expected: 2 FAIL, with `assert [] == [True]` and `KeyError: 'extraction'`.

- [ ] **Step 3: Write minimal implementation**

3a. `KnovasConnector/src/app.py`. Replace

```python
from routes.sync_control import sync_control_bp
from sync.sync_scheduler import maybe_auto_start
```

with

```python
from routes.sync_control import sync_control_bp
from sync import extract_metrics
from sync.sync_scheduler import maybe_auto_start
```

In `create_app`, directly after `    app.register_blueprint(m365_bp)`, insert:

```python

    # rc_build_info: which knovas-extract and which settings this process
    # extracts with -- versions and setting names only, one series.
    extract_metrics.set_build_info()
```

The call goes through the module attribute, so the test's monkeypatch sees it.

3b. `KnovasConnector/src/routes/sync_control.py`. Replace `from sync.sync_config import load_sync_config` with

```python
from sync import extract_metrics
from sync.sync_config import load_sync_config
```

and in `sync_status`, replace

```python
    status["doc_fields"] = doc_fields_status()
```

with

```python
    status["doc_fields"] = doc_fields_status()
    # The extractor and its settings (versions and setting names only): the
    # Platform's System tab compares the version with its own.
    status["extraction"] = extract_metrics.extraction_info()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_extract_metrics.py tests/integration/test_sync_routes.py tests/test_doc_fields_no_values_in_logs.py tests/health`
Expected: PASS.

Run the whole Connector suite: `rc-pytest tests`
Expected: PASS, apart from the known Windows-only failures listed in the plan header.

- [ ] **Step 5: Docs**

5a. `KnovasConnector/docs/operations.md`, section "Metrics". Directly after the paragraph

```markdown
Document fields add four counters (see [Document fields](#document-fields)). Every label value comes from a closed set, anything else counts as `other`, so no field key, value, path or pointer can become a label.
```

insert:

````markdown

Extraction adds four series, counted in the API process from what each extraction returns (the extraction child's own registry dies with it):

| Series | Labels | Meaning |
|--------|--------|---------|
| `rc_build_info` | `rc_version`, `knovas_extract_version`, `pdf_text_mode`, `docx_text_mode`, `ocr_engine` (`auto`, `tesserocr`, `cli`, `mupdf`; `off` while `RC_PDF_OCR_ENABLED=false`) | always 1, set at start |
| `rc_ocr_pages_total` | `result`: `ocr` (text from OCR), `failed` (OCR failed, the page stays empty), `skipped` (time budget, page cap, pixel cap or no OCR engine) | PDF pages |
| `rc_ocr_seconds_total` | — | wall-clock seconds knovas-extract spent on OCR |
| `rc_extract_warnings_total` | `class`: `ocr`, `layout`, `metadata`, `markdown`, `tables`, `sentences`, `other` | the library's warnings by class, never their text; `ocr` includes the informational "OCR applied to N of M pages" line, so watch the `failed` and `skipped` results of `rc_ocr_pages_total` for trouble |

`GET /sync/status` names the same extractor and settings:

```json
"extraction": {"knovas_extract_version": "0.4.0a1", "pdf_text_mode": "layout", "docx_text_mode": "layout", "ocr_engine": "auto"}
```

The Platform's *Verwaltung → System* compares `knovas_extract_version` with its own and warns when they differ.
````

5b. `docs/specifications.md` §1.9, row `GET /sync/status`. Replace `Sync status; supports `?live=1` and `?live=1&deep_scan=1`` with `Sync status; supports `?live=1` and `?live=1&deep_scan=1`; `extraction` names the knovas-extract version and the text modes`.

5c. `KnovasConnector/CHANGELOG.md`. Directly after PIN-2's bullet, which ends with `and the Connector image must pick tesserocr as its OCR engine.`, insert:

```markdown
- **Extraction metrics** (`/metrics`): `rc_build_info{rc_version,knovas_extract_version,pdf_text_mode,docx_text_mode,ocr_engine}` (always 1), `rc_ocr_pages_total{result="ocr"|"failed"|"skipped"}`, `rc_ocr_seconds_total` and `rc_extract_warnings_total{class}` — the library's warnings mapped to `ocr`, `layout`, `metadata`, `markdown`, `tables`, `sentences` or `other`, never their text. Counted in the API process from the document the extraction child returns (`ExtractedDocument.warnings` now carries the library's warnings). `GET /sync/status` gains `extraction` (`knovas_extract_version`, `pdf_text_mode`, `docx_text_mode`, `ocr_engine`); the Platform's System tab compares the version with its own.
```

- [ ] **Step 6: Commit**

```bash
cd $WT
git add KnovasConnector/src/app.py KnovasConnector/src/routes/sync_control.py \
  KnovasConnector/tests/unit/test_extract_metrics.py KnovasConnector/tests/integration/test_sync_routes.py \
  KnovasConnector/docs/operations.md docs/specifications.md KnovasConnector/CHANGELOG.md
git commit -F - <<'EOF'
rc: publish the extractor version in /metrics and /sync/status

create_app sets rc_build_info once (one series: the Connector's version,
its knovas-extract version, the PDF and DOCX text modes and the OCR engine),
and GET /sync/status reports the same as "extraction", so an operator and
the Platform's System tab can see which extractor produced the index.
Versions and setting names only.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

#### Step group 4C: the System tab compares both extractor versions

- [ ] **Step 1: Write the failing test**

Create `KnovasPlatform/components/docbridge_integration/tests/test_web_admin_system_extractor.py`:

```python
"""System tab: the extractor of the Platform and of the Knovas Connector side
by side (spec L5).

The admin upload here and the Connector's sync extract into one index, so
the tab names both knovas-extract versions and warns when they differ. The
Connector's version comes from the /sync/status answer its own line already
fetched -- never a second request. The checks run without an app.
"""

from __future__ import annotations

import pytest

from conftest import DummyKnovasClient


class _Connector:
    """What the System tab's ping sees: health() is /sync/status."""

    def __init__(self, status=None, error=None):
        self._status = {} if status is None else status
        self._error = error
        self.calls = 0

    def health(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._status


def _status(version):
    return {"capabilities": [], "extraction": {
        "knovas_extract_version": version, "pdf_text_mode": "layout",
        "docx_text_mode": "layout", "ocr_engine": "auto"}}


def _extractor(rc=None):
    from web_interface import admin_system

    checks = admin_system.collect(lambda: DummyKnovasClient(None),
                                  rc_client_factory=(lambda: rc) if rc is not None else None)
    return next(c for c in checks if c["key"] == "extractor")


@pytest.fixture(autouse=True)
def _healthy(monkeypatch):
    monkeypatch.setattr(DummyKnovasClient, "health_result", True)


@pytest.fixture
def platform_version(monkeypatch):
    """The Platform's own knovas-extract, as the tab reads it."""
    from web_interface import admin_system

    def use(version):
        monkeypatch.setattr(admin_system, "platform_extractor_version", lambda: version)

    use("0.4.0a1")
    return use


class TestBothSides:
    def test_the_same_version_is_ok(self, platform_version):
        check = _extractor(_Connector(_status("0.4.0a1")))
        assert check["state"] == "ok"
        assert check["detail"] == "Plattform 0.4.0a1, Knovas Connector 0.4.0a1"

    def test_different_versions_warn(self, platform_version):
        check = _extractor(_Connector(_status("0.3.0")))
        assert check["state"] == "warn"
        assert check["detail"] == "Plattform 0.4.0a1, Knovas Connector 0.3.0"
        assert "verschiedenen Versionen" in check["hint"]

    def test_a_connector_that_does_not_report_it_is_called_out(self, platform_version):
        check = _extractor(_Connector({"capabilities": []}))
        assert check["state"] == "warn"
        assert check["detail"] == "Plattform 0.4.0a1, Knovas Connector: keine Angabe"
        assert "Aktualisieren" in check["hint"]

    def test_an_unreachable_connector_skips_the_comparison(self, platform_version):
        from knovas_connector_client import KnovasConnectorError

        check = _extractor(_Connector(error=KnovasConnectorError("down")))
        assert check["state"] == "skip"
        assert check["detail"] == "Plattform 0.4.0a1, Knovas Connector nicht erreichbar"

    def test_without_a_connector_the_platform_alone(self, platform_version):
        check = _extractor()
        assert check["state"] == "ok" and check["detail"] == "Plattform 0.4.0a1"

    def test_a_platform_without_the_library_warns(self, platform_version):
        platform_version(None)
        check = _extractor(_Connector(_status("0.4.0a1")))
        assert check["state"] == "warn"
        assert check["detail"] == "Plattform: nicht installiert, Knovas Connector 0.4.0a1"

    def test_the_connector_is_asked_once(self, platform_version):
        rc = _Connector(_status("0.4.0a1"))
        _extractor(rc)
        assert rc.calls == 1, "the Connector line's ping answer is reused"

    def test_the_platform_side_is_the_installed_library(self):
        import knovas_extract

        from web_interface import admin_system

        assert admin_system.platform_extractor_version() == knovas_extract.__version__


class TestVersionFromStatus:
    @pytest.mark.parametrize("status", [
        None, [], "0.4.0a1", {}, {"extraction": None}, {"extraction": {}},
        {"extraction": {"knovas_extract_version": 4}},
        {"extraction": {"knovas_extract_version": ""}},
        {"extraction": {"knovas_extract_version": "0.4.0a1 <b>"}},
        {"extraction": {"knovas_extract_version": "9" * 41}},
    ])
    def test_anything_but_a_plain_version_is_none(self, status):
        from knovas_connector_client import extractor_version_from_status

        assert extractor_version_from_status(status) is None

    def test_a_version_is_returned_as_given(self):
        from knovas_connector_client import extractor_version_from_status

        assert extractor_version_from_status(_status("0.4.0a1")) == "0.4.0a1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_web_admin_system_extractor.py`
Expected: FAIL/ERROR. Fixture setup errors with `AttributeError: <module 'web_interface.admin_system' …> has no attribute 'platform_extractor_version'`, and the version tests fail with `ImportError: cannot import name 'extractor_version_from_status'`.

- [ ] **Step 3: Write minimal implementation**

3a. `KnovasPlatform/components/docbridge_integration/src/knovas_connector_client.py`. Replace

```python
import logging
from typing import Any, Mapping, Optional
```

with

```python
import logging
import re
from typing import Any, Mapping, Optional
```

and directly after the function `capabilities_from_status` (before `def advertised_capabilities`), insert:

```python
#: A version as knovas-extract spells it (PEP 440); anything else in an
#: answer is not shown.
_EXTRACTOR_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+!_-]{0,39}")


def extractor_version_from_status(status: Any) -> Optional[str]:
    """``extraction.knovas_extract_version`` of a ``/sync/status`` answer;
    None from a Knovas Connector too old to report it, or when the value is
    not a plain version string."""
    if not isinstance(status, Mapping):
        return None
    block = status.get("extraction")
    if not isinstance(block, Mapping):
        return None
    version = block.get("knovas_extract_version")
    if isinstance(version, str) and _EXTRACTOR_VERSION_RE.fullmatch(version):
        return version
    return None
```

3b. `KnovasPlatform/components/docbridge_integration/src/web_interface/admin_system.py`. Replace

```python
from knovas_connector_client import capabilities_from_status
```

with

```python
from knovas_connector_client import capabilities_from_status, extractor_version_from_status
```

Directly before `def collect(client_factory: Callable[[], Any], *, gate=None,`, insert:

```python
def platform_extractor_version() -> str | None:
    """``knovas_extract.__version__`` of this Platform; None without the library."""
    try:
        import knovas_extract
    except Exception:  # noqa: BLE001 - the check names the missing library
        return None
    version = getattr(knovas_extract, "__version__", None)
    return version if isinstance(version, str) and version else None


def _extractor_check(platform: str | None, connector: str | None,
                     reached: bool | None) -> Check:
    """Both sides' knovas-extract side by side (spec L5).

    ``reached`` is None without a Knovas Connector, False when its ping
    failed, True when it answered -- ``connector`` is then what its
    /sync/status reports, None from one too old to report it. The admin
    upload here and the Connector's sync extract into one index, so a
    difference is worth a warning.
    """
    label = "Extraktor (knovas-extract)"
    parts = [f"Plattform {platform}" if platform else "Plattform: nicht installiert"]
    if reached is True:
        parts.append(f"Knovas Connector {connector}" if connector else "Knovas Connector: keine Angabe")
    elif reached is False:
        parts.append("Knovas Connector nicht erreichbar")
    detail = ", ".join(parts)
    if platform is None:
        return Check("extractor", label, WARN, detail,
                     hint="Ohne knovas-extract scheitern Upload und Vorschau in der Verwaltung.")
    if reached is None:
        return Check("extractor", label, OK, detail)
    if reached is False:
        return Check("extractor", label, SKIP, detail)
    if connector is None:
        return Check("extractor", label, WARN, detail,
                     hint="Diese Version des Knovas Connector meldet ihren Extraktor nicht. "
                          "Aktualisieren, damit beide Seiten denselben verwenden.")
    if connector != platform:
        return Check("extractor", label, WARN, detail,
                     hint="Die beiden Seiten extrahieren mit verschiedenen Versionen: dieselbe Datei "
                          "kann ueber den Upload hier anders im Index landen als ueber den Knovas "
                          "Connector. Beide Images mit demselben Stand neu bauen.")
    return Check("extractor", label, OK, detail)


```

In `collect`, directly before `    if rc_client_factory is None:`, insert:

```python
    rc_reached: bool | None = None
    rc_extractor: str | None = None
```

Directly after `        answer, ms, exc = _timed(_rc_ping)`, insert:

```python
        rc_reached = exc is None
        rc_extractor = extractor_version_from_status(answer) if exc is None else None
```

Replace the end of `collect`

```python
            hint=rc_hint if exc is None else "Betrifft nur den Reiter Ingestion.",
        ))

    return checks
```

with

```python
            hint=rc_hint if exc is None else "Betrifft nur den Reiter Ingestion.",
        ))

    # -- Extraktor ---------------------------------------------------------
    checks.append(_extractor_check(platform_extractor_version(), rc_extractor, rc_reached))

    return checks
```

`admin_system.html` renders every check generically (label, detail, hint, state), so no template or JS change is needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_web_admin_system_extractor.py tests/test_web_admin_system_doc_fields.py tests/test_doc_fields_render.py`
Expected: PASS. The existing Connector-line tests are unchanged, because the extractor row is extra.

- [ ] **Step 5: Docs**

`RELEASE_NOTES.md`, in PIN-1's section. Directly after the bullet

```markdown
- **Outlook-Mails, deren Text nur als RTF vorliegt**, werden gelesen (bisher
  leerer Text).
```

insert:

```markdown
- *Verwaltung -> System* nennt die Extraktor-Version der Plattform und des
  Knovas Connector und warnt, wenn sie sich unterscheiden oder der Knovas
  Connector keine meldet (dann ist er aelter als die Plattform).
```

- [ ] **Step 6: Commit**

```bash
cd $WT
git add KnovasPlatform/components/docbridge_integration/src/knovas_connector_client.py \
  KnovasPlatform/components/docbridge_integration/src/web_interface/admin_system.py \
  KnovasPlatform/components/docbridge_integration/tests/test_web_admin_system_extractor.py RELEASE_NOTES.md
git commit -F - <<'EOF'
platform: System tab compares the extractor of both sides

The admin upload and the Knovas Connector's sync extract into one index; a
different knovas-extract on either side changes the text a document gets.
The System tab now shows the Platform's knovas_extract.__version__ next to
the version the Connector reports in /sync/status (read from the answer the
Connector line already fetched, never a second request), warns when they
differ or the Connector reports none, and skips the comparison when the
Connector is unreachable.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### After Task A6: the final pin (re-run of PIN-1 and PIN-2)

PIN-1 set `b5d45404a6df0aa5fb2b934c8ae4efab9fe764a1` as the interim default. When Task A6 has merged the library PR, and again when it has published `v0.4.0a1`, change only the two ARG lines. No doc, test or CI line names the sha.

1. **Merge commit known.** `A6_SHA` is the full 40-character sha of the library PR's merge commit, from Task A6.

   ```bash
   cd $WT
   A6_SHA=<full 40-character merge commit from Task A6>
   sed -i "s/^ARG KNOVAS_EXTRACT_GIT_REF=[0-9a-f]*$/ARG KNOVAS_EXTRACT_GIT_REF=${A6_SHA}/" \
     KnovasConnector/Dockerfile KnovasPlatform/components/docbridge_integration/Dockerfile
   bash scripts/ci/check_knovas_extract_pin.sh
   bash scripts/ci/test_check_knovas_extract_pin.sh
   rc-pytest tests/unit/test_image_license_gate.py tests/unit/test_knovas_extract_pin_scripts.py
   git add KnovasConnector/Dockerfile KnovasPlatform/components/docbridge_integration/Dockerfile
   git commit -F - <<'EOF'
   rc+platform: pin knovas-extract to the library PR's merge commit

   The library PR (CI green, DOCX tables in layout mode, HTML-only e-mails,
   fail-soft sentence cap) is merged; both images and both CI jobs now run
   its merge commit instead of the interim main b5d4540.

   Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
   EOF
   ```

   Expected: the check prints `knovas-extract pin: 0.4.0a1 from git <A6_SHA> (both Dockerfiles agree)`; the smoke test and the pytest files pass. CI on the PR then installs and asserts `<A6_SHA>`.

2. **`v0.4.0a1` on PyPI.** First check that the release resolves with the images' extras: `$SP/venvs/rc/Scripts/python -m pip download --no-deps --dest "$SP/pypi-check" "knovas-extract==0.4.0a1"`. Then:

   ```bash
   cd $WT
   sed -i "s/^ARG KNOVAS_EXTRACT_GIT_REF=[0-9a-f]*$/ARG KNOVAS_EXTRACT_GIT_REF=/" \
     KnovasConnector/Dockerfile KnovasPlatform/components/docbridge_integration/Dockerfile
   bash scripts/ci/check_knovas_extract_pin.sh
   rc-pytest tests/unit/test_image_license_gate.py
   git add KnovasConnector/Dockerfile KnovasPlatform/components/docbridge_integration/Dockerfile
   git commit -F - <<'EOF'
   rc+platform: knovas-extract 0.4.0a1 from PyPI

   The release is published (signed, with provenance). An empty
   KNOVAS_EXTRACT_GIT_REF makes both images and both CI jobs install
   knovas-extract==0.4.0a1 from PyPI; the build fails if the installed
   version is anything else.

   Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
   EOF
   ```

   Expected: `knovas-extract pin: 0.4.0a1 from PyPI (both Dockerfiles agree)`. The local `eval … check_knovas_extract_pin.sh` lines in `local-commands.md` then skip the git install, and a plain `pip install -e ".[dev]"` resolves the floor from PyPI.

---

## Part B4 — Document fields in the Platform (FLD)

These tasks finish the Platform side of Document fields 1.5.0 (spec §7 F1, F2, F3, F6, F7). They run **after INT** (merged tree, rename sweep, 1.5.0 texts and the §5.2 merge fixes are in place) and are **independent of EXT and PIN**. Run them in order FLD-1 … FLD-13: FLD-4 builds on FLD-3's template, FLD-5 tests both against the mock, FLD-8 edits a method FLD-7 rewrites, FLD-11 consumes FLD-10's client field, FLD-12/FLD-13 consume FLD-11's payload. Where a file is also touched by another part (`RELEASE_NOTES.md`, `KnovasPlatform/docs/features/document-fields.md`, `scripts/doctor.sh`), each step anchors on the paragraph it changes, not on line numbers.

Conventions for every FLD task:

```bash
cd $WT
PF=KnovasPlatform/components/docbridge_integration
MOCK=KnovasPlatform/mock_knovas_api
```

- `pf-pytest` runs in `$WT/$PF` and `mock-pytest` in `$WT/$MOCK` (plan header) — test paths below are relative to those directories.
- Added lines in `.py` files stay ASCII (`scripts/check_ascii_py.py`, `test_doc_fields_view.py::test_new_modules_are_ascii_only`, `test_web_admin_doc_fields.py::TestStatic::test_module_is_ascii_only`, `test_mock_doc_fields.py::test_new_python_files_are_ascii`): umlauts, dashes and German quotes in Python strings are `\u` escapes. Templates, JS, CSS and the feature doc carry real characters; `RELEASE_NOTES.md` keeps its ASCII transliteration.
- UI strings are German. No field value, node name, pointer or query text in a log line, metric or error; new code that logs gets a sentinel test.
- JS behaviour tests use the Node `vm` harness of `tests/test_experiments_frontend.py` (`_run_node`). They skip without `node`; check `node --version` (≥ 18) before Step 2.

---

### Task FLD-1: Query body — only the keys Knovas reads (F7)

**Files:**
- Modify: `$PF/src/knovas_client.py` (`KnovasAPIClient.__init__`, remove `_load_encryption_matrix`, `_secured_query_request_body`, `search_documents`, `_search_documents_secured`)
- Modify: `$PF/src/web_interface/app.py` (comment in `search()`)
- Modify: `$PF/config/config.yaml` (remove `api.encryption_matrix_path`)
- Modify: `docs/specifications.md` (§2.4 optional variables), `scripts/search-probe.sh` (`ask()`), `RELEASE_NOTES.md`
- Test: `$PF/tests/test_knovas_client_doc_fields.py`, `$PF/tests/test_knovas_client_hardening.py`, `$PF/tests/test_doc_fields_mock_contract.py`

**Interfaces:**
- Consumes: nothing from earlier FLD tasks.
- Produces: `KnovasAPIClient._secured_query_request_body(self, query, limit=None, where=None, return_fields=None) -> Dict[str, Any]` (keys `Input`, `limit`, and `where`/`return_fields` only when given); `KnovasAPIClient._search_documents_secured(self, query, limit, where=None, return_fields=None)`; `search_documents(query, limit=20, filters=None, *, where=None, return_fields=None)` keeps its signature — `filters` is used by the legacy GET only. `KnovasAPIClient.encryption_matrix_path` no longer exists.

- [ ] **Step 1: Write the failing test**

`tests/test_knovas_client_doc_fields.py`, class `TestSearchBody` — replace

```python
    def test_without_the_new_keys_the_body_is_todays(self):
        client = secured(Resp(200, {"results": []}), broker=False)
        client.search_documents("Mietvertrag", limit=5, filters={"akten_id": "A-42"})
        assert _calls(client)[0]["json"] == {
            "Input": "Mietvertrag", "limit": 5, "top_k": 5, "filters": {"akten_id": "A-42"}}
```

with

```python
    def test_the_body_carries_only_what_knovas_reads(self):
        """F7: /secured/query reads Input and limit (plus where and
        return_fields); top_k, filters and encryption_matrix it never read."""
        client = secured(Resp(200, {"results": []}), broker=False)
        client.search_documents("Mietvertrag", limit=5, filters={"akten_id": "A-42"})
        assert _calls(client)[0]["json"] == {"Input": "Mietvertrag", "limit": 5}

    def test_an_encryption_matrix_file_is_not_read(self, tmp_path, monkeypatch):
        matrix = tmp_path / "matrix.json"
        matrix.write_text("[[1, 0], [0, 1]]", encoding="utf-8")
        monkeypatch.setenv("SEMANTIX_ENCRYPTION_MATRIX_PATH", str(matrix))
        client = secured(Resp(200, {"results": []}), broker=False)
        client.search_documents("q", limit=5, where={"doc_type": "invoice"},
                                return_fields=["title"])
        assert set(_calls(client)[0]["json"]) == {"Input", "limit", "where", "return_fields"}
        assert not hasattr(client, "encryption_matrix_path")
```

and in `test_limit_is_clamped_to_50` replace `assert body["limit"] == sent and body["top_k"] == sent` with `assert body["limit"] == sent and "top_k" not in body`.

`tests/test_knovas_client_hardening.py` — in the module docstring replace the line `  C5  ``filters`` must not be silently dropped in secured search mode.` with `  C5  ``filters`` stay on the Platform: /secured/query reads none (spec F7).`; in `make_client` delete the line `        "api.encryption_matrix_path": "",`; replace the whole class `TestC5SecuredFilters` (from `class TestC5SecuredFilters:` through its `assert body.get("filters") == ...` block) with:

```python
class TestC5FiltersStayOnThePlatform:
    """C5 once forwarded ``filters`` into /secured/query so that case scoping
    was not dropped silently. Knovas reads no such key (1.5.0, spec F7): the
    secured body leaves it out, and the Platform applies its own filters
    (exact_match, the score thresholds) to the answer. The legacy GET still
    sends them as query parameters."""

    def test_the_secured_body_carries_no_filters(self):
        client = make_secured_client()
        captured = {}

        def responder(method, url, **kw):
            captured.update(kw)
            return FakeResponse(200, {"results": []})

        client._session = FakeSession(responder)
        client.search_documents("hello world", limit=5, filters={"akten_id": "A-42"})
        assert captured.get("json") == {"Input": "hello world", "limit": 5}

    def test_the_legacy_get_still_sends_them_as_parameters(self):
        client = make_client(use_secured_api=False, allow_legacy_api_fallback=True)
        captured = {}

        def responder(method, url, **kw):
            captured.update(kw)
            return FakeResponse(200, {"results": []})

        client._session = FakeSession(responder)
        client.search_documents("hello", limit=5, filters={"akten_id": "A-42"})
        assert captured["params"] == {"query": "hello", "limit": 5, "akten_id": "A-42"}
```

`tests/test_doc_fields_mock_contract.py` — in `_app_on_mock.RealClient.__init__` delete the line `                "encryption_matrix_path": "",`; in `TestRoutesOnTheMock.test_off_today_s_search` replace `assert set(sent) == {"Input", "limit", "top_k", ASSERTION_FIELD}` with `assert set(sent) == {"Input", "limit", ASSERTION_FIELD}`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_knovas_client_doc_fields.py::TestSearchBody tests/test_knovas_client_hardening.py::TestC5FiltersStayOnThePlatform tests/test_doc_fields_mock_contract.py::TestRoutesOnTheMock::test_off_today_s_search`
Expected: FAIL — `test_the_body_carries_only_what_knovas_reads` (body still has `top_k`, `filters`), `test_an_encryption_matrix_file_is_not_read` (`encryption_matrix` key in the body, attribute present), `test_limit_is_clamped_to_50` (`top_k` in body), `test_the_secured_body_carries_no_filters`, `test_off_today_s_search` (`top_k` sent). `test_the_legacy_get_still_sends_them_as_parameters` passes already (it guards the legacy path).

- [ ] **Step 3: Write minimal implementation**

`src/knovas_client.py`, `KnovasAPIClient.__init__` — replace

```python
        self._cert_lock = threading.RLock()

        self.encryption_matrix_path = (
            (self.config.get('api.encryption_matrix_path', '') or '').strip()
            or (os.getenv('SEMANTIX_ENCRYPTION_MATRIX_PATH') or '').strip()
        )

        self.endpoints = {
```

with

```python
        self._cert_lock = threading.RLock()

        self.endpoints = {
```

Replace everything from `    def _load_encryption_matrix(self) -> Optional[Any]:` through the end of `_secured_query_request_body` (its last line `        return body`, directly before `    def _build_session(self) -> requests.Session:`) with:

```python
    def _secured_query_request_body(
        self,
        query: Union[str, List[str]],
        limit: Optional[int] = None,
        where: Optional[Dict[str, Any]] = None,
        return_fields: Optional[Union[bool, List[str]]] = None,
    ) -> Dict[str, Any]:
        """The /secured/query body: the keys Knovas reads (1.5.0).

        ``Input`` and ``limit``; ``where`` and ``return_fields`` only when
        given, so a query without them is the body it has always been (D8).
        ``top_k``, ``filters`` and ``encryption_matrix`` are not sent: the
        server reads none of them (spec F7). ``limit`` is clamped to 50: the
        server answers 422 above it (QP:91-103), which made every "Mehr
        laden" past 50 a failed search.
        """
        if isinstance(query, list):
            inputs = [str(q).strip() for q in query if str(q).strip()]
            if not inputs:
                raise ValueError('Input must be a non-empty string or non-empty list of strings')
            body_input: Union[str, List[str]] = inputs if len(inputs) > 1 else inputs[0]
        else:
            q = str(query).strip()
            if not q:
                raise ValueError('Input must be a non-empty string or non-empty list of strings')
            body_input = q
        body: Dict[str, Any] = {'Input': body_input}
        if limit is not None and limit > 0:
            body['limit'] = min(int(limit), _SECURED_QUERY_MAX_LIMIT)
        if where is not None:
            body['where'] = where
        if return_fields is not None:
            body['return_fields'] = return_fields
        return body
```

In `search_documents`, replace the docstring line `            filters: Additional search filters` with

```python
            filters: Query parameters of the legacy GET search. /secured/query
                reads no filters, so the secured path does not send them
                (spec F7); the Platform applies its own filters to the answer.
```

and replace

```python
            return self._search_documents_secured(
                query=query, limit=limit, filters=filters, **extra)
```

with

```python
            return self._search_documents_secured(query=query, limit=limit, **extra)
```

In `_search_documents_secured`, delete the parameter line `        filters: Optional[Dict[str, Any]] = None,` and replace

```python
                data=self._secured_query_request_body(
                    query, limit=limit, filters=filters, **extra),
```

with

```python
                data=self._secured_query_request_body(query, limit=limit, **extra),
```

`src/web_interface/app.py`, in `search()` — replace

```python
                # exact_match is decided here, after Knovas answers -- it is not
                # something /secured/query knows about. Forwarding it would put
                # an unknown key in the request body and a warning in the log on
                # every single search.
```

with

```python
                # exact_match is decided here, after Knovas answers. /secured/query
                # reads no filters at all (the client leaves them out, spec F7);
                # only the legacy GET forwards them, as query parameters, so a
                # local-only key stays here.
```

`config/config.yaml` — replace

```yaml
  allow_legacy_api_fallback: "${SEMANTIX_ALLOW_LEGACY_API_FALLBACK:-false}"

  # When the tenant uses encrypted embeddings, POST /secured/query requires
  # "encryption_matrix" (orthogonal matrix JSON). Path to a JSON file, or empty.
  encryption_matrix_path: "${SEMANTIX_ENCRYPTION_MATRIX_PATH:-}"

  # Legacy authentication (only relevant in legacy/mock mode)
```

with

```yaml
  allow_legacy_api_fallback: "${SEMANTIX_ALLOW_LEGACY_API_FALLBACK:-false}"

  # Legacy authentication (only relevant in legacy/mock mode)
```

`docs/specifications.md` — replace `- Optional: \`SEMANTIX_CUSTOMER_ID\`, \`SEMANTIX_ENCRYPTION_MATRIX_PATH\`` with `- Optional: \`SEMANTIX_CUSTOMER_ID\``.

`scripts/search-probe.sh` — replace `    body = json.dumps({"Input": text, "limit": limit, "top_k": limit}).encode()` with `    body = json.dumps({"Input": text, "limit": limit}).encode()`.

`RELEASE_NOTES.md` — insert as the last bullet of `## Dokumentfelder (Dokumentwerte)`, directly before the line starting `Anleitung: [KnovasPlatform/docs/features/document-fields.md]` (keep one blank line before that line):

```markdown
- Die Suche schickt `top_k`, `filters` und `encryption_matrix` nicht mehr an
  Knovas, und `SEMANTIX_ENCRYPTION_MATRIX_PATH` wird nicht mehr gelesen: der
  Server liest keinen dieser Schluessel (Knovas 1.5.0). `limit` bleibt.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_knovas_client_doc_fields.py tests/test_knovas_client_hardening.py tests/test_doc_fields_mock_contract.py tests/test_knovas_client_secured_api.py tests/test_search_server_order.py tests/test_search_exact_match.py tests/test_web_search_doc_fields.py`
Expected: PASS.

Run: `grep -rn "top_k\|encryption_matrix\|ENCRYPTION_MATRIX" KnovasPlatform scripts docs/specifications.md --include=*.py --include=*.yaml --include=*.md --include=*.sh --include=*.js | grep -v "/docs/superpowers/"`
Expected: only the two lines of `$PF/src/web_interface/static/js/experiments_detail.js` (the `{"top_k": 20}` example of an experiment runner's parameters, not a Knovas query).

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $PF/src/knovas_client.py $PF/src/web_interface/app.py $PF/config/config.yaml \
  docs/specifications.md scripts/search-probe.sh RELEASE_NOTES.md \
  $PF/tests/test_knovas_client_doc_fields.py $PF/tests/test_knovas_client_hardening.py \
  $PF/tests/test_doc_fields_mock_contract.py
git commit -F - <<'EOF'
platform: send only the query keys Knovas reads

/secured/query reads Input, limit, where and return_fields (Knovas 1.5.0).
top_k, filters and encryption_matrix were never read, so the client stops
sending them and no longer loads SEMANTIX_ENCRYPTION_MATRIX_PATH; the
"forwarding filters" warning on every search goes with them. The legacy GET
search keeps its query parameters. Spec F7.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task FLD-2: `listing_only` held 5 minutes and called temporary (F6)

**Files:**
- Modify: `$PF/src/doc_fields_capability.py` (module docstring, constants, `settings`, `CapabilityCache.__init__`, `CapabilityCache.observe` docstring)
- Modify: `$PF/src/doc_fields_view.py` (new `FILTERS_TEMPORARILY_UNAVAILABLE`, `error_message`)
- Modify: `$PF/src/web_interface/admin_system.py` (`DOC_FIELDS_STATES`, `_doc_fields_check`)
- Modify: `$PF/config/config.yaml` (`web.doc_fields.calibration_recheck_seconds`)
- Modify: `KnovasPlatform/docs/features/document-fields.md`, `RELEASE_NOTES.md`, `scripts/doctor.sh`
- Test: `$PF/tests/test_doc_fields_capability.py`, `$PF/tests/test_doc_fields_view.py`, `$PF/tests/test_web_search_doc_fields.py`, `$PF/tests/test_web_admin_system_doc_fields.py`, `$PF/tests/test_doc_fields_render.py`

**Interfaces:**
- Consumes: —
- Produces: `doc_fields_capability.LISTING_ONLY_HOLD_SECONDS = 300` (default of `web.doc_fields.calibration_recheck_seconds` and of `CapabilityCache(calibration_recheck=...)`; `DEFAULT_CALIBRATION_RECHECK` is removed); `doc_fields_view.FILTERS_TEMPORARILY_UNAVAILABLE = "Feldfilter bei Knovas vorübergehend nicht verfügbar – später erneut versuchen."` (returned for `where_requires_calibration` / `filters_need_calibration`); `admin_system.DOC_FIELDS_STATES["listing_only"] == "Werte + Liste (Feldfilter bei Knovas vorübergehend nicht verfügbar)"`.

- [ ] **Step 1: Write the failing test**

`tests/test_doc_fields_capability.py` — in `_cache` replace `    kw.setdefault("calibration_recheck", 3600)` with `    kw.setdefault("calibration_recheck", cap.LISTING_ONLY_HOLD_SECONDS)`. Replace the whole test `test_needs_calibration_is_held_past_a_probe` with:

```python
    def test_needs_calibration_is_held_past_a_probe(self, clock):
        """The probe cannot see calibration (find never checks it); if it
        could lift the hold, every TTL would offer the filter rail again and
        the next filtered search would meet the same 503."""
        client, cache = ProbeClient("filters"), _cache(clock, ttl=60)
        cache.get(client)
        cache.observe("needs_calibration")
        for _ in range(4):
            clock.advance(70)  # past the 60 s capability TTL, inside the hold
            assert cache.get(client) is Capability.listing_only
        assert client.probes == 1
        clock.advance(21)  # 301 s after the signal
        assert cache.get(client) is Capability.filters and client.probes == 2

    def test_the_hold_lasts_five_minutes(self, clock):
        """F6: Knovas 1.5.0 calls 503 where_requires_calibration "a problem on
        the Knovas side. Try again later." -- five minutes, not an hour."""
        assert cap.LISTING_ONLY_HOLD_SECONDS == 300
        client, cache = ProbeClient("filters"), CapabilityCache(clock=clock)
        cache.get(client)
        cache.observe("needs_calibration")
        clock.advance(299)
        assert cache.get(client) is Capability.listing_only and client.probes == 1
        clock.advance(2)
        assert cache.get(client) is Capability.filters and client.probes == 2
```

In `TestSettings.test_defaults` replace `s.registry_cache_seconds, s.find_page_size) == (300, 30, 3600, 300, 50)` with `s.registry_cache_seconds, s.find_page_size) == (300, 30, 300, 300, 50)`, and add to class `TestSettings`:

```python
    def test_the_shipped_config_holds_listing_only_for_five_minutes(self):
        """config/config.yaml sets the hold explicitly; it must say five
        minutes too, or the code default never applies."""
        import pathlib

        from config_loader import ConfigLoader

        path = pathlib.Path(__file__).resolve().parents[1] / "config" / "config.yaml"
        assert cap.settings(ConfigLoader(str(path))).calibration_recheck == 300
```

`tests/test_doc_fields_view.py`, class `TestMessages` — replace `test_missing_calibration_is_never_called_temporary` with:

```python
    def test_missing_calibration_is_a_temporary_problem_at_knovas(self):
        """F6: Knovas 1.5.0 lists 503 where_requires_calibration as "a problem
        on the Knovas side. Try again later." -- not a setup step."""
        text = view.error_message("where_requires_calibration")
        assert text == ("Feldfilter bei Knovas vorübergehend nicht verfügbar "
                        "– später erneut versuchen.")
        assert text == view.FILTERS_TEMPORARILY_UNAVAILABLE
        assert "Kalibrierung" not in text
        assert view.error_message("filters_need_calibration") == text
```

`tests/test_web_search_doc_fields.py`, class `TestRefusedFilter` — replace `test_calibration_message_is_not_temporary` with:

```python
    def test_calibration_message_says_try_again_later(self, filters_app, identity_repo):
        """F6: a temporary problem at Knovas, never a missing setup step."""
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        api.fail_call("search_documents", 503, "where_requires_calibration")
        body = search(client, where={"doc_type": "invoice"}).get_json()
        assert "vorübergehend nicht verfügbar" in body["error"]
        assert "Kalibrierung" not in body["error"]
```

`tests/test_web_admin_system_doc_fields.py`, class `TestFourStates` — replace `test_listing_only_names_the_missing_calibration` with:

```python
    def test_listing_only_says_filters_are_temporarily_unavailable(self):
        import doc_fields_capability as dfc

        client = FakeDocFieldsApi("listing_only")
        dfc.capability_for(client)
        dfc.observe("needs_calibration")
        check = _doc_fields(_collect(client))
        assert check["state"] == "warn"
        assert check["detail"].startswith(
            "Werte + Liste (Feldfilter bei Knovas vorübergehend nicht verfügbar)")
        assert "vorübergehend nicht verfügbar" in check["hint"]
        assert "später erneut versuchen" in check["hint"]
        assert "Kalibrierung" not in check["detail"] + check["hint"]
```

`tests/test_doc_fields_render.py` — in `SYSTEM_LABELS` replace `    "listing_only": "Werte + Liste (Filter in der Suche: Kalibrierung bei Knovas fehlt)",` with `    "listing_only": "Werte + Liste (Feldfilter bei Knovas vorübergehend nicht verfügbar)",`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_doc_fields_capability.py tests/test_doc_fields_view.py::TestMessages tests/test_web_search_doc_fields.py::TestRefusedFilter tests/test_web_admin_system_doc_fields.py tests/test_doc_fields_render.py`
Expected: FAIL — `AttributeError: module 'doc_fields_capability' has no attribute 'LISTING_ONLY_HOLD_SECONDS'`, the shipped config still says 3600, `view.FILTERS_TEMPORARILY_UNAVAILABLE` missing, the System tab still says "Kalibrierung bei Knovas fehlt".

- [ ] **Step 3: Write minimal implementation**

`src/doc_fields_capability.py` — in the module docstring replace

```
``listing_only`` cannot be probed -- ``find`` never checks calibration -- so
it is learned from the first query that answers 503
``where_requires_calibration`` and held for ``calibration_recheck_seconds``;
the probe does not lift it in the meantime.
```

with

```
``listing_only`` cannot be probed -- ``find`` never checks calibration -- so
it is learned from the first query that answers 503
``where_requires_calibration`` and held for ``calibration_recheck_seconds``
(``LISTING_ONLY_HOLD_SECONDS``, five minutes: Knovas 1.5.0 calls that answer
a temporary problem on its side); the probe does not lift it in the meantime.
```

Replace

```python
DEFAULT_CAPABILITY_TTL = 300
DEFAULT_UNKNOWN_TTL = 30
DEFAULT_CALIBRATION_RECHECK = 3600
```

with

```python
DEFAULT_CAPABILITY_TTL = 300
DEFAULT_UNKNOWN_TTL = 30
#: How long a 503 ``where_requires_calibration`` holds the capability at
#: ``listing_only`` (search filters hidden, listing and card values kept)
#: before the probe may lift it. Knovas 1.5.0 calls that answer "a problem
#: on the Knovas side. Try again later." -- temporary, so the Platform asks
#: again after five minutes, not after an hour (spec F6).
LISTING_ONLY_HOLD_SECONDS = 300
```

In `settings()` replace `                                     DEFAULT_CALIBRATION_RECHECK, 1, 7 * 86400),` with `                                     LISTING_ONLY_HOLD_SECONDS, 1, 7 * 86400),`. In `CapabilityCache.__init__` replace `                 calibration_recheck: float = DEFAULT_CALIBRATION_RECHECK,` with `                 calibration_recheck: float = LISTING_ONLY_HOLD_SECONDS,`. In the `observe` docstring replace `        ``needs_calibration`` -> listing_only, held for the recheck period.` with `        ``needs_calibration`` -> listing_only, held for the recheck period (``LISTING_ONLY_HOLD_SECONDS`` unless configured).`

`src/doc_fields_view.py` — after the `RAIL_NOT_APPLIED_TO_SEARCH = (...)` constant add:

```python
# 503 where_requires_calibration: Knovas 1.5.0 calls it "a problem on the
# Knovas side. Try again later." (spec F6) -- temporary, not a setup step.
FILTERS_TEMPORARILY_UNAVAILABLE = (
    "Feldfilter bei Knovas vorübergehend nicht verfügbar – "
    "später erneut versuchen."
)
```

In `error_message` replace

```python
    if code in ("where_requires_calibration", "filters_need_calibration"):
        # H9: missing calibration is a setup step at Knovas, not an outage.
        return ("Filter in der Suche sind bei Knovas noch nicht eingerichtet "
                "(Kalibrierung fehlt).")
```

with

```python
    if code in ("where_requires_calibration", "filters_need_calibration"):
        return FILTERS_TEMPORARILY_UNAVAILABLE
```

`src/web_interface/admin_system.py` — replace `    "listing_only": "Werte + Liste (Filter in der Suche: Kalibrierung bei Knovas fehlt)",` with `    "listing_only": "Werte + Liste (Feldfilter bei Knovas vorübergehend nicht verfügbar)",` and replace

```python
            hint="Filter in der Suche sind bei Knovas noch nicht eingerichtet (Kalibrierung "
                 "fehlt). Werte, Liste und Feldfilter in der Verwaltung funktionieren."), True
```

with

```python
            hint="Feldfilter in der Suche sind bei Knovas vorübergehend nicht "
                 "verfügbar – später erneut versuchen; die Plattform fragt in "
                 "wenigen Minuten erneut. Werte, Liste und Feldfilter in der Verwaltung "
                 "funktionieren."), True
```

`config/config.yaml` — replace

```yaml
    # After Knovas says filters need a relevance calibration, how long the
    # Platform keeps the listing without the search filters before asking again.
    calibration_recheck_seconds: 3600
```

with

```yaml
    # After Knovas answers a filtered search with 503
    # where_requires_calibration ("a problem on the Knovas side, try again
    # later"), how long the Platform keeps the listing without the search
    # filters before asking again.
    calibration_recheck_seconds: 300
```

`KnovasPlatform/docs/features/document-fields.md` — replace the table row starting `| \`Werte + Liste (Filter in der Suche: Kalibrierung bei Knovas fehlt)\`` with

```markdown
| `Werte + Liste (Feldfilter bei Knovas vorübergehend nicht verfügbar)` | values and listing; Knovas answered a filtered search with `503 where_requires_calibration` ("a problem on the Knovas side, try again later") | additionally: typed values on result cards, the **Liste anzeigen** listing, the admin **Feldfilter** |
```

and replace

```markdown
shown as `aus` for 30 seconds. The "listing without filters" state is learned
from the first filtered search Knovas refuses for a missing calibration and is
kept for `calibration_recheck_seconds` (1 hour); the first filtered search
after that may meet the same answer once more.
```

with

```markdown
shown as `aus` for 30 seconds. The "listing without filters" state is learned
from the first filtered search Knovas answers with `503
where_requires_calibration` — Knovas 1.5.0 calls it a temporary problem on its
side — and is kept for `calibration_recheck_seconds` (5 minutes); the first
filtered search after that may meet the same answer once more.
```

`RELEASE_NOTES.md` — the System-tab state list names the old label (split across a line break: `` `Werte + Liste (Filter in`` / ``der Suche: Kalibrierung bei Knovas fehlt)` ``); replace that label with `` `Werte + Liste (Feldfilter bei Knovas voruebergehend nicht verfuegbar)` `` and re-wrap the paragraph at 79 columns.

`scripts/doctor.sh` — replace the comment lines

```
# Whether search filters are calibrated cannot be probed; the first filtered
# search says so.
```

with

```
# Whether Knovas can apply search filters right now cannot be probed; the
# first filtered search says so.
```

and replace

```python
    print("         Whether search filters are calibrated cannot be probed; the first filtered")
    print("         search says so (System tab: 'Kalibrierung bei Knovas fehlt').")
```

with

```python
    print("         Whether Knovas can apply search filters right now cannot be probed; the")
    print("         first filtered search says so (System tab: 'Feldfilter bei Knovas")
    print("         voruebergehend nicht verfuegbar').")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_doc_fields_capability.py tests/test_doc_fields_view.py tests/test_web_search_doc_fields.py tests/test_web_admin_system_doc_fields.py tests/test_doc_fields_render.py tests/test_web_documents_find.py`
Expected: PASS. Then `mock-pytest tests/test_doctor_doc_fields_probe.py` → PASS, and `grep -rn "Kalibrierung" RELEASE_NOTES.md scripts/doctor.sh KnovasPlatform/docs $PF/src $PF/tests` → no output.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $PF/src/doc_fields_capability.py $PF/src/doc_fields_view.py \
  $PF/src/web_interface/admin_system.py $PF/config/config.yaml \
  KnovasPlatform/docs/features/document-fields.md RELEASE_NOTES.md scripts/doctor.sh \
  $PF/tests/test_doc_fields_capability.py $PF/tests/test_doc_fields_view.py \
  $PF/tests/test_web_search_doc_fields.py $PF/tests/test_web_admin_system_doc_fields.py \
  $PF/tests/test_doc_fields_render.py
git commit -F - <<'EOF'
platform: listing_only is a temporary state, held five minutes

Knovas 1.5.0 lists 503 where_requires_calibration as "a problem on the
Knovas side. Try again later." The Platform kept the listing_only state for
an hour and told people filters were "noch nicht eingerichtet (Kalibrierung
fehlt)". It now holds the state for LISTING_ONLY_HOLD_SECONDS (300, also the
shipped config value) and says "Feldfilter bei Knovas voruebergehend nicht
verfuegbar - spaeter erneut versuchen" in the search, the System tab, the
docs and doctor.sh. Spec F6.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task FLD-3: Field forms — how values are read, and the field counter (F1)

**Files:**
- Modify: `$PF/src/web_interface/admin_doc_fields.py` (vocabulary; new `reading_from_form`, `_select`, `_fy_start_month`, `_check_fiscal_year`, `reading_text`, `field_count_text`; `definition_from_form`, `changes_from_form`, `registry_rows`, `refill_field_row`, `_page`)
- Modify: `$PF/src/web_interface/templates/admin_doc_fields.html` (edit form, create form, Typ column, counter)
- Modify: `$PF/src/doc_fields_view.py` (`error_message` for `field_type_locked`)
- Test: `$PF/tests/test_web_admin_doc_fields.py`, `$PF/tests/test_doc_fields_view.py`

**Interfaces:**
- Consumes: —
- Produces (in `web_interface.admin_doc_fields`): `CODE_SCHEMES = ("generic", "che_uid", "iban", "qr_ref", "bger", "bvger", "ecli", "icd10gm", "bcp47")`, `LINK_POLICIES = ("resolve", "never")`, `FY_LABELS = ("start", "end")`, `FIELD_CAP = 256`, `READING_DEFAULTS: Dict[str, Any]`, `reading_from_form(form, datatype) -> Dict[str, Any]`, `reading_text(raw: Mapping) -> str`, `field_count_text(raw_fields) -> str`; form inputs `code_scheme`, `link_policy`, `fy_start_month`, `fy_label`, `date_order`; registry row keys `code_scheme`, `link_policy`, `fy_start_month` (int or None), `fy_label` (`""`/`start`/`end`), `date_order` (`""`/`dmy`/`mdy`/`ymd`), `reading`; template context keys `code_schemes`, `link_policies`, `fy_months`, `fy_labels`, `field_count_text`.

- [ ] **Step 1: Write the failing test**

`tests/test_web_admin_doc_fields.py` — add after class `TestChangesFromForm`:

```python
class TestReadingSettings:
    """F1: how values of a field are read -- per datatype, sent on create
    only when it differs from Knovas's default, on edit only when changed."""

    def test_each_setting_goes_only_with_its_type(self):
        from web_interface.admin_doc_fields import definition_from_form

        everything = {"code_scheme": "bger", "link_policy": "never", "fy_start_month": "7",
                      "fy_label": "start", "date_order": "mdy"}
        code = definition_from_form({"key": "aktenzeichen", "datatype": "code", **everything})
        assert code["code_scheme"] == "bger"
        assert not {"link_policy", "fy_start_month", "fy_label", "date_order"} & set(code)
        entity = definition_from_form({"key": "gegenpartei", "datatype": "entity_ref",
                                       **everything})
        assert entity["link_policy"] == "never" and "code_scheme" not in entity
        period = definition_from_form({"key": "geschaeftsjahr", "datatype": "period",
                                       **everything})
        assert (period["fy_start_month"], period["fy_label"]) == (7, "start")
        date = definition_from_form({"key": "eingang", "datatype": "date", **everything})
        assert date["date_order"] == "mdy" and "fy_start_month" not in date

    def test_defaults_are_not_sent(self):
        from web_interface.admin_doc_fields import definition_from_form

        assert "code_scheme" not in definition_from_form(
            {"key": "belegnummer", "datatype": "code", "code_scheme": "generic"})
        assert "link_policy" not in definition_from_form(
            {"key": "gegenpartei", "datatype": "entity_ref", "link_policy": "resolve"})
        period = definition_from_form({"key": "jahr", "datatype": "period",
                                       "fy_start_month": "", "fy_label": ""})
        assert "fy_start_month" not in period and "fy_label" not in period
        assert "date_order" not in definition_from_form(
            {"key": "eingang", "datatype": "date", "date_order": ""})
        january = definition_from_form({"key": "jahr", "datatype": "period",
                                        "fy_start_month": "1"})
        assert january["fy_start_month"] == 1 and "fy_label" not in january

    @pytest.mark.parametrize("form", [
        {"key": "k", "datatype": "code", "code_scheme": "isbn"},
        {"key": "k", "datatype": "entity_ref", "link_policy": "maybe"},
        {"key": "k", "datatype": "period", "fy_start_month": "13", "fy_label": "start"},
        {"key": "k", "datatype": "period", "fy_start_month": "Juli", "fy_label": "start"},
        {"key": "k", "datatype": "period", "fy_start_month": "7"},
        {"key": "k", "datatype": "period", "fy_label": "middle"},
        {"key": "k", "datatype": "date", "date_order": "dym"},
    ])
    def test_refused_before_knovas(self, form):
        from web_interface.admin_doc_fields import FormError, definition_from_form

        with pytest.raises(FormError):
            definition_from_form(form)

    CODE = {"id": "f2", "key": "aktenzeichen", "datatype": "code", "code_scheme": "generic",
            "labels": {"de": "Aktenzeichen"}, "aliases": [], "display": False, "facet": False,
            "sensitivity": "normal", "warnings": []}
    PERIOD = {"id": "f3", "key": "geschaeftsjahr", "datatype": "period", "fy_start_month": 7,
              "fy_label": "start", "labels": {"de": "Geschäftsjahr"}, "aliases": [],
              "display": False, "facet": False, "sensitivity": "normal", "warnings": []}
    DATE = {"id": "f4", "key": "eingang", "datatype": "date", "date_order": None,
            "labels": {}, "display": False, "facet": False, "warnings": []}

    def test_an_edit_sends_only_changed_settings(self):
        from web_interface.admin_doc_fields import changes_from_form, needs_use_confirmation

        assert changes_from_form({"code_scheme": "generic"}, self.CODE) == {}
        changes = changes_from_form({"code_scheme": "bger"}, self.CODE)
        assert changes == {"code_scheme": "bger"} and needs_use_confirmation(changes)
        assert changes_from_form({"fy_start_month": "7", "fy_label": "start"},
                                 self.PERIOD) == {}
        assert changes_from_form({"fy_start_month": "", "fy_label": ""}, self.PERIOD) == {
            "fy_start_month": None, "fy_label": None}
        assert changes_from_form({"date_order": ""}, self.DATE) == {}
        assert changes_from_form({"date_order": "ymd"}, self.DATE) == {"date_order": "ymd"}
        # A setting of another datatype is never read from the form.
        assert changes_from_form({"date_order": "ymd", "code_scheme": "iban"},
                                 self.PERIOD) == {}

    def test_a_business_year_keeps_its_naming(self):
        from web_interface.admin_doc_fields import FormError, changes_from_form

        with pytest.raises(FormError):
            changes_from_form({"fy_label": ""}, self.PERIOD)  # July stays, naming gone
        assert changes_from_form({"fy_start_month": "9"}, self.PERIOD) == {"fy_start_month": 9}

    def test_reading_text_for_the_registry_table(self):
        from web_interface.admin_doc_fields import reading_text

        assert reading_text({"datatype": "code", "code_scheme": "iban"}) == \
            "Schema: IBAN (mit Prüfziffer)"
        assert reading_text({"datatype": "code", "code_scheme": "generic"}) == ""
        assert reading_text(self.PERIOD) == \
            "Geschäftsjahr ab Juli, benannt nach dem Anfangsjahr"
        assert reading_text({"datatype": "date", "date_order": "mdy"}) == \
            "liest 03/04/2024 als Monat/Tag/Jahr"
        assert reading_text({"datatype": "entity_ref", "link_policy": "never"}) == \
            "Namen bleiben unverknüpft"
        assert reading_text({"datatype": "text"}) == ""

    def test_the_field_counter(self):
        from web_interface.admin_doc_fields import field_count_text

        assert field_count_text(core_fields()) == "12 von 256 Feldern"
        full = [field_def(f"f{i:03d}", "text", "F") for i in range(256)]
        assert field_count_text(full).startswith(
            "256 von 256 Feldern – die Höchstzahl ist erreicht")
```

Add after class `TestRegistryWrites`:

```python
@needs_db
class TestReadingSettingsOnThePage:
    def test_the_page_counts_the_fields_and_offers_the_settings(self, as_admin):
        html = as_admin.get("/admin/doc-fields").data.decode("utf-8")
        assert "12 von 256 Feldern" in html
        create = html[html.index("doc-field-create-form"):]
        create = create[:create.index("</form>")]
        for name in ("code_scheme", "link_policy", "fy_start_month", "fy_label", "date_order"):
            assert f'name="{name}"' in create, name
        language = next(f for f in FakeDocFieldsApi.current.registry if f["key"] == "language")
        form = html[html.index(f'/admin/doc-fields/{language["id"]}/update'):]
        form = form[:form.index("</form>")]
        assert '<option value="generic" selected>' in form
        assert 'name="fy_start_month"' not in form

    def test_create_sends_the_settings(self, as_admin):
        _post(as_admin, "/admin/doc-fields/create", key="geschaeftsjahr", datatype="period",
              fy_start_month="7", fy_label="start", code_scheme="iban", date_order="mdy")
        sent = _calls(FakeDocFieldsApi.current, "create_doc_field")[0]["defn"]
        assert (sent["fy_start_month"], sent["fy_label"]) == (7, "start")
        assert "code_scheme" not in sent and "date_order" not in sent

    def test_a_locked_setting_is_explained(self, as_admin):
        api = FakeDocFieldsApi.current
        language = next(f for f in api.registry if f["key"] == "language")
        api.fail_call("update_doc_field", 409, "field_type_locked")
        response = _post(as_admin, f"/admin/doc-fields/{language['id']}/update",
                         code_scheme="bcp47")
        assert response.status_code == 409
        assert "Kennungsschema" in response.data.decode("utf-8")
        assert _calls(api, "update_doc_field")[0]["changes"] == {"code_scheme": "bcp47"}

    def test_the_confirmation_keeps_the_posted_setting(self, as_admin, platform_db, admin):
        _profile(platform_db, admin, fields={"language": "de-CH"})
        api = FakeDocFieldsApi.current
        language = next(f for f in api.registry if f["key"] == "language")
        refused = _post(as_admin, f"/admin/doc-fields/{language['id']}/update",
                        code_scheme="bcp47")
        assert refused.status_code == 409
        assert _calls(api, "update_doc_field") == []
        html = refused.data.decode("utf-8")
        form = html[html.index(f'/admin/doc-fields/{language["id"]}/update'):]
        form = form[:form.index("</form>")]
        assert '<option value="bcp47" selected>' in form
```

In `TestFeatureOff.test_without_a_knovas_connector_the_prefix_is_typed` replace `            datatypes=[], date_roles=[], unknown_key_modes=[], date_orders=[],` with

```python
            datatypes=[], date_roles=[], unknown_key_modes=[], date_orders=[],
            code_schemes=[], link_policies=[], fy_months=[], fy_labels=[],
            field_count_text="",
```

`tests/test_doc_fields_view.py`, class `TestMessages` — add:

```python
    def test_a_locked_field_names_what_is_locked(self):
        """F1: type, code scheme, business year, date order and existing
        choices lock together (Knovas 409 field_type_locked)."""
        text = view.error_message("field_type_locked")
        assert "nicht mehr möglich" in text
        for word in ("Typ", "Kennungsschema", "Geschäftsjahr", "Datumsreihenfolge",
                     "Auswahlwerte"):
            assert word in text, word
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_web_admin_doc_fields.py::TestReadingSettings tests/test_web_admin_doc_fields.py::TestReadingSettingsOnThePage tests/test_doc_fields_view.py::TestMessages::test_a_locked_field_names_what_is_locked`
Expected: FAIL — `ImportError: cannot import name 'reading_text'` / `'field_count_text'`, the defn lacks `code_scheme`, `FormError` not raised, the page has no counter, the locked text names only the type.

- [ ] **Step 3: Write minimal implementation**

`src/web_interface/admin_doc_fields.py` — replace

```python
DATE_ORDERS = ("dmy", "mdy", "ymd")
LABEL_LANGS = ("de", "fr", "it", "en")
```

with

```python
DATE_ORDERS = ("dmy", "mdy", "ymd")
LABEL_LANGS = ("de", "fr", "it", "en")
CODE_SCHEMES = ("generic", "che_uid", "iban", "qr_ref", "bger", "bvger", "ecli", "icd10gm",
                "bcp47")
LINK_POLICIES = ("resolve", "never")
FY_LABELS = ("start", "end")

#: An account can have up to 256 fields (Knovas 1.5.0); retired ones count.
FIELD_CAP = 256

#: What Knovas applies when a field definition leaves a setting out (spec
#: F1). Create sends a setting only when it differs from this.
READING_DEFAULTS: Dict[str, Any] = {
    "code_scheme": "generic", "link_policy": "resolve",
    "fy_start_month": None, "fy_label": None, "date_order": None,
}
```

After the `DATE_ORDER_LABELS = {...}` dict add:

```python
CODE_SCHEME_LABELS = {
    "generic": "allgemein (ohne Prüfung)",
    "che_uid": "UID (CHE-123.456.789, mit Prüfziffer)",
    "iban": "IBAN (mit Prüfziffer)",
    "qr_ref": "QR-Referenz (mit Prüfziffer)",
    "bger": "Geschäftsnummer Bundesgericht (4A_123/2024)",
    "bvger": "Geschäftsnummer Bundesverwaltungsgericht (E-2228/2020)",
    "ecli": "ECLI",
    "icd10gm": "ICD-10-GM (E11.90)",
    "bcp47": "Sprachcode (de-CH)",
}
LINK_POLICY_LABELS = {
    "resolve": "mit Einträgen des Wissensgraphen verknüpfen",
    "never": "nie verknüpfen (Namen bleiben Namen)",
}
FY_LABEL_LABELS = {
    "start": "nach dem Anfangsjahr (GJ 2024 = 2024/25)",
    "end": "nach dem Endjahr (GJ 2024 = 2023/24)",
}
MONTH_LABELS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                "September", "Oktober", "November", "Dezember")
```

After `EXPIRED_FORM = "Formular ist abgelaufen. Bitte erneut versuchen."` add:

```python
FY_LABEL_REQUIRED = (
    "Beginnt das Geschäftsjahr nicht im Januar, bitte angeben, ob „GJ 2024“ "
    "nach dem Anfangs- oder dem Endjahr benannt ist."
)
```

After `labels_from_form` add:

```python
def _select(form: Mapping[str, Any], name: str, allowed: Sequence[str],
            message: str) -> Optional[str]:
    """A select's value: None when empty, else one of ``allowed``."""
    value = _text(form, name)
    if not value:
        return None
    if value not in allowed:
        raise FormError(message)
    return value


def _fy_start_month(form: Mapping[str, Any]) -> Optional[int]:
    value = _text(form, "fy_start_month")
    if not value:
        return None
    if not value.isdigit() or not 1 <= int(value) <= 12:
        raise FormError("Der Monat, in dem das Geschäftsjahr beginnt, ist 1 bis 12.")
    return int(value)


def _check_fiscal_year(month: Any, label: Any) -> None:
    """Knovas refuses a business year that does not start in January
    without its naming (registry.py, ``fy_label``)."""
    if month not in (None, 1) and not label:
        raise FormError(FY_LABEL_REQUIRED)


def reading_from_form(form: Mapping[str, Any], datatype: Any) -> Dict[str, Any]:
    """How values of a ``datatype`` are read, as the form sets it (spec F1):
    ``code_scheme`` for a code, ``link_policy`` for an entity field,
    ``fy_start_month`` and ``fy_label`` for a period, ``date_order`` for a
    date. Only inputs the form carries, and only those of its datatype; an
    empty select is Knovas's default (``READING_DEFAULTS``)."""
    out: Dict[str, Any] = {}
    if datatype == "code" and "code_scheme" in form:
        out["code_scheme"] = _select(form, "code_scheme", CODE_SCHEMES,
                                     "Unbekanntes Kennungsschema.") or "generic"
    if datatype == "entity_ref" and "link_policy" in form:
        out["link_policy"] = _select(form, "link_policy", LINK_POLICIES,
                                     "Unbekannte Verknüpfungsregel.") or "resolve"
    if datatype == "period":
        if "fy_start_month" in form:
            out["fy_start_month"] = _fy_start_month(form)
        if "fy_label" in form:
            out["fy_label"] = _select(form, "fy_label", FY_LABELS,
                                      "Unbekannte Benennung des Geschäftsjahres.")
    if datatype == "date" and "date_order" in form:
        out["date_order"] = _select(form, "date_order", DATE_ORDERS,
                                    "Unbekannte Datumsreihenfolge.")
    return out
```

In `definition_from_form` replace

```python
    defn["display"] = _checked(form, "display")
    defn["facet"] = _checked(form, "facet")
    defn["sensitivity"] = sensitivity
    return defn
```

with

```python
    reading = reading_from_form(form, datatype)
    _check_fiscal_year(reading.get("fy_start_month"), reading.get("fy_label"))
    for name, value in reading.items():
        if value != READING_DEFAULTS[name]:
            defn[name] = value
    defn["display"] = _checked(form, "display")
    defn["facet"] = _checked(form, "facet")
    defn["sensitivity"] = sensitivity
    return defn
```

In `changes_from_form` replace

```python
        if role != (current.get("date_role") or None):
            changes["date_role"] = role
    return changes
```

with

```python
        if role != (current.get("date_role") or None):
            changes["date_role"] = role
    reading = reading_from_form(form, datatype)
    if "fy_start_month" in reading or "fy_label" in reading:
        _check_fiscal_year(reading.get("fy_start_month", current.get("fy_start_month")),
                           reading.get("fy_label", current.get("fy_label")))
    for name, value in reading.items():
        stored = current.get(name)
        if (READING_DEFAULTS[name] if stored is None else stored) != value:
            changes[name] = value
    return changes
```

Before `def registry_rows(` add:

```python
def reading_text(raw: Mapping[str, Any]) -> str:
    """How a field reads values, in words for the registry table: a code
    scheme other than ``generic``, a business year that does not start in
    January, a date field's own order, names that are never linked. Empty
    when the field reads as Knovas does by default."""
    datatype = raw.get("datatype")
    if datatype == "code":
        scheme = str(raw.get("code_scheme") or "generic")
        return "" if scheme == "generic" else f"Schema: {CODE_SCHEME_LABELS.get(scheme, scheme)}"
    if datatype == "period":
        month = raw.get("fy_start_month")
        if isinstance(month, int) and not isinstance(month, bool) and 2 <= month <= 12:
            naming = {"start": ", benannt nach dem Anfangsjahr",
                      "end": ", benannt nach dem Endjahr"}.get(str(raw.get("fy_label") or ""), "")
            return f"Geschäftsjahr ab {MONTH_LABELS[month - 1]}{naming}"
        return ""
    if datatype == "date" and raw.get("date_order") in DATE_ORDERS:
        return f"liest 03/04/2024 als {DATE_ORDER_LABELS[raw['date_order']].split(' (')[0]}"
    if datatype == "entity_ref" and raw.get("link_policy") == "never":
        return "Namen bleiben unverknüpft"
    return ""


def field_count_text(raw_fields: Any) -> str:
    """"n von 256 Feldern" (spec F1). Every field Knovas lists counts,
    retired ones too: the cap counts every registry row."""
    count = sum(1 for raw in raw_fields or ()
                if isinstance(raw, Mapping) and isinstance(raw.get("key"), str))
    text = f"{count} von {FIELD_CAP} Feldern"
    if count >= FIELD_CAP:
        text += (" – die Höchstzahl ist erreicht (stillgelegte Felder "
                 "zählen mit).")
    return text
```

In `registry_rows` replace

```python
            "date_role_label": DATE_ROLE_LABELS.get(date_role or "", ""),
```

with

```python
            "date_role_label": DATE_ROLE_LABELS.get(date_role or "", ""),
            "code_scheme": str(raw.get("code_scheme") or "generic"),
            "link_policy": "never" if raw.get("link_policy") == "never" else "resolve",
            "fy_start_month": (raw.get("fy_start_month")
                               if isinstance(raw.get("fy_start_month"), int)
                               and not isinstance(raw.get("fy_start_month"), bool) else None),
            "fy_label": raw.get("fy_label") if raw.get("fy_label") in FY_LABELS else "",
            "date_order": raw.get("date_order") if raw.get("date_order") in DATE_ORDERS else "",
            "reading": reading_text(raw),
```

In `refill_field_row` replace

```python
    if "date_role" in form and _text(form, "date_role") in ("", *DATE_ROLES):
        row["date_role"] = _text(form, "date_role")
```

with

```python
    if "date_role" in form and _text(form, "date_role") in ("", *DATE_ROLES):
        row["date_role"] = _text(form, "date_role")
    for name, allowed in (("code_scheme", CODE_SCHEMES), ("link_policy", LINK_POLICIES),
                          ("fy_label", ("", *FY_LABELS)), ("date_order", ("", *DATE_ORDERS))):
        if name in form and _text(form, name) in allowed:
            row[name] = _text(form, name)
    if "fy_start_month" in form:
        month = _text(form, "fy_start_month")
        if month == "" or (month.isdigit() and 1 <= int(month) <= 12):
            row["fy_start_month"] = int(month) if month else None
```

In `_page` replace

```python
            "date_orders": [(o, DATE_ORDER_LABELS[o]) for o in DATE_ORDERS],
```

with

```python
            "date_orders": [(o, DATE_ORDER_LABELS[o]) for o in DATE_ORDERS],
            "code_schemes": [(s, CODE_SCHEME_LABELS[s]) for s in CODE_SCHEMES],
            "link_policies": [(p, LINK_POLICY_LABELS[p]) for p in LINK_POLICIES],
            "fy_months": [(m, MONTH_LABELS[m - 1]) for m in range(1, 13)],
            "fy_labels": [(label, FY_LABEL_LABELS[label]) for label in FY_LABELS],
```

replace

```python
        problems: List[str] = []
        raw_fields: List[Dict[str, Any]] = []
        try:
            raw_fields = client.doc_fields()
        except DocFieldsUnavailable as exc:
            dfc.observe_exception(exc)
            return _off_page(error=error, status=status)
        except Exception as exc:  # noqa: BLE001
            _log_failure("registry read", exc)
            problems.append("Das Feldverzeichnis ist derzeit nicht abrufbar.")
```

with

```python
        problems: List[str] = []
        raw_fields: List[Dict[str, Any]] = []
        registry_read = True
        try:
            raw_fields = client.doc_fields()
        except DocFieldsUnavailable as exc:
            dfc.observe_exception(exc)
            return _off_page(error=error, status=status)
        except Exception as exc:  # noqa: BLE001
            _log_failure("registry read", exc)
            problems.append("Das Feldverzeichnis ist derzeit nicht abrufbar.")
            registry_read = False
```

and in `context.update({` replace `            "fields": rows,` with

```python
            "fields": rows,
            "field_count_text": field_count_text(raw_fields) if registry_read else "",
```

`src/web_interface/templates/admin_doc_fields.html` — replace

```html
        <h2 class="section-label">Felder</h2>
```

with

```html
        <h2 class="section-label">Felder</h2>
        {% if field_count_text %}<p class="hint doc-fields-count">{{ field_count_text }}</p>{% endif %}
```

Replace

```html
                            <span class="sub">{{ f.cardinality_label }}{% if f.date_role_label %} · {{ f.date_role_label }}{% endif %}{% if f.target_name %} · Ziel: {{ f.target_name }}{% endif %}</span>
```

with

```html
                            <span class="sub">{{ f.cardinality_label }}{% if f.date_role_label %} · {{ f.date_role_label }}{% endif %}{% if f.reading %} · {{ f.reading }}{% endif %}{% if f.target_name %} · Ziel: {{ f.target_name }}{% endif %}</span>
```

In the edit form insert directly before the line `                                        <label class="admin-check"><input type="checkbox" name="display" value="1" {% if f.display %}checked{% endif %}> <span>auf Trefferkarten zeigen</span></label>`:

```html
                                        {% if f.datatype == 'code' %}
                                        <label>Kennungsschema
                                            <select name="code_scheme">
                                                {% for value, label in code_schemes %}
                                                <option value="{{ value }}" {% if value == f.code_scheme %}selected{% endif %}>{{ label }}</option>
                                                {% endfor %}
                                            </select>
                                        </label>
                                        {% endif %}
                                        {% if f.datatype == 'entity_ref' %}
                                        <label>Namen
                                            <select name="link_policy">
                                                {% for value, label in link_policies %}
                                                <option value="{{ value }}" {% if value == f.link_policy %}selected{% endif %}>{{ label }}</option>
                                                {% endfor %}
                                            </select>
                                        </label>
                                        {% endif %}
                                        {% if f.datatype == 'period' %}
                                        <label>Geschäftsjahr beginnt im
                                            <select name="fy_start_month">
                                                <option value="" {% if f.fy_start_month is none %}selected{% endif %}>— Kalenderjahr (Standard) —</option>
                                                {% for value, label in fy_months %}
                                                <option value="{{ value }}" {% if value == f.fy_start_month %}selected{% endif %}>{{ label }}</option>
                                                {% endfor %}
                                            </select>
                                        </label>
                                        <label>Geschäftsjahr benannt
                                            <select name="fy_label">
                                                <option value="" {% if not f.fy_label %}selected{% endif %}>— nicht festgelegt —</option>
                                                {% for value, label in fy_labels %}
                                                <option value="{{ value }}" {% if value == f.fy_label %}selected{% endif %}>{{ label }}</option>
                                                {% endfor %}
                                            </select>
                                        </label>
                                        {% endif %}
                                        {% if f.datatype == 'date' %}
                                        <label>Datumsangaben mit Schrägstrich lesen als
                                            <select name="date_order">
                                                <option value="" {% if not f.date_order %}selected{% endif %}>Kontoeinstellung (Standard)</option>
                                                {% for value, label in date_orders %}
                                                <option value="{{ value }}" {% if value == f.date_order %}selected{% endif %}>{{ label }}</option>
                                                {% endfor %}
                                            </select>
                                        </label>
                                        {% endif %}
                                        {% if f.datatype in ('code', 'period', 'date') %}
                                        <p class="hint">Wie Werte gelesen werden, lässt sich nur bei vorläufigen, kaum genutzten Feldern ändern.</p>
                                        {% endif %}
```

In the create form insert directly after the `<label data-df-only="date period">Datumsrolle … </label>` block (before `<label class="admin-check"><input type="checkbox" name="display" value="1"> <span>auf Trefferkarten zeigen</span></label>`):

```html
                <label data-df-only="code">Kennungsschema
                    <select name="code_scheme">
                        {% for value, label in code_schemes %}<option value="{{ value }}">{{ label }}</option>{% endfor %}
                    </select>
                </label>
                <label data-df-only="entity_ref">Namen
                    <select name="link_policy">
                        {% for value, label in link_policies %}<option value="{{ value }}">{{ label }}</option>{% endfor %}
                    </select>
                </label>
                <label data-df-only="period">Geschäftsjahr beginnt im
                    <select name="fy_start_month">
                        <option value="">— Kalenderjahr (Standard) —</option>
                        {% for value, label in fy_months %}<option value="{{ value }}">{{ label }}</option>{% endfor %}
                    </select>
                </label>
                <label data-df-only="period">Geschäftsjahr benannt
                    <select name="fy_label">
                        <option value="">— nicht festgelegt —</option>
                        {% for value, label in fy_labels %}<option value="{{ value }}">{{ label }}</option>{% endfor %}
                    </select>
                </label>
                <label data-df-only="date">Datumsangaben mit Schrägstrich lesen als
                    <select name="date_order">
                        <option value="">Kontoeinstellung (Standard)</option>
                        {% for value, label in date_orders %}<option value="{{ value }}">{{ label }}</option>{% endfor %}
                    </select>
                </label>
                <p class="hint" data-df-only="code period date">
                    Wie Werte gelesen werden, lässt sich später nur in engen Fällen ändern:
                    vor dem ersten Upload festlegen.
                </p>
```

`src/doc_fields_view.py`, `error_message` — replace

```python
    if code == "field_type_locked":
        return "Der Typ dieses Feldes kann nicht mehr geändert werden."
```

with

```python
    if code == "field_type_locked":
        # Knovas 1.5.0: what decides how values are read locks once a field
        # is confirmed or in use (spec F1).
        return ("Diese Änderung ist nicht mehr möglich: Typ, Kennungsschema, "
                "Geschäftsjahr, Datumsreihenfolge und vorhandene Auswahlwerte eines "
                "bestätigten oder genutzten Feldes bleiben, wie sie sind.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_web_admin_doc_fields.py tests/test_doc_fields_view.py tests/test_doc_fields_render.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $PF/src/web_interface/admin_doc_fields.py $PF/src/web_interface/templates/admin_doc_fields.html \
  $PF/src/doc_fields_view.py $PF/tests/test_web_admin_doc_fields.py $PF/tests/test_doc_fields_view.py
git commit -F - <<'EOF'
platform: field forms set how values are read, and count the fields

Knovas 1.5.0 takes a code scheme for identifiers, a link policy for entity
fields, the first month and naming of a business year, and a date field's
own date order. The Dokumentfelder tab offers each with its type; create
sends a setting only when it differs from Knovas's default, an edit only
when it changed. A business year that does not start in January must say
whether "GJ 2024" names its start or end year (Knovas refuses it otherwise).
The 409 field_type_locked text now names everything that locks, the
registry table shows the settings, and the tab counts "n von 256 Feldern".
Spec F1.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task FLD-4: Choice editor — one row per choice with labels DE/FR/IT/EN and other names (F1)

Chosen form: **structured rows** (the rule form's indexed-row pattern), replacing the `code = Bezeichnung` textarea. Row `i` posts `choice_code_<i>`, `choice_label_de_<i>`, `choice_label_fr_<i>`, `choice_label_it_<i>`, `choice_label_en_<i>`, `choice_aliases_<i>` (other names, comma-separated). Rows are read in numeric order of `i`; an entirely empty row is skipped. What a row shows is what is sent (an emptied label or name list removes it); labels in languages the rows cannot show (Knovas keeps up to 16) stay. A stale page that still posts `enum_values` changes no choices (the field has no `choice_code_*` inputs).

**Files:**
- Modify: `$PF/src/web_interface/admin_doc_fields.py` (remove `parse_enum_lines`, `_shown_enum_label`, `enum_lines`, `merge_enum`; add `CHOICES_MAX`, `CHOICE_ALIASES_MAX`, `CHOICE_ROWS_NEW`, `CHOICE_ROWS_EXTRA`, `has_choice_rows`, `parse_choice_rows`, `merge_choices`, `choice_rows`, `posted_choice_rows`; `definition_from_form`, `changes_from_form`, `registry_rows`, `refill_field_row`, `_page`)
- Modify: `$PF/src/web_interface/templates/admin_doc_fields.html` (macros, edit form, create form)
- Modify: `$PF/src/web_interface/static/js/admin_doc_fields.js` ("Weitere Zeile")
- Modify: `$PF/src/web_interface/static/css/admin.css` (choice rows)
- Modify: `KnovasPlatform/docs/features/document-fields.md` (Felder bullet), `RELEASE_NOTES.md`
- Test: `$PF/tests/test_web_admin_doc_fields.py`

**Interfaces:**
- Consumes: FLD-3's `reading_from_form`, `READING_DEFAULTS`, template context keys.
- Produces: `parse_choice_rows(form) -> List[Dict[str, Any]]` (`{code, labels, aliases}`), `merge_choices(current, rows) -> List[Any]` (Knovas `enum_values`), `choice_rows(enum_values) -> List[Dict]` (`{code, labels: {de,fr,it,en}, aliases_text}`), `posted_choice_rows(form)`, `has_choice_rows(form) -> bool`; registry row key `choices` (replaces `enum_text`); context keys `choice_rows_new`, `choice_rows_extra`.

- [ ] **Step 1: Write the failing test**

`tests/test_web_admin_doc_fields.py` — directly after the `needs_db = pytest.mark.skipif(...)` statement add:

```python
def _rows(*choices):
    """The choice editor's inputs for ``(code, {lang: label}, "Name, Name")``."""
    form = {}
    for i, (code, labels, aliases) in enumerate(choices):
        form[f"choice_code_{i}"] = code
        for lang in ("de", "fr", "it", "en"):
            form[f"choice_label_{lang}_{i}"] = labels.get(lang, "")
        form[f"choice_aliases_{i}"] = aliases
    return form
```

In `TestDefinitionFromForm`, replace `test_an_enum_field_with_labels_and_codes` with:

```python
    def test_an_enum_field_with_labels_and_codes(self):
        from web_interface.admin_doc_fields import definition_from_form

        defn = definition_from_form({
            "key": "kostenstelle", "datatype": "enum", "cardinality": "one",
            "label_de": "Kostenstelle", "label_fr": "Centre de coûts", "label_it": "",
            "aliases": "kst, kostenstelle-nr, kst", "sensitivity": "normal", "display": "1",
            **_rows(("4100", {"de": "Verwaltung", "fr": "Administration"}, "Verw, Admin, Verw"),
                    ("", {}, ""), ("4200", {}, "")),
        })
        assert defn == {
            "key": "kostenstelle", "datatype": "enum", "cardinality": "one",
            "labels": {"de": "Kostenstelle", "fr": "Centre de coûts"},
            "aliases": ["kst", "kostenstelle-nr"],
            "enum_values": [{"code": "4100", "labels": {"de": "Verwaltung", "fr": "Administration"},
                             "aliases": ["Verw", "Admin"]}, "4200"],
            "display": True, "facet": False, "sensitivity": "normal",
        }
```

In `test_type_specific_inputs_go_only_with_their_type` replace `                "date_role": "due", "enum_values": "a"}` with `                "date_role": "due", **_rows(("a", {}, ""))}`. In the parameter list of `test_refused_before_knovas` replace the three `enum_values` entries

```python
        {"key": "ok_key", "datatype": "enum", "enum_values": ""},
        {"key": "ok_key", "datatype": "enum", "enum_values": "a b = x"},
        {"key": "ok_key", "datatype": "enum", "enum_values": "a\na"},
```

with

```python
        {"key": "ok_key", "datatype": "enum"},
        {"key": "ok_key", "datatype": "enum", **_rows(("a b", {}, ""))},
        {"key": "ok_key", "datatype": "enum", **_rows(("a", {}, ""), ("a", {}, ""))},
        {"key": "ok_key", "datatype": "enum", **_rows(("", {"de": "Ohne Code"}, ""))},
        {"key": "ok_key", "datatype": "enum",
         **_rows(("a", {}, ", ".join(f"n{i}" for i in range(33))))},
```

Replace `test_an_enum_error_names_the_line_not_the_value` with:

```python
    def test_a_choice_error_names_the_row_not_the_value(self):
        from web_interface.admin_doc_fields import FormError, parse_choice_rows

        with pytest.raises(FormError) as caught:
            parse_choice_rows(_rows(("ok", {}, ""), ("Muster AG", {}, "")))
        assert "Zeile 2" in str(caught.value)
        assert "Muster" not in str(caught.value)
```

In `TestChangesFromForm` replace `_form` and `test_an_enum_relabel_keeps_other_languages_and_aliases` with:

```python
    def _form(self, rows=None, **over):
        form = {"label_de": "Dokumentart", "label_fr": "", "label_it": "",
                "label_en": "Document type", "aliases": "art", "flags": "1",
                "display": "1", "facet": "1", "sensitivity": "normal"}
        form.update(_rows(*(rows if rows is not None else (
            ("invoice", {"de": "Rechnung", "fr": "Facture"}, "rg"), ("other", {}, "")))))
        form.update(over)
        return form

    def test_the_rows_are_what_is_sent(self):
        from web_interface.admin_doc_fields import changes_from_form

        changes = changes_from_form(self._form(rows=[
            ("invoice", {"de": "Kreditorenbeleg", "fr": "Facture"}, "rg"), ("other", {}, ""),
            ("offer", {"de": "Offerte", "en": "Offer"}, "Angebot")]), self.CURRENT)
        assert changes == {"enum_values": [
            {"code": "invoice", "labels": {"de": "Kreditorenbeleg", "fr": "Facture"},
             "aliases": ["rg"]},
            "other",
            {"code": "offer", "labels": {"de": "Offerte", "en": "Offer"}, "aliases": ["Angebot"]},
        ]}

    def test_an_emptied_input_goes_and_unseen_languages_stay(self):
        from web_interface.admin_doc_fields import changes_from_form

        current = dict(self.CURRENT, enum_values=[
            {"code": "invoice", "labels": {"de": "Rechnung", "fr": "Facture", "rm": "Quint"},
             "aliases": ["rg"]}])
        changes = changes_from_form(self._form(rows=[("invoice", {"de": "Rechnung"}, "")]),
                                    current)
        assert changes == {"enum_values": [
            {"code": "invoice", "labels": {"de": "Rechnung", "rm": "Quint"}}]}

    def test_a_removed_row_removes_the_choice(self):
        from web_interface.admin_doc_fields import changes_from_form, needs_use_confirmation

        changes = changes_from_form(self._form(rows=[
            ("invoice", {"de": "Rechnung", "fr": "Facture"}, "rg")]), self.CURRENT)
        assert changes == {"enum_values": [
            {"code": "invoice", "labels": {"de": "Rechnung", "fr": "Facture"}, "aliases": ["rg"]}]}
        assert needs_use_confirmation(changes)
```

Add a class after `TestChangesFromForm`:

```python
class TestChoiceRows:
    def test_rows_in_order_empty_ones_skipped(self):
        from web_interface.admin_doc_fields import parse_choice_rows

        form = {"choice_code_10": "c", "choice_code_2": "b", "choice_label_fr_2": "B fr",
                "choice_aliases_2": "bb, b2, bb", "choice_code_0": "a", "choice_code_5": "",
                "choice_label_de_5": "", "choice_aliases_5": ""}
        assert parse_choice_rows(form) == [
            {"code": "a", "labels": {}, "aliases": []},
            {"code": "b", "labels": {"fr": "B fr"}, "aliases": ["bb", "b2"]},
            {"code": "c", "labels": {}, "aliases": []},
        ]

    def test_a_new_field_s_choices(self):
        from web_interface.admin_doc_fields import merge_choices

        assert merge_choices(None, [
            {"code": "4100", "labels": {"de": "Verwaltung"}, "aliases": ["Verw"]},
            {"code": "4200", "labels": {}, "aliases": []}]) == [
            {"code": "4100", "labels": {"de": "Verwaltung"}, "aliases": ["Verw"]}, "4200"]

    def test_the_editor_shows_each_language_and_the_other_names(self):
        from web_interface.admin_doc_fields import choice_rows

        assert choice_rows([{"code": "invoice", "labels": {"de": "Rechnung", "rm": "Quint"},
                             "aliases": ["RG", "Beleg"]}, "other"]) == [
            {"code": "invoice", "labels": {"de": "Rechnung", "fr": "", "it": "", "en": ""},
             "aliases_text": "RG, Beleg"},
            {"code": "other", "labels": {"de": "", "fr": "", "it": "", "en": ""},
             "aliases_text": ""}]
```

In `TestRows.test_registry_rows_carry_labels_status_and_use` replace `        assert by["doc_type"]["enum_text"].startswith("contract = Vertrag")` with

```python
        assert by["doc_type"]["choices"][0] == {
            "code": "contract", "labels": {"de": "Vertrag", "fr": "", "it": "", "en": ""},
            "aliases_text": ""}
```

Replace the module-level `test_an_untouched_enum_without_a_german_label_is_no_change` with:

```python
def test_an_untouched_choice_without_a_german_label_is_no_change():
    """platform-admin-ingestion-3, with the row editor: a choice labelled in
    French and Italian only shows in those columns; sent back unchanged it is
    no change -- and no in-use confirmation for a change nobody made."""
    from web_interface.admin_doc_fields import (
        changes_from_form,
        choice_rows,
        needs_use_confirmation,
    )

    current = {"key": "belegart", "datatype": "enum", "labels": {"de": "Belegart"},
               "display": False, "facet": False,
               "enum_values": [{"code": "rechnung", "labels": {"fr": "Facture", "it": "Fattura"}},
                               {"code": "offerte", "labels": {"de": "Offerte"}}]}
    shown = choice_rows(current["enum_values"])
    assert shown[0]["labels"] == {"de": "", "fr": "Facture", "it": "Fattura", "en": ""}
    form = {"flags": "1", "display": "1", "label_de": "Belegart",
            **_rows(*[(r["code"], r["labels"], r["aliases_text"]) for r in shown])}
    changes = changes_from_form(form, current)
    assert changes == {"display": True}
    assert not needs_use_confirmation(changes)
    assert changes_from_form(dict(form, display=""), current) == {}
    relabelled = changes_from_form(dict(form, choice_label_de_0="Rechnung"), current)
    assert relabelled["enum_values"][0]["labels"] == {"fr": "Facture", "it": "Fattura",
                                                      "de": "Rechnung"}
    assert relabelled["enum_values"][1] == {"code": "offerte", "labels": {"de": "Offerte"}}
```

In `TestRegistryWrites.test_create_sends_the_definition_and_audits_key_and_type` replace `                         enum_values="4100 = Verwaltung", sensitivity="normal")` with `                         sensitivity="normal", **_rows(("4100", {"de": "Verwaltung"}, "")))`. In `test_update_sends_only_what_changed` replace

```python
                         sensitivity="normal",
                         enum_values="contract = Vertrag\ninvoice = Rechnung\n"
                                     "correspondence.email = E-Mail\nreport = Bericht\nother = Andere")
```

with

```python
                         sensitivity="normal",
                         **_rows(("contract", {"de": "Vertrag"}, ""),
                                 ("invoice", {"de": "Rechnung"}, ""),
                                 ("correspondence.email", {"de": "E-Mail"}, ""),
                                 ("report", {"de": "Bericht"}, ""),
                                 ("other", {"de": "Andere"}, "")))
```

Add after class `TestReadingSettingsOnThePage`:

```python
@needs_db
class TestChoiceEditor:
    def _form_of(self, html, field):
        form = html[html.index(f'/admin/doc-fields/{field["id"]}/update'):]
        return form[:form.index("</form>")]

    def test_the_rows_show_each_choice(self, as_admin):
        field = next(f for f in FakeDocFieldsApi.current.registry if f["key"] == "doc_type")
        form = self._form_of(as_admin.get("/admin/doc-fields").data.decode("utf-8"), field)
        assert 'name="choice_code_0" value="contract"' in form
        assert 'name="choice_label_de_0" value="Vertrag"' in form
        assert 'name="choice_code_5" value=""' in form  # five choices, then empty rows
        assert "data-df-choice-add" in form and 'name="enum_values"' not in form

    def test_labels_and_other_names_go_out(self, as_admin):
        api = FakeDocFieldsApi.current
        field = next(f for f in api.registry if f["key"] == "status")
        response = _post(as_admin, f"/admin/doc-fields/{field['id']}/update",
                         **_rows(("draft", {"de": "Entwurf", "fr": "Projet"}, "Vorlage"),
                                 ("final", {"de": "Final"}, ""),
                                 ("signed", {"de": "Unterzeichnet"}, "")))
        assert response.status_code == 200, response.data.decode("utf-8")[:500]
        assert _calls(api, "update_doc_field")[0]["changes"] == {"enum_values": [
            {"code": "draft", "labels": {"de": "Entwurf", "fr": "Projet"}, "aliases": ["Vorlage"]},
            {"code": "final", "labels": {"de": "Final"}},
            {"code": "signed", "labels": {"de": "Unterzeichnet"}}]}

    def test_the_confirmation_shows_the_posted_rows(self, as_admin, platform_db, admin):
        _profile(platform_db, admin, fields={"status": "draft"})
        api = FakeDocFieldsApi.current
        field = next(f for f in api.registry if f["key"] == "status")
        refused = _post(as_admin, f"/admin/doc-fields/{field['id']}/update",
                        **_rows(("draft", {"de": "Entwurf"}, ""), ("final", {"de": "Final"}, ""),
                                ("signed", {"de": "Unterzeichnet"}, ""),
                                ("archived", {"de": "Archiviert"}, "")))
        assert refused.status_code == 409
        assert _calls(api, "update_doc_field") == []
        form = self._form_of(refused.data.decode("utf-8"), field)
        assert 'name="choice_code_3" value="archived"' in form
```

In `TestStatic` add:

```python
    def test_choice_rows_grow_without_markup(self):
        js = (STATIC / "js" / "admin_doc_fields.js").read_text(encoding="utf-8")
        assert "cloneNode(true)" in js and "data-df-choice-add" in js
        html = (TEMPLATES / "admin_doc_fields.html").read_text(encoding="utf-8")
        assert 'name="enum_values"' not in html and "data-df-choice-row" in html
```

In `TestFeatureOff.test_without_a_knovas_connector_the_prefix_is_typed` replace `            field_count_text="",` with

```python
            field_count_text="", choice_rows_new=adf.CHOICE_ROWS_NEW,
            choice_rows_extra=adf.CHOICE_ROWS_EXTRA,
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_web_admin_doc_fields.py`
Expected: FAIL — `ImportError: cannot import name 'parse_choice_rows'` (and `merge_choices`, `choice_rows`), `KeyError: 'choices'`, `AttributeError: module ... has no attribute 'CHOICE_ROWS_NEW'`, the page still renders `name="enum_values"`.

- [ ] **Step 3: Write minimal implementation**

`src/web_interface/admin_doc_fields.py` — after `FIELD_CAP = 256` add:

```python
#: Choices per field and other names per choice (registry.py
#: _MAX_ENUM_VALUES, _MAX_ALIASES).
CHOICES_MAX = 500
CHOICE_ALIASES_MAX = 32
#: Empty choice rows in "Neues Feld" and below a field's own choices. More
#: come with "Weitere Zeile" (admin_doc_fields.js) or after saving.
CHOICE_ROWS_NEW = 4
CHOICE_ROWS_EXTRA = 2
```

Delete the four functions `parse_enum_lines`, `_shown_enum_label`, `enum_lines` and `merge_enum` (from `def parse_enum_lines(text: Any) -> List[Tuple[str, str]]:` through the `    return out` that ends `merge_enum`, directly before `def labels_from_form(`), keep `_enum_entries`, and put in their place:

```python
_CHOICE_CODE_RE = re.compile(r"^choice_code_(\d{1,4})$")


def _choice_indices(form: Mapping[str, Any]) -> List[int]:
    """The row numbers of the choice editor's rows in ``form``, in order."""
    found = {int(m.group(1)) for m in (_CHOICE_CODE_RE.match(str(name)) for name in form) if m}
    return sorted(found)


def has_choice_rows(form: Mapping[str, Any]) -> bool:
    """Whether ``form`` carries the choice editor (an enum field's form)."""
    return bool(_choice_indices(form))


def parse_choice_rows(form: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The choice editor: one row per choice -> ``[{code, labels, aliases}]``.

    Row ``i`` is ``choice_code_<i>``, ``choice_label_<lang>_<i>`` for de,
    fr, it and en, and ``choice_aliases_<i>`` (other names,
    comma-separated), in the order of ``i``. ``labels`` holds the languages
    filled in, ``aliases`` the names without repeats. An empty row is
    skipped (the rows for new choices); a row with text but no code, a code
    outside the server's pattern, a repeated code, more than 32 other names
    or more than 500 choices is a FormError naming the row, never its input.
    """
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for number, i in enumerate(_choice_indices(form), 1):
        code = _text(form, f"choice_code_{i}")
        labels = {lang: _text(form, f"choice_label_{lang}_{i}") for lang in LABEL_LANGS}
        labels = {lang: text for lang, text in labels.items() if text}
        aliases = split_list(form.get(f"choice_aliases_{i}"))
        if not code:
            if labels or aliases:
                raise FormError(f"Auswahlwerte, Zeile {number}: bitte einen Code angeben.")
            continue
        if not ENUM_CODE_RE.match(code):
            raise FormError(
                f"Auswahlwerte, Zeile {number}: ein Code besteht aus Buchstaben, "
                "Ziffern, Punkt, Bindestrich und _ (höchstens 64 Zeichen).")
        if code in seen:
            raise FormError(f"Auswahlwerte, Zeile {number}: dieser Code kommt zweimal vor.")
        if len(aliases) > CHOICE_ALIASES_MAX:
            raise FormError(f"Auswahlwerte, Zeile {number}: höchstens "
                            f"{CHOICE_ALIASES_MAX} weitere Namen.")
        seen.add(code)
        rows.append({"code": code, "labels": labels, "aliases": aliases})
    if len(rows) > CHOICES_MAX:
        raise FormError(f"Höchstens {CHOICES_MAX} Auswahlwerte pro Feld.")
    return rows


def merge_choices(current: Any, rows: Sequence[Mapping[str, Any]]) -> List[Any]:
    """The new ``enum_values`` from the choice rows.

    The rows show the code, the labels in DE/FR/IT/EN and the other names,
    so what they hold is what is sent: an emptied input removes that label
    or those names. Labels in other languages (Knovas keeps up to 16) are
    not shown and stay. A choice without labels and other names goes as its
    bare code when it was one (or is new), so an untouched form compares
    equal to what Knovas holds and changes nothing.
    """
    by_code: Dict[str, Any] = {}
    for item in _enum_entries(current):
        by_code[item if isinstance(item, str) else item["code"]] = item
    out: List[Any] = []
    for row in rows:
        code = str(row["code"])
        old = by_code.get(code)
        kept: Dict[str, str] = {}
        if isinstance(old, dict):
            kept = {lang: text for lang, text in (old.get("labels") or {}).items()
                    if lang not in LABEL_LANGS}
        labels = {**kept, **dict(row.get("labels") or {})}
        aliases = list(row.get("aliases") or [])
        if not labels and not aliases and not isinstance(old, dict):
            out.append(code)
            continue
        entry: Dict[str, Any] = {"code": code}
        if labels:
            entry["labels"] = labels
        if aliases:
            entry["aliases"] = aliases
        out.append(entry)
    return out


def choice_rows(enum_values: Any) -> List[Dict[str, Any]]:
    """``enum_values`` as the choice editor shows them: the code, the labels
    in DE/FR/IT/EN (empty where none) and the other names as text."""
    rows: List[Dict[str, Any]] = []
    for item in _enum_entries(enum_values):
        if isinstance(item, str):
            rows.append({"code": item, "labels": dict.fromkeys(LABEL_LANGS, ""),
                         "aliases_text": ""})
            continue
        labels = item.get("labels") or {}
        rows.append({"code": item["code"],
                     "labels": {lang: str(labels.get(lang) or "") for lang in LABEL_LANGS},
                     "aliases_text": ", ".join(str(a) for a in item.get("aliases") or [])})
    return rows


def posted_choice_rows(form: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The choice rows exactly as posted (rows with any input), to show them
    again with a confirmation -- unchecked, the person's own page only."""
    rows: List[Dict[str, Any]] = []
    for i in _choice_indices(form):
        row = {"code": _text(form, f"choice_code_{i}"),
               "labels": {lang: _text(form, f"choice_label_{lang}_{i}") for lang in LABEL_LANGS},
               "aliases_text": _text(form, f"choice_aliases_{i}")}
        if row["code"] or any(row["labels"].values()) or row["aliases_text"]:
            rows.append(row)
    return rows
```

In `definition_from_form` replace

```python
    if datatype == "enum":
        items = parse_enum_lines(form.get("enum_values"))
        if not items:
            raise FormError("Ein Auswahlfeld braucht mindestens einen Code.")
        defn["enum_values"] = merge_enum(None, items)
```

with

```python
    if datatype == "enum":
        rows = parse_choice_rows(form)
        if not rows:
            raise FormError("Ein Auswahlfeld braucht mindestens einen Code.")
        defn["enum_values"] = merge_choices(None, rows)
```

In `changes_from_form` replace the docstring sentence `Labels and enum values are merged with what the form cannot show (see``/``    ``merge_enum``).` with `Labels and choices are merged with the languages the form cannot show (see``/``    ``merge_choices``).` and replace

```python
    if datatype == "enum" and "enum_values" in form:
        items = parse_enum_lines(form.get("enum_values"))
        if not items:
            raise FormError("Ein Auswahlfeld braucht mindestens einen Code.")
        merged = merge_enum(current.get("enum_values"), items)
        if merged != _enum_entries(current.get("enum_values")):
            changes["enum_values"] = merged
```

with

```python
    if datatype == "enum" and has_choice_rows(form):
        rows = parse_choice_rows(form)
        if not rows:
            raise FormError("Ein Auswahlfeld braucht mindestens einen Code.")
        merged = merge_choices(current.get("enum_values"), rows)
        if merged != _enum_entries(current.get("enum_values")):
            changes["enum_values"] = merged
```

In `registry_rows` replace `            "enum_text": enum_lines(raw.get("enum_values")),` with `            "choices": choice_rows(raw.get("enum_values")),`. In `refill_field_row` replace

```python
    if "enum_values" in form:
        row["enum_text"] = str(form.get("enum_values") or "")
```

with

```python
    if has_choice_rows(form):
        row["choices"] = posted_choice_rows(form)
```

In `_page` replace `            "fy_labels": [(label, FY_LABEL_LABELS[label]) for label in FY_LABELS],` with

```python
            "fy_labels": [(label, FY_LABEL_LABELS[label]) for label in FY_LABELS],
            "choice_rows_new": CHOICE_ROWS_NEW,
            "choice_rows_extra": CHOICE_ROWS_EXTRA,
```

`src/web_interface/templates/admin_doc_fields.html` — directly after the line `<main class="admin doc-fields-admin">` insert:

```html
{% macro choice_row(i, c) -%}
<div class="choice-row" data-df-choice-row>
    <input type="text" name="choice_code_{{ i }}" value="{{ c.code if c else '' }}" aria-label="Code" autocomplete="off" spellcheck="false" maxlength="64">
    {% for lang in ('de', 'fr', 'it', 'en') %}
    <input type="text" name="choice_label_{{ lang }}_{{ i }}" value="{{ c.labels[lang] if c else '' }}" aria-label="Bezeichnung {{ lang|upper }}" maxlength="200">
    {% endfor %}
    <input type="text" name="choice_aliases_{{ i }}" value="{{ c.aliases_text if c else '' }}" aria-label="Weitere Namen (mit Komma getrennt)">
</div>
{%- endmacro %}
{% macro choice_editor(choices, extra) -%}
<fieldset class="choice-rows" data-df-choices>
    <legend>Auswahlwerte</legend>
    <div class="choice-row choice-row-head" aria-hidden="true">
        <span>Code</span><span>DE</span><span>FR</span><span>IT</span><span>EN</span><span>Weitere Namen (Komma)</span>
    </div>
    {% for c in choices %}{{ choice_row(loop.index0, c) }}{% endfor %}
    {% for j in range(extra) %}{{ choice_row(choices|length + j, None) }}{% endfor %}
    <button type="button" class="secondary compact" data-df-choice-add>Weitere Zeile</button>
</fieldset>
{%- endmacro %}
```

In the edit form replace

```html
                                        <label>Auswahlwerte (je Zeile: Code = Bezeichnung)
                                            <textarea name="enum_values" rows="5">{{ f.enum_text }}</textarea>
                                        </label>
```

with

```html
                                        {{ choice_editor(f.choices, choice_rows_extra) }}
```

In the create form replace

```html
                <label data-df-only="enum">Auswahlwerte (je Zeile: Code = Bezeichnung)
                    <textarea name="enum_values" rows="4" placeholder="rechnung = Rechnung&#10;offerte = Offerte"></textarea>
                </label>
```

with

```html
                <div data-df-only="enum">
                    {{ choice_editor([], choice_rows_new) }}
                </div>
```

`src/web_interface/static/js/admin_doc_fields.js` — in the header comment, after the line ` * - "Neues Feld": show the inputs that belong to the chosen type only.` add ` * - Auswahlwerte: "Weitere Zeile" adds an empty choice row.`; directly before `    // -- Folder rules: typed value inputs ------------------------------------` insert:

```js
    // -- Auswahlwerte: eine Zeile je Auswahl; "Weitere Zeile" haengt eine an --
    // Die neue Zeile ist eine geleerte Kopie der letzten, mit der naechsten
    // Nummer in den Namen (choice_code_<n>, ...). Ohne dieses Skript kommen
    // weitere leere Zeilen nach dem Speichern.
    document.querySelectorAll('[data-df-choices]').forEach(function (box) {
        var add = box.querySelector('[data-df-choice-add]');
        if (!add) { return; }
        add.addEventListener('click', function () {
            var rows = box.querySelectorAll('[data-df-choice-row]');
            var last = rows[rows.length - 1];
            if (!last) { return; }
            var next = 0;
            box.querySelectorAll('input[name^="choice_code_"]').forEach(function (input) {
                var n = parseInt(String(input.name).slice('choice_code_'.length), 10);
                if (!isNaN(n) && n >= next) { next = n + 1; }
            });
            var row = last.cloneNode(true);
            row.querySelectorAll('input').forEach(function (input) {
                input.value = '';
                input.name = String(input.name).replace(/_\d+$/, '_' + next);
            });
            last.parentNode.insertBefore(row, last.nextSibling);
            var first = row.querySelector('input');
            if (first) { first.focus(); }
        });
    });

```

`src/web_interface/static/css/admin.css` — after the `.rule-value-row { … }` rule insert:

```css
/* Auswahlwerte: eine Zeile je Auswahl -- Code, DE, FR, IT, EN, weitere Namen. */
.admin .choice-rows {
    border: none;
    margin: 6px 0 0;
    padding: 0;
    display: grid;
    gap: 6px;
}

.admin .choice-rows legend {
    font-size: 0.83rem;
    font-weight: 500;
    margin-bottom: 4px;
}

.admin .choice-row {
    display: grid;
    grid-template-columns: minmax(70px, 0.8fr) repeat(4, minmax(80px, 1fr)) minmax(110px, 1.4fr);
    gap: 6px;
    align-items: center;
}

.admin .choice-row input[type=text] {
    max-width: none;
    margin-top: 0;
}

.admin .choice-row-head {
    font-size: 0.75rem;
    color: var(--text-secondary);
}

.admin .choice-rows > button {
    justify-self: start;
}
```

and inside the `@media (max-width: 720px)` block that contains `.rule-value-row { grid-template-columns: 1fr; }`, after that rule add:

```css
    .admin .choice-row {
        grid-template-columns: 1fr 1fr;
    }

    .admin .choice-row-head {
        display: none;
    }
```

`KnovasPlatform/docs/features/document-fields.md` — replace the bullet starting `- **Felder**: create, change and retire (*Stilllegen*) fields — key, type` (through `retired: uploads with a retired key are no longer accepted.`) with:

```markdown
- **Felder**: create, change and retire (*Stilllegen*) fields — key, type
  (Text, Auswahl, Datum, Zeitraum, Betrag, Zahl, Kennung, Ja/Nein, Eintrag
  aus dem Wissensgraph), one or several values, labels DE/FR/IT/EN, other
  names, the entity target, *auf Trefferkarten zeigen*, *als Filter
  anbieten*, *normal* / *besonders schützenswert* — and how values are read:
  the *Kennungsschema* of an identifier (UID, IBAN, QR reference, case
  numbers of the Federal Supreme and Administrative Courts, ECLI, ICD-10-GM,
  language codes; *allgemein* by default), whether entity names are linked
  (*nie verknüpfen* keeps them as names), the first month of a business year
  and whether "GJ 2024" names its start or end year, and a date field's own
  order for `03/04/2024` (default: the account setting). Choice lists have
  one row per choice — code, labels DE/FR/IT/EN, other names (comma list);
  *Weitere Zeile* adds rows. Create sends only what differs from Knovas's
  defaults; an edit sends only what changed. What decides how values are
  read locks once a field is confirmed or in use ("Diese Änderung ist nicht
  mehr möglich …"). The tab counts "n von 256 Feldern" (retired fields count).
  Knovas refuses keys that look personal. A field the current Ingestion
  profile uses asks for a confirmation before it is changed or retired:
  uploads with a retired key are no longer accepted.
```

`RELEASE_NOTES.md` — insert as the last bullet of `## Dokumentfelder (Dokumentwerte)`, directly before the line starting `Anleitung: [KnovasPlatform/docs/features/document-fields.md]`:

```markdown
- **Felder anlegen wie in Knovas 1.5.0** (Reiter *Dokumentfelder*): je Typ
  das Kennungsschema (UID, IBAN, QR-Referenz, Geschaeftsnummern, ECLI,
  ICD-10-GM, Sprachcode), ob Namen mit Eintraegen verknuepft werden, Beginn
  und Benennung des Geschaeftsjahres, die Datumsreihenfolge eines
  Datumsfelds; Auswahlwerte je Zeile mit Code, Bezeichnungen DE/FR/IT/EN und
  weiteren Namen. Der Reiter zeigt "n von 256 Feldern".
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_web_admin_doc_fields.py tests/test_doc_fields_render.py tests/test_web_admin_documents.py tests/test_web_admin_ingestion.py`
Expected: PASS. `grep -rn "parse_enum_lines\|merge_enum\|enum_lines\|enum_text" $PF/src $PF/tests` → no output.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $PF/src/web_interface/admin_doc_fields.py $PF/src/web_interface/templates/admin_doc_fields.html \
  $PF/src/web_interface/static/js/admin_doc_fields.js $PF/src/web_interface/static/css/admin.css \
  KnovasPlatform/docs/features/document-fields.md RELEASE_NOTES.md $PF/tests/test_web_admin_doc_fields.py
git commit -F - <<'EOF'
platform: choice lists get one row per choice with four labels and names

Knovas 1.5.0 takes choices with labels in DE/FR/IT/EN and other names. The
"code = Bezeichnung" textarea could show only the German label, so it kept
the rest invisibly. The Dokumentfelder tab now has one row per choice (code,
DE, FR, IT, EN, other names) and "Weitere Zeile" to add one; what a row
shows is what is sent, labels in other languages stay, and an untouched
form still changes nothing. The in-use confirmation shows the posted rows
again. Spec F1.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task FLD-5: The mock checks field definitions like the server (F1)

**Files:**
- Modify: `$MOCK/app.py` (constants `ENUM_CODE_RE`, `SCHEME_RE`, `DATE_ORDERS`; `_check_enum_values`; `_create_field`; `_check_definition`)
- Test: `$MOCK/tests/test_mock_doc_fields.py`, `$PF/tests/test_doc_fields_mock_contract.py`

**Interfaces:**
- Consumes: FLD-3/FLD-4 `definition_from_form` output (Platform contract test).
- Produces: the mock answers `400 invalid_field_definition` with the server's paths for `date_order`, `fy_start_month`, `fy_label`, `code_scheme` and `enum_values[...]`, sets `code_scheme: "generic"` on a code field created without one, and locks `code_scheme` changes as before.

- [ ] **Step 1: Write the failing test**

`$MOCK/tests/test_mock_doc_fields.py` — add after class `TestValuesRegistry`:

```python
class TestFieldReadingSettings:
    """Knovas 1.5.0 field options as registry.py checks them
    (_clean_columns, _check_merged, _clean_enum_values), so the Platform's
    field forms are tested against the server's rules."""

    def _create(self, mock, **body):
        return mock.call("POST", "/secured/graph/doc-fields", body)

    def test_options_are_stored(self):
        mock = Mock(doc_fields="values")
        status, body = self._create(mock, key="aktenzeichen", datatype="code",
                                    code_scheme="bger")
        assert status == 201 and body["field"]["code_scheme"] == "bger"
        assert self._create(mock, key="belegnummer", datatype="code")[1]["field"][
            "code_scheme"] == "generic"
        status, body = self._create(mock, key="geschaeftsjahr", datatype="period",
                                    fy_start_month=7, fy_label="start")
        assert status == 201
        assert (body["field"]["fy_start_month"], body["field"]["fy_label"]) == (7, "start")
        status, body = self._create(mock, key="eingang", datatype="date", date_order="mdy")
        assert status == 201 and body["field"]["date_order"] == "mdy"
        status, body = self._create(mock, key="gegenseite", datatype="entity_ref",
                                    link_policy="never")
        assert status == 201 and body["field"]["link_policy"] == "never"
        status, body = self._create(mock, key="kostenstelle", datatype="enum", enum_values=[
            {"code": "4100", "labels": {"de": "Verwaltung", "fr": "Administration"},
             "aliases": ["Verw"]}, "4200"])
        assert status == 201 and body["field"]["enum_values"][0]["aliases"] == ["Verw"]

    @pytest.mark.parametrize("body, path", [
        ({"key": "jahr_x", "datatype": "period", "fy_start_month": 7}, "fy_label"),
        ({"key": "jahr_x", "datatype": "period", "fy_start_month": 13, "fy_label": "start"},
         "fy_start_month"),
        ({"key": "jahr_x", "datatype": "date", "fy_start_month": 7, "fy_label": "start"},
         "fy_start_month"),
        ({"key": "jahr_x", "datatype": "period", "fy_label": "middle"}, "fy_label"),
        ({"key": "kennung_x", "datatype": "text", "code_scheme": "iban"}, "code_scheme"),
        ({"key": "kennung_x", "datatype": "code", "code_scheme": "IBAN!"}, "code_scheme"),
        ({"key": "datum_x", "datatype": "date", "date_order": "dym"}, "date_order"),
        ({"key": "art_x", "datatype": "enum", "enum_values": ["a", "a"]}, "enum_values[1]"),
        ({"key": "art_x", "datatype": "enum", "enum_values": [{"code": "a", "x": 1}]},
         "enum_values[0]"),
        ({"key": "art_x", "datatype": "enum",
          "enum_values": [{"code": "a", "aliases": ["n"] * 33}]}, "enum_values[0].aliases"),
    ])
    def test_refused_like_the_server(self, body, path):
        status, answer = self._create(Mock(doc_fields="values"), **body)
        assert (status, answer["error_code"], answer.get("path")) == (
            400, "invalid_field_definition", path)

    def test_the_code_scheme_locks_and_the_link_policy_does_not(self):
        mock = Mock(doc_fields="values")
        field_id = self._create(mock, key="aktenzeichen", datatype="code")[1]["field"]["id"]
        status, body = mock.call("PATCH", f"/secured/graph/doc-fields/{field_id}",
                                 {"code_scheme": "bger"})
        assert (status, body["error_code"]) == (409, "field_type_locked")
        entity_id = self._create(mock, key="gegenseite", datatype="entity_ref")[1]["field"]["id"]
        status, body = mock.call("PATCH", f"/secured/graph/doc-fields/{entity_id}",
                                 {"link_policy": "never"})
        assert status == 200 and body["field"]["link_policy"] == "never"
```

`$PF/tests/test_doc_fields_mock_contract.py`, class `TestClientAgainstTheMock` — add:

```python
    def test_the_field_form_s_definitions_are_accepted(self):
        """F1: what the Dokumentfelder form builds is what the server takes
        (the mock checks definitions like registry.py)."""
        from web_interface.admin_doc_fields import definition_from_form

        client, state = mock_client("values")
        for form in (
            {"key": "aktenzeichen", "datatype": "code", "code_scheme": "bger"},
            {"key": "geschaeftsjahr", "datatype": "period", "fy_start_month": "7",
             "fy_label": "start"},
            {"key": "eingang", "datatype": "date", "date_order": "mdy"},
            {"key": "gegenseite", "datatype": "entity_ref", "link_policy": "never"},
            {"key": "kostenstelle", "datatype": "enum", "choice_code_0": "4100",
             "choice_label_de_0": "Verwaltung", "choice_label_fr_0": "Administration",
             "choice_aliases_0": "Verw", "choice_code_1": "4200"},
        ):
            assert client.create_doc_field(definition_from_form(form))["key"] == form["key"]
        fields = {f["key"]: f for f in client.doc_fields()}
        assert fields["aktenzeichen"]["code_scheme"] == "bger"
        assert (fields["geschaeftsjahr"]["fy_start_month"],
                fields["geschaeftsjahr"]["fy_label"]) == (7, "start")
        assert fields["kostenstelle"]["enum_values"][0]["aliases"] == ["Verw"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mock-pytest tests/test_mock_doc_fields.py::TestFieldReadingSettings`
Expected: FAIL — `code_scheme` is `None` for a code field created without one, and every refusal case answers 201.
Run: `pf-pytest tests/test_doc_fields_mock_contract.py::TestClientAgainstTheMock::test_the_field_form_s_definitions_are_accepted`
Expected: PASS already (the Platform side is done; the test pins the pair) — it must still pass after Step 3.

- [ ] **Step 3: Write minimal implementation**

`$MOCK/app.py` — replace `KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")` with

```python
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# registry.py: _ENUM_CODE_RE, _SCHEME_RE, DATE_ORDERS.
ENUM_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
SCHEME_RE = re.compile(r"^[a-z0-9][a-z0-9_:.\-]{0,63}$")
DATE_ORDERS = ("dmy", "mdy", "ymd")
```

Directly before `class MockState:` add:

```python
def _check_enum_values(values: Any) -> None:
    """The server's enum_values shape (registry._clean_enum_values): codes or
    {code, labels?, aliases?}, codes unique and of the code pattern, labels
    of at most 200 characters, at most 32 other names of at most 64."""
    if values is None:
        return
    if not isinstance(values, list) or not values or len(values) > 500:
        raise DocFieldError("invalid_field_definition", 400, "enum_values")
    seen = set()
    for i, item in enumerate(values):
        path = f"enum_values[{i}]"
        if isinstance(item, str):
            code = item
        elif isinstance(item, dict) and not set(item) - {"code", "labels", "aliases"}:
            code = item.get("code")
            labels = item.get("labels") or {}
            if not isinstance(labels, dict) or not all(
                    isinstance(k, str) and isinstance(v, str) and v.strip() and len(v) <= 200
                    for k, v in labels.items()):
                raise DocFieldError("invalid_field_definition", 400, f"{path}.labels")
            aliases = item.get("aliases") or []
            if not isinstance(aliases, list) or len(aliases) > 32 or not all(
                    isinstance(a, str) and a.strip() and len(a) <= 64 for a in aliases):
                raise DocFieldError("invalid_field_definition", 400, f"{path}.aliases")
        else:
            raise DocFieldError("invalid_field_definition", 400, path)
        if not isinstance(code, str) or not ENUM_CODE_RE.match(code) or code in seen:
            raise DocFieldError("invalid_field_definition", 400, path)
        seen.add(code)
```

In `_create_field` replace

```python
        _check_definition(row)
        state.fields[row["id"]] = row
        return row
```

with

```python
        if datatype == "code" and row["code_scheme"] is None:
            row["code_scheme"] = "generic"          # registry.py create_field
        _check_definition(row)
        state.fields[row["id"]] = row
        return row
```

In `_check_definition` replace

```python
        if row["link_policy"] not in ("resolve", "never"):
            raise DocFieldError("invalid_field_definition", 400, "link_policy")
```

with

```python
        if row["link_policy"] not in ("resolve", "never"):
            raise DocFieldError("invalid_field_definition", 400, "link_policy")
        if row["date_order"] is not None and row["date_order"] not in DATE_ORDERS:
            raise DocFieldError("invalid_field_definition", 400, "date_order")
        month = row["fy_start_month"]
        if month is not None and (isinstance(month, bool) or not isinstance(month, int)
                                  or not 1 <= month <= 12):
            raise DocFieldError("invalid_field_definition", 400, "fy_start_month")
        if row["fy_label"] is not None and row["fy_label"] not in ("start", "end"):
            raise DocFieldError("invalid_field_definition", 400, "fy_label")
        if (month is not None or row["fy_label"] is not None) and row["datatype"] != "period":
            raise DocFieldError("invalid_field_definition", 400, "fy_start_month")
        if month not in (None, 1) and row["fy_label"] is None:
            raise DocFieldError("invalid_field_definition", 400, "fy_label")
        scheme = row["code_scheme"]
        if scheme is not None and (not isinstance(scheme, str) or not SCHEME_RE.match(scheme)
                                   or row["datatype"] != "code"):
            raise DocFieldError("invalid_field_definition", 400, "code_scheme")
        _check_enum_values(row["enum_values"])
```

(The existing `(row["datatype"] == "enum") != bool(row["enum_values"])` check stays above, so a missing list still answers path `enum_values`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `mock-pytest tests/test_mock_doc_fields.py tests/test_doctor_doc_fields_probe.py`
Expected: PASS (goldens unchanged).
Run: `pf-pytest tests/test_doc_fields_mock_contract.py tests/test_doc_fields_render.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $MOCK/app.py $MOCK/tests/test_mock_doc_fields.py $PF/tests/test_doc_fields_mock_contract.py
git commit -F - <<'EOF'
platform: mock checks field definitions like the server

The Platform's field forms now send code schemes, link policies, business
years, date orders and multilingual choices. The mock accepted anything;
it now refuses what KnowledgeBase registry.py refuses (paths included),
sets code_scheme "generic" on a code field without one, and a contract
test sends what definition_from_form builds through the real client.
Spec F1.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task FLD-6: `where` lists of at most 50 values; "Verstanden als" names every operator (F2)

**Files:**
- Modify: `$PF/src/doc_fields_view.py` (`WHERE_LIST_MAX`, `_longest_list`, `validate_where`, `_OP_PREFIX`, `EXISTS_TEXT`, `PARTLY_TEXT`, `_between_text`, `_operand_text`, `_operand_op`, `error_message` for `where_too_complex`)
- Test: `$PF/tests/test_doc_fields_view.py`, `$PF/tests/test_web_search_doc_fields.py`, `$PF/tests/test_web_documents_find.py`

**Interfaces:**
- Consumes: —
- Produces: `doc_fields_view.WHERE_LIST_MAX = 50`; `validate_where` raises `ValueError` for any list longer than 50 anywhere in an operand; chip texts `eine von …`, `beginnt mit …`, `ab …`, `bis …`, `zwischen … und …`, `liegt ganz in …`, `…, auch teilweise`, `hat einen Wert` (constants `EXISTS_TEXT`, `PARTLY_TEXT`); `_operand_op` ignores `match`.

- [ ] **Step 1: Write the failing test**

`tests/test_doc_fields_view.py`, class `TestValidateWhere` — add:

```python
    def test_lists_hold_at_most_50_values(self):
        """Knovas: "8 keys per search and 50 values per list" (1.5.0)."""
        assert view.WHERE_LIST_MAX == 50
        view.validate_where({"reference": [f"R-{i}" for i in range(50)]})
        for bad in ({"reference": [f"R-{i}" for i in range(51)]},
                    {"reference": {"in": [f"R-{i}" for i in range(51)]}},
                    {"mandant": [{"name": f"Firma {i}"} for i in range(51)]}):
            with pytest.raises(ValueError):
                view.validate_where(bad)

    def test_every_operator_the_rail_builds_passes(self):
        where = {
            "doc_type": {"prefix": "corr"},
            "amount": {"between": ["CHF 1'000", "CHF 5'000"]},
            "document_date": {"gte": "01.01.2024", "lte": "30.06.2024", "match": "possible"},
            "period": {"within": "2024"}, "status": {"exists": True},
            "mandant": [{"name": "Muster AG"}, {"name": "Beispiel GmbH"}],
            "reference": ["E11.90", "E10.1"], "privileged": False,
        }
        assert view.validate_where(where) == where
```

Class `TestResolvedChips` — in `test_ranges_lists_and_node_ids` replace

```python
        assert texts == {"document_date": "ab 01.10.2026", "doc_type": "Rechnung; Vertrag",
                         "mandant": view.LINKED_ENTITY, "period": "2024 – 2025"}
```

with

```python
        assert texts == {"document_date": "ab 01.10.2026",
                         "doc_type": "eine von Rechnung; Vertrag",
                         "mandant": view.LINKED_ENTITY, "period": "zwischen 2024 und 2025"}
```

and add:

```python
    @pytest.mark.parametrize("key, operand, text, op", [
        ("doc_type", ["invoice", "contract"], "eine von Rechnung; Vertrag", "in"),
        ("doc_type", "invoice", "Rechnung", "eq"),
        ("doc_type", {"prefix": "corr"}, "beginnt mit corr", "prefix"),
        ("reference", ["E11.90", "E10.1"], "eine von E11.90; E10.1", "in"),
        ("reference", {"in": ["E11.90", "E10.1"]}, "eine von E11.90; E10.1", "in"),
        ("amount", {"gte": "CHF 1'000"}, "ab CHF 1'000", "gte"),
        ("amount", {"lte": "CHF 5'000"}, "bis CHF 5'000", "lte"),
        ("amount", {"between": ["CHF 1'000", "CHF 5'000"]},
         "zwischen CHF 1'000 und CHF 5'000", "between"),
        ("period", "GJ 2024", "GJ 2024", "eq"),
        ("period", {"gte": "2024", "lte": "2025"}, "zwischen 2024 und 2025", "range"),
        ("document_date", {"gte": "01.01.2024", "lte": "30.06.2024", "match": "possible"},
         "zwischen 01.01.2024 und 30.06.2024, auch teilweise", "range"),
        ("document_date", {"lte": "30.06.2024", "match": "possible"},
         "bis 30.06.2024, auch teilweise", "lte"),
        ("period", {"within": "2024"}, "liegt ganz in 2024", "within"),
        ("status", {"exists": True}, "hat einen Wert", "exists"),
        ("mandant", [{"name": "Muster AG"}, {"name": "Beispiel GmbH"}],
         "eine von Muster AG; Beispiel GmbH", "in"),
        ("privileged", True, "Ja", "eq"),
    ])
    def test_every_operator_reads_in_german(self, registry, key, operand, text, op):
        chip = view.resolved_chips({key: operand}, None, registry)[0]
        assert (chip["text"], chip["op"]) == (text, op)
```

Class `TestMessages` — add:

```python
    def test_too_complex_names_both_limits(self):
        text = view.error_message("where_too_complex")
        assert "8 Felder" in text and "50 Werte" in text
```

`tests/test_web_search_doc_fields.py`, class `TestWhatGoesOut` — in `test_a_malformed_where_never_leaves_the_platform` replace

```python
        for where in (too_many, {"Bad Key!": "x"}, {"doc_type": {"a": {"b": {"c": {"d": 1}}}}},
                      ["doc_type"], "doc_type"):
```

with

```python
        for where in (too_many, {"Bad Key!": "x"}, {"doc_type": {"a": {"b": {"c": {"d": 1}}}}},
                      ["doc_type"], "doc_type", {"reference": [f"R-{i}" for i in range(51)]}):
```

and add:

```python
    def test_operators_reach_knovas_and_read_back_in_german(self, filters_app, identity_repo):
        """F2: the rail's operators go out as written; "Verstanden als" says
        each one in German."""
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        where = {"doc_type": ["invoice", "contract"],
                 "document_date": {"gte": "01.01.2024", "lte": "31.12.2024",
                                   "match": "possible"},
                 "status": {"exists": True}}
        response = search(client, where=where)
        assert response.status_code == 200
        assert api.search_requests[-1]["where"] == where
        texts = {c["field"]: c["text"]
                 for c in response.get_json()["document_fields"]["resolved"]}
        assert texts == {"doc_type": "eine von Rechnung; Vertrag",
                         "document_date": "zwischen 01.01.2024 und 31.12.2024, auch teilweise",
                         "status": "hat einen Wert"}
```

`tests/test_web_documents_find.py`, `TestGating.test_bounds_are_checked_before_knovas` — add to the parameter list `        {"where": {"doc_type": ["invoice"] * 51}},`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_doc_fields_view.py::TestValidateWhere tests/test_doc_fields_view.py::TestResolvedChips tests/test_doc_fields_view.py::TestMessages::test_too_complex_names_both_limits tests/test_web_search_doc_fields.py::TestWhatGoesOut tests/test_web_documents_find.py::TestGating`
Expected: FAIL — `AttributeError: ... no attribute 'WHERE_LIST_MAX'`; 51-value lists pass; chips read `Rechnung; Vertrag`, `2024 – 2025`, `vorhanden`, `innerhalb 2024`; `match` makes the op `range` for `{"lte", "match"}`.

- [ ] **Step 3: Write minimal implementation**

`src/doc_fields_view.py` — replace `WHERE_MAX_KEYS = 8` with

```python
WHERE_MAX_KEYS = 8
WHERE_LIST_MAX = 50
```

Replace the block from `_OP_PREFIX = {` through the end of `_operand_op` (its last line `    return "eq"`, directly before `def _linked_text(`) with:

```python
_OP_PREFIX = {
    "gt": "nach ", "gte": "ab ", "lt": "vor ", "lte": "bis ",
    "overlaps": "überschneidet ", "within": "liegt ganz in ",
    "prefix": "beginnt mit ",
}
_OPS_IN_ORDER = ("eq", "in", "gt", "gte", "lt", "lte", "between", "overlaps", "within",
                 "prefix", "exists")
#: What the "Verstanden als" chips say for what is not a value (spec F2).
EXISTS_TEXT = "hat einen Wert"
PARTLY_TEXT = "auch teilweise"


def _between_text(spec: Optional[Mapping[str, Any]], lo: Any, hi: Any) -> str:
    return f"zwischen {_operand_text(spec, lo)} und {_operand_text(spec, hi)}"


def _operand_text(spec: Optional[Mapping[str, Any]], operand: Any) -> str:
    """The person's own operand in German (spec F2): "eine von ...",
    "beginnt mit ...", "ab ...", "bis ...", "zwischen ... und ...",
    "liegt ganz in ...", ", auch teilweise", "hat einen Wert". Enum codes
    become labels, entity operands their name; a ``node_id`` operand is never
    shown as the id."""
    if isinstance(operand, (list, tuple)):
        parts = [part for part in (_operand_text(spec, item) for item in operand) if part]
        if len(parts) > 1:
            return "eine von " + "; ".join(parts)
        return parts[0] if parts else ""
    if isinstance(operand, Mapping):
        if "name" in operand or "node_id" in operand:
            return _format_entity(operand)
        parts: List[str] = []
        both = "gte" in operand and "lte" in operand
        if both:
            parts.append(_between_text(spec, operand["gte"], operand["lte"]))
        for op in _OPS_IN_ORDER:
            if op not in operand or (both and op in ("gte", "lte")):
                continue
            value = operand[op]
            if op == "exists":
                parts.append(EXISTS_TEXT)
            elif op in ("eq", "in"):
                parts.append(_operand_text(spec, value))
            elif op == "between" and isinstance(value, (list, tuple)) and len(value) == 2:
                parts.append(_between_text(spec, value[0], value[1]))
            elif op == "within" and isinstance(value, (list, tuple)) and len(value) == 2:
                parts.append("liegt ganz " + _between_text(spec, value[0], value[1]))
            else:
                parts.append(_OP_PREFIX.get(op, "") + _operand_text(spec, value))
        if operand.get("match") == "possible":
            parts.append(PARTLY_TEXT)
        return ", ".join(part for part in parts if part)
    if isinstance(operand, bool):
        return _format_bool(operand)
    if spec is not None and spec.get("datatype") == "enum":
        return _enum_label(spec, operand)
    return "" if operand is None else str(operand).strip()


def _operand_op(operand: Any) -> str:
    if isinstance(operand, (list, tuple)):
        return "in"
    if isinstance(operand, Mapping):
        ops = [op for op in operand if op not in ("name", "node_id", "match")]
        return ops[0] if len(ops) == 1 else ("eq" if not ops else "range")
    return "eq"
```

Before `def validate_where(` add:

```python
def _longest_list(value: Any) -> int:
    """The longest list anywhere in an operand: ``["a", "b"]`` is 2,
    ``{"in": [...]}`` the length of its list, ``"x"`` 0."""
    if isinstance(value, Mapping):
        return max((_longest_list(v) for v in value.values()), default=0)
    if isinstance(value, (list, tuple)):
        return max([len(value)] + [_longest_list(v) for v in value])
    return 0
```

In `validate_where` replace the docstring with

```python
    """Bound a ``where`` that came from the browser; return it unchanged.

    A non-empty object of at most 8 keys, each matching
    ``^[a-z0-9_.\\- ]{1,64}$``, operands nested at most 3 deep with lists of
    at most 50 values (Knovas: "8 keys per search and 50 values per list"),
    at most 8 KiB as compact JSON. Raises ValueError (with a message that
    names no value). Knovas validates the meaning; this only keeps an
    oversized or oddly shaped body from leaving the Platform.
    """
```

and replace

```python
        if _depth(operand) > WHERE_MAX_DEPTH:
            raise ValueError("where is nested too deeply")
```

with

```python
        if _depth(operand) > WHERE_MAX_DEPTH:
            raise ValueError("where is nested too deeply")
        if _longest_list(operand) > WHERE_LIST_MAX:
            raise ValueError(f"where has a list of more than {WHERE_LIST_MAX} values")
```

In `error_message` replace

```python
    if code == "where_too_complex":
        return f"Der Filter ist zu umfangreich (höchstens {WHERE_MAX_KEYS} Felder)."
```

with

```python
    if code == "where_too_complex":
        return (f"Der Filter ist zu umfangreich (höchstens {WHERE_MAX_KEYS} Felder "
                f"und {WHERE_LIST_MAX} Werte pro Liste).")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_doc_fields_view.py tests/test_web_search_doc_fields.py tests/test_web_documents_find.py tests/test_doc_fields_mock_contract.py tests/test_web_admin_documents.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $PF/src/doc_fields_view.py $PF/tests/test_doc_fields_view.py \
  $PF/tests/test_web_search_doc_fields.py $PF/tests/test_web_documents_find.py
git commit -F - <<'EOF'
platform: where lists of at most 50 values, chips for every operator

Knovas 1.5.0 takes 8 keys per search and 50 values per list. validate_where
bounded the keys only; it now refuses a longer list anywhere in an operand
before anything leaves the Platform, and where_too_complex names both
limits. The "Verstanden als" chips now say each operator the filter rail
sends: "eine von", "beginnt mit", "ab", "bis", "zwischen ... und ...",
"liegt ganz in", "auch teilweise", "hat einen Wert". Spec F2.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task FLD-7: Filter rail operators per datatype (F2)

**Files:**
- Modify: `$PF/src/web_interface/static/js/doc_fields.js` (header comment; statics `OPERATORS`, `railType`, `splitList`, `buildOperand`, `parseOperand`, `placeholder`; remove `_labelled`; rewrite `_controlFor`; new `_suggestions`)
- Modify: `$PF/src/web_interface/static/css/style.css` (rail control parts)
- Modify: `KnovasPlatform/docs/features/document-fields.md` (Filter rail bullet, honesty-rules intro), `RELEASE_NOTES.md`
- Test: `$PF/tests/test_frontend_static.py`

**Interfaces:**
- Consumes: FLD-6's chip texts and list limit (server side).
- Produces: `DocFieldsUI.OPERATORS` (per datatype `[[op, label], …]`, first = default), `DocFieldsUI.buildOperand(datatype, {op, text, choices, lo, hi, partly}) -> where value | undefined` (spec F2 table), `DocFieldsUI.parseOperand(datatype, operand) -> state` (inverse, used to restore the rail and for the Cortex handoff), `DocFieldsUI.splitList(text, separator)`, `DocFieldsUI.railType(datatype)`, `DocFieldsUI.placeholder(type, op)`. Every rail control reads/writes through these, so search (`searchWhere`) and "Liste anzeigen" (`collectWhere`) send identical values.

Rail values, as the spec table: enum — choices (`"code"` / `["a","b"]`), *beginnt mit* (`{"prefix"}`); code — *ist*, *beginnt mit*, *eine von* (comma list); text — *ist*, *beginnt mit*; money/number — *ist*, *ab / bis* (`{"gte"}`, `{"lte"}`, `{"between": [a, b]}` when both); date/period — *Zeitraum* (plain value), *von / bis* (`{"gte", "lte"}`, `+ "match": "possible"` with *auch teilweise*), *liegt ganz in* (`{"within"}`); bool — *Ja* / *Nein*; entity_ref — one or several names separated by `;` (`{"name"}` / `[{"name"}, …]`); every type *hat einen Wert* (`{"exists": true}`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_frontend_static.py`:

```python
# ---------------------------------------------------------------------------
# F2: the filter rail's operators (doc_fields.js under Node)
# ---------------------------------------------------------------------------

from test_experiments_frontend import _run_node, needs_node  # noqa: E402

_DF_EXPORT = "\n;globalThis.__DF = DocFieldsUI;"
_UNDEF = "__undefined__"
_ALL_TYPES = ("enum", "code", "text", "money", "number", "date", "period", "bool",
              "entity_ref")


def _rail(body):
    """``body`` with DocFieldsUI as ``__DF``, in the vm harness of
    test_experiments_frontend."""
    return _run_node(body, [DOC_FIELDS_JS], suffix=_DF_EXPORT)


@needs_node
class TestRailOperands:
    """The ``where`` value per datatype and operator (spec F2's table). The
    rail's controls read and restore through these two functions only, for
    the search and for "Liste anzeigen" alike."""

    TABLE = [
        ("enum", {"op": "in", "choices": ["invoice"]}, "invoice"),
        ("enum", {"op": "in", "choices": ["invoice", "contract"]}, ["invoice", "contract"]),
        ("enum", {"op": "in", "choices": []}, _UNDEF),
        ("enum", {"op": "prefix", "text": " correspondence "}, {"prefix": "correspondence"}),
        ("code", {"op": "eq", "text": "4A_123/2024"}, "4A_123/2024"),
        ("code", {"op": "prefix", "text": "E11"}, {"prefix": "E11"}),
        ("code", {"op": "in", "text": "E11.90, E10.1, , E11.90"}, ["E11.90", "E10.1"]),
        ("code", {"op": "in", "text": "E11.90"}, "E11.90"),
        ("text", {"op": "eq", "text": "Telefon"}, "Telefon"),
        ("text", {"op": "prefix", "text": "Tel"}, {"prefix": "Tel"}),
        ("text", {"op": "eq", "text": "   "}, _UNDEF),
        ("money", {"op": "eq", "text": "CHF 1'000"}, "CHF 1'000"),
        ("money", {"op": "range", "lo": "CHF 1'000"}, {"gte": "CHF 1'000"}),
        ("money", {"op": "range", "hi": "CHF 5'000"}, {"lte": "CHF 5'000"}),
        ("money", {"op": "range", "lo": "CHF 1'000", "hi": "CHF 5'000"},
         {"between": ["CHF 1'000", "CHF 5'000"]}),
        ("number", {"op": "range", "lo": "10", "hi": "20"}, {"between": ["10", "20"]}),
        ("number", {"op": "range"}, _UNDEF),
        ("date", {"op": "overlaps", "text": "2024"}, "2024"),
        ("date", {"op": "range", "lo": "01.01.2024", "hi": "30.06.2024"},
         {"gte": "01.01.2024", "lte": "30.06.2024"}),
        ("date", {"op": "range", "lo": "01.01.2024", "hi": "30.06.2024", "partly": True},
         {"gte": "01.01.2024", "lte": "30.06.2024", "match": "possible"}),
        ("date", {"op": "range", "hi": "30.06.2024", "partly": True},
         {"lte": "30.06.2024", "match": "possible"}),
        ("date", {"op": "within", "text": "2024"}, {"within": "2024"}),
        ("period", {"op": "overlaps", "text": "GJ 2024"}, "GJ 2024"),
        ("period", {"op": "within", "text": "GJ 2024"}, {"within": "GJ 2024"}),
        ("bool", {"op": "true"}, True),
        ("bool", {"op": "false"}, False),
        ("bool", {"op": ""}, _UNDEF),
        ("entity_ref", {"op": "in", "text": "Muster AG"}, {"name": "Muster AG"}),
        ("entity_ref", {"op": "in", "text": "Muster AG; Beispiel GmbH;"},
         [{"name": "Muster AG"}, {"name": "Beispiel GmbH"}]),
        ("entity_ref", {"op": "in", "text": ""}, _UNDEF),
    ] + [(t, {"op": "exists"}, {"exists": True}) for t in _ALL_TYPES]

    def test_the_operator_table(self):
        cases = [[t, s] for t, s, _ in self.TABLE]
        result = _rail("const cases = " + json.dumps(cases) + ";\n"
                       "out(cases.map(([type, state]) => {"
                       " const v = __DF.buildOperand(type, state);"
                       " return v === undefined ? '" + _UNDEF + "' : v; }));")
        assert result == [v for _, _, v in self.TABLE]

    def test_a_value_survives_a_rail_rebuild(self):
        """load() reads the rail before it rebuilds it and writes the values
        back; the Cortex handoff writes {name}: parseOperand must give back
        what buildOperand made."""
        values = [[t, v] for t, _, v in self.TABLE if v != _UNDEF]
        result = _rail("const cases = " + json.dumps(values) + ";\n"
                       "out(cases.map(([type, value]) =>"
                       " __DF.buildOperand(type, __DF.parseOperand(type, value))));")
        assert result == [v for _, v in values]

    def test_an_empty_rail_starts_at_each_type_s_first_operator(self):
        result = _rail("out(['enum', 'code', 'money', 'date', 'bool', 'entity_ref', 'weird']"
                       ".map((t) => __DF.parseOperand(t, undefined).op));")
        assert result == ["in", "eq", "eq", "overlaps", "", "in", "eq"]

    def test_every_type_offers_hat_einen_wert(self):
        result = _rail("out(" + json.dumps(list(_ALL_TYPES)) + ".map((t) =>"
                       " __DF.OPERATORS[t].map(([op]) => op)));")
        assert all("exists" in ops for ops in result)


def test_the_rail_controls_use_the_one_builder():
    """F2: every facet control reads and restores through buildOperand and
    parseOperand, so search and "Liste anzeigen" send the same values."""
    source = _source(DOC_FIELDS_JS)
    body = _code(_method_body(source, "_controlFor"))
    assert "DocFieldsUI.buildOperand(" in body and "DocFieldsUI.parseOperand(" in body
    assert "_labelled" not in source
    suggestions = _code(_method_body(source, "_suggestions"))
    assert "this.suggest(field.key, last)" in suggestions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --version && pf-pytest tests/test_frontend_static.py::TestRailOperands tests/test_frontend_static.py::test_the_rail_controls_use_the_one_builder`
Expected: FAIL — `TypeError: __DF.buildOperand is not a function` (Node stderr) and `AssertionError: method _suggestions not found`.

- [ ] **Step 3: Write minimal implementation**

`src/web_interface/static/js/doc_fields.js` — in the header comment, after the line `// - Serverdaten gehen nur per textContent in die Seite -- diese Datei setzt` block (before `class DocFieldsUI {`), add:

```js
// - Jedes Feld der Leiste hat eine Bedingung (OPERATORS). Den where-Wert
//   baut nur buildOperand -- fuer die Suche und "Liste anzeigen" derselbe.
```

After `    static HANDOFF_KEY = 'knovas.docFieldsHandoff';` insert:

```js

    /**
     * Bedingungen je Feldtyp (Spec F2), die erste ist die Vorgabe. Was sie
     * an Knovas schicken, entscheidet buildOperand.
     */
    static OPERATORS = {
        enum: [['in', 'ist'], ['prefix', 'beginnt mit'], ['exists', 'hat einen Wert']],
        code: [['eq', 'ist'], ['prefix', 'beginnt mit'], ['in', 'eine von'],
            ['exists', 'hat einen Wert']],
        text: [['eq', 'ist'], ['prefix', 'beginnt mit'], ['exists', 'hat einen Wert']],
        money: [['eq', 'ist'], ['range', 'ab / bis'], ['exists', 'hat einen Wert']],
        number: [['eq', 'ist'], ['range', 'ab / bis'], ['exists', 'hat einen Wert']],
        date: [['overlaps', 'Zeitraum'], ['range', 'von / bis'], ['within', 'liegt ganz in'],
            ['exists', 'hat einen Wert']],
        period: [['overlaps', 'Zeitraum'], ['range', 'von / bis'], ['within', 'liegt ganz in'],
            ['exists', 'hat einen Wert']],
        bool: [['', '– alle –'], ['true', 'Ja'], ['false', 'Nein'], ['exists', 'hat einen Wert']],
        entity_ref: [['in', 'ist'], ['exists', 'hat einen Wert']],
    };

    /** Der Datentyp der Leiste: ein unbekannter gilt als Text. */
    static railType(datatype) {
        return Object.prototype.hasOwnProperty.call(DocFieldsUI.OPERATORS, datatype)
            ? datatype : 'text';
    }

    /** "a, b,, a" -> ["a", "b"]: getrimmt, ohne Leere und Wiederholungen. */
    static splitList(text, separator) {
        const out = [];
        String(text == null ? '' : text).split(separator).forEach((part) => {
            const item = part.trim();
            if (item && !out.includes(item)) out.push(item);
        });
        return out;
    }

    /**
     * Der where-Wert eines Feldes aus dem, was die Leiste haelt -- rein, ohne
     * DOM (tests/test_frontend_static.py prueft ihn unter Node). state:
     * {op, text, choices, lo, hi, partly}; undefined heisst kein Filter.
     */
    static buildOperand(datatype, state) {
        const type = DocFieldsUI.railType(datatype);
        const s = state || {};
        const op = String(s.op == null ? '' : s.op);
        const text = String(s.text == null ? '' : s.text).trim();
        const one = (items) => (items.length === 1 ? items[0] : items);
        if (op === 'exists') return { exists: true };
        if (type === 'bool') {
            if (op === 'true') return true;
            if (op === 'false') return false;
            return undefined;
        }
        if (op === 'prefix') return text ? { prefix: text } : undefined;
        if (type === 'enum') {
            const codes = (Array.isArray(s.choices) ? s.choices : [])
                .map((code) => String(code)).filter(Boolean);
            return codes.length ? one(codes) : undefined;
        }
        if (type === 'entity_ref') {
            const names = DocFieldsUI.splitList(text, ';').map((name) => ({ name }));
            return names.length ? one(names) : undefined;
        }
        if (op === 'in') {
            const codes = DocFieldsUI.splitList(text, ',');
            return codes.length ? one(codes) : undefined;
        }
        if (op === 'range') {
            const lo = String(s.lo == null ? '' : s.lo).trim();
            const hi = String(s.hi == null ? '' : s.hi).trim();
            if (!lo && !hi) return undefined;
            if (type === 'date' || type === 'period') {
                const range = {};
                if (lo) range.gte = lo;
                if (hi) range.lte = hi;
                if (s.partly) range.match = 'possible';
                return range;
            }
            if (lo && hi) return { between: [lo, hi] };
            return lo ? { gte: lo } : { lte: hi };
        }
        if (op === 'within') return text ? { within: text } : undefined;
        return text || undefined;   // ist; Zeitraum (der einfache Wert ueberschneidet)
    }

    /** Die Belegung der Leiste zu einem where-Wert (Wiederherstellen, Cortex-Uebergabe). */
    static parseOperand(datatype, operand) {
        const type = DocFieldsUI.railType(datatype);
        const state = {
            op: DocFieldsUI.OPERATORS[type][0][0], text: '', choices: [], lo: '', hi: '',
            partly: false,
        };
        if (operand === undefined || operand === null) return state;
        const object = typeof operand === 'object' && !Array.isArray(operand);
        if (object && operand.exists === true) return Object.assign(state, { op: 'exists' });
        if (type === 'bool') {
            if (operand === true || operand === false) {
                return Object.assign(state, { op: String(operand) });
            }
            return state;
        }
        if (object && !('name' in operand) && !('node_id' in operand)) {
            if (typeof operand.prefix === 'string') {
                return Object.assign(state, { op: 'prefix', text: operand.prefix });
            }
            if (typeof operand.within === 'string') {
                return Object.assign(state, { op: 'within', text: operand.within });
            }
            if (typeof operand.overlaps === 'string') {
                return Object.assign(state, { op: 'overlaps', text: operand.overlaps });
            }
            if (Array.isArray(operand.between) && operand.between.length === 2) {
                return Object.assign(state, {
                    op: 'range', lo: String(operand.between[0]), hi: String(operand.between[1]),
                });
            }
            if (typeof operand.gte === 'string' || typeof operand.lte === 'string') {
                return Object.assign(state, {
                    op: 'range',
                    lo: typeof operand.gte === 'string' ? operand.gte : '',
                    hi: typeof operand.lte === 'string' ? operand.lte : '',
                    partly: operand.match === 'possible',
                });
            }
            return state;
        }
        const items = Array.isArray(operand) ? operand : [operand];
        if (type === 'entity_ref') {
            const names = items.map((v) => (v && typeof v === 'object' ? v.name : v))
                .filter((v) => typeof v === 'string' && v);
            return Object.assign(state, { op: 'in', text: names.join('; ') });
        }
        const texts = items.filter((v) => typeof v === 'string' || typeof v === 'number')
            .map((v) => String(v));
        if (type === 'enum') return Object.assign(state, { op: 'in', choices: texts });
        if (type === 'code' && texts.length > 1) {
            return Object.assign(state, { op: 'in', text: texts.join(', ') });
        }
        return Object.assign(state, { text: texts[0] || '' });
    }

    /** Platzhalter der Werteingabe je Typ und Bedingung. */
    static placeholder(type, op) {
        if (op === 'prefix') return type === 'code' ? 'Anfang, z. B. E11' : 'Anfang';
        if (op === 'in' && type === 'code') return 'mehrere mit Komma trennen';
        if (type === 'entity_ref') return 'Name, mehrere mit ; trennen';
        if (op === 'within') return type === 'period' ? 'z. B. GJ 2024' : 'z. B. 2024';
        if (type === 'period') return 'z. B. GJ 2024';
        if (type === 'date') return 'z. B. März 2024';
        if (type === 'money') return "z. B. CHF 1'000";
        if (type === 'number') return "z. B. 1'234.5";
        return '';
    }
```

Delete the method `_labelled(field, control, extra) { … }` (directly after `renderRail`, 12 lines). Replace the whole method `_controlFor` — from its doc comment `    /** Ein Eingabeelement je Feldtyp; read() liefert den Operanden oder undefined. */` through its closing brace, directly before `    /** Sortierung der Liste: Dokumentpfad oder ein Datums-/Zeitraumfeld. */` — with:

```js
    /**
     * Ein Filter je Facettenfeld: die Bedingung (OPERATORS) und die Eingaben,
     * die sie braucht. read() liefert den where-Wert aus buildOperand oder
     * undefined, write() stellt einen ueber parseOperand wieder her.
     */
    _controlFor(field) {
        const type = DocFieldsUI.railType(field.datatype);
        const label = String(field.label || field.key);
        const wrap = document.createElement('div');
        wrap.className = 'doc-fields-control';
        const caption = document.createElement('label');
        const mode = document.createElement('select');
        mode.id = `dfFilter_${field.key}`;
        mode.className = 'doc-fields-op';
        caption.htmlFor = mode.id;
        caption.textContent = label;
        DocFieldsUI.OPERATORS[type].forEach(([value, text]) => {
            mode.appendChild(this._option(value, text));
        });
        wrap.append(caption, mode);
        if (type === 'bool') {
            // Ja / Nein / hat einen Wert: die Bedingung ist der Wert.
            this._controls.push({
                key: field.key,
                read: () => DocFieldsUI.buildOperand(type, { op: mode.value }),
                write: (v) => { mode.value = DocFieldsUI.parseOperand(type, v).op; },
            });
            return wrap;
        }
        const choices = document.createElement('select');
        choices.multiple = true;
        choices.title = 'Mehrere mit Strg- oder Cmd-Klick wählen';
        choices.setAttribute('aria-label', `${label}: Auswahl`);
        (field.enum || []).forEach((item) => {
            if (item && item.code) choices.appendChild(this._option(item.code, item.label || item.code));
        });
        choices.size = Math.min(5, Math.max(2, choices.options.length));
        const input = document.createElement('input');
        input.type = 'text';
        input.autocomplete = 'off';
        input.setAttribute('aria-label', `${label}: Wert`);
        const dated = type === 'date' || type === 'period';
        const range = document.createElement('div');
        range.className = 'doc-fields-range';
        const from = document.createElement('input');
        const to = document.createElement('input');
        [from, to].forEach((el) => { el.type = 'text'; el.autocomplete = 'off'; });
        from.placeholder = dated ? 'von' : 'ab';
        to.placeholder = 'bis';
        from.setAttribute('aria-label', `${label} ${from.placeholder}`);
        to.setAttribute('aria-label', `${label} bis`);
        range.append(from, to);
        const partlyLabel = document.createElement('label');
        partlyLabel.className = 'doc-fields-partly';
        const partly = document.createElement('input');
        partly.type = 'checkbox';
        partlyLabel.append(partly, ' auch teilweise');
        wrap.append(choices, input, range, partlyLabel);
        if (type === 'entity_ref' && field.has_target && field.sensitivity === 'normal') {
            // Vorschlaege nur fuer Felder mit Zieltyp und nie fuer besonders
            // schuetzenswerte: dort bleibt es bei freiem Text.
            wrap.appendChild(this._suggestions(field, input));
        }
        const sync = () => {
            const op = mode.value;
            const listed = type === 'enum' && op === 'in';
            choices.hidden = !listed;
            input.hidden = listed || op === 'range' || op === 'exists';
            range.hidden = op !== 'range';
            partlyLabel.hidden = !(dated && op === 'range');
            input.placeholder = DocFieldsUI.placeholder(type, op);
        };
        mode.addEventListener('change', sync);
        const state = () => ({
            op: mode.value,
            text: input.value,
            choices: Array.from(choices.options).filter((o) => o.selected).map((o) => o.value),
            lo: from.value,
            hi: to.value,
            partly: partly.checked,
        });
        this._controls.push({
            key: field.key,
            read: () => DocFieldsUI.buildOperand(type, state()),
            write: (v) => {
                const s = DocFieldsUI.parseOperand(type, v);
                mode.value = s.op;
                input.value = s.text;
                Array.from(choices.options).forEach((o) => { o.selected = s.choices.includes(o.value); });
                from.value = s.lo;
                to.value = s.hi;
                partly.checked = !!s.partly;
                sync();
            },
        });
        sync();
        return wrap;
    }

    /**
     * Namensvorschlaege fuer ein Entitaetsfeld. Bei mehreren Namen (";") gilt
     * der Vorschlag dem letzten; die Option traegt die ganze Zeile, damit
     * die Auswahl die schon getippten Namen behaelt. Der getippte Text geht
     * nur im JSON-Koerper an die Plattform (suggest), nie an Knovas.
     */
    _suggestions(field, input) {
        const list = document.createElement('datalist');
        list.id = `dfSuggest_${field.key}`;
        input.setAttribute('list', list.id);
        let timer = null;
        let seq = 0;
        input.addEventListener('input', () => {
            window.clearTimeout(timer);
            const typed = input.value;
            timer = window.setTimeout(async () => {
                const mine = ++seq;
                const before = typed.split(';');
                const last = before.pop();
                const names = await this.suggest(field.key, last);
                if (mine !== seq) return;
                const head = before.map((part) => part.trim()).filter(Boolean);
                list.replaceChildren(...names.map((name) => this._option(
                    [...head, name].join('; '), name)));
            }, DocFieldsUI.SUGGEST_DELAY_MS);
        });
        return list;
    }
```

`src/web_interface/static/css/style.css` — after the rule `.doc-fields-range input { flex: 1; min-width: 0; }` insert:

```css
/* Bedingung und Eingaben eines Filters: nur die der gewählten Bedingung
   sind sichtbar (doc_fields.js setzt hidden; .doc-fields-range ist flex). */
.doc-fields-control [hidden] {
    display: none;
}

.doc-fields-control select[multiple] {
    padding: 4px 6px;
}

.doc-fields-partly {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.78rem;
}
```

`KnovasPlatform/docs/features/document-fields.md` — replace the line `These are pinned by tests (the decisions live in Python; there is no JS test` / `runner):` with `These are pinned by tests (the decisions live in Python; the filter rail's` / `value builder runs under Node in tests/test_frontend_static.py):`. Replace the bullet starting `- **Filter rail** (state \`Werte + Filter\`), built from the fields marked` (through `Fields marked *besonders schützenswert* get no suggestions.`) with:

```markdown
- **Filter rail** (state `Werte + Filter`), built from the fields marked
  *als Filter anbieten*. Each field has a condition: choice lists *ist* (one
  or several choices) and *beginnt mit*; identifiers *ist*, *beginnt mit*
  and *eine von* (comma list); text *ist* and *beginnt mit*; amounts and
  numbers *ist* and *ab / bis*; dates and periods *Zeitraum* (overlaps),
  *von / bis* with *auch teilweise*, and *liegt ganz in*; yes/no fields
  *Ja* / *Nein*; entity fields one or several names (separated by `;`) with
  suggestions; every field *hat einen Wert*. Values are written as in the
  documents ("GJ 2024", "Q1 2024", "CHF 1'000" — Knovas parses them).
  "Verstanden als" names each condition ("eine von …", "zwischen … und …",
  "auch teilweise", "hat einen Wert"). A filter holds at most 8 fields and
  50 values per list; a larger one never leaves the Platform. Suggestions
  come from the field's node list, fetched **without** the typed text and
  filtered inside the Platform: typed prefixes never reach Knovas. Fields
  marked *besonders schützenswert* get no suggestions.
```

`RELEASE_NOTES.md` — insert as the last bullet of `## Dokumentfelder (Dokumentwerte)`, directly before the line starting `Anleitung: [KnovasPlatform/docs/features/document-fields.md]`:

```markdown
- **Filterleiste mit Bedingungen:** je Feld "ist", "eine von", "beginnt
  mit", "ab / bis" bzw. "von / bis" (mit "auch teilweise"), "liegt ganz in"
  und "hat einen Wert"; "Verstanden als" nennt jede Bedingung. Eine Liste im
  Filter hat hoechstens 50 Werte.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_frontend_static.py tests/test_experiments_frontend.py::TestSearchHitCards tests/test_doc_fields_render.py`
Expected: PASS (including `test_javascript_parses[doc_fields.js]`, `test_doc_fields_js_never_writes_markup`, `test_doc_fields_js_builds_no_url_from_data`, `test_doc_fields_posts_carry_the_csrf_header`).

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $PF/src/web_interface/static/js/doc_fields.js $PF/src/web_interface/static/css/style.css \
  KnovasPlatform/docs/features/document-fields.md RELEASE_NOTES.md $PF/tests/test_frontend_static.py
git commit -F - <<'EOF'
platform: filter rail offers every where operator per datatype

The rail sent a plain value (and gte/lte for dates) only. Each facet now has
a condition, as Knovas 1.5.0 takes them: one or several choices, beginnt
mit, eine von, ab / bis (between when both), von / bis with auch teilweise
(match possible), liegt ganz in, several entity names, and hat einen Wert.
One pure builder (buildOperand, with parseOperand to restore the rail)
makes the value for the search and for Liste anzeigen alike; Node tests pin
the spec's table and the round trip. Spec F2.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task FLD-8: Listing sort "Dokumentpfad absteigend" (F3)

**Files:**
- Modify: `$PF/src/web_interface/static/js/doc_fields.js` (`_renderSort`)
- Modify: `$PF/tests/doc_fields_fakes.py` (`FakeDocFieldsApi.find_doc_values`: pointer descending)
- Modify: `KnovasPlatform/docs/features/document-fields.md` (Liste anzeigen bullet), `RELEASE_NOTES.md`
- Test: `$PF/tests/test_web_documents_find.py`, `$PF/tests/test_frontend_static.py`

**Interfaces:**
- Consumes: FLD-7's `doc_fields.js`.
- Produces: rail sort option `pointer:desc` → `collectSort()` returns `{field: "pointer", order: "desc"}`; the route already accepts it (`documents_find` sort validation: `field == "pointer"`, `order in ("asc", "desc")`) — no server change.

- [ ] **Step 1: Write the failing test**

`tests/test_web_documents_find.py` — add:

```python
class TestPointerSort:
    def test_the_path_descending(self, listing, identity_repo):
        """F3: "Dokumentpfad absteigend" goes to Knovas as written."""
        app, api = listing
        client = signed_in(app, identity_repo, role="member")
        body = find(client, {"doc_type": ["invoice", "contract", "memo"]},
                    sort={"field": "pointer", "order": "desc"}).get_json()
        assert _finds(api)[-1]["sort"] == {"field": "pointer", "order": "desc"}
        assert [d["doc_id"] for d in body["documents"]] == sorted(
            [INVOICE, CONTRACT, MEMO], reverse=True)
        assert "deadline_banner" not in body
```

`tests/test_frontend_static.py` — append:

```python
def test_the_listing_sorts_by_path_both_ways():
    body = _code(_method_body(_source(DOC_FIELDS_JS), "_renderSort"))
    assert "this._option('pointer:asc', 'Dokumentpfad aufsteigend')" in body
    assert "this._option('pointer:desc', 'Dokumentpfad absteigend')" in body


@needs_node
def test_collect_sort_reads_pointer_descending():
    result = _rail("out(__DF.prototype.collectSort.call({ _sortControl: { value: 'pointer:desc' } }));")
    assert result == {"field": "pointer", "order": "desc"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_web_documents_find.py::TestPointerSort tests/test_frontend_static.py::test_the_listing_sorts_by_path_both_ways tests/test_frontend_static.py::test_collect_sort_reads_pointer_descending`
Expected: FAIL — the fake returns pointer ascending; `_renderSort` has no `pointer:desc` option. (`test_collect_sort_reads_pointer_descending` passes already: `collectSort` handles it.)

- [ ] **Step 3: Write minimal implementation**

`src/web_interface/static/js/doc_fields.js`, `_renderSort` — replace

```js
        select.appendChild(this._option('pointer:asc', 'Dokumentpfad'));
```

with

```js
        select.appendChild(this._option('pointer:asc', 'Dokumentpfad aufsteigend'));
        select.appendChild(this._option('pointer:desc', 'Dokumentpfad absteigend'));
```

`tests/doc_fields_fakes.py`, `find_doc_values` — replace

```python
            present.sort(key=lambda d: str(self._effective(d)[key]),
                         reverse=sort.get("order") == "desc")
            docs = present + missing
```

with

```python
            present.sort(key=lambda d: str(self._effective(d)[key]),
                         reverse=sort.get("order") == "desc")
            docs = present + missing
        elif sort and sort.get("order") == "desc":
            docs.reverse()  # the pointer, descending
```

`KnovasPlatform/docs/features/document-fields.md` — replace `  fields list every matching document visible to the person, sorted by a date` / `  field or the path, page by page.` with `  fields list every matching document visible to the person, sorted by a date` / `  field (newest or oldest first) or by the path (ascending or descending),` / `  page by page.`

`RELEASE_NOTES.md` — insert as the last bullet of `## Dokumentfelder (Dokumentwerte)`, directly before the line starting `Anleitung: [KnovasPlatform/docs/features/document-fields.md]`:

```markdown
- *Liste anzeigen* sortiert auch nach Dokumentpfad absteigend.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_web_documents_find.py tests/test_frontend_static.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $PF/src/web_interface/static/js/doc_fields.js $PF/tests/doc_fields_fakes.py \
  KnovasPlatform/docs/features/document-fields.md RELEASE_NOTES.md \
  $PF/tests/test_web_documents_find.py $PF/tests/test_frontend_static.py
git commit -F - <<'EOF'
platform: the field listing sorts by path descending too

Knovas sorts a listing by the pointer in either order; the rail offered
ascending only. "Dokumentpfad absteigend" sits next to "aufsteigend"; the
route already passed the order through. Spec F3.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task FLD-9: German text for the warning `ambiguous_number` (F3)

**Files:**
- Modify: `$PF/src/doc_fields_view.py` (`_WARNINGS`, `error_message`)
- Modify: `$PF/src/knovas_client.py` (`DOC_FIELDS_ERROR_CODES`)
- Test: `$PF/tests/test_doc_fields_view.py`, `$PF/tests/test_knovas_client_doc_fields.py`

**Interfaces:**
- Consumes: —
- Produces: `warning_text("ambiguous_number") == "Zahl mehrdeutig – als Dezimalzahl gelesen (für Tausender 1'234 schreiben)"`; `error_message("ambiguous_number", {"path": "set.<key>"})` = `„<label>“: <text>`; `"ambiguous_number" in DOC_FIELDS_ERROR_CODES`.

- [ ] **Step 1: Write the failing test**

`tests/test_doc_fields_view.py`, class `TestMessages` — add:

```python
    def test_an_ambiguous_number_is_explained(self, registry):
        """1.5.0: "1,234 is 1.234, with a warning"; 1'234 is a thousand."""
        text = view.warning_text("ambiguous_number")
        assert text == ("Zahl mehrdeutig – als Dezimalzahl gelesen "
                        "(für Tausender 1'234 schreiben)")
        assert view.error_message("ambiguous_number", {"path": "set.amount"}, registry) == \
            "„Betrag“: " + text
```

`tests/test_knovas_client_doc_fields.py`, class `TestQueryRejected` — add:

```python
    def test_ambiguous_number_is_a_doc_fields_code(self):
        from knovas_client import DOC_FIELDS_ERROR_CODES

        assert {"ambiguous_date", "ambiguous_number"} <= DOC_FIELDS_ERROR_CODES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_doc_fields_view.py::TestMessages::test_an_ambiguous_number_is_explained tests/test_knovas_client_doc_fields.py::TestQueryRejected::test_ambiguous_number_is_a_doc_fields_code`
Expected: FAIL — `warning_text` returns the code itself; the code is not in the set.

- [ ] **Step 3: Write minimal implementation**

`src/doc_fields_view.py`, `_WARNINGS` — replace

```python
    "ambiguous_date": "Datum mehrdeutig – bitte prüfen",
```

with

```python
    "ambiguous_date": "Datum mehrdeutig – bitte prüfen",
    "ambiguous_number": ("Zahl mehrdeutig – als Dezimalzahl gelesen "
                         "(für Tausender 1'234 schreiben)"),
```

In `error_message` replace `    if code in ("ambiguous_date", "unresolved_entity", "ambiguous_entity"):` with `    if code in ("ambiguous_date", "ambiguous_number", "unresolved_entity", "ambiguous_entity"):`.

`src/knovas_client.py`, `DOC_FIELDS_ERROR_CODES` — replace `    "key_looks_personal", "ambiguous_date", "unresolved_entity", "ambiguous_entity",` with `    "key_looks_personal", "ambiguous_date", "ambiguous_number", "unresolved_entity",` / `    "ambiguous_entity",`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_doc_fields_view.py tests/test_knovas_client_doc_fields.py tests/test_web_document_fields.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $PF/src/doc_fields_view.py $PF/src/knovas_client.py \
  $PF/tests/test_doc_fields_view.py $PF/tests/test_knovas_client_doc_fields.py
git commit -F - <<'EOF'
platform: German text for the ambiguous_number warning

Knovas 1.5.0 reads "1,234" as 1.234 and warns ambiguous_number; the panel
and the folder rules showed the bare code. It now says the number was read
as a decimal and that a thousand is written 1'234. Spec F3.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task FLD-10: The client keeps Knovas's `auto_scope` (node ids only) (F3)

**Files:**
- Modify: `$PF/src/knovas_client.py` (`_AUTO_SCOPE_IDS_MAX`, `_auto_scope_echo`, `_secured_query_honesty`, `_unwrap_secured_query_response`)
- Test: `$PF/tests/test_knovas_client_doc_fields.py`

**Interfaces:**
- Consumes: —
- Produces: `search_documents(...)["semantix"]["auto_scope"] = {"applied": bool, "fallback": bool, "node_ids": [str]}` (detected node ids first, then the rest of the scope, each once, at most 200 = the server's `MAX_SCOPE_IDS`); absent when Knovas sent no `auto_scope`. Identifier ids, channels and scores are dropped.

- [ ] **Step 1: Write the failing test**

`tests/test_knovas_client_doc_fields.py`, class `TestSearchResponse` — add:

```python
    def test_auto_scope_is_kept_as_node_ids_only(self):
        """F3: Knovas narrowed the search to nodes named in the question
        (shape of query_pipeline.py on KB develop); the Platform keeps
        applied, fallback and the node ids -- detected ones first."""
        client = secured(Resp(200, {
            "status": "success", "results": [],
            "auto_scope": {"detections": [{"node_id": "n-2", "identifier_id": "i-9",
                                           "channel": "lexical", "score": 0.97}],
                           "node_ids": ["n-1", "n-2"], "applied": True, "fallback": False,
                           "canonicalized": True, "residualized": False}}))
        meta = client.search_documents("q", limit=5)["semantix"]
        assert meta["auto_scope"] == {"applied": True, "fallback": False,
                                      "node_ids": ["n-2", "n-1"]}

    def test_auto_scope_ids_are_bounded(self):
        ids = [f"n-{i}" for i in range(250)]
        client = secured(Resp(200, {"status": "success", "results": [],
                                    "auto_scope": {"node_ids": ids, "fallback": True}}))
        block = client.search_documents("q", limit=5)["semantix"]["auto_scope"]
        assert block["node_ids"] == ids[:200]
        assert (block["applied"], block["fallback"]) == (False, True)

    def test_a_nested_auto_scope_is_kept(self):
        out = _unwrap_secured_query_response({"status": "success", "data": {
            "results": [], "auto_scope": {"applied": False, "fallback": True,
                                          "node_ids": ["n-1"]}}})
        assert out["auto_scope"]["fallback"] is True
```

In `test_an_old_server_gives_nulls_and_no_echo` replace `        assert "where" not in meta and "return_fields" not in meta` with `        assert "where" not in meta and "return_fields" not in meta and "auto_scope" not in meta`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_knovas_client_doc_fields.py::TestSearchResponse`
Expected: FAIL — `KeyError: 'auto_scope'` (not kept), the nested block is not lifted.

- [ ] **Step 3: Write minimal implementation**

`src/knovas_client.py`, `_unwrap_secured_query_response` — replace

```python
    for key in ("no_strong_matches", "no_results_reason", "relevance_gate_applied",
                "meta", "where", "return_fields"):
```

with

```python
    for key in ("no_strong_matches", "no_results_reason", "relevance_gate_applied",
                "meta", "where", "return_fields", "auto_scope"):
```

Directly before `def _secured_query_honesty(` add:

```python
# The most auto_scope node ids kept: the server's own bound on a scope
# (query_scope.MAX_SCOPE_IDS).
_AUTO_SCOPE_IDS_MAX = 200


def _auto_scope_echo(block: Dict[str, Any]) -> Dict[str, Any]:
    """``auto_scope`` as the Platform keeps it (spec F3): whether Knovas ran
    the search inside the nodes it recognised in the question (``applied``)
    or found nothing there and searched everything (``fallback``), and the
    node ids -- the detected ones first, then the rest of the scope, each
    once. Identifier ids, channels and scores stay behind: nothing on the
    page uses them."""
    ids: List[str] = []
    for detection in block.get("detections") or ():
        if isinstance(detection, dict) and isinstance(detection.get("node_id"), str):
            ids.append(detection["node_id"])
    ids.extend(i for i in block.get("node_ids") or () if isinstance(i, str))
    unique = [i for i in dict.fromkeys(ids) if i][:_AUTO_SCOPE_IDS_MAX]
    return {"applied": block.get("applied") is True, "fallback": block.get("fallback") is True,
            "node_ids": unique}
```

In `_secured_query_honesty` replace the docstring sentence `    so a caller reads one shape. ``where`` and ``return_fields`` appear only` / `    when Knovas echoed them: their absence means "not filtered" (H2).` with `    so a caller reads one shape. ``where`` and ``return_fields`` appear only` / `    when Knovas echoed them: their absence means "not filtered" (H2);` / `    ``auto_scope`` (node ids only) when Knovas narrowed the search by a name.` and replace

```python
    return_fields = result.get("return_fields")
    if isinstance(return_fields, dict):
        out["return_fields"] = {"applied": return_fields.get("applied")}
    return out
```

with

```python
    return_fields = result.get("return_fields")
    if isinstance(return_fields, dict):
        out["return_fields"] = {"applied": return_fields.get("applied")}
    auto_scope = result.get("auto_scope")
    if isinstance(auto_scope, dict):
        out["auto_scope"] = _auto_scope_echo(auto_scope)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_knovas_client_doc_fields.py tests/test_knovas_query_parse.py tests/test_search_server_order.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $PF/src/knovas_client.py $PF/tests/test_knovas_client_doc_fields.py
git commit -F - <<'EOF'
platform: keep Knovas's auto_scope block, node ids only

With QUERY_AUTO_SCOPE_ENABLED Knovas narrows a search to the knowledge-
graph nodes it recognises in the question and says so in auto_scope
(applied, fallback, node_ids, detections). The client dropped it. It now
keeps applied, fallback and the node ids (detected first, at most 200),
also from a nested answer; identifier ids and scores stay behind. Spec F3.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task FLD-11: `notices` in the search answer, names read as the person (F3)

**Files:**
- Modify: `$PF/src/doc_fields_view.py` (`NOTICE_KINDS`, `NOTICE_NAMES_MAX`, `notice`, `auto_scope_of`)
- Modify: `$PF/src/doc_fields_capability.py` (`_NODE_NAMES`, `node_names_for`, `invalidate`, imports, module docstring)
- Modify: `$PF/src/web_interface/doc_fields_routes.py` (`search_notices`)
- Modify: `$PF/src/web_interface/app.py` (`search()` payload and docstring)
- Modify: `$PF/tests/doc_fields_fakes.py` (knobs `return_fields_unreadable`, `degraded`, `auto_scope`)
- Test: `$PF/tests/test_doc_fields_view.py`, `$PF/tests/test_doc_fields_capability.py`, `$PF/tests/test_web_search_doc_fields.py`

**Interfaces:**
- Consumes: FLD-10's `semantix["auto_scope"] = {"applied", "fallback", "node_ids"}`; existing `semantix["return_fields"]`, `semantix["degraded_to_bm25"]`, `SearchPlan.fields_unavailable`, `SearchPlan.capability`.
- Produces: `doc_fields_view.NOTICE_KINDS = ("return_fields_unavailable", "degraded_to_bm25", "auto_scope_applied", "auto_scope_fallback")`, `NOTICE_NAMES_MAX = 5`, `notice(kind, names=(), hidden_count=0) -> {"kind", "names", "hidden_count"}` (overflow beyond 5 names moves into `hidden_count`), `auto_scope_of(meta) -> Tuple[Optional[str], List[str]]`; `doc_fields_capability.node_names_for(client, user_key, node_ids) -> Tuple[List[str], int]` (one `graph_nodes()` read as the person, never `q`, cached per person for `registry_cache_seconds`, failures not cached, > 5000 nodes → no names); `doc_fields_routes.search_notices(client, plan, meta, user_key) -> List[Dict[str, Any]]`; **search payload key `notices`** (contract shape `{"kind", "names", "hidden_count"}`), `[]` when there is nothing to say. Fake knobs: `FakeDocFieldsApi.return_fields_unreadable`, `.degraded`, `.auto_scope` (the processed `semantix["auto_scope"]` shape).

- [ ] **Step 1: Write the failing test**

`tests/test_doc_fields_view.py` — add:

```python
class TestNotices:
    def test_the_shape(self):
        assert view.notice("degraded_to_bm25") == {
            "kind": "degraded_to_bm25", "names": [], "hidden_count": 0}
        assert view.notice("auto_scope_applied", ["Muster AG", ""], 2) == {
            "kind": "auto_scope_applied", "names": ["Muster AG"], "hidden_count": 2}
        many = view.notice("auto_scope_fallback", [f"Firma {i}" for i in range(7)], 1)
        assert many["names"] == [f"Firma {i}" for i in range(5)] and many["hidden_count"] == 3
        with pytest.raises(ValueError):
            view.notice("something_else")

    @pytest.mark.parametrize("meta, expected", [
        ({"auto_scope": {"applied": True, "fallback": False, "node_ids": ["n1", "n2"]}},
         ("auto_scope_applied", ["n1", "n2"])),
        ({"auto_scope": {"applied": False, "fallback": True, "node_ids": ["n1"]}},
         ("auto_scope_fallback", ["n1"])),
        ({"auto_scope": {"applied": False, "fallback": False, "node_ids": ["n1"]}}, (None, [])),
        ({"auto_scope": {"applied": True, "node_ids": []}}, (None, [])),
        ({"auto_scope": "applied"}, (None, [])),
        ({}, (None, [])),
        (None, (None, [])),
    ])
    def test_auto_scope_of(self, meta, expected):
        assert view.auto_scope_of(meta) == expected
```

`tests/test_doc_fields_capability.py` — add after class `TestEntityNamesFor`:

```python
class TestNodeNamesFor:
    """F3: the names of auto-scope nodes, read as the person (one node list,
    never with q); a node Knovas does not list for them is only counted."""

    def test_names_in_order_hidden_counted_never_q(self):
        client = FakeDocFieldsApi("filters")
        names, hidden = cap.node_names_for(client, "alice", ["m2", "gone", "m1", "m2"])
        assert names == ["Beispiel GmbH", "Muster AG"] and hidden == 1
        assert client.graph_nodes_calls == [{"node_type_id": None, "q": None}]

    def test_cached_per_user(self):
        client = FakeDocFieldsApi("filters")
        cap.node_names_for(client, "alice", ["m1"])
        cap.node_names_for(client, "alice", ["m2"])
        assert len(client.graph_nodes_calls) == 1
        cap.node_names_for(client, "bob", ["m1"])
        assert len(client.graph_nodes_calls) == 2
        cap.invalidate("alice")
        cap.node_names_for(client, "alice", ["m1"])
        assert len(client.graph_nodes_calls) == 3

    def test_a_failure_names_nobody_and_is_not_cached(self):
        client = FakeDocFieldsApi("filters")
        original = client.graph_nodes

        def broken(**kw):
            raise RuntimeError("graph down")

        client.graph_nodes = broken
        assert cap.node_names_for(client, "alice", ["m1", "m2"]) == ([], 2)
        client.graph_nodes = original
        assert cap.node_names_for(client, "alice", ["m1"]) == (["Muster AG"], 0)

    def test_more_than_5000_nodes_names_nobody(self):
        client = FakeDocFieldsApi("filters")
        for i in range(5001):
            client.nodes[f"x{i}"] = {"id": f"x{i}", "name": f"Firma {i}",
                                     "node_type_id": "t-mandant"}
        assert cap.node_names_for(client, "alice", ["m1"]) == ([], 1)

    def test_nothing_is_asked_without_ids(self):
        client = FakeDocFieldsApi("filters")
        assert cap.node_names_for(client, "alice", []) == ([], 0)
        assert client.graph_nodes_calls == []

    def test_no_name_in_a_log_line(self, caplog):
        import logging

        sentinel = "Sentinel-Knoten-AG"
        client = FakeDocFieldsApi("filters")
        client.nodes["s1"] = {"id": "s1", "name": sentinel, "node_type_id": "t-mandant"}

        def broken(**kw):
            raise RuntimeError(sentinel)

        with caplog.at_level(logging.DEBUG):
            assert cap.node_names_for(client, "alice", ["s1"]) == ([sentinel], 0)
            cap.invalidate()
            client.graph_nodes = broken
            assert cap.node_names_for(client, "alice", ["s1"]) == ([], 1)
        assert caplog.records and sentinel not in caplog.text
```

`tests/test_web_search_doc_fields.py` — add `import json` after `import logging`; in `TestFeatureOffParity.test_legacy_client_gets_todays_call_and_additive_keys` replace `                             "document_fields", "honesty"}` with `                             "document_fields", "honesty", "notices"}` and after the `assert body["honesty"] == {...}` statement add `        assert body["notices"] == []`. Add at the end of the file (before `TestNoValuesInLogs` or after it):

```python
# ---------------------------------------------------------------------------
# F3: what Knovas said about the answer, above the results
# ---------------------------------------------------------------------------

class TestNotices:
    def test_none_for_an_ordinary_search(self, filters_app, identity_repo):
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        assert search(client).get_json()["notices"] == []

    def test_values_knovas_could_not_read(self, filters_app, identity_repo):
        app, api = filters_app
        api.return_fields_unreadable = True
        client = signed_in(app, identity_repo, role="member")
        body = search(client).get_json()
        assert body["notices"] == [{"kind": "return_fields_unavailable", "names": [],
                                    "hidden_count": 0}]
        assert body["results"] and all(r["fields_display"] == [] for r in body["results"])

    def test_values_the_platform_could_not_ask_for(self, filters_app, identity_repo):
        app, api = filters_app
        api.fail_call("doc_fields", 503, "doc_fields_unavailable")
        client = signed_in(app, identity_repo, role="member")
        assert [n["kind"] for n in search(client).get_json()["notices"]] == [
            "return_fields_unavailable"]

    def test_degraded_results_are_marked(self, filters_app, identity_repo):
        app, api = filters_app
        api.degraded = True
        client = signed_in(app, identity_repo, role="member")
        body = search(client).get_json()
        assert body["results"] and [n["kind"] for n in body["notices"]] == ["degraded_to_bm25"]

    @pytest.mark.parametrize("applied, fallback, kind", [
        (True, False, "auto_scope_applied"), (False, True, "auto_scope_fallback")])
    def test_auto_scope_names_only_what_the_person_may_see(self, filters_app, identity_repo,
                                                          applied, fallback, kind):
        app, api = filters_app
        api.auto_scope = {"applied": applied, "fallback": fallback,
                          "node_ids": ["m1", "hidden-node"]}
        client = signed_in(app, identity_repo, role="member")
        body = search(client).get_json()
        assert body["notices"] == [{"kind": kind, "names": ["Muster AG"], "hidden_count": 1}]
        assert "hidden-node" not in json.dumps(body), "node ids never reach the browser"
        assert api.graph_nodes_calls == [{"node_type_id": None, "q": None}]

    def test_at_most_five_names(self, filters_app, identity_repo):
        app, api = filters_app
        for i in range(7):
            api.nodes[f"x{i}"] = {"id": f"x{i}", "name": f"Firma {i}",
                                  "node_type_id": "t-mandant"}
        api.auto_scope = {"applied": True, "fallback": False,
                          "node_ids": [f"x{i}" for i in range(7)]}
        client = signed_in(app, identity_repo, role="member")
        notice = search(client).get_json()["notices"][0]
        assert notice["names"] == [f"Firma {i}" for i in range(5)]
        assert notice["hidden_count"] == 2

    def test_no_name_in_a_log_line(self, filters_app, identity_repo, caplog):
        app, api = filters_app
        api.nodes["s1"] = {"id": "s1", "name": "Sentinel-Knoten AG", "node_type_id": "t-mandant"}
        api.auto_scope = {"applied": True, "fallback": False, "node_ids": ["s1"]}
        client = signed_in(app, identity_repo, role="member")
        caplog.set_level(logging.DEBUG)
        assert search(client).get_json()["notices"][0]["names"] == ["Sentinel-Knoten AG"]
        assert "Sentinel-Knoten" not in "\n".join(r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_doc_fields_view.py::TestNotices tests/test_doc_fields_capability.py::TestNodeNamesFor tests/test_web_search_doc_fields.py::TestNotices tests/test_web_search_doc_fields.py::TestFeatureOffParity`
Expected: FAIL — `AttributeError: module 'doc_fields_view' has no attribute 'notice'`, `... 'doc_fields_capability' has no attribute 'node_names_for'`, `KeyError: 'notices'`, the fake ignores `return_fields_unreadable`.

- [ ] **Step 3: Write minimal implementation**

`src/doc_fields_view.py` — append at the end of the file:

```python
# ---------------------------------------------------------------------------
# Notices above the results (spec F3)
# ---------------------------------------------------------------------------

#: What a search answer may say above its results, in display order. The
#: browser words them (app.js); the server sends kinds, names and counts.
NOTICE_KINDS = ("return_fields_unavailable", "degraded_to_bm25", "auto_scope_applied",
                "auto_scope_fallback")
#: The most knowledge-graph names one notice names; the others are counted.
NOTICE_NAMES_MAX = 5


def notice(kind: str, names: Iterable[Any] = (), hidden_count: Any = 0) -> Dict[str, Any]:
    """One entry of a search answer's ``notices``: ``{kind, names,
    hidden_count}``. ``names`` are node names the person may see (auto
    scope only), at most five; ``hidden_count`` counts the nodes not named --
    invisible to the person, or beyond the five."""
    if kind not in NOTICE_KINDS:
        raise ValueError(f"unknown notice kind: {kind!r}")
    shown = [n.strip() for n in names or () if isinstance(n, str) and n.strip()]
    try:
        hidden = max(0, int(hidden_count or 0))
    except (TypeError, ValueError):
        hidden = 0
    hidden += max(0, len(shown) - NOTICE_NAMES_MAX)
    return {"kind": kind, "names": shown[:NOTICE_NAMES_MAX], "hidden_count": hidden}


def auto_scope_of(meta: Any) -> Tuple[Optional[str], List[str]]:
    """``(kind, node ids)`` of Knovas's narrowing by a name it recognised in
    the question (``meta["auto_scope"]`` as knovas_client keeps it):
    ``auto_scope_applied`` when the search ran inside those nodes,
    ``auto_scope_fallback`` when that found nothing and Knovas searched
    everything; ``(None, [])`` otherwise, or without node ids."""
    block = meta.get("auto_scope") if isinstance(meta, Mapping) else None
    if not isinstance(block, Mapping):
        return None, []
    ids = [i for i in block.get("node_ids") or () if isinstance(i, str) and i]
    if not ids:
        return None, []
    if block.get("fallback") is True:
        return "auto_scope_fallback", ids
    if block.get("applied") is True:
        return "auto_scope_applied", ids
    return None, []
```

`src/doc_fields_capability.py` — replace `from typing import Any, Callable, Dict, List, Optional, Tuple` with `from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple`; in the module docstring replace `and entity-name caches are per *user*, because what Knovas returns depends on` with `, entity-name and node-name caches are per *user*, because what Knovas returns depends on` (so the sentence reads "The registry, entity-name and node-name caches are per *user* …"). Replace

```python
_NAMES: Dict[Tuple[str, str], Tuple[float, Optional[Tuple[str, ...]]]] = {}
```

with

```python
_NAMES: Dict[Tuple[str, str], Tuple[float, Optional[Tuple[str, ...]]]] = {}
_NODE_NAMES: Dict[str, Tuple[float, Optional[Dict[str, str]]]] = {}
```

After `entity_names_for` (before `def invalidate(`) add:

```python
def node_names_for(client: Any, user_key: Any,
                   node_ids: Iterable[Any]) -> Tuple[List[str], int]:
    """``(names, hidden_count)`` for knowledge-graph node ids, as this
    person may see them (spec F3, auto scope).

    The names come from one node list read as this person
    (``graph_nodes()`` without ``q`` or a type, cached per person for
    ``registry_cache_seconds``): a node Knovas does not list for them is not
    named, only counted -- and so is every node when the list cannot be read
    (not cached) or holds more than 5000 nodes. Names keep the order of
    ``node_ids``, each once. Never raises; logs exception class names only.
    """
    wanted = [str(i) for i in dict.fromkeys(node_ids or ()) if i]
    if not wanted:
        return [], 0
    who, now = _user(user_key), _now()
    with _CACHE_LOCK:
        cached = _NODE_NAMES.get(who)
    if cached is not None and now < cached[0]:
        by_id = cached[1]
    else:
        try:
            nodes = client.graph_nodes()
        except Exception as exc:  # noqa: BLE001 - names are optional
            logger.warning("Node names unavailable: %s", type(exc).__name__)
            return [], len(wanted)
        by_id = None
        if len(nodes or ()) <= ENTITY_NAMES_MAX:
            by_id = {str(n["id"]): n["name"].strip() for n in nodes or ()
                     if isinstance(n, dict) and n.get("id") and isinstance(n.get("name"), str)
                     and n["name"].strip()}
        ttl = settings(getattr(client, "config", None)).registry_cache_seconds
        with _CACHE_LOCK:
            _NODE_NAMES[who] = (now + ttl, by_id)
    if by_id is None:
        return [], len(wanted)
    names: List[str] = []
    for node_id in wanted:
        name = by_id.get(node_id)
        if name and name not in names:
            names.append(name)
    return names, sum(1 for node_id in wanted if not by_id.get(node_id))
```

Replace `invalidate` with:

```python
def invalidate(user_key: Any = None) -> None:
    """Drop the registry, entity-name and node-name caches of one user, or
    of everyone when ``user_key`` is None (after a registry write, or an
    ``unknown_field`` answer)."""
    with _CACHE_LOCK:
        if user_key is None:
            _REGISTRY.clear()
            _NAMES.clear()
            _NODE_NAMES.clear()
            return
        who = _user(user_key)
        _REGISTRY.pop(who, None)
        _NODE_NAMES.pop(who, None)
        for key in [k for k in _NAMES if k[0] == who]:
            _NAMES.pop(key, None)
```

`src/web_interface/doc_fields_routes.py` — after `honesty_block` add:

```python
def search_notices(client: Any, plan: SearchPlan, meta: Any,
                   user_key: Any) -> List[Dict[str, Any]]:
    """The ``notices`` of a search answer (spec F3): what Knovas said about
    this answer that the person reads above the results.

    - ``return_fields_unavailable``: Knovas answered ``return_fields:
      {"applied": false}``, or the Platform could not ask for values under
      a capability that shows them (registry unreadable, return_fields
      refused and retried without).
    - ``degraded_to_bm25``: ``meta.degraded_to_bm25`` is true.
    - ``auto_scope_applied`` / ``auto_scope_fallback``: Knovas narrowed the
      search by a name it recognised. Names are read through the knowledge-
      graph client as this person (``node_names_for``): a node they may not
      see is counted, never named; node ids never reach the browser.

    Never raises; logs kinds and counts only.
    """
    meta = meta if isinstance(meta, Mapping) else {}
    out: List[Dict[str, Any]] = []
    echo = meta.get("return_fields")
    if ((isinstance(echo, Mapping) and echo.get("applied") is False)
            or (plan.fields_unavailable and plan.capability.sends_return_fields)):
        out.append(dfv.notice("return_fields_unavailable"))
    if meta.get("degraded_to_bm25") is True:
        out.append(dfv.notice("degraded_to_bm25"))
    kind, node_ids = dfv.auto_scope_of(meta)
    if kind is not None:
        names, hidden = dfc.node_names_for(client, user_key, node_ids)
        out.append(dfv.notice(kind, names, hidden))
        logger.info("Search notice %s: named=%d unnamed=%d", kind, len(names), hidden)
    return out
```

`src/web_interface/app.py`, `search()` — in the docstring replace `            degraded_to_bm25; null where Knovas does not say).` with `            degraded_to_bm25; null where Knovas does not say) and ``notices``` / `            (spec F3: kinds, visible names and counts, never node ids).`; replace

```python
                'honesty': doc_fields_routes.honesty_block(semantix_meta),
            }
```

with

```python
                'honesty': doc_fields_routes.honesty_block(semantix_meta),
                # What Knovas said about this answer, shown above the results
                # (spec F3): kinds, names the person may see, counts.
                'notices': doc_fields_routes.search_notices(
                    api_client, plan, semantix_meta,
                    doc_fields_routes.user_key_for(identity_gate)),
            }
```

`tests/doc_fields_fakes.py` — after `        self.scripted_pages: List[Dict[str, Any]] = []  # find answers, in order` add:

```python
        self.return_fields_unreadable = False         # return_fields: {"applied": false}
        self.degraded = False                         # meta.degraded_to_bm25 true
        self.auto_scope: Optional[Dict[str, Any]] = None  # semantix["auto_scope"], as the client keeps it
```

In `search_documents` replace

```python
        if self.mode != "off" and return_fields is not None:
            for row in rows:
                doc = self.docs.get(row.get("doc_id"))
                fields = self._return(doc, return_fields) if doc else {}
                row["fields"] = fields
                row["title"], row["title_from_values"] = display_title(
                    row["doc_id"], row.get("title"), fields.get("title"))
            meta["return_fields"] = {"applied": True}
```

with

```python
        if self.mode != "off" and return_fields is not None:
            if self.return_fields_unreadable:
                # Knovas could not read the values: results without fields.
                meta["return_fields"] = {"applied": False}
            else:
                for row in rows:
                    doc = self.docs.get(row.get("doc_id"))
                    fields = self._return(doc, return_fields) if doc else {}
                    row["fields"] = fields
                    row["title"], row["title_from_values"] = display_title(
                        row["doc_id"], row.get("title"), fields.get("title"))
                meta["return_fields"] = {"applied": True}
```

and replace

```python
            "degraded_to_bm25": False,
        })
        return {"results": rows, "total": len(rows), "semantix": meta}
```

with

```python
            "degraded_to_bm25": bool(self.degraded),
        })
        if self.auto_scope is not None:
            meta["auto_scope"] = copy.deepcopy(self.auto_scope)
        return {"results": rows, "total": len(rows), "semantix": meta}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_doc_fields_view.py tests/test_doc_fields_capability.py tests/test_web_search_doc_fields.py tests/test_web_documents_find.py tests/test_web_document_fields.py tests/test_doc_fields_mock_contract.py tests/test_experiments_regressions_web.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $PF/src/doc_fields_view.py $PF/src/doc_fields_capability.py \
  $PF/src/web_interface/doc_fields_routes.py $PF/src/web_interface/app.py \
  $PF/tests/doc_fields_fakes.py $PF/tests/test_doc_fields_view.py \
  $PF/tests/test_doc_fields_capability.py $PF/tests/test_web_search_doc_fields.py
git commit -F - <<'EOF'
platform: the search answer carries notices about Knovas's answer

/api/search adds "notices": values that could not be read (return_fields
applied false, or the Platform could not ask), degraded_to_bm25 also on a
non-empty list, and Knovas's automatic narrowing by a recognised name
(applied or fallback). Node names are read once per person from the
knowledge graph, without q, and cached like the registry; a node the
person may not see is counted, never named, and node ids never reach the
browser. Logs carry kinds and counts only. Spec F3.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task FLD-12: Notices rendered above the results (F3)

**Files:**
- Modify: `$PF/src/web_interface/static/js/app.js` (constructor, `performSearch`, `displayListing`, `clearResults`, `displayRefusal`; new `NOTICE_TEXTS`, `_renderSearchNotices`, `_searchNoticeText`, `_scopeNamesText`)
- Modify: `$PF/src/web_interface/templates/index.html` (`#searchNotices`)
- Modify: `KnovasPlatform/docs/features/document-fields.md` (Search section), `RELEASE_NOTES.md`
- Test: `$PF/tests/test_frontend_static.py`

**Interfaces:**
- Consumes: FLD-11's payload `notices: [{"kind", "names", "hidden_count"}]`.
- Produces: `DocumentSearchApp._renderSearchNotices(notices, shown)`, `_searchNoticeText(notice) -> string`, `_scopeNamesText(names, hidden, dative) -> string`, `DocumentSearchApp.NOTICE_TEXTS`; container `#searchNotices` above `#resultsNotice`.

- [ ] **Step 1: Write the failing test**

`tests/test_frontend_static.py` — in `test_app_js_renders_field_data_without_markup` replace

```python
    for name in ("_appendFieldChips", "_showReasonedEmptyState", "displayListing",
                 "displayRefusal"):
```

with

```python
    for name in ("_appendFieldChips", "_showReasonedEmptyState", "displayListing",
                 "displayRefusal", "_renderSearchNotices"):
```

in `test_index_loads_doc_fields_after_app_and_has_its_containers` replace `                       "showListButton", "resultsHeading"):` with `                       "showListButton", "resultsHeading", "searchNotices"):` and after `    assert 'id="previewFieldsSection" hidden' in html` add `    assert 'id="searchNotices" hidden' in html`. Append:

```python
def test_search_notices_come_from_the_answer():
    """F3: the page shows what the server put in ``notices``; listings,
    refusals and a cleared list carry none."""
    source = _source(APP_JS)
    assert "this._renderSearchNotices(data.notices" in _code(_method_body(source, "performSearch"))
    for name in ("displayListing", "displayRefusal", "clearResults"):
        assert "this._renderSearchNotices([], 0)" in _code(_method_body(source, name)), name


_APP_EXPORT = "\n;globalThis.__App = DocumentSearchApp;"


@needs_node
class TestSearchNotices:
    def _run(self, body):
        return _run_node(body, [APP_JS], suffix=_APP_EXPORT)

    CASES = [
        ({"kind": "return_fields_unavailable"},
         "Feldwerte konnten nicht gelesen werden; die Treffer werden ohne Werte angezeigt."),
        ({"kind": "degraded_to_bm25"},
         "Eingeschränkte Suchqualität: diese Treffer wurden nur über genaue "
         "Wörter gefunden. Für die volle Qualität später erneut suchen."),
        ({"kind": "auto_scope_applied", "names": ["Muster AG"], "hidden_count": 0},
         "Suche automatisch auf Muster AG eingegrenzt (in der Frage erkannt)."),
        ({"kind": "auto_scope_applied", "names": ["Muster AG", "Beispiel GmbH"],
          "hidden_count": 0},
         "Suche automatisch auf Muster AG und Beispiel GmbH eingegrenzt (in der Frage erkannt)."),
        ({"kind": "auto_scope_applied", "names": ["Muster AG"], "hidden_count": 1},
         "Suche automatisch auf Muster AG und 1 weiteren Eintrag eingegrenzt "
         "(in der Frage erkannt)."),
        ({"kind": "auto_scope_applied", "names": ["Muster AG", "Beispiel GmbH"],
          "hidden_count": 2},
         "Suche automatisch auf Muster AG, Beispiel GmbH und 2 weitere Einträge "
         "eingegrenzt (in der Frage erkannt)."),
        ({"kind": "auto_scope_applied", "names": [], "hidden_count": 2},
         "Suche automatisch auf 2 Einträge eingegrenzt (in der Frage erkannt)."),
        ({"kind": "auto_scope_applied", "names": [], "hidden_count": 1},
         "Suche automatisch auf 1 Eintrag eingegrenzt (in der Frage erkannt)."),
        ({"kind": "auto_scope_fallback", "names": ["Muster AG"], "hidden_count": 0},
         "In Muster AG nichts gefunden – alle Dokumente durchsucht."),
        ({"kind": "auto_scope_fallback", "names": ["Muster AG"], "hidden_count": 2},
         "In Muster AG und 2 weiteren Einträgen nichts gefunden – "
         "alle Dokumente durchsucht."),
        ({"kind": "auto_scope_fallback", "names": [], "hidden_count": 1},
         "In 1 erkannten Eintrag nichts gefunden – alle Dokumente durchsucht."),
        ({"kind": "something_new"}, ""),
    ]

    def test_each_kind_reads_in_german(self):
        notices = [n for n, _ in self.CASES]
        result = self._run("const cases = " + json.dumps(notices) + ";\n"
                           "out(cases.map((n) => __App.prototype._searchNoticeText"
                           ".call(__App.prototype, n)));")
        assert result == [text for _, text in self.CASES]

    def test_notices_go_above_the_results_as_text(self):
        result = self._run(r"""
        const box = new FakeEl('div');
        const app = Object.create(__App.prototype);
        app.searchNotices = box;
        app._renderSearchNotices([
          { kind: 'auto_scope_applied', names: ['<b>Muster AG</b>'], hidden_count: 0 },
          { kind: 'degraded_to_bm25', names: [], hidden_count: 0 },
          { kind: 'something_new' },
        ], 3);
        const shown = { hidden: box.hidden, kinds: box.children.map((c) => c.dataset.kind),
                        first: box.children[0].textContent };
        app._renderSearchNotices([{ kind: 'degraded_to_bm25' },
          { kind: 'return_fields_unavailable' },
          { kind: 'auto_scope_fallback', names: ['Muster AG'], hidden_count: 0 }], 0);
        const empty = box.children.map((c) => c.dataset.kind);
        app._renderSearchNotices([], 0);
        out({ shown, empty, hiddenAfter: box.hidden });
        """)
        assert result["shown"] == {
            "hidden": False, "kinds": ["auto_scope_applied", "degraded_to_bm25"],
            "first": "Suche automatisch auf <b>Muster AG</b> eingegrenzt (in der Frage erkannt)."}
        assert result["empty"] == ["auto_scope_fallback"]
        assert result["hiddenAfter"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_frontend_static.py`
Expected: FAIL — `AssertionError: method _renderSearchNotices not found`, `searchNotices` missing in index.html, Node: `TypeError: __App.prototype._searchNoticeText is not a function`.

- [ ] **Step 3: Write minimal implementation**

`src/web_interface/templates/index.html` — replace

```html
                <p class="results-notice" id="resultsNotice" hidden></p>
```

with

```html
                <!-- Hinweise von Knovas zu dieser Antwort (app.js, per textContent). -->
                <div class="search-notices" id="searchNotices" hidden></div>
                <p class="results-notice" id="resultsNotice" hidden></p>
```

`src/web_interface/static/js/app.js` — in the constructor replace `        this.resultsNotice = document.getElementById('resultsNotice');` with

```js
        this.resultsNotice = document.getElementById('resultsNotice');
        this.searchNotices = document.getElementById('searchNotices');
```

In `performSearch` replace

```js
                this.displayResults(data.results, data.total, data.semantix, data.has_more);
                if (this.docFields) this.docFields.renderSearchState(data.document_fields);
```

with

```js
                this.displayResults(data.results, data.total, data.semantix, data.has_more);
                this._renderSearchNotices(data.notices, (data.results || []).length);
                if (this.docFields) this.docFields.renderSearchState(data.document_fields);
```

In `displayListing` replace

```js
        this.resultsQuery.textContent = '';
        this.resultsNotice.hidden = true;
        this.resultsContainer.querySelectorAll('.empty-state')
```

with

```js
        this.resultsQuery.textContent = '';
        this.resultsNotice.hidden = true;
        this._renderSearchNotices([], 0);
        this.resultsContainer.querySelectorAll('.empty-state')
```

In `clearResults` replace

```js
        this.resultsCount.textContent = '';
        this.resultsNotice.hidden = true;
        this.loadMoreButton.hidden = true;
```

with

```js
        this.resultsCount.textContent = '';
        this.resultsNotice.hidden = true;
        this._renderSearchNotices([], 0);
        this.loadMoreButton.hidden = true;
```

In `displayRefusal` replace

```js
        this.resultsCount.textContent = '0 Ergebnisse';
        this.resultsNotice.hidden = true;
        this.loadMoreButton.hidden = true;
        if (this.showListButton) this.showListButton.hidden = true;
    }
```

with

```js
        this.resultsCount.textContent = '0 Ergebnisse';
        this.resultsNotice.hidden = true;
        this._renderSearchNotices([], 0);
        this.loadMoreButton.hidden = true;
        if (this.showListButton) this.showListButton.hidden = true;
    }
```

After `    static DEGRADED_TEXT = 'Hinweis: eingeschränkte Suchqualität.';` insert:

```js

    /**
     * Hinweise ueber den Treffern (F3): was Knovas zu dieser Antwort gesagt
     * hat. Der Server liefert Art, Namen und Anzahl -- nie Knoten-Ids --,
     * der Satz entsteht hier (tests/test_frontend_static.py).
     */
    static NOTICE_TEXTS = {
        return_fields_unavailable: 'Feldwerte konnten nicht gelesen werden; die Treffer werden ohne Werte angezeigt.',
        degraded_to_bm25: 'Eingeschränkte Suchqualität: diese Treffer wurden nur über genaue Wörter gefunden. Für die volle Qualität später erneut suchen.',
    };

    /**
     * Die Hinweise ueber der Trefferliste, per textContent. Ohne Treffer
     * sagt der Leerzustand die Suchqualitaet selbst, und Feldwerte gibt es
     * keine: diese beiden entfallen dann.
     */
    _renderSearchNotices(notices, shown) {
        const box = this.searchNotices;
        if (!box) return;
        box.textContent = '';
        const onlyWithResults = ['degraded_to_bm25', 'return_fields_unavailable'];
        (Array.isArray(notices) ? notices : []).forEach((notice) => {
            if (!notice || (!shown && onlyWithResults.includes(notice.kind))) return;
            const text = this._searchNoticeText(notice);
            if (!text) return;
            const line = document.createElement('p');
            line.className = 'results-notice search-notice';
            line.dataset.kind = String(notice.kind);
            line.textContent = text;
            box.appendChild(line);
        });
        box.hidden = box.children.length === 0;
    }

    /** Ein Hinweis als Satz; '' fuer eine Art, die diese Seite nicht kennt. */
    _searchNoticeText(notice) {
        const n = notice || {};
        const names = (Array.isArray(n.names) ? n.names : []).map((x) => String(x)).filter(Boolean);
        const hidden = Number.isInteger(n.hidden_count) && n.hidden_count > 0 ? n.hidden_count : 0;
        if (n.kind === 'auto_scope_applied') {
            return `Suche automatisch auf ${this._scopeNamesText(names, hidden, false)} `
                + 'eingegrenzt (in der Frage erkannt).';
        }
        if (n.kind === 'auto_scope_fallback') {
            return `In ${this._scopeNamesText(names, hidden, true)} nichts gefunden – `
                + 'alle Dokumente durchsucht.';
        }
        return DocumentSearchApp.NOTICE_TEXTS[n.kind] || '';
    }

    /**
     * Die erkannten Eintraege: die Namen, die die Person sehen darf, die
     * anderen nur gezaehlt. dative: "In ... nichts gefunden".
     */
    _scopeNamesText(names, hidden, dative) {
        const entry = (count) => (count === 1 ? 'Eintrag' : (dative ? 'Einträgen' : 'Einträge'));
        if (!names.length) {
            if (!hidden) return dative ? 'den erkannten Einträgen' : 'erkannte Einträge';
            return dative ? `${hidden} erkannten ${entry(hidden)}` : `${hidden} ${entry(hidden)}`;
        }
        const parts = names.slice();
        if (hidden) {
            const more = hidden === 1 || dative ? 'weiteren' : 'weitere';
            parts.push(`${hidden} ${more} ${entry(hidden)}`);
        }
        if (parts.length === 1) return parts[0];
        return `${parts.slice(0, -1).join(', ')} und ${parts[parts.length - 1]}`;
    }
```

`KnovasPlatform/docs/features/document-fields.md` — in `## Search, listing and cards`, after the **Cards** bullet insert:

```markdown
- **Notices above the results** (spec F3): "Feldwerte konnten nicht gelesen
  werden …" when Knovas could not read the values asked for (or the Platform
  could not ask), "Eingeschränkte Suchqualität …" when Knovas found the
  results by exact words only, and "Suche automatisch auf … eingegrenzt" /
  "In … nichts gefunden – alle Dokumente durchsucht" when Knovas narrowed the
  search by a name it recognised in the question. Names are read through the
  person's own knowledge-graph view; nodes they may not see are counted, never
  named. The API has no switch to turn the narrowing off, so the notice
  explains and offers no action.
```

`RELEASE_NOTES.md` — insert as the last bullet of `## Dokumentfelder (Dokumentwerte)`, directly before the line starting `Anleitung: [KnovasPlatform/docs/features/document-fields.md]`:

```markdown
- **Hinweise ueber den Treffern:** wenn Knovas die Feldwerte nicht lesen
  konnte, nur ueber genaue Woerter gesucht hat, oder die Suche automatisch
  auf einen in der Frage erkannten Namen eingegrenzt hat ("Suche automatisch
  auf Muster AG eingegrenzt"; Namen, die die Person nicht sehen darf, werden
  nur gezaehlt).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_frontend_static.py tests/test_experiments_frontend.py::TestSearchHitCards tests/test_experiments_frontend.py::TestScriptsStatic tests/test_doc_fields_render.py`
Expected: PASS (incl. `test_app_js_defines_every_method_once`, `test_javascript_parses[app.js]`).

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $PF/src/web_interface/static/js/app.js $PF/src/web_interface/templates/index.html \
  KnovasPlatform/docs/features/document-fields.md RELEASE_NOTES.md $PF/tests/test_frontend_static.py
git commit -F - <<'EOF'
platform: show Knovas's notices above the results

The search page renders the answer's notices above the list, built with
textContent: values that could not be read, reduced search quality on a
non-empty list, and "Suche automatisch auf <Namen> eingegrenzt" / "In
<Namen> nichts gefunden - alle Dokumente durchsucht" with unnamed nodes
counted. Listings, refusals and a cleared list carry none. Spec F3.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task FLD-13: Mock plays `auto_scope` and unreadable values; end to end (F3)

**Files:**
- Modify: `$MOCK/app.py` (module docstring, `AUTO_SCOPE_MODES`, `MockState.__init__`, `MockState.detect_nodes`, `create_app`, `secured_query`)
- Modify: `$MOCK/testing.py` (docstrings)
- Test: `$MOCK/tests/test_mock_doc_fields.py`, `$PF/tests/test_doc_fields_mock_contract.py`

**Interfaces:**
- Consumes: FLD-10 (client keeps `auto_scope`), FLD-11 (`notices`).
- Produces: `create_app(..., auto_scope=None)` (env `MOCK_AUTO_SCOPE`, `off` | `applied` | `fallback`; anything else `ValueError`); `MockState.auto_scope`, `MockState.detect_nodes(queries) -> List[str]`, `MockState.return_fields_unreadable` (switch, default False). A query whose text names a node (folded) gets the KB `develop` block `{"detections": [{node_id, identifier_id: None, channel: "lexical", score: 1.0}], "node_ids", "applied", "fallback", "canonicalized": False, "residualized": False}` in every doc-fields mode; the mock does not narrow the results. Goldens unchanged.

- [ ] **Step 1: Write the failing test**

`$MOCK/tests/test_mock_doc_fields.py` — add after class `TestFiltersUncalibrated`:

```python
class TestAutoScope:
    """QUERY_AUTO_SCOPE_ENABLED: Knovas narrows a search to the nodes it
    recognises in the question (shape of query_pipeline.py, KB develop)."""

    def test_off_by_default_and_from_the_environment(self, monkeypatch):
        monkeypatch.delenv("MOCK_AUTO_SCOPE", raising=False)
        assert "auto_scope" not in Mock(doc_fields="filters").query(Input="Muster AG")[1]
        monkeypatch.setenv("MOCK_AUTO_SCOPE", "applied")
        assert Mock(doc_fields="filters").state.auto_scope == "applied"

    @pytest.mark.parametrize("mode", ["off", "values", "filters"])
    def test_applied_names_the_detected_node(self, mode):
        status, body = Mock(doc_fields=mode, auto_scope="applied").query(
            Input="Was schuldet die muster ag?")
        node = testing.mock_module().stable_id("node", "Muster AG")
        assert status == 200
        assert body["auto_scope"] == {
            "detections": [{"node_id": node, "identifier_id": None, "channel": "lexical",
                            "score": 1.0}],
            "node_ids": [node], "applied": True, "fallback": False,
            "canonicalized": False, "residualized": False}

    def test_fallback(self):
        block = Mock(doc_fields="filters", auto_scope="fallback").query(
            Input="Beispiel GmbH")[1]["auto_scope"]
        assert (block["applied"], block["fallback"]) == (False, True)

    def test_no_name_no_block(self):
        assert "auto_scope" not in Mock(doc_fields="filters", auto_scope="applied").query(
            Input="lease")[1]

    def test_a_bad_mode_fails_loudly(self):
        with pytest.raises(ValueError):
            testing.load_mock_app(auto_scope="sometimes")


class TestReturnFieldsUnreadable:
    def test_values_cannot_be_read(self):
        mock = Mock(doc_fields="filters")
        mock.state.return_fields_unreadable = True
        body = mock.query(Input="lease", return_fields=["doc_type"])[1]
        assert body["return_fields"] == {"applied": False}
        assert body["results"] and all("fields" not in r for r in body["results"])
        body = mock.query(Input="lease", where={"doc_type": "contract"}, return_fields=True)[1]
        assert body["where"]["applied"] is True and body["return_fields"] == {"applied": False}
```

`$PF/tests/test_doc_fields_mock_contract.py`, class `TestRoutesOnTheMock` — add:

```python
    def test_auto_scope_is_named_through_the_person_s_graph(self, platform_db, tmp_path,
                                                            monkeypatch, identity_repo):
        """F3 end to end: the mock recognises "Muster AG"; the Platform names
        it from GET /secured/graph/nodes as the person, without q."""
        app, state = _app_on_mock(platform_db, tmp_path, monkeypatch, "filters",
                                  auto_scope="applied")
        state.add_document("rc-sync/Muster AG/Vertrag_1.pdf", title="Vertrag Muster AG",
                           snippet="Mietvertrag mit der Muster AG",
                           fields={"doc_type": "contract"})
        client = signed_in(app, identity_repo, role="member")
        body = client.post("/api/search", json={"query": "Muster AG"}).get_json()
        assert [r["doc_id"] for r in body["results"]] == ["rc-sync/Muster AG/Vertrag_1.pdf"]
        assert body["notices"] == [{"kind": "auto_scope_applied", "names": ["Muster AG"],
                                    "hidden_count": 0}]
        reads = [r for r in state.requests if r["path"] == "/secured/graph/nodes"]
        assert len(reads) == 1 and reads[0]["query"] == {}, "never with the typed text"

    def test_unreadable_values_are_said(self, platform_db, tmp_path, monkeypatch,
                                        identity_repo):
        app, state = _app_on_mock(platform_db, tmp_path, monkeypatch, "filters")
        state.return_fields_unreadable = True
        client = signed_in(app, identity_repo, role="member")
        body = client.post("/api/search", json={"query": "lease"}).get_json()
        assert body["results"] and body["results"][0]["fields_display"] == []
        assert [n["kind"] for n in body["notices"]] == ["return_fields_unavailable"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mock-pytest tests/test_mock_doc_fields.py::TestAutoScope tests/test_mock_doc_fields.py::TestReturnFieldsUnreadable`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'auto_scope'`; `return_fields` still `{"applied": True}`.
Run: `pf-pytest tests/test_doc_fields_mock_contract.py::TestRoutesOnTheMock`
Expected: FAIL in the two new tests for the same reasons.

- [ ] **Step 3: Write minimal implementation**

`$MOCK/app.py` — in the module docstring insert directly before the paragraph starting `` `refuse_init_fields="<status>:<code>"` ``:

```
`auto_scope` (env `MOCK_AUTO_SCOPE`: `off`, `applied`, `fallback`) plays the
server's automatic narrowing by name (QUERY_AUTO_SCOPE_ENABLED; KB develop
query_pipeline.py): a query whose text names a node (folded) gets the
`auto_scope` block with that node, applied or fallback as chosen, in every
mode. The mock does not narrow the results; it only reports. The switch
`return_fields_unreadable` answers a query that asks for values with
`"return_fields": {"applied": false}` and no `fields` on the results.

```

Replace `MODES = ("off", "values", "filters")` with

```python
MODES = ("off", "values", "filters")
AUTO_SCOPE_MODES = ("off", "applied", "fallback")
```

In `MockState.__init__` replace

```python
    def __init__(self, doc_fields: str, calibrated: bool, refuse_init_fields: Optional[str],
                 brokered: bool) -> None:
        mode = str(doc_fields or "off").strip().lower()
        if mode not in MODES:
            raise ValueError(f"doc_fields must be one of {MODES}, got {doc_fields!r}")
        self.mode = mode
```

with

```python
    def __init__(self, doc_fields: str, calibrated: bool, refuse_init_fields: Optional[str],
                 brokered: bool, auto_scope: Optional[str] = "off") -> None:
        mode = str(doc_fields or "off").strip().lower()
        if mode not in MODES:
            raise ValueError(f"doc_fields must be one of {MODES}, got {doc_fields!r}")
        scope = str(auto_scope or "off").strip().lower()
        if scope not in AUTO_SCOPE_MODES:
            raise ValueError(f"auto_scope must be one of {AUTO_SCOPE_MODES}, got {auto_scope!r}")
        self.mode = mode
        self.auto_scope = scope
```

and replace

```python
        self.s1 = True
        self.quarantined: set = set()
```

with

```python
        self.s1 = True
        # True plays a server that could not read the values asked for:
        # results without `fields`, `return_fields: {"applied": false}`.
        self.return_fields_unreadable = False
        self.quarantined: set = set()
```

After `nodes_named` add:

```python
    def detect_nodes(self, queries: List[str]) -> List[str]:
        """The node ids whose name occurs in the question, folded: the
        mock's stand-in for the server's QueryEntityDetector (exact names
        only, in name order)."""
        text = " ".join(fold(q) for q in queries)
        return [n["id"] for n in sorted(self.nodes.values(), key=lambda n: n["name"])
                if fold(n["name"]) and fold(n["name"]) in text]
```

Replace

```python
def create_app(doc_fields: Optional[str] = None, calibrated: bool = True,
               refuse_init_fields: Optional[str] = None, brokered: bool = False) -> Flask:
    """One mock Knovas API with its own state. `doc_fields` defaults to the
    env MOCK_DOC_FIELDS, read at call time (default `off`)."""
    if doc_fields is None:
        doc_fields = os.environ.get("MOCK_DOC_FIELDS", "off")
    app = Flask(__name__)
    state = MockState(doc_fields, calibrated, refuse_init_fields, brokered)
```

with

```python
def create_app(doc_fields: Optional[str] = None, calibrated: bool = True,
               refuse_init_fields: Optional[str] = None, brokered: bool = False,
               auto_scope: Optional[str] = None) -> Flask:
    """One mock Knovas API with its own state. `doc_fields` and
    `auto_scope` default to the env MOCK_DOC_FIELDS and MOCK_AUTO_SCOPE,
    read at call time (default `off`)."""
    if doc_fields is None:
        doc_fields = os.environ.get("MOCK_DOC_FIELDS", "off")
    if auto_scope is None:
        auto_scope = os.environ.get("MOCK_AUTO_SCOPE", "off")
    app = Flask(__name__)
    state = MockState(doc_fields, calibrated, refuse_init_fields, brokered, auto_scope)
```

In `secured_query` replace

```python
            "mock": True,
        }
        if state.mode == "off":
            return jsonify(body)
```

with

```python
            "mock": True,
        }
        detected = state.detect_nodes(queries) if state.auto_scope != "off" else []
        if detected:
            # QUERY_AUTO_SCOPE_ENABLED (query_pipeline.py, KB develop): the
            # nodes named in the question, and whether the search ran inside
            # them or fell back to everything. Independent of doc fields.
            body["auto_scope"] = {
                "detections": [{"node_id": node_id, "identifier_id": None,
                                "channel": "lexical", "score": 1.0} for node_id in detected],
                "node_ids": sorted(detected),
                "applied": state.auto_scope == "applied",
                "fallback": state.auto_scope == "fallback",
                "canonicalized": False,
                "residualized": False,
            }
        if state.mode == "off":
            return jsonify(body)
```

and replace

```python
        wanted = state.wanted_keys(resolved["return_keys"]) if resolved else None
        if wanted is not None:
            for result in results:
                fields = state.fields_of(result["pointer"], wanted, node_ids=False)
                if "title" in wanted and result["pointer"] in state.anchors:
                    fields["title"] = state.title_of(result["pointer"])[0]
                result["fields"] = dict(sorted(fields.items()))
            body["return_fields"] = {"applied": True}
        return jsonify(body)
```

with

```python
        wanted = state.wanted_keys(resolved["return_keys"]) if resolved else None
        if wanted is not None and state.return_fields_unreadable:
            body["return_fields"] = {"applied": False}
        elif wanted is not None:
            for result in results:
                fields = state.fields_of(result["pointer"], wanted, node_ids=False)
                if "title" in wanted and result["pointer"] in state.anchors:
                    fields["title"] = state.title_of(result["pointer"])[0]
                result["fields"] = dict(sorted(fields.items()))
            body["return_fields"] = {"applied": True}
        return jsonify(body)
```

`$MOCK/testing.py` — replace `- \`load_mock_app(**kw)\`: \`create_app(**kw)\` of the mock (doc_fields,` / `  calibrated, refuse_init_fields, brokered); a fresh app with its own state.` with `- \`load_mock_app(**kw)\`: \`create_app(**kw)\` of the mock (doc_fields,` / `  calibrated, refuse_init_fields, brokered, auto_scope); a fresh app with its` / `  own state.`, and in `load_mock_app` replace `    refuse_init_fields=..., brokered=...). Apps share no state."""` with `    refuse_init_fields=..., brokered=..., auto_scope=...). Apps share no state."""`.

- [ ] **Step 4: Run test to verify it passes**

Run: `mock-pytest tests/test_mock_doc_fields.py tests/test_doctor_doc_fields_probe.py`
Expected: PASS (goldens untouched).
Run: `pf-pytest tests/test_doc_fields_mock_contract.py tests/test_doc_fields_render.py`
Expected: PASS.
Then the full suites once: `pf-pytest tests` and `mock-pytest tests` → PASS except the known Windows-only failures listed in the plan header.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add $MOCK/app.py $MOCK/testing.py $MOCK/tests/test_mock_doc_fields.py \
  $PF/tests/test_doc_fields_mock_contract.py
git commit -F - <<'EOF'
platform: mock plays auto_scope and unreadable values

The mock Knovas API now reports QUERY_AUTO_SCOPE_ENABLED's block (applied or
fallback, KB develop shape) for a question that names a node, and a switch
answers return_fields {"applied": false} without values. Contract tests run
both through the real client and the Platform routes: the notice names the
node as the person sees it, read without the typed text. Goldens unchanged.
Spec F3.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

## Part B5: Document fields in the Connector (RCF)

This part covers spec §7 **F4** (upload warnings carry their field key), §7 **F5** (fields are re-sent without a trigger upload) and §8 **L1** (file-property opt-ins `keywords` and `document_status`). The work is in the Knovas Connector (`KnovasConnector/`). The parts these features need in the Platform (Ingestion tab, ingestion compiler, contract copy) and the mock checks are included here too.

It runs after INT. Every anchor below quotes `$MERGED`, and new user-facing text says "Knovas Connector" as the rename sweep requires. It does not depend on EXT or PIN, except that RCF-6/7 read `ExtractedDocument.source_metadata`, which EXT does not change.

REX also edits `sync_executor.run_sync_work`, `sync_scheduler.py` and `RC_CAPABILITIES`. The anchors here avoid the lines REX rewrites. The capability is appended as the **last** entry, after anything already there. FLD (F3) adds the German text for `ambiguous_number`, which RCF-3 then shows automatically.

Order: RCF-1 → RCF-2 → RCF-3 (F4), RCF-4 → RCF-5 (F5), RCF-6 → RCF-7 → RCF-8 (L1). F5 and L1 are independent of each other. RCF-7's check against the mock uses F4's `warning_pairs`.

Decisions a reviewer should know about:
1. The contract's `KnovasUploader` is the class **`SemantixUploader`** in `sync/knovas_uploader.py`. Code identifiers keep their names (§5.3), so the probe is `SemantixUploader.probe_doc_fields()`.
2. Probe status classes: `404` → False; 5xx, network errors and **429** → None; anything else → True. The 429 case deviates from the contract, which says True. A rate-limit answer comes before routing and says nothing about the feature. Reading it as "on" would requeue and re-send billed documents every hour.
3. `GET /sync/status` → `doc_fields.warnings` changes from `{code: count}` to `[{code, key, count}]`, as the contract defines it. The Platform reads both shapes (an older Connector sends the old one). The `POST /sync` summary (`DocFieldsCycle.as_dict`) keeps `{code: count}`.
4. New capability **`metadata_fields_v2`**. Without it, a new Platform would push `keywords`/`document_status` to a 0.3.0 Connector, whose schema answers 400. The Platform requires it the way it requires `metadata_fields_v1` today.
5. **`msg:categories` is never produced today.** knovas-extract 0.4.0a1 reads `getattr(msg, "categories", None)`, and extract-msg 0.56.1 (the version the library's CI venv resolves) has no `categories` on a message, only `CalendarBase.keywords`. The Connector maps the key when it is present; the docs and release notes say Outlook categories are not read yet. A fix belongs in the library (Part A).
6. `rc_doc_fields_warnings_total{code}` gains the 1.5.0 code `ambiguous_number`, which was counted as `other`.
7. `METADATA_MAPPING_VERSION` stays `1`, pinned by a frozen digest. Bumping it would re-send every source that uses file properties.
8. Item name `document_status` (contract) for the spec's "status". There are five existing opt-ins, not four.

---

### Task RCF-1: Connector: keep the field key of every upload warning (F4, pure part)

**Files:**
- Modify: `KnovasConnector/src/sync/doc_fields_payload.py` (`FieldsEcho`, new `_warning_key`, `parse_init_echo`, `FieldsOutcome`, `outcome_after_init`)
- Test: `KnovasConnector/tests/unit/test_doc_fields_payload.py`

**Interfaces:**
- Consumes: none
- Produces:
  - `FieldsEcho.warnings: tuple[tuple[str, str], ...]`: one `(code, key)` per echo warning, in the server's order. `code` is normalised exactly as `warning_codes` counts it (`_code`: code-shaped, else `"other"`). `key` is the warning's `key` when it matches `KEY_RE`, else `""`. The path is never kept.
  - `FieldsOutcome.warnings: tuple[tuple[str, str], ...]`: the same pairs, `()` without an echo.
  - Both fields come last, so positional construction (`FieldsEcho(2, (...), Counter(...), {...})`, `FieldsOutcome("staged", 3, Counter(...))`) still works. `warning_codes` is unchanged.

- [ ] **Step 1: Write the failing test**

In `KnovasConnector/tests/unit/test_doc_fields_payload.py`, replace the expectation of `test_echo_present`:

```python
    echo = parse_init_echo(body)
    assert echo == FieldsEcho(
        staged=3,
        unknown_keys=("mandat",),
        warning_codes=Counter({"unresolved_entity": 2, "ambiguous_date": 1, "other": 1}),
        suggest={"mandat": ("mandant", "mandate", "mandat_nr")},
    )
```
with:
```python
    echo = parse_init_echo(body)
    assert echo == FieldsEcho(
        staged=3,
        unknown_keys=("mandat",),
        warning_codes=Counter({"unresolved_entity": 2, "ambiguous_date": 1, "other": 1}),
        suggest={"mandat": ("mandant", "mandate", "mandat_nr")},
        warnings=(
            ("unresolved_entity", "mandant"),
            ("ambiguous_date", "period"),
            ("unresolved_entity", "x"),
            ("other", "y"),
        ),
    )
```

Add after `test_echo_tolerates_malformed_sub_keys`:

```python
def test_echo_warning_keys_are_keys_only():
    """Spec F4: a warning names its field key. Anything that is not
    key-shaped (a label, a value, a number) is kept as "" -- its code still
    counts -- and the warning's path never travels."""
    body = {"fields": {"staged": 1, "warnings": [
        {"key": "party", "path": "fields.party[0]", "code": "unresolved_entity"},
        {"key": "Muster AG", "path": "fields.party[1]", "code": "unresolved_entity"},
        {"key": "Dokumentdatum", "path": "fields.Dokumentdatum", "code": "ambiguous_date"},
        {"path": "fields.amount", "code": "invalid_value"},
        {"key": 7, "code": "Muster AG"},
    ]}}
    echo = parse_init_echo(body)
    assert echo.warnings == (
        ("unresolved_entity", "party"),
        ("unresolved_entity", ""),
        ("ambiguous_date", ""),
        ("invalid_value", ""),
        ("other", ""),
    )
    assert echo.warning_codes == Counter(code for code, _ in echo.warnings)
    assert "fields.party[0]" not in repr(echo) and "Muster AG" not in repr(echo)


def test_outcome_carries_the_warning_pairs():
    echo = parse_init_echo({"fields": {"staged": 1, "warnings": [
        {"key": "amount", "path": "fields.amount", "code": "invalid_value"}]}})
    out = outcome_after_init({"amount": "CHF x"}, echo, "d1")
    assert out.warnings == (("invalid_value", "amount"),)
    # The transmission entry and the stored record keep codes only.
    assert out.as_tx_entry() == {"outcome": "staged", "staged": 1, "warning_codes": ["invalid_value"]}
    assert record_for(out).warning_codes == ("invalid_value",)
    assert outcome_after_init({"amount": "CHF x"}, None, "d1").warnings == ()
    assert refused_outcome("unknown_field", "d1").warnings == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_doc_fields_payload.py::test_echo_present tests/unit/test_doc_fields_payload.py::test_echo_warning_keys_are_keys_only tests/unit/test_doc_fields_payload.py::test_outcome_carries_the_warning_pairs`
Expected: FAIL with `TypeError: FieldsEcho.__init__() got an unexpected keyword argument 'warnings'` for the first test and `AttributeError: 'FieldsEcho' object has no attribute 'warnings'` for the other two.

- [ ] **Step 3: Write minimal implementation**

`KnovasConnector/src/sync/doc_fields_payload.py`. Replace:
```python
def _keys(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    out: list[str] = []
    for value in values:
        if _is_key(value) and value not in out:
            out.append(value)
    return tuple(out)


@dataclass(frozen=True)
class FieldsEcho:
    """The init ``fields`` echo, reduced to keys, codes and counts."""

    staged: int
    unknown_keys: tuple[str, ...] = ()
    warning_codes: Counter = field(default_factory=Counter, hash=False)
    suggest: Mapping[str, tuple[str, ...]] = field(default_factory=dict, hash=False)
```
with:
```python
def _keys(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    out: list[str] = []
    for value in values:
        if _is_key(value) and value not in out:
            out.append(value)
    return tuple(out)


def _warning_key(value: Any) -> str:
    """A warning's field key, or ``""`` when it is not key-shaped (spec F4)."""
    return value if _is_key(value) else ""


@dataclass(frozen=True)
class FieldsEcho:
    """The init ``fields`` echo, reduced to keys, codes and counts.

    ``warnings`` keeps one ``(code, key)`` pair per echo warning, in the
    server's order (spec F4): the code exactly as ``warning_codes`` counts
    it, and the warning's field key -- ``""`` when what the server named is
    not key-shaped. The warning's ``path`` is not kept.
    """

    staged: int
    unknown_keys: tuple[str, ...] = ()
    warning_codes: Counter = field(default_factory=Counter, hash=False)
    suggest: Mapping[str, tuple[str, ...]] = field(default_factory=dict, hash=False)
    warnings: tuple[tuple[str, str], ...] = ()
```

Replace (in `parse_init_echo`):
```python
    (``not_accepted``), never as stored. Keys outside the key pattern and
    codes outside the code pattern are dropped or counted as ``other``.
    """
```
with:
```python
    (``not_accepted``), never as stored. Keys outside the key pattern and
    codes outside the code pattern are dropped or counted as ``other``; a
    warning's key outside the pattern becomes ``""`` (its code still counts).
    """
```

Replace:
```python
    codes: Counter = Counter()
    warnings = echo.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if isinstance(warning, MappingABC):
                codes[_code(warning.get("code"))] += 1
```
with:
```python
    codes: Counter = Counter()
    pairs: list[tuple[str, str]] = []
    warnings = echo.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if isinstance(warning, MappingABC):
                code = _code(warning.get("code"))
                codes[code] += 1
                pairs.append((code, _warning_key(warning.get("key"))))
```

Replace:
```python
    return FieldsEcho(
        staged=staged,
        unknown_keys=_keys(echo.get("unknown_keys")),
        warning_codes=codes,
        suggest=suggest,
    )
```
with:
```python
    return FieldsEcho(
        staged=staged,
        unknown_keys=_keys(echo.get("unknown_keys")),
        warning_codes=codes,
        suggest=suggest,
        warnings=tuple(pairs),
    )
```

Replace (in `FieldsOutcome`):
```python
    digest: str = ""
    transient: bool = False

    @property
    def refusal_code(self) -> Optional[str]:
```
with:
```python
    digest: str = ""
    transient: bool = False
    #: ``(code, key)`` per echo warning (spec F4); empty without an echo.
    warnings: tuple[tuple[str, str], ...] = ()

    @property
    def refusal_code(self) -> Optional[str]:
```

Replace (end of `outcome_after_init`):
```python
        suggest=dict(echo.suggest),
        digest=digest,
    )
```
with:
```python
        suggest=dict(echo.suggest),
        digest=digest,
        warnings=echo.warnings,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_doc_fields_payload.py`
Expected: PASS. The neighbouring suite `rc-pytest tests/unit/test_uploader_doc_fields.py tests/unit/test_sync_executor_doc_fields.py` still passes.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/doc_fields_payload.py KnovasConnector/tests/unit/test_doc_fields_payload.py
git commit -F - <<'EOF'
rc: keep the field key of every Knovas upload warning

Knovas 1.5.0 names the field of each upload warning ({key, path, code});
the Connector kept only the codes, so "invalid_value 3x" never said which
field to look at. The init echo now keeps one (code, key) pair per warning
next to the code counter: the key only when it is key-shaped (a label or a
value becomes ""), never the warning's path. Pure data; the cycle stats
and /sync/status follow (spec F4).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task RCF-2: Connector: count `(code, key)` per cycle and list them in `/sync/status` (F4)

**Files:**
- Modify: `KnovasConnector/src/sync/sync_executor.py` (`MAX_REPORTED_WARNINGS`, `DocFieldsCycle.warning_pairs`, `note_outcome`, new `warning_entries`)
- Modify: `KnovasConnector/src/sync/sync_scheduler.py` (`doc_fields_status`)
- Modify: `KnovasConnector/src/sync/doc_fields_metrics.py` (`WARNING_CODES`)
- Modify: `KnovasConnector/docs/operations.md`, `KnovasConnector/CHANGELOG.md`
- Test: `KnovasConnector/tests/unit/test_sync_executor_doc_fields.py`, `KnovasConnector/tests/test_doc_fields_no_values_in_logs.py`, `KnovasConnector/tests/contract/test_doc_fields_against_mock.py`

**Interfaces:**
- Consumes: `FieldsOutcome.warnings` (RCF-1).
- Produces:
  - `DocFieldsCycle.warning_pairs: Counter[tuple[str, str]]`
  - `DocFieldsCycle.warning_entries() -> list[dict[str, Any]]`
  - `sync_executor.MAX_REPORTED_WARNINGS = 50`
  - `GET /sync/status` → `doc_fields.warnings: [{"code": str, "key": str, "count": int}]`: the last cycle, sorted by count descending, ties by code then key, at most 50, and `[]` before the first cycle.
  - `DocFieldsCycle.as_dict()["warnings"]` (the `POST /sync` summary) stays `{code: count}`.
  - Metric label `ambiguous_number`.

- [ ] **Step 1: Write the failing test**

`KnovasConnector/tests/unit/test_sync_executor_doc_fields.py`: let the scripted server echo configurable warnings. Replace:
```python
        self.inits: list[dict] = []

    def rels(self) -> list[str]:
```
with:
```python
        self.inits: list[dict] = []
        self.echo_warnings: list[dict] = []

    def rels(self) -> list[str]:
```
Replace:
```python
            if self.mode == "values" and "fields" in body:
                out["fields"] = echo_for(body["fields"])
```
with:
```python
            if self.mode == "values" and "fields" in body:
                out["fields"] = dict(echo_for(body["fields"]), warnings=list(self.echo_warnings))
```
Append at the end of the file:
```python
class TestWarningKeys:
    """Spec F4: Knovas's upload warnings are counted per (code, key) and
    /sync/status lists them; the code counts stay for the POST /sync summary."""

    def test_pairs_per_cycle_and_in_the_status(self, rc):
        from sync.sync_scheduler import _remember_doc_fields, doc_fields_status

        rc.server.echo_warnings = [
            {"key": "mandant", "path": "fields.mandant", "code": "unresolved_entity"},
            {"key": "period", "path": "fields.period", "code": "invalid_value"},
        ]
        for i in range(3):
            rc.write(f"Muster AG/GJ 2024/R{i}.txt")
        result = rc.run(rc.body(rc.source(**MANDATE)))
        assert result.files_uploaded == 3
        assert result.doc_fields.warning_pairs == Counter({
            ("unresolved_entity", "mandant"): 3, ("invalid_value", "period"): 3})
        assert result.doc_fields.as_dict()["warnings"] == {"invalid_value": 3, "unresolved_entity": 3}
        _remember_doc_fields(result)
        assert doc_fields_status()["warnings"] == [
            {"code": "invalid_value", "key": "period", "count": 3},
            {"code": "unresolved_entity", "key": "mandant", "count": 3},
        ]


def test_warning_entries_are_the_most_frequent_first_and_capped():
    from sync.doc_fields_payload import FieldsOutcome
    from sync.sync_executor import MAX_REPORTED_WARNINGS, DocFieldsCycle

    cycle = DocFieldsCycle()
    pairs = tuple(("invalid_value", f"k{i:02d}") for i in range(60))
    cycle.note_outcome("staged", FieldsOutcome("staged", warnings=pairs))
    cycle.note_outcome("staged", FieldsOutcome(
        "staged", warnings=(("ambiguous_date", "document_date"),) * 3))
    entries = cycle.warning_entries()
    assert MAX_REPORTED_WARNINGS == 50 and len(entries) == 50
    assert entries[0] == {"code": "ambiguous_date", "key": "document_date", "count": 3}
    assert entries[1] == {"code": "invalid_value", "key": "k00", "count": 1}
    assert entries[-1] == {"code": "invalid_value", "key": "k48", "count": 1}
    assert DocFieldsCycle().warning_entries() == []
```

`KnovasConnector/tests/test_doc_fields_no_values_in_logs.py`: replace:
```python
        self.mode = "values"
        self.refuse: Optional[requests.Response] = None
        self.fail_all: Optional[requests.Response] = None
```
with:
```python
        self.mode = "values"
        self.refuse: Optional[requests.Response] = None
        self.fail_all: Optional[requests.Response] = None
        self.extra_warnings: list[dict[str, Any]] = []
```
Replace:
```python
                    "warnings": [{"key": k, "path": f"fields.{k}", "code": "unresolved_entity"}
                                 for k in fields] + [{"key": "x", "path": "fields.x", "code": STATIC}],
```
with:
```python
                    "warnings": (
                        [{"key": k, "path": f"fields.{k}", "code": "unresolved_entity"} for k in fields]
                        + [{"key": "x", "path": "fields.x", "code": STATIC}]
                        + list(self.extra_warnings)
                    ),
```
Append at the end of the file:
```python
def test_warning_keys_reach_the_status_never_a_value(rig, caplog):
    """Spec F4: /sync/status names the field key of each warning. A "key"
    that is really a value, and the warning's path, never appear -- nor in
    a log line."""
    from sync.sync_scheduler import _remember_doc_fields, doc_fields_status

    root, server = rig
    caplog.set_level(logging.DEBUG)
    server.extra_warnings = [
        {"key": STATIC, "path": "fields.keywords[0]", "code": "invalid_value"},
        {"key": "keywords", "path": f"fields.keywords.{CAPTURE}", "code": "invalid_value"},
    ]
    _write(root / CAPTURE / PERIOD / "Rechnung.txt")
    result = _run(_body(root, fields={"doc_type": STATIC, "keywords": [STATIC]},
                        field_templates=["{party}/{period}/**"]))
    _remember_doc_fields(result)
    status = doc_fields_status()
    entries = {(w["code"], w["key"]): w["count"] for w in status["warnings"]}
    assert entries[("invalid_value", "keywords")] == 1
    assert entries[("invalid_value", "")] == 1, "a value in the key slot is dropped, the warning counted"
    assert entries[("unresolved_entity", "doc_type")] == 1
    assert entries[("other", "x")] == 1
    text = json.dumps(status, ensure_ascii=False)
    for sentinel in SENTINELS:
        assert sentinel not in text
    _assert_clean(caplog, *SENTINELS)


def test_every_warning_code_of_knovas_1_5_is_a_metric_label():
    from sync import doc_fields_metrics as m

    documented = {"invalid_value", "type_mismatch", "checksum_failed", "restricted_identifier",
                  "cap_exceeded", "ambiguous_date", "ambiguous_number", "unresolved_entity",
                  "ambiguous_entity"}
    assert documented <= m.WARNING_CODES
```

`KnovasConnector/tests/contract/test_doc_fields_against_mock.py` (`test_status_advertises_capabilities_and_the_doc_fields_block`): replace:
```python
        assert block["warnings"] == {"unresolved_entity": 1}
```
with:
```python
        assert block["warnings"] == [{"code": "unresolved_entity", "key": "party", "count": 1}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_sync_executor_doc_fields.py::TestWarningKeys tests/unit/test_sync_executor_doc_fields.py::test_warning_entries_are_the_most_frequent_first_and_capped tests/test_doc_fields_no_values_in_logs.py tests/contract/test_doc_fields_against_mock.py::TestRoutes::test_status_advertises_capabilities_and_the_doc_fields_block`
Expected: FAIL with:
- `AttributeError: 'DocFieldsCycle' object has no attribute 'warning_pairs'`
- `ImportError: cannot import name 'MAX_REPORTED_WARNINGS'`
- `TypeError: string indices must be integers` (the status block is still a dict)
- `AssertionError` for `ambiguous_number` and for the contract test

- [ ] **Step 3: Write minimal implementation**

`KnovasConnector/src/sync/sync_executor.py`. Replace:
```python
#: At most this many unknown keys / suggestions are kept per cycle.
MAX_REPORTED_KEYS = 20
```
with:
```python
#: At most this many unknown keys / suggestions are kept per cycle.
MAX_REPORTED_KEYS = 20
#: At most this many ``(code, key)`` warning entries are reported per cycle
#: (spec F4), the most frequent first.
MAX_REPORTED_WARNINGS = 50
```
Replace:
```python
    Outcome names, refusal and warning codes, failure classes and counts --
    plus the registry or configuration KEYS the server did not know. Never a
    value, a capture, a path or a pointer.
```
with:
```python
    Outcome names, refusal and warning codes, failure classes and counts --
    plus the registry or configuration KEYS the server did not know or named
    in a warning. Never a value, a capture, a path or a pointer.
```
Replace:
```python
    warnings: Counter = field(default_factory=Counter)
    dropped: Counter = field(default_factory=Counter)
    unknown_keys: list[str] = field(default_factory=list)
```
with:
```python
    warnings: Counter = field(default_factory=Counter)
    #: ``(code, key)`` per Knovas upload warning (spec F4): the field key,
    #: never the value or the warning's path.
    warning_pairs: Counter = field(default_factory=Counter)
    dropped: Counter = field(default_factory=Counter)
    unknown_keys: list[str] = field(default_factory=list)
```
Replace:
```python
        self.warnings.update(fields.warning_codes)
        for key in fields.unknown_keys:
```
with:
```python
        self.warnings.update(fields.warning_codes)
        self.warning_pairs.update(fields.warnings)
        for key in fields.unknown_keys:
```
Replace:
```python
            "rel_collisions": self.rel_collisions,
            "requeued": self.requeued,
        }

    def as_dict(self) -> dict[str, Any]:
```
with:
```python
            "rel_collisions": self.rel_collisions,
            "requeued": self.requeued,
        }

    def warning_entries(self) -> list[dict[str, Any]]:
        """``[{"code", "key", "count"}]`` for /sync/status (spec F4): the most
        frequent first (ties by code, then key), at most MAX_REPORTED_WARNINGS.
        Codes and field keys only."""
        ranked = sorted(self.warning_pairs.items(), key=lambda item: (-item[1], item[0]))
        return [
            {"code": code, "key": key, "count": count}
            for (code, key), count in ranked[:MAX_REPORTED_WARNINGS]
        ]

    def as_dict(self) -> dict[str, Any]:
```

`KnovasConnector/src/sync/sync_scheduler.py`. Replace:
```python
def doc_fields_status() -> dict[str, Any]:
    """The ``doc_fields`` block of GET /sync/status: keys, codes and counts.

    ``pending_reupload`` is an estimate for the Platform's ETA: what the last
```
with:
```python
def doc_fields_status() -> dict[str, Any]:
    """The ``doc_fields`` block of GET /sync/status: keys, codes and counts.

    ``warnings`` lists ``{"code", "key", "count"}`` of the last cycle, the
    most frequent first (spec F4); the POST /sync summary keeps
    ``{code: count}``.

    ``pending_reupload`` is an estimate for the Platform's ETA: what the last
```
Replace:
```python
        "warnings": dict(sorted(cycle.warnings.items())),
```
with:
```python
        "warnings": cycle.warning_entries(),
```

`KnovasConnector/src/sync/doc_fields_metrics.py`. Replace:
```python
        "ambiguous_date",
        "unresolved_entity",
```
with:
```python
        "ambiguous_date",
        "ambiguous_number",
        "unresolved_entity",
```

`KnovasConnector/docs/operations.md`. Replace:
```
  "warnings": {"unresolved_entity": 12, "ambiguous_date": 1},
```
with:
```
  "warnings": [{"code": "unresolved_entity", "key": "party", "count": 12},
               {"code": "ambiguous_date", "key": "document_date", "count": 1}],
```
Replace:
```
- `unknown_keys` / `suggest` hold registry keys only (at most 20, from the last cycle): a configured key Knovas does not know, and the keys it suggests instead.
```
with:
```
- `unknown_keys` / `suggest` hold registry keys only (at most 20, from the last cycle): a configured key Knovas does not know, and the keys it suggests instead.
- `warnings` lists Knovas's upload warnings of the last cycle per code and field key, the most frequent first, at most 50. The key is the field's key, never a value or the warning's JSON path; what is not key-shaped is reported as `""`. Before this release the block was `{code: count}`; the `POST /sync` summary keeps that shape, and the metric `rc_doc_fields_warnings_total` stays per code.
```
Replace:
```
`ambiguous_date`, `unresolved_entity`
```
with:
```
`ambiguous_date`, `ambiguous_number`, `unresolved_entity`
```

`KnovasConnector/CHANGELOG.md`. Replace the first:
```
## Unreleased

```
with:
```
## Unreleased

### Document fields per Knovas 1.5.0 (Knovas Connector)

- **Upload warnings name their field** (F4): `GET /sync/status` → `doc_fields.warnings` is a list of `{code, key, count}` for the last cycle, the most frequent first, at most 50 (it was `{code: count}`; the `POST /sync` summary keeps that shape). Field keys only — never a value or the warning's JSON path; a "key" that is not key-shaped is reported as `""`. `rc_doc_fields_warnings_total{code}` counts `ambiguous_number` (Knovas 1.5.0) instead of `other`.

```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_sync_executor_doc_fields.py tests/test_doc_fields_no_values_in_logs.py tests/contract/test_doc_fields_against_mock.py`
Expected: PASS. The neighbouring suite `rc-pytest tests/unit/test_sync_scheduler.py tests/integration/test_sync_routes.py tests/test_sync_source_fields_schema.py` still passes.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/sync_executor.py KnovasConnector/src/sync/sync_scheduler.py \
  KnovasConnector/src/sync/doc_fields_metrics.py KnovasConnector/docs/operations.md \
  KnovasConnector/CHANGELOG.md KnovasConnector/tests/unit/test_sync_executor_doc_fields.py \
  KnovasConnector/tests/test_doc_fields_no_values_in_logs.py \
  KnovasConnector/tests/contract/test_doc_fields_against_mock.py
git commit -F - <<'EOF'
rc: report Knovas upload warnings per code and field key in /sync/status

Spec F4. Each cycle now counts (code, key) pairs next to the code counter,
and GET /sync/status lists them as [{code, key, count}], the most frequent
first and at most 50, so the Ingestion tab can say "invalid_value 3x
(amount)". Keys only: a value in the key slot becomes "", the path is never
kept; a privacy test sends value sentinels through every slot. The POST
/sync summary and the metric stay per code; the metric's closed label set
gains ambiguous_number, a 1.5.0 code that was counted as "other".

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task RCF-3: Platform: Ingestion tab shows "invalid_value 3× (amount)" (F4)

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/admin_ingestion.py` (new `MAX_WARNING_ENTRIES`, new `_warning_entries`, `doc_fields_status`)
- Modify: `KnovasPlatform/docs/features/document-fields.md`
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_web_admin_ingestion.py` (`TestStatusBar`)

The status lines are rendered server-side (`templates/admin_ingestion.html`, `df.status.lines`). `static/js/admin_ingestion.js` does not render them, so neither file changes.

**Interfaces:**
- Consumes: the Connector's `doc_fields.warnings` (RCF-2: `[{code, key, count}]`), and an older Connector's `{code: count}`.
- Produces: `admin_ingestion._warning_entries(value) -> list[tuple[str, str, int]]` and `MAX_WARNING_ENTRIES = 50`. Two status lines:
  - `"Hinweise von Knovas: <code> <n>× (<key>), …."`
  - `"Bedeutung der Codes – <code>: <German text>; …."`, each code once, with the existing `doc_fields_view.warning_text`.

- [ ] **Step 1: Write the failing test**

In `TestStatusBar.BLOCK`, replace:
```python
        "warnings": {"unresolved_entity": 12, "ambiguous_date": 1},
```
with:
```python
        "warnings": [{"code": "unresolved_entity", "key": "party", "count": 12},
                     {"code": "invalid_value", "key": "amount", "count": 3},
                     {"code": "ambiguous_date", "key": "document_date", "count": 1},
                     {"code": "invalid_value", "key": SENTINEL, "count": 2},
                     {"code": SENTINEL, "key": "party", "count": 9},
                     {"code": "invalid_value", "key": "amount", "count": 0}],
```
In `test_every_count_the_spec_lists_is_shown`, replace:
```python
        assert "unresolved_entity 12× (nicht verknüpft)" in text
```
with:
```python
        assert ("Hinweise von Knovas: unresolved_entity 12× (party), invalid_value 3× "
                "(amount), ambiguous_date 1× (document_date), invalid_value 2×.") in text
        assert ("Bedeutung der Codes – unresolved_entity: nicht verknüpft; "
                "invalid_value: Wert ungültig, nicht übernommen; "
                "ambiguous_date: Datum mehrdeutig – bitte prüfen.") in text
```
Add after `test_a_quiet_block`:
```python
    def test_an_older_connector_reports_codes_without_keys(self):
        from doc_fields_capability import Capability
        from web_interface.admin_ingestion import doc_fields_status

        block = dict(self.BLOCK, warnings={"unresolved_entity": 12, "ambiguous_date": 1, SENTINEL: 4})
        out = doc_fields_status(self._status(block=block), capability=Capability.values)
        text = "\n".join(line["text"] for line in out["lines"])
        assert "Hinweise von Knovas: ambiguous_date 1×, unresolved_entity 12×." in text
        assert "Bedeutung der Codes – ambiguous_date: Datum mehrdeutig" in text
        assert SENTINEL not in text

    def test_at_most_fifty_warning_entries(self):
        from doc_fields_capability import Capability
        from web_interface.admin_ingestion import MAX_WARNING_ENTRIES, doc_fields_status

        block = dict(self.BLOCK, warnings=[{"code": "invalid_value", "key": f"k{i}", "count": 1}
                                           for i in range(60)])
        out = doc_fields_status(self._status(block=block), capability=Capability.values)
        (line,) = [entry["text"] for entry in out["lines"]
                   if entry["text"].startswith("Hinweise von Knovas")]
        assert MAX_WARNING_ENTRIES == 50
        assert line.count("invalid_value 1×") == 50 and "(k49)" in line and "(k50)" not in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_web_admin_ingestion.py::TestStatusBar`
Expected: FAIL. The new lines are missing (`AssertionError`) and `MAX_WARNING_ENTRIES` cannot be imported (`ImportError`). Today's `_codes` drops a list, so no "Hinweise" line is rendered at all.

- [ ] **Step 3: Write minimal implementation**

`.../src/web_interface/admin_ingestion.py`. Replace:
```python
def _codes(mapping: Any) -> list[tuple[str, int]]:
    """``{code: count}`` from a status block, keeping only code-shaped keys
    and positive counts (the block is meant to hold nothing else)."""
    if not isinstance(mapping, Mapping):
        return []
    return sorted((code, _count(n)) for code, n in mapping.items()
                  if isinstance(code, str) and _CODE_RE.match(code) and _count(n))
```
with:
```python
def _codes(mapping: Any) -> list[tuple[str, int]]:
    """``{code: count}`` from a status block, keeping only code-shaped keys
    and positive counts (the block is meant to hold nothing else)."""
    if not isinstance(mapping, Mapping):
        return []
    return sorted((code, _count(n)) for code, n in mapping.items()
                  if isinstance(code, str) and _CODE_RE.match(code) and _count(n))


#: Warning entries the status bar shows at most (the Knovas Connector sends
#: at most 50, the most frequent first).
MAX_WARNING_ENTRIES = 50


def _warning_entries(value: Any) -> list[tuple[str, str, int]]:
    """``(code, key, count)`` from a status block's ``warnings``.

    A current Knovas Connector sends ``[{"code", "key", "count"}]`` (spec
    F4), an older one ``{code: count}`` -- read with an empty key. Only
    code-shaped codes, key-shaped keys and positive counts are kept, so
    nothing but a code, a field key or a number reaches the page.
    """
    if isinstance(value, Mapping):
        return [(code, "", n) for code, n in _codes(value)]
    if not isinstance(value, (list, tuple)):
        return []
    entries: list[tuple[str, str, int]] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        code, key, n = entry.get("code"), entry.get("key"), _count(entry.get("count"))
        if not (isinstance(code, str) and _CODE_RE.match(code) and n):
            continue
        entries.append((code, key if isinstance(key, str) and _CODE_RE.match(key) else "", n))
    return entries[:MAX_WARNING_ENTRIES]
```
Replace (in `doc_fields_status`):
```python
    warnings = _codes(block.get("warnings"))
    if warnings:
        say("Hinweise von Knovas: " + ", ".join(
            f"{code} {n}× ({warning_text(code)})" for code, n in warnings) + ".")
```
with:
```python
    warnings = _warning_entries(block.get("warnings"))
    if warnings:
        # "invalid_value 3x (amount)" per code and field key, then each
        # code's meaning once (spec F4).
        say("Hinweise von Knovas: " + ", ".join(
            f"{code} {n}×" + (f" ({key})" if key else "") for code, key, n in warnings) + ".")
        say("Bedeutung der Codes – " + "; ".join(
            f"{code}: {warning_text(code)}"
            for code in dict.fromkeys(code for code, _, _ in warnings)) + ".")
```

`KnovasPlatform/docs/features/document-fields.md`. Replace:
```
the fields, counts of refused / not accepted / failed documents, Knovas's
warning codes, unknown keys with suggestions, pending re-uploads with an ETA,
```
with:
```
the fields, counts of refused / not accepted / failed documents, Knovas's
warnings per code and field key ("invalid_value 3× (amount)", at most 50, the
most frequent first, with the meaning of each code below them), unknown keys
with suggestions, pending re-uploads with an ETA,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_web_admin_ingestion.py::TestStatusBar`
Expected: PASS. The neighbouring suite `pf-pytest tests/test_web_admin_ingestion.py` still passes.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasPlatform/components/docbridge_integration/src/web_interface/admin_ingestion.py \
  KnovasPlatform/components/docbridge_integration/tests/test_web_admin_ingestion.py \
  KnovasPlatform/docs/features/document-fields.md
git commit -F - <<'EOF'
platform: name the field of each Knovas upload warning in the Ingestion tab

Spec F4. The status panel now reads the Knovas Connector's
[{code, key, count}] and says "invalid_value 3x (amount)", followed by one
line with the existing German meaning of each code. An older Connector's
{code: count} still renders, without keys. Only code- and key-shaped
strings and positive counts reach the page; at most 50 entries.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task RCF-4: Connector: capability probe `probe_doc_fields()` (F5, uploader)

**Files:**
- Modify: `KnovasConnector/src/sync/knovas_uploader.py` (`DOC_FIELDS_PROBE_PATH`, `PROBE_UNKNOWN_STATUSES`, new `SemantixUploader.probe_doc_fields`)
- Test: `KnovasConnector/tests/unit/test_uploader_doc_fields.py`, `KnovasConnector/tests/contract/test_doc_fields_against_mock.py`

How `_request` behaves today:
- It takes one char token and one request token from the ingest limiter per attempt, and raises `RequestException("ingest rate limit exceeded")` when they are exhausted.
- It retries `RETRY_STATUS` = {429, 503, 504} and network errors with backoff, up to `max_retries`.
- With `max_retries=1` it returns the first response, or raises the first exception, without sleeping. That is the single try.
- `json_body=None` sends no body.

The mock needs no change. In `off` mode, `GET /secured/graph/doc-fields` already answers the unknown-route 404, pinned by `TestOff::test_every_doc_fields_route_is_the_unknown_route_404`. In `values`/`filters` mode it answers 200 (`TestValuesRegistry::test_core_is_seeded`). A BROKERED tenant gets 401 `assertion_rejected`.

**Interfaces:**
- Consumes: `SemantixUploader._request(method, path, *, json_body=None, max_retries=5, retry_status=None)`.
- Produces: `SemantixUploader.probe_doc_fields(self) -> Optional[bool]`: one `GET /secured/graph/doc-fields` with no body.
  - `404` → `False`
  - `>= 500`, `429`, or no answer (any exception) → `None`
  - anything else → `True`
- Also produces `knovas_uploader.DOC_FIELDS_PROBE_PATH` and `PROBE_UNKNOWN_STATUSES = frozenset({429})`.

- [ ] **Step 1: Write the failing test**

Append to `KnovasConnector/tests/unit/test_uploader_doc_fields.py`:
```python
PROBE = "/secured/graph/doc-fields"


class TestCapabilityProbe:
    """``probe_doc_fields`` (spec F5): one GET without a body under the
    tenant mTLS; the status class is the whole answer."""

    @staticmethod
    def _probe(monkeypatch, answer):
        calls: list = []

        def request(method, url, json=None, **kw):
            calls.append((method, "/" + url.split("/", 3)[3], json, kw.get("cert"), kw.get("verify")))
            if isinstance(answer, Exception):
                raise answer
            return _response(answer, {"status": "error"} if answer >= 400 else {"fields": []})

        monkeypatch.setattr("sync.knovas_uploader.requests.request", request)
        return SemantixUploader().probe_doc_fields(), calls

    @pytest.mark.parametrize("status,expected", [
        (200, True), (400, True), (401, True), (403, True),
        (404, False),
        (500, None), (502, None), (503, None), (504, None),
        # A rate limit sits in front of any route: it says nothing about fields.
        (429, None),
    ])
    def test_the_status_decides(self, env, monkeypatch, status, expected):
        answer, calls = self._probe(monkeypatch, status)
        assert answer is expected
        assert len(calls) == 1, "single try: no backoff inside the probe"
        method, path, body, cert, verify = calls[0]
        assert (method, path, body) == ("GET", PROBE, None)
        assert cert and verify, "the tenant mTLS of every other call"

    @pytest.mark.parametrize("exc", [requests.ConnectionError("refused"), requests.Timeout("slow"),
                                     OSError("no CA bundle")])
    def test_no_answer_is_unknown(self, env, monkeypatch, exc):
        answer, calls = self._probe(monkeypatch, exc)
        assert answer is None and len(calls) == 1

    def test_an_exhausted_ingest_limiter_is_unknown(self, env, monkeypatch):
        monkeypatch.setattr("sync.knovas_uploader.acquire_request", lambda: False)
        answer, calls = self._probe(monkeypatch, 200)
        assert answer is None and calls == []
```

Append to `KnovasConnector/tests/contract/test_doc_fields_against_mock.py`, before `def _sync_response(result) -> dict:`:
```python
class TestCapabilityProbe:
    """Spec F5: the probe against every server state the mock plays."""

    @pytest.mark.parametrize("mode,expected", [("off", False), ("values", True), ("filters", True)])
    def test_the_answer_follows_the_server_state(self, rig, mode, expected):
        from sync.knovas_uploader import SemantixUploader

        state = rig.mock(doc_fields=mode)
        assert SemantixUploader().probe_doc_fields() is expected
        (probe,) = [r for r in state.requests if r["path"] == "/secured/graph/doc-fields"]
        assert probe["method"] == "GET" and probe["body"] == b"" and probe["query"] == {}

    def test_a_brokered_tenant_asks_for_an_assertion_and_that_means_on(self, rig):
        """BROKERED: every graph route wants a principal assertion the
        Connector does not send (401); the route exists, so fields are on."""
        from sync.knovas_uploader import SemantixUploader

        rig.mock(doc_fields="values", brokered=True)
        assert SemantixUploader().probe_doc_fields() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_uploader_doc_fields.py::TestCapabilityProbe tests/contract/test_doc_fields_against_mock.py::TestCapabilityProbe`
Expected: FAIL with `AttributeError: 'SemantixUploader' object has no attribute 'probe_doc_fields'`.

- [ ] **Step 3: Write minimal implementation**

`KnovasConnector/src/sync/knovas_uploader.py`. Replace:
```python
RETRY_STATUS = {429, 503, 504}
MAX_BACKOFF = 30.0
INIT_PATH = "/secured/init_document_transmission"
```
with:
```python
RETRY_STATUS = {429, 503, 504}
MAX_BACKOFF = 30.0
INIT_PATH = "/secured/init_document_transmission"
#: The capability probe (spec F5). While Document fields are off, Knovas
#: answers every ``/secured/graph/doc-*`` route with its unknown-route 404.
DOC_FIELDS_PROBE_PATH = "/secured/graph/doc-fields"
#: Probe answers that, besides a 5xx, say nothing about the feature: a 429
#: is a rate limit in front of any route. Read as "unknown".
PROBE_UNKNOWN_STATUSES = frozenset({429})
```
Replace (end of `delete_by_pointer`, end of the class):
```python
        if resp.status_code in (200, 404):
            return True, None
        return False, f"delete failed: {resp.status_code}"
```
with:
```python
        if resp.status_code in (200, 404):
            return True, None
        return False, f"delete failed: {resp.status_code}"

    def probe_doc_fields(self) -> Optional[bool]:
        """Whether Knovas takes document fields now (spec F5).

        One ``GET /secured/graph/doc-fields`` without a body, under the same
        mTLS and ingest limiter as every other call and without a retry (the
        executor asks again an hour later): ``404`` -> False (off, or a
        Knovas without the feature); a 5xx, a 429 or no answer at all ->
        None (unknown); any other answer -> True: the route exists, and a
        401/403 only says it wants more than this call carries (a BROKERED
        tenant's principal assertion). The answer's body is never read.
        """
        try:
            resp = self._request("GET", DOC_FIELDS_PROBE_PATH, max_retries=1)
        except Exception:  # noqa: BLE001 - a probe never fails a cycle; no answer is "unknown"
            return None
        status = resp.status_code
        if status == 404:
            return False
        if status >= 500 or status in PROBE_UNKNOWN_STATUSES:
            return None
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_uploader_doc_fields.py tests/contract/test_doc_fields_against_mock.py`
Expected: PASS. The neighbouring suite `rc-pytest tests/unit/test_knovas_uploader.py tests/test_sync_access_groups.py` still passes.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/knovas_uploader.py KnovasConnector/tests/unit/test_uploader_doc_fields.py \
  KnovasConnector/tests/contract/test_doc_fields_against_mock.py
git commit -F - <<'EOF'
rc: ask Knovas whether it takes document fields (capability probe)

Spec F5, uploader part. probe_doc_fields() sends one GET
/secured/graph/doc-fields without a body through the existing request
helper (tenant mTLS, ingest limiter) with a single try. 404 means the
feature is off (Knovas 1.5.0 answers every doc-fields route so then); a
5xx, a 429 or no answer is unknown; anything else means on, including a
BROKERED tenant's 401 for the missing assertion. 429 is unknown rather
than on: a rate limit says nothing about fields, and "on" would re-send
billed documents. Pinned against every state of the mock.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task RCF-5: Connector: hourly probe re-sends `not_accepted` documents without a trigger upload (F5, executor)

**Files:**
- Modify: `KnovasConnector/src/sync/sync_executor.py` (imports, `DOC_FIELDS_PROBE_INTERVAL_SECONDS`, `_last_doc_fields_probe`, `_claim_doc_fields_probe`, `_requeue_not_accepted`, `_probe_doc_fields`, `run_sync_work`)
- Modify: `KnovasConnector/tests/conftest.py` (`fresh_doc_fields_memory`)
- Modify: `KnovasConnector/docs/configuration.md`, `KnovasConnector/docs/operations.md`, `KnovasConnector/CHANGELOG.md`
- Test: `KnovasConnector/tests/unit/test_sync_executor_doc_fields.py`

**Where the hourly timestamp lives: a module-level `_last_doc_fields_probe` in `sync_executor`, guarded by a lock.**
- It is the only place that survives the continuous worker's cycle loop and the one-time runs of `POST /sync`. `_run_once` builds a new `SemantixUploader` every cycle, and `run_sync_work` has no executor object.
- Persisting it in the state DB would need a schema change for no benefit: a restart costs at most one extra GET.
- This matches the scheduler's existing module memory (`_last_fields_answer`, …).
- `time.monotonic()` is immune to wall-clock jumps. The lock keeps "once an hour" true under gthread (E6), even though cycles are already serialised by `_scheduler_lock`.

**Where the probe runs: after the scan, before the first upload.** The spec says to requeue the rows "the scan reaches", which is only known once `plan_sync_cycle` has run.
- It probes only when such rows exist (`count_fields_requeue_candidates(OUTCOME_NOT_ACCEPTED, plan.scanned_paths)`), so a cycle without them neither probes nor uses up the hourly slot.
- True → requeue exactly as the echo trigger does (`requeue_fields(OUTCOME_NOT_ACCEPTED, plan.scanned_paths)`, re-sent next cycle through the side queue within `RC_FIELDS_REUPLOAD_PER_CYCLE`), and `requeue_checked = True`.
- False or None → nothing changes, and the echo trigger still fires on a `staged` echo.

**Interfaces:**
- Consumes: `SemantixUploader.probe_doc_fields() -> Optional[bool]` (RCF-4). It is read with `getattr`: a duck-typed fake without it is never probed, and a `MagicMock` answer counts as "unknown".
- Produces:
  - `sync_executor.DOC_FIELDS_PROBE_INTERVAL_SECONDS = 3600.0`
  - `sync_executor._last_doc_fields_probe: Optional[float]`
  - `_claim_doc_fields_probe() -> bool`
  - `_requeue_not_accepted(state, plan, stats) -> int`
  - `_probe_doc_fields(uploader, state, plan, stats) -> bool`
  - Log line `doc_fields probe=on|off|unknown requeued=<n>`

- [ ] **Step 1: Write the failing test**

`KnovasConnector/tests/unit/test_sync_executor_doc_fields.py`. Replace:
```python
import json
import os
from collections import Counter
```
with:
```python
import json
import logging
import os
from collections import Counter
```
Replace:
```python
DELETE = "/secured/delete_information_object"
FIXED_MTIME = 1_700_000_000
```
with:
```python
DELETE = "/secured/delete_information_object"
PROBE = "/secured/graph/doc-fields"
FIXED_MTIME = 1_700_000_000
```
Replace (added by RCF-2):
```python
        self.echo_warnings: list[dict] = []
```
with:
```python
        self.echo_warnings: list[dict] = []
        #: The capability probe (spec F5): its answer -- a status, or None for
        #: no answer at all -- and the calls it got. 404: fields are off.
        self.probe_status: Optional[int] = 404
        self.probes: list[tuple[str, Any]] = []
```
Replace:
```python
        if path == DELETE:
            return _response(200, {"status": "success"})
        return _response(404, {"status": "error", "error_code": "HTTP_404"})
```
with:
```python
        if path == DELETE:
            return _response(200, {"status": "success"})
        if path == PROBE:
            self.probes.append((method, json))
            if self.probe_status is None:
                raise requests.ConnectionError("no answer")
            return _response(self.probe_status, {"status": "success", "fields": []}
                             if self.probe_status == 200 else {"status": "error"})
        return _response(404, {"status": "error", "error_code": "HTTP_404"})
```
Append at the end of the file:
```python
class TestCapabilityProbe:
    """Spec F5: documents Knovas did not take fields for come back without
    a trigger upload once Knovas answers the probe -- one probe an hour at
    most, only when the scan reached such documents, never with a body."""

    @staticmethod
    def _not_accepted(rc, *rels: str) -> dict:
        body = rc.body(rc.source(**MANDATE))
        for rel in rels:
            rc.write(rel)
        rc.server.mode = "off"
        rc.run(body)
        assert rc.server.probes == [], "no not_accepted row yet: nothing to ask about"
        assert all(rc.fields(rel).outcome == "not_accepted" for rel in rels)
        return body

    def test_off_404_changes_nothing(self, rc, caplog):
        body = self._not_accepted(rc, REL)
        caplog.set_level(logging.INFO, logger="sync.sync_executor")
        result = rc.run(body)
        assert rc.server.probes == [("GET", None)]
        assert result.doc_fields.requeued == 0 and result.files_uploaded == 0
        assert rc.fields(REL).outcome == "not_accepted" and rc.fields(REL).digest
        assert "doc_fields probe=off requeued=0" in caplog.text
        assert rc.run(body).document_sync.fields_changed == 0

    def test_on_requeues_the_reached_rows_and_they_come_back_within_the_bound(self, rc, caplog):
        rels = [f"Muster AG/GJ 2024/R{i}.txt" for i in range(5)]
        rc.env(RC_FIELDS_REUPLOAD_PER_CYCLE="2")
        body = self._not_accepted(rc, *rels)
        rc.server.mode = "values"
        rc.server.probe_status = 200
        caplog.set_level(logging.INFO, logger="sync.sync_executor")
        first = rc.run(body)
        assert first.doc_fields.requeued == 5 and first.files_uploaded == 0
        assert "doc_fields probe=on requeued=5" in caplog.text
        sent = [rc.run(body).files_uploaded for _ in range(3)]
        assert sent == [2, 2, 1], "the side queue's per-cycle bound"
        assert all(rc.fields(rel).outcome == "staged" for rel in rels)
        assert rc.server.probes == [("GET", None)], "one probe an hour"

    @pytest.mark.parametrize("answer", [500, 503, 429, None])
    def test_unknown_changes_nothing_and_is_asked_again_an_hour_later(self, rc, answer):
        import sync.sync_executor as executor

        body = self._not_accepted(rc, REL)
        rc.server.probe_status = answer
        assert rc.run(body).doc_fields.requeued == 0
        assert rc.run(body).doc_fields.requeued == 0
        assert len(rc.server.probes) == 1, "single try, and not again within the hour"
        rc.monkeypatch.setattr(executor, "_last_doc_fields_probe",
                               executor._last_doc_fields_probe
                               - executor.DOC_FIELDS_PROBE_INTERVAL_SECONDS)
        rc.server.probe_status = 200
        assert rc.run(body).doc_fields.requeued == 1
        assert len(rc.server.probes) == 2

    def test_the_echo_trigger_still_works_when_the_probe_says_off(self, rc):
        body = self._not_accepted(rc, REL)
        rc.server.mode = "values"          # inits echo again ...
        rc.server.probe_status = 404       # ... while the probe still says off
        rc.write("Muster AG/GJ 2024/Neu.txt")
        result = rc.run(body)
        assert len(rc.server.probes) == 1
        assert result.doc_fields.requeued == 1, "the first staged echo queued it"
        assert rc.run(body).document_sync.fields_changed == 1
        assert rc.fields(REL).outcome == "staged"

    def test_no_probe_without_not_accepted_rows_or_with_the_kill_switch(self, rc):
        body = rc.body(rc.source(**MANDATE))
        rc.write(REL)
        rc.run(body)
        rc.run(body)
        assert rc.server.probes == [], "staged rows give nothing to ask about"
        rc.server.mode = "off"
        rc.write("Muster AG/GJ 2024/Neu.txt")
        rc.run(body)
        rc.env(RC_DOC_FIELDS="off")
        rc.server.probe_status = 200
        rc.run(body)
        assert rc.server.probes == [], "RC_DOC_FIELDS=off sends nothing, no probe either"

    def test_a_row_the_scan_does_not_reach_is_no_reason_to_probe(self, rc):
        body = rc.body(rc.source(**MANDATE))
        body["ingestion"]["delete_on_remove"] = False
        rc.write(REL)
        rc.server.mode = "off"
        rc.run(body)
        (rc.root / REL).unlink()
        rc.write("Beispiel GmbH/GJ 2024/Neu.txt")
        rc.server.probe_status = 200
        rc.run(body)
        assert rc.server.probes == []
        assert rc.fields(REL).outcome == "not_accepted" and rc.fields(REL).digest
```

`KnovasConnector/tests/conftest.py`. Replace:
```python
    """The scheduler keeps the last cycle's document-fields answers and the
    requeue scope in module memory; no test sees another test's."""
    import sys

    scheduler = sys.modules.get("sync.sync_scheduler")
    if scheduler is not None:
        for name, value in (("_last_doc_fields", None), ("_last_fields_changed", None),
                            ("_fields_requeued_since_scan", 0), ("_last_fields_answer", None),
                            ("_requeue_reachable", None)):
            monkeypatch.setattr(scheduler, name, value, raising=False)
    yield
```
with:
```python
    """The scheduler keeps the last cycle's document-fields answers and the
    requeue scope in module memory, the executor its last capability probe
    (spec F5); no test sees another test's."""
    import sys

    scheduler = sys.modules.get("sync.sync_scheduler")
    if scheduler is not None:
        for name, value in (("_last_doc_fields", None), ("_last_fields_changed", None),
                            ("_fields_requeued_since_scan", 0), ("_last_fields_answer", None),
                            ("_requeue_reachable", None)):
            monkeypatch.setattr(scheduler, name, value, raising=False)
    executor = sys.modules.get("sync.sync_executor")
    if executor is not None:
        monkeypatch.setattr(executor, "_last_doc_fields_probe", None, raising=False)
    yield
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_sync_executor_doc_fields.py::TestCapabilityProbe`
Expected: FAIL.
- `test_off_404_changes_nothing` and `test_on_…` fail with `AssertionError: assert [] == [('GET', None)]`: no probe is sent yet.
- The `unknown` cases fail on `len(rc.server.probes) == 1`.
- The two "no probe" tests already pass.

- [ ] **Step 3: Write minimal implementation**

`KnovasConnector/src/sync/sync_executor.py`. Replace:
```python
import fnmatch
import logging
import os
import re
from collections import Counter
```
with:
```python
import fnmatch
import logging
import os
import re
import threading
import time
from collections import Counter
```
Replace:
```python
    if sync_config.get("sequential_subfolders"):
        return 120
    return None


def run_sync_work(
```
with:
```python
    if sync_config.get("sequential_subfolders"):
        return 120
    return None


#: Spec F5: a cycle asks Knovas at most this often whether it takes fields.
DOC_FIELDS_PROBE_INTERVAL_SECONDS = 3600.0
#: ``time.monotonic()`` of the last probe. Module memory, like the
#: scheduler's last-cycle state: it survives the worker's cycle loop and the
#: one-time runs of POST /sync (the uploader is built anew for every cycle,
#: so it cannot keep it); a restart costs at most one extra GET.
_last_doc_fields_probe: Optional[float] = None
_doc_fields_probe_lock = threading.Lock()


def _claim_doc_fields_probe() -> bool:
    """True when no probe ran within DOC_FIELDS_PROBE_INTERVAL_SECONDS; the
    slot is then taken, whatever the probe will answer."""
    global _last_doc_fields_probe
    now = time.monotonic()
    with _doc_fields_probe_lock:
        last = _last_doc_fields_probe
        if last is not None and now - last < DOC_FIELDS_PROBE_INTERVAL_SECONDS:
            return False
        _last_doc_fields_probe = now
        return True


def _requeue_not_accepted(state: SyncStateStore, plan: _ScanPlan, stats: DocFieldsCycle) -> int:
    """Queue the ``not_accepted`` rows this cycle's scan reached for a fields
    re-upload: the next cycle finds them ``fields_changed`` and re-sends them
    within RC_FIELDS_REUPLOAD_PER_CYCLE. Returns how many."""
    count = state.requeue_fields(OUTCOME_NOT_ACCEPTED, plan.scanned_paths)
    stats.requeued += count
    return count


def _probe_doc_fields(
    uploader: Any, state: SyncStateStore, plan: _ScanPlan, stats: DocFieldsCycle
) -> bool:
    """Spec F5: re-send without a trigger upload.

    ``not_accepted`` rows come back after the first upload whose answer
    carries the fields echo -- and a cycle that uploads nothing new gets
    none. So when this cycle's scan reached such rows, at most once an hour,
    ask Knovas (``uploader.probe_doc_fields``: one GET, no body). On: the
    rows are requeued exactly like after a ``staged`` echo, and True tells
    the caller that this cycle's echo check is done. Off or unknown: nothing
    changes, and the echo trigger still works. Logs the answer class and a
    count only.
    """
    probe = getattr(uploader, "probe_doc_fields", None)
    if not callable(probe):
        return False
    if not state.count_fields_requeue_candidates(OUTCOME_NOT_ACCEPTED, plan.scanned_paths):
        return False
    if not _claim_doc_fields_probe():
        return False
    answer = probe()
    requeued = _requeue_not_accepted(state, plan, stats) if answer is True else 0
    label = "on" if answer is True else "off" if answer is False else "unknown"
    logger.info("doc_fields probe=%s requeued=%d", label, requeued)
    return answer is True


def run_sync_work(
```
Replace (in `run_sync_work`):
```python
        requeue_checked = False
```
with:
```python
        # Spec F5: after the scan, before the first upload. When the probe
        # requeued this cycle's not_accepted rows, the echo check below is done.
        requeue_checked = stats is not None and _probe_doc_fields(uploader, state, plan, stats)
```
Replace:
```python
                requeue_checked = True
                if state.count_fields_requeue_candidates(OUTCOME_NOT_ACCEPTED, plan.scanned_paths):
                    stats.requeued += state.requeue_fields(OUTCOME_NOT_ACCEPTED, plan.scanned_paths)
                    logger.info("doc_fields requeued=%d outcome=not_accepted", stats.requeued)
```
with:
```python
                requeue_checked = True
                if _requeue_not_accepted(state, plan, stats):
                    logger.info("doc_fields requeued=%d outcome=not_accepted", stats.requeued)
```

`KnovasConnector/docs/configuration.md`. Replace:
```
cycle's scan reached for a re-upload, within the bound. `POST /sync/doc-fields/requeue` does the same on
```
with:
```
cycle's scan reached for a re-upload, within the bound. A cycle that uploads
nothing new gets no echo, so at the start of a cycle whose scan reaches
`not_accepted` documents the Connector also asks Knovas, at most once an hour,
whether it takes fields (`GET /secured/graph/doc-fields`, no body, one try):
`404` means still off; a 5xx, a 429 or no answer means unknown (asked again an
hour later); any other answer means on, and those documents are queued exactly
as after an echo. `POST /sync/doc-fields/requeue` does the same on
```

`KnovasConnector/docs/operations.md`. Replace:
```
Documents recorded `not_accepted` are queued automatically the first time Knovas answers with an echo (those that cycle's scan reached).
```
with:
```
Documents recorded `not_accepted` are queued automatically the first time Knovas answers with an echo, or when the hourly capability probe finds the feature on (those that cycle's scan reached; log line `doc_fields probe=on|off|unknown requeued=n`; see [configuration.md](configuration.md#re-uploads-and-what-they-cost)).
```

`KnovasConnector/CHANGELOG.md`. Replace the F4 bullet RCF-2 wrote:
```
- **Upload warnings name their field** (F4): `GET /sync/status` → `doc_fields.warnings` is a list of `{code, key, count}` for the last cycle, the most frequent first, at most 50 (it was `{code: count}`; the `POST /sync` summary keeps that shape). Field keys only — never a value or the warning's JSON path; a "key" that is not key-shaped is reported as `""`. `rc_doc_fields_warnings_total{code}` counts `ambiguous_number` (Knovas 1.5.0) instead of `other`.
```
with the same bullet followed by:
```
- **Not accepted fields come back without a trigger upload** (F5): at the start of a cycle whose scan reaches documents recorded `not_accepted`, at most once an hour, the Connector asks `GET /secured/graph/doc-fields` (mTLS, no body, one try). `404`: still off, nothing changes. A 5xx, a 429 or no answer: unknown, asked again an hour later. Any other answer: those documents are requeued exactly as after a `staged` echo and re-sent within `RC_FIELDS_REUPLOAD_PER_CYCLE`. The echo trigger stays. Log line `doc_fields probe=on|off|unknown requeued=n`.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_sync_executor_doc_fields.py`
Expected: PASS. With the scripted server's default probe answer of 404, the existing tests run as before. The neighbouring suite `rc-pytest tests/contract/test_doc_fields_against_mock.py tests/test_doc_fields_no_values_in_logs.py tests/unit/test_sync_executor.py tests/unit/test_sync_dataloss_fixes.py tests/unit/test_m365_sync.py` still passes; `test_m365_sync::test_first_cycle_indexes_everything_and_keeps_nothing` is a known Windows-only failure.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/sync_executor.py KnovasConnector/tests/conftest.py \
  KnovasConnector/tests/unit/test_sync_executor_doc_fields.py KnovasConnector/docs/configuration.md \
  KnovasConnector/docs/operations.md KnovasConnector/CHANGELOG.md
git commit -F - <<'EOF'
rc: re-send not-accepted document fields without a trigger upload

Spec F5. Documents uploaded while Knovas ignored fields came back only
after some other upload got the fields echo, which a quiet folder never
produces. After the scan of a cycle that reached such documents, at most
once an hour, the executor now probes Knovas; "on" requeues them exactly
like the echo trigger (side queue, RC_FIELDS_REUPLOAD_PER_CYCLE), "off" or
"unknown" changes nothing and the echo trigger keeps working. The hourly
mark lives in module memory with a lock: the uploader is rebuilt every
cycle, and a restart costs one extra GET. One log line, class and count.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task RCF-6: Connector: carry keywords, categories and content status in `source_metadata` (L1, extraction side)

**Files:**
- Modify: `KnovasConnector/src/sync/metadata_fields.py` (new constants, `source_metadata_from`)
- Modify: `KnovasConnector/src/sync/document_text.py` (`ExtractedDocument` docstring)
- Test: `KnovasConnector/tests/unit/test_metadata_fields.py`, `KnovasConnector/tests/unit/test_document_text_source_metadata.py`

What changes and why:
- Today `source_metadata_from` passes `author`, `language`, `created`, `modified` and `eml:content_language` only, and caps nothing.
- The library crops every `Metadata.extra` string at `Limits.max_metadata_value_length` (4096). `_scalar_extra`'s 128-character limit applies to `ExtractedDocument.extra`, not to this.
- The new keys are copied from `extra` and **dropped, never cut,** above 4096 characters: a cut keyword list would end in half a word.

Verified with the library at `b5d4540`:
- `docx:keywords`, `docx:content_status` and `pdf:keywords` arrive as plain strings.
- `msg:categories` would arrive as the JSON array `sanitize_scalar` writes for a list. It is never set with extract-msg 0.56.1; see the intro.

**Interfaces:**
- Consumes: knovas-extract `Metadata.extra` keys `pdf:keywords`, `docx:keywords`, `msg:categories`, `docx:content_status`.
- Produces in `metadata_fields`:
  - `PDF_KEYWORDS`, `DOCX_KEYWORDS`, `MSG_CATEGORIES`, `DOCX_CONTENT_STATUS`
  - `FILE_PROPERTY_KEYS: tuple[str, ...]`
  - `MAX_SOURCE_VALUE_CHARS = 4096`
  - `ExtractedDocument.source_metadata` may now hold those four keys (stripped `str`, ≤ 4096 characters).

- [ ] **Step 1: Write the failing test**

Append to the `source_metadata_from` section of `KnovasConnector/tests/unit/test_metadata_fields.py`:
```python
def test_source_metadata_from_reads_the_file_properties():
    metadata = SimpleNamespace(
        author=None, language=None, created=None, modified=None,
        extra={"pdf:keywords": " Rechnung, Kreditor ", "docx:keywords": "Vertrag",
               "msg:categories": '["Projekt Alpha"]', "docx:content_status": "Final",
               "docx:category": "Intern", "pdf:subject": "Offerte", "docx:revision": "3"},
    )
    assert source_metadata_from(metadata) == {
        "pdf:keywords": "Rechnung, Kreditor",
        "docx:keywords": "Vertrag",
        "msg:categories": '["Projekt Alpha"]',
        "docx:content_status": "Final",
    }


def test_a_file_property_over_the_cap_is_left_out_not_cut():
    from sync.metadata_fields import MAX_SOURCE_VALUE_CHARS

    assert MAX_SOURCE_VALUE_CHARS == 4096
    metadata = SimpleNamespace(extra={
        "pdf:keywords": "k" * (MAX_SOURCE_VALUE_CHARS + 1),
        "docx:keywords": "k" * MAX_SOURCE_VALUE_CHARS,
        "docx:content_status": 3,
    })
    assert source_metadata_from(metadata) == {"docx:keywords": "k" * MAX_SOURCE_VALUE_CHARS}
```

`KnovasConnector/tests/unit/test_document_text_source_metadata.py`. Replace the module docstring:
```python
"""``ExtractedDocument.source_metadata`` (spec section 3.5).

The extractor's author, language, created and modified (plus the .eml
Content-Language header) travel with the extracted document, through the
forked extraction child and its result queue, to the metadata mapping.
"""
```
with:
```python
"""``ExtractedDocument.source_metadata`` (spec section 3.5, L1).

The extractor's author, language, created and modified (plus the .eml
Content-Language header and the keyword and status file properties) travel
with the extracted document, through the forked extraction child and its
result queue, to the metadata mapping.
"""
```
Append after `test_pdf_metadata_is_carried`:
```python
def test_docx_keywords_and_content_status_are_carried(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Mietvertrag mit Beispiel GmbH.")
    document.core_properties.keywords = "Vertrag, Miete"
    document.core_properties.content_status = "Final"
    buf = io.BytesIO()
    document.save(buf)
    path = tmp_path / "mietvertrag.docx"
    path.write_bytes(buf.getvalue())
    md = extract_document(path).source_metadata
    assert md["docx:keywords"] == "Vertrag, Miete"
    assert md["docx:content_status"] == "Final"


def test_pdf_keywords_are_carried(tmp_path):
    fitz = pytest.importorskip("fitz")
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Rechnung der Muster AG.")
    pdf.set_metadata({"keywords": "Rechnung; Kreditor"})
    path = tmp_path / "rechnung.pdf"
    pdf.save(path)
    assert extract_document(path).source_metadata["pdf:keywords"] == "Rechnung; Kreditor"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_metadata_fields.py::test_source_metadata_from_reads_the_file_properties tests/unit/test_metadata_fields.py::test_a_file_property_over_the_cap_is_left_out_not_cut tests/unit/test_document_text_source_metadata.py::test_docx_keywords_and_content_status_are_carried tests/unit/test_document_text_source_metadata.py::test_pdf_keywords_are_carried`
Expected: FAIL. `AssertionError: assert {} == {...}`, `ImportError: cannot import name 'MAX_SOURCE_VALUE_CHARS'`, and `KeyError: 'docx:keywords'` / `KeyError: 'pdf:keywords'`.

- [ ] **Step 3: Write minimal implementation**

`KnovasConnector/src/sync/metadata_fields.py`. Replace:
```python
#: Keys of ``ExtractedDocument.source_metadata`` (knovas-extract ``Metadata``
#: attributes, plus the .eml Content-Language header from ``extra``).
SOURCE_METADATA_ATTRIBUTES = ("author", "language", "created", "modified")
EML_CONTENT_LANGUAGE = "eml:content_language"
```
with:
```python
#: Keys of ``ExtractedDocument.source_metadata`` (knovas-extract ``Metadata``
#: attributes, plus the .eml Content-Language header and the file properties
#: below from ``extra``).
SOURCE_METADATA_ATTRIBUTES = ("author", "language", "created", "modified")
EML_CONTENT_LANGUAGE = "eml:content_language"
#: File properties for the ``keywords`` and ``document_status`` items
#: (knovas-extract ``Metadata.extra`` keys). ``msg:categories`` is a list the
#: library writes as a JSON array.
PDF_KEYWORDS = "pdf:keywords"
DOCX_KEYWORDS = "docx:keywords"
MSG_CATEGORIES = "msg:categories"
DOCX_CONTENT_STATUS = "docx:content_status"
FILE_PROPERTY_KEYS = (PDF_KEYWORDS, DOCX_KEYWORDS, MSG_CATEGORIES, DOCX_CONTENT_STATUS)
#: knovas-extract's ``Limits.max_metadata_value_length``. A longer ``extra``
#: value is left out, never cut: a cut keyword list ends in half a word.
MAX_SOURCE_VALUE_CHARS = 4096
```
Replace:
```python
    Reads ``author``, ``language``, ``created`` and ``modified`` from a
    knovas-extract ``Metadata`` and ``extra["eml:content_language"]``.
    Missing, non-string and blank values are left out; values are stripped
    but otherwise kept verbatim.
    """
```
with:
```python
    Reads ``author``, ``language``, ``created`` and ``modified`` from a
    knovas-extract ``Metadata``, and ``eml:content_language`` and the file
    properties (``FILE_PROPERTY_KEYS``) from its ``extra``. Missing,
    non-string and blank values are left out, and so is an ``extra`` value
    longer than ``MAX_SOURCE_VALUE_CHARS``; values are stripped but
    otherwise kept verbatim.
    """
```
Replace:
```python
    extra = getattr(metadata, "extra", None)
    if isinstance(extra, dict):
        value = extra.get(EML_CONTENT_LANGUAGE)
        if isinstance(value, str) and value.strip():
            out[EML_CONTENT_LANGUAGE] = value.strip()
    return out
```
with:
```python
    extra = getattr(metadata, "extra", None)
    if isinstance(extra, dict):
        for key in (EML_CONTENT_LANGUAGE, *FILE_PROPERTY_KEYS):
            value = extra.get(key)
            if isinstance(value, str) and value.strip() and len(value) <= MAX_SOURCE_VALUE_CHARS:
                out[key] = value.strip()
    return out
```

`KnovasConnector/src/sync/document_text.py`. Replace:
```python
    `source_metadata` holds the extractor's `author`, `language`, `created`
    and `modified` plus the .eml `eml:content_language` header, as strings
    (`sync.metadata_fields.source_metadata_from`). Only the opted-in
```
with:
```python
    `source_metadata` holds the extractor's `author`, `language`, `created`
    and `modified`, the .eml `eml:content_language` header and the file
    properties `pdf:keywords`, `docx:keywords`, `msg:categories` and
    `docx:content_status`, as strings of at most 4096 characters
    (`sync.metadata_fields.source_metadata_from`). Only the opted-in
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_metadata_fields.py tests/unit/test_document_text_source_metadata.py`
Expected: PASS. The neighbouring suite `rc-pytest tests/unit/test_doc_fields_payload.py tests/unit/test_document_text.py` still passes, and `test_new_modules_are_ascii_and_import_no_http_client` stays green.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/metadata_fields.py KnovasConnector/src/sync/document_text.py \
  KnovasConnector/tests/unit/test_metadata_fields.py KnovasConnector/tests/unit/test_document_text_source_metadata.py
git commit -F - <<'EOF'
rc: carry keywords, Outlook categories and Word content status from the extractor

Spec L1, extraction side. knovas-extract already reads pdf:keywords,
docx:keywords, docx:content_status and msg:categories into
Metadata.extra; source_metadata now carries them to the metadata mapping,
stripped and dropped (never cut) above the library's 4096-character
metadata cap. Nothing maps them yet; nothing is logged.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task RCF-7: Connector: opt-ins `keywords` and `document_status`, sync schema, capability `metadata_fields_v2` (L1)

`METADATA_ITEMS` and the schema enum have to change in the same commit, because `test_doc_fields_payload.py::test_library_limits_match_the_sync_request_schema` requires them to be equal. The Platform's schema copy must also stay byte-identical, which `tests/test_sync_source_fields_schema.py::TestPlatformCopy` and the Platform's `test_bundled_schemas_match_the_checkout_when_present` check. That makes this an `rc+platform` commit.

**The digest needs no change.** `config_digest` already hashes `sorted(spec.metadata_fields)`, and `spec_from_source` keeps only items in `METADATA_ITEMS`. Run against `$MERGED`, adding `keywords` today leaves the digest unchanged (`9d8c9593…1d9c` both times), because the unknown item is filtered out. Once the item is listed, enabling it changes the digest, so the folder is re-sent within the bound. The Platform's confirmation is already in place, since `SourceFolder.field_config()` includes the metadata items. `METADATA_MAPPING_VERSION` stays `1`, pinned below.

**The mock needs no change.** Its core registry has `keywords` (text, several) and `status` (choice list with labels Final/Entwurf/…). The mock test added here pins that behaviour and passes as soon as it is written.

**`contracts/vectors`.** A new `contracts/vectors/metadata_fields.json` holds golden vectors for the mapping. The Platform implements no metadata mapping, so it gets no copy; `test_rc_contract_copies.py` copies only what the Platform executes, the template vectors. The file sits in `vectors/`, outside CI's `contracts/*.json` schema glob.

**Interfaces:**
- Consumes: `source_metadata` keys and constants from RCF-6; `DocFieldsCycle.warning_pairs` (RCF-2, in the mock test).
- Produces:
  - `METADATA_ITEMS = ("language", "email_date", "email_doc_type", "email_author", "document_author", "keywords", "document_status")`
  - `ITEM_TARGETS["keywords"] = "keywords"`, `ITEM_TARGETS["document_status"] = "status"`
  - `KEYWORD_SOURCES = {".pdf": "pdf:keywords", ".docx": "docx:keywords", ".msg": "msg:categories"}`
  - `MAX_KEYWORDS = 32`, `MAX_KEYWORD_CHARS = 256`
  - `map_metadata` yields `keywords` as `list[str]` and `status` as `str`
  - The sync-request schema enum lists both items; `RC_CAPABILITIES` gains `"metadata_fields_v2"` as its last entry.

- [ ] **Step 1: Write the failing test**

Create `KnovasConnector/contracts/vectors/metadata_fields.json`:
```json
[
  {
    "name": "pdf keywords: split on comma and semicolon, trimmed, the first spelling kept",
    "ext": ".pdf",
    "items": ["keywords"],
    "source_metadata": {"pdf:keywords": " Rechnung, Kreditor;rechnung ;; 2024 "},
    "fields": {"keywords": ["Rechnung", "Kreditor", "2024"]}
  },
  {
    "name": "docx keywords",
    "ext": ".docx",
    "items": ["keywords"],
    "source_metadata": {"docx:keywords": "Vertrag; Miete"},
    "fields": {"keywords": ["Vertrag", "Miete"]}
  },
  {
    "name": "keywords are NFC and de-duplicated ignoring case",
    "ext": ".docx",
    "items": ["keywords"],
    "source_metadata": {"docx:keywords": "Müller, Müller; MÜLLER, Straße, STRASSE"},
    "fields": {"keywords": ["Müller", "Straße"]}
  },
  {
    "name": "msg categories as the JSON array knovas-extract writes: items kept whole",
    "ext": ".msg",
    "items": ["keywords"],
    "source_metadata": {"msg:categories": "[\"Projekt Alpha\", \"Kunde; Muster AG\", \"projekt alpha\"]"},
    "fields": {"keywords": ["Projekt Alpha", "Kunde; Muster AG"]}
  },
  {
    "name": "msg categories as plain text are split",
    "ext": ".msg",
    "items": ["keywords"],
    "source_metadata": {"msg:categories": "Projekt Alpha; Kunde"},
    "fields": {"keywords": ["Projekt Alpha", "Kunde"]}
  },
  {
    "name": "separators and blanks only: no keywords",
    "ext": ".pdf",
    "items": ["keywords"],
    "source_metadata": {"pdf:keywords": " , ;; , "},
    "fields": {}
  },
  {
    "name": "each format reads its own keywords only",
    "ext": ".pdf",
    "items": ["keywords"],
    "source_metadata": {"docx:keywords": "Vertrag", "msg:categories": "Kunde"},
    "fields": {}
  },
  {
    "name": "no keywords for .eml, .md or .txt",
    "ext": ".eml",
    "items": ["keywords"],
    "source_metadata": {"pdf:keywords": "Rechnung", "docx:keywords": "Vertrag"},
    "fields": {}
  },
  {
    "name": "word content status is sent as written",
    "ext": ".docx",
    "items": ["document_status"],
    "source_metadata": {"docx:content_status": "Final"},
    "fields": {"status": "Final"}
  },
  {
    "name": "status is trimmed, never translated",
    "ext": ".docx",
    "items": ["document_status"],
    "source_metadata": {"docx:content_status": "  In Review "},
    "fields": {"status": "In Review"}
  },
  {
    "name": "a blank status yields nothing",
    "ext": ".docx",
    "items": ["document_status"],
    "source_metadata": {"docx:content_status": "   "},
    "fields": {}
  },
  {
    "name": "status only from Word files",
    "ext": ".pdf",
    "items": ["document_status"],
    "source_metadata": {"docx:content_status": "Final"},
    "fields": {}
  },
  {
    "name": "nothing without the opt-in",
    "ext": ".docx",
    "items": ["language", "document_author"],
    "source_metadata": {"docx:keywords": "Vertrag", "docx:content_status": "Final"},
    "fields": {}
  },
  {
    "name": "both items next to an existing one",
    "ext": ".docx",
    "items": ["document_author", "keywords", "document_status"],
    "source_metadata": {"author": "Beispiel GmbH", "docx:keywords": "Vertrag", "docx:content_status": "Entwurf"},
    "fields": {"author": "Beispiel GmbH", "keywords": ["Vertrag"], "status": "Entwurf"}
  }
]
```

`KnovasConnector/tests/unit/test_metadata_fields.py`. Replace:
```python
import inspect
import io
import os
```
with:
```python
import inspect
import io
import json
import os
```
Replace:
```python
from types import SimpleNamespace

import pytest
```
with:
```python
from pathlib import Path
from types import SimpleNamespace

import pytest
```
Replace:
```python
def test_mapping_version_and_items():
    assert METADATA_MAPPING_VERSION == 1
    assert set(METADATA_ITEMS) == {
        "language", "email_date", "email_doc_type", "email_author", "document_author",
    }
    assert set(ITEM_TARGETS) == set(METADATA_ITEMS)
    # No Message-ID, sender or recipient mapping (spec section 3.5).
    assert set(ITEM_TARGETS.values()) == {"language", "document_date", "doc_type", "author"}
```
with:
```python
VECTORS = Path(__file__).resolve().parents[2] / "contracts" / "vectors" / "metadata_fields.json"


def test_mapping_version_and_items():
    # New items need no bump: a bump re-sends every source that uses file properties.
    assert METADATA_MAPPING_VERSION == 1
    assert METADATA_ITEMS == (
        "language", "email_date", "email_doc_type", "email_author", "document_author",
        "keywords", "document_status",
    )
    assert set(ITEM_TARGETS) == set(METADATA_ITEMS)
    # No Message-ID, sender or recipient mapping (spec section 3.5).
    assert set(ITEM_TARGETS.values()) == {
        "language", "document_date", "doc_type", "author", "keywords", "status"}
```
Append before the `# --- source_metadata_from` section:
```python
# --- keywords and document_status (spec L1) ----------------------------------------------


def test_golden_vectors():
    cases = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert len(cases) >= 12
    assert {"keywords", "document_status"} <= {item for case in cases for item in case["items"]}
    assert set(FILE_PROPERTY_KEYS) <= {key for case in cases for key in case["source_metadata"]}
    failed = [case["name"] for case in cases
              if map_metadata(case["source_metadata"], case["ext"], case["items"]) != case["fields"]]
    assert failed == []


def test_keywords_are_capped_at_32_values_in_order():
    raw = ", ".join(f"Stichwort {i}" for i in range(40))
    assert map_metadata({"docx:keywords": raw}, ".docx", {"keywords"}) == {
        "keywords": [f"Stichwort {i}" for i in range(32)]}


def test_a_keyword_over_256_characters_is_skipped_not_cut():
    raw = ";".join(["a" * 257, "b" * 256, "c"])
    assert map_metadata({"pdf:keywords": raw}, ".pdf", {"keywords"}) == {"keywords": ["b" * 256, "c"]}


def test_skipped_keywords_do_not_use_up_the_cap():
    raw = ", ".join([" "] * 10 + ["x" * 300] * 10 + [f"k{i}" for i in range(40)])
    assert map_metadata({"pdf:keywords": raw}, ".pdf", {"keywords"})["keywords"] == [
        f"k{i}" for i in range(32)]


def test_status_final_is_sent_as_written_and_empty_values_are_skipped():
    assert map_metadata({"docx:content_status": "Final"}, ".docx", {"document_status"}) == {
        "status": "Final"}
    assert map_metadata({"docx:content_status": "  "}, ".docx", {"document_status"}) == {}
    assert map_metadata({"docx:keywords": " ; , "}, ".docx", {"keywords"}) == {}


def test_word_keywords_and_status_end_to_end(tmp_path):
    docx = pytest.importorskip("docx")
    from sync.document_text import extract_document

    document = docx.Document()
    document.add_paragraph("Mietvertrag mit Beispiel GmbH.")
    document.core_properties.keywords = "Vertrag, Miete; vertrag"
    document.core_properties.content_status = "Final"
    buf = io.BytesIO()
    document.save(buf)
    path = tmp_path / "mietvertrag.docx"
    path.write_bytes(buf.getvalue())
    md = extract_document(path).source_metadata
    assert map_metadata(md, ".docx", {"keywords", "document_status"}) == {
        "keywords": ["Vertrag", "Miete"], "status": "Final"}
```
and add `FILE_PROPERTY_KEYS` to the module import at the top:
```python
from sync.metadata_fields import (
    EMAIL_DOC_TYPE,
    FILE_PROPERTY_KEYS,
    ITEM_TARGETS,
```
(The rest of the import list is unchanged. `FILE_PROPERTY_KEYS` exists since RCF-6, so the module still imports.)

`KnovasConnector/tests/unit/test_doc_fields_payload.py`. Append after `test_digest_is_non_empty_for_a_metadata_source_even_when_the_payload_is_empty`:
```python
def test_a_new_file_property_item_changes_the_digest_and_the_old_items_keep_theirs():
    """Spec L1: enabling keywords or document_status re-sends the source
    within the bound. METADATA_MAPPING_VERSION stays, so a source with the
    old items only keeps its digest (a bump would re-send every one)."""
    rel = "a/b.pdf"
    base = _spec(metadata_fields=["language"])
    assert config_digest(rel, base) == "9d8c959313ac1d672a40f5ecebb11a7f4a40d639c7a8287ccf0a5bfe26381d9c"
    digests = {config_digest(rel, _spec(metadata_fields=items))
               for items in (["language"], ["language", "keywords"], ["language", "document_status"])}
    assert len(digests) == 3


def test_file_properties_stay_below_templates_and_fixed_values():
    md = {"docx:keywords": "Vertrag, Miete", "docx:content_status": "Final"}
    spec = _spec(fields={"keywords": ["Akte"]}, metadata_fields=["keywords", "document_status"])
    assert assemble("a/Vertrag.docx", spec, md, ".docx").values == {
        "keywords": ["Akte"], "status": "Final"}
    only_properties = _spec(metadata_fields=["keywords", "document_status"])
    assert assemble("a/Vertrag.docx", only_properties, md, ".docx").values == {
        "keywords": ["Vertrag", "Miete"], "status": "Final"}
```

`KnovasConnector/tests/test_sync_source_fields_schema.py`. Replace:
```python
METADATA_ITEMS = ("language", "email_date", "email_doc_type", "email_author", "document_author")
```
with:
```python
METADATA_ITEMS = ("language", "email_date", "email_doc_type", "email_author", "document_author",
                  "keywords", "document_status")
```
Replace:
```python
    @pytest.mark.parametrize("item", ["email_message_id", "sender", "recipients", "created", "LANGUAGE"])
```
with:
```python
    @pytest.mark.parametrize("item", ["email_message_id", "sender", "recipients", "created", "LANGUAGE",
                                      "status", "categories"])
```

`KnovasConnector/tests/contract/test_doc_fields_against_mock.py`. Replace:
```python
import os
from pathlib import Path
```
with:
```python
import os
from collections import Counter
from pathlib import Path
```
Replace:
```python
        assert status["capabilities"] == ["source_fields_v1", "field_templates_v1",
                                          "metadata_fields_v1", "fields_requeue_v1"]
```
with the list extended by `"metadata_fields_v2"` as its last entry. If an earlier task already appended a capability, keep it and put `"metadata_fields_v2"` after it, in the same order as `RC_CAPABILITIES`:
```python
        assert status["capabilities"] == ["source_fields_v1", "field_templates_v1",
                                          "metadata_fields_v1", "fields_requeue_v1",
                                          "metadata_fields_v2"]
```
Insert before `def _sync_response(result) -> dict:`:
```python
def _word(path: Path, *, keywords: str = "", status: str = "") -> Path:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Vertrag mit Beispiel GmbH.")
    document.core_properties.keywords = keywords
    document.core_properties.content_status = status
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    os.utime(path, (FIXED_MTIME, FIXED_MTIME))
    return path


def test_word_keywords_and_status_reach_the_server(rig):
    """L1 against the mock: keywords are staged as a list; Word's status
    "Final" matches the status choice ``final``; one Knovas does not know is
    dropped with an invalid_value warning counted under its key (F4)."""
    rig.mock(doc_fields="values")
    _word(rig.root / "Vertraege" / "final.docx", keywords="Vertrag, Miete; vertrag", status="Final")
    _word(rig.root / "Vertraege" / "review.docx", status="In Review")
    body = rig.body(metadata_fields=["keywords", "document_status"])
    body["filters"]["include_globs"] = ["*.docx"]
    result = rig.run(body)
    assert result.files_uploaded == 2 and result.errors == []
    sent = {r["json"]["path"]: r["json"].get("fields") for r in rig.init_requests()}
    assert sent == {"Vertraege/final.docx": {"keywords": ["Vertrag", "Miete"], "status": "Final"},
                    "Vertraege/review.docx": {"status": "In Review"}}
    final = rig.state.anchors["rc-sync/Vertraege/final.docx"]["upload"]
    assert final["status"]["values"] == ["final"]
    assert final["keywords"]["values"] == ["Vertrag", "Miete"]
    assert "status" not in rig.state.anchors["rc-sync/Vertraege/review.docx"]["upload"]
    assert result.doc_fields.warning_pairs == Counter({("invalid_value", "status"): 1})
```

`KnovasPlatform/mock_knovas_api/tests/test_mock_doc_fields.py`. Append to class `TestValuesInit`:
```python
    def test_file_property_values(self):
        """What the Knovas Connector's file-property opt-ins send (L1): Word's
        content status as written is matched against the status choices'
        labels; one Knovas does not know is dropped with an invalid_value
        warning naming the key, never the value; keywords are text values."""
        mock = Mock(doc_fields="values")
        status, body = mock.init(fields={"status": "Final", "keywords": ["Vertrag", "Miete"]})
        assert status == 201
        assert body["fields"]["staged"] == 2 and body["fields"]["warnings"] == []
        assert mock.values()[1]["fields"] == {"keywords": ["Vertrag", "Miete"], "status": "final"}
        other = "rc-sync/Vertraege/review.docx"
        status, body = mock.init(pointer=other, fields={"status": "In Review"})
        assert status == 201 and body["fields"]["staged"] == 0
        assert body["fields"]["warnings"] == [
            {"key": "status", "path": "fields.status", "code": "invalid_value"}]
        assert "status" not in mock.values(other)[1]["fields"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_metadata_fields.py tests/unit/test_doc_fields_payload.py tests/test_sync_source_fields_schema.py tests/contract/test_doc_fields_against_mock.py`
Expected: FAIL.
- `test_mapping_version_and_items` (`AssertionError`, five items)
- `test_golden_vectors` (cases fail: no `keywords`/`status` mapped)
- the cap/skip/status tests (`{} != {...}`)
- the digest test (`assert 1 == 3`)
- `TestRequestAcceptsFields::test_templates_and_metadata_items` (schema refuses `keywords`)
- the capability list and `test_word_keywords_and_status_reach_the_server` (no `fields` sent for the documents)

Run: `mock-pytest tests/test_mock_doc_fields.py::TestValuesInit::test_file_property_values`
Expected: PASS already. This test pins existing mock behaviour; the mock needs no change.

- [ ] **Step 3: Write minimal implementation**

`KnovasConnector/src/sync/metadata_fields.py`. Replace:
```python
    document_author  -> author         pdf/docx/md author, junk skipped
```
with:
```python
    document_author  -> author         pdf/docx/md author, junk skipped
    keywords         -> keywords       pdf/docx keywords, .msg categories:
                                       split on , and ;, trimmed, NFC,
                                       de-duplicated ignoring case, at most
                                       32 values of at most 256 characters
    document_status  -> status         .docx content status, trimmed and
                                       sent as written: Knovas matches it
                                       against the status choices and drops
                                       one it does not know (invalid_value)
```
Replace:
```python
import re
from email.utils import getaddresses
```
with:
```python
import json
import re
import unicodedata
from email.utils import getaddresses
```
Replace:
```python
METADATA_ITEMS = ("language", "email_date", "email_doc_type", "email_author", "document_author")

#: The registry key each item writes.
ITEM_TARGETS = {
    "language": "language",
    "email_date": "document_date",
    "email_doc_type": "doc_type",
    "email_author": "author",
    "document_author": "author",
}
```
with:
```python
METADATA_ITEMS = (
    "language", "email_date", "email_doc_type", "email_author", "document_author",
    "keywords", "document_status",
)

#: The registry key each item writes.
ITEM_TARGETS = {
    "language": "language",
    "email_date": "document_date",
    "email_doc_type": "doc_type",
    "email_author": "author",
    "document_author": "author",
    "keywords": "keywords",
    "document_status": "status",
}
```
Replace:
```python
MAX_SOURCE_VALUE_CHARS = 4096
```
with:
```python
MAX_SOURCE_VALUE_CHARS = 4096
#: The file property each format keeps its keywords in.
KEYWORD_SOURCES = {".pdf": PDF_KEYWORDS, ".docx": DOCX_KEYWORDS, ".msg": MSG_CATEGORIES}
#: The Knovas 1.5.0 limits of a field that holds several values.
MAX_KEYWORDS = 32
MAX_KEYWORD_CHARS = 256
_KEYWORD_SEPARATORS = re.compile(r"[,;]")
```
Replace:
```python
def _document_author(raw: Optional[str]) -> Optional[str]:
    if raw is None or raw.casefold() in JUNK_AUTHORS:
        return None
    return raw
```
with:
```python
def _document_author(raw: Optional[str]) -> Optional[str]:
    if raw is None or raw.casefold() in JUNK_AUTHORS:
        return None
    return raw


def _keyword_items(raw: str) -> list[str]:
    """The items of a keywords value: a JSON array of strings (how
    knovas-extract writes a list into ``extra``, e.g. Outlook categories)
    gives its items whole; any other text is split on ``,`` and ``;``."""
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, str)]
    return _KEYWORD_SEPARATORS.split(raw)


def _keywords(raw: Optional[str]) -> list[str]:
    """Trimmed, NFC, de-duplicated ignoring case (the first spelling stays),
    at most MAX_KEYWORDS values of at most MAX_KEYWORD_CHARS characters. An
    empty or over-long item is skipped, never cut, and does not count."""
    if raw is None:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in _keyword_items(raw):
        value = unicodedata.normalize("NFC", item.strip())
        if not value or len(value) > MAX_KEYWORD_CHARS:
            continue
        folded = value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        out.append(value)
        if len(out) == MAX_KEYWORDS:
            break
    return out
```
Replace:
```python
    if document and "document_author" in items:
        author = _document_author(_text(md, "author"))
        if author is not None:
            out[ITEM_TARGETS["document_author"]] = author
    return out
```
with:
```python
    if document and "document_author" in items:
        author = _document_author(_text(md, "author"))
        if author is not None:
            out[ITEM_TARGETS["document_author"]] = author
    if "keywords" in items:
        keywords = _keywords(_text(md, KEYWORD_SOURCES.get(ext, "")))
        if keywords:
            out[ITEM_TARGETS["keywords"]] = keywords
    if ext == ".docx" and "document_status" in items:
        status = _text(md, DOCX_CONTENT_STATUS)
        if status is not None:
            out[ITEM_TARGETS["document_status"]] = status
    return out
```

`KnovasConnector/contracts/sync_request.schema.json`. Replace:
```json
            "items": { "enum": ["language", "email_date", "email_doc_type", "email_author", "document_author"] },
            "description": "Extractor metadata items mapped into upload fields for this source, each one opted into by the administrator. Mapped keys are never registered automatically."
```
with:
```json
            "items": { "enum": ["language", "email_date", "email_doc_type", "email_author", "document_author", "keywords", "document_status"] },
            "description": "Extractor metadata items mapped into upload fields for this source, each one opted into by the administrator. Mapped keys are never registered automatically. keywords and document_status need the capability metadata_fields_v2. Golden vectors: contracts/vectors/metadata_fields.json."
```
Then make the Platform copy byte-identical:
```bash
cp $WT/KnovasConnector/contracts/sync_request.schema.json \
   $WT/KnovasPlatform/components/docbridge_integration/src/identity/rc_contracts/sync_request.schema.json
```

`KnovasConnector/src/sync/sync_scheduler.py`. Replace:
```python
    "metadata_fields_v1",
    "fields_requeue_v1",
)
```
with the following. If an earlier task appended an entry after `fields_requeue_v1`, keep it and add the new line after it, as the tuple's last entry:
```python
    "metadata_fields_v1",
    "fields_requeue_v1",
    # The file-property items ``keywords`` and ``document_status`` (spec L1).
    "metadata_fields_v2",
)
```

`KnovasConnector/docs/configuration.md`. Replace:
```
| `document_author` | `author` | `.pdf` / `.docx` author; placeholder authors (`Administrator`, `User`, `Microsoft Office User`, …) are skipped. |
```
with:
```
| `document_author` | `author` | `.pdf` / `.docx` author; placeholder authors (`Administrator`, `User`, `Microsoft Office User`, …) are skipped. |
| `keywords` | `keywords` | `.pdf` / `.docx` keywords, `.msg` categories: split on `,` and `;`, trimmed, Unicode NFC, duplicates ignoring case dropped (the first spelling stays), at most 32 values of at most 256 characters. knovas-extract 0.4.0a1 does not read Outlook categories yet (extract-msg 0.56 has none on a message), so `.msg` files give none until it does. |
| `document_status` | `status` | `.docx` content status (*Dokumentstatus*), trimmed and sent as written. Knovas matches it against the labels and other names of the `status` choices; one it does not know is dropped with an `invalid_value` warning (counted per key in `/sync/status`). |
```
Replace:
```
`.md` and `.txt` files never yield `author` or `language`: they are extracted
as plain text, which has no document properties. A file's modification time,
the Microsoft 365 `lastModifiedDateTime` and PDF/DOCX created/modified dates
never become `document_date`, and no Message-ID or sender/recipient key is
mapped.
```
with:
```
`.md` and `.txt` files never yield `author`, `language`, `keywords` or `status`:
they are extracted as plain text, which has no document properties. A file's
modification time, the Microsoft 365 `lastModifiedDateTime` and PDF/DOCX
created/modified dates never become `document_date`, and no Message-ID or
sender/recipient key is mapped. `keywords` and `document_status` need a
Platform and a Connector that know them (capability `metadata_fields_v2`);
golden vectors: [contracts/vectors/metadata_fields.json](../contracts/vectors/metadata_fields.json).
```

`KnovasConnector/docs/operations.md`. Replace:
```
"capabilities": ["source_fields_v1", "field_templates_v1", "metadata_fields_v1", "fields_requeue_v1"],
```
with:
```
"capabilities": ["source_fields_v1", "field_templates_v1", "metadata_fields_v1", "fields_requeue_v1", "metadata_fields_v2"],
```
Replace:
```
- `enabled` is `RC_DOC_FIELDS`.
```
with:
```
- `metadata_fields_v2`: the sync body may carry the file-property items `keywords` and `document_status`; the Platform sends them only to a Connector that reports it.
- `enabled` is `RC_DOC_FIELDS`.
```

`KnovasConnector/CHANGELOG.md`. After the F5 bullet that RCF-5 added (it begins `- **Not accepted fields come back without a trigger upload** (F5):`), add:
```
- **File properties `keywords` and `document_status`** (L1; capability `metadata_fields_v2`): `keywords` → field `keywords` from `pdf:keywords`, `docx:keywords` and `msg:categories` (split on `,` and `;`, trimmed, NFC, de-duplicated ignoring case, at most 32 values of at most 256 characters); `document_status` → field `status` from `docx:content_status`, trimmed and sent as written (Knovas drops a status it does not know with an `invalid_value` warning). Below path templates and fixed values, as every file property. Enabling one changes the documents' config digest, so the folder is re-sent within the bound; `METADATA_MAPPING_VERSION` stays 1. Golden vectors: `contracts/vectors/metadata_fields.json`. knovas-extract 0.4.0a1 does not read Outlook categories yet.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_metadata_fields.py tests/unit/test_doc_fields_payload.py tests/test_sync_source_fields_schema.py tests/contract/test_doc_fields_against_mock.py`
Expected: PASS.
Run: `pf-pytest tests/test_ingestion_compiler.py::TestContractsAreAvailableWithoutAMonorepoCheckout tests/test_rc_contract_copies.py`
Expected: PASS (the copy is byte-identical).
Run: `mock-pytest tests/test_mock_doc_fields.py`
Expected: PASS.
The neighbouring suite `rc-pytest tests/integration/test_sync_routes.py tests/unit/test_sync_executor_doc_fields.py tests/test_doc_fields_no_values_in_logs.py` still passes.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/metadata_fields.py KnovasConnector/src/sync/sync_scheduler.py \
  KnovasConnector/contracts/sync_request.schema.json KnovasConnector/contracts/vectors/metadata_fields.json \
  KnovasPlatform/components/docbridge_integration/src/identity/rc_contracts/sync_request.schema.json \
  KnovasConnector/docs/configuration.md KnovasConnector/docs/operations.md KnovasConnector/CHANGELOG.md \
  KnovasConnector/tests/unit/test_metadata_fields.py KnovasConnector/tests/unit/test_doc_fields_payload.py \
  KnovasConnector/tests/test_sync_source_fields_schema.py \
  KnovasConnector/tests/contract/test_doc_fields_against_mock.py \
  KnovasPlatform/mock_knovas_api/tests/test_mock_doc_fields.py
git commit -F - <<'EOF'
rc+platform: file-property opt-ins keywords and document_status

Spec L1. Two new per-folder opt-ins in the Connector's upload layer, below
templates and fixed values: keywords from PDF/Word keywords and Outlook
categories (split on , and ;, trimmed, NFC, de-duplicated ignoring case,
at most 32 values of at most 256 characters) and status from Word's content
status, sent as written so Knovas matches it against the status choices.
The sync schema (and the Platform's byte copy) accept them; a Connector
that does advertises metadata_fields_v2, so the Platform never sends them
to one whose schema would answer 400. The config digest already covers the
items, so enabling one re-sends the folder within the bound; the mapping
version stays 1. Golden vectors in contracts/vectors/metadata_fields.json;
the mock already plays both, now pinned.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task RCF-8: Platform: offer the two opt-ins in the Ingestion tab, compile and gate them (L1)

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/identity/ingestion_compiler.py` (`METADATA_ITEMS`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/doc_fields_view.py` (`METADATA_TARGETS`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/admin_ingestion.py` (module docstring, `METADATA_LABELS`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_connector_client.py` (`CAP_METADATA_FIELDS_V2`, `METADATA_ITEMS_V2`, `required_capabilities`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/admin_system.py` (`RC_DOC_FIELD_CAPABILITIES`)
- Modify: `KnovasPlatform/docs/features/document-fields.md`, `RELEASE_NOTES.md`
- Test: `tests/test_ingestion_compiler.py`, `tests/test_web_admin_ingestion.py`, `tests/test_doc_fields_view.py`, `tests/test_web_admin_system_doc_fields.py` (under `KnovasPlatform/components/docbridge_integration/`)

The checkboxes are rendered server-side from `metadata_items` (`admin_ingestion.py`, `{"key": k, "label": METADATA_LABELS[k]} for k in METADATA_ITEMS`), for the folder rows and for the `__n__` row template alike. `templates/admin_ingestion.html` and `static/js/admin_ingestion.js` need no change: the JS clones the server-rendered row.

**Interfaces:**
- Consumes: the schema copy and the Connector capability `metadata_fields_v2` (RCF-7).
- Produces:
  - `ingestion_compiler.METADATA_ITEMS` (7 items, form order)
  - `doc_fields_view.METADATA_TARGETS["keywords"] = "keywords"`, `["document_status"] = "status"`
  - `admin_ingestion.METADATA_LABELS["keywords"] = "Stichwörter aus Datei-Eigenschaften (PDF/Word-Stichwörter, Outlook-Kategorien)"`, `["document_status"] = "Status aus Word-Dokumentstatus"`
  - `knovas_connector_client.CAP_METADATA_FIELDS_V2 = "metadata_fields_v2"`, `METADATA_ITEMS_V2 = frozenset({"keywords", "document_status"})`
  - `required_capabilities(...)` adds `metadata_fields_v2` when a source opts into either item.

- [ ] **Step 1: Write the failing test**

`tests/test_ingestion_compiler.py`. Append:
```python
class TestFilePropertyOptIns:
    """Spec L1: keywords and Word content status from file properties."""

    def test_the_offered_order(self):
        assert ic.METADATA_ITEMS == ("language", "email_date", "email_doc_type", "email_author",
                                     "document_author", "keywords", "document_status")

    def test_they_compile_and_validate_against_the_shipped_schema(self, sync_request_validator):
        compiled = ic.compile_profile(_with_fields(metadata_fields=("keywords", "document_status")))
        assert compiled.sync_request["sources"][0]["metadata_fields"] == ["keywords", "document_status"]
        sync_request_validator.validate(compiled.sync_request)

    def test_their_targets_must_be_registered(self):
        checked = ic.validate_profile_fields(
            _with_fields(metadata_fields=("keywords", "document_status")), _registry())
        assert checked.sources[0].metadata_fields == ("keywords", "document_status")
        registry = [f for f in _registry() if f["key"] != "status"]
        with pytest.raises(ic.ProfileError) as excinfo:
            ic.validate_profile_fields(_with_fields(metadata_fields=("document_status",)), registry)
        assert "status" in str(excinfo.value)

    def test_enabling_one_re_sends_the_folder(self):
        old = ic.IngestionProfile(identifier_prefix="p", sources=[
            ic.SourceFolder(path="/a", metadata_fields=("language",))])
        new = ic.IngestionProfile(identifier_prefix="p", sources=[
            ic.SourceFolder(path="/a", metadata_fields=("language", "keywords"))])
        assert ic.field_config_changes(old, new) == ["/a"]
```

`tests/test_doc_fields_view.py`. Append to `TestProfileFieldKeys`:
```python
    def test_the_file_property_items_name_their_targets(self):
        profile = {"sources": [{"path": "/a", "metadata_fields": ["keywords", "document_status"]}]}
        assert view.profile_field_keys(profile) == {"keywords", "status"}
```

`tests/test_web_admin_ingestion.py`. Append to `TestFieldInputsParse`:
```python
    def test_the_file_property_opt_ins_parse_in_form_order(self):
        from web_interface.admin_ingestion import parse_metadata_items

        assert parse_metadata_items(["document_status", "keywords", "language"], 1) == (
            "language", "keywords", "document_status")
```
Append to `TestCheckProfileFields`:
```python
    def test_keywords_or_status_need_a_connector_that_knows_them(self):
        from identity.ingestion_compiler import RC_TOO_OLD, ProfileError

        for item in ("keywords", "document_status"):
            with pytest.raises(ProfileError) as excinfo:
                self._check(_profile(_folder(metadata_fields=("language", item))))
            assert str(excinfo.value) == RC_TOO_OLD
        check = self._check(_profile(_folder(metadata_fields=("keywords", "document_status"))),
                            rc=_RC(caps=ALL_CAPS + ("metadata_fields_v2",)))
        assert check.profile.sources[0].metadata_fields == ("keywords", "document_status")
```
Append to `TestKnovasConnectorClientDocFields`:
```python
    def test_the_file_property_items_need_v2(self):
        from knovas_connector_client import CAP_METADATA_FIELDS_V2, required_capabilities

        assert CAP_METADATA_FIELDS_V2 == "metadata_fields_v2"
        assert required_capabilities({"sources": [{"path": "/a", "metadata_fields": ["language"]}]}) == {
            "source_fields_v1", "metadata_fields_v1"}
        for item in ("keywords", "document_status"):
            assert required_capabilities({"sources": [
                {"path": "/a", "metadata_fields": ["language", item]}]}) == {
                "source_fields_v1", "metadata_fields_v1", "metadata_fields_v2"}
```
Append to `TestTemplateFields`:
```python
    def test_the_file_property_opt_ins_are_offered(self):
        from identity.ingestion_compiler import METADATA_ITEMS
        from web_interface.admin_ingestion import METADATA_LABELS

        assert set(METADATA_LABELS) == set(METADATA_ITEMS)
        keywords = ("Stichwörter aus Datei-Eigenschaften "
                    "(PDF/Word-Stichwörter, Outlook-Kategorien)")
        assert METADATA_LABELS["keywords"] == keywords
        assert METADATA_LABELS["document_status"] == "Status aus Word-Dokumentstatus"
        html = _render(doc_fields=_df())
        for key in ("keywords", "document_status"):
            assert f'name="folder-0-metadata" value="{key}"' in html
            assert f'name="folder-__n__-metadata" value="{key}"' in html
        assert keywords in html and "Status aus Word-Dokumentstatus" in html
```

`tests/test_web_admin_system_doc_fields.py`. Append to `TestKnovasConnector`:
```python
    def test_the_file_property_capability_is_named(self):
        rc = _RCWithCaps({"source_fields_v1", "metadata_fields_v1", "metadata_fields_v2"})
        check = _rc(_collect(FakeDocFieldsApi("values"), rc))
        assert check["detail"] == ("antwortet; Dokumentfelder: source_fields_v1, "
                                   "metadata_fields_v1, metadata_fields_v2")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_ingestion_compiler.py::TestFilePropertyOptIns tests/test_doc_fields_view.py::TestProfileFieldKeys tests/test_web_admin_ingestion.py::TestFieldInputsParse tests/test_web_admin_ingestion.py::TestCheckProfileFields tests/test_web_admin_ingestion.py::TestKnovasConnectorClientDocFields tests/test_web_admin_ingestion.py::TestTemplateFields tests/test_web_admin_system_doc_fields.py::TestKnovasConnector`
Expected: FAIL.
- The `METADATA_ITEMS` order (`AssertionError`).
- `validate_profile_fields` raises `ProfileError: … unbekannte Dateieigenschaft`.
- `profile_field_keys` returns `set()`.
- `parse_metadata_items` raises `ProfileError`.
- The capability check passes, so the error is not `RC_TOO_OLD`.
- `ImportError: cannot import name 'CAP_METADATA_FIELDS_V2'`, `KeyError: 'keywords'`, and the System tab detail lacks `metadata_fields_v2`.
- `test_they_compile_and_validate_against_the_shipped_schema` already passes; RCF-7 widened the schema.

- [ ] **Step 3: Write minimal implementation**

`src/identity/ingestion_compiler.py`. Replace:
```python
#: The extractor metadata items Knovas Connector maps (spec 3.5), in the
#: order the form offers them.
METADATA_ITEMS = ("language", "email_date", "email_doc_type", "email_author", "document_author")
```
with:
```python
#: The extractor metadata items Knovas Connector maps (spec 3.5, L1), in the
#: order the form offers them. ``keywords`` and ``document_status`` need a
#: Connector that reports ``metadata_fields_v2``.
METADATA_ITEMS = (
    "language", "email_date", "email_doc_type", "email_author", "document_author",
    "keywords", "document_status",
)
```

`src/doc_fields_view.py`. Replace:
```python
    "email_author": "author",
    "document_author": "author",
}
```
with:
```python
    "email_author": "author",
    "document_author": "author",
    "keywords": "keywords",
    "document_status": "status",
}
```

`src/web_interface/admin_ingestion.py`. Replace:
```python
      (plus ``field_templates_v1`` / ``metadata_fields_v1`` when used);
```
with:
```python
      (plus ``field_templates_v1`` / ``metadata_fields_v1`` when used, and
      ``metadata_fields_v2`` for the keywords and status file properties);
```
Replace:
```python
    "document_author": "Autor aus Dokumenteigenschaften (pdf, docx)",
}
```
with:
```python
    "document_author": "Autor aus Dokumenteigenschaften (pdf, docx)",
    # Spec L1. Knovas keeps a status only when it matches a choice of the
    # ``status`` field; one it does not know shows as invalid_value (status).
    "keywords": ("Stichwörter aus Datei-Eigenschaften "
                 "(PDF/Word-Stichwörter, Outlook-Kategorien)"),
    "document_status": "Status aus Word-Dokumentstatus",
}
```

`src/knovas_connector_client.py`. Replace:
```python
CAP_METADATA_FIELDS = "metadata_fields_v1"
CAP_FIELDS_REQUEUE = "fields_requeue_v1"
```
with:
```python
CAP_METADATA_FIELDS = "metadata_fields_v1"
#: A Knovas Connector that maps the file-property items ``keywords`` and
#: ``document_status`` (spec L1); an older one refuses them in the sync body.
CAP_METADATA_FIELDS_V2 = "metadata_fields_v2"
CAP_FIELDS_REQUEUE = "fields_requeue_v1"

#: The metadata items only a Connector with ``metadata_fields_v2`` accepts.
METADATA_ITEMS_V2 = frozenset({"keywords", "document_status"})
```
Replace:
```python
    accepts. ``fields`` needs ``source_fields_v1``; templates and metadata
    items need theirs on top of it.
    """
```
with:
```python
    accepts. ``fields`` needs ``source_fields_v1``; templates and metadata
    items need theirs on top of it, and the items ``keywords`` and
    ``document_status`` also ``metadata_fields_v2``.
    """
```
Replace:
```python
        if source.get("metadata_fields"):
            needed.update((CAP_SOURCE_FIELDS, CAP_METADATA_FIELDS))
    return frozenset(needed)
```
with:
```python
        items = source.get("metadata_fields")
        if items:
            needed.update((CAP_SOURCE_FIELDS, CAP_METADATA_FIELDS))
            if any(item in METADATA_ITEMS_V2 for item in items):
                needed.add(CAP_METADATA_FIELDS_V2)
    return frozenset(needed)
```

`src/web_interface/admin_system.py`. Replace:
```python
RC_DOC_FIELD_CAPABILITIES = ("source_fields_v1", "field_templates_v1",
                             "metadata_fields_v1", "fields_requeue_v1")
```
with:
```python
RC_DOC_FIELD_CAPABILITIES = ("source_fields_v1", "field_templates_v1",
                             "metadata_fields_v1", "fields_requeue_v1", "metadata_fields_v2")
```

`KnovasPlatform/docs/features/document-fields.md`. Replace:
```
- **Aus Dateieigenschaften** — opt-ins: language (document properties of
  pdf/docx, often the program's language; `.eml` Content-Language), e-mail
  date as document date, e-mails as document type "E-Mail", e-mail sender as
  author, author from pdf/docx properties. `.md` and `.txt` files have no
  properties.
```
with:
```
- **Aus Dateieigenschaften** — opt-ins: language (document properties of
  pdf/docx, often the program's language; `.eml` Content-Language), e-mail
  date as document date, e-mails as document type "E-Mail", e-mail sender as
  author, author from pdf/docx properties, *Stichwörter aus
  Datei-Eigenschaften* (PDF/Word keywords and Outlook categories, split on
  `,` and `;`, at most 32; knovas-extract 0.4.0a1 does not read Outlook
  categories yet) and *Status aus Word-Dokumentstatus* (sent as written;
  Knovas keeps it only when it matches a choice of `status`, otherwise the
  status panel counts an `invalid_value` under `status`). These two need a
  Knovas Connector that reports `metadata_fields_v2`. `.md` and `.txt` files
  have no properties.
```

`RELEASE_NOTES.md` (ASCII transliteration like the rest of the file). Replace the first:
```
# Unreleased

```
with:
```
# Unreleased

## Dokumentfelder: Hinweise mit Feld, Stichwoerter und Status, erneutes Senden

- **Hinweise von Knovas nennen das Feld.** Der Reiter *Ingestion* zeigt die
  Upload-Hinweise des letzten Durchlaufs je Code und Feldschluessel, z.B.
  `invalid_value 3x (amount)`, und darunter einmal die Bedeutung jedes
  Codes; nie einen Wert. Ein aelterer Knovas Connector liefert nur Codes.
- **Nicht uebernommene Felder ohne Anlass erneut senden.** Hat Knovas Felder
  nicht angenommen, fragt der Knovas Connector hoechstens einmal pro Stunde
  nach (`GET /secured/graph/doc-fields`). Nimmt Knovas sie an, sendet er
  diese Dokumente erneut, hoechstens 100 pro Durchlauf -- auch wenn sonst
  nichts hochgeladen wird.
- **Stichwoerter und Status aus Datei-Eigenschaften.** Zwei neue Opt-ins pro
  Ordner: *Stichwoerter aus Datei-Eigenschaften (PDF/Word-Stichwoerter,
  Outlook-Kategorien)* fuellt `keywords`, *Status aus Word-Dokumentstatus*
  fuellt `status`; Knovas uebernimmt einen Status nur, wenn er zu einer
  Status-Auswahl passt. Einschalten sendet die Dokumente des Ordners erneut
  (verrechnet, mit Bestaetigung) und braucht einen Knovas Connector, der
  `metadata_fields_v2` meldet. Outlook-Kategorien liest knovas-extract
  0.4.0a1 noch nicht.

```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_ingestion_compiler.py tests/test_doc_fields_view.py tests/test_web_admin_ingestion.py tests/test_web_admin_system_doc_fields.py`
Expected: PASS. The neighbouring suite `pf-pytest tests/test_rc_contract_copies.py tests/test_web_admin_doc_fields.py` still passes.

- [ ] **Step 5: Commit**

```bash
cd $WT
P=KnovasPlatform/components/docbridge_integration
git add $P/src/identity/ingestion_compiler.py $P/src/doc_fields_view.py \
  $P/src/web_interface/admin_ingestion.py $P/src/knovas_connector_client.py \
  $P/src/web_interface/admin_system.py $P/tests/test_ingestion_compiler.py \
  $P/tests/test_doc_fields_view.py $P/tests/test_web_admin_ingestion.py \
  $P/tests/test_web_admin_system_doc_fields.py KnovasPlatform/docs/features/document-fields.md \
  RELEASE_NOTES.md
git commit -F - <<'EOF'
platform: offer keywords and Word status as per-folder file properties

Spec L1. The Ingestion tab offers "Stichwoerter aus Datei-Eigenschaften
(PDF/Word-Stichwoerter, Outlook-Kategorien)" and "Status aus
Word-Dokumentstatus"; the compiler emits them, the save checks that
keywords and status are active fields, and they count as fields in use.
A profile using either needs a Knovas Connector that reports
metadata_fields_v2, so an older one is refused with "zu alt" before a
version row is written instead of answering 400 to the push. Enabling one
changes the folder's field configuration, so the existing billed re-send
confirmation applies. Release notes cover F4, F5 and L1.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

**Section check (after RCF-8):** `rc-pytest tests`, `pf-pytest tests`, `mock-pytest tests`. All pass except the known Windows-only failures listed in the plan header. Then `grep -rn "probe_doc_fields\|warning_pairs\|metadata_fields_v2" $WT/KnovasConnector/src $WT/KnovasPlatform/components/docbridge_integration/src` shows the new names exactly as in the interface contract, with `SemantixUploader` in place of the contract's `KnovasUploader`.

---

**Points for the lead:**
1. **Contract table:** please change `KnovasUploader.probe_doc_fields` to `SemantixUploader.probe_doc_fields` (`sync/knovas_uploader.py`). No `KnovasUploader` class exists, and §5.3 keeps identifiers.
2. **429 deviates from the contract.** RCF-4 and RCF-5 read a 429 as unknown (None), not True: a rate limit says nothing about the feature, and True would re-send billed documents every hour. If you want the contract's wording kept, it is one line, `PROBE_UNKNOWN_STATUSES = frozenset()`, plus moving `429` to True in the uploader test.
3. **Residual risk in F5, as the spec defines it.** A non-conforming intermediary (proxy or WAF) that answers non-404 to the GET while Knovas ignores upload fields would requeue `not_accepted` documents once an hour, bounded per cycle. A conforming 1.5.0 server cannot cause this. A cheaper alternative, if you want it: on "on", re-send one canary document and let its echo trigger the bulk requeue. That would mean amending the spec.
4. **New capability `metadata_fields_v2`** (RCF-7/8). It is not in the contract table, but the Platform's push gate needs it. If REX adds a capability too, both are appended at the end of `RC_CAPABILITIES`, and the contract test's list must follow that order.
5. **Library gap for Part A: `msg:categories` is never produced.** `knovas_extract/extractors/msg.py` uses `getattr(msg, "categories", None)`, but extract-msg 0.56.1 has no `categories` on a `Message`; only `CalendarBase.keywords` exists. A fix would be `getattr(msg, "categories", None) or msg.getNamedProp("Keywords", "{00020329-0000-0000-C000-000000000046}")`, guarded by try/except, plus a test. The Connector already parses the JSON array that `sanitize_scalar` writes. If you add the fix, drop the "Outlook-Kategorien liest knovas-extract 0.4.0a1 noch nicht" sentences from `KnovasConnector/docs/configuration.md`, `KnovasConnector/CHANGELOG.md`, the Platform feature doc and `RELEASE_NOTES.md`.
6. **`/sync/status` `doc_fields.warnings` changes shape** from a dict to a list, as the contract defines. An older Platform simply shows no warnings line; it does not crash. `POST /sync` keeps `{code: count}`.
7. **Small additions beyond the brief:** `ambiguous_number` added to the Connector's closed metric label set (F4 task), and a 1.5.0 vectors file `contracts/vectors/metadata_fields.json` for the Connector only. The Platform executes no metadata mapping, so it gets no copy; the schema copy is the one kept byte-identical.

---

## Part B6 — Re-extraction after an extractor upgrade (REX)

Spec §8 L6 (decision D5). For every document, the Knovas Connector records which extraction produced its upload (an **extraction stamp**) and a sha256 of exactly what the upload carried (`text_sha256`). After an extractor upgrade, the Platform's Ingestion tab shows *N Dokumente mit älterer Extraktion*. An administrator can re-extract them, but only after a confirmation that states the cost and duration. Re-extraction goes through a bounded side queue and **uploads only documents whose upload would change** (every upload is billed).

**This part runs after EXT and PIN.** REX-1 reads EXT's `docx_text_mode()`, `ocr_dpi()` and `sentence_emit_max_bytes()` (default `0`). REX-4 adds to PIN's `extraction` block of `GET /sync/status`. RCF and FLD touch neighbouring code (`run_sync_work`, `doc_fields_status`). Every hunk below quotes its anchor from `$MERGED`. If an earlier part changed lines next to an anchor, keep that part's lines and apply the hunk around them.

**Order:** REX-1 → REX-2 → REX-3a → REX-3b → REX-3c → REX-4 → REX-5a → REX-5b. The requested REX-3 is split into a/b/c and REX-5 into a/b so that each piece has its own test cycle. Together they cover exactly the requested scope.

**Decisions the lead and the other parts need to know (all of them stay inside this part):**

1. **Queue marker (REX-2).** The fields side queue has no marker column. Each cycle derives membership from `fields_digest` ≠ the governing digest (`_classify_status` → `fields_changed`; `REQUEUE_DIGEST` overloads the digest). Reusing it would turn a re-extraction into a fields re-upload: it would only work with `RC_DOC_FIELDS` on, count against `fields_attempts`, and end as `reupload_failed:*`.
   - So REX-2 adds two columns next to the contract's `extraction_stamp` and `text_sha256`:
     - `resend_reason TEXT NULL` (value `reextract`)
     - `resend_attempts INTEGER NOT NULL DEFAULT 0`
   - **How the side queue reads it:**
     - `plan_sync_cycle` loads `load_reextract_queue()` (rel → stored `text_sha256`) once per incremental cycle.
     - A scanned file whose status is `synced` and whose rel is in that dict becomes a candidate (one per path).
     - Candidates are ranked: partial (from `partial_documents`) → `.pdf` → `.docx` → `.eml`/`.msg` → the rest, in scan order within a rank.
     - The first `RC_REEXTRACT_PER_CYCLE` candidates form `plan.reextract_queue`. That queue is trimmed to the room `max_files_per_cycle` leaves after the primary queue and the fields queue, and is processed last, in rank order.
   - **The marker is cleared by:**
     - any successful upload (`set_extraction`);
     - an unchanged or unconvertible re-extraction (`set_extraction_stamp`);
     - the third failed attempt (`count_reextract_failure`).
2. **Hash inputs (REX-1).** `upload_text_sha256(parts, fields_digest, *, title=None, description=None)` keeps the contract's positional signature.
   - The keyword-only `title`/`description` are additions: the extractor supplies both, and an upgrade can change them without changing the text.
   - The uploader passes `fields_values_digest(<the fields object the init carries>)`, **not** the stored `config_digest`. `config_digest` by design leaves out extractor values (author, e-mail date, language), which a newer extractor may read differently.
   - `tables` payloads are excluded, because the server drops them.
   - New public helpers: `stamp_inputs()` and `fields_values_digest()`.
3. **Status keys beyond the contract (REX-4).** `extraction` gains `queued` and `per_cycle` next to `outdated`. The Platform's ETA needs the Connector's bound, and `queued` shows progress.
   - `outdated` is one count over all tracked rows. The state DB has no source column (relative paths only; duplicates across sources share a row), so the spec's "per source" is not available.
   - Please add `queued` and `per_cycle` to the contract table.
4. **Uploader (REX-3a).**
   - `upload_file(..., *, unchanged_text_sha256=None)`.
   - `UploadResult` gains `text_sha256`, `extraction_stamp` and a new status `"unchanged"`.
5. **Failures (REX-3c).** A failed re-extraction never records a skip or a partial note, and never counts toward `RC_EXTRACT_MAX_RETRIES`, because Knovas still holds the last upload.
   - Unconvertible → the row takes the current stamp and leaves the queue.
   - Any other failure → counted; the row leaves the queue after `REEXTRACT_MAX_ATTEMPTS = 3` and stays outdated. This is a constant, not a new setting.
   - A **new** unconvertible file also gets the current stamp (REX-3b). Without this, corrupt files would count as "älterer Extraktion" forever.
6. **Scheduler (REX-3c, REX-4).** Queued re-extractions that the scan reached count as pending work, so the worker does not back off while they wait. They also hold back a sequential subfolder, exactly as `fields_changed` does.
7. **Platform (REX-5b).**
   - `POST /admin/ingestion/reextract` is admin-only: `require_admin` is passed into `attach_ingestion_routes`, and `TestShape` counts both gates. The tab itself stays open to `admin`/`ingestion_manager`.
   - The confirmation carries the count it showed (`confirm_reextract=<N>`); the dialog is shown again if the count has grown since.
   - The confirmation is server-rendered, like the field-change and restore confirmations, so `admin_ingestion.js` is not changed.
8. **Known limitation.** A queued document that no scan reaches any more stays queued and counted. Examples: a completed subfolder of a sequential import, or a removed file kept by `delete_on_remove: false`. Documented in operations.md.

**Privacy:** no new log line, metric label, error or audit entry carries a path, a title or document text — only counts, stamps and hashes. REX-3c, REX-4 and REX-5b include caplog or audit assertions for this. A failed or changed re-extraction goes through `run_sync_work`'s existing per-upload lines, like every upload. Those lines are unchanged here.

---

### Task REX-1: Extraction stamp and uploaded-text hash (`sync.extraction_stamp`)

**Files:**
- Create: `KnovasConnector/src/sync/extraction_stamp.py`
- Test: `KnovasConnector/tests/unit/test_extraction_stamp.py`

**Interfaces:**
- Consumes:
  - EXT: `sync.document_text.docx_text_mode() -> str`, `ocr_dpi() -> Optional[int]` (None when unset), `sentence_emit_max_bytes() -> int` (default 0).
  - Existing: `pdf_text_mode() -> str` (default `layout`), `ocr_engine() -> str` (default `auto`).
- Produces:
  - `EXTRACTION_SCHEMA = 1`, `STAMP_LENGTH = 16`
  - `stamp_inputs() -> dict[str, Any]`
  - `current_extraction_stamp() -> str` (16 lowercase hex characters)
  - `fields_values_digest(values: Optional[Mapping[str, Any]]) -> Optional[str]`
  - `upload_text_sha256(parts: Sequence[Mapping[str, Any]], fields_digest: Optional[str], *, title: Optional[str] = None, description: Optional[str] = None) -> str` (64 hex characters)
  - Private `_knovas_extract_version() -> Optional[str]` (lru-cached; tests monkeypatch it).

- [ ] **Step 1: Write the failing test**

```python
"""The extraction stamp and the uploaded-text hash (spec L6)."""
from __future__ import annotations

import re
from importlib import metadata

import pytest

from sync import extraction_stamp
from sync.extraction_stamp import (
    EXTRACTION_SCHEMA,
    current_extraction_stamp,
    fields_values_digest,
    stamp_inputs,
    upload_text_sha256,
)

#: The real lookup, captured before any test replaces it.
REAL_VERSION = extraction_stamp._knovas_extract_version
SETTINGS = ("RC_PDF_TEXT_MODE", "RC_DOCX_TEXT_MODE", "RC_OCR_ENGINE", "RC_OCR_DPI",
            "RC_SENTENCE_EMIT_MAX_BYTES")
PARTS = [
    {"snippet": "Rechnung 17 an Muster AG", "page_number": 1, "sentence_number": 1},
    {"snippet": "\fSeite zwei: Honorar CHF 1'200", "page_number": 2, "sentence_number": 4},
]


@pytest.fixture(autouse=True)
def defaults(monkeypatch):
    for key in SETTINGS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(extraction_stamp, "_knovas_extract_version", lambda: "0.4.0a1")


class TestStamp:
    def test_sixteen_hex_characters_and_stable(self):
        stamp = current_extraction_stamp()
        assert re.fullmatch(r"[0-9a-f]{16}", stamp)
        assert current_extraction_stamp() == stamp

    @pytest.mark.parametrize("key,value", [
        ("RC_PDF_TEXT_MODE", "plain"),
        ("RC_DOCX_TEXT_MODE", "plain"),
        ("RC_OCR_ENGINE", "cli"),
        ("RC_OCR_DPI", "200"),
        ("RC_SENTENCE_EMIT_MAX_BYTES", "1048576"),
    ])
    def test_each_setting_changes_it(self, monkeypatch, key, value):
        before = current_extraction_stamp()
        monkeypatch.setenv(key, value)
        assert current_extraction_stamp() != before

    def test_the_library_version_changes_it(self, monkeypatch):
        before = current_extraction_stamp()
        monkeypatch.setattr(extraction_stamp, "_knovas_extract_version", lambda: "0.4.0")
        assert current_extraction_stamp() != before
        monkeypatch.setattr(extraction_stamp, "_knovas_extract_version", lambda: None)
        assert re.fullmatch(r"[0-9a-f]{16}", current_extraction_stamp())

    def test_the_schema_changes_it(self, monkeypatch):
        before = current_extraction_stamp()
        monkeypatch.setattr(extraction_stamp, "EXTRACTION_SCHEMA", EXTRACTION_SCHEMA + 1)
        assert current_extraction_stamp() != before

    def test_it_covers_versions_and_settings_only(self):
        assert stamp_inputs() == {
            "knovas_extract": "0.4.0a1", "pdf_text_mode": "layout", "docx_text_mode": "layout",
            "ocr_engine": "auto", "ocr_dpi": None, "sentence_emit_max_bytes": 0, "schema": 1,
        }

    def test_the_version_is_the_installed_distribution(self):
        REAL_VERSION.cache_clear()
        assert REAL_VERSION() == metadata.version("knovas-extract")


class TestUploadTextSha256:
    def test_equal_content_gives_an_equal_hash(self):
        same = [dict(reversed(list(part.items()))) for part in PARTS]
        assert upload_text_sha256(same, "d") == upload_text_sha256(PARTS, "d")
        assert re.fullmatch(r"[0-9a-f]{64}", upload_text_sha256(PARTS, None))

    def test_the_order_of_the_parts_matters(self):
        assert upload_text_sha256(list(reversed(PARTS)), None) != upload_text_sha256(PARTS, None)

    @pytest.mark.parametrize("change", [
        {"snippet": "Rechnung 18 an Muster AG"}, {"page_number": 3}, {"sentence_number": 2},
    ])
    def test_text_and_locations_matter(self, change):
        changed = [dict(PARTS[0], **change), PARTS[1]]
        assert upload_text_sha256(changed, None) != upload_text_sha256(PARTS, None)

    def test_numbers_count_as_the_transmission_sends_them(self):
        assert upload_text_sha256([{"snippet": "x", "page_number": 0}], None) == \
            upload_text_sha256([{"snippet": "x"}], None)
        assert upload_text_sha256([{"snippet": "x", "sentence_number": "4"}], None) == \
            upload_text_sha256([{"snippet": "x", "sentence_number": 4}], None)

    def test_tables_are_left_out(self):
        with_tables = [dict(PARTS[0], tables=[{"headers": ["Betrag"], "rows": [["1200"]]}]),
                       PARTS[1]]
        assert upload_text_sha256(with_tables, None) == upload_text_sha256(PARTS, None)

    def test_fields_title_and_description_matter(self):
        base = upload_text_sha256(PARTS, None)
        assert upload_text_sha256(PARTS, fields_values_digest({})) != base
        assert upload_text_sha256(PARTS, None, title="Rechnung 17") != base
        assert upload_text_sha256(PARTS, None, description="Mandat 17") != base


class TestFieldsValuesDigest:
    def test_none_is_none_and_key_order_does_not_matter(self):
        assert fields_values_digest(None) is None
        assert fields_values_digest({"a": 1, "b": ["x", "y"]}) == \
            fields_values_digest({"b": ["x", "y"], "a": 1})

    def test_a_clear_differs_from_values(self):
        assert fields_values_digest({}) != fields_values_digest({"doc_type": "invoice"})
        assert re.fullmatch(r"[0-9a-f]{64}", fields_values_digest({}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_extraction_stamp.py`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'sync.extraction_stamp'`

- [ ] **Step 3: Write minimal implementation**

Create `KnovasConnector/src/sync/extraction_stamp.py`:

```python
"""Extraction stamp and uploaded-text hash (spec L6).

Every upload the Knovas Connector makes is recorded with two values:

* the **extraction stamp** -- 16 hex characters of sha256 over the settings
  that shape a document's text: the installed knovas-extract version, the
  PDF and DOCX text modes, the OCR engine, the DPI setting, the sentence
  gate and ``EXTRACTION_SCHEMA``. A row whose stamp is not
  ``current_extraction_stamp()`` (or that has none) was produced by an
  older extraction;
* the **uploaded-text hash** (``upload_text_sha256``) -- sha256 over exactly
  what the upload carried to the index. A re-extraction whose hash equals
  the stored one is not sent again: every upload is billed.

No I/O besides reading the settings and the installed version, and no
logging. Stamps and hashes may be logged and reported; what goes into the
hash (text, field values) never leaves this module.
"""
from __future__ import annotations

import functools
import hashlib
import json
from importlib import metadata
from typing import Any, Mapping, Optional, Sequence

from sync.document_text import (
    docx_text_mode,
    ocr_dpi,
    ocr_engine,
    pdf_text_mode,
    sentence_emit_max_bytes,
)

#: Bump when the Connector's own processing changes what an upload carries
#: for the same extractor output (chunking, page markers, part numbering):
#: every document then counts as produced by an older extraction.
EXTRACTION_SCHEMA = 1

#: Hex characters of the stamp.
STAMP_LENGTH = 16


@functools.lru_cache(maxsize=1)
def _knovas_extract_version() -> Optional[str]:
    """The installed knovas-extract version; None when it is not installed.
    Read once per process: another version means another image."""
    try:
        return metadata.version("knovas-extract")
    except metadata.PackageNotFoundError:
        return None


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def stamp_inputs() -> dict[str, Any]:
    """What the stamp covers: versions and settings, never document data."""
    return {
        "knovas_extract": _knovas_extract_version(),
        "pdf_text_mode": pdf_text_mode(),
        "docx_text_mode": docx_text_mode(),
        "ocr_engine": ocr_engine(),
        "ocr_dpi": ocr_dpi(),
        "sentence_emit_max_bytes": sentence_emit_max_bytes(),
        "schema": EXTRACTION_SCHEMA,
    }


def current_extraction_stamp() -> str:
    """16 hex characters of sha256 over the canonical JSON of ``stamp_inputs()``."""
    return hashlib.sha256(_canonical(stamp_inputs())).hexdigest()[:STAMP_LENGTH]


def fields_values_digest(values: Optional[Mapping[str, Any]]) -> Optional[str]:
    """sha256 of the ``fields`` object an init carries; None for an init
    without one.

    Not the stored ``fields_digest`` (``doc_fields_payload.config_digest``):
    that one covers the configuration and by design leaves out the values
    the extractor supplies (author, e-mail date, language), which a newer
    extractor may read differently.
    """
    if values is None:
        return None
    return hashlib.sha256(_canonical(dict(values))).hexdigest()


def _wire_number(value: Any) -> Optional[int]:
    """A part's page or sentence number as the transmission sends it
    (``knovas_uploader._transmit_part_body``: numbers >= 1 only)."""
    if value is None:
        return None
    number = int(value)
    return number if number >= 1 else None


def upload_text_sha256(
    parts: Sequence[Mapping[str, Any]],
    fields_digest: Optional[str],
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """sha256 over what one upload carries to the index.

    Canonical JSON (sorted keys, ``ensure_ascii=False``) of every part's
    snippet -- page markers included, as transmitted -- with its page and
    sentence number, in order; the fields digest (``fields_values_digest``);
    and the init's title and description, which the extractor supplies too.
    ``tables`` payloads are left out: the server drops them at its part
    buffer, so they never reach the index.
    """
    canonical = {
        "parts": [
            {
                "snippet": str(part.get("snippet") or ""),
                "page_number": _wire_number(part.get("page_number")),
                "sentence_number": _wire_number(part.get("sentence_number")),
            }
            for part in parts
        ],
        "fields": fields_digest,
        "title": title,
        "description": description,
    }
    return hashlib.sha256(_canonical(canonical)).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_extraction_stamp.py`
Expected: PASS. The neighbouring suite `rc-pytest tests/unit/test_document_text.py` still passes.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/extraction_stamp.py KnovasConnector/tests/unit/test_extraction_stamp.py
git commit -F - <<'EOF'
rc: extraction stamp and uploaded-text hash (L6)

Every upload will record which extraction produced it -- a short hash of
the knovas-extract version, the PDF/DOCX text modes, the OCR engine, the
DPI setting, the sentence gate and EXTRACTION_SCHEMA -- and the sha256 of
exactly what it carried. A re-extraction after an extractor upgrade can
then skip documents whose upload would not change; every upload is billed.
The hash covers the field values, title and description as well, because
config_digest leaves out the values the extractor supplies.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task REX-2: State DB columns and methods for re-extraction

**Files:**
- Modify: `KnovasConnector/src/sync/sync_state_db.py` (`_FIELDS_COLUMNS` neighbourhood, `FieldsState` neighbourhood, `_connect`, `_ensure_fields_columns` → `_ensure_columns`, new re-extraction methods before `list_tracked_paths`)
- Modify: `KnovasConnector/src/sync/sync_state.py` (import; wrappers before `status_for`)
- Test: `KnovasConnector/tests/unit/test_sync_state_db_reextract_migration.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `sync_state_db.RESEND_REEXTRACT = "reextract"`
  - `sync_state_db.ExtractionState(stamp, text_sha256, resend_reason, resend_attempts)` (NamedTuple)
  - `documents` columns `extraction_stamp TEXT`, `text_sha256 TEXT`, `resend_reason TEXT`, `resend_attempts INTEGER NOT NULL DEFAULT 0`
  - On both `SyncStateDatabase` and `SyncStateStore`:
    - `extraction_state(rel) -> Optional[ExtractionState]`
    - `set_extraction(rel, stamp: Optional[str], text_sha: Optional[str]) -> bool`
    - `set_extraction_stamp(rel, stamp: str) -> bool`
    - `count_extraction_outdated(stamp: str) -> int`
    - `requeue_reextract(stamp: str) -> int`
    - `count_reextract_queued() -> int`
    - `load_reextract_queue() -> dict[str, Optional[str]]`
    - `count_reextract_failure(rel, max_attempts: int) -> bool`
  - `SyncStateStore.record_reextract_unchanged(rel, stamp: str, partial: Optional[dict]) -> None`

- [ ] **Step 1: Write the failing test**

```python
"""The re-extraction columns of the sync state DB (spec L6).

An existing state file gains ``extraction_stamp``, ``text_sha256``,
``resend_reason`` and ``resend_attempts`` in place. Every row keeps its
fingerprint and its fields columns, and every row synced before counts as
produced by an older extraction (stamp NULL) -- nothing is queued by the
upgrade itself.
"""
from __future__ import annotations

import sqlite3

import pytest

from sync.doc_fields_payload import FieldsRecord
from sync.sync_state import SyncStateStore
from sync.sync_state_db import RESEND_REEXTRACT, ExtractionState, FieldsState, SyncStateDatabase

TABLES = """
CREATE TABLE partial_documents (
    relative_path TEXT PRIMARY KEY NOT NULL,
    note_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE extract_retries (
    relative_path TEXT PRIMARY KEY NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT
);
"""
PRE_FIELDS = """
CREATE TABLE documents (
    relative_path TEXT PRIMARY KEY NOT NULL,
    mtime_iso TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_uploaded_at TEXT,
    transmission_key_id TEXT
);
""" + TABLES
DOC_FIELDS = """
CREATE TABLE documents (
    relative_path TEXT PRIMARY KEY NOT NULL,
    mtime_iso TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_uploaded_at TEXT,
    transmission_key_id TEXT,
    fields_digest TEXT,
    fields_sent INTEGER NOT NULL DEFAULT 0,
    fields_outcome TEXT,
    fields_warning_codes TEXT,
    fields_attempts INTEGER NOT NULL DEFAULT 0
);
""" + TABLES
NEW_COLUMNS = {"extraction_stamp", "text_sha256", "resend_reason", "resend_attempts"}
TS = "2026-10-01T00:00:00Z"
STAMP = "0123456789abcdef"
OLDER = "fedcba9876543210"
SHA = "a" * 64


def _old_db(path, schema: str) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(schema)
    for rel, size, key in (("Mandate/a.pdf", 10, "tk-old"), ("leer.txt", 0, "skip:unconvertible")):
        conn.execute(
            "INSERT INTO documents (relative_path, mtime_iso, size_bytes, last_uploaded_at, "
            "transmission_key_id) VALUES (?, ?, ?, ?, ?)",
            (rel, TS, size, TS, key),
        )
    conn.commit()
    conn.close()


def _columns(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    finally:
        conn.close()


@pytest.fixture
def store(tmp_path):
    s = SyncStateStore(str(tmp_path / "state.json"))
    yield s
    s.close()


class TestMigration:
    @pytest.mark.parametrize("schema", [PRE_FIELDS, DOC_FIELDS], ids=["pre-fields", "doc-fields"])
    def test_an_older_file_gains_the_columns_and_every_row_is_outdated(self, tmp_path, schema):
        db_path = tmp_path / "state.db"
        _old_db(db_path, schema)
        db = SyncStateDatabase(db_path)
        try:
            assert db.load_fingerprints() == {"Mandate/a.pdf": (TS, 10), "leer.txt": (TS, 0)}
            assert db.extraction_state("Mandate/a.pdf") == ExtractionState(None, None, None, 0)
            assert db.count_extraction_outdated(STAMP) == 2
            assert db.count_reextract_queued() == 0, "the upgrade queues nothing by itself"
            assert db.load_reextract_queue() == {}
        finally:
            db.close()
        assert NEW_COLUMNS <= _columns(db_path)

    def test_the_fields_columns_survive(self, tmp_path):
        db_path = tmp_path / "state.db"
        _old_db(db_path, DOC_FIELDS)
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE documents SET fields_digest = 'd1', fields_sent = 1, "
                     "fields_outcome = 'staged' WHERE relative_path = 'Mandate/a.pdf'")
        conn.commit()
        conn.close()
        db = SyncStateDatabase(db_path)
        try:
            assert db.fields_state("Mandate/a.pdf") == FieldsState("d1", True, "staged", 0)
        finally:
            db.close()

    def test_opening_twice_and_side_by_side_is_harmless(self, tmp_path):
        db_path = tmp_path / "state.db"
        _old_db(db_path, PRE_FIELDS)
        first, second = SyncStateDatabase(db_path), SyncStateDatabase(db_path)
        try:
            first.load_fingerprints()
            second.load_fingerprints()
        finally:
            first.close()
            second.close()
        again = SyncStateDatabase(db_path)
        try:
            assert again.count_tracked() == 2
        finally:
            again.close()

    def test_an_older_connector_writing_the_file_leaves_the_row_outdated(self, tmp_path):
        """Downgrade: the old INSERT OR REPLACE names only its own columns,
        so the stamp falls back to NULL -- outdated, never wrongly current,
        and not queued."""
        db_path = tmp_path / "state.db"
        db = SyncStateDatabase(db_path)
        try:
            db.record_upload("a.pdf", TS, 1, "tk")
            assert db.set_extraction("a.pdf", STAMP, SHA) is True
        finally:
            db.close()
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT OR REPLACE INTO documents "
            "(relative_path, mtime_iso, size_bytes, last_uploaded_at, transmission_key_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("a.pdf", TS, 2, TS, "tk2"),
        )
        conn.commit()
        conn.close()
        db = SyncStateDatabase(db_path)
        try:
            assert db.extraction_state("a.pdf") == ExtractionState(None, None, None, 0)
            assert db.count_extraction_outdated(STAMP) == 1
        finally:
            db.close()


class TestStampAndQueue:
    def test_set_extraction_stores_both_and_leaves_the_queue(self, store):
        store.record_upload("a.pdf", TS, 1, "tk")
        assert store.requeue_reextract(STAMP) == 1
        assert store.set_extraction("a.pdf", STAMP, SHA) is True
        assert store.extraction_state("a.pdf") == ExtractionState(STAMP, SHA, None, 0)
        assert store.count_extraction_outdated(STAMP) == 0
        assert store.set_extraction("missing.pdf", STAMP, SHA) is False
        assert store.count_tracked_paths() == 1, "never creates a row"

    def test_requeue_marks_outdated_rows_once(self, store):
        for rel in ("a.pdf", "b.docx", "c.txt"):
            store.record_upload(rel, TS, 1, "tk")
        store.set_extraction("c.txt", STAMP, SHA)
        store.set_extraction("b.docx", OLDER, SHA)
        assert store.count_extraction_outdated(STAMP) == 2
        assert store.requeue_reextract(STAMP) == 2
        assert store.requeue_reextract(STAMP) == 0, "already waiting: not counted twice"
        assert store.count_reextract_queued() == 2
        assert store.load_reextract_queue() == {"a.pdf": None, "b.docx": SHA}
        assert store.extraction_state("b.docx").resend_reason == RESEND_REEXTRACT

    def test_set_extraction_stamp_keeps_the_hash(self, store):
        store.record_upload("a.pdf", TS, 1, "tk")
        store.set_extraction("a.pdf", OLDER, SHA)
        store.requeue_reextract(STAMP)
        assert store.set_extraction_stamp("a.pdf", STAMP) is True
        assert store.extraction_state("a.pdf") == ExtractionState(STAMP, SHA, None, 0)

    def test_unchanged_moves_the_stamp_and_follows_the_new_partial_note(self, store):
        store.record_partial("scan.pdf", TS, 1, "tk", {"reason": "ocr_backend_none"})
        store.set_extraction("scan.pdf", OLDER, SHA)
        store.requeue_reextract(STAMP)
        store.record_reextract_unchanged("scan.pdf", STAMP, None)
        assert store.extraction_state("scan.pdf") == ExtractionState(STAMP, SHA, None, 0)
        assert store.partial_paths() == [], "the newer extraction is complete"
        assert store.load_fingerprints()["scan.pdf"] == (TS, 1)
        store.set_extraction("scan.pdf", OLDER, SHA)
        store.requeue_reextract(STAMP)
        store.record_reextract_unchanged("scan.pdf", STAMP, {"ocr_pages_skipped": 3})
        assert store.partial_note("scan.pdf") == {"ocr_pages_skipped": 3}

    def test_failures_leave_the_queue_after_the_cap_and_stay_outdated(self, store):
        store.record_upload("a.pdf", TS, 1, "tk")
        store.requeue_reextract(STAMP)
        assert store.count_reextract_failure("a.pdf", 3) is False
        assert store.count_reextract_failure("a.pdf", 3) is False
        assert store.extraction_state("a.pdf").resend_attempts == 2
        assert store.count_reextract_failure("a.pdf", 3) is True
        assert store.extraction_state("a.pdf") == ExtractionState(None, None, None, 0)
        assert store.count_extraction_outdated(STAMP) == 1
        assert store.count_reextract_failure("a.pdf", 3) is False, "not queued: nothing counted"
        assert store.requeue_reextract(STAMP) == 1, "a new request queues it again"

    def test_fingerprint_writes_keep_the_columns(self, store):
        store.record_upload("a.pdf", TS, 1, "tk1", fields=FieldsRecord("d", "staged", sent=True))
        store.set_extraction("a.pdf", STAMP, SHA)
        store.record_upload("a.pdf", "2026-10-02T00:00:00Z", 2, "tk2")
        store.record_skip("a.pdf", TS, 1, reason="unconvertible")
        assert store.extraction_state("a.pdf") == ExtractionState(STAMP, SHA, None, 0)

    def test_remove_and_reset_forget_the_columns(self, store):
        store.record_upload("a.pdf", TS, 1, "tk")
        store.record_upload("b.pdf", TS, 1, "tk")
        store.requeue_reextract(STAMP)
        store.remove_tracked("a.pdf")
        assert store.extraction_state("a.pdf") is None
        assert store.count_reextract_queued() == 1
        store.reset_all()
        assert store.count_extraction_outdated(STAMP) == 0
        assert store.count_reextract_queued() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_sync_state_db_reextract_migration.py`
Expected: FAIL — `ImportError: cannot import name 'RESEND_REEXTRACT' from 'sync.sync_state_db'`

- [ ] **Step 3: Write minimal implementation**

3.1 `KnovasConnector/src/sync/sync_state_db.py`: insert directly after the existing tuple

```python
_FIELDS_COLUMNS = (
    ("fields_digest", "TEXT"),
    ("fields_sent", "INTEGER NOT NULL DEFAULT 0"),
    ("fields_outcome", "TEXT"),
    ("fields_warning_codes", "TEXT"),
    ("fields_attempts", "INTEGER NOT NULL DEFAULT 0"),
)
```

the following:

```python

#: Re-extraction columns of ``documents`` (spec L6), added in place like the
#: fields columns. ``extraction_stamp`` NULL means synced before stamps
#: existed, i.e. by an older extraction. ``resend_reason`` is the side-queue
#: marker (``RESEND_REEXTRACT``); ``resend_attempts`` counts failed
#: re-extractions. An older Knovas Connector's ``INSERT OR REPLACE`` resets
#: them to NULL / 0: outdated and not queued, the safe direction.
_EXTRACTION_COLUMNS = (
    ("extraction_stamp", "TEXT"),
    ("text_sha256", "TEXT"),
    ("resend_reason", "TEXT"),
    ("resend_attempts", "INTEGER NOT NULL DEFAULT 0"),
)

#: ``resend_reason`` of a row ``POST /sync/reextract/requeue`` queued.
RESEND_REEXTRACT = "reextract"
```

3.2 Directly after `class FieldsState(NamedTuple)`, which ends with

```python
    digest: Optional[str]
    sent: bool
    outcome: Optional[str]
    attempts: int
```

insert:

```python


class ExtractionState(NamedTuple):
    """The re-extraction columns of one ``documents`` row."""

    stamp: Optional[str]
    text_sha256: Optional[str]
    resend_reason: Optional[str]
    resend_attempts: int
```

3.3 In `_connect`, replace `self._ensure_fields_columns()` with `self._ensure_columns()`.

3.4 Replace

```python
    def _ensure_fields_columns(self) -> None:
        conn = self._conn
        assert conn is not None
        present = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        for name, decl in _FIELDS_COLUMNS:
```

with

```python
    def _ensure_columns(self) -> None:
        """Add the fields and the re-extraction columns to an older file."""
        conn = self._conn
        assert conn is not None
        present = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        for name, decl in _FIELDS_COLUMNS + _EXTRACTION_COLUMNS:
```

The rest of the method (the ALTER, the duplicate-column guard, the commit) stays as it is.

3.5 Insert directly before `    def list_tracked_paths(self) -> list[str]:`:

```python
    # ----- re-extraction (spec L6) ---------------------------------------------

    def extraction_state(self, relative_path: str) -> Optional[ExtractionState]:
        conn = self._connect()
        row = conn.execute(
            "SELECT extraction_stamp, text_sha256, resend_reason, resend_attempts "
            "FROM documents WHERE relative_path = ?",
            (relative_path,),
        ).fetchone()
        if not row:
            return None
        return ExtractionState(row[0], row[1], row[2], int(row[3] or 0))

    def set_extraction(
        self, relative_path: str, stamp: Optional[str], text_sha: Optional[str]
    ) -> bool:
        """After an upload: the stamp of the extraction that produced it and
        the hash of what it carried; the row leaves the re-extraction queue.
        Existing rows only; False when the path is not tracked."""
        conn = self._connect()
        cur = conn.execute(
            "UPDATE documents SET extraction_stamp = ?, text_sha256 = ?, "
            "resend_reason = NULL, resend_attempts = 0 WHERE relative_path = ?",
            (stamp, text_sha, relative_path),
        )
        conn.commit()
        return bool(cur.rowcount)

    def set_extraction_stamp(self, relative_path: str, stamp: str) -> bool:
        """The current extraction read the file and nothing was uploaded --
        the upload would not change, or the file is unconvertible: the stamp
        moves on, the stored hash stays, the row leaves the queue."""
        conn = self._connect()
        cur = conn.execute(
            "UPDATE documents SET extraction_stamp = ?, resend_reason = NULL, "
            "resend_attempts = 0 WHERE relative_path = ?",
            (stamp, relative_path),
        )
        conn.commit()
        return bool(cur.rowcount)

    def count_extraction_outdated(self, stamp: str) -> int:
        """Tracked rows an older extraction produced: another stamp, or none
        (synced before stamps existed). Counts only, for ``/sync/status``."""
        conn = self._connect()
        row = conn.execute(
            "SELECT COUNT(*) FROM documents "
            "WHERE extraction_stamp IS NULL OR extraction_stamp != ?",
            (stamp,),
        ).fetchone()
        return int(row[0]) if row else 0

    def requeue_reextract(self, stamp: str) -> int:
        """Queue every outdated row for re-extraction (``resend_reason``);
        returns how many were newly queued -- rows already waiting are not
        counted twice."""
        conn = self._connect()
        cur = conn.execute(
            "UPDATE documents SET resend_reason = ?, resend_attempts = 0 "
            "WHERE (extraction_stamp IS NULL OR extraction_stamp != ?) "
            "AND resend_reason IS NULL",
            (RESEND_REEXTRACT, stamp),
        )
        conn.commit()
        return int(cur.rowcount or 0)

    def count_reextract_queued(self) -> int:
        conn = self._connect()
        row = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE resend_reason = ?", (RESEND_REEXTRACT,)
        ).fetchone()
        return int(row[0]) if row else 0

    def load_reextract_queue(self) -> dict[str, Optional[str]]:
        """Queued rows and their stored ``text_sha256`` (None: uploaded
        before hashes existed), read once per scan cycle."""
        conn = self._connect()
        cur = conn.execute(
            "SELECT relative_path, text_sha256 FROM documents WHERE resend_reason = ?",
            (RESEND_REEXTRACT,),
        )
        return {row[0]: row[1] for row in cur}

    def count_reextract_failure(self, relative_path: str, max_attempts: int) -> bool:
        """One more failed re-extraction of a queued row. At ``max_attempts``
        the row leaves the queue -- still outdated, so the next request
        queues it again. True when it left."""
        conn = self._connect()
        conn.execute(
            "UPDATE documents SET resend_attempts = resend_attempts + 1 "
            "WHERE relative_path = ? AND resend_reason = ?",
            (relative_path, RESEND_REEXTRACT),
        )
        row = conn.execute(
            "SELECT resend_attempts FROM documents WHERE relative_path = ? AND resend_reason = ?",
            (relative_path, RESEND_REEXTRACT),
        ).fetchone()
        left = row is not None and int(row[0] or 0) >= max_attempts
        if left:
            conn.execute(
                "UPDATE documents SET resend_reason = NULL, resend_attempts = 0 "
                "WHERE relative_path = ?",
                (relative_path,),
            )
        conn.commit()
        return left

```

3.6 `KnovasConnector/src/sync/sync_state.py`: replace

```python
from sync.sync_state_db import FieldsState, SyncStateDatabase, json_state_path_to_db
```

with

```python
from sync.sync_state_db import (
    ExtractionState,
    FieldsState,
    SyncStateDatabase,
    json_state_path_to_db,
)
```

3.7 Insert directly before `    def status_for(self, relative_path: str, mtime_iso: str, size_bytes: int) -> str:`:

```python
    # ----- re-extraction (spec L6) ---------------------------------------------

    def extraction_state(self, relative_path: str) -> Optional[ExtractionState]:
        return self._db.extraction_state(relative_path)

    def set_extraction(
        self, relative_path: str, stamp: Optional[str], text_sha: Optional[str]
    ) -> bool:
        """After an upload: its extraction stamp and the hash of what it
        carried; the path leaves the re-extraction queue."""
        return self._db.set_extraction(relative_path, stamp, text_sha)

    def set_extraction_stamp(self, relative_path: str, stamp: str) -> bool:
        return self._db.set_extraction_stamp(relative_path, stamp)

    def record_reextract_unchanged(
        self, relative_path: str, stamp: str, partial: Optional[dict[str, Any]]
    ) -> None:
        """A re-extraction that would upload exactly what Knovas holds:
        nothing was sent. The stamp moves on, the path leaves the queue,
        and the partial note follows the NEW extraction -- a born-digital
        PDF an older release recorded partial is complete now and leaves
        the backfill list. Fingerprint and upload columns stay."""
        self._db.set_extraction_stamp(relative_path, stamp)
        if partial:
            self._db.set_partial(relative_path, dict(partial))
        else:
            self._db.clear_partial(relative_path)

    def count_extraction_outdated(self, stamp: str) -> int:
        return self._db.count_extraction_outdated(stamp)

    def requeue_reextract(self, stamp: str) -> int:
        """Queue every tracked path an older extraction produced (another
        stamp, or none); returns how many were newly queued."""
        return self._db.requeue_reextract(stamp)

    def count_reextract_queued(self) -> int:
        return self._db.count_reextract_queued()

    def load_reextract_queue(self) -> dict[str, Optional[str]]:
        return self._db.load_reextract_queue()

    def count_reextract_failure(self, relative_path: str, max_attempts: int) -> bool:
        return self._db.count_reextract_failure(relative_path, max_attempts)

```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_sync_state_db_reextract_migration.py`
Expected: PASS. The neighbouring suite `rc-pytest tests/unit/test_sync_state_db_doc_fields_migration.py tests/unit/test_sync_state_db.py tests/unit/test_sync_state.py tests/unit/test_subfolder_queue.py` still passes.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/sync_state_db.py KnovasConnector/src/sync/sync_state.py \
  KnovasConnector/tests/unit/test_sync_state_db_reextract_migration.py
git commit -F - <<'EOF'
rc: state DB columns for re-extraction (L6)

documents gains extraction_stamp and text_sha256 (spec L6) plus
resend_reason and resend_attempts, added in place like the doc-fields
columns. The fields side queue has no marker of its own: it is derived
from the fields digest. Reusing it would turn a re-extraction into a
fields re-upload that only works with RC_DOC_FIELDS on, so re-extraction
gets its own marker. Rows synced before have no stamp and count as an
older extraction; the upgrade queues nothing by itself. An unchanged
re-extraction moves the stamp and lets the partial note follow the new
extraction.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task REX-3a: Uploader reports stamp and text hash, and skips an unchanged re-extraction

**Files:**
- Modify: `KnovasConnector/src/sync/knovas_uploader.py` (imports, `UploadResult`, `SemantixUploader.upload_file`)
- Test: `KnovasConnector/tests/unit/test_uploader_reextract.py`

**Interfaces:**
- Consumes: REX-1 `current_extraction_stamp()`, `fields_values_digest()`, `upload_text_sha256()`.
- Produces:
  - `UploadResult.text_sha256: Optional[str] = None` and `UploadResult.extraction_stamp: Optional[str] = None`, both set on `status="ok"` and on the new `status="unchanged"`.
  - `SemantixUploader.upload_file(file_path, relative_path, sync_body, access_groups=(), *, source=None, previous_fields_sent=False, unchanged_text_sha256: Optional[str] = None) -> UploadResult`. When the computed hash equals `unchanged_text_sha256`, no request is made and the result is `unchanged` (`transmission_key_id=None`, `ingestion_requests=0`, `partial` as extracted).

- [ ] **Step 1: Write the failing test**

```python
"""The uploader's side of re-extraction (spec L6).

Every upload reports the extraction stamp and the sha256 of what it
carried. Given the hash of the last upload (``unchanged_text_sha256``) it
re-extracts, compares, and sends nothing -- no init, no part, no billing --
when they match. The HTTP layer is ``sync.knovas_uploader.requests.request``
answered by a scripted server; extraction runs in-process.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import pytest
import requests

from sync.doc_fields_payload import spec_from_source
from sync.extraction_stamp import current_extraction_stamp
from sync.knovas_uploader import SemantixUploader

INIT = "/secured/init_document_transmission"
PART = "/secured/transmit_document_part"
BODY = {"ingestion": {"identifier_prefix": "rc-sync"}}
REL = "Rechnung_17.txt"


def _response(status: int, body: Any = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = b"" if body is None else json.dumps(body).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    return resp


class Server:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Optional[dict]]] = []

    @property
    def inits(self) -> list[dict]:
        return [body for _, path, body in self.calls if path == INIT]

    def __call__(self, method: str, url: str, json: Optional[dict] = None, **_: Any):
        path = "/" + url.split("/", 3)[3]
        self.calls.append((method, path, json))
        if path == INIT:
            return _response(201, {"status": "success", "transmission_key_id": "tk-1"})
        if path == PART:
            return _response(200, {"status": "success", "transmission_complete": True})
        return _response(404, {"status": "error", "error_code": "HTTP_404"})


@pytest.fixture
def env(tmp_path, monkeypatch):
    """In-process extraction, no sidecar, a generous ingest limiter, no
    backoff sleeps, and the scripted server."""
    from config import load_config, reset_config
    from sync.ingest_rate_limit import configure

    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "0")
    monkeypatch.delenv("RC_DOC_FIELDS", raising=False)
    reset_config()
    load_config(validate=False, force_reload=True)
    configure(60000, 1000)
    monkeypatch.setattr("sync.knovas_uploader.write_context_sidecar", lambda *a, **k: None)
    monkeypatch.setattr("sync.knovas_uploader.time.sleep", lambda s: None)
    server = Server()
    monkeypatch.setattr("sync.knovas_uploader.requests.request", server)
    path = tmp_path / REL
    path.write_text("Rechnung fuer Beratung", encoding="utf-8")
    yield server, path
    reset_config()


def test_an_upload_reports_its_stamp_and_the_hash_of_what_it_carried(env):
    server, path = env
    up = SemantixUploader().upload_file(path, REL, BODY)
    assert up.status == "ok" and len(server.inits) == 1
    assert up.extraction_stamp == current_extraction_stamp()
    assert re.fullmatch(r"[0-9a-f]{64}", up.text_sha256)


def test_the_same_hash_sends_nothing(env):
    server, path = env
    first = SemantixUploader().upload_file(path, REL, BODY)
    server.calls.clear()
    again = SemantixUploader().upload_file(path, REL, BODY,
                                           unchanged_text_sha256=first.text_sha256)
    assert server.calls == [], "no init, no part: nothing is billed"
    assert again.status == "unchanged"
    assert (again.transmission_key_id, again.ingestion_requests, again.error) == (None, 0, None)
    assert (again.text_sha256, again.extraction_stamp, again.parts) == (
        first.text_sha256, first.extraction_stamp, first.parts)


def test_another_hash_uploads_as_always(env):
    server, path = env
    up = SemantixUploader().upload_file(path, REL, BODY, unchanged_text_sha256="0" * 64)
    assert up.status == "ok" and len(server.inits) == 1 and up.transmission_key_id == "tk-1"


def test_field_values_and_the_description_are_part_of_it(env):
    server, path = env
    invoice = spec_from_source({"path": "/data", "fields": {"doc_type": "invoice"}})
    contract = spec_from_source({"path": "/data", "fields": {"doc_type": "contract"}})
    first = SemantixUploader().upload_file(path, REL, BODY, source=invoice)
    other_fields = SemantixUploader().upload_file(path, REL, BODY, source=contract,
                                                  unchanged_text_sha256=first.text_sha256)
    assert other_fields.status == "ok"
    described = {"ingestion": {"identifier_prefix": "rc-sync", "description": "Mandat 17"}}
    other_description = SemantixUploader().upload_file(path, REL, described, source=invoice,
                                                       unchanged_text_sha256=first.text_sha256)
    assert other_description.status == "ok"
    same = SemantixUploader().upload_file(path, REL, BODY, source=invoice,
                                          unchanged_text_sha256=first.text_sha256)
    assert same.status == "unchanged"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_uploader_reextract.py`
Expected: FAIL — `AttributeError: 'UploadResult' object has no attribute 'extraction_stamp'` and `TypeError: SemantixUploader.upload_file() got an unexpected keyword argument 'unchanged_text_sha256'`

- [ ] **Step 3: Write minimal implementation**

3.1 `KnovasConnector/src/sync/knovas_uploader.py`: directly after the `from sync.doc_fields_payload import (... refused_outcome,\n)` block, add:

```python
from sync.extraction_stamp import (
    current_extraction_stamp,
    fields_values_digest,
    upload_text_sha256,
)
```

3.2 In `UploadResult`, after

```python
    #: Values left out of ``fields`` before sending, by reason (counts).
    fields_dropped: dict[str, int] = field(default_factory=dict)
```

add:

```python
    #: sha256 of what the upload carries to the index
    #: (``extraction_stamp.upload_text_sha256``) and the stamp of the
    #: extraction that produced it (spec L6). Set on ``ok`` and on
    #: ``unchanged`` -- a re-extraction whose hash matched
    #: ``unchanged_text_sha256``, for which no request was made.
    text_sha256: Optional[str] = None
    extraction_stamp: Optional[str] = None
```

3.3 Replace the signature and docstring

```python
        *,
        source: Optional[SourceSpec] = None,
        previous_fields_sent: bool = False,
    ) -> UploadResult:
        """Extract, init and transmit one file.

        ``source`` is the governing source's ``SourceSpec``: its access
        groups win over ``access_groups``, and its fields configuration
        becomes the init ``fields`` (spec 3.6). ``previous_fields_sent``
        (from the state row) makes an empty payload a ``{}`` clear. Without
        ``source`` the init body is exactly what it was before fields.
        """
```

with

```python
        *,
        source: Optional[SourceSpec] = None,
        previous_fields_sent: bool = False,
        unchanged_text_sha256: Optional[str] = None,
    ) -> UploadResult:
        """Extract, init and transmit one file.

        ``source`` is the governing source's ``SourceSpec``: its access
        groups win over ``access_groups``, and its fields configuration
        becomes the init ``fields`` (spec 3.6). ``previous_fields_sent``
        (from the state row) makes an empty payload a ``{}`` clear. Without
        ``source`` the init body is exactly what it was before fields.

        An ``ok`` result carries the extraction stamp and ``text_sha256``,
        the hash of what the upload carried (spec L6). A re-extraction
        passes the hash of the last upload as ``unchanged_text_sha256``:
        when the new one matches, no request is made and the status is
        ``unchanged``.
        """
```

3.4 Replace

```python
        if fields_on:
            payload = assemble(relative_path, source, doc.source_metadata, ext)
            dropped = {k: int(v) for k, v in payload.dropped.items() if v}
            doc_fields_metrics.record_dropped(dropped)
            fields_value = fields_to_send(payload, previous_fields_sent)
            fields_digest = config_digest(relative_path, source)
            if fields_value is not None:
                init_body["fields"] = fields_value

        fields_outcome: Optional[FieldsOutcome] = None
```

with

```python
        if fields_on:
            payload = assemble(relative_path, source, doc.source_metadata, ext)
            dropped = {k: int(v) for k, v in payload.dropped.items() if v}
            fields_value = fields_to_send(payload, previous_fields_sent)
            fields_digest = config_digest(relative_path, source)
            if fields_value is not None:
                init_body["fields"] = fields_value

        # What this upload carries to the index, and which extraction made it
        # (spec L6). A re-extraction that would carry exactly what Knovas
        # holds sends nothing: no init, no part, nothing billed.
        stamp = current_extraction_stamp()
        text_sha256 = upload_text_sha256(
            parts,
            fields_values_digest(fields_value),
            title=init_body["title"],
            description=init_body.get("description"),
        )
        if unchanged_text_sha256 is not None and text_sha256 == unchanged_text_sha256:
            return UploadResult(
                relative_path=relative_path,
                transmission_key_id=None,
                parts=part_count,
                status="unchanged",
                ingestion_requests=0,
                partial=partial,
                text_sha256=text_sha256,
                extraction_stamp=stamp,
            )
        # Dropped values are counted only for what is actually sent.
        doc_fields_metrics.record_dropped(dropped)

        fields_outcome: Optional[FieldsOutcome] = None
```

3.5 Replace the final return of `upload_file`

```python
            partial=partial,
            fields=fields_outcome,
            fields_dropped=dropped,
        )
```

with

```python
            partial=partial,
            fields=fields_outcome,
            fields_dropped=dropped,
            text_sha256=text_sha256,
            extraction_stamp=stamp,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_uploader_reextract.py`
Expected: PASS. The neighbouring suites `rc-pytest tests/unit/test_knovas_uploader.py tests/unit/test_uploader_doc_fields.py tests/test_sync_access_groups.py tests/contract/test_doc_fields_against_mock.py` still pass; init bodies are byte-identical.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/knovas_uploader.py KnovasConnector/tests/unit/test_uploader_reextract.py
git commit -F - <<'EOF'
rc: uploader reports stamp and text hash, skips unchanged re-extractions (L6)

Every ok upload now reports the extraction stamp and the sha256 of what it
carried: the parts with their page and sentence numbers, the field values,
the title and the description. Given the hash of the last upload, a
re-extraction compares first and makes no request when nothing changed,
so it is not billed. Dropped field values are counted only for what is
sent.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task REX-3b: Record the stamp and text hash of every upload

**Files:**
- Modify: `KnovasConnector/src/sync/sync_executor.py` (import; `record_upload_outcome`: docstring, `ok` branch, unconvertible branch)
- Test: `KnovasConnector/tests/unit/test_sync_executor_reextract.py` (new; harness reused by REX-3c)

**Interfaces:**
- Consumes:
  - REX-2: `SyncStateStore.set_extraction`, `set_extraction_stamp`, `extraction_state`, `count_extraction_outdated`.
  - REX-3a: `UploadResult.extraction_stamp`, `UploadResult.text_sha256`.
- Produces:
  - Every `ok` upload, incremental or full mode (full mode: existing rows only), stores stamp + hash and clears the queue marker.
  - A file recorded `skip:unconvertible` gets the current stamp.

- [ ] **Step 1: Write the failing test**

Create `KnovasConnector/tests/unit/test_sync_executor_reextract.py`:

```python
"""Re-extraction through the sync cycle (spec L6).

Every upload records the extraction stamp and the sha256 of what it
carried. A row ``POST /sync/reextract/requeue`` queued is re-extracted on a
later cycle -- partial first, then .pdf, .docx, mail, the rest; at most
RC_REEXTRACT_PER_CYCLE, after new, modified and fields work -- and uploaded
in place only when its upload would change. Whole cycles with the real
uploader against a scripted Secure API; extraction runs in-process on small
text files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import pytest
import requests

from sync.extraction_stamp import current_extraction_stamp
from sync.sync_state import SyncStateStore

INIT = "/secured/init_document_transmission"
PART = "/secured/transmit_document_part"
DELETE = "/secured/delete_information_object"
FIXED_MTIME = 1_700_000_000
FIXED_MTIME_ISO = "2023-11-14T22:13:20Z"
TEXT = "Rechnung fuer Beratung"


def _response(status: int, body: Any = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = b"" if body is None else json.dumps(body).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    return resp


class Server:
    """A scripted Secure API. ``fail_init``: answer every init with that status."""

    def __init__(self) -> None:
        self.inits: list[dict] = []
        self.fail_init: Optional[int] = None

    def rels(self) -> list[str]:
        return [body["path"] for body in self.inits]

    def __call__(self, method: str, url: str, json: Optional[dict] = None, **_: Any):
        path = "/" + url.split("/", 3)[3]
        if path == INIT:
            self.inits.append(json or {})
            if self.fail_init is not None:
                return _response(self.fail_init, {"status": "error"})
            return _response(201, {"status": "success",
                                   "transmission_key_id": f"tk-{len(self.inits)}"})
        if path in (PART, DELETE):
            return _response(200, {"status": "success", "transmission_complete": True})
        return _response(404, {"status": "error", "error_code": "HTTP_404"})


class Harness:
    def __init__(self, root: Path, state_path: Path, monkeypatch) -> None:
        self.root = root
        self.state_path = state_path
        self.monkeypatch = monkeypatch
        self.server = Server()

    def write(self, rel: str, text: str = TEXT) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (FIXED_MTIME, FIXED_MTIME))
        return path

    def body(self, mode: str = "incremental", **source: Any) -> dict:
        return {
            "mode": mode,
            "sources": [{"path": str(self.root), "recursive": True, **source}],
            "filters": {"include_globs": ["*.txt", "*.md", "*.pdf", "*.docx", "*.eml", "*.msg"]},
            "ingestion": {"identifier_prefix": "rc-sync"},
        }

    def run(self, body: dict, *, sync_config: Optional[dict] = None):
        from sync.knovas_uploader import SemantixUploader
        from sync.sync_executor import run_sync_work

        self.monkeypatch.setattr("sync.knovas_uploader.requests.request", self.server)
        self.server.inits.clear()
        return run_sync_work(body, SemantixUploader(), sync_config=sync_config)

    def state(self) -> SyncStateStore:
        return SyncStateStore(str(self.state_path))

    def extraction(self, rel: str):
        store = self.state()
        try:
            return store.extraction_state(rel)
        finally:
            store.close()

    def outdated(self) -> int:
        store = self.state()
        try:
            return store.count_extraction_outdated(current_extraction_stamp())
        finally:
            store.close()

    def upgrade(self) -> None:
        """A newer knovas-extract: every stamp so far is an older extraction."""
        self.monkeypatch.setattr("sync.extraction_stamp._knovas_extract_version",
                                 lambda: "99.0.0")

    def env(self, **values: str) -> None:
        from config import load_config, reset_config

        for key, value in values.items():
            self.monkeypatch.setenv(key, value)
        reset_config()
        load_config(validate=False, force_reload=True)


@pytest.fixture
def rc(tmp_path, monkeypatch):
    from config import load_config, reset_config
    from sync.ingest_rate_limit import configure

    root = tmp_path / "share"
    root.mkdir()
    state_path = tmp_path / "state" / ".rc-sync-state.json"
    monkeypatch.setenv("RC_WATCH_ROOTS", str(root))
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("SEMANTIX_CERT_AUTO_RENEW_ENABLED", "false")
    monkeypatch.delenv("M365_FOLDER_URL", raising=False)
    for key in ("RC_DOC_FIELDS", "RC_FIELDS_REUPLOAD_PER_CYCLE", "RC_REEXTRACT_PER_CYCLE",
                "RC_UPLOAD_ORDER", "RC_PDF_TEXT_MODE"):
        monkeypatch.delenv(key, raising=False)
    reset_config()
    load_config(validate=False, force_reload=True)
    configure(60000, 1000)
    monkeypatch.setattr("sync.knovas_uploader.write_context_sidecar", lambda *a, **k: None)
    monkeypatch.setattr("sync.knovas_uploader.time.sleep", lambda s: None)
    yield Harness(root, state_path, monkeypatch)
    reset_config()


class TestEveryUploadIsStamped:
    def test_an_upload_stores_the_stamp_and_the_hash(self, rc):
        rc.write("a.txt")
        assert rc.run(rc.body()).files_uploaded == 1
        state = rc.extraction("a.txt")
        assert state.stamp == current_extraction_stamp()
        assert state.text_sha256 is not None and len(state.text_sha256) == 64
        assert state.resend_reason is None and rc.outdated() == 0

    def test_rows_synced_before_this_release_are_outdated_and_nothing_is_resent(self, rc):
        rc.write("a.txt")
        store = rc.state()
        try:
            store.record_upload("a.txt", FIXED_MTIME_ISO, len(TEXT), "tk-old")
        finally:
            store.close()
        assert rc.outdated() == 1
        assert rc.run(rc.body()).files_uploaded == 0, "outdated is no reason to upload by itself"

    def test_a_partial_upload_is_stamped_too(self, rc, monkeypatch):
        monkeypatch.setattr("sync.knovas_uploader.partial_note_for",
                            lambda doc, expect_ocr: {"ocr_pages_skipped": 2, "ocr_pages": 5})
        rc.write("scan.txt")
        assert rc.run(rc.body()).files_partial == 1
        assert rc.extraction("scan.txt").stamp == current_extraction_stamp()

    def test_a_new_unconvertible_file_carries_the_current_stamp(self, rc):
        rc.write("leer.txt", text="")
        rc.run(rc.body())
        assert rc.server.inits == []
        assert rc.extraction("leer.txt").stamp == current_extraction_stamp()
        assert rc.outdated() == 0, "the current extractor's verdict, not an older extraction"

    def test_a_changed_setting_makes_every_row_outdated(self, rc):
        rc.write("a.txt")
        rc.write("b.txt")
        rc.run(rc.body())
        assert rc.outdated() == 0
        rc.env(RC_PDF_TEXT_MODE="plain")
        assert rc.outdated() == 2

    def test_full_mode_stamps_existing_rows_only(self, rc):
        rc.write("a.txt")
        rc.run(rc.body())
        store = rc.state()
        try:
            store.set_extraction("a.txt", None, None)
        finally:
            store.close()
        rc.write("neu.txt")
        assert rc.run(rc.body(mode="full")).files_uploaded == 2
        assert rc.extraction("a.txt").stamp == current_extraction_stamp()
        assert rc.extraction("neu.txt") is None, "full mode creates no rows"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_sync_executor_reextract.py`
Expected: FAIL — `assert None == '<16 hex>'` in `test_an_upload_stores_the_stamp_and_the_hash`, `test_a_partial_upload_is_stamped_too`, `test_a_new_unconvertible_file_carries_the_current_stamp` and `test_full_mode_stamps_existing_rows_only`, and `assert 2 == 0` in `test_a_changed_setting_makes_every_row_outdated`.

- [ ] **Step 3: Write minimal implementation**

3.1 `KnovasConnector/src/sync/sync_executor.py`: directly after the `from sync.document_text import (... is_unconvertible_error,\n)` block, add:

```python
from sync.extraction_stamp import current_extraction_stamp
```

3.2 In the docstring of `record_upload_outcome`, after the paragraph that ends

```
    extraction retry counter and is never recorded partial for exhausted
    retries: its text is already complete at Knovas.
```

insert:

```

    Re-extraction (spec L6): every ``ok`` upload also stores its extraction
    stamp and the hash of what it carried, and the row leaves the
    re-extraction queue; a file found unconvertible takes the current
    stamp -- the current extractor's verdict, not an older extraction.
```

3.3 Replace

```python
        else:
            if incremental and key:
                state.record_upload(relative_path, mtime_iso, size_bytes, key, fields=record)
            elif record is not None and not incremental:
                state.update_fields(relative_path, record)
            outcome = "synced"
        if record is not None:
```

with

```python
        else:
            if incremental and key:
                state.record_upload(relative_path, mtime_iso, size_bytes, key, fields=record)
            elif record is not None and not incremental:
                state.update_fields(relative_path, record)
            outcome = "synced"
        if upload.extraction_stamp is not None:
            # Which extraction produced this upload and what it carried
            # (spec L6); the row leaves the re-extraction queue. Full mode
            # updates rows that exist only, like the fields columns.
            state.set_extraction(relative_path, upload.extraction_stamp, upload.text_sha256)
        if record is not None:
```

3.4 Replace

```python
    if _should_skip_failed_upload(upload, mode):
        state.record_skip(
            relative_path, mtime_iso, size_bytes, reason="unconvertible",
            fields=FieldsRecord(digest, OUTCOME_NONE) if fields_on else None,
        )
        ocr_metrics.SKIP_UNCONVERTIBLE.inc()
```

with

```python
    if _should_skip_failed_upload(upload, mode):
        state.record_skip(
            relative_path, mtime_iso, size_bytes, reason="unconvertible",
            fields=FieldsRecord(digest, OUTCOME_NONE) if fields_on else None,
        )
        # The current extractor's verdict: not an older extraction (spec L6).
        state.set_extraction_stamp(relative_path, current_extraction_stamp())
        ocr_metrics.SKIP_UNCONVERTIBLE.inc()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_sync_executor_reextract.py`
Expected: PASS. The neighbouring suites `rc-pytest tests/unit/test_sync_executor.py tests/unit/test_sync_executor_doc_fields.py tests/unit/test_sync_executor_partial.py tests/unit/test_backfill_partial_ocr.py` still pass.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/sync_executor.py KnovasConnector/tests/unit/test_sync_executor_reextract.py
git commit -F - <<'EOF'
rc: record the extraction stamp and text hash of every upload (L6)

record_upload_outcome stores the stamp and the uploaded-text hash for
every ok upload, including partial uploads, full mode (existing rows
only) and the partial backfill, which calls the same function. A file
found unconvertible takes the current stamp: it is the current
extractor's verdict, so a corrupt file is not reported as "older
extraction" forever.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task REX-3c: Bounded re-extraction side queue (`RC_REEXTRACT_PER_CYCLE`)

**Files:**
- Modify: `KnovasConnector/src/config.py` (`AppConfig`, constants, `_reextract_config_problems`, `load_config`, `reextract_per_cycle`)
- Modify: `KnovasConnector/src/sync/sync_executor.py` (imports, `REEXTRACT_MAX_ATTEMPTS`, `SyncRunResult`, `_reextract_rank`, `record_upload_outcome`, `_ScanPlan`, `plan_sync_cycle`, `run_sync_work`)
- Test: `KnovasConnector/tests/unit/test_sync_executor_reextract.py` (append)

**Interfaces:**
- Consumes:
  - REX-2: `load_reextract_queue`, `partial_paths`, `record_reextract_unchanged`, `set_extraction_stamp`, `count_reextract_failure`.
  - REX-3a: `upload_file(..., unchanged_text_sha256=...)` and the `"unchanged"` status.
- Produces:
  - Config: `RC_REEXTRACT_PER_CYCLE` (default 100, range 1..10000; boot refuses values outside it, runtime clamps); `config.reextract_per_cycle() -> int`; `REEXTRACT_PER_CYCLE_DEFAULT`, `REEXTRACT_PER_CYCLE_RANGE`, `_reextract_config_problems() -> list[str]`.
  - `sync_executor.REEXTRACT_MAX_ATTEMPTS = 3`.
  - `_ScanPlan.reextract_queue: list[UploadItem]`, `_ScanPlan.reextract_text_sha: dict[str, Optional[str]]`, `_ScanPlan.reextract_reached: int`.
  - `SyncRunResult.reextract_uploaded / reextract_unchanged / reextract_failed / reextract_reached: int`.
  - `record_upload_outcome(..., reextract: bool = False)`, which may return `"unchanged"`.

- [ ] **Step 1: Write the failing test**

Add `import dataclasses` and `import logging` to the imports of `KnovasConnector/tests/unit/test_sync_executor_reextract.py`, then append:

```python
def _requeue(rc) -> int:
    store = rc.state()
    try:
        return store.requeue_reextract(current_extraction_stamp())
    finally:
        store.close()


def _forget_hash(rc, *rels: str) -> None:
    """As for a row uploaded before this release: no stamp, no hash."""
    store = rc.state()
    try:
        for rel in rels:
            store.set_extraction(rel, None, None)
    finally:
        store.close()


def _newer_extractor_reads(rc, suffix: str) -> None:
    """A newer extractor reads the same file into a different text."""
    from sync import knovas_uploader

    real = knovas_uploader.extract_document_guarded

    def newer(path, **kwargs):
        doc = real(path, **kwargs)
        return dataclasses.replace(doc, text=doc.text + suffix, sentences=None,
                                   sections=None, pages=None)

    rc.monkeypatch.setattr("sync.knovas_uploader.extract_document_guarded", newer)


class TestReextraction:
    def test_unchanged_text_is_not_uploaded_again(self, rc):
        rc.write("a.txt")
        rc.run(rc.body())
        sha = rc.extraction("a.txt").text_sha256
        rc.upgrade()
        assert rc.outdated() == 1
        assert _requeue(rc) == 1
        result = rc.run(rc.body())
        assert rc.server.inits == [], "nothing sent: no billing"
        assert (result.reextract_unchanged, result.reextract_uploaded, result.files_uploaded) == (1, 0, 0)
        assert result.transmissions == []
        assert rc.extraction("a.txt") == (current_extraction_stamp(), sha, None, 0)
        assert rc.outdated() == 0
        assert rc.run(rc.body()).reextract_reached == 0, "done: it never comes back"

    def test_changed_text_is_uploaded_in_place(self, rc):
        rc.write("a.txt")
        rc.run(rc.body())
        old = rc.extraction("a.txt").text_sha256
        rc.upgrade()
        _requeue(rc)
        _newer_extractor_reads(rc, " samt Tabelle")
        result = rc.run(rc.body())
        assert rc.server.rels() == ["a.txt"]
        assert rc.server.inits[0]["identifier"] == "rc-sync/a.txt", "same identifier: in place"
        assert result.reextract_uploaded == 1 and result.files_uploaded == 1
        state = rc.extraction("a.txt")
        assert state.stamp == current_extraction_stamp() and state.text_sha256 not in (None, old)
        assert rc.outdated() == 0 and rc.run(rc.body()).files_uploaded == 0

    def test_a_row_from_before_hashes_is_uploaded_once(self, rc):
        rc.write("a.txt")
        rc.run(rc.body())
        _forget_hash(rc, "a.txt")
        _requeue(rc)
        assert rc.run(rc.body()).reextract_uploaded == 1
        rc.upgrade()
        _requeue(rc)
        assert rc.run(rc.body()).reextract_unchanged == 1, "later upgrades send only what changed"

    def test_an_unchanged_re_extraction_follows_the_new_partial_note(self, rc):
        """A born-digital PDF an older release recorded partial: the newer
        extraction is complete and the text the same -- not re-sent, and
        off the backfill list."""
        rc.write("a.txt")
        rc.run(rc.body())
        store = rc.state()
        try:
            store.record_partial("a.txt", FIXED_MTIME_ISO, len(TEXT), "tk-1",
                                 {"reason": "ocr_backend_none"})
        finally:
            store.close()
        rc.upgrade()
        _requeue(rc)
        assert rc.run(rc.body()).reextract_unchanged == 1
        store = rc.state()
        try:
            assert store.partial_paths() == []
        finally:
            store.close()


class TestQueueOrderAndBounds:
    def test_partial_first_then_pdf_docx_mail_and_the_rest(self, rc):
        from sync.sync_executor import plan_sync_cycle

        names = ["z.txt", "c.msg", "b.eml", "d.docx", "e.pdf", "f.pdf", "notiz.md"]
        store = rc.state()
        try:
            for name in names:
                rc.write(name)
                store.record_upload(name, FIXED_MTIME_ISO, len(TEXT), "tk-old")
            store.record_partial("f.pdf", FIXED_MTIME_ISO, len(TEXT), "tk-old",
                                 {"ocr_pages_skipped": 1})
            assert store.requeue_reextract(current_extraction_stamp()) == 7
            plan = plan_sync_cycle(rc.body(), store)
        finally:
            store.close()
        order = [item[1] for item in plan.reextract_queue]
        assert order[:3] == ["f.pdf", "e.pdf", "d.docx"]
        assert set(order[3:5]) == {"b.eml", "c.msg"} and set(order[5:]) == {"notiz.md", "z.txt"}
        assert plan.reextract_reached == 7 and plan.upload_queue == []

    def test_the_bound_per_cycle(self, rc):
        rc.env(RC_REEXTRACT_PER_CYCLE="2")
        for i in range(5):
            rc.write(f"r{i}.txt")
        rc.run(rc.body())
        rc.upgrade()
        assert _requeue(rc) == 5
        cycles = [rc.run(rc.body()).reextract_unchanged for _ in range(4)]
        assert cycles == [2, 2, 1, 0]
        assert rc.outdated() == 0

    def test_on_top_of_new_work_and_within_max_files_per_cycle(self, rc):
        for i in range(3):
            rc.write(f"r{i}.txt")
        rc.run(rc.body())
        rc.upgrade()
        _requeue(rc)
        rc.write("Neu1.txt")
        rc.write("Neu2.txt")
        result = rc.run(rc.body(), sync_config={"max_files_per_cycle": 3})
        assert sorted(rc.server.rels()) == ["Neu1.txt", "Neu2.txt"], "new work first"
        assert result.files_uploaded == 2 and result.reextract_unchanged == 1
        assert result.reextract_reached == 3

    def test_processing_keeps_the_priority_not_small_first(self, rc):
        big = "Rechnung " * 600
        rc.write("gross.txt", text=big)
        rc.write("klein.txt")
        rc.run(rc.body())
        _forget_hash(rc, "gross.txt", "klein.txt")
        store = rc.state()
        try:
            store.record_partial("gross.txt", FIXED_MTIME_ISO, len(big), "tk-1",
                                 {"ocr_pages_skipped": 1})
            store.requeue_reextract(current_extraction_stamp())
        finally:
            store.close()
        rc.run(rc.body())
        assert rc.server.rels() == ["gross.txt", "klein.txt"]

    def test_a_fields_change_wins_and_its_upload_clears_the_mark(self, rc):
        rc.write("a.txt")
        rc.run(rc.body())
        rc.upgrade()
        _requeue(rc)
        result = rc.run(rc.body(fields={"doc_type": "invoice"}))
        assert result.document_sync.fields_changed == 1 and result.reextract_reached == 0
        assert rc.server.rels() == ["a.txt"]
        assert rc.server.inits[0]["fields"] == {"doc_type": "invoice"}
        assert rc.outdated() == 0 and rc.extraction("a.txt").resend_reason is None

    def test_a_sequential_subfolder_waits_for_its_re_extractions(self, rc, monkeypatch):
        from sync.subfolder_queue import SubfolderQueue

        calls = []
        original = SubfolderQueue.maybe_advance

        def spy(self, source_root, **kwargs):
            calls.append(kwargs)
            return original(self, source_root, **kwargs)

        monkeypatch.setattr(SubfolderQueue, "maybe_advance", spy)
        rc.write("A/r.txt")
        rc.write("B/r.txt")
        cfg = {"sequential_subfolders": True}
        first = rc.run(rc.body(), sync_config=cfg)
        assert first.subfolder_progress["current_subfolder"] == "A"
        rc.upgrade()
        assert _requeue(rc) == 1
        result = rc.run(rc.body(), sync_config=cfg)
        assert result.reextract_reached == 1 and calls[-1]["modified"] == 1
        assert result.subfolder_progress["current_subfolder"] == "A", "waits for the re-extraction"
        done = rc.run(rc.body(), sync_config=cfg)
        assert done.subfolder_progress["current_subfolder"] == "B"


class TestFailures:
    def test_a_failure_keeps_the_row_outdated_and_leaves_the_queue_after_three(self, rc):
        rc.write("a.txt")
        rc.run(rc.body())
        _forget_hash(rc, "a.txt")
        _requeue(rc)
        rc.server.fail_init = 500
        for attempt in (1, 2, 3):
            result = rc.run(rc.body())
            assert (result.reextract_failed, result.files_retry) == (1, 1), attempt
            assert rc.outdated() == 1
        assert rc.extraction("a.txt").resend_reason is None, "left the queue after 3 attempts"
        assert rc.run(rc.body()).reextract_reached == 0 and rc.server.inits == [], "no loop"
        store = rc.state()
        try:
            assert store.retry_count("a.txt") == 0 and store.partial_paths() == [], \
                "never an extraction retry, never partial: Knovas holds the last upload"
            assert store.load_fingerprints()["a.txt"] == (FIXED_MTIME_ISO, len(TEXT))
        finally:
            store.close()
        rc.server.fail_init = None
        assert _requeue(rc) == 1, "a new request queues it again"
        assert rc.run(rc.body()).reextract_uploaded == 1 and rc.outdated() == 0

    def test_an_unconvertible_file_takes_the_current_stamp_and_leaves_the_queue(self, rc):
        rc.write("leer.txt", text="")
        rc.run(rc.body())
        rc.upgrade()
        assert rc.outdated() == 1 and _requeue(rc) == 1
        result = rc.run(rc.body())
        assert rc.server.inits == [] and result.files_retry == 0
        state = rc.extraction("leer.txt")
        assert state.stamp == current_extraction_stamp() and state.resend_reason is None
        assert rc.outdated() == 0


class TestConfiguration:
    @pytest.fixture(autouse=True)
    def _fresh_config_afterwards(self):
        from config import reset_config

        yield
        reset_config()

    @staticmethod
    def _load(monkeypatch, raw: Optional[str]):
        from config import load_config, reset_config

        monkeypatch.delenv("RC_REEXTRACT_PER_CYCLE", raising=False)
        if raw is not None:
            monkeypatch.setenv("RC_REEXTRACT_PER_CYCLE", raw)
        reset_config()
        return load_config(validate=False, force_reload=True)

    @pytest.mark.parametrize("raw,expected", [(None, 100), ("250", 250), ("0", 1),
                                              ("99999", 10000), ("viele", 100)])
    def test_the_bound_is_clamped_at_runtime(self, monkeypatch, raw, expected):
        from config import reextract_per_cycle

        self._load(monkeypatch, raw)
        assert reextract_per_cycle() == expected

    def test_boot_refuses_what_it_would_have_to_guess(self, monkeypatch):
        from config import _reextract_config_problems, load_config

        self._load(monkeypatch, "0")
        assert _reextract_config_problems() == ["RC_REEXTRACT_PER_CYCLE must be between 1 and 10000"]
        self._load(monkeypatch, "viele")
        assert _reextract_config_problems() == ["RC_REEXTRACT_PER_CYCLE must be an integer"]
        with pytest.raises(SystemExit):
            load_config(validate=True, force_reload=True)
        self._load(monkeypatch, "500")
        assert _reextract_config_problems() == []


def test_a_re_extraction_logs_counts_only(rc, caplog):
    secret = "Muster AG Geheimakte"
    rc.write(f"{secret}.txt", text=f"{secret}: Honorarnote")
    rc.run(rc.body())
    rc.upgrade()
    caplog.clear()
    caplog.set_level(logging.INFO)
    assert _requeue(rc) == 1
    result = rc.run(rc.body())
    assert result.reextract_unchanged == 1
    assert "reextract uploaded=0 unchanged=1 failed=0 reached=1" in caplog.text
    assert secret not in caplog.text and "Honorarnote" not in caplog.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/unit/test_sync_executor_reextract.py`
Expected: FAIL. `TestEveryUploadIsStamped` still passes. The new tests fail with:
- `AttributeError: 'SyncRunResult' object has no attribute 'reextract_unchanged'`
- `AttributeError: '_ScanPlan' object has no attribute 'reextract_queue'`
- `ImportError: cannot import name 'reextract_per_cycle' from 'config'`

- [ ] **Step 3: Write minimal implementation**

3.1 `KnovasConnector/src/config.py`: in `AppConfig`, after `rc_fields_reupload_max_attempts: int = 3`, add:

```python
    # Re-extraction after an extractor upgrade (spec L6): documents
    # re-extracted per cycle after POST /sync/reextract/requeue.
    rc_reextract_per_cycle: int = 100
```

3.2 After `FIELDS_REUPLOAD_MAX_ATTEMPTS_RANGE = (1, 100)`, add:

```python
REEXTRACT_PER_CYCLE_DEFAULT = 100
REEXTRACT_PER_CYCLE_RANGE = (1, 10000)
```

3.3 Directly after the function `_doc_fields_config_problems` (it ends with `    return problems`), add:

```python


def _reextract_config_problems() -> list[str]:
    """Refuse an unreadable re-extraction bound at boot, like the fields
    bound (RC_FIELDS_REUPLOAD_PER_CYCLE)."""
    raw = (os.environ.get("RC_REEXTRACT_PER_CYCLE") or "").strip()
    if not raw:
        return []
    try:
        value = int(raw)
    except ValueError:
        return ["RC_REEXTRACT_PER_CYCLE must be an integer"]
    low, high = REEXTRACT_PER_CYCLE_RANGE
    if not low <= value <= high:
        return [f"RC_REEXTRACT_PER_CYCLE must be between {low} and {high}"]
    return []
```

3.4 In `load_config`, after the block

```python
        doc_fields_problems = _doc_fields_config_problems()
        if doc_fields_problems:
            print("Document fields misconfigured:", file=sys.stderr)
            for problem in doc_fields_problems:
                print(f"  - {problem}", file=sys.stderr)
            sys.exit(1)
```

add:

```python
        reextract_problems = _reextract_config_problems()
        if reextract_problems:
            print("Re-extraction misconfigured:", file=sys.stderr)
            for problem in reextract_problems:
                print(f"  - {problem}", file=sys.stderr)
            sys.exit(1)
```

3.5 In the `AppConfig(...)` construction, after the `rc_fields_reupload_max_attempts=_bounded_int(...)` argument, add:

```python
        rc_reextract_per_cycle=_bounded_int(
            "RC_REEXTRACT_PER_CYCLE",
            REEXTRACT_PER_CYCLE_DEFAULT,
            REEXTRACT_PER_CYCLE_RANGE,
        ),
```

3.6 At the end of `config.py`, add:

```python


def reextract_per_cycle() -> int:
    """``RC_REEXTRACT_PER_CYCLE`` (default 100, 1-10000): documents
    re-extracted per cycle after ``POST /sync/reextract/requeue``, on top of
    the fields bound and within ``max_files_per_cycle`` (spec L6)."""
    return int(get_config().rc_reextract_per_cycle)
```

3.7 `KnovasConnector/src/sync/sync_executor.py`: add `import heapq` after `import fnmatch`. Add `reextract_per_cycle` to the `from config import ...` line, so that today's line

```python
from config import doc_fields_enabled, fields_reupload_max_attempts, fields_reupload_per_cycle
```

becomes

```python
from config import (
    doc_fields_enabled,
    fields_reupload_max_attempts,
    fields_reupload_per_cycle,
    reextract_per_cycle,
)
```

3.8 After `RETRIES_EXHAUSTED_NOTE = {"reason": "extract_retries_exhausted"}`, add:

```python

#: Failed re-extractions of one queued document before it leaves the queue
#: (spec L6). It stays outdated; the next ``POST /sync/reextract/requeue``
#: queues it again.
REEXTRACT_MAX_ATTEMPTS = 3
```

3.9 After the function `_ordered_upload_queue`, add:

```python


#: Re-extraction order after partial documents (spec L6): PDFs, Word files,
#: e-mails, then everything else.
_REEXTRACT_RANK = {".pdf": 1, ".docx": 2, ".eml": 3, ".msg": 3}
_REEXTRACT_RANK_REST = 4


def _reextract_rank(rel: str, partial: frozenset) -> int:
    """Partial documents first -- a newer extractor most likely completes
    them -- then by extension."""
    if rel in partial:
        return 0
    return _REEXTRACT_RANK.get(os.path.splitext(rel)[1].lower(), _REEXTRACT_RANK_REST)
```

3.10 In `SyncRunResult`, after `rate_limit: Optional[dict[str, Any]] = None`, add:

```python
    #: Re-extraction (spec L6), counts only: re-extracted and uploaded,
    #: re-extracted with an unchanged upload (nothing sent), failed; and the
    #: queued rows this cycle's scan reached, counted before the uploads like
    #: ``fields_changed`` (no idle backoff, no subfolder advance meanwhile).
    reextract_uploaded: int = 0
    reextract_unchanged: int = 0
    reextract_failed: int = 0
    reextract_reached: int = 0
```

3.11 `record_upload_outcome`: replace the signature lines

```python
    digest: Optional[str] = None,
    fields_reupload: bool = False,
    stats: Optional[DocFieldsCycle] = None,
) -> str:
```

with

```python
    digest: Optional[str] = None,
    fields_reupload: bool = False,
    reextract: bool = False,
    stats: Optional[DocFieldsCycle] = None,
) -> str:
```

Extend the docstring paragraph REX-3b added (the one ending `the current extractor's verdict, not an older extraction.`) with:

```
    ``"unchanged"``: a re-extraction (``reextract``) whose upload would
    carry exactly what Knovas holds -- nothing was sent, the stamp moves on
    and the partial note follows the new extraction. A failed re-extraction
    never records a skip or a partial and never touches the extraction
    retry counter (Knovas holds the last upload): unconvertible takes the
    current stamp and leaves the queue, anything else counts an attempt and
    leaves after ``REEXTRACT_MAX_ATTEMPTS``, still outdated.
```

Replace

```python
    incremental = mode == "incremental"
    fields_on = digest is not None
    if upload.status == "ok":
```

with

```python
    incremental = mode == "incremental"
    fields_on = digest is not None
    if upload.status == "unchanged":
        # A re-extraction whose upload would carry exactly what Knovas
        # holds: nothing was sent, nothing is billed. The stamp moves on and
        # the partial note follows the new extraction.
        state.record_reextract_unchanged(
            relative_path, upload.extraction_stamp or current_extraction_stamp(), upload.partial
        )
        return "unchanged"
    if upload.status == "ok":
```

Replace

```python
    error = upload.error or "upload failed"
    if not incremental:
        return "retry"
    if _should_skip_failed_upload(upload, mode):
```

with

```python
    error = upload.error or "upload failed"
    if not incremental:
        return "retry"
    if reextract:
        # Knovas holds the document's last upload, so a failed re-extraction
        # is no content failure: no skip, no partial, no extraction retry.
        # Unconvertible is the current extractor's verdict -- the stamp moves
        # on and the row leaves the queue. Anything else is tried again next
        # cycle, at most REEXTRACT_MAX_ATTEMPTS times; the row stays outdated.
        if _should_skip_failed_upload(upload, mode):
            state.set_extraction_stamp(relative_path, current_extraction_stamp())
            return "skipped"
        state.count_reextract_failure(relative_path, REEXTRACT_MAX_ATTEMPTS)
        return "retry"
    if _should_skip_failed_upload(upload, mode):
```

3.12 `_ScanPlan`: after `template_errors: Counter = field(default_factory=Counter)`, add:

```python
    # Re-extractions (spec L6): at most RC_REEXTRACT_PER_CYCLE queued synced
    # rows in ``_reextract_rank`` order, only where the file cap leaves room
    # after the primary and the fields queue; uploaded last.
    reextract_queue: list[UploadItem] = field(default_factory=list)
    # The stored ``text_sha256`` per queued re-extraction (None: uploaded
    # before hashes existed -- such a row is uploaded whatever the text).
    reextract_text_sha: dict[str, Optional[str]] = field(default_factory=dict)
    # Queued rows the scan reached, whether or not they fit this cycle.
    reextract_reached: int = 0
```

3.13 `plan_sync_cycle`: at the end of its docstring, before the closing `"""` (after `the digests of the copies cannot alternate.`), add:

```

    Re-extraction (spec L6): a synced file whose row ``POST
    /sync/reextract/requeue`` queued is a candidate; the first
    RC_REEXTRACT_PER_CYCLE by ``_reextract_rank`` (scan order within a rank)
    form ``reextract_queue``, trimmed to what ``max_upload_files`` leaves
    after the primary and the fields queue.
```

Replace

```python
    governing: dict[str, SourceSpec] = {}
    rel_collisions = 0
```

with

```python
    governing: dict[str, SourceSpec] = {}
    # Re-extraction (spec L6): rows POST /sync/reextract/requeue queued
    # (``resend_reason``), with their stored text hash, read once per cycle.
    reextract_rows = state.load_reextract_queue() if incremental else {}
    partial_rows = frozenset(state.partial_paths()) if reextract_rows else frozenset()
    reextract_candidates: list[tuple[int, int, UploadItem, Optional[str], bool]] = []
    reextract_seen: set[str] = set()
    rel_collisions = 0
```

Replace

```python
            if queued and digest is not None:
                fields_digests[rel] = digest
                fields_sent[rel] = bool(fields_state is not None and fields_state.sent)

    if max_upload_files > 0:
        # Re-uploads only take what the cycle's file cap leaves after new
        # and modified work.
        del fields_queue[max(0, max_upload_files - len(upload_queue)):]
```

with

```python
            if queued and digest is not None:
                fields_digests[rel] = digest
                fields_sent[rel] = bool(fields_state is not None and fields_state.sent)
        elif status == "synced" and rel in reextract_rows and rel not in reextract_seen:
            # A queued re-extraction the scan reached; one per path (the
            # first source governs a duplicate, as it does for fields).
            reextract_seen.add(rel)
            reextract_candidates.append((
                _reextract_rank(rel, partial_rows),
                len(reextract_candidates),
                (abs_path, rel, mtime_iso, size_bytes, spec),
                digest,
                bool(fields_state is not None and fields_state.sent),
            ))

    reextract_queue: list[UploadItem] = []
    reextract_text_sha: dict[str, Optional[str]] = {}
    for _rank, _seq, queued_item, queued_digest, queued_sent in heapq.nsmallest(
        reextract_per_cycle(), reextract_candidates
    ):
        queued_rel = queued_item[1]
        reextract_queue.append(queued_item)
        reextract_text_sha[queued_rel] = reextract_rows.get(queued_rel)
        if queued_digest is not None:
            fields_digests[queued_rel] = queued_digest
            fields_sent[queued_rel] = queued_sent

    if max_upload_files > 0:
        # Re-uploads only take what the cycle's file cap leaves after new
        # and modified work; re-extractions what is left after both.
        del fields_queue[max(0, max_upload_files - len(upload_queue)):]
        del reextract_queue[max(0, max_upload_files - len(upload_queue) - len(fields_queue)):]
```

In the final `return _ScanPlan(...)`, after `template_errors=template_errors,`, add:

```python
        reextract_queue=reextract_queue,
        reextract_text_sha=reextract_text_sha,
        reextract_reached=len(reextract_candidates),
```

3.14 `run_sync_work`: replace

```python
        order = upload_order()
        work = [(item, False) for item in _ordered_upload_queue(plan.upload_queue, order)]
        work += [(item, True) for item in _ordered_upload_queue(plan.fields_queue, order)]
        requeue_checked = False
        for (abs_path, rel, mtime_iso, size_bytes, spec), fields_reupload in work:
            if should_stop():
```

with

```python
        order = upload_order()
        work = [(item, "new") for item in _ordered_upload_queue(plan.upload_queue, order)]
        work += [(item, "fields") for item in _ordered_upload_queue(plan.fields_queue, order)]
        # Last, in their own order (partial, .pdf, .docx, mail, the rest):
        # the re-extractions an administrator asked for (spec L6).
        work += [(item, "reextract") for item in plan.reextract_queue]
        requeue_checked = False
        for (abs_path, rel, mtime_iso, size_bytes, spec), kind in work:
            fields_reupload = kind == "fields"
            reextract = kind == "reextract"
            if should_stop():
```

Replace

```python
            upload_kwargs = fields_upload_kwargs(
                spec, fields_on=fields_on, previous_fields_sent=plan.fields_sent.get(rel, False)
            )
            try:
```

with

```python
            upload_kwargs = fields_upload_kwargs(
                spec, fields_on=fields_on, previous_fields_sent=plan.fields_sent.get(rel, False)
            )
            stored_sha = plan.reextract_text_sha.get(rel) if reextract else None
            if stored_sha:
                # Re-extract and compare: an upload that would carry what
                # Knovas holds is not sent. Without a stored hash (uploaded
                # before hashes existed) the document is uploaded as always.
                upload_kwargs["unchanged_text_sha256"] = stored_sha
            try:
```

Replace

```python
            outcome = record_upload_outcome(
                state, rel, mtime_iso, size_bytes, upload, mode,
                digest=digest, fields_reupload=fields_reupload, stats=stats,
            )
```

with

```python
            outcome = record_upload_outcome(
                state, rel, mtime_iso, size_bytes, upload, mode,
                digest=digest, fields_reupload=fields_reupload, reextract=reextract, stats=stats,
            )
            if reextract:
                if outcome == "unchanged":
                    result.reextract_unchanged += 1
                elif upload.status == "ok":
                    result.reextract_uploaded += 1
                else:
                    result.reextract_failed += 1
            if outcome == "unchanged":
                # Nothing was transmitted, so there is no transmission entry.
                continue
```

Replace

```python
        if stats is not None:
            # What a requeue request may queue until the next cycle: a row
```

with

```python
        result.reextract_reached = plan.reextract_reached
        if plan.reextract_queue:
            # Counts only: never a path.
            logger.info(
                "reextract uploaded=%d unchanged=%d failed=%d reached=%d",
                result.reextract_uploaded, result.reextract_unchanged,
                result.reextract_failed, plan.reextract_reached,
            )

        if stats is not None:
            # What a requeue request may queue until the next cycle: a row
```

Replace

```python
            # Fields re-uploads count as modified: a subfolder completes only
            # once they are done. A source skipped for a bad template was
            # not scanned at all and never advances.
            queue.maybe_advance(
                source_root,
                pending=ds.pending,
                modified=ds.modified + ds.fields_changed,
```

with

```python
            # Fields re-uploads count as modified: a subfolder completes only
            # once they are done -- and so do queued re-extractions (spec
            # L6). A source skipped for a bad template was not scanned at
            # all and never advances.
            queue.maybe_advance(
                source_root,
                pending=ds.pending,
                modified=ds.modified + ds.fields_changed + result.reextract_reached,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/unit/test_sync_executor_reextract.py`
Expected: PASS. The neighbouring suites `rc-pytest tests/unit/test_sync_executor.py tests/unit/test_sync_executor_doc_fields.py tests/unit/test_sync_executor_partial.py tests/unit/test_scan_resume.py tests/unit/test_sync_large_corpus.py tests/unit/test_backfill_partial_ocr.py` still pass. So does the full Connector suite `rc-pytest tests`, apart from the known Windows-only failures.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/config.py KnovasConnector/src/sync/sync_executor.py \
  KnovasConnector/tests/unit/test_sync_executor_reextract.py
git commit -F - <<'EOF'
rc: bounded re-extraction side queue, RC_REEXTRACT_PER_CYCLE (L6)

Rows queued for re-extraction that the scan reaches are re-extracted
after new, modified and fields work, at most RC_REEXTRACT_PER_CYCLE
(default 100, 1-10000) and within max_files_per_cycle. Partial documents
go first, then PDFs, Word files, e-mails and the rest. An upload that
would carry what Knovas holds is not sent; the stamp moves on and the
partial note follows the new extraction. A failure never records a skip
or a partial and never uses the extraction retries; after three attempts
the row leaves the queue, still outdated. Queued re-extractions hold a
sequential subfolder like fields_changed. Counts-only log line.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task REX-4: `POST /sync/reextract/requeue`, extraction counts in `/sync/status`, no idle backoff, docs

**Files:**
- Modify: `KnovasConnector/src/sync/sync_scheduler.py` (imports, `reextract_status`, `requeue_reextract`, `_pending_work`)
- Modify: `KnovasConnector/src/routes/sync_control.py` (imports, `sync_status`, new route `sync_reextract_requeue`)
- Modify: `KnovasConnector/docs/operations.md`, `KnovasConnector/docs/configuration.md`, `KnovasConnector/CHANGELOG.md`, `KnovasConnector/.env.example`, `knovas.env.example`, `docs/specifications.md`
- Test: `KnovasConnector/tests/integration/test_sync_reextract_routes.py`

**Interfaces:**
- Consumes:
  - REX-2: `count_extraction_outdated`, `count_reextract_queued`, `requeue_reextract`.
  - REX-3c: `SyncRunResult.reextract_reached`, `config.reextract_per_cycle()`.
  - PIN: `status["extraction"]` with `knovas_extract_version`, `pdf_text_mode`, `docx_text_mode`, `ocr_engine`.
- Produces:
  - `sync_scheduler.reextract_status() -> dict[str, int]` (`outdated`, `queued`, `per_cycle`)
  - `sync_scheduler.requeue_reextract() -> int`
  - `POST /sync/reextract/requeue`: no body read → `200 {"requeued": int}`. Same decorators as `/sync/doc-fields/requeue`.
  - `GET /sync/status["extraction"]` gains `outdated`, `queued`, `per_cycle`.

- [ ] **Step 1: Write the failing test**

```python
"""``POST /sync/reextract/requeue`` and the ``extraction`` counts of
``GET /sync/status`` (spec L6): the gate of the doc-fields requeue, counts
only -- never a path -- and a worker that does not idle while
re-extractions wait."""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from sync.extraction_stamp import current_extraction_stamp
from sync.sync_state import SyncStateStore

TS = "2026-10-01T00:00:00Z"
REL = "Muster AG/GJ 2024/Rechnung_17.pdf"


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    from config import load_config, reset_config

    path = tmp_path / "state" / ".rc-sync-state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(path))
    monkeypatch.delenv("RC_REEXTRACT_PER_CYCLE", raising=False)
    reset_config()
    load_config(validate=False, force_reload=True)
    return path


@pytest.fixture
def employee(rc_client, state_path, monkeypatch):
    from config import load_config, reset_config

    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
    reset_config()
    load_config(validate=False, force_reload=True)
    with patch("auth.knovas_verify_client.get_verify_client") as client:
        client.return_value.verify_operator.return_value = (True, "c", None)
        yield rc_client


def _seed(state_path, *rels: str) -> None:
    store = SyncStateStore(str(state_path))
    try:
        for rel in rels:
            store.record_upload(rel, TS, 1, "tk")
    finally:
        store.close()


def _extraction(client, headers) -> dict:
    return client.get("/sync/status", headers=headers).get_json()["extraction"]


def test_the_route_is_gated_like_the_doc_fields_requeue(rc_client):
    assert rc_client.post("/sync/reextract/requeue", json={}).status_code in (401, 403, 429)


def test_requeue_queues_every_outdated_document_once_and_wakes_the_worker(
    employee, auth_headers, state_path, caplog
):
    caplog.set_level(logging.INFO)
    _seed(state_path, REL, "b.docx")
    with patch("routes.sync_control.request_cycle_now") as wake:
        first = employee.post("/sync/reextract/requeue", json={}, headers=auth_headers)
        again = employee.post("/sync/reextract/requeue", json={}, headers=auth_headers)
    assert first.status_code == 200 and first.get_json() == {"requeued": 2}
    assert again.status_code == 200 and again.get_json() == {"requeued": 0}, \
        "already queued documents are not counted twice"
    assert wake.call_count == 1, "only a request that queued something wakes the worker"
    assert "reextract requeued=2" in caplog.text
    assert "Muster" not in caplog.text and "b.docx" not in caplog.text


def test_the_status_counts_outdated_and_queued_documents_and_the_bound(
    employee, auth_headers, state_path
):
    _seed(state_path, REL, "b.docx", "c.txt")
    store = SyncStateStore(str(state_path))
    try:
        store.set_extraction("c.txt", current_extraction_stamp(), "0" * 64)
    finally:
        store.close()
    block = _extraction(employee, auth_headers)
    assert (block["outdated"], block["queued"], block["per_cycle"]) == (2, 0, 100)
    assert {"knovas_extract_version", "pdf_text_mode", "docx_text_mode", "ocr_engine"} <= set(block), \
        "the counts join the block that names the extractor"
    employee.post("/sync/reextract/requeue", json={}, headers=auth_headers)
    block = _extraction(employee, auth_headers)
    assert (block["outdated"], block["queued"]) == (2, 2)
    text = employee.get("/sync/status", headers=auth_headers).get_data(as_text=True)
    assert "Muster" not in text and "b.docx" not in text


def test_the_bound_is_the_connectors_setting(employee, auth_headers, monkeypatch):
    from config import load_config, reset_config

    monkeypatch.setenv("RC_REEXTRACT_PER_CYCLE", "250")
    reset_config()
    load_config(validate=False, force_reload=True)
    assert _extraction(employee, auth_headers)["per_cycle"] == 250


def test_the_platform_principal_of_an_admin_may_requeue_and_a_member_may_not(
    rc_client, state_path, tmp_path, monkeypatch
):
    """The Platform reaches the route as the signed-in person, exactly as
    it reaches /sync/doc-fields/requeue."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from tests.test_platform_principal import TENANT, mint

    private = ed25519.Ed25519PrivateKey.generate()
    pub = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pem = tmp_path / "broker_ed25519.pub"
    pem.write_bytes(pub)
    monkeypatch.setenv("RC_PLATFORM_BROKER_PUBKEY_PATH", str(pem))
    monkeypatch.setenv("RC_CLIENT_ID", TENANT)
    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    resp = rc_client.post("/sync/reextract/requeue", json={},
                          headers={"X-Platform-Principal": mint(private, pub)})
    assert resp.status_code == 200 and resp.get_json() == {"requeued": 0}
    resp = rc_client.post("/sync/reextract/requeue", json={},
                          headers={"X-Platform-Principal": mint(private, pub, rol=["member"])})
    assert resp.status_code == 403


def test_queued_re_extractions_keep_the_worker_from_idling(monkeypatch):
    import sync.sync_scheduler as scheduler
    from sync.sync_executor import SyncRunResult
    from sync.sync_state import DocumentSyncSummary

    monkeypatch.setattr(scheduler, "_idle_scan_multiplier", 1)
    idle = SyncRunResult(files_scanned=10, document_sync=DocumentSyncSummary(total=10, synced=10))
    busy = SyncRunResult(files_scanned=10, document_sync=DocumentSyncSummary(total=10, synced=10),
                         reextract_reached=3)
    assert scheduler._pending_work(idle) == 0 and scheduler._pending_work(busy) == 3
    cfg = {"scan_interval_seconds": 60, "scan_interval_idle_max_seconds": 3600}
    assert scheduler._effective_scan_interval_seconds(cfg, idle) == 120, "idle: backs off"
    assert scheduler._effective_scan_interval_seconds(cfg, busy) == 60, "re-extracting: no backoff"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rc-pytest tests/integration/test_sync_reextract_routes.py`
Expected: FAIL. `assert 404 == 200`, because the route does not exist yet. `KeyError: 'outdated'` from the `extraction` block. `assert 0 == 3`, because `_pending_work` ignores `reextract_reached`.

- [ ] **Step 3: Write minimal implementation**

3.1 `KnovasConnector/src/sync/sync_scheduler.py`: add `reextract_per_cycle` to the config import, so that

```python
from config import doc_fields_enabled, fields_reupload_per_cycle, get_config
```

becomes

```python
from config import doc_fields_enabled, fields_reupload_per_cycle, get_config, reextract_per_cycle
```

Also add, after `from sync.default_sync_body import build_default_sync_body`:

```python
from sync.extraction_stamp import current_extraction_stamp
```

3.2 Directly after the function `requeue_doc_fields` (it ends with `    return count`), add:

```python


def reextract_status() -> dict[str, int]:
    """The re-extraction counts of GET /sync/status (spec L6), merged into
    its ``extraction`` block: ``outdated`` (tracked documents an older
    extraction produced -- another stamp, or none), ``queued`` (waiting for
    re-extraction) and ``per_cycle`` (RC_REEXTRACT_PER_CYCLE). Counts only."""
    from sync.sync_state import SyncStateStore

    store = SyncStateStore()
    try:
        outdated = store.count_extraction_outdated(current_extraction_stamp())
        queued = store.count_reextract_queued()
    finally:
        store.close()
    return {"outdated": outdated, "queued": queued, "per_cycle": reextract_per_cycle()}


def requeue_reextract() -> int:
    """POST /sync/reextract/requeue: queue every tracked document an older
    extraction produced; returns how many were newly queued. The next
    cycles re-extract them within RC_REEXTRACT_PER_CYCLE (spec L6)."""
    from sync.sync_state import SyncStateStore

    store = SyncStateStore()
    try:
        count = store.requeue_reextract(current_extraction_stamp())
    finally:
        store.close()
    logger.info("reextract requeued=%d", count)
    return count
```

3.3 Replace

```python
def _pending_work(result: SyncRunResult) -> int:
    """New, modified and fields-changed documents the last scan found."""
    ds = result.document_sync
    if ds is None:
        return 0
    return ds.pending + ds.modified + ds.fields_changed
```

with

```python
def _pending_work(result: SyncRunResult) -> int:
    """New, modified and fields-changed documents the last scan found, and
    the queued re-extractions it reached (spec L6)."""
    ds = result.document_sync
    if ds is None:
        return result.reextract_reached
    return ds.pending + ds.modified + ds.fields_changed + result.reextract_reached
```

3.4 `KnovasConnector/src/routes/sync_control.py`: in the `from sync.sync_scheduler import (...)` list, add `reextract_status,` after `load_last_sync_body,` and `requeue_reextract,` after `requeue_doc_fields,`.

3.5 In `sync_status`, directly before `    if request.args.get("live") == "1":`, after `status["doc_fields"] = doc_fields_status()` and after any line PIN added for `status["extraction"]`, add:

```python
    # Re-extraction (spec L6): documents an older extraction produced, those
    # queued, and the per-cycle bound -- counts only. Merged into the
    # ``extraction`` block wherever that block was built.
    status.setdefault("extraction", {}).update(reextract_status())
```

3.6 At the end of the file, add the route. Give it exactly the decorators `sync_doc_fields_requeue` has at that point: today `@_apply_decorators`; if EXT's E6 added a state lock to that route, add the same lock. The route itself writes no shared file, only one `UPDATE` in the state DB, which SQLite serialises.

```python


@sync_control_bp.route("/sync/reextract/requeue", methods=["POST"])
@_apply_decorators
def sync_reextract_requeue():
    """Re-extract the documents an older extraction produced (spec L6).

    Queues every tracked document whose extraction stamp is not the current
    one (``resend_reason`` = ``reextract``); the next cycles re-extract them
    within RC_REEXTRACT_PER_CYCLE and upload those whose text, parts or
    fields changed -- each such upload is billed. No body is read. Answers
    ``{"requeued": n}``: newly queued, never counted twice.
    """
    requeued = requeue_reextract()
    if requeued:
        # A running worker picks them up now rather than after its idle wait.
        request_cycle_now()
    return jsonify({"requeued": requeued}), 200
```

3.7 Docs.

`KnovasConnector/docs/operations.md`: insert directly before `## Upgrades`:

~~~markdown
## Re-extraction after an extractor upgrade

Every upload records an **extraction stamp** — 16 hex characters of a hash over the installed knovas-extract version, `RC_PDF_TEXT_MODE`, `RC_DOCX_TEXT_MODE`, `RC_OCR_ENGINE`, `RC_OCR_DPI`, `RC_SENTENCE_EMIT_MAX_BYTES` and an internal schema number — and the sha256 of exactly what it carried (every part with its page and sentence number, the field values, title and description). A document whose stamp is not the current one — or that has none, because it was synced before this release — was produced by an **older extraction**. Nothing is re-extracted by itself: every upload is billed.

`GET /sync/status` reports them, counts only:

```json
"extraction": {"knovas_extract_version": "0.4.0a1", "pdf_text_mode": "layout",
               "docx_text_mode": "layout", "ocr_engine": "auto",
               "outdated": 1234, "queued": 0, "per_cycle": 100}
```

The Platform's Ingestion tab shows `outdated` as *N Dokumente mit älterer Extraktion* and offers *Neu extrahieren* to an administrator after a confirmation that states count, cost and duration. It calls:

```bash
curl -sS -X POST "$RC_BASE/sync/reextract/requeue" \
  -H "Authorization: Bearer $EMPLOYEE_JWT" \
  -H "Content-Type: application/json" -d '{}'
# {"requeued": 1234}
```

Same authorization as `/sync/doc-fields/requeue`; no body is read. Every outdated document is queued (state column `resend_reason = 'reextract'`; a document already queued is not counted again) and a running worker starts its next cycle at once. Each cycle then takes — after new, modified and field re-uploads, and within `max_files_per_cycle` — at most `RC_REEXTRACT_PER_CYCLE` queued documents its scan reached, partial ones first, then PDFs, Word files, e-mails and the rest, and re-extracts each one:

- **unchanged** — the upload would carry exactly what Knovas holds: nothing is sent and nothing billed; the stamp is updated and the partial note follows the new extraction (a born-digital PDF an older release recorded partial leaves the backfill list);
- **changed** — uploaded in place (same identifier): **a billed upload**;
- **unconvertible** — the stamp is updated, nothing is sent;
- **any other failure** — tried again on the next cycle, at most 3 times; the document stays outdated, and the next request queues it again. It never counts toward `RC_EXTRACT_MAX_RETRIES` and is never recorded partial: Knovas still holds its last upload.

Documents uploaded before this release have no hash, so the **first** re-extraction uploads every one of them; later upgrades upload only what changed. While queued documents wait, the worker does not back off and a sequential subfolder does not advance. A queued document no scan reaches any more (a completed subfolder of a sequential import, a removed file kept by `delete_on_remove: false`) stays queued and counted. Each cycle logs one line of counts: `reextract uploaded=… unchanged=… failed=… reached=…`.

The SQLite `documents` table gains `extraction_stamp`, `text_sha256`, `resend_reason` and `resend_attempts` on first start; an older Knovas Connector ignores them, and a row it rewrites counts as outdated again.

~~~

`KnovasConnector/docs/configuration.md`: insert directly before `## Microsoft 365 (OneDrive / SharePoint) as the document source`:

~~~markdown
### Re-extraction after an extractor upgrade

Read by `config.py`; see [operations.md](operations.md#re-extraction-after-an-extractor-upgrade).

| Variable | Default | Meaning |
|----------|---------|---------|
| `RC_REEXTRACT_PER_CYCLE` | `100` | Documents re-extracted per cycle after `POST /sync/reextract/requeue` (the Platform's *Neu extrahieren*), on top of `RC_FIELDS_REUPLOAD_PER_CYCLE` and within `max_files_per_cycle` (range 1–10000; outside it the boot stops). Only a document whose upload would change is sent again — each such upload is billed. |

~~~

`KnovasConnector/CHANGELOG.md`: insert directly after the line `## Unreleased` and its following blank line:

~~~markdown
### Re-extraction after an extractor upgrade

- Every upload records an **extraction stamp** (16 hex characters over the knovas-extract version, `RC_PDF_TEXT_MODE`, `RC_DOCX_TEXT_MODE`, `RC_OCR_ENGINE`, `RC_OCR_DPI`, `RC_SENTENCE_EMIT_MAX_BYTES` and an internal schema number) and the sha256 of what it carried. Rows synced before have no stamp and count as an older extraction; nothing is re-sent by itself.
- **`POST /sync/reextract/requeue`** → `{"requeued": n}` queues them; each cycle re-extracts at most **`RC_REEXTRACT_PER_CYCLE`** (100, 1–10000) after new, modified and field re-uploads — partial first, then PDF, DOCX, e-mail, the rest — and uploads in place only what changed (the first round uploads all: no hash yet). A failure never uses `RC_EXTRACT_MAX_RETRIES`; after 3 the document leaves the queue, still outdated.
- **`GET /sync/status`**: `extraction.outdated`, `extraction.queued`, `extraction.per_cycle` — counts only.

~~~

`KnovasConnector/.env.example`: append after `# RC_FIELDS_REUPLOAD_MAX_ATTEMPTS=3`:

```
# Documents re-extracted per cycle after the Platform's "Neu extrahieren"
# (POST /sync/reextract/requeue); only those whose upload changes are sent
# again, each a billed upload.
# RC_REEXTRACT_PER_CYCLE=100
```

`knovas.env.example`: insert after `#   RC_FIELDS_REUPLOAD_MAX_ATTEMPTS=3`:

```
#
#   # After an extractor upgrade, Verwaltung -> Ingestion -> Neu extrahieren
#   # lets the Knovas Connector re-read the documents an older extraction
#   # produced, at most this many per cycle; only those whose text changed
#   # are sent again, each a billed upload.
#   RC_REEXTRACT_PER_CYCLE=100
```

`docs/specifications.md`: insert directly before `**Optional OneDrive mirror**`:

```
**Re-extraction after an extractor upgrade**

- `RC_REEXTRACT_PER_CYCLE` (default `100`, 1–10000) — documents re-extracted per cycle after the Platform's *Neu extrahieren* (`POST /sync/reextract/requeue`); only a document whose upload would change is sent again, each such upload is billed
- Details: `KnovasConnector/docs/operations.md` (*Re-extraction after an extractor upgrade*)

```

- [ ] **Step 4: Run test to verify it passes**

Run: `rc-pytest tests/integration/test_sync_reextract_routes.py`
Expected: PASS. The neighbouring suites `rc-pytest tests/integration tests/unit/test_sync_scheduler.py tests/contract/test_doc_fields_against_mock.py tests/test_platform_principal.py` still pass. If a PIN test asserts the **exact** key set of `extraction`, extend it in this commit with `outdated`, `queued` and `per_cycle`. The full suite `rc-pytest tests` passes, apart from the known Windows-only failures.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasConnector/src/sync/sync_scheduler.py KnovasConnector/src/routes/sync_control.py \
  KnovasConnector/tests/integration/test_sync_reextract_routes.py \
  KnovasConnector/docs/operations.md KnovasConnector/docs/configuration.md \
  KnovasConnector/CHANGELOG.md KnovasConnector/.env.example knovas.env.example docs/specifications.md
git commit -F - <<'EOF'
rc: POST /sync/reextract/requeue and extraction counts in /sync/status (L6)

The route queues every tracked document an older extraction produced and
wakes the worker. It has the same gate as /sync/doc-fields/requeue,
including the Platform principal, reads no body and answers
{"requeued": n}. /sync/status adds outdated, queued and per_cycle to the
extraction block, counts only: the Platform needs them for "N Dokumente
mit aelterer Extraktion" and for the cost/duration dialog. Queued
re-extractions count as pending work, so the worker does not back off to
an hour per cycle while they wait. Configuration and operations docs,
changelog and env examples follow.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task REX-5a: `KnovasConnectorClient.requeue_reextract`

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_connector_client.py` (new method after `requeue_doc_fields`)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_knovas_connector_client.py` (append)

**Interfaces:**
- Consumes: REX-4 `POST /sync/reextract/requeue`.
- Produces: `KnovasConnectorClient.requeue_reextract(self) -> dict`.
  - Returns `{"requeued": int}`; an odd answer gives `{"requeued": 0}`.
  - Raises `KnovasConnectorError` with `status == 404` for an older Connector, and with `status is None` when the Connector is unreachable.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knovas_connector_client.py`:

```python
def test_requeue_reextract_posts_an_empty_body_as_the_signed_in_person():
    session = _Session({("POST", "requeue"): _Resp(200, {"requeued": 12})})
    client = KnovasConnectorClient(BASE, principal_broker=_Broker(), session=session)
    assert client.requeue_reextract() == {"requeued": 12}
    method, url, body, headers = session.calls[0]
    assert (method, url, body) == ("POST", f"{BASE}/sync/reextract/requeue", {})
    assert headers["X-Platform-Principal"] == "token-for-u-1"


@pytest.mark.parametrize("answer", [{"requeued": "viele"}, {"requeued": -3}, {}, ["x"], None])
def test_requeue_reextract_reads_an_odd_answer_as_nothing_queued(answer):
    session = _Session({("POST", "requeue"): _Resp(200, answer)})
    client = KnovasConnectorClient(BASE, principal_broker=_Broker(), session=session)
    assert client.requeue_reextract() == {"requeued": 0}


def test_requeue_reextract_on_an_old_connector_is_a_404_error():
    session = _Session({("POST", "requeue"): _Resp(404, {"error": "Not Found"})})
    client = KnovasConnectorClient(BASE, principal_broker=_Broker(), session=session)
    with pytest.raises(KnovasConnectorError) as excinfo:
        client.requeue_reextract()
    assert excinfo.value.status == 404


def test_requeue_reextract_on_an_unreachable_connector_has_no_status():
    def down(_kw):
        raise requests.exceptions.ConnectionError("refused")

    session = _Session({("POST", "requeue"): down})
    client = KnovasConnectorClient(BASE, principal_broker=_Broker(), session=session)
    with pytest.raises(KnovasConnectorError) as excinfo:
        client.requeue_reextract()
    assert excinfo.value.status is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_knovas_connector_client.py`
Expected: FAIL — `AttributeError: 'KnovasConnectorClient' object has no attribute 'requeue_reextract'`

- [ ] **Step 3: Write minimal implementation**

In `src/knovas_connector_client.py`, directly after the method `requeue_doc_fields` (it ends with `            return 0`), add:

```python

    def requeue_reextract(self) -> dict:
        """Queue every document an older extraction produced for
        re-extraction (``POST /sync/reextract/requeue``); ``{"requeued": n}``.

        The Knovas Connector re-extracts them within its per-cycle bound and
        uploads only those whose text changed -- each such upload is billed.
        An older Connector answers 404: KnovasConnectorError with
        ``status == 404``.
        """
        payload = self._call("POST", "/sync/reextract/requeue", body={})
        try:
            requeued = max(0, int((payload or {}).get("requeued") or 0))
        except (TypeError, ValueError, AttributeError):
            requeued = 0
        return {"requeued": requeued}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_knovas_connector_client.py`
Expected: PASS. The neighbouring test `pf-pytest tests/test_web_admin_ingestion.py::TestKnovasConnectorClientDocFields` still passes.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasPlatform/components/docbridge_integration/src/knovas_connector_client.py \
  KnovasPlatform/components/docbridge_integration/tests/test_knovas_connector_client.py
git commit -F - <<'EOF'
platform: KnovasConnectorClient.requeue_reextract (L6)

Wraps the Knovas Connector's POST /sync/reextract/requeue as the
signed-in person and returns {"requeued": n}. An older Connector's 404
and an unreachable Connector stay distinguishable (status 404 vs None),
so the Ingestion tab can say "zu alt" or "nicht erreichbar".

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task REX-5b: Ingestion tab — "N Dokumente mit älterer Extraktion", *Neu extrahieren* (admin only, confirmed, audited)

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/admin.py` (`attach_ingestion_routes(...)` call)
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/admin_ingestion.py` (module docstring; re-extraction helpers before `template_preview`; `attach_ingestion_routes` signature; `_reextract_context`; `_page`; route `reextract`)
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/templates/admin_ingestion.html`
- Modify: `KnovasPlatform/docs/features/document-administration.md`, `RELEASE_NOTES.md`
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_web_admin_ingestion.py` (`FakeKnovasConnectorClient`, `TestShape`, new `TestReextractSection`, `TestTemplateReextract`, `TestLiveReextract`)
- No change to `static/js/admin_ingestion.js`: the confirmation is server-rendered, like the field-change and restore confirmations.

**Interfaces:**
- Consumes:
  - REX-5a: `KnovasConnectorClient.requeue_reextract() -> dict`.
  - REX-4: `/sync/status["extraction"]` with `outdated`, `queued`, `per_cycle`.
  - Existing: `reupload_bound`, `reupload_eta`, `_schedule_label`, `_count`.
- Produces:
  - Route `POST /admin/ingestion/reextract` (endpoint `admin.reextract`): admin only; CSRF first; `confirm_reextract=<N>` required and must be ≥ the current count; audited as `ingestion.reextract_requeued` with `{"outdated", "requeued"}`.
  - `admin_ingestion.extraction_block(rc_status)`
  - `reextract_status(rc_status, *, throughput="normal", is_admin=False) -> dict | None`
  - `reextract_text(count, per_cycle, schedule, throughput) -> str`
  - `REEXTRACT_AUDIT_ACTION`
  - `attach_ingestion_routes(..., require_ingestion, require_admin)`

- [ ] **Step 1: Write the failing test**

1.1 In `FakeKnovasConnectorClient`, replace

```python
    requeue_answer = 0
    #: Extra /discover answers by root, for the template preview.
    extra_entries: dict = {}
```

with

```python
    requeue_answer = 0
    #: Re-extraction (spec L6). None leaves ``extraction`` out of status(),
    #: the way an older Knovas Connector answers.
    extraction_block = None
    reextract_answer: dict = {"requeued": 0}
    reextract_error = None
    #: Extra /discover answers by root, for the template preview.
    extra_entries: dict = {}
```

In its `status()`, replace

```python
        if self.document_sync is not None:
            out["document_sync"] = self.document_sync
        return out
```

with

```python
        if self.document_sync is not None:
            out["document_sync"] = self.document_sync
        if self.extraction_block is not None:
            out["extraction"] = self.extraction_block
        return out
```

After its method `requeue_doc_fields`, add:

```python
    def requeue_reextract(self):
        self.calls.append("requeue_reextract")
        if self.reextract_error is not None:
            raise self.reextract_error
        return dict(self.reextract_answer)
```

1.2 In `TestShape.test_routes_are_gated_and_posts_check_csrf_first`, replace

```python
        assert src.count("@bp.route") == src.count("@require_ingestion")
```

with

```python
        # Every route has a gate; *Neu extrahieren* is admin only (spec L6).
        assert src.count("@bp.route") == (src.count("@require_ingestion")
                                          + src.count("@require_admin"))
```

In the same test's `first_state_change` dict, after `"def requeue_doc_fields(": "rc_client_factory()",`, add:

```python
            "def reextract(": "rc_client_factory()",
```

1.3 Append to the file:

```python
class TestReextractSection:
    """Spec L6: "N Dokumente mit älterer Extraktion" and the cost of *Neu
    extrahieren*, from the Knovas Connector's counts only."""

    BLOCK = {"knovas_extract_version": "0.4.0a1", "outdated": 20000, "queued": 0,
             "per_cycle": 100}

    def test_a_connector_without_the_counts_shows_nothing(self):
        from web_interface.admin_ingestion import reextract_status

        assert reextract_status(None) is None
        assert reextract_status({"scheduler_state": "x"}) is None
        assert reextract_status({"extraction": {"knovas_extract_version": "0.4.0a1"}}) is None

    def test_the_counts_and_who_may_ask(self):
        from web_interface.admin_ingestion import reextract_status

        status = {"extraction": dict(self.BLOCK, queued=12)}
        admin = reextract_status(status, is_admin=True)
        assert (admin["outdated"], admin["queued"], admin["per_cycle"]) == (20000, 12, 100)
        assert admin["can_request"] is True and admin["admin_only"] is False
        other = reextract_status(status, is_admin=False)
        assert other["can_request"] is False and other["admin_only"] is True
        current = reextract_status({"extraction": {"outdated": 0}}, is_admin=True)
        assert current["can_request"] is False and current["admin_only"] is False
        assert reextract_status({"extraction": {"outdated": "viele"}}, is_admin=True)["outdated"] == 0

    def test_the_bound_is_the_connectors_capped_by_the_speed(self):
        from web_interface.admin_ingestion import reextract_status

        status = {"extraction": dict(self.BLOCK, per_cycle=500)}
        assert reextract_status(status, throughput="gentle")["per_cycle"] == 100
        assert reextract_status({"extraction": {"outdated": 3}})["per_cycle"] == 100, "default bound"

    def test_the_sentence_states_count_cost_and_duration_like_a_field_change(self):
        from web_interface.admin_ingestion import reextract_text

        text = reextract_text(20000, 100, "nightly", "normal")
        assert text.startswith("20000 Dokumente wurden mit einer älteren Extraktion indexiert.")
        assert "je ein verrechneter Upload, höchstens 20000" in text
        assert "Bei 100 pro Durchlauf" in text and "ca. 3 Nächte" in text
        assert "10-mal Start" in reextract_text(1000, 500, "manual", "gentle")


def _rx(**overrides):
    rx = {"outdated": 7, "queued": 0, "per_cycle": 100, "can_request": True,
          "admin_only": False, "confirm": None}
    rx.update(overrides)
    return rx


class TestTemplateReextract:
    def test_without_the_counts_the_page_is_as_before(self):
        html = _render()
        assert "älterer Extraktion" not in html and "reextract" not in html

    def test_the_section_and_its_button(self):
        html = _render(reextract=_rx(queued=2))
        assert "7 Dokumente mit älterer Extraktion" in html
        assert "2 davon zum Neu-Extrahieren vorgemerkt" in html
        assert 'action="/admin/reextract"' in html and ">Neu extrahieren</button>" in html
        assert 'name="confirm_reextract"' not in html, "the button only opens the confirmation"
        assert html.count('name="csrf_token"') >= html.count('method="post"')

    def test_who_may_not_ask_is_told_so(self):
        html = _render(reextract=_rx(can_request=False, admin_only=True))
        assert 'action="/admin/reextract"' not in html
        assert "nur die Rolle admin" in html

    def test_the_confirmation_carries_the_count_it_showed(self):
        html = _render(reextract=_rx(confirm={"count": 7, "text": "7 Dokumente wurden ..."}))
        assert "Neu extrahieren bestätigen" in html and "7 Dokumente wurden ..." in html
        assert 'name="confirm_reextract" value="7"' in html
        assert "7 Dokumente neu extrahieren</button>" in html


@pytest.mark.skipif(not platform_db_reachable(),
                    reason="No PostgreSQL at the identity test DSN")
class TestLiveReextract:
    """*Neu extrahieren* through the real app, login and audit log (spec L6)."""

    BLOCK = {"knovas_extract_version": "0.4.0a1", "pdf_text_mode": "layout",
             "docx_text_mode": "layout", "ocr_engine": "auto",
             "outdated": 20000, "queued": 0, "per_cycle": 100}

    @pytest.fixture
    def rc(self, monkeypatch):
        import knovas_connector_client

        FakeKnovasConnectorClient.last_instance = None
        monkeypatch.setattr(knovas_connector_client, "KnovasConnectorClient",
                            FakeKnovasConnectorClient)
        monkeypatch.setattr(FakeKnovasConnectorClient, "extraction_block", dict(self.BLOCK))
        monkeypatch.setattr(FakeKnovasConnectorClient, "reextract_answer", {"requeued": 20000})
        return FakeKnovasConnectorClient

    @pytest.fixture
    def client(self, rc, identity_app):
        return identity_app.test_client()

    @pytest.fixture
    def people(self, identity_repo):
        from _console import PASSWORD

        out = {}
        for email, role in (("chef@kanzlei.ch", "admin"),
                            ("ingest@kanzlei.ch", "ingestion_manager"),
                            ("anwalt@kanzlei.ch", "member")):
            u = identity_repo.create(email=email, display_name=email.split("@")[0],
                                     password=PASSWORD)
            identity_repo.grant_role(u.id, role)
            out[email] = identity_repo.get(u.id)
        return out

    @staticmethod
    def _post(client, **fields):
        from _console import post_form

        return post_form(client, "/admin/ingestion/reextract", page="/admin/ingestion", **fields)

    def test_the_count_shows_and_only_an_admin_gets_the_button(self, client, people):
        from _console import sign_in

        sign_in(client, "chef@kanzlei.ch")
        html = client.get("/admin/ingestion").data.decode("utf-8")
        assert "20000 Dokumente mit älterer Extraktion" in html
        assert 'action="/admin/ingestion/reextract"' in html
        _logout(client)
        sign_in(client, "ingest@kanzlei.ch")
        html = client.get("/admin/ingestion").data.decode("utf-8")
        assert "20000 Dokumente mit älterer Extraktion" in html
        assert 'action="/admin/ingestion/reextract"' not in html
        assert "nur die Rolle admin" in html

    def test_anyone_but_an_admin_is_refused_the_post(self, client, people, rc):
        from _console import sign_in

        for email in ("ingest@kanzlei.ch", "anwalt@kanzlei.ch"):
            sign_in(client, email)
            with client.session_transaction() as sess:
                token = sess.get("csrf_token")
            r = client.post("/admin/ingestion/reextract",
                            data={"csrf_token": token, "confirm_reextract": "20000"})
            assert r.status_code == 403, email
            _logout(client)
        assert rc.last_instance.count("requeue_reextract") == 0

    def test_the_post_checks_csrf_first(self, client, people, rc):
        from _console import sign_in

        sign_in(client, "chef@kanzlei.ch")
        r = client.post("/admin/ingestion/reextract", data={"confirm_reextract": "20000"})
        assert r.status_code == 400
        assert rc.last_instance.count("requeue_reextract") == 0

    def test_a_click_shows_count_cost_and_duration_and_queues_nothing(
        self, client, people, rc, platform_db
    ):
        from _console import sign_in

        sign_in(client, "chef@kanzlei.ch")
        r = self._post(client)
        html = r.data.decode("utf-8")
        assert r.status_code == 400
        assert "Neu extrahieren bestätigen" in html
        assert "20000 Dokumente wurden mit einer älteren Extraktion indexiert" in html
        assert "je ein verrechneter Upload, höchstens 20000" in html
        assert "Bei 100 pro Durchlauf" in html and "ca. 3 Nächte" in html
        assert 'name="confirm_reextract" value="20000"' in html
        assert rc.last_instance.count("requeue_reextract") == 0
        assert platform_db.execute("SELECT count(*) FROM audit_log "
                                   "WHERE action = 'ingestion.reextract_requeued'").fetchone()[0] == 0

    def test_the_confirmed_click_queues_and_is_audited_with_counts_only(
        self, client, people, rc, platform_db
    ):
        from _console import sign_in

        sign_in(client, "chef@kanzlei.ch")
        r = self._post(client, confirm_reextract="20000")
        assert r.status_code == 200
        assert "20000 Dokumente zum Neu-Extrahieren vorgemerkt" in r.data.decode("utf-8")
        assert rc.last_instance.count("requeue_reextract") == 1
        row = platform_db.execute("SELECT detail FROM audit_log "
                                  "WHERE action = 'ingestion.reextract_requeued'").fetchone()
        assert row[0] == {"outdated": 20000, "requeued": 20000}

    def test_a_count_that_grew_since_the_dialog_is_confirmed_again(self, client, people, rc):
        from _console import sign_in

        sign_in(client, "chef@kanzlei.ch")
        r = self._post(client, confirm_reextract="150")
        assert r.status_code == 400
        assert 'name="confirm_reextract" value="20000"' in r.data.decode("utf-8")
        assert rc.last_instance.count("requeue_reextract") == 0

    def test_nothing_outdated_queues_nothing(self, client, people, rc, monkeypatch):
        from _console import sign_in

        monkeypatch.setattr(FakeKnovasConnectorClient, "extraction_block",
                            dict(self.BLOCK, outdated=0))
        sign_in(client, "chef@kanzlei.ch")
        r = self._post(client, confirm_reextract="0")
        assert r.status_code == 200
        assert "Keine Dokumente mit älterer Extraktion." in r.data.decode("utf-8")
        assert rc.last_instance.count("requeue_reextract") == 0

    def test_an_old_connector_is_told_to_update(self, client, people, rc, monkeypatch):
        from _console import sign_in
        from knovas_connector_client import KnovasConnectorError

        monkeypatch.setattr(FakeKnovasConnectorClient, "reextract_error",
                            KnovasConnectorError("HTTP 404", status=404))
        sign_in(client, "chef@kanzlei.ch")
        r = self._post(client, confirm_reextract="20000")
        assert r.status_code == 502
        assert "Knovas Connector zu alt – bitte aktualisieren" in r.data.decode("utf-8")

    def test_a_connector_without_the_counts_is_too_old_and_never_asked(
        self, client, people, rc, monkeypatch
    ):
        from _console import sign_in

        monkeypatch.setattr(FakeKnovasConnectorClient, "extraction_block", None)
        sign_in(client, "chef@kanzlei.ch")
        assert "älterer Extraktion" not in client.get("/admin/ingestion").data.decode("utf-8")
        r = self._post(client, confirm_reextract="20000")
        assert r.status_code == 400
        assert "Knovas Connector zu alt – bitte aktualisieren" in r.data.decode("utf-8")
        assert rc.last_instance.count("requeue_reextract") == 0

    def test_an_unreachable_connector_is_not_called_too_old(self, client, people, rc, monkeypatch):
        from _console import sign_in
        from knovas_connector_client import KnovasConnectorError

        def down(self):
            raise KnovasConnectorError("Knovas Connector nicht erreichbar: timeout", status=None)

        monkeypatch.setattr(FakeKnovasConnectorClient, "status", down)
        sign_in(client, "chef@kanzlei.ch")
        r = self._post(client, confirm_reextract="20000")
        html = r.data.decode("utf-8")
        assert r.status_code == 502
        assert "Knovas Connector nicht erreichbar" in html and "zu alt" not in html
        assert rc.last_instance.count("requeue_reextract") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pf-pytest tests/test_web_admin_ingestion.py`
Expected: FAIL.
- `ImportError: cannot import name 'reextract_status' from 'web_interface.admin_ingestion'`
- `TestShape`: `ValueError: substring not found` (`def reextract(`)
- The template tests fail: no section.
- `TestLiveReextract`: 404 on `/admin/ingestion/reextract`, and no count on the page.

- [ ] **Step 3: Write minimal implementation**

3.1 `src/web_interface/admin.py`: in the `attach_ingestion_routes(...)` call, replace

```python
            rc_client_factory=rc_client_factory,
            require_ingestion=require_ingestion,
        )
```

with

```python
            rc_client_factory=rc_client_factory,
            require_ingestion=require_ingestion,
            require_admin=require_admin,
        )
```

3.2 `src/web_interface/admin_ingestion.py`: in the module docstring, directly before the line `Plan: docs/superpowers/plans/2026-09-02-admin-ingestion-tab.md`, insert:

```
Re-extraction (spec L6)
-----------------------
After an extractor upgrade the tab shows how many documents an older
extraction produced (the Knovas Connector's ``extraction.outdated``) and
offers *Neu extrahieren* to an admin only, after a confirmation that states
count, cost and duration and carries the count it showed. Audited with
counts only.

```

3.3 Directly before `def template_preview(`, insert:

```python
# ---------------------------------------------------------------------------
# Re-extraction after an extractor upgrade (spec L6)
# ---------------------------------------------------------------------------
#
# Counts only: the Knovas Connector reports how many documents an older
# extraction produced; no path, title or text reaches this page or a log.

#: The Knovas Connector's default re-extraction bound (RC_REEXTRACT_PER_CYCLE),
#: used when its status does not report ``per_cycle``.
DEFAULT_REEXTRACT_PER_CYCLE = 100
REEXTRACT_AUDIT_ACTION = "ingestion.reextract_requeued"
REEXTRACT_UNCONFIRMED = "Bitte bestätigen, dass die Dokumente neu extrahiert werden."
REEXTRACT_CHANGED = ("Seit der Anzeige sind weitere Dokumente mit älterer Extraktion "
                     "dazugekommen; bitte die neue Zahl bestätigen.")
REEXTRACT_TOO_OLD = ("Knovas Connector zu alt – bitte aktualisieren: er kann Dokumente "
                     "noch nicht neu extrahieren.")
REEXTRACT_UNREACHABLE = ("Knovas Connector nicht erreichbar – bitte später erneut "
                         "versuchen.")


def extraction_block(rc_status: Any) -> Mapping[str, Any] | None:
    """The Knovas Connector's ``extraction`` block when it counts outdated
    documents (one that can re-extract); None for an older or unreachable
    one."""
    if not isinstance(rc_status, Mapping):
        return None
    block = rc_status.get("extraction")
    if not isinstance(block, Mapping) or "outdated" not in block:
        return None
    return block


def reextract_status(rc_status: Any, *, throughput: str = "normal",
                     is_admin: bool = False) -> dict[str, Any] | None:
    """The tab's re-extraction section: ``N Dokumente mit älterer
    Extraktion``, how many already wait, the per-cycle bound as the
    throughput preset lets it through, and whether to offer *Neu
    extrahieren* (admins only). None without the Connector's counts."""
    block = extraction_block(rc_status)
    if block is None:
        return None
    outdated = _count(block.get("outdated"))
    per_cycle = _count(block.get("per_cycle")) or DEFAULT_REEXTRACT_PER_CYCLE
    return {
        "outdated": outdated,
        "queued": _count(block.get("queued")),
        "per_cycle": reupload_bound(per_cycle, throughput),
        "can_request": bool(is_admin) and outdated > 0,
        "admin_only": not is_admin and outdated > 0,
        "confirm": None,
    }


def reextract_text(count: Any, per_cycle: Any, schedule: str, throughput: str) -> str:
    """The cost sentence *Neu extrahieren* is confirmed against, computed
    like a field change's (``reupload_text``). Only documents whose upload
    changes are sent, so ``count`` is the upper bound of billed uploads."""
    count = _count(count)
    bound = reupload_bound(_count(per_cycle) or DEFAULT_REEXTRACT_PER_CYCLE, throughput)
    eta = reupload_eta(count, bound, schedule, throughput)
    return (f"{count} Dokumente wurden mit einer älteren Extraktion indexiert. Der Knovas "
            "Connector liest sie neu und sendet jedes, dessen Text, Seitenzahlen oder Felder "
            "sich geändert haben, erneut an Knovas – je ein verrechneter Upload, "
            f"höchstens {count}. Bei {bound} pro Durchlauf und Zeitplan "
            f"„{_schedule_label(schedule)}“ {eta}.")


def _confirmed_count(raw: Any) -> int | None:
    """The count a confirmation carries (``confirm_reextract``); None
    without one. Digits only: the field holds the number the dialog showed."""
    text = str(raw or "").strip()
    return int(text) if text.isdigit() else None


def _reextract_error(exc: Exception) -> str:
    """What the page says when the Knovas Connector refused or could not
    be asked: 404 is an older Connector, no status an unreachable one."""
    status = getattr(exc, "status", None)
    if status == 404:
        return REEXTRACT_TOO_OLD
    if status is None:
        return REEXTRACT_UNREACHABLE
    return f"Neu extrahieren fehlgeschlagen: {exc}"


```

3.4 Replace

```python
def attach_ingestion_routes(bp, gate, *, csrf_valid, csrf_token, page_context,
                            client_factory, rc_client_factory, require_ingestion):
```

with

```python
def attach_ingestion_routes(bp, gate, *, csrf_valid, csrf_token, page_context,
                            client_factory, rc_client_factory, require_ingestion,
                            require_admin):
```

3.5 Inside `attach_ingestion_routes`, directly before `    def _page(form=None, *, error=None, ...`, insert:

```python
    def _reextract_context(rc_status, current, *, confirm: bool) -> dict[str, Any] | None:
        """The re-extraction section (spec L6); with ``confirm`` also the
        dialog that states count, cost and duration, bound to that count."""
        running = current.profile if current else None
        schedule = running.schedule if running else "nightly"
        throughput = running.throughput if running else "normal"
        roles = set(getattr(gate.current_user(), "roles", ()) or ())
        section = reextract_status(rc_status, throughput=throughput, is_admin="admin" in roles)
        if section is not None and confirm and section["can_request"]:
            section["confirm"] = {
                "count": section["outdated"],
                "text": reextract_text(section["outdated"], section["per_cycle"],
                                       schedule, throughput),
            }
        return section

```

3.6 Replace the `_page` signature

```python
    def _page(form=None, *, error=None, notice=None, status=200, preview=None, support_json=None,
              reupload_paths=None, restore_version=None, field_warnings=(), field_notes=()):
```

with

```python
    def _page(form=None, *, error=None, notice=None, status=200, preview=None, support_json=None,
              reupload_paths=None, restore_version=None, field_warnings=(), field_notes=(),
              reextract_confirm=False):
```

In its `render_template(...)` call, replace

```python
                restore_version=restore_version, warnings=field_warnings, notes=field_notes,
                rc_reachable=rc_reachable),
            me=gate.current_user(),
```

with

```python
                restore_version=restore_version, warnings=field_warnings, notes=field_notes,
                rc_reachable=rc_reachable),
            reextract=_reextract_context(rc_status, current, confirm=reextract_confirm),
            me=gate.current_user(),
```

3.7 Directly before `    @bp.route("/ingestion/template-preview", methods=["POST"])`, insert:

```python
    @bp.route("/ingestion/reextract", methods=["POST"])
    @require_admin
    def reextract():
        """*Neu extrahieren* (spec L6): the Knovas Connector queues every
        document an older extraction produced, re-extracts them within its
        per-cycle bound and uploads those whose text changed -- each a billed
        upload. Admin only; the first click shows count, cost and duration,
        the second carries the count it showed. Audited with counts only."""
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        rc = rc_client_factory()
        try:
            block = extraction_block(rc.status())
        except (KnovasConnectorError, PermissionError) as exc:
            return _page(error=_reextract_error(exc), status=502)
        if block is None:
            return _page(error=REEXTRACT_TOO_OLD, status=400)
        outdated = _count(block.get("outdated"))
        if not outdated:
            return _page(notice="Keine Dokumente mit älterer Extraktion.")
        confirmed = _confirmed_count(request.form.get("confirm_reextract"))
        if confirmed is None:
            return _page(error=REEXTRACT_UNCONFIRMED, status=400, reextract_confirm=True)
        if outdated > confirmed:
            return _page(error=REEXTRACT_CHANGED, status=400, reextract_confirm=True)
        try:
            answer = rc.requeue_reextract()
        except (KnovasConnectorError, PermissionError) as exc:
            return _page(error=_reextract_error(exc), status=502)
        count = _count((answer or {}).get("requeued"))
        audit.record(gate.connection(), action=REEXTRACT_AUDIT_ACTION, actor=gate.current_user(),
                     target_type="knovas_connector", target_id="sync",
                     detail={"outdated": outdated, "requeued": count})
        if not count:
            return _page(notice="Keine Dokumente zum Neu-Extrahieren vorgemerkt.")
        return _page(notice=(f"{count} Dokumente zum Neu-Extrahieren vorgemerkt; der Knovas "
                             "Connector liest sie in den nächsten Durchläufen neu und "
                             "sendet die geänderten erneut – je ein verrechneter Upload."))

```

3.8 `templates/admin_ingestion.html`. Replace

```html
{% set df = doc_fields if doc_fields is defined else none %}
```

with

```html
{% set df = doc_fields if doc_fields is defined else none %}
{# Re-extraction after an extractor upgrade (spec L6); absent in older
   render calls and for a Knovas Connector without the counts. #}
{% set rx = reextract if reextract is defined else none %}
```

Directly before `    <section class="ingestion-status-bar">`, insert:

```html
    {% if rx and rx.confirm %}
    <section class="panel reextract-confirm">
        <h2>Neu extrahieren bestätigen</h2>
        <p>{{ rx.confirm.text }}</p>
        <form class="inline" method="post" action="{{ url_for('admin.reextract') }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <input type="hidden" name="confirm_reextract" value="{{ rx.confirm.count }}">
            <button type="submit">{{ rx.confirm.count }} Dokumente neu extrahieren</button>
        </form>
    </section>
    {% endif %}

```

Directly before

```html
    {% if preview %}
    <section class="panel">
        <h2>Vorschau</h2>
```

insert:

```html
    {% if rx %}
    <section class="panel reextract-status">
        <h2>Extraktion</h2>
        <p>{{ rx.outdated }} Dokumente mit älterer Extraktion{% if rx.queued %} · {{ rx.queued }} davon zum Neu-Extrahieren vorgemerkt{% endif %}</p>
        {% if rx.can_request %}
        <form class="inline" method="post" action="{{ url_for('admin.reextract') }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <button type="submit" class="secondary compact">Neu extrahieren</button>
        </form>
        <p class="hint">Vorher zeigt ein Dialog Anzahl, Kosten und Dauer. Gesendet wird nur, was sich geändert hat – jedes gesendete Dokument ist ein verrechneter Upload.</p>
        {% elif rx.admin_only %}
        <p class="hint">Neu extrahieren kann nur die Rolle admin auslösen.</p>
        {% endif %}
    </section>
    {% endif %}

```

3.9 `KnovasPlatform/docs/features/document-administration.md`: insert directly before `## Scale`:

~~~markdown
**Neu extrahieren.** After an extractor upgrade the tab shows *N Dokumente
mit älterer Extraktion*: the Knovas Connector's `/sync/status` →
`extraction.outdated`, the documents whose extraction stamp (knovas-extract
version, PDF and DOCX text modes, OCR engine, DPI, sentence gate) is not the
current one. Only an `admin` gets the *Neu extrahieren* button
(`POST /admin/ingestion/reextract`). The first click shows count, cost and
duration — computed like a field change's, with the Connector's
`RC_REEXTRACT_PER_CYCLE` — and nothing is queued until that confirmation,
which carries the count it showed, is sent; a count that grew since is
confirmed again. The Connector then re-extracts at most that many documents
per cycle and uploads only those whose text, page numbers, fields, title or
description changed — each such upload is billed; the first round after the
release that introduced this uploads all of them, since no comparison hash
exists yet. The request is audited as `ingestion.reextract_requeued` with
counts only. An older Connector is named: *Knovas Connector zu alt – bitte
aktualisieren*.

~~~

3.10 `RELEASE_NOTES.md`: insert directly above `## Dokumentfelder (Dokumentwerte)`:

~~~markdown
## Neu extrahieren nach einem Extraktor-Update

Neuere Versionen von knovas-extract lesen manche Dokumente besser (Tabellen
in Word-Dateien, Seitenzahlen grosser Scans, Texterkennung je Seite). Bereits
indexierte Dokumente profitieren davon erst, wenn sie neu extrahiert werden.

- *Verwaltung -> Ingestion* zeigt "N Dokumente mit aelterer Extraktion" und
  bietet Administratoren *Neu extrahieren* an. Ein Dialog nennt vorher
  Anzahl, Kosten und Dauer wie bei einer Feldaenderung; ohne diese
  Bestaetigung wird nichts neu extrahiert. Die Anfrage steht mit Zahlen im
  Protokoll (`ingestion.reextract_requeued`).
- Der Knovas Connector liest die Dokumente neu, hoechstens
  `RC_REEXTRACT_PER_CYCLE` (100) je Durchlauf, nach neuen, geaenderten und
  wegen Feldern erneut zu sendenden Dateien -- teilweise extrahierte zuerst,
  dann PDF, Word, E-Mails. Gesendet wird nur, was sich geaendert hat (Text,
  Seitenzahlen, Felder, Titel, Beschreibung); jedes gesendete Dokument ist
  ein verrechneter Upload. Beim ersten Mal nach diesem Update werden alle
  gesendet, weil der Vergleichswert noch fehlt.
- `GET /sync/status` des Connectors meldet `extraction.outdated`, `queued`
  und `per_cycle` -- nur Zahlen.

~~~

- [ ] **Step 4: Run test to verify it passes**

Run: `pf-pytest tests/test_web_admin_ingestion.py`
Expected: PASS. Also pass: `pf-pytest tests/test_knovas_connector_client.py tests/test_web_admin_doc_fields.py tests/test_identity_ingestion_profiles.py`, and the full Platform suite `pf-pytest tests`, apart from the known Windows-only failures.

- [ ] **Step 5: Commit**

```bash
cd $WT
git add KnovasPlatform/components/docbridge_integration/src/web_interface/admin.py \
  KnovasPlatform/components/docbridge_integration/src/web_interface/admin_ingestion.py \
  KnovasPlatform/components/docbridge_integration/src/web_interface/templates/admin_ingestion.html \
  KnovasPlatform/components/docbridge_integration/tests/test_web_admin_ingestion.py \
  KnovasPlatform/docs/features/document-administration.md RELEASE_NOTES.md
git commit -F - <<'EOF'
platform: Neu extrahieren in the Ingestion tab, admin only, confirmed (L6)

The tab shows "N Dokumente mit aelterer Extraktion" from the Knovas
Connector's /sync/status. Only an admin gets "Neu extrahieren": a full
re-extraction can re-send the whole corpus, and every upload is billed.
The first click shows count, cost and duration, computed like the
field-change dialog from the Connector's per-cycle bound and the running
schedule. The second carries the count it showed and is asked again if
the count grew. The request is CSRF-checked and audited as
ingestion.reextract_requeued with counts only. An older Connector (404,
or no counts) is named "zu alt"; an unreachable one is never called too
old.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

## Part B7 — Verification (VER)

Runs after every other Part B task. VER-1 adds the one piece of test infrastructure the end-to-end
check needs; VER-2 to VER-4 prove the success criteria of spec §3 on Linux, in the real images and
end to end; VER-5 is the whole-branch review and the hand-off.

### Task VER-1: The mock Knovas API can show what it received

**Files:**
- Modify: `KnovasPlatform/mock_knovas_api/app.py` (`secured_transmit`, `secured_init…`, new `GET /_mock/parts`)
- Test: `KnovasPlatform/mock_knovas_api/tests/test_mock_record_parts.py`

**Interfaces:**
- Produces: with env `MOCK_RECORD_PARTS=1` (read at call time, like `MOCK_DOC_FIELDS`), the mock keeps every
  `init_document_transmission` body's `title`, `path`, `fields` and every `transmit_document_part`
  body's `part_number`, `snippet`, `page_number`, `sentence_number`, keyed by transmission key, in
  memory; `GET /_mock/parts` returns `{"documents": [{"title", "path", "fields", "parts": [...]}]}`.
  Without the env variable the route answers 404 and nothing is kept.

- [ ] **Step 1: Write the failing test**

```python
"""MOCK_RECORD_PARTS: the end-to-end check reads back what an uploader sent."""

from __future__ import annotations

import pytest

from app import create_app  # the mock's factory, as the other mock tests import it


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MOCK_RECORD_PARTS", "1")
    return create_app().test_client()


def test_parts_are_recorded_per_document(client):
    init = client.post("/secured/init_document_transmission",
                       json={"title": "Rechnung", "path": "a/b.pdf", "pointer": "rc-sync/a/b.pdf",
                             "fields": {"doc_type": "invoice"}, "total_parts": 1})
    key = init.get_json().get("key") or init.get_json().get("transmission_key_id")
    client.post("/secured/transmit_document_part",
                json={"key": key, "part_number": 0, "snippet": "Seite eins", "page_number": 1,
                      "sentence_number": 1})
    docs = client.get("/_mock/parts").get_json()["documents"]
    assert docs[-1]["title"] == "Rechnung"
    assert docs[-1]["fields"] == {"doc_type": "invoice"}
    assert docs[-1]["parts"] == [{"part_number": 0, "snippet": "Seite eins", "page_number": 1,
                                  "sentence_number": 1}]


def test_route_is_off_without_the_switch(monkeypatch):
    monkeypatch.delenv("MOCK_RECORD_PARTS", raising=False)
    assert create_app().test_client().get("/_mock/parts").status_code == 404
```
(Before writing, read `KnovasPlatform/mock_knovas_api/tests/conftest.py` and an existing mock test to use the
same import of the app factory and the same init body keys the mock validates; adjust only those names.)

- [ ] **Step 2: Run it to verify it fails** — `mock-pytest tests/test_mock_record_parts.py -q` → FAIL (404 / no route).

- [ ] **Step 3: Implement** — in `create_app()`:

```python
    _recorded: dict[str, dict[str, Any]] = {}

    def _recording() -> bool:
        return os.environ.get("MOCK_RECORD_PARTS", "") == "1"

    @app.get("/_mock/parts")
    def mock_parts() -> Any:
        if not _recording():
            return jsonify({"error": "not found"}), 404
        return jsonify({"documents": list(_recorded.values())})
```
In `secured_init` (the `/secured/init_document_transmission` handler), after the key is created:
```python
        if _recording():
            _recorded[key] = {"title": payload.get("title"), "path": payload.get("path"),
                              "fields": payload.get("fields"), "parts": []}
```
In `secured_transmit`, before the return:
```python
        if _recording():
            doc = _recorded.get(str(payload.get("key") or payload.get("transmission_key_id") or ""))
            if doc is not None:
                doc["parts"].append({k: payload.get(k) for k in
                                     ("part_number", "snippet", "page_number", "sentence_number")})
```
(use the variable names the handlers already have for the parsed body and the key.)

- [ ] **Step 4: Run** — `mock-pytest -q` → all mock tests pass (209 + 2).

- [ ] **Step 5: Commit** — `mock: MOCK_RECORD_PARTS keeps what an uploader sent, for end-to-end checks` (+ attribution line).

### Task VER-2: The suites on Linux, as CI runs them

**Files:** none (verification).

- [ ] **Step 1: Connector suite** (Python 3.12, Tesseract, the pinned library from `$LIB`):

```bash
WTW='E:\Knovas\KnovasComponents\.claude\worktrees\fields-extract-upgrade'
LIBW='C:\Users\siran\AppData\Local\Temp\claude\E--Knovas-KnovasComponents\c6f4754c-3eb8-4feb-9b99-b78ab17ffd1c\scratchpad\lib-fix'
MSYS_NO_PATHCONV=1 docker run --rm -v "${WTW}:/repo:ro" -v "${LIBW}:/lib:ro" python:3.12-slim bash -c '
apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends git libmagic1 tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng >/dev/null 2>&1
cp -r /repo/KnovasConnector /rc && cp -r /lib /libw && cd /rc
pip install -q --root-user-action=ignore "/libw[pdf,ocr,docx,msg,html,rtf,sentences]" && pip install -q --root-user-action=ignore -e ".[dev]"
RC_SKIP_CONFIG_VALIDATION=true TESTING=true RC_RATE_LIMIT_ENABLED=true RC_MTLS_DEV_BYPASS=true RC_MTLS_DEV_EMPLOYEE_ID=11111111-1111-1111-1111-111111111111 KNOVAS_INTERNAL_API_URL=http://internal-api:5000 RC_INSTANCE_TOKEN=test-token RC_CLIENT_ID=22222222-2222-2222-2222-222222222222 RC_WATCH_ROOTS=/tmp SEMANTIX_SECURE_BASE_URL=https://knovas:8443 SEMANTIX_CLIENT_CERT_PATH=/certs/client.pem SEMANTIX_CLIENT_KEY_PATH=/certs/client.key SEMANTIX_CA_CERT_PATH=/certs/ca.pem python -m pytest -q -p no:cacheprovider | tail -3'
```
Expected: all pass (the Windows-only failures pass here).

- [ ] **Step 2: Platform suite** (Python 3.11 + PostgreSQL 15 on a private network):

```bash
docker network create kc-verify >/dev/null 2>&1
docker run -d --rm --name kc-verify-pg --network kc-verify -e POSTGRES_USER=platform -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=knovas_platform_test postgres:15-alpine
MSYS_NO_PATHCONV=1 docker run --rm --network kc-verify -v "${WTW}:/repo:ro" -v "${LIBW}:/lib:ro" python:3.11-slim bash -c '
apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends git libmagic1 nodejs >/dev/null 2>&1
cp -r /repo/KnovasPlatform /kp && cp -r /lib /libw && cd /kp/components/docbridge_integration
grep -v "^knovas-extract" requirements.txt > /tmp/req.txt && pip install -q --root-user-action=ignore pytest -r /tmp/req.txt
pip install -q --root-user-action=ignore "/libw[pdf,ocr,docx,msg,html,rtf,markdown,sentences]"
PLATFORM_DB_REQUIRED=true PLATFORM_DB_TEST_DSN=postgresql://platform:testpw@kc-verify-pg:5432/knovas_platform_test python -m pytest -q -p no:cacheprovider | tail -3
cd /kp/mock_knovas_api && python -m pytest -q -p no:cacheprovider | tail -1
cd /kp && python -m pytest experiments-sdk/python -q -p no:cacheprovider | tail -1'
docker rm -f kc-verify-pg; docker network rm kc-verify
```
Expected: Platform, mock and SDK suites pass.

### Task VER-3: Both images

- [ ] **Step 1: Build** — `cd "$WT" && ./scripts/setup.sh` is not needed; build directly:
`docker compose --env-file knovas.env.example build knovas-connector docbridge-web` (if the env file is required to be complete, copy `knovas.env.example` to a scratch env file and fill the five required values with dummies; never commit it).

- [ ] **Step 2: Checks inside the images**

```bash
for img in $(docker compose --env-file knovas.env.example config --images | grep -E "knovas-connector|docbridge"); do
  echo "== $img"
  docker run --rm --entrypoint sh "$img" -c 'pip show pymupdf-layout >/dev/null 2>&1 && echo "LICENCE FAIL" || echo "licence ok"; python -c "import knovas_extract as k; print(k.__version__)"'
done
docker run --rm --entrypoint python "$(docker compose --env-file knovas.env.example config --images | grep knovas-connector)" -c "from knovas_extract._ocr.backend import select_backend; print(select_backend('auto', language='deu+eng').name)"
docker inspect --format '{{json .Config.Cmd}}' "$(docker compose --env-file knovas.env.example config --images | grep knovas-connector)"
```
Expected: `licence ok` twice; `0.4.0a1` twice; `tesserocr`; the Connector CMD contains `-k gthread --threads 4`.

### Task VER-4: End to end against the mock

**Files:** `$SP/e2e/` (corpus and scripts, not in the repository).

- [ ] **Step 1: Corpus** — a script `$SP/e2e/make_corpus.py` (run with `$SP/venvs/rc/Scripts/python`) writes into
`$SP/e2e/docs/Muster AG/GJ 2024/`: `bericht.pdf` (born-digital, 3 pages with text, made with `fitz`),
`scan.pdf` (2 image-only pages rendered from text at 200 dpi, like the library's `_raster_of` helper),
`bilanz.docx` (a paragraph, a 3×3 table `Position | 2023 | 2022` with amounts, a paragraph),
`offerte.eml` (HTML-only body with `Gr&uuml;ezi` and two paragraphs, `Date:` header).

- [ ] **Step 2: Run the stack with the mock** — `MOCK_DOC_FIELDS=filters MOCK_RECORD_PARTS=1`, the Connector
watching `$SP/e2e/docs` with a source profile: fixed `doc_type` none, path template `{mandant}/{period}/**`,
opt-ins `email_date`, `email_doc_type`, `document_author`, `language`, `keywords`, `document_status`.
Use the repository's documented local setup (`KnovasConnector/docs/local-setup.md`, compose profile `mock`)
and trigger one cycle with `POST /sync` (it must now return without killing the worker).

- [ ] **Step 3: Assertions** (a script `$SP/e2e/check.py` reading `GET /_mock/parts` and the Connector's
`GET /sync/status`):
1. no document recorded partial, `rc_ocr_backend_degraded_total` unchanged for `bericht.pdf`;
2. every part of `bericht.pdf` and `scan.pdf` carries a `page_number`;
3. the parts of `bilanz.docx` contain the line `Umsatz | 2023: 1'234.00 | 2022: 1'100.00`;
4. the parts of `offerte.eml` contain `Grüezi` and no `&uuml;`;
5. every init carried `fields` with `mandant = Muster AG` and `period = GJ 2024`; `offerte.eml` carried
   `document_date` and `doc_type = correspondence.email`; the status shows the fields as staged;
6. `POST /secured/query` on the mock with `"where": {"mandant": "Muster AG"}` answers `"where": {"applied": true}`;
7. `/sync/status` shows `extraction.knovas_extract_version == "0.4.0a1"` and `outdated == 0` after the cycle;
8. a second cycle uploads nothing (no new parts recorded).

### Task VER-5: Whole-branch review and hand-off

- [ ] **Step 1:** Run the review workflow over `git diff origin/main...HEAD` in `$WT` (dimensions: correctness,
privacy (no values in logs), Connector/Platform mirror consistency, docs accuracy against 1.5.0, tests that
assert behaviour). Fix confirmed findings with tests.
- [ ] **Step 2:** `cd /e/Knovas && graphify update .`
- [ ] **Step 3:** Ask the user before pushing `worktree-fields-extract-upgrade` and opening the PR; the PR body
lists the parts (INT, EXT, PIN, FLD, RCF, REX, VER), the library release, and the re-ingest note
(administrators decide; uploads are billed), ending with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

**Never touch the user's running stack.** The machine runs the user's own compose project
`knovascomponents` (docbridge-web on 127.0.0.1:8081, knovas-connector on 127.0.0.1:5001, platform-db,
knovas-mock, experiments-runner). Every compose command in VER-3/VER-4 uses its own project name and
ports: `docker compose -p kc-verify …` with `DOCBRIDGE_WEB_BIND`/ports moved (e.g. 18081, 15001), and
`docker compose -p kc-verify down -v` at the end. Never run `docker compose` without `-p kc-verify`,
never `down`/`build`/`up` the `knovascomponents` project.

---

## Appendix — notes from the section drafts

### Notes from the INT draft

Part B1 (Integration) is below, ready to paste. I checked it by replaying INT-2 through INT-6 in a scratch copy of `$FIX` (scripts are in `$SP/int-sim/`, nothing was written to `$WT`, `$MERGED`, `$FIX` or `$LIB`). Every anchor matched exactly once, or as often as the plan says. Every DB-free test named in the plan failed and then passed as stated. The PostgreSQL-backed tests were not run, because the shared test DB was in use by another run.

**Things you need to act on**
1. **Escape decoding in tool parameters.** Every tool parameter decodes backslash-u plus 4 hex digits into the actual character. I saw this in Write, Edit and Bash, and expect the same here. Bash also collapses a double backslash into one. My section contains neither sequence: every `.py` edit is anchored on an ASCII fragment.
   - Other sections that quote Python strings in this repo's ASCII-escape style (umlauts, en dashes) will reach you with the real characters. That breaks the ASCII rule and the Edit anchors. Please check them before writing the plan file.
   - To write a literal backslash-u, type `\` followed by `u` and the hex digits.
2. **`pf-pytest` needs `PYTHONUTF8=1` on Windows.** Without it, two `test_experiments_frontend.py::TestSearchHitCards` tests fail on garbled umlauts in node's output. The trial runs used `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`. Please add this to the contract's definition.
3. **Full Platform runs on Windows need `--ignore=tests/test_experiments_runner_client.py`.** `socketserver.UnixStreamServer` does not exist on Windows.
4. **Platform test runs must be serial.** My one attempt against `kc-plan-pg` failed at setup with `type "citext" does not exist`, because another run was using the DB at the same time. My aborted run may have left one or two empty `t…` test schemas in `knovas_platform_test`. They are harmless. I did not drop them, because one of the three may belong to the other run.

**Additions beyond the brief, all in the plan**
- **INT-3 is split** into INT-3a (listing), INT-3b (experiments search), INT-3c (nginx 180 s, with a new test file) and INT-3d (image CMD). INT-4 to INT-6 keep their numbers.
  - INT-3c also adds a RELEASE_NOTES section, because existing host-nginx installations must renew their site config.
  - INT-3d also gives the image's CMD `--access-logfile=-`, as compose has.
- **INT-5 renames more than the listed lines:**
  - Four console messages that 08228ba missed: `knovas_connector_client.py:138/151/231` and `ingestion_compiler.py:381`.
  - The metric help text at `doc_fields_metrics.py:96` (it is in the spec).
  - Four lines that PR #22 renames outside 08228ba: RELEASE_NOTES 100/108/129 and `KnovasPlatform/docs/README.md:26`. They get PR #22's exact text, so the later merge sees identical changes.
  - The RELEASE_NOTES entry uses a619d10's wording, which I found in `wt-main-pr22`.
- **INT-5 leaves the engineering pack's option value `Knovas Connector` alone** on purpose: it is stored data, and renaming it needs a migration.
- **INT-6 rewrites a few more texts with the same "only once Knovas has enabled it" wording:** `docs/KnovasAPI/README.md`, `docs/KnovasAPI/Secure_API.md`, `KnovasConnector/.env.example`, the `aus` table cell in `document-fields.md`, and the doctor.sh "off" line (with a mock test).

**Open question for the user (S1).** Spec §12 says the server today keeps BROKERED Connector entity values as unlinked names. The fields docs say Knovas refuses such uploads with `401 assertion_rejected` before S1. INT-6 leaves the S1 sentences unchanged; someone should decide which is true for 1.5.0.

**Interfaces other sections must respect**
- `doc_fields_routes.attach` gains a required `split=` keyword (INT-3a). Any FLD edit of `documents_find` must keep the split step.
- The Platform Dockerfile's `CMD` changes in INT-3d. PIN tasks must keep it.
- German UI text added later should say "der Knovas Connector".
- `$SP/rc_leftovers.py` (created in INT-5) can be rerun by any later task that adds user-facing text.

======================================================================

### Notes from the PIN draft

The PIN section is below. Every code block, anchor and expected test output in it was reproduced in a scratch copy of the merged tree before writing it up. Things you should know first:

- **The audit path in the contract is wrong.** The file lives at `C:\Users\siran\.claude\projects\E--Knovas-KnovasComponents--claude-worktrees-fields-extract-upgrade\c6f4754c-…\tool-results\toolu_018MosC2fVZRcoN6RKHEVtH6.txt`; I used that one.
- **Database on port 55433 is missing `citext`.** A PostgreSQL is listening on 127.0.0.1:55433, the default test address, but has no `citext`. My scratch run of `test_web_admin_ingestion.py` hit it and failed with `type "citext" does not exist`. The test fixture creates and drops a schema per test, so I don't think anything was left behind, but I couldn't confirm. Check that this is the `kc-plan-pg` container before any `pf-pytest` with `PLATFORM_DB_REQUIRED=true`.
- **CI tests the interim pin until Task A6.** That pin is `b5d4540`, which lacks the library PR's fixes, so EXT tests that need DOCX layout mode or the fail-soft sentence cap pass locally against the editable library but not in CI until A6.
- **PIN-3 is not a separate task.** The `[rtf]` extra and the striprtf licence note are folded into PIN-1. A Connector test of an RTF-only Outlook mail would need a binary fixture, because msgforge (the repo's only MSG writer) always adds a plain-text body; the intro explains this.
- **Extra public names beyond the contract** in `sync.extract_metrics`: `RC_VERSION` (kept equal to pyproject's version by a test), `extraction_info()`, `knovas_extract_version()`, `label_sets()`, `WARNING_CLASSES`. They are listed in PIN-4's Interfaces. REX adds `outdated` inside `sync_status`, not inside `extraction_info()`, because those four values also label `rc_build_info`.

How it was checked:
- **Anchors.** All anchors applied exactly once to a mirror of the post-rename tree, with EXT's `docx_text_mode()` stubbed in.
- **Red/green.** Every red and green step reproduced: PIN-1, 2A to 2C, 4A to 4C.
- **Full suites.** The full Connector suite passes in the mirror (known Windows-only failures excluded), as do the Platform System-tab tests.
- **Workflow and scripts.** `ci.yml` parses as YAML. The shell scripts pass in Git Bash, and the assert script passes against a git install of the pin.
- **Docker.** Build args reaching heredoc `RUN` steps was confirmed with Docker 28; the throwaway image was removed.
- **Transcription.** A script confirmed every validated string appears verbatim in the text below. Python blocks are ASCII-only.

Files are in `C:\Users\siran\AppData\Local\Temp\claude\E--Knovas-KnovasComponents\c6f4754c-3eb8-4feb-9b99-b78ab17ffd1c\scratchpad\pin-validate\`:
- plan-pin.md — the verified copy of the section below; use it if pasting from here goes wrong
- verify_plan.py

---
