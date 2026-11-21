# Demo law firm site (synthetic)

A local Docker stand-in for what we find at a Swiss law-firm client: one Windows-style server that is both Active
Directory domain controller and file server, plus a SQL Server for the practice software. All data is synthetic
(firm "Quarzfels", `.example` domains). Part of `FEAT-2026-11-19-demo-law-firm`.

| Container | Host name | IP | Stands for at a client |
|-----------|-----------|----|------------------------|
| `dc` | SRV-DC01 | 172.28.215.10 | Windows Server: AD DC (LDAP/LDAPS, Kerberos, DNS) and file server with one hidden share |
| `sql` | SRV-SQL01 | 172.28.215.20 | SQL Server of the practice-management software |
| `loader` | | dynamic | one-shot: the IT partner who fills the server (share tree, ACLs, database) |
| `client` | | dynamic | a domain workstation / the Connector host; runs the smoke test |

Network `demo-kanzlei-net` = 172.28.215.0/24 (dynamic addresses from .128 up). `dc` is Samba 4.17 (Debian 12) and runs
with `cap_add: SYS_ADMIN` (not fully privileged): Samba writes NT ACLs as `security.*` xattrs on sysvol.

## Prerequisites

Docker Desktop (WSL2 backend) or Docker Engine with Compose v2, Git Bash on Windows. About 3 GB of disk for the
images; SQL Server takes 2 GB of RAM. The data generator's output: a site folder (`directory.json`, `acl.json`,
`db/*.sql`) and a share folder (the tree under the share root).

## Use

```bash
./init-site.sh <site-dir> <share-dir>   # writes .env: data paths + random passwords (gitignored; never commit it)
./up.sh                                  # first start builds the image and provisions the domain (~20 s)
./load.sh                                # wipe share + database, copy tree (mtimes kept), run db/*.sql, apply ACLs
./smoke.sh                               # PASS/FAIL per check, non-zero exit on any FAIL
./check-records.sh <work>/truth          # the database, read as knovas_ro, equals truth/records/*.jsonl row for row
./check-share.sh <work>                  # every file on the share equals manifest.jsonl (path and SHA-256)
./apply-changes.sh <work>/site/changes/tag-1   # play one working day (database and share); then check against
                                               # <work>/truth/changes/tag-1 with the two scripts above
./down.sh                                # stop; ./down.sh --volumes also deletes domain, files and database
```

- `load.sh` is re-runnable: the loader prints what it will remove, empties the share volume and drops the database,
  then loads. To load other data: `DEMO_SITE_DIR=... DEMO_SHARE_DIR=... ./load.sh` (the paths are saved in `.env`).
- `reset.sh` empties the share and drops the database; the domain stays. The domain is provisioned from
  `directory.json` only on the first start, so after users or groups change run `./reset.sh --all && ./up.sh`
  (the dc log warns when `directory.json` differs from the provisioned one). A new service account also needs
  `./init-site.sh` again (it only adds missing passwords).
- Fixture for testing the site itself: `tests/fixture/` (run `tests/fixture/set-mtimes.sh` first: git does not keep
  mtimes), then `./init-site.sh tests/fixture/site tests/fixture/share`.
- Ports on `127.0.0.1` only, off by default: set `DEMO_PUBLISH_PORTS=1` in `.env` → SMB 4450, LDAP 3890, LDAPS 6360,
  SQL 14330 (`compose.ports.yml`). Kerberos is not published.

## Checking a connector against the site

`./check-records.sh <truth-dir>` reads every table with the login `knovas_ro` and compares it with the generator's
`truth/records/<TABLE>.jsonl`, row for row (numbers to the cent, yes/no as stored). It names tables, keys and columns,
never values. It is the proof that the truth files are an oracle: a connector that reads this database must end up
with the same records. `<truth-dir>/sources.json` says for each table what its key is, how a change shows and how a row
disappears. The truth folder is mounted read-only for the run and copied nowhere.

A site of the connection layer (Knovas Link and its components) reaches the sources when its containers are on the
network `demo-kanzlei-net` (`docker network connect demo-kanzlei-net <container>`), at the fixed addresses above.

`./apply-changes.sh <changes-dir>` runs the day's `db.sql` as the software would have written it, copies the new and
saved-over files with their times, deletes the files listed in `delete.txt` and applies `acl.json` again. Apply a set
once; `./load.sh` goes back to the build. After a set is applied, `smoke.sh` check 7a fails on purpose (it compares
with the build's folder): use `check-share.sh` with the folder of the state you expect.

## Accounts

`.env` holds: `PW_DOMAIN_ADMIN` (`QUARZFELS\Administrator`), `PW_SA` (SQL `sa`), `PW_PEOPLE` (initial password of
every person), one `PW_<SAM>` per service account (`svc-knovas` → `PW_SVC_KNOVAS`), `PW_SQL_KNOVAS_RO` (SQL login
`knovas_ro`, `db_datareader`) and `PW_SQL_PRAXIS_APP` (SQL login `praxis_app`, `db_owner`). Passwords never expire.
OUs: `OU=Kanzlei` with `Mitarbeiter` (people), `Ehemalige` (disabled people), `Dienstkonten`, `Gruppen`.

## Connecting as a Knovas Connector host would

```bash
mount -t cifs '//172.28.215.10/shares$/Quarzfels' /mnt/kanzlei \
  -o credentials=/etc/knovas/kanzlei.cred,vers=3.0,sec=ntlmssp,ro,domain=QUARZFELS
# kanzlei.cred: username=svc-knovas / password=<PW_SVC_KNOVAS>
```

Or as a Docker volume (what `smoke.sh` check 8 does; works on Docker Desktop):

```yaml
volumes:
  kanzlei:
    driver: local
    driver_opts:
      type: cifs
      device: "//172.28.215.10/shares$$/Quarzfels"
      o: "addr=172.28.215.10,username=svc-knovas,password=${PW_SVC_KNOVAS},domain=QUARZFELS,vers=3.0,sec=ntlmssp,ro"
```

The container must be on `demo-kanzlei-net` (or the host must route to it). LDAP: `ldaps://172.28.215.10`, bind as
`svc-knovas@ad.quarzfels.example` (simple binds need TLS; the certificate is self-signed for
`SRV-DC01.ad.quarzfels.example`; CA in the dc at `/var/lib/samba/private/tls/ca.pem`). SQL:
`172.28.215.20,1433`, login `knovas_ro`, database from `directory.json`, trust the server certificate.

## How permissions work

`acl.json` is applied as POSIX ACLs on the share (kernel-enforced; Samba shows them to Windows as NT ACLs). Entries
are applied parents first. `protect: true` replaces the folder's ACL and everything below with its grants plus
Domain Admins full; `protect: false` adds its grants below the folder. `read` = list/read, `modify` and `full` =
read/write/delete (POSIX cannot tell them apart). Group nesting works to any depth. Every folder also gives Domain
Users traverse-only, which mimics Windows' "bypass traverse checking". New files take the folder's rights.

## Licence

SQL Server runs as **Developer edition** (`MSSQL_PID=Developer`, `ACCEPT_EULA=Y`): free for development and test
only, never for production or real client data. Samba and Debian are free software.
