# Troubleshooting

| Symptom | Fix |
|---------|-----|
| `docbridge-web` unhealthy / restart loop | `docker compose logs docbridge-web --tail 50` — see rows below |
| `ModuleNotFoundError: semantix_client` | Pull latest `main` (uses `knovas_client`); rebuild with `./start_stack.sh` |
| `RuntimeError: WEB_SECRET_KEY` | In `.env`, set `WEB_SECRET_KEY` to random hex (not `replace-with-random-hex`): `openssl rand -hex 32` |
| `RuntimeError: COMPANY_LOGIN_PASSWORD` | Set a real `COMPANY_LOGIN_PASSWORD` (not `replace-with-strong-company-password`) |
| No login page / open search UI | `COMPANY_LOGIN_ENABLED=false` in `.env`, or placeholder secrets caused old image to skip login; fix secrets and rebuild |
| `./start_stack.sh: Permission denied` | `chmod +x start_stack.sh stop_stack.sh scripts/verify_deploy.sh` |
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
