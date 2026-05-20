# Knovas Open Companion (Windows)

Small exe in the user RDP session: handles the custom open URL from the browser, calls `POST /api/open-tokens/redeem`, opens the returned UNC with the shell (not a temp copy).

## Build

From the open companion project directory under `components/`:

```bash
dotnet build -c Release
```

Ship the Release `net8.0-windows` exe from `bin/`.

## Install

1. Copy the built exe to a fixed path on the gold image.
2. Edit paths in the companion’s `register-protocol.reg`, import per user or via GPO.
3. `apiBase` in the open URL must match the web app origin users reach (HTTPS recommended).

## UI integration

On **Öffnen**, the SPA mints a token then sets `window.location.href` to `companion_href` from the API response. CSRF + session required for mint; see [http-api-open-tokens.md](http-api-open-tokens.md).

## Issues

[troubleshooting.md](troubleshooting.md)
