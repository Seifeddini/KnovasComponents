# Remote Controller docs

## Start here

| Doc | Use |
|-----|-----|
| [local-setup.md](local-setup.md) | **Local dev** — localhost only, step-by-step setup and sync |
| [SETUP.md](SETUP.md) | **Production** — clone → configure → HTTPS edge → go-live |

## Run and test on your machine

| Doc | Use |
|-----|-----|
| [local-commands.md](local-commands.md) | API cheat sheet, pytest (after [local-setup.md](local-setup.md)) |
| [stopping web servers](../../docs/stopping-web-servers.md) | Stop platform + RC Docker and dev processes |

## Reference

| Doc | Use |
|-----|-----|
| [configuration.md](configuration.md) | `.env` and sync scheduler JSON |
| [network-and-firewall.md](network-and-firewall.md) | Firewall, public URL, outbound rules |
| [operations.md](operations.md) | Health, metrics, **stop sync**, upgrades |
| [onboarding-checklist.md](onboarding-checklist.md) | Go-live checklist |
| [hosting/server_01_home-corpus-setup.md](hosting/server_01_home-corpus-setup.md) | server_01_home corpus ingest setup (detailed) |
| [nginx-edge.example.conf](nginx-edge.example.conf) | HTTPS edge at NGINX |

Example sync body: [examples/sync-request.json](../examples/sync-request.json)
