# Windows

Follow [setup.md](../setup.md) first. Windows host notes:

```powershell
cd KnovasComponents
Copy-Item knovas.env.example knovas.env   # edit
bash ./scripts/setup.sh      # Git Bash or WSL
bash ./scripts/start.sh
.\KnovasPlatform\scripts\verify_deploy.ps1
```

`setup.sh` and `start.sh` are shell scripts — run them from Git Bash or WSL.
There is no PowerShell equivalent; only the verifier has one.

Open `http://localhost:8081` (or the host and port from `DOCBRIDGE_WEB_PORT`).

**Server A or clients:** Configure `OPEN_UNC_ROOT` to the UNC users already use. **Öffnen** in the browser opens `\\server\share\...` on the client PC — no install. See [integration/opening-documents.md](../integration/opening-documents.md). Optional companion if IT blocks `file:` / UNC from HTTPS.

Stop the stack: `bash ./scripts/stop.sh` — [stopping web servers](../../../docs/stopping-web-servers.md).
