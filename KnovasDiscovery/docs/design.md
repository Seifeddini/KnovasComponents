# Knovas Discovery: design

Founder request of 20 November 2026. Proposed under `BET-2026-11-sources-and-access` (its measure is the time from
first contact with a kind of source to that source loaded); worked example: the demo law firm
(`FEAT-2026-11-19-demo-law-firm`). Status: draft, built by an agent, not verified by anyone else.

## Why

Before we connect a client we need to know what they have, what we can connect today, what needs work, and what that
work costs. Today that knowledge is in people's heads and in one seven-line checklist. Some sources repeat at every
client (a file share, Microsoft 365, a directory); some are particular (a practice database). The repeating ones
should be connected in days at a standard price. The particular ones need an estimate that rests on facts, not on a
guess made in a sales call.

## What it is

Three things that belong together:

1. **A method** in four steps that anyone in the team can run: questionnaire, scan, classification, plan
   ([runbook.md](runbook.md); the client's questionnaire in German: [fragebogen.md](fragebogen.md)).
2. **A tool** (`knovas-discovery`) that does the scan and writes the report. It only reads, and it reads no content.
3. **A catalogue** of the kinds of source we know (`src/knovas_discovery/catalogue/sources.yaml`): how to recognise
   each, what Knovas can do with it today, what the client has to provide, and what drives the effort. Every
   discovery that meets something new adds an entry. This is what makes the second client cheaper than the first.

## The four steps

| Step | Who | Time | Result |
|------|-----|------|--------|
| 1. Questionnaire | sales or consultant, with the client's office manager and IT provider | 45 min | `intake.yaml`: who they are, what software they name, where to look, which account we get |
| 2. Scan | consultant, on a machine inside the client's network or over their VPN | 1 to 2 h | `findings.json` (facts, no names) and `detail.json` (with names; stays at the client) |
| 3. Classification | the tool, then the consultant reviews | 30 min | every source with a class, its prerequisites, its risks and, if custom, its effort |
| 4. Plan | the tool writes it, the consultant completes it | 30 min | `report.md` and `connection-plan.md`: what we connect first, what later, what we need, what is open |

## Classes

| Class | Meaning | Offer |
|-------|---------|-------|
| **A: standard** | A released Knovas component connects it with configuration only | part of onboarding, phase 1 |
| **B: standard with mapping** | A released component connects it, and someone has to model or map something for this client | fixed package, phase 2 |
| **C: custom** | No component reads it; a connector or an export has to be built | individual offer from the effort class, phase 3 |
| **D: not now** | We cannot or should not connect it (no access, no way to read it, no value) | named in the report with the reason |

A source's class comes from the catalogue entry it matches. A source that matches nothing is class C with the
note "unknown kind: add it to the catalogue".

## Effort of a custom source

Effort is counted in points from facts the scan measures, so two people get the same number. For a database:

| Driver | Points |
|--------|--------|
| Tables that hold the firm's records | 1 each, at most 12 |
| No foreign keys (relations have to be worked out) | 3 |
| Change markers: on every record table 0; on some 2; on none 5 | 0, 2 or 5 |
| Rows that are removed without a flag | 3 |
| Rights kept in the source (a table says who may see what) | 5 |
| Fields the client defined itself | 2 |
| Product not in the catalogue (no mapping to reuse) | 5 |
| More than a million rows in the record tables | 2 |
| Only Windows authentication | 2 |

Effort class: **S** up to 8 points, **M** 9 to 16, **L** 17 to 26, **XL** above. What a class costs is in
`catalogue/pricing.yaml`, which the founders fill in; the tool prints "price not set" until they do. For custom
sources that are not databases the consultant sets the points by hand, with a reason, in `review.yaml`.

## Rules the tool keeps

1. **It only reads.** No write to any source, ever. In a database it queries the catalogue views only
   (`sys.*`, `INFORMATION_SCHEMA`): no row of the client's tables is read. On a share it lists; it opens no file.
   In a directory it counts.
2. **Two outputs.** `findings.json` and the reports hold numbers, patterns, and the names of database tables and
   columns (schema, needed to recognise a product). They hold no name of a person, client, file, folder or group,
   so they may leave the site. `detail.json` holds the names a consultant needs on site (which folders are
   unreadable, which groups exist) and never leaves it.
3. **No secret in a file or on a command line.** Passwords come from environment variables named in `intake.yaml`,
   or from a prompt. They are never written, logged or shown.
4. **A probe that fails says so and the scan goes on.** A report with a failed probe is a result.
5. **The same input gives the same report**, except for the time of the scan.

## What the probes measure

| Probe | Reads | Facts in `findings.json` |
|-------|-------|--------------------------|
| `environment` | the scanning machine | OS, Docker present, CPU and memory, free disk, whether the Knovas API host answers on its port, proxy variables set |
| `fileshare` | folder listings below each root | files, bytes, folders, depth; files per extension; share the Knovas Connector reads; age of files in bands; folders that refuse the account (count); name patterns (below); largest file; listing speed; a forecast of the first read |
| `directory` | LDAP | people enabled and disabled, accounts that look like services, groups, deepest nesting, groups without members, whether it is Active Directory |
| `mssql` | catalogue views | version and edition; databases visible; per table: rows, columns, key, foreign keys, row version, columns that look like "changed at", "deleted" flags; per table a guessed role (records, lookup, software, leftover); the product, if the catalogue knows its tables |
| `m365` | Microsoft Graph, read-only | licences by kind, people, SharePoint sites, drives and their size, whether Exchange Online is in use |

Name patterns on a share, each as a share of files or a count: AutoDoc names (`{GUID}_{id}_{type}.ext`), folders
named as number ranges, Office lock files, Outlook archives, dictations, scanner output, files without extension,
paths longer than 240 characters.

## Files

```
intake.yaml        written by a person from the questionnaire (template: knovas-discovery init)
findings.json      written by: knovas-discovery scan
detail.json        written by: knovas-discovery scan        (stays at the client)
review.yaml        optional, written by the consultant: corrections to classes, manual effort, notes
report.json        written by: knovas-discovery report
report.md          the discovery report for Knovas and the client
connection-plan.md the three phases, what we need from the client, what is open, the basis of the offer
```

`intake.yaml`:

```yaml
client: {key: quarzfels, name: Quarzfels Advokatur, sector: law-firm, people: 13, lawyers: 10, it_provider: external}
software:                       # what the client says it uses; the scan confirms or contradicts it
  - {category: practice, product: unknown}
targets:
  fileshares:
    - key: kanzlei
      path: /mnt/kanzlei        # where the share is visible on the scanning machine (a mount, or a UNC path on Windows)
      roots: [Quarzfels/Praxis/Autodoc, Quarzfels/Gemeinsam]   # empty: the whole path
  directories:
    - {key: ad, url: "ldaps://172.28.215.10", bind_user: "svc-knovas@ad.quarzfels.example",
       password_env: DISCOVERY_LDAP_PASSWORD, base_dn: "DC=ad,DC=quarzfels,DC=example", verify_tls: false}
  databases:
    - {key: praxis, kind: mssql, host: 172.28.215.20, port: 1433, user: knovas_ro,
       password_env: DISCOVERY_SQL_PASSWORD, verify_tls: false}
  m365:
    - {key: tenant, tenant_id: "", client_id: "", secret_env: DISCOVERY_M365_SECRET}
knovas: {api_host: api.knovas.ch, api_port: 8443}
```

`findings.json`: `{tool_version, scanned_at, client: {key, sector, people, lawyers}, probes: {environment: {...},
fileshares: [...], directories: [...], databases: [...], m365: [...]}}`. Every probe result has `key`, `status`
(`ok`, `partial`, `failed`, `skipped`), `error` (a code, no secret, no name) and `facts`.

A catalogue entry:

```yaml
- key: autodoc-store
  name: Document store of a practice software (AutoDoc)
  category: documents            # documents, records, identity, mail, collaboration, finance, other
  class: B
  knovas: {status: released, component: Knovas Connector, how: "watch root on the store; matters from file names by a file-pattern mapping in the Builder"}
  detect:                        # all conditions of one block must hold; any block is enough
    - on: fileshare
      when: [{fact: patterns.autodoc_names_share, op: ">=", value: 0.3}]
  needs_from_client: [...]
  risks: [...]
  effort_drivers: [...]          # for class C: which facts drive the effort
  questions: [...]               # what to ask the client when this is found
```

`detect` conditions name a fact of the probe result (`on` says which probe) by a dotted path. Operators: `>=`, `>`,
`<=`, `<`, `==`, `!=`, `contains_any` (the fact is a list or set of names; the value is a list), `contains_all`.
For databases the fact `table_names` is the list of table names in upper case.

## Checked against the demo law firm

The demo firm's site has truth files for everything the scan measures. `demo/kanzlei/discovery/` holds the
firm's `intake.yaml`, a script that runs the scan against the site, the report it produced, and a comparison of the
findings with the truth: file counts per root, readable files, folders that refuse the account, people, groups,
tables, rows, keys, change markers and roles. The comparison is the tool's test: what it reports there must be what
we planted.

## Not in this version

- The scan of Microsoft 365 is written against Graph's documented answers and tested with recorded examples. It has
  not run against a tenant.
- No scan of an Exchange server, a document management system or a cloud service: the questionnaire covers them
  and the catalogue classifies them.
- No network scan. The tool looks only where `intake.yaml` points. Finding servers nobody named is the IT
  provider's knowledge, and scanning a client's network needs their written consent.
- Prices. The effort class is computed; what it costs is the founders' decision.
