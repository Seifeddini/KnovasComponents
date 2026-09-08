# Knovas — Demo-Kanzlei (client pack)

This is the only document you need. One folder of synthetic Swiss law-firm files, then search.

**You need:** Docker, a Knovas tenant, and the three certificate files Knovas sent you.

## 1. Certificates

Put these in `certs/` at the repo root (names must match):

- `client-cert.pem`
- `client-key.pem`
- `ca-root.pem`

```bash
chmod 600 certs/client-key.pem
```

## 2. Config

```bash
cp knovas.env.example knovas.env
```

Edit four lines in `knovas.env`:

```bash
KNOVAS_API_URL=https://api.knovas.ch:8443
KNOVAS_PLATFORM_URL=http://192.168.1.15:8081
KNOVAS_DOCUMENTS_PATH=/home/YOU/corpus/kanzlei
PLATFORM_ADMIN_EMAIL=you@your-firm.example
DOCBRIDGE_WEB_BIND=0.0.0.0
WEB_SESSION_COOKIE_SECURE=false
```

`KNOVAS_DOCUMENTS_PATH` is the **host** folder of the Akten (create it in the next step). `DOCBRIDGE_WEB_BIND=0.0.0.0` is what makes the UI reachable at `http://192.168.1.15:8081` from another machine; without it Docker listens on loopback only. A second checkout on this server keeps that bind and moves the port (8082); set `KNOVAS_PLATFORM_URL` to that same IP with the new port, or let setup rewrite the port if the URL already has one.

## 3. Generate the files (once)

```bash
cd RemoteController
python3 -m venv ../.venv
source ../.venv/bin/activate
pip install -U pip
pip install -r scripts/demo_kanzlei/requirements.txt
python scripts/demo_kanzlei/cli.py -v build --full --out ~/corpus/kanzlei --skip-zefix
python scripts/demo_kanzlei/cli.py verify --out ~/corpus/kanzlei
python scripts/demo_kanzlei/cli.py touch --out ~/corpus/kanzlei
```

Ubuntu may block system `pip` — the venv above is the fix. `KNOVAS_DOCUMENTS_PATH` must be that same `--out` folder.

## 4. Start

```bash
cd /path/to/KnovasComponents   # repo root
./scripts/setup.sh
./scripts/start.sh
```

## 5. Sign in and search

```bash
docker compose --env-file knovas.env exec docbridge-web \
  cat /app/data/platform-admin-bootstrap
```

Open the URL `./scripts/start.sh` printed. Use `PLATFORM_ADMIN_EMAIL` and that one-time password. Change it after login.

A second copy on the same server is named after the folder (`KnovasDemo` → `knovasdemo-…`) and uses the next free ports (here **8082**, not 8081). Docker still binds loopback, same as the other stack. The other stack is reachable in the browser because **host nginx** on 443 proxies to `127.0.0.1:8081`. Point a second vhost at **8082**: copy the existing site, change `server_name` and `proxy_pass http://127.0.0.1:8082;`, set `KNOVAS_PLATFORM_URL` to that https URL, then `nginx -t` and reload. DNS for the new name must hit this server.

Or, without a second name, in `knovas.env`: `DOCBRIDGE_WEB_BIND=0.0.0.0`, `WEB_SESSION_COOKIE_SECURE=false`, `KNOVAS_PLATFORM_URL=http://THIS_SERVER:8082`, then setup + start, and open `http://THIS_SERVER:8082`.

Try: **Schaffhauserstrasse**, **Meierhans**, **2024-017**.

Ingest of ~640 files is rate-limited; the first hits appear before the full set is up.

## If it fails

| What you see | What to do |
|--------------|------------|
| `Missing certs/…` | Step 1 — filenames must match exactly |
| `The container name "/platform-db" is already in use` | Pull this version — names are per-folder now; then `./scripts/start.sh` |
| `KNOVAS_DOCUMENTS_PATH does not exist` | Step 3, then the same absolute path in `knovas.env` |
| Browser cannot connect | Host nginx still points at 8081. Second vhost → `127.0.0.1:8082`, or `DOCBRIDGE_WEB_BIND=0.0.0.0` in `knovas.env` |
| Login form comes back immediately | `WEB_SESSION_COOKIE_SECURE=false` in `knovas.env`, then `./scripts/setup.sh && ./scripts/start.sh` |
| Health `watch_roots` not ok | Path must be the `kanzlei` folder, not its parent; then setup + start again |
| Search is empty | Run `touch` (end of step 3), wait for ingest |

Stop: `./scripts/stop.sh`. Reset admin password: `./scripts/admin-password.sh`.
