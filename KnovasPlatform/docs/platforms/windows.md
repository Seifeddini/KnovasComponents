# Windows

Follow [setup.md](../setup.md) first. Windows host notes:

```powershell
cd KnovasComponents\KnovasPlatform
Copy-Item .env.example .env   # edit
.\start_stack.ps1
.\scripts\verify_deploy.ps1
```

Open `http://localhost:8081` (or the host and port from `DOCBRIDGE_WEB_PORT`).

**Server A or clients:** Configure `OPEN_UNC_ROOT` to the UNC users already use. **Öffnen** in the browser opens `\\server\share\...` on the client PC — no install. See [integration/opening-documents.md](../integration/opening-documents.md). Optional companion if IT blocks `file:` / UNC from HTTPS.
