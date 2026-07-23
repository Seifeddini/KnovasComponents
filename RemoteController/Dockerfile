FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
# Install runtime dependencies, including knovas-extract from PyPI. The
# pyproject constraint (knovas-extract>=0.2, no upper bound) means a fresh
# build always resolves the latest published release.
RUN pip install --no-cache-dir $(python -c "import tomllib; print(' '.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))")

FROM python:3.12-slim AS runtime
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
