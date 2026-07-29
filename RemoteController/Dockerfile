FROM python:3.12-slim AS builder
WORKDIR /build
# pip install from git+https needs git in the builder image.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./

# knovas-extract>=0.3 (PDF OCR) may lag PyPI. Default: install from git main.
# After 0.3.0 is published, rebuild with an empty ref to use PyPI instead:
#   docker compose build --build-arg KNOVAS_EXTRACT_FROM_GIT= remote-controller
ARG KNOVAS_EXTRACT_FROM_GIT=github.com/Seifeddini/knovas-extract-python.git@main
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
        "knovas-extract[pdf,docx,msg,html,markdown,sentences] @ git+https://${KNOVAS_EXTRACT_FROM_GIT}"; \
    else \
      pip install --no-cache-dir \
        "knovas-extract[pdf,docx,msg,html,markdown,sentences]>=0.3"; \
    fi

FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-deu \
    tesseract-ocr-eng \
    tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 rcuser
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src ./src
COPY contracts ./contracts
# Create writable data dirs for the unprivileged runtime user. This also seeds
# the rc-config and rc-state named volumes with rcuser ownership when they are
# mounted, so no post-start chown is required.
RUN mkdir -p /app/config /var/rc-state && chown rcuser:rcuser /app/config /var/rc-state
# Default the sync state into the writable, volume-backed directory.
ENV RC_SYNC_STATE_PATH=/var/rc-state/.rc-sync-state.json
USER rcuser
ENV PYTHONPATH=/app/src
EXPOSE 5001
# Single worker: continuous sync scheduler uses in-process locks (see docs/SETUP.md).
CMD ["gunicorn", "-b", "0.0.0.0:5001", "-w", "1", "--timeout", "120", "app:app"]
