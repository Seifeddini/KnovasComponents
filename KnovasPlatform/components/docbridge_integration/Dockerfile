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
    && rm -rf /var/lib/apt/lists/*

# Install Microsoft ODBC Driver for SQL Server (if needed for Access alternative)
# Note: For Access databases, you may need to install mdbtools
RUN apt-get update && apt-get install -y \
    mdbtools \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

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
