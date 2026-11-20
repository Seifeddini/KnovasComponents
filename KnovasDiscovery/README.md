# Knovas Discovery

A read-only scan we run at a client before onboarding: it finds what data sources the client has and how each can
be connected to Knovas, and writes a discovery report and a connection plan. The method, the classes and the effort
model are in [docs/design.md](docs/design.md); the steps on site in [docs/runbook.md](docs/runbook.md). Every fact
the probes produce is listed in [docs/facts.md](docs/facts.md). Status: draft, built by an agent, not yet run at a
client. Against the synthetic demo law firm (`demo/kanzlei/discovery/`, `bash run-demo.sh`) 51 comparisons with the
generator's truth pass and 2 fail, both limits named below: `demo/kanzlei/discovery/out/verification.txt`.

> **Before the first scan at a client:** the consent text in [docs/fragebogen.md](docs/fragebogen.md) is an unchecked
> draft. A lawyer has to check it first.

## What it does and does not do

- **It only reads.** On a share it lists folders and reads file sizes and dates; it opens no file. In a database it
  queries the catalogue views (`sys.*`, `SERVERPROPERTY`); no row of a client table is read. In a directory it
  counts. It writes nothing anywhere except its own output folder.
- **It looks only where `intake.yaml` points.** No network scan.
- **A probe that fails says so, and the scan goes on.** A report with a failed probe is a result.

## Install

Python 3.11 or newer, on Windows or Linux:

```bash
python -m venv .venv
.venv/bin/pip install ./KnovasDiscovery          # Windows: .venv\Scripts\pip install .\KnovasDiscovery
```

Dependencies: PyYAML, ldap3, pymssql (with FreeTDS and TLS built in), requests. Or build the image (below).

## The five commands

```bash
knovas-discovery init <folder>                         # writes intake.yaml with comments; fill it in from the questionnaire
knovas-discovery scan --intake intake.yaml --out out   # runs the probes; writes out/findings.json and out/detail.json
knovas-discovery scan ... --only fileshare,directory   # only some probes: environment,fileshare,directory,mssql,m365
knovas-discovery report --out out [--review review.yaml]   # writes out/report.json, report.md, connection-plan.md
knovas-discovery catalogue [--check]                   # the catalogue as a table; --check validates it
```

`python -m knovas_discovery` works the same.

Passwords and the Microsoft 365 client secret come only from the environment variables named in `intake.yaml`
(`password_env`, `secret_env`). If one is not set and the scan runs at a terminal, it asks for it; otherwise that
probe fails with `password_missing:<VARIABLE>`. A password is never taken from the command line or a file, and never
written, logged or shown; error texts that go into `detail.json` are scrubbed of it.

`review.yaml` (optional) lets the consultant correct the classification. Source ids are those in `report.json`
(`<catalogue entry>@<probe result key>`):

```yaml
sources:
  practice-database@praxis:KanzleiDB:
    effort: [{points: 3, reason: "the vendor's export has to be studied first"}]
    notes: ["Vendor contact: through the IT provider."]
  fileshare@kanzlei:Quarzfels/Gemeinsam:
    class: D
    reason: "the client keeps the common drive out of scope"
notes: ["First discovery at a client with this practice software."]
```

## What leaves the site, and what does not

| File | Holds | Leaves the site |
|------|-------|-----------------|
| `findings.json` | numbers, patterns, the names of database tables and columns, the client's key, the target keys and share roots named in `intake.yaml` | yes |
| `report.json`, `report.md`, `connection-plan.md` | the same, classified | yes |
| `detail.json` | names a consultant needs on site: folders that refuse the account, top-level folders with file counts, the 20 largest files, group names with member counts, OU names, service account names, SharePoint site addresses, driver error texts (scrubbed of passwords) | **no** |
| `intake.yaml` | the client's name, addresses, account names (no password) | stays with the consultant |

No name of a person, client, file, folder or group is in `findings.json` or the reports; a test plants marker names
in every one of those places and fails if one appears.

## Run it in Docker

For a scanning machine with Docker but no Python. Build once:

```bash
docker build -t knovas/discovery:0.1.0 KnovasDiscovery
```

Mount the share read-only as a Docker volume of type cifs, then scan (Git Bash on Windows: prefix with
`MSYS_NO_PATHCONV=1`):

```bash
docker volume create -d local -o type=cifs -o 'device=//fileserver/share$' \
  -o "o=addr=10.0.0.10,username=svc-knovas,password=$SHARE_PASSWORD,domain=FIRM,vers=3.0,sec=ntlmssp,ro" kd-share
export DISCOVERY_LDAP_PASSWORD DISCOVERY_SQL_PASSWORD        # set them first (read -rs NAME)
docker run --rm -e DISCOVERY_LDAP_PASSWORD -e DISCOVERY_SQL_PASSWORD \
  -v kd-share:/mnt/share:ro -v "$PWD:/work" knovas/discovery:0.1.0 scan --intake /work/intake.yaml --out /work/out
docker run --rm -v "$PWD:/work" knovas/discovery:0.1.0 report --out /work/out
docker volume rm kd-share                                    # the volume's options hold the share password
```

`-e NAME` without a value hands the variable from your shell to the container, so the password is not on the
`docker run` command line. The cifs volume is different: Docker keeps its options, password included, until the
volume is removed, so remove it at once. On a Linux host without Docker volumes, mount with `mount -t cifs ... -o
credentials=<file>,ro` and give the mount point as `path`.

## Run it natively on a Windows laptop over the client's VPN

```powershell
py -3.13 -m venv $env:TEMP\kd; & $env:TEMP\kd\Scripts\pip install .\KnovasDiscovery
net use \\fileserver\share$ /user:FIRM\svc-knovas *          # asks for the password; the session reads as that account
& $env:TEMP\kd\Scripts\knovas-discovery scan --intake intake.yaml --out out   # asks for the LDAP and SQL passwords
& $env:TEMP\kd\Scripts\knovas-discovery report --out out
net use \\fileserver\share$ /delete
```

In `intake.yaml` the share's `path` is then the UNC path (`'\\fileserver\share$'`). The scan asks for every password
whose variable is not set, because it runs at a terminal. The listing over a VPN is slower than on site: the
report's listing speed says by how much.

## Limits

- **Microsoft 365 is not verified against a tenant.** The m365 probe is written against Microsoft Graph's
  documented answers and tested with recorded examples (`tests/fixtures/m365_graph.json`); it has never run against
  a real tenant. Treat its numbers as unconfirmed until it has.
- Databases: Microsoft SQL Server only. `verify_tls: true` relies on FreeTDS checking the certificate against
  `ca_file` or the system's store; only `verify_tls: false` (self-signed) has been run, against the demo site.
  `authentication` is `windows` or `mixed`: SQL Server's own property cannot say "SQL logins only".
- Counts in a database are what the login can see: a login without VIEW DEFINITION sees fewer views and procedures.
- Table roles and change markers are guesses from names, keys, column types and row counts (rules in
  [docs/facts.md](docs/facts.md)); the consultant confirms them. A change marker that is only an increasing number
  (an identity column) is not recognised.
- A share's files inside folders that refuse the account are not counted, not even by extension: an Outlook archive
  in a personal folder the account cannot read is not seen.
- The directory probe reads Active Directory or another LDAP server over LDAPS with a simple bind; no Kerberos.
- No scan of an Exchange server, a document management system or a cloud service: the questionnaire covers them, and
  `on: intake` entries of the catalogue classify what the client names.

## Develop

```bash
pip install -e "./KnovasDiscovery[dev]"
pytest KnovasDiscovery/tests
python -m knovas_discovery.facts > KnovasDiscovery/docs/facts.md    # after changing a probe's FACTS
```
