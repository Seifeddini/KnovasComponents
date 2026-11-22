# The Knovas Assistent

State on 22 November 2026. Overview and status words: [README.md](README.md).

## What it is

The Knovas Assistent answers an employee's question from the firm's own documents. It looks for passages with the
same search the employee uses, as that employee, and asks a language model to answer in short statements, each tied to
one passage and carrying a word-for-word quote from it. Plain program code checks every statement before it is shown;
one that fails is left out. If nothing is left, the assistant says "Dazu finde ich nichts in Ihren Quellen" and lists
the closest passages instead of answering from general knowledge. Customers see "Knovas Assistent", never a model's
name.

## What a person can do with it

- **Ask or search in one box** on the search page, with a switch "Fragen / Suchen"; one to three words without a
  question mark default to searching; the person's choice wins (PMVP-22a).
- **Read the answer** as numbered statements. A click on a number opens the quote under the statement, with the
  document's title and page and a link that opens the document as a search hit does. All passages are listed below.
- **See that something was left out** ("2 Aussagen ohne Beleg ausgelassen"), never the removed text.
- **Get a plain message** when the service is busy ("ausgelastet") or fails; never half an answer.
- **Ask on in the same chat** (PMVP-23): every answered question becomes her chat, and the box under the answer
  («Nachfragen oder etwas Neues fragen …») asks a follow-up there. «Und wer hat unterschrieben?» after a question on
  a contract is searched together with that question, so it finds the same contract.
- **Find her chats again** (PMVP-23): the sidebar shows «+ Neue Frage» and her newest 20 chats under «Heute»,
  «Diese Woche» and «Früher», the open one marked; «Alle Gespräche» lists every chat, 50 a page. She opens one and
  reads it as it was answered, asks on in it, or deletes it («Gespräch löschen», asked first). A document of an old
  chat opens after the Core confirms she may still read it. On a phone the list folds behind «Gespräche».
- **An administrator** switches the assistant on, chooses provider and model, enters the key and presses
  "Verbindung testen" (PMVP-25).
- **Not yet:** the passage opened inside the document (PMVP-22b), waiting states (PMVP-24), renaming or searching
  chats (W-10, 12 January).

**The five checks.** A statement is shown only if (1) it is in the agreed form, (2) its source is a passage this
person's search returned, (3) its quote stands word for word in that passage (three words or more), (4) every number in
it stands in the passage, and (5) it has at most 500 characters and is not the "nothing found" sentence. The quote shown
is cut from the passage, never taken from the model. The verifier's 148 hand-made attacks and 120,000 generated
statements found no way past. Trade-off: 10 of 10 realistic answers with exact quotes survive, 6 of 10 with typical model
slips.

## How it protects data and access

- **One way in:** documents are read only through the person's own search; what search would not show her, the
  assistant never sees or sends on.
- **Its own claim** (RB-6): the assistant is a member of roles like a person (every role but Administration, until an
  administrator takes it out of one, for example "Personal"). Every call it makes to the Core for an answer (the
  search, and the generation with the provider "Knovas") carries the assistant's own signed claim: the groups of its
  roles that the asking person also holds, never more than hers, and it says "the assistant, for this person". If the
  assistant cannot be read, the answer fails; it never falls back to the person's own claim. This narrows only once the
  Core enforces claims (CR-R1, "Ein"); until then it is signed and logged and changes nothing.
- **What leaves the installation:** the instruction, the passages search returned, the question and the chat so far, to
  the provider the administrator chose ([data-protection.md](data-protection.md)).
- **Logs** hold counts, reasons, durations and error types; no question, statement, quote or passage (tested).
- **Plain text only** on the page: no formatting, code or links from the model.
- **Documents open** only for this reply's passages and only for this person; the question is kept out of the page
  address, so it does not reach the web server's access log.
- **The key** is stored encrypted and never shown, logged or put into an error; without a key no call is made.
- **Chats:** only the owner reads, continues or deletes them; another person's chat, a missing one and a wrong
  address all answer the same "not found", before anything is asked or opened; deleting removes messages and
  references; removing a person removes her chats. A stored answer keeps its passages (owner only, deleted with
  it, never sent to the model again: a follow-up sends the earlier statements only). Chats are **deleted
  automatically 90 days after their last message**, by a job in each web worker that runs once a day for the whole
  installation; the audit log records how many, nothing else. A question and its answer are stored together or not
  at all; if storing fails, the answer is shown once with «Dieses Gespräch konnte nicht gespeichert werden.».
- **Off by default:** while off, the ask page does not answer and the box only searches.

## Where it stands

| Item | What | State | Approves before main |
|---|---|---|---|
| PMVP-20 | the service: search, compose, generate, check | verified 21 Nov (with notes); integrated | Siran (sensitive) |
| PMVP-20 hardening | numbers checked against the quote; months, number words, Roman numerals, signs | in build; nothing pushed | Siran (sensitive) |
| PMVP-20 passages | longer passages for the model | built; stopped on one point (below); not verified | Siran (sensitive) |
| PMVP-21 | chat storage: three new tables, owner only | verified 19 Nov (with notes); integrated; no page uses it yet | management (critical: database change) |
| PMVP-22a | the ask page | built; no verification verdict yet | Jannik or Siran (routine) |
| PMVP-25 | switch, provider and model, key, connection test | built; no verification verdict yet | management (critical: a secret) |
| PMVP-22b | passage in the document | not started | routine |
| PMVP-23 | follow-up questions and past chats (list, open, delete; deleted after 90 days); takes up FU-01, FU-02, FU-03, FU-06, FU-07 | built 22 Nov on `feat/pmvp-23` (33 frozen acceptance tests pass); no verification verdict yet; no migration | Siran (sensitive) |
| PMVP-24 | waiting line, a time limit under the web server's, failure states | work order decided 22 Nov; after PMVP-23 | Jannik or Siran (routine) |
| RB-6 | the assistant as a member of roles, answering with its own claim narrowed to the person | built; no verification verdict yet | management (critical: a migration, with RB-7) |

## Decisions taken

| Date | Who | What |
|---|---|---|
| 19 Nov | founder | No formal model needed for "reads only through search": the Brain's search enforces access (FU-14) |
| 19 Nov | founder | No customer text in the logs |
| 20 Nov | founder | Paused after two failed verifications (German legal text could not be cut into sentences safely); resumed the same day with statements, sources and quotes |
| 21 Nov | founder | The Platform calls the model itself; the customer chooses the provider; Knovas chose Anthropic, so its passages may be sent there (FU-33) |
| 21 Nov | coordinator | Probe: Claude Opus 5.5 answered 8 of 8 invented cases correctly in the statement form |
| 21 Nov | coordinator | No automatic retry towards Anthropic, so no question outlasts the web server's 180 seconds; the key has no setting in the deployment files |
| 19 Nov | verification record | Chat storage is critical under the repository's rule for database changes (the plan said sensitive) |
| 21 Nov | founder | The Platform signs agents' claims, the assistant's too; one rule for every agent (its roles, narrowed to the person) |
| 22 Nov | founder | A chat keeps its passages (owner only, deleted with it); chats are deleted automatically 90 days after their last message, by a daily job that runs once however many web workers there are |
| 22 Nov | coordinator | A follow-up searches the chat's last question and the follow-up together (D1); the sidebar shows the newest 20 chats and «Alle Gespräche» (D4) |

## Known limits and open points

- **Short passages (FU-37):** the Core sends each hit's own short text (2 to 172 characters measured on the demo
  tenant). The passages work drops passages under three words but cannot lengthen them; a request to the Core for
  neighbouring sentences (up to 1,200 characters) is written. Decide after the founder has tried the assistant on the
  demo tenant.
- **What the check cannot see (FU-27):** a statement that says more in words than its quote; a "judge" model is planned
  after the pilot, with the Core.
- **No model in production behind the Core (FU-32):** with the provider "Knovas" the pilot gets no answers; owner to be
  named. The Anthropic path removes this once verified and approved.
- **Provider terms (FU-33):** region, retention and client contracts are open before the first law firm; the decision
  record is not yet written.
- **Number check too generous** today (it reads the whole passage); the hardening is in build.
- **Smaller points:** a slow Core against the web server's time limit (FU-08, PMVP-24), the demo profile (FU-10).
  Taken up by PMVP-23: long chats (FU-06: a history of numbers is weighed as tokens; FU-07: the newest question and
  answer are cut, not dropped), input edge cases in chat storage (FU-01: bad characters and page numbers are
  cleaned and a failed write answers with a message, not an error; FU-02: a long question is cut to a 200-character
  title; FU-03: the list comes in pages).
- **Follow-ups (D1):** a follow-up is searched together with the chat's last question. A follow-up that changes
  the subject still carries the last question into the search, and "und im ersten Vertrag?" further back is not
  resolved; a second model call that rewrites the follow-up was not taken (cost, time, a generation slot).
- **The component card** still says "planned" and names withdrawn test files (FU-18).
- **Decided by the founder on 22 Nov (PMVP-23), built:** a stored chat keeps the text of its passages (owner only,
  deleted with it); chats are deleted automatically 90 days after their last message (the coordinator's reading of
  "90 days").

## Depends on

The Knovas Core's search and its access rules; a language model, the Core's generation route or Anthropic's Claude API,
chosen per installation; the Platform's database (PostgreSQL) for chats and settings; the settings store and the
installation's settings key; the shell.

## Owner

The engine owner role, proposed on the card; a person has still to confirm it.

**Where the detail is:** `docs/platform-mvp/work-orders/PMVP-20*.md`, `PMVP-21.md`, `PMVP-22a.md`, `PMVP-25.md`
(builder's notes on `feat/pmvp-22a`, `feat/pmvp-25`); `docs/components/L2-platform-assistant.md`;
`docs/platform-mvp/follow-ups.md`.
