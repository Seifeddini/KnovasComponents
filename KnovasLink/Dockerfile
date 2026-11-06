# Knovas Link: the one component per site that talks to Knovas (connector protocol 1.1).
#   docker build -t knovas-link:1.1.0 KnovasLink
# No secret is in the image: the one-time token comes from the site's env file, the key is made at first start on
# the /data volume, and the components' tokens are written to the /link volume.
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY contracts/ ./contracts/
COPY src/ ./src/
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 LINK_DATA_DIR=/data LINK_TOKENS_DIR=/link LINK_PORT=8460

RUN useradd --system --uid 10460 --no-create-home link \
    && mkdir -p /data /link && chown link /data /link && chmod 700 /data && chmod 755 /link
USER link
VOLUME ["/data", "/link"]

# Listens on the site's internal network only; the install package publishes no port.
EXPOSE 8460
# Up when the local API answers at all (an unauthenticated ping is refused with 401, which is enough here).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request,urllib.error,sys\ntry: urllib.request.urlopen('http://127.0.0.1:8460/local/v1/ping', timeout=4)\nexcept urllib.error.HTTPError as e: sys.exit(0 if e.code == 401 else 1)\nexcept Exception: sys.exit(1)"]

ENTRYPOINT ["python", "-m", "knovas_link"]
