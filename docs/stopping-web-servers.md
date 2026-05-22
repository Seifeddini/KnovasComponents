# Stopping web servers

How to shut down Knovas Platform and Remote Controller HTTP services (Docker and local dev).

## Knovas Platform (search UI)

From `KnovasComponents/KnovasPlatform/`:

**Recommended (Linux/macOS or Git Bash):**

```bash
./stop_stack.sh
```

**Windows (PowerShell):**

```powershell
.\stop_stack.ps1
```

**Manual — production stack** (`docbridge-web`, `docbridge-web-nginx`):

```bash
docker compose down
```

**Manual — demo stack** (includes the Knovas mock API container):

```bash
docker compose --profile mock down
```

The helper scripts run `docker compose --profile mock down` so both the search UI and an active mock API are stopped.

Confirm nothing is listening on your web port (default `8081` from `DOCBRIDGE_WEB_PORT`):

```bash
docker compose ps
```

---

## Remote Controller (sync API)

From `KnovasComponents/RemoteController/`:

```bash
docker compose down
```

Removes the RC container and the bundled NGINX edge if you started with Compose.

---

## Local dev servers (not Docker)

If you started processes directly on the host, stop them in the terminal where they run (`Ctrl+C`), or end the process.

| Component | Typical command | Port (default) |
|-----------|-----------------|----------------|
| Remote Controller (Gunicorn) | `gunicorn -b 127.0.0.1:5001 ...` | `5001` (`RC_API_PORT`) |
| Remote Controller (Flask dev) | `python src/app.py` | from `.env` |
| Docbridge (unusual on host) | Gunicorn inside container only in this bundle | — |

**Find stray listeners (Linux):**

```bash
ss -tlnp | grep -E ':5001|:8081'
```

**Windows (PowerShell):**

```powershell
Get-NetTCPConnection -LocalPort 5001,8081 -ErrorAction SilentlyContinue |
  Select-Object LocalPort, OwningProcess
```

Then stop the matching process in Task Manager or `Stop-Process -Id <pid>`.

---

## Stop everything in the monorepo

Run from each product folder (order does not matter):

```bash
cd KnovasComponents/KnovasPlatform && ./stop_stack.sh
cd ../RemoteController && docker compose down
```

On Windows:

```powershell
cd KnovasComponents\KnovasPlatform; .\stop_stack.ps1
cd ..\RemoteController; docker compose down
```

---

## Related

- Start platform: [KnovasPlatform/docs/setup.md](../KnovasPlatform/docs/setup.md)
- Start Remote Controller: [RemoteController/docs/SETUP.md](../RemoteController/docs/SETUP.md)
- Demo mock API: [KnovasPlatform/docs/demo.md](../KnovasPlatform/docs/demo.md)
