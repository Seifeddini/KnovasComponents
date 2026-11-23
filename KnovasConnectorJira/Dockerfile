# The Jira connector at a company's site, behind Knovas Link (connector protocol 2.0; FEAT-2026-11-10-write-back J-4).
# Built from the repository's root, so that the connector kit is in the context (the release workflow's context too):
#   docker build -f KnovasConnectorJira/Dockerfile -t jira-connector:0.1.0 .
# or: docker compose --env-file knovas.env --profile jira-connector build jira-connector
# Dockerfile.dockerignore beside this file lets only what the image needs into that context. The connector kit is the
# one package in KnovasConnectorKit, installed as knovas-connector-kit (tests/test_image.py). No secret is in the
# image: a source's credential comes from the site's knovas-jira.env at run time, the component's token from Link's
# volume.
FROM python:3.11-slim

WORKDIR /app
COPY KnovasConnectorJira/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The connector kit from its own folder. Its pins are in requirements.txt above, so it is installed without its
# dependencies, and built with the base image's setuptools (nothing more is downloaded). The contracts are checked
# against their lock here, so an image whose contract files changed on the way (CRLF) is not built.
COPY KnovasConnectorKit/ /tmp/knovas-connector-kit/
RUN pip install --no-cache-dir --no-deps --no-build-isolation /tmp/knovas-connector-kit \
    && rm -rf /tmp/knovas-connector-kit \
    && python -c "from importlib.metadata import version; from knovas_connector_kit import contracts; contracts.schemas(); print('kit', version('knovas-connector-kit'))"

COPY KnovasConnectorJira/src/ ./src/
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1
# The command line under its own name, also for `docker compose run --rm <service> dump-stream ...`.
RUN printf '#!/bin/sh\nexec python -m knovas_connector_jira "$@"\n' > /usr/local/bin/knovas-connector-jira \
    && chmod 755 /usr/local/bin/knovas-connector-jira \
    && python -c "import knovas_connector_jira.connector as c; print('jira-connector', c.VERSION, 'kit', c.PROTOCOL)"

RUN useradd --system --uid 10003 --no-create-home connector
USER connector

ENTRYPOINT ["knovas-connector-jira"]
CMD ["serve"]
