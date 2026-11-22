# Data protection in the Platform MVP

State on 22 November 2026. Overview and status words: [README.md](README.md).

## What it is

How the Platform MVP keeps a firm's documents and its people's data where they belong: what leaves the installation,
who may see which document, what logs may hold, how secrets are kept, and which data is used for building and which
for the pilot. It is not one component; each rule lives in the part named beside it.

## What a person can do with it

- **An administrator** decides whether the assistant is on and which provider answers (the Knovas Core or Anthropic),
  enters the provider's key, and sets access groups and folder rules; later she sees and switches the enforcement
  position with a second administrator (PMVP-42, not started).
- **An employee** sees and deletes only her own chats; a document opens only if her own search or view handed it to her.
- **Who may do what is decided by roles** (RB-1, RB-2, built, not yet verified): a person may do what her roles allow,
  and the Platform signs her requests with the access groups she holds directly and through her roles, never more than
  32.

## How it protects data and access

**What leaves the installation.**

| To | What | When |
|---|---|---|
| The chosen provider: the Knovas Core or Anthropic | the assistant's instruction, the passages this person's search returned (each up to 1,200 characters of the text around the hit, PMVP-20 context), the question and the chat so far (DEC-0084) | every question, only while the assistant is on |
| Anthropic | one invented passage and question, no customer text | an administrator presses "Verbindung testen" |
| The Knovas Core | searches and per-thing reads as the signed-in person, over the Platform's certificate (as today) | search, dossier |
| The Knovas Core | the assistant's searches and its generation calls with **the assistant's own signed claim** (its id, the groups and functions of its roles that the person also holds) and "the assistant, for this person" (both ids) | every question, only while the assistant is on (RB-6) |
| The Knovas Core | the assistant's reads of a document's text (`GET /secured/document/<id>/text`): only the documents this question's search returned, at most 8, with the same claim and "the assistant, for this person"; the document's id and where to read from, no text | every question, only while the assistant is on (PMVP-20 context) |
| The Knovas Core (for its MCP gateway) | per external agent, every minute: **20 signed claims** (the agent's id, the groups and functions of its roles, narrowed to its person if it has one), each valid at most 300 seconds; or "drop this agent's claims" | only while the switch «Agenten über das Knovas-Gateway» is on (off by default; RB-7) |

The pilot (Knovas GmbH) chose Anthropic on 21 November, so its passages may be sent there. Fonts and styles load from
the installation itself.

**Access rights through the Brain's search.** The assistant finds documents only through the person's search (FU-14).
Since the founder's decision of 22 November it also reads, for the hits that search returned and only those, the text
around each hit from the Core's document text, under five limits, each tested: only documents this question's search
just returned; the Core decides every read and any refusal keeps the search's short text; as the person, with the same
claim as the search; at most 8 documents, 3 parts and 8 seconds a question, one read at a time; nothing of the text in a
log line. The Core does not yet read the claim on this request (CR-1c): with enforcement off it reads as the search
does, under the Core's `enforcing` it reads open documents only, and under «Ein» (`brokered`) it refuses every read,
so the short texts stay; it never reads a document the search did not return.

The dossier reads one thing at a time as the person; a thing she may not see looks like one that does not exist, and a fact whose
source document was not listed for her is marked "ohne Beleg" with the document hidden. **But** the Brain's search
follows rights only where a customer's enforcement is on, and it is off for every tenant today; until then a search,
and so what goes to a provider, may hold passages beyond the person's rights (RISK-03 on the assistant's card). No
customer is switched to "Ein" before the Core's end-to-end rights test has passed twice (DEC-0082).

**No customer text in logs** (founder, 19 November). MVP items log ids, counts, durations, status and error types,
and test their lines for customer text. A sweep of the whole web application (maintenance, pull request #55) removes
questions, bodies, passages, exception messages, document names and paths and e-mail addresses from every log line.

**The Knovas client's own lines** (RB-FIX-1, FU-43, verified with notes, integrated). When a call to the Knovas Core fails, the
Platform logs which kind of call it was (`/secured/access_groups/{id}`, never the id itself: a group's id can be a
name the firm chose), the answer's status, Knovas's error code when it is a short code word, Knovas's own number for
the request (so Knovas can find its side) and how long the answer was - never its text, which can quote a role's
name, a question or a passage. The same holds for the search's diagnostic line (it counted the passages but printed
them) and for an uploaded document (its name is no longer logged). The HTTP library under the client no longer writes
each request's address at the most detailed log level (it held document addresses).

**The errors the client hands to the rest of the Platform** (RB-FIX-2, built, not yet verified; RB-FIX-1 had said
this already held, which was true only for a refused call). An error the client raises names the kind of call and,
for a refusal, the status, Knovas's short error code and Knovas's request number - never the address with its
query (when Knovas could not be reached, the error used to quote the whole address, `?q=` with a name included, and
pages that log the error's trace wrote it), and never Knovas's own text (it can quote what was sent). A page that
shows a refusal says it in its own words. The Cortex pages' error lines no longer name the id from the address
either. The HTTP library's warnings (an answer whose headers do not parse, a full connection pool) are kept, with
every address in them replaced by `<url>`.

**Finding Knovas's own message.** The Platform's log line for a failed call, and the error the client raises, carry
Knovas's request number (`request_id=…`, Knovas's `X-Request-ID`). An operator who needs Knovas's full message
looks that number up in the Knovas Core's own logs, where every line of the request carries it (`trace_id`); the
Platform keeps only the short code and the number.

**Keyed fingerprints.** Where a log line must name a document, the sweep writes a fingerprint keyed with the
installation's own secret, so nobody can test a guessed client or matter name against the logs; without a key it says
"nokey", never an unkeyed fingerprint. An operator on the installation can compute a document's fingerprint to find
its lines.

**Secrets encrypted** (as built in PMVP-25, not yet verified). The provider's key is stored encrypted by the settings
store with the installation's settings key, which comes from the deployment, not from the repository. It never goes
back to the browser, into a log, an error, a page or the audit entry (which says "geändert"), and errors from the
provider are passed on without the request that carries it. Without a saved key no call is made. Keys used for
building are kept outside the repository.

**Chats** stay in the Platform's own database, owner only; the chat store does not encrypt their text itself.

**Roles** (RB-1, RB-2). Who may do what is now decided by roles, kept in the Platform's own database like accounts.
The move to roles happens inside the installation when the new version starts: nothing leaves it, nothing is written
to the Knovas Core, and everyone keeps the functions she had (each person passes the same standard pages and buttons
as before; tested). The one gain, as intended, is that everyone becomes a member ("Fragen und suchen"): where an
installation has opened a custom view, a profile view or the editing of document fields to members, people who were
not members before now get them. The old grants stay untouched for one release as the way back. A role's Core group
is only recorded until RB-4 links it in the Core on an administrator's action, so roles do not yet change what anyone
may read. No role name, description, person's name or address is written to a log. A request is never signed with more
than 32 access groups (the Core's limit): a change that would give a 33rd is refused, and nothing is dropped silently.

**A role's data** (RB-5, verified with notes, not yet integrated). What a role may read is a folder rule or a document's own rights
at the Knovas Core naming the role's group; the Platform keeps no copy and reads them back on every page. An
administrator adds or removes them on the role page, always after a dry run that names who will gain and who will lose
sight once enforcement is «Ein»; walling an open folder or document, or opening a walled one, needs an explicit tick.
The change takes the four-eyes path of the «Zugriff» pages (with the installation's bypass on, an administrator acts
alone and it is recorded; off, a second person confirms); only an administrator carries it out. **Until «Ein»
(CR-R1, CR-R2) the rules are stored at Knovas and hide nothing.** Moving direct group grants into roles changes nobody's
groups or functions (compared inside the move) and writes nothing to Knovas. Audit rows and logs hold ids and counts
only: never a path, a document's or group's name or a person; the approval request keeps the path for the approver's
page, and its log lines and audit rows name the role by id. The approvals' bypass line in the log names the
administrator by id, no longer by address.

**Agents** (RB-6, RB-7, built, not yet verified). An agent is a member of roles that is not a person: the Knovas
Assistent, or an external agent (Claude Code, Copilot) an administrator registers.

- **One rule for every agent:** it reads what its roles allow and, acting for a person, only what she may also read; a
  person's own direct access groups reach no agent, and no agent is in Administration. The Platform signs that claim;
  the Core stays the only judge of what it shows.
- **The assistant** asks the Core with its own claim narrowed to the person who asks, so an administrator can keep it
  out of a role («Personal») even for people in that role. This narrows only once the Core enforces the claim (CR-R1,
  «Ein»); until then it is signed and logged and changes nothing.
- **External agents** read through Knovas's MCP gateway (`mcp.knovas.ch`), never through the Platform, with a key Knovas
  issues for the agent's id. **The Platform holds no agent key**: no table has a column for one. The Core receives
  signed claims with the agent's groups and keeps them at most 300 seconds; an external agent reads through the gateway
  what its claim allows, at most 20 calls a minute. With the switch on, revoking an agent on the Agenten page drops
  its claims at the Core at once. Deactivating its person on the people page reaches the Core with the next minute's
  push, which drops them; the claims the Core already holds end within 300 seconds at the latest. Deleting a person
  deletes her agent and sends the Core nothing; its claims there also end within 300 seconds.
- **On client installations agents are switched on only after «Ein»** (founder, 21 November); the switch is off by
  default and, off, nothing about agents reaches the Core.
- **Logs and audit hold ids only:** an agent's id, counts, HTTP statuses and error classes; never a claim, an agent's
  or a person's name, a group id or a question.

**Demo tenant and Knovas GmbH tenant.**

| | Demo law firm tenant | Knovas GmbH tenant (Client Zero) |
|---|---|---|
| Purpose | development data: invented law-firm documents | the measured pilot |
| What the build may do | read-only searches, at most one every two seconds; nothing else | nothing; its test questions, people and rights table come with PMVP-43 |
| What reaches it | nothing | only descriptions of the new components (a domain, four components, one decision), written by the existing loader when the KnowledgeBase change is merged; no document, no card mapping |

## Where it stands

| Item | State | Approves before main |
|---|---|---|
| Log sweep, pull request #55 | verified (second verification, with notes); not on main; not in the build branch | open: no class named |
| The Knovas client's log lines (RB-FIX-1, FU-43) | verified (with notes); integrated | Siran (sensitive) |
| The client's errors and the HTTP library's warnings without addresses or Knovas text (RB-FIX-2) | built; no verification verdict yet | Siran (sensitive) |
| Second log sweep (FU-22, FU-31) | not started; waits for #55 | its web-server part is deployment: sensitive |
| Key handling (PMVP-25) | built; no verification verdict yet | management (critical) |
| Enforcement switch (PMVP-42); pilot rights test (PMVP-43) | not started; PMVP-42 waits for the Core's rights test | management (critical); Siran (sensitive) |
| Roles: the move to roles, functions and groups read from roles, the 32-group cap (RB-1, RB-2) | built; no verification verdict yet | management (critical: a migration) |
| Agents: one signed claim for every agent; external agents' claims handed to the Core behind a switch (RB-6, RB-7) | built; no verification verdict yet; acts with the Core's CR-R1, CR-R5, CR-R6 | management (critical: a migration, signing and claims) |
| A role's data: folder rules and document rights written to Knovas after a dry run, four eyes, adopting a group, moving direct grants (RB-5) | verified (with notes); not yet integrated; hides nothing until CR-R1, CR-R2 and «Ein» | Siran (sensitive) |
| The assistant's reads of the text around each hit, under the five limits (PMVP-20 context) | built; no verification verdict yet; reads as the person once the Core takes the claim (CR-1c) | Siran (sensitive) |

## Decisions taken

| Date | Who | What |
|---|---|---|
| 19 Nov | founder | No customer text in the logs |
| 19 Nov | founder | Pilot with enforcement on; "Ein" only after two green end-to-end runs of the Core (DEC-0082) |
| 19 Nov | founder | No formal model needed for "reads only through search" (FU-14) |
| 20 Nov | after the sweep's first verification | The log fingerprint is keyed with the installation's secret |
| 21 Nov | founder | The customer chooses the provider; the pilot's passages may go to Anthropic |
| 21 Nov | founder | The Platform signs agents' claims; external agents use only Knovas's gateway ("Core gateway only"); on client installations agents only after «Ein» |
| 22 Nov | founder | The assistant may read the Core's document text for the hits its search returned, under five limits (POST-02 amended) |

## Known limits and open points

- **Enforcement is off for every tenant.** The Core's last real end-to-end run (10 August) showed a document to a person
  without the right; the repair is outside this plan (Jira SS-664, SS-665, SS-572). Tripwire: no green run by 1 December.
- **Provider terms (FU-33):** region, retention and what a client contract says about sending passages: open before the
  first law firm.
- **Older log lines on the build branch:** a document address (FU-25), until the sweep reaches main and main is
  merged into the build branch. The two lines of FU-09 (the Knovas client) are rewritten on the build branch by
  RB-FIX-1; the sweep changes the same lines, so that merge keeps RB-FIX-1's.
- **Left after the sweep (FU-22, FU-31):** text can still reach a log outside the web application (experiments runner,
  sync agent, scripts); the web server's default error log writes document addresses; the application server's own
  error line; two unkeyed fingerprints; refusals that name a view's file.
- **Harness limit (FU-36):** names differing only by "ß"/"ss", a ligature or a Unicode form count as one document, so a
  rights question could pass on the wrong one; the pilot's real names are checked before PMVP-60.

## Depends on

The Knovas Core's rights engine and its end-to-end test; the Platform's certificate (it sets the tenant); the
installation's web secret (fingerprints) and settings key (secrets); Anthropic, if chosen.

## Owner

No single card; the rules sit on the assistant's, Admin's and access cards (engine owner role, proposed). The logs rule
is the founder's.

**Where the detail is:** `docs/platform-mvp/follow-ups.md` (FU-09, 14, 22, 25, 31, 33, 36, 37);
`docs/platform-mvp/work-orders/PMVP-25.md` (branch `feat/pmvp-25`), `PMVP-20-passages.md`, `PMVP-20-context.md`, `RB-6-7.md` (branch
`feat/rb-6-7`); `docs/platform-mvp/core-requests.md` (CR-1c, CR-R1, CR-R5, CR-R6); branch
`maint/platform-logs-no-customer-text`; KnowledgeBase decision DEC-0082.
