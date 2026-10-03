FROM python:3.12-slim AS builder
WORKDIR /build
# pip install from git+https needs git in the builder image.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./

# knovas-extract is installed from git until the release the RC needs is on
# PyPI. KNOVAS_EXTRACT_REF selects the revision and defaults to a pinned sha
# (knovas-extract-python `main` on 2026-10-02): CI installs this same sha for
# the RC test job and fails when the two differ (.github/workflows/ci.yml,
# KNOVAS_EXTRACT_SHA), so the image and the tests run one extractor. Override
# per build with --build-arg KNOVAS_EXTRACT_REF=<tag or sha>. An empty
# KNOVAS_EXTRACT_FROM_GIT installs from PyPI instead:
#   docker compose build --build-arg KNOVAS_EXTRACT_FROM_GIT= remote-controller
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
# Single worker: continuous sync scheduler uses in-process locks (see docs/SETUP.md).
CMD ["gunicorn", "-b", "0.0.0.0:5001", "-w", "1", "--timeout", "120", "app:app"]
