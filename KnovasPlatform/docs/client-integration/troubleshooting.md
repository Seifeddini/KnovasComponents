# Troubleshooting

| Symptom | Fix |
|---------|-----|
| Mint 503 | Set `OPEN_UNC_ROOT` / AutoDoc mount; check `OPEN_COMPANION_ENABLED` |
| Mint 400 CSRF | Reload `/` after login; send `X-CSRF-Token` |
| Redeem 401 | Token expired, wrong `WEB_SECRET_KEY` across workers, or replay |
| UNC access denied | User SMB ACL vs service account; test `dir \\share\...` in user RDP session |
| Click does nothing | Import companion `register-protocol.reg` (edit exe path first) |
| PDF preview 415 | Not a PDF or inline PDF disabled in `.env` |

Check Network tab on `/api/open-tokens/mint`; companion shows HTTP errors in a message box.
