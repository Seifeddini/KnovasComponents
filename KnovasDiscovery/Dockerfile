# Knovas Discovery in a container: for a scanning machine with Docker but without Python.
# cifs-utils lets the container mount an SMB share itself (needs --cap-add SYS_ADMIN --cap-add DAC_READ_SEARCH);
# the usual way is a Docker volume of type cifs, mounted read-only (README.md).
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends cifs-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/knovas-discovery
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && knovas-discovery catalogue --check

WORKDIR /work
ENTRYPOINT ["knovas-discovery"]
CMD ["--help"]
