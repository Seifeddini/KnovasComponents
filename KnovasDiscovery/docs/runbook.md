# Discovery runbook: from first call to connection plan

For anyone at Knovas who prepares a client for onboarding. You need no engineering background for steps 1, 3 and 4.
Step 2 needs someone who can open a terminal. Design and reasons: [design.md](design.md).

**The result** is two documents: a discovery report (what the client has) and a connection plan (what we connect
first, what later, what we need from them, what a custom connection costs in effort). With them an offer can be
written and an installation planned without a second visit.

**Three rules that are not negotiable**

1. We only read, and we read no content. No document is opened, no row of a database table is read.
2. Nothing with a name in it leaves the client. `findings.json`, `report.md` and `connection-plan.md` may go to
   Knovas. `detail.json` stays on the machine it was written on. If you need a name to do your work, look at it
   there.
3. No password in a file, a chat or an e-mail. The client's IT provider types it, or sets it as an environment
   variable on the scanning machine.

## Before you start

| What | Who gives it | Why |
|------|--------------|-----|
| Signed consent for the scan ([fragebogen.md](fragebogen.md), annex) | the client's managing partner | We look at a law firm's systems. Their professional secrecy covers what we see |
| A contact at their IT provider | the client | They create the account and know where things are |
| A read-only account for the file server and the directory | IT provider | The scan and, later, the Knovas Connector use the same account. If it sees too much or too little, we learn it now |
| A read-only database login, if they have a practice database | IT provider | Without it the database is classified from the questionnaire only |
| A way in: a machine in their network, or their VPN | IT provider | The scan runs inside |
| For Microsoft 365: ten minutes of a global administrator | client or IT provider | App registration with read-only permissions |

## Step 1: questionnaire (45 minutes, with the client)

Go through [fragebogen.md](fragebogen.md) in a call. Fill it in yourself while they talk; do not send it ahead and
wait. People answer "we have everything in WinJur" and mean three systems. Ask where a file goes when a letter
arrives, when an e-mail arrives, when a matter closes. Write down the words they use.

Then put the answers into `intake.yaml`:

```bash
knovas-discovery init ./kunde-xy          # writes ./kunde-xy/intake.yaml with comments
```

Everything the client named goes under `software`, also what we will not scan. Everything we may look at goes
under `targets`. If you are not sure whether we may look at something, we may not.

## Step 2: scan (1 to 2 hours, inside the client's network)

On a Windows laptop over their VPN, or on the machine that will later run the Knovas site:

```bash
pip install ./KnovasDiscovery               # from a checkout of KnovasComponents; or use the Docker image (README)
set DISCOVERY_LDAP_PASSWORD=...             # typed by the IT provider, in this window only
set DISCOVERY_SQL_PASSWORD=...
knovas-discovery scan --intake kunde-xy/intake.yaml --out kunde-xy
```

If a password variable is not set, the scan asks for it at the terminal; let the IT provider type it.

The scan prints one line per probe. A probe that fails says why and the others go on. Typical first-run failures and
what they mean:

| What you see | What it means |
|--------------|---------------|
| fileshare `failed: path_not_found` | The share is not connected on this machine. Connect it with the service account, not with your own |
| fileshare: thousands of refused folders | The account is not what the IT provider thinks it is. Stop and call them |
| fileshare: no refused folders at all | The account sees everything, also personnel and wages. That is a finding: say so in the plan |
| directory `failed: bind_refused` | Wrong user name form. Try `user@domain` |
| mssql `failed: login_refused` | The login exists on another server or instance. Ask for server, instance and port |
| mssql: no database listed | The login has no database assigned. It needs read access to the practice database |

A large share takes time: the scan lists every folder once. It reads no file. If it must be interrupted, run it
again for one root with `--only fileshare`.

Do not leave before you have looked at `detail.json` with the IT provider for five minutes: are the refused folders
the ones that should be refused? Is a folder missing that the client talked about?

## Step 3: classification (30 minutes, back at your desk)

```bash
knovas-discovery report --out kunde-xy
```

Read `report.md` from top to bottom. The tool matches what it found against the catalogue of sources we know. Your
part is what a tool cannot know:

- **Is the class right for this client?** A file share is class A. A file share where the client wants three
  practice groups walled off from each other is not: move it to B in `review.yaml` and say why.
- **Is something missing?** Compare with the questionnaire. What the client named and no probe saw is listed as
  "named by the client, not scanned". Decide for each: scan it later, classify it from the answers, or leave it out.
- **Custom sources that are not databases** get their effort from you. Write the points and the reason in
  `review.yaml`. Use the database table in [design.md](design.md) as a yardstick, and ask an engineer when unsure.
- **Something the catalogue does not know.** It is reported as class C, "unknown kind". After the discovery, add an
  entry to `catalogue/sources.yaml` (below).

`review.yaml` (a source's id is in `report.json` and in the report's tables: `<catalogue entry>@<what was scanned>`):

```yaml
sources:
  fileshare@kanzlei:Quarzfels/Gemeinsam:
    class: B
    reason: "Three teams must not see each other's folders; needs an access model per folder."
  dms@intake:2:dms:imanage:          # a system the client named and we did not scan
    effort: [{points: 14, reason: "iManage Cloud, documented interface, rights per workspace must be read."}]
notes:
  - "The client moves to Microsoft 365 in spring; do not plan work on the Exchange server."
```

Run `report` again after every change to `review.yaml`.

## Step 4: the plan (30 minutes)

`connection-plan.md` has three phases:

| Phase | What | When | Price |
|-------|------|------|-------|
| 1 | Class A: what a released component connects with configuration | at onboarding | part of onboarding |
| 2 | Class B: connected by a released component, with modelling or mapping for this client | in the first weeks | fixed package per source |
| 3 | Class C: custom | after an individual offer | from the effort class |

Complete it by hand:

1. **What we need from the client**: the tool lists it per source. Put a name and a date next to each line.
2. **Open questions**: ask them now, in one e-mail, not one by one over weeks.
3. **What the client will not get** (class D): say it in the offer, with the reason. A client who finds out later
   that their mail archive is not searchable is an unhappy client.
4. **Sizing**: the forecast of the first read tells the client when search is complete. Days, not hours, is normal.

The offer is written by a person from this plan. Prices per class are in `catalogue/pricing.yaml` and are set by
the founders. Where the tool prints "price not set", ask.

## After every discovery: make the next one cheaper

The catalogue is the team's memory. Within a week of a discovery:

- **A product we now know**: add its table names as a `detect` block (`on: mssql`, `table_names contains_all`), so
  the next scan recognises it by itself and its effort drops (a known product has a mapping to reuse).
- **A question that turned out to matter**: add it to the entry's `questions`.
- **A risk we learned the hard way**: add it to `risks`, in one sentence a client would understand.
- Raise the entry's `seen` by one. Never write a client's name into the catalogue.
- If the scan measured something wrong, that is a bug in the tool: report it with the fact and what was true.

Check your edit with `knovas-discovery catalogue --check`.

## What discovery does not do

It does not find servers nobody named. It does not judge the client's security. It does not read content, so it
cannot say whether their documents are good. It does not replace the installation: it makes sure the installation
holds no surprise.
