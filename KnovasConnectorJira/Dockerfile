# The Jira connector at a company's site, behind Knovas Link (connector protocol 2.0; FEAT-2026-11-10-write-back J-4).
#   docker build -t jira-connector:0.1.0 KnovasConnectorJira
# or: docker compose --env-file knovas.env --profile jira-connector build jira-connector
# Built from this folder alone (the release workflow's context): the connector kit is the byte-for-byte copy under
# vendor/ (tests/test_image.py). No secret is in the image: a source's credential comes from the site's
# knovas-sources.env at run time, the component's token from Link's volume.
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vendor/ ./vendor/
COPY src/ ./src/
ENV PYTHONPATH=/app/src:/app/vendor PYTHONUNBUFFERED=1
# The command line under its own name, also for `docker compose run --rm <service> dump-stream ...`.
RUN printf '#!/bin/sh\nexec python -m knovas_connector_jira "$@"\n' > /usr/local/bin/knovas-connector-jira \
    && chmod 755 /usr/local/bin/knovas-connector-jira \
    && python -c "import knovas_connector_jira.connector as c; print('jira-connector', c.VERSION, 'kit', c.PROTOCOL)"

RUN useradd --system --uid 10003 --no-create-home connector
USER connector

ENTRYPOINT ["knovas-connector-jira"]
CMD ["serve"]
