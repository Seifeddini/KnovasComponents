FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir $(python -c "import tomllib; print(' '.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))")

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 rcuser
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src ./src
COPY contracts ./contracts
USER rcuser
ENV PYTHONPATH=/app/src
EXPOSE 5001
# Single worker: continuous sync scheduler uses in-process locks (see docs/hosting/installation.md).
CMD ["gunicorn", "-b", "0.0.0.0:5001", "-w", "1", "app:app"]
