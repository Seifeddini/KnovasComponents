# Local commands — run, sync, test

Run the Remote Controller on your machine and call discover/sync APIs. For first-time production setup, start with [SETUP.md](SETUP.md).

## Run the service

### Docker Compose (same as production)

```bash
cd RemoteController   # or KnovasComponents/RemoteController
cp .env.example .env  # if not done yet
docker compose up -d --build
docker compose logs -f remote-controller
```

Health (no auth):

```bash
curl -sS http://127.0.0.1:5001/health
```

Stop:

```bash
docker compose down
```

See also [stopping web servers](../../docs/stopping-web-servers.md) (platform + RC + dev processes).

### Python from source (dev/staging)

```bash
cd RemoteController
cp .env.example .env
pip install -e ".[dev]"
export PYTHONPATH=src
gunicorn -b 127.0.0.1:5001 -w 1 app:app
```

Or use the Flask dev server (uses `RC_API_PORT` from `.env`):

```bash
python src/app.py
```

Use **one** Gunicorn worker for continuous sync (`-w 1`).

---

## Employee auth

Discover and sync require:

- `Authorization: Bearer <employee JWT>` from Knovas (`generate_emp_jwt`)
- A valid operator UUID in the JWT (`sub`, `employee_id`, `operator_id`, or `id` claim)
- Reachable `KNOVAS_INTERNAL_API_URL` for `POST /remote_controller/verify_operator`
- Valid tenant mTLS paths for Knovas ingestion (`SEMANTIX_*` in `.env`)

---

## API commands

| Endpoint | Method | Auth |
|----------|--------|------|
| `/health` | GET | none |
| `/metrics` | GET | none |
| `/discover` | GET | Bearer JWT |
| `/sync` | POST | Bearer JWT; JSON body |
| `/sync/start` | POST | same; uses last saved body if request has no JSON |
| `/sync/stop` | POST | same |
| `/sync/status` | GET | same |

**Local base URL** (direct to RC):

```bash
export RC_BASE=http://127.0.0.1:5001
```

**Production** (via HTTPS edge):

```bash
export RC_BASE=https://rc.yourcompany.com
```

### Discover

**Production** (Knovas operator verify + `RC_INSTANCE_TOKEN`):

```bash
export EMPLOYEE_JWT="<from Knovas generate_emp_jwt>"

curl -sS "$RC_BASE/discover" \
  -H "Authorization: Bearer $EMPLOYEE_JWT"
```

**Internal LAN** (`RC_INTERNAL_LOCAL_BYPASS=true`, e.g. `docker-compose.internal.yml` — no JWT or instance token):

```bash
curl -sS "$RC_BASE/discover" | python3 -m json.tool

# Corpus on server01 (container path + depth)
curl -sS "$RC_BASE/discover?root=/data/corpus&max_depth=10&include_globs=**/*.txt"
```

### Sync (internal LAN, no JWT / instance token)

Requires tenant mTLS certs in `.env` (`SEMANTIX_*`) — uploads use those, not `RC_INSTANCE_TOKEN`.

**One-shot incremental sync** (uses scheduler mode from saved config; corpus example):

```bash
curl -sS -X POST "$RC_BASE/sync" \
  -H "Content-Type: application/json" \
  -d @examples/sync-request-corpus.json

curl -sS "$RC_BASE/sync/status"
```

**Continuous background sync:**

```bash
curl -sS -X POST "$RC_BASE/sync/start" \
  -H "Content-Type: application/json" \
  -d @examples/sync-request-corpus.json

curl -sS "$RC_BASE/sync/status"
curl -sS "$RC_BASE/sync/status?live=1"

curl -sS -X POST "$RC_BASE/sync/stop"
```

Production (edge URL) still requires `RC_INSTANCE_TOKEN` and employee JWT on all discover/sync endpoints.

### Sync (one-shot or continuous per scheduler config)

Edit [examples/sync-request.json](../examples/sync-request.json) so `sources[].path` resolves under `RC_WATCH_ROOTS` (e.g. `/data/docs` in Docker).

```bash
curl -sS -X POST "$RC_BASE/sync" \
  -H "Authorization: Bearer $EMPLOYEE_JWT" \
  -H "Content-Type: application/json" \
  -d @examples/sync-request.json
```

### Continuous sync control

```bash
# Start using last POST /sync body (or pass JSON body)
curl -sS -X POST "$RC_BASE/sync/start" \
  -H "Authorization: Bearer $EMPLOYEE_JWT" \
  -H "Content-Type: application/json" \
  -d @examples/sync-request.json

curl -sS "$RC_BASE/sync/status" \
  -H "Authorization: Bearer $EMPLOYEE_JWT"

# Live inventory (includes excluded_max_age when max_document_age_seconds is set)
curl -sS "$RC_BASE/sync/status?live=1" \
  -H "Authorization: Bearer $EMPLOYEE_JWT"

curl -sS -X POST "$RC_BASE/sync/stop" \
  -H "Authorization: Bearer $EMPLOYEE_JWT"
```

### Metrics

```bash
curl -sS "$RC_BASE/metrics"
```

---

## Tests

From the `RemoteController` directory:

```bash
pip install -e ".[dev]"
pytest
```

Live Knovas API reachability (requires real `.env` values, no `RC_SKIP_CONFIG_VALIDATION` skip for those tests):

```bash
pytest --knovas-api
```

Unit tests set `RC_SKIP_CONFIG_VALIDATION` automatically via [tests/conftest.py](../tests/conftest.py).

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| 401 on discover/sync | Missing or malformed Bearer JWT, or no operator UUID claim |
| 403 | Operator not allowlisted or JWT rejected by Knovas verify |
| 503 on discover/sync | RC cannot reach `KNOVAS_INTERNAL_API_URL` |
| Sync paused | Outside configured sync window in `config/remote_controller_sync.json` |
| No files uploaded | `sources[].path` not under `RC_WATCH_ROOTS` or filters exclude files |
| `excluded_max_age` in status | File `mtime` older than effective `max_document_age_seconds` (scheduler default or sync-body filter) |

See also [network-and-firewall.md](network-and-firewall.md) and [operations.md](operations.md).
