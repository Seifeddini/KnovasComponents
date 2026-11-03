# A sync component: one deployable connection between a company's source and its Brain (connector protocol v1).
# Build (the mapping runner comes from the private knowledge base, read with a build secret that stays in no layer):
#   docker build --secret id=kb_read_token,env=KB_READ_TOKEN -t sync-component:0.1.0 KnovasPlatform/components/sync_component
# or with the repository's compose file: docker compose --profile sync-component build sync-component
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The same release of the runner as the Platform's image (docbridge_integration/Dockerfile).
ARG BRAIN_RUNNER_GIT_REF=43ea868e85b1be8ffa6299aa35289e74e28bf000
RUN --mount=type=secret,id=kb_read_token,required=true \
    git config --global url."https://x-access-token:$(cat /run/secrets/kb_read_token)@github.com/".insteadOf "https://github.com/" \
    && pip install --no-cache-dir --no-deps \
       "brain-runner @ git+https://github.com/Seifeddini/KnowledgeBase.git@${BRAIN_RUNNER_GIT_REF}#subdirectory=scripts/brain_runner" \
    && git config --global --unset-all url."https://x-access-token:$(cat /run/secrets/kb_read_token)@github.com/".insteadOf

COPY src/ ./src/
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 SYNC_DATA_DIR=/data

RUN useradd --system --uid 10001 component && mkdir -p /data && chown component /data
USER component
VOLUME /data

ENTRYPOINT ["python", "-m", "sync_component"]
