#!/usr/bin/env bash
# HTTPS in front of the Platform: a Let's Encrypt certificate and the hardened
# host nginx site, in one step.
#
#   sudo ./scripts/host-https.sh knovas.example.ch it@example.ch
#
# Needs: the name's DNS A record pointing at this server, port 80 reachable
# from the internet (Let's Encrypt checks it; the site answers nothing else
# there but a redirect), nginx and certbot installed (the Azure cloud-init does
# both). Safe to re-run: it renews nothing early and rewrites only its own files.
set -euo pipefail

FQDN="${1:-}"
EMAIL="${2:-}"
if [[ -z "$FQDN" || -z "$EMAIL" ]]; then
  echo "Usage: sudo $0 <public name, e.g. knovas.example.ch> <email for Let's Encrypt notices>" >&2
  exit 1
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo: it writes /etc/nginx and asks Let's Encrypt for a certificate." >&2
  exit 1
fi
for tool in nginx certbot; do
  command -v "$tool" >/dev/null || { echo "Missing $tool: sudo apt-get install -y nginx certbot" >&2; exit 1; }
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$ROOT_DIR/KnovasPlatform/deploy/host-nginx/knovas-platform.conf.example"
KNOVAS_ENV="$ROOT_DIR/knovas.env"
PORT=8081
if [[ -f "$KNOVAS_ENV" ]]; then
  # shellcheck source=../KnovasPlatform/scripts/lib/read_env.sh
  source "$ROOT_DIR/KnovasPlatform/scripts/lib/read_env.sh"
  PORT="$(read_env_var DOCBRIDGE_WEB_PORT 8081 "$KNOVAS_ENV")"
  PLATFORM_URL="$(read_env_var KNOVAS_PLATFORM_URL "" "$KNOVAS_ENV")"
  if [[ -n "$PLATFORM_URL" && "${PLATFORM_URL%/}" != "https://$FQDN" ]]; then
    echo "NOTE: knovas.env has KNOVAS_PLATFORM_URL=$PLATFORM_URL; people will use https://$FQDN."
    echo "      Set KNOVAS_PLATFORM_URL=https://$FQDN, then ./scripts/setup.sh && ./scripts/start.sh"
  fi
fi

SITE=/etc/nginx/sites-available/knovas
WEBROOT=/var/www/letsencrypt
LIVE="/etc/letsencrypt/live/$FQDN"
mkdir -p "$WEBROOT"
install -m 0644 "$ROOT_DIR/KnovasPlatform/deploy/host-nginx/knovas-login-limit.conf" \
  /etc/nginx/conf.d/knovas-login-limit.conf

render() {
  # $1: 443 block with certificate (full) or port 80 only (bootstrap)
  sed -e "s/knovas\.example\.internal;/$FQDN;/g" \
      -e "s#/etc/ssl/certs/knovas\.example\.internal-fullchain\.pem#$LIVE/fullchain.pem#" \
      -e "s#/etc/ssl/private/knovas\.example\.internal\.key#$LIVE/privkey.pem#" \
      -e "s#proxy_pass http://127\.0\.0\.1:8081;#proxy_pass http://127.0.0.1:$PORT;#g" \
      "$TEMPLATE" \
  | if [[ "$1" == bootstrap ]]; then
      # Only the port 80 server block: nginx refuses a TLS block whose
      # certificate does not exist yet.
      awk 'BEGIN{n=0} /^server \{/{n++} n<=1 {print}'
    else
      cat
    fi
}

enable_site() {
  ln -sf "$SITE" /etc/nginx/sites-enabled/knovas
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl reload nginx
}

if [[ ! -f "$LIVE/fullchain.pem" ]]; then
  echo "==> Requesting a Let's Encrypt certificate for $FQDN"
  render bootstrap > "$SITE"
  enable_site
  certbot certonly --webroot -w "$WEBROOT" -d "$FQDN" -m "$EMAIL" \
    --agree-tos --non-interactive --no-eff-email
else
  echo "==> Certificate for $FQDN already there (certbot renews it by itself)"
fi

echo "==> Installing the site"
render full > "$SITE"
enable_site

# certbot's timer renews through the same webroot; nginx must then load it.
install -d /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'HOOK'
#!/bin/sh
systemctl reload nginx
HOOK
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

echo "==> Checking"
if curl -fsS --max-time 10 --resolve "$FQDN:443:127.0.0.1" "https://$FQDN/health" >/dev/null; then
  echo "    https://$FQDN answers (through nginx, to the Platform on 127.0.0.1:$PORT)"
else
  echo "    nginx is up, but the Platform behind it did not answer yet."
  echo "    Start it (./scripts/start.sh), then: curl -I https://$FQDN"
fi
