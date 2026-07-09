FROM python:3.12-slim AS builder
WORKDIR /build
# knovas-extract is not on PyPI yet — install from a local BuildKit context.
# The build must supply a named context "extract-src" pointing at the
# knovas-extract-python source tree, e.g.:
#   docker build --build-context extract-src=../../knovas-extract-python \
#                -f Dockerfile ...
# Or via docker-compose.yml: services.<svc>.build.additional_contexts.
# Install knovas-extract FIRST so its version constraint in pyproject.toml
# resolves against the already-installed local copy (pip won't try PyPI).
COPY --from=extract-src . /tmp/knovas-extract
RUN pip install --no-cache-dir "/tmp/knovas-extract[pdf,docx,msg,markdown,sentences]"

COPY pyproject.toml README.md ./
# Install remaining deps from pyproject (skip knovas-extract; already installed above).
RUN pip install --no-cache-dir $(python -c "import tomllib; print(' '.join(d for d in tomllib.load(open('pyproject.toml','rb'))['project']['dependencies'] if not d.lower().startswith('knovas-extract')))")

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
# Single worker: continuous sync scheduler uses in-process locks (see docs/SETUP.md).
CMD ["gunicorn", "-b", "0.0.0.0:5001", "-w", "1", "--timeout", "120", "app:app"]
