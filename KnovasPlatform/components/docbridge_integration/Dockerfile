# DocBridge-Knovas Integration Dockerfile

FROM python:3.11-slim

LABEL maintainer="Knovas Team"
LABEL description="DocBridge-Knovas Integration Services"

# Set working directory
WORKDIR /app

# Install system dependencies (git: pip install from git+https needs it)
RUN apt-get update && apt-get install -y \
    git \
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
    && rm -rf /var/lib/apt/lists/*

# Install Microsoft ODBC Driver for SQL Server (if needed for Access alternative)
# Note: For Access databases, you may need to install mdbtools
RUN apt-get update && apt-get install -y \
    mdbtools \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies, except knovas-extract (handled below).
RUN grep -viE '^[[:space:]]*knovas-extract' requirements.txt > /tmp/requirements-no-extract.txt \
    && pip install --no-cache-dir -r /tmp/requirements-no-extract.txt

# knovas-extract>=0.3 may lag PyPI (latest published is 0.2.0). Default: install
# from git main. After 0.3.0 is published, rebuild with an empty ref to use PyPI:
#   docker compose build --build-arg KNOVAS_EXTRACT_FROM_GIT= docbridge-web
ARG KNOVAS_EXTRACT_FROM_GIT=github.com/Seifeddini/knovas-extract-python.git@main
RUN if [ -n "${KNOVAS_EXTRACT_FROM_GIT}" ]; then \
      pip install --no-cache-dir \
        "knovas-extract[pdf,docx,msg,markdown,sentences] @ git+https://${KNOVAS_EXTRACT_FROM_GIT}"; \
    else \
      pip install --no-cache-dir \
        "knovas-extract[pdf,docx,msg,markdown,sentences]>=0.3"; \
    fi

# Copy application code
COPY src/ ./src/
COPY config/ ./config/

# Create directories for data and logs
RUN mkdir -p /app/data /app/logs

# Set environment variables
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Expose internal app port (NGINX proxies externally)
EXPOSE 5000

# Default command (can be overridden)
CMD ["gunicorn", "--workers=2", "--threads=4", "--bind=0.0.0.0:5000", "web_interface.wsgi:app"]
