# Troubleshooting

| Symptom | Fix |
|---------|-----|
| `docbridge-web` unhealthy / restart loop | `docker compose logs docbridge-web --tail 50` — see rows below |
| `ModuleNotFoundError: semantix_client` | Pull latest `main` (uses `knovas_client`); rebuild with `./scripts/start.sh` from the repo root |
| `RuntimeError: WEB_SECRET_KEY` | In `.env`, set `WEB_SECRET_KEY` to random hex (not `replace-with-random-hex`): `openssl rand -hex 32` |
| `identity.enabled is true, but COMPANY_LOGIN_NAME/COMPANY_LOGIN_PASSWORD are still set` | Remove both from `knovas.env` and re-run `./scripts/setup.sh`. Per-user accounts supersede the shared firm login; the Platform will not start with both doors open. Staging a cutover instead? Set `IDENTITY_ENABLED=false` **and** keep both values. See [RELEASE_NOTES.md](../../../RELEASE_NOTES.md) |
| `could not connect to server` / identity DB errors at boot | The Platform needs `platform-db`. Start the whole stack from the repo root with `./scripts/start.sh`, not one service by hand |
| `dependency failed to start: container docbridge-web is unhealthy` | The app died during import; the health check never had anything to probe. `docker compose logs docbridge-web` shows the real `RuntimeError` — usually one of the two rows above |
| No login page / open search UI | Placeholder secrets caused an old image to skip login; fix `knovas.env`, re-run `./scripts/setup.sh && ./scripts/start.sh` |
| `Permission denied` running a script | `chmod +x scripts/*.sh KnovasPlatform/scripts/verify_deploy.sh`, or run it as `bash <script>` |
| `127.0.0.1:8081` / port 8081 already in use | Stack may already be up: `curl http://127.0.0.1:8081/health`. Else `./scripts/stop.sh`, check `ss -tlnp` for 8081, remove stale `docbridge-*` containers. See [host-nginx-internal.md](../deployment/host-nginx-internal.md#troubleshooting-port-8081-already-in-use) |
| nginx 502 / bad gateway | Docker not on 127.0.0.1:8081: `./scripts/start.sh`; match `proxy_pass` port to `DOCBRIDGE_WEB_PORT` |
| Öffnen / open-token wrong host | Set `OPEN_PUBLIC_BASE_URL=https://<fqdn>` in `.env`; recreate `docbridge-web` |
| UI unchanged after `docker compose build` | Run `./KnovasPlatform/scripts/verify_deploy.sh` on **`DOCBRIDGE_WEB_PORT`** (not necessarily 8081). Expect `enrichment.loaded: true` and `build_id` **onedrive-locations-v3**. Fix `.env`: `SEARCH_ENRICHMENT_PATH=/mnt/autodoc/.search_enrichment.jsonl` (not `/app/sync_meta/...`). Host nginx `proxy_pass` must match `DOCBRIDGE_WEB_PORT`. |
| Öffnen does nothing | Client must reach the share; set `OPEN_UNC_ROOT` / `OPEN_CLIENT_LOCAL_ROOT`; browser may block `file:`/UNC from HTTPS — intranet zone or Edge policy; try optional companion |
| client-path 503 | Set `OPEN_UNC_ROOT` and/or `OPEN_CLIENT_LOCAL_ROOT` + `OPEN_LOCAL_ROOT`; check `OPEN_BROWSER_CLIENT_PATH` and AutoDoc mount |
| Mint 503 (companion) | Set `OPEN_COMPANION_ENABLED=true` and path mapping; only needed for companion fallback |
| Mint 400 CSRF | Reload `/` after login; send `X-CSRF-Token` |
| Redeem 401 | Token expired, wrong `WEB_SECRET_KEY` across workers, or replay |
| UNC access denied | User SMB ACL vs service account; test `dir \\share\...` in user RDP session |
| Click does nothing (companion mode) | Import `register-protocol.reg` or Linux `xdg-mime` handler — only when `OPEN_COMPANION_ENABLED=true` |
| File opens on server instead of client | Keep `OPEN_ALLOW_SERVER_SIDE_STARTFILE=false`; use browser **Öffnen** (`OPEN_BROWSER_CLIENT_PATH=true`) |
| PDF preview 415 | Not a PDF or inline PDF disabled in `.env` |

Check the Network tab on `/api/open-tokens/mint`; the companion shows HTTP errors in a message box.

Setup steps: [setup.md](../setup.md).
