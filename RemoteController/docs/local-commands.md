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

## Local auth shortcut (non-production)

Skip employee client certificates when testing against `127.0.0.1` — set in `.env`:

```env
RC_MTLS_DEV_BYPASS=true
RC_MTLS_DEV_EMPLOYEE_ID=<operator-uuid-matching-jwt>
```

`RC_MTLS_DEV_EMPLOYEE_ID` must match the operator UUID in the JWT you pass to `Authorization: Bearer`.

You still need:

- A real **employee JWT** from Knovas (`generate_emp_jwt`)
- Reachable `KNOVAS_INTERNAL_API_URL` for `POST /remote_controller/verify_operator`
- Valid tenant mTLS paths for Knovas ingestion (`SEMANTIX_*` in `.env`)

Never enable dev bypass in production.

---

## API commands

| Endpoint | Method | Auth |
|----------|--------|------|
| `/health` | GET | none |
| `/metrics` | GET | none |
| `/discover` | GET | Bearer + mTLS (or dev bypass) |
| `/sync` | POST | Bearer + mTLS; JSON body |
| `/sync/start` | POST | same; uses last saved body if request has no JSON |
| `/sync/stop` | POST | same |
| `/sync/status` | GET | same |

**Local base URL** (direct to RC):

```bash
export RC_BASE=http://127.0.0.1:5001
```

**Production** (via edge — employee cert required unless using a staging tunnel):

```bash
export RC_BASE=https://rc.yourcompany.com
```

### Discover

```bash
export EMPLOYEE_JWT="<from Knovas generate_emp_jwt>"

curl -sS "$RC_BASE/discover" \
  -H "Authorization: Bearer $EMPLOYEE_JWT"
```

With production edge mTLS, add `--cert employee-rc.pem --key employee-rc.key`.

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

Unit tests set `RC_SKIP_CONFIG_VALIDATION` and `RC_MTLS_DEV_BYPASS` automatically via [tests/conftest.py](../tests/conftest.py).

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| 401 on discover/sync | Missing `Authorization: Bearer` or dev bypass / employee cert |
| 403 | JWT/cert mismatch or operator not allowlisted |
| 503 on discover/sync | RC cannot reach `KNOVAS_INTERNAL_API_URL` |
| Sync paused | Outside configured sync window in `config/remote_controller_sync.json` |
| No files uploaded | `sources[].path` not under `RC_WATCH_ROOTS` or filters exclude files |

See also [network-and-firewall.md](network-and-firewall.md) and [operations.md](operations.md).
