# Stopping web servers

How to shut down Knovas Platform and Remote Controller HTTP services (Docker and local dev).

## Knovas stack (search UI, RemoteController, identity DB)

One Compose project at the repo root covers all of it. From `KnovasComponents/`:

**Recommended:**

```bash
./scripts/stop.sh
```

**Manual:**

```bash
docker compose --env-file knovas.env down
```

**Demo stack** (adds the profile-gated mock API container):

```bash
docker compose --env-file knovas.env --profile mock down
```

`stop.sh` does not pass `--profile mock`, so stop a demo run with the command
above rather than the script.

**Rebuild and start again:**

```bash
./scripts/start.sh
```

Confirm nothing is listening on your web port (default `8081` from `DOCBRIDGE_WEB_PORT`):

```bash
docker compose --env-file knovas.env ps
```

> Older checkouts had a second Compose project under `KnovasPlatform/` with its
> own `start_stack.sh` / `stop_stack.sh`. Those are gone. If containers from one
> are still running, `docker rm -f docbridge-web docbridge-web-nginx`.

---

## Remote Controller on its own

RemoteController is part of the root stack above, so `./scripts/stop.sh` already
stops it. Only a **standalone** RC checkout (its own Compose project, for
component development) needs this, from `KnovasComponents/RemoteController/`:

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

One command, from the repo root:

```bash
cd KnovasComponents && ./scripts/stop.sh
```

On Windows (Git Bash or WSL):

```bash
cd KnovasComponents && bash ./scripts/stop.sh
```

That is the whole stack. Add `cd RemoteController && docker compose down` only if
you also started a standalone RC project for component development.

---

## Related

- Start platform: [KnovasPlatform/docs/setup.md](../KnovasPlatform/docs/setup.md)
- Start Remote Controller: [RemoteController/docs/SETUP.md](../RemoteController/docs/SETUP.md)
- Demo mock API: [KnovasPlatform/docs/demo.md](../KnovasPlatform/docs/demo.md)
