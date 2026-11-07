# A sync component: one deployable connection between a company's source and its Brain (connector protocol v1).
# Build (the mapping runner comes from the private knowledge base, read with a build secret that stays in no layer):
#   docker build --secret id=kb_read_token,env=KB_READ_TOKEN -t sync-component:0.1.0 KnovasPlatform/components/sync_component
# or with the repository's compose file: docker compose --env-file knovas.env --profile sync-component build sync-component

# kustomize for the runner's kustomize_build extractor (V1-G6). Without it a kustomization is read as files, its
# patches and components are not applied, and a mapping that needs them (a company's `runtime` mapping) is stopped.
# Pinned: the archive must match the SHA-256 the release publishes in its checksums.txt (kustomize/v5.4.3, taken
# 2026-11-06) before anything in it is unpacked or run; never piped from curl into tar. This stage keeps curl out of
# the image. To bump: change the version and both checksums together (tests/test_sync_component_image.py).
FROM python:3.11-slim AS kustomize
ARG KUSTOMIZE_VERSION=5.4.3
ARG KUSTOMIZE_SHA256_AMD64=3669470b454d865c8184d6bce78df05e977c9aea31c30df3c669317d43bcc7a7
ARG KUSTOMIZE_SHA256_ARM64=1b515578b0af12c15d9856720066ce2fe66756d63785b2cbccaf2885beb2381c
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "$arch" in \
      amd64) sha="$KUSTOMIZE_SHA256_AMD64" ;; \
      arm64) sha="$KUSTOMIZE_SHA256_ARM64" ;; \
      *) echo "kustomize: no pinned checksum for $arch" >&2; exit 1 ;; \
    esac; \
    tgz=/tmp/kustomize.tar.gz; \
    curl -fsSL -o "$tgz" \
      "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_${arch}.tar.gz"; \
    echo "$sha  $tgz" | sha256sum -c -; \
    tar -xzf "$tgz" -C /usr/local/bin kustomize; \
    rm -f "$tgz"; \
    /usr/local/bin/kustomize version

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The same release of the runner as the Platform's image (docbridge_integration/Dockerfile).
ARG BRAIN_RUNNER_GIT_REF=646f222124324fab28cf42002ac3962ab3876127
RUN --mount=type=secret,id=kb_read_token,required=true \
    git config --global url."https://x-access-token:$(cat /run/secrets/kb_read_token)@github.com/".insteadOf "https://github.com/" \
    && pip install --no-cache-dir --no-deps \
       "brain-runner @ git+https://github.com/Seifeddini/KnowledgeBase.git@${BRAIN_RUNNER_GIT_REF}#subdirectory=scripts/brain_runner" \
    && git config --global --unset-all url."https://x-access-token:$(cat /run/secrets/kb_read_token)@github.com/".insteadOf

COPY --from=kustomize /usr/local/bin/kustomize /usr/local/bin/kustomize
RUN kustomize version

COPY src/ ./src/
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 SYNC_DATA_DIR=/data

RUN useradd --system --uid 10001 component && mkdir -p /data && chown component /data
USER component
VOLUME /data

ENTRYPOINT ["python", "-m", "sync_component"]
