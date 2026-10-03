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

Edit these lines in `knovas.env`:

```bash
KNOVAS_API_URL=https://api.knovas.ch:8443
KNOVAS_PLATFORM_URL=http://192.168.1.15:8081
KNOVAS_DOCUMENTS_PATH=/home/YOU/corpus/kanzlei
PLATFORM_ADMIN_EMAIL=you@your-firm.example
DOCBRIDGE_WEB_BIND=0.0.0.0
WEB_SESSION_COOKIE_SECURE=false
PLATFORM_TRUSTED_PROXY_HOPS=1
```

**Documents in OneDrive or SharePoint instead?** Replace the `KNOVAS_DOCUMENTS_PATH`
line with `KNOVAS_DOCUMENTS_URL=<the folder's address from the browser>` plus
`M365_CLIENT_ID` and `M365_CLIENT_SECRET`, and skip step 3. Nothing is copied to
the server; results open and preview in OneDrive/SharePoint. See
[../microsoft-365.md](../microsoft-365.md).

`KNOVAS_DOCUMENTS_PATH` is the **host** folder of the Akten (create it in the next step). `DOCBRIDGE_WEB_BIND=0.0.0.0` is what makes the UI reachable at `http://192.168.1.15:8081` from another machine; without it Docker listens on loopback only. A second checkout on this server keeps that bind and moves the port (8082); set `KNOVAS_PLATFORM_URL` to that same IP with the new port, or let setup rewrite the port if the URL already has one. `PLATFORM_TRUSTED_PROXY_HOPS=1` says that only the stack's own nginx stands in front of the app here; the default, 2, expects host nginx in front of it as well and would let a browser choose the address its session is recorded with.

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

Or, without a second name, in `knovas.env`: `DOCBRIDGE_WEB_BIND=0.0.0.0`, `WEB_SESSION_COOKIE_SECURE=false`, `PLATFORM_TRUSTED_PROXY_HOPS=1`, `KNOVAS_PLATFORM_URL=http://THIS_SERVER:8082`, then setup + start, and open `http://THIS_SERVER:8082`.

Try: **Schaffhauserstrasse**, **Meierhans**, **2024-017**.

A hit opens its preview when you click the card. A card marked «Datei nicht verfügbar», or a preview that says «Vorschau nicht verfügbar (HTTP 404)», means the text was ingested but the file itself is not on the mount — the first row of the table below.

Ingest of ~640 files is rate-limited; the first hits appear before the full set is up.

## If it fails

| What you see | What to do |
|--------------|------------|
| `Missing certs/…` | Step 1 — filenames must match exactly |
| `The container name "/platform-db" is already in use` | Pull this version — names are per-folder now; then `./scripts/start.sh` |
| `KNOVAS_DOCUMENTS_PATH does not exist` | Step 3, then the same absolute path in `knovas.env` |
| Browser cannot connect | Host nginx still points at 8081. Second vhost → `127.0.0.1:8082`, or `DOCBRIDGE_WEB_BIND=0.0.0.0` and `PLATFORM_TRUSTED_PROXY_HOPS=1` in `knovas.env` |
| Login form comes back immediately | `WEB_SESSION_COOKIE_SECURE=false` in `knovas.env`, then `./scripts/setup.sh && ./scripts/start.sh` |
| Health `watch_roots` not ok | Path must be the `kanzlei` folder, not its parent; then setup + start again |
| Search is empty | Run `touch` (end of step 3), wait for ingest |
| Results look right, but **Öffnen** fails and there is no preview | The files are not where the Platform looks. `./scripts/doctor.sh` names which of the two it is: a `KNOVAS_DOCUMENTS_PATH` that is not the ingested folder, or a Kennung on the Übernahme profile that is not `KNOVAS_IDENTIFIER_PREFIX` |
| **Öffnen** says `Open mapping not configured` | Expected when the documents live only on this server. **Öffnen** starts the file on the *user's* PC, so that PC needs its own path to it: `KNOVAS_SHARE_UNC=\\fileserver\share`, or `OPEN_CLIENT_LOCAL_ROOT=` the path they mount it at. No share at all? Put `OPEN_ALLOW_DEGRADED_DOWNLOAD_OPEN=true` in `knovas.env` for a Download button instead |

## Switches you may want

All go in `knovas.env`, then `./scripts/setup.sh && ./scripts/start.sh`.

| Setting | What it does |
|---------|--------------|
| `CORTEX_ENABLED=false` | Takes Cortex out of the navigation and refuses its routes. For a firm that only wants the search. |
| `IDENTITY_ENABLED=false` plus `COMPANY_LOGIN_NAME=` / `COMPANY_LOGIN_PASSWORD=` | One shared login for the whole firm instead of per-person accounts. Simpler to run; the audit record then says "the company" rather than who, and everyone who signs in can open every document. |
| `EXPERIMENTS_ENABLED=true` | Switches on **Experimente** (hypotheses, measurements, evaluations, decisions). Off by default and needs per-user accounts. Only people with the role `experimenter`, `experiments_manager` or `admin` (Verwaltung → Personen) see it; administrators have full manager rights in it. For everyone else it does not exist. |
| `EXPERIMENTS_ACCESS_GROUPS=` | The Knovas access group(s), comma-separated, that every experiment written to Knovas carries. Every experimenter needs them too, under Verwaltung → Personen. Empty (the default): nothing is written to Knovas and the module searches its own database only. |
| `EXPERIMENTS_INDEX_UNRESTRICTED=true` | Writes experiments to Knovas without an access group, so every user of the tenant can find them there. Only with a Knovas folder rule that restricts the `experiments/` prefix. |
| `COMPOSE_PROFILES=experiments` plus `EXPERIMENTS_RUNNER_URL=unix:///run/experiments-runner/runner.sock` | Builds and starts the sandbox for Python and Julia evaluators: its own container without any network, reached over a socket. `EXPERIMENTS_RUNNER_MEMORY` (default `3g`) and `EXPERIMENTS_RUNNER_CPUS` (default `2`, at most the host's CPU count or Docker refuses to create the container) set its limits. Without it the built-in evaluators still work. |
| `DOC_FIELDS_UI=off` | Hides document fields (Dokumentfelder) — filters, lists by field, values on cards. Knovas has them on for every account since 1.5.0, and the Platform shows them as far as Knovas serves them; this switch can only turn them off. See [document-fields.md](document-fields.md). |
| `DOC_FIELDS_EDIT_ROLES=admin,ingestion_manager` | Who may edit field values (default: administrators only). Adding `member` lets every member change the values of every document they can see. |
| `RC_DOC_FIELDS=off` | The Knovas Connector stops sending field values with uploads. |

## When a search looks wrong

```bash
./scripts/search-probe.sh "Sophie Keller"
```

Asks Knovas the query with the same certificates the app uses, then checks what
is actually indexed for the same words. It answers the only question worth
asking first: **coverage** (the documents exist on disk but the sync has not
reached them, so no tuning will help — let it finish) or **ranking** (they are
indexed and still did not come back, which is worth reporting).

Stop: `./scripts/stop.sh`. Reset admin password: `./scripts/admin-password.sh`.
