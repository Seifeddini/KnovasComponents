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

# knovas-extract >=0.3 (PDF-OCR) ist noch nicht auf PyPI veroeffentlicht --
# dort endet die Reihe bei 0.2.0. Es kommt deshalb per Default aus git,
# analog zu dem, was RemoteController/Dockerfile in 88d0c3c bereits tut.
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
