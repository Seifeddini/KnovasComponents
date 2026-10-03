FROM python:3.12-slim AS builder
WORKDIR /build
# pip install from git+https needs git in the builder image.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./

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
#   docker compose build --build-arg KNOVAS_EXTRACT_GIT_REF=<sha> remote-controller
#   docker compose build --build-arg KNOVAS_EXTRACT_GIT_REF= remote-controller   # PyPI
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

FROM python:3.12-slim AS runtime
# Tesseract + the language packs OCR may be configured with (RC_TESSERACT_LANG:
# deu+eng default, fra / ita per tenant). Models ship in the image: extraction
# performs no network I/O (GI-EXTRACT-05).
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-deu \
    tesseract-ocr-eng \
    tesseract-ocr-fra \
    tesseract-ocr-ita \
    && rm -rf /var/lib/apt/lists/*
# TESSDATA_PREFIX: where the Debian packages put the models, for tesserocr and
# the CLI alike. OMP_THREAD_LIMIT=1: one Tesseract thread per OCR worker — the
# RC parallelises per page, and OpenMP's own threads only fight each other.
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata \
    OMP_THREAD_LIMIT=1
RUN useradd --create-home --uid 10001 rcuser
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src ./src
COPY contracts ./contracts
COPY scripts/backfill_partial_ocr.py scripts/build_context_sidecars.py ./scripts/
# Create writable data dirs for the unprivileged runtime user. This also seeds
# the rc-config and rc-state named volumes with rcuser ownership when they are
# mounted, so no post-start chown is required.
RUN mkdir -p /app/config /var/rc-state && chown rcuser:rcuser /app/config /var/rc-state
# Default the sync state into the writable, volume-backed directory. The OCR
# disk cache (.rc-ocr-cache.db) lives beside it.
ENV RC_SYNC_STATE_PATH=/var/rc-state/.rc-sync-state.json
USER rcuser
ENV PYTHONPATH=/app/src
EXPOSE 5001
# One worker process -- the continuous sync scheduler and its locks live in it
# (docs/SETUP.md) -- with four request threads: gthread's main loop keeps
# reporting while a long POST /sync runs, so --timeout no longer kills the
# worker and the scheduler thread with it, and GET /sync/status answers
# meanwhile. Shell form so RC_GUNICORN_TIMEOUT (default 120 s) is read at
# start; exec keeps gunicorn PID 1 so it receives docker's SIGTERM.
CMD exec gunicorn -b 0.0.0.0:5001 -w 1 -k gthread --threads 4 --timeout "${RC_GUNICORN_TIMEOUT:-120}" app:app
