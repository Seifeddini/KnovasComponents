# Onboarding checklist

Follow [GETTING_STARTED.md](GETTING_STARTED.md) for the full ordered path.

1. Install RC; mount tenant cert, employee CA trust (at edge), and watch root volumes.
2. Fill `.env` and `config/remote_controller_sync.json`.
3. Complete [network-and-firewall.md](network-and-firewall.md); verify `curl https://<rc-base>/health` from outside your LAN.
4. Hand Knovas admin: base URL, instance token distribution path, operator contacts.
5. Knovas admin: register endpoint, run probe, issue employee RC certificates.
6. Employee: obtain JWT, run `GET /discover` then `POST /sync` during the sync window.
7. Partner: confirm ingestion in Knovas; review logs and `GET /sync/status`.
