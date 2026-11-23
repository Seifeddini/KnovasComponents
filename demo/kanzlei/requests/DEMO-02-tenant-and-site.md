# DEMO-02: what a person has to create (tenant, certificates, site)

`FEAT-2026-11-19-demo-law-firm`, item DEMO-02. Serves all scenarios. Written by the agent for Siran; an agent never
creates a tenant or a certificate. Status on 20 November 2026: **nothing requested yet** (founder: "do not create
the tenant for now"). Everything below this line can be done in one sitting when you decide to.

## What exists without it

The stand-in runs locally and needs no tenant: the file server with the share, the directory, the SQL Server with the
firm's records, and the truth files ([`../README.md`](../README.md)). What does not run without a tenant: the Knovas
Connector's upload, search, the assistant, rights enforcement, the Builder's publish. So scenarios S1 to S8 wait for
this request; only their inputs are ready.

## The request

| # | What | Why | Decision in the plan |
|---|------|-----|----------------------|
| 1 | **Demo tenant** `demo-kanzlei` | Holds the stand-in's documents. Nothing real ever goes on it | D-B2 (environment and size): both founders |
| 2 | **Test tenant** `demo-kanzlei-test`, empty | A publish from the Builder and every scenario script runs here first (POST-04). Closes inbox item IN-2026-11-02-04 | D-B2 |
| 3 | **Client certificate per tenant** (`client-cert.pem`, `client-key.pem`, `ca-root.pem`) | The Connector and the Platform identify the tenant by it | critical change: a person issues them |
| 4 | **Where the site runs** | The stack of `docs/client/README.md` next to the stand-in's containers | D-B3 (server; how Olymp reaches it) |
| 5 | **Size at the start** | The small build first (52 documents the Connector reads), the full build's 743 after the capacity check | D-B2, plan §8.2 |

## What I need back

- The three certificate files of the **test** tenant first, placed by you in `certs/` of the site's checkout
  (never in the repository, never in a chat).
- The address of the API for that environment (`KNOVAS_API_URL`).
- The e-mail address that becomes the Platform's first administrator (`PLATFORM_ADMIN_EMAIL`).

## What happens then (C1, about half a day)

1. `demo/kanzlei/site`: `up.sh`, `load.sh` with the small build, `smoke.sh` green.
2. The share is mounted read-only with the account `svc-knovas`, as at a client; `KNOVAS_DOCUMENTS_PATH` points at
   `Quarzfels/Praxis/Autodoc`.
3. `./scripts/setup.sh && ./scripts/start.sh`; the Connector reads the store; "Meierhans" is found in the search.
4. First report and first findings (S1, S2).

## Sizes, so you can judge the load

| Build | Files on the share | Folders in the store | Read by the Connector | Database rows |
|-------|--------------------|----------------------|-----------------------|---------------|
| small (`--layout client`) | 420 | 1 | 52 (28 in the store, 24 on the common drive) | 60 matters |
| full (`--layout client --full`) | 31,838 | 63 | 743 (703 in the store, 40 on the common drive) | 1,500 matters, 31,153 document rows |

The Core receives only the last-but-one column. Production had incidents under load on 3 and 9 November; the full
build's upload should be agreed with the reliability work first.
