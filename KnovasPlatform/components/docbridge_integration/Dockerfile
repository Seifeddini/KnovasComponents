# DocBridge-Knovas Integration Dockerfile

FROM python:3.11-slim

LABEL maintainer="Knovas Team"
LABEL description="DocBridge-Knovas Integration Services"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    unixodbc \
    unixodbc-dev \
    odbcinst \
    libpq-dev \
    gcc \
    g++ \
    tesseract-ocr \
    tesseract-ocr-deu \
    tesseract-ocr-eng \
    tesseract-ocr-fra \
    tesseract-ocr-ita \
    git \
    && rm -rf /var/lib/apt/lists/*

# OCR runs inside the admin upload's extraction child (knovas_extract_upload):
# one Tesseract thread per worker (the pool parallelises per page, OMP would
# oversubscribe the 4 cores shared with the search UI) and the Debian
# tessdata folder the RC image uses as well. Mirrors RemoteController/Dockerfile.
ENV OMP_THREAD_LIMIT=1 \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# Install Microsoft ODBC Driver for SQL Server (if needed for Access alternative)
# Note: For Access databases, you may need to install mdbtools
RUN apt-get update && apt-get install -y \
    mdbtools \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies: everything but knovas-extract first, so a pin
# bump below rebuilds only the knovas-extract layer.
RUN grep -v '^knovas-extract' requirements.txt > /tmp/requirements-other.txt \
    && pip install --no-cache-dir -r /tmp/requirements-other.txt

# knovas-extract: one pin for both customer images. Keep both defaults equal
# to those in RemoteController/Dockerfile (scripts/ci/check_knovas_extract_pin.sh
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

# Copy application code
COPY src/ ./src/
COPY config/ ./config/

# Create directories for data and logs
RUN mkdir -p /app/data /app/logs /app/secrets/broker

# Set environment variables
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Expose internal app port (NGINX proxies externally)
EXPOSE 5000

# Default command (can be overridden). --timeout must exceed the admin
# upload's extraction ceiling (RC_EXTRACT_TIMEOUT_SECONDS, default 120 s):
# the extraction child is killed by the request thread at that ceiling, and
# gunicorn must not kill the worker first. The root docker-compose.yml passes
# DOCBRIDGE_WEB_TIMEOUT for the same reason -- keep it above the ceiling.
CMD ["gunicorn", "--workers=2", "--threads=4", "--timeout=180", "--bind=0.0.0.0:5000", "web_interface.wsgi:app"]
