# Work order PMVP-23: follow-up questions and past chats

**Decided on 2026-11-22** by the coordinator and the founder (see "Decisions"); its acceptance tests are frozen.

| | |
|---|---|
| Item | PMVP-23 (Jira: filed by the coordinator), plan `FEAT-2026-11-19-platform-mvp` § 4 **W-06** (follow-ups keep the chat's context), **W-07** (past chats: a list per person, open and delete, owner only); § 5.2 step 3 ("add the last turns of the chat within the Core's limits"); spec items SPEC-platform-mvp-05 and -06; contract L2-PLATFORM-ASSISTANT POST-01, POST-02, POST-03, POST-04 |
| Follow-ups taken up | **FU-01** (bad input reaches the chat store and answers 500), **FU-02** (a title over 200 characters is refused; 22a stores no chats, so it falls here), **FU-03** (`list_chats` has no limit), **FU-06** (a digit-heavy history overflows the model), **FU-07** (no history at all when the turns are long). Also FU-26 (what "refused" is), FU-34 (hrefs from `url_for` only), FU-46 (log tests set DEBUG explicitly) |
| Class | **sensitive** (Siran approves before `main`): the plan's § 6.5 lists chat storage as sensitive, and this item is the first code that reads, lists and deletes stored chats (POST-03; spec item 06 is classed sensitive). It also changes how the assistant service composes a prompt (PMVP-20's sensitive module). **Not critical**: no migration, no change to an existing table (`list_chats` gains arguments; PMVP-21's tables stay). If the work needs a migration, stop and write why under "Builder's notes" |
| Depends on | PMVP-21 (chat store, integrated `7115ac8`), PMVP-22a (ask page, integrated `205d7126`), PMVP-25 (switch, provider), RB-6 (the assistant answers with its own claim, `AsTheAssistant`): all on `feat/platform-mvp` |
| Branch / worktree | `feat/pmvp-23` from `origin/feat/platform-mvp` (`74478d1a` or later), worktree `.worktrees/pmvp-23` |
| Visual contract | `docs/platform-mvp/reference-screens/01-assistent.html` and `out/01-assistent.png`, with `shell.js` for the Workspace's sidebar: «+ Neue Frage»; the person's own chats under «Heute» and «Diese Woche», the open one marked; under the answer the box «Nachfragen oder etwas Neues fragen …» with «Fragen» |
| Card | `docs/components/L2-platform-assistant.card.yaml` (its page, `boundaries.yaml`); `L2-platform-shell.card.yaml` for the sidebar's new block |
| Core requests | None needed. Before **CR-1** lands, follow-ups work and their answers are as thin as first answers (FU-37: short passages). **CR-2** is not used. The rights requests (CR-R1 and later) change nothing here: the follow-up's search is the person's own, as today |
| Frozen tests | `P/tests/test_pmvp23_chats_followups_acceptance.py`, blob `3fd3e660755cb9f26d366a029f675b424917c053` in `docs/platform-mvp/frozen-tests.txt` (33 tests: SPEC-05 and SPEC-06's examples, the owner checks on the four chat addresses, signed out, the form's token, the switch, the list and the sidebar's 20, delete, D2, D3 with two workers, no customer text in a log line at DEBUG). Proven by the drafting session against a throwaway reference in its scratchpad (not committed): 33 pass; on today's branch 31 fail (the two that pass: `busy` and `failed` store nothing); 16 of 16 mutants killed |
| Points | 5, one item (D6) |

`P` = `KnovasPlatform/components/docbridge_integration`.

## Goal

A person asks, reads the answer, and asks on in the same chat: «Und wer hat unterschrieben?» after a question on a
contract answers about the same contract. Every question and its answer are kept as that person's chat. The
Workspace's sidebar lists her own chats, newest first; she opens one and reads it as it was answered, asks on in it,
or deletes it. Nobody else sees, opens or deletes it. A chat is deleted by itself 90 days after its last message.

## What PMVP-23 does not do

The waiting line and the time limit (PMVP-24); the passage inside the document (PMVP-22b); renaming or searching
chats (W-10) and re-checking old references (W-11), both 12 January; «Wie kam die Antwort zustande?»; names as links
to a dossier (PMVP-33).

## What to build

1. **A question becomes a chat.** `POST /fragen` (22a's route): after an `answered` or `no_answer` reply, create the
   chat for the signed-in person, store the question (role `user`) and the answer (role `assistant`, item 3), grant
   the reply's passages as 22a does, and answer `303` to `GET /fragen/<chat_id>`. The title is the question as one
   line, cut at a word boundary to at most 200 characters with «…» when cut (FU-02). `busy` and `failed`: nothing is
   stored; the page as today (the plain message), with the question back in the box.
2. **The chat page** `GET /fragen/<chat_id>`: `ask.html` draws every turn in order: the question (the first as the
   page head, later ones as a heading of the next level), the answer as 22a draws it (statements with their chips,
   the quote under the statement, «Quellen» or «Die nächsten Stellen», the removed line), then the box «Nachfragen
   oder etwas Neues fragen …» with «Fragen» (item 4), and «Gespräch löschen» (item 6). Quote ids are unique on the
   page (`zitat-<turn>-<i>`). The newest turn has an anchor; the redirect lands there.
3. **What an answer is stored as** (no migration). The assistant message's `text` is one JSON object, the answer as
   shown: `{"v": 1, "status": "answered" | "no_answer", "statements": [{"text", "source", "quote"}], "removed": n,
   "sentences": {"<passage number>": <sentence number>}}` (the sentence numbers are what 22a puts into `s=` to mark
   the passage). Its references are the reply's passages: number, pointer, page, title and `text` = the passage as
   search returned it (decision D2). The page draws a stored answer exactly as it drew it when new. A message that
   does not parse as that shape is shown as one plain paragraph without chips, and its error type is logged.
4. **A follow-up** `POST /fragen/<chat_id>` (W-06, SPEC-05). Read the chat as its owner first: another person's
   chat, a missing one and a malformed id give the same 404, before anything is asked. Then:
   - **history**: the chat's turns as `Turn`s, oldest first: a user turn as typed; an assistant turn as its
     statements' text, one per line (no quote, no passage, no JSON); a `no_answer` turn as `NO_ANSWER_TEXT`;
   - **what is searched**: the chat's last question and the follow-up together (decision D1);
   - `service.answer(question, history, search_text=…)` (item 5), through `AsTheAssistant` as today;
   - `answered` / `no_answer`: store both messages, grant the passages, `303` to the chat at the new turn;
     `busy` / `failed`: the chat page as it was, the plain message under the last turn, the question back in the box,
     nothing stored.
5. **The service** (`P/src/assistant/service.py`), and only these changes:
   1. `AssistantService.answer(question, history=(), *, search_text=None)`: the search uses `search_text` when given
      (cut to `MAX_QUESTION_BYTES`), the prompt uses `question`. `AsTheAssistant.answer` passes it on. Without it
      everything is as before; PMVP-20's frozen file passes unchanged.
   2. **FU-07**: `_fit_history` always keeps the newest pair (question and answer), cutting it to fit if it must
      (the answer first, then the question, with `cut_to_bytes`), and then adds older pairs whole while they fit.
      Whole pairs only; the history starts with a user turn.
   3. **FU-06**: the history's size is counted with each digit weighing one token (three bytes at the budget's rate
      of about three bytes a token), so a history full of numbers stays within the model's 8,192 tokens. How the
      passages are fitted does not change.
6. **Delete** `POST /fragen/<chat_id>/loeschen`: delete as the owner (messages and references go with the chat), then
   `303` to the search page. Another person's chat, a missing one or a malformed id: the same 404, nothing deleted.
   On the chat page one button «Gespräch löschen»; with JavaScript it asks first («Dieses Gespräch löschen? Das lässt
   sich nicht rückgängig machen.»), without JavaScript it deletes at once.
7. **The list** (W-07, SPEC-06, FU-03). In the Workspace's sidebar, while the assistant is on and a person is signed
   in: «+ Neue Frage» (to the search page's box) and the person's newest 20 chats, grouped «Heute», «Diese Woche»,
   «Früher» by their last change (the installation's time zone), the open chat marked `aria-current="page"`; when
   there are more, «Alle Gespräche» to `GET /fragen/verlauf` (every chat of hers, 50 a page, newest first, «Ältere»
   and «Neuere»). `ChatStore.list_chats(*, owner, limit=None, offset=0)`: without the new arguments it lists as
   today.
   - The block stands **after** the sidebar's main `<nav>` **and after the `<script>` that follows it** (that script
     finds the menu as its previous sibling), in an element of its own (for example `<nav aria-label="Gespräche">`):
     PMVP-11's frozen navigation test reads the first `<nav>` of the sidebar as the menu, and that must stay «Suche»
     alone. Nothing in the block is marked current on the search page.
   - Titles are the person's text: escaped, `dir="auto"`, one line, cut by CSS.
   - A failed read draws no list and never breaks the page (the error's type is logged).
   - At 390 px nothing scrolls sideways (the shell turns the sidebar into a row there). The mark-up always holds the
     newest 20 (the frozen test reads them); if they do not fit the row, fold them with CSS behind «Gespräche» and
     say so in your notes.
8. **Opening a document from a stored answer.** Every document link on the chat page goes to
   `GET /fragen/<chat_id>/quelle/<turn>/<number>`. That route reads the chat as the owner, takes that turn's
   reference, asks `document_readable(pointer)` as the person, grants that one document and redirects where 22a's
   `_open_href` sends it (a PDF in the browser's viewer at its page and sentence, else the file). Anything else gives
   the same 404, with nothing granted. This is the dossier's pattern (`/dossier/<id>/dokument/<n>`, PMVP-30a rework
   1): grants last an hour (`document_grants.DEFAULT_TTL_SECONDS`), and a stored chat outlives them.
9. **Bad input** (FU-01). Before anything is stored: NUL characters are removed from the question, the statements,
   the quotes, the titles and the passage texts; a page outside 1 to 2^31−1 is stored as no page; a reference whose
   pointer is empty after that is left out. A question and its answer (and a new chat) are stored in **one
   transaction** (`conn.transaction()` around the store's calls; psycopg nests the store's own as savepoints), so a
   failure leaves nothing half stored. If storing fails, the reply is shown once, unsaved, with «Dieses Gespräch
   konnte nicht gespeichert werden.» (status 200, the error's type logged); never a 500.
10. **Forms and tokens.** The two new form posts carry the hidden `csrf_token` field like `assistant.ask`. Add their
    endpoints to `_CSRF_EXEMPT_ENDPOINTS` in `app.py`, with one comment, and change nothing else in `app.py`. Each
    handler refuses a missing or wrong field with the form's usual 400 before it reads the chat.
11. **Logs** (POST-04): one line per request with the status and counts (turns, history turns sent, statements); ids
    at most. Never a question, a title, a statement, a quote, a pointer or a passage.
12. **Deletion after 90 days** (D3). In `P/src/identity/chats.py`: `RETENTION_DAYS = 90` and
    `purge_expired_chats(conn, *, days=RETENTION_DAYS) -> Optional[int]`. In **one transaction** it takes
    `pg_try_advisory_xact_lock(hashtextextended('knovas.assistant.chats.retention', 0))`; if another worker holds
    it, it returns None. Under the lock, if an `audit_log` row `assistant.chats.retention` exists for today (UTC),
    it returns None. Otherwise it deletes every chat whose `updated_at` (which every new message moves: the last
    message) is more than 90 days old, its messages and references by the existing cascade, writes one audit row
    (`identity.audit.record`, action `assistant.chats.retention`, target type `assistant_chats`, detail
    `{"deleted": n}`: no id, title, owner or actor) and returns n. One log line with the count.
    **How it runs** (coordinator's question, answered here): a thread in each gunicorn worker, `ChatRetentionJob`,
    built like RB-7's `AgentSyncJob` (`identity/agent_delivery.py`): it ticks every hour on a connection of its own
    (`gate.open_connection`), calls `purge_expired_chats`, and never raises. `install_assistant` starts it whenever
    personal accounts are on, **whatever the assistant's switch says** (chats stored earlier still expire), and
    not under `app.testing`. The first tick is one interval after the start. Why this and not a CLI or a
    scheduler: the deployment has none (one container, gunicorn with `--workers ${DOCBRIDGE_WEB_WORKERS:-2}`; no
    cron, no compose job), a CLI would need one outside the Platform (a deployment change: sensitive), and the
    thread pattern exists and was verified with RB-7. The transaction's lock makes one worker do the work; the
    audit row makes it once a day, also across restarts. **It must run once even with several workers**: the
    frozen test runs it from two connections at the same moment.
13. **The cards.** `L2-platform-assistant.card.yaml`: the operations `GET /fragen/<id>`, `POST /fragen/<id>`,
    `POST /fragen/<id>/loeschen`, `GET /fragen/<id>/quelle/<turn>/<n>` and `GET /fragen/verlauf`, with their lines
    (POST-01 for a stored turn, POST-02 for a follow-up's passages, POST-03 and POST-04 with your tests as evidence);
    the `review` entries that name FU-06 and FU-07 replaced by evidence; `chats.py` now has callers.
    `L2-platform-shell.card.yaml`: the sidebar's chat block (it draws what it is handed and names no role).
    `boundaries.yaml`; card checks as in `work-orders/PMVP-01.md`.

## Rules

1. **Owner only, everywhere.** Every route reads and writes through `ChatStore` with the signed-in person as the
   owner; no route takes an owner from the request. Another person's chat, a missing one and a malformed id give the
   same 404 page, and no log line tells them apart.
2. **POST-02 holds for follow-ups.** Passages reach the prompt only from this turn's search, as the person (with the
   assistant's claim narrowed to her, RB-6). Earlier turns reach the model as the text of their statements only:
   no stored passage text and no quote is ever sent again.
3. **The Core's limits** (32 messages, 50,000 bytes each, the prompt budget) are kept by the service's composer. The
   route hands over the whole history; it does not cut it itself.
4. **Nothing half stored, nothing stored for `busy` or `failed`.**
5. **Text stays text.** Autoescape on; nothing marked safe; every href built with `url_for` (FU-34); no Markdown or
   HTML from the model or a title.
6. **The switch.** With the assistant off, every new address answers 404 and no chat list is drawn; without personal
   accounts too.
7. New `.py` files ASCII-only (`scripts/check_ascii_py.py`); German words as `\u` escapes, as in 22a.

## Decisions (2026-11-22)

- **D1, what a follow-up searches for:** the chat's last question and the follow-up together. It is deterministic
  and costs no second model call. The alternative, a model call that rewrites the follow-up into a question of its
  own, doubles the cost and the time, takes a second of the Core's four generation slots, and can fail on its own.
  Without one of the two, SPEC-05's example cannot pass: the assistant's README lists "a follow-up is searched as
  typed" as a known limit. **Decided (coordinator): the last question plus the follow-up.**
- **D2, passage text kept with the chat:** the reference's `text` column (PMVP-21 made it for this) holds the
  passage as search returned it, so PMVP-22b can show it under the sentence later without a read the Core does not
  offer (CR-2). It is never sent to the model again, is read by its owner only, and is deleted with the chat. The
  alternative is to keep only the quotes. **Decided (founder): keep it; owner only; deleted with the chat.**
- **D3, how long chats are kept. Decided (founder): deleted automatically after 90 days.** The coordinator reads
  this as 90 days after the chat's **last message**, so a chat in use is not cut off; `data-protection.md` says so,
  and the founder may correct it. A daily job deletes them with their messages and references (item 12); owner
  deletion and `ON DELETE CASCADE` stay.
- **D4, the list in the sidebar**, as the reference draws it: the newest 20 and «Alle Gespräche». **Decided
  (coordinator).**
- **D5, frozen tests. Decided (coordinator):** frozen before the build (see the table at the top). The class stays
  sensitive.
- **D6, size. Decided (coordinator):** one item of 5 points.

## Your tests (`P/tests/test_assistant_chats_*.py`)

The frozen file pins the addresses, the service's call, `list_chats`'s arguments and the retention function. Your
own tests cover the rest of the order, in particular FU-01, FU-02, FU-06, FU-07, the 303's anchor, «Alle
Gespräche»'s pages, the store failure and `ChatRetentionJob` (one tick, never raises, not started under testing).

With a fake service (it records the question, the history and the search text it was given) and the store on your
test database:

- **Refused** (FU-26, the `pmvp_pages` helpers): signed out, each new address 302 to sign-in; switch off, 404;
  without personal accounts, 404; each new form post without the field, with a wrong one, or with the header only:
  400 before any read or service call.
- **Wrong owner**: Alice's client against Bob's chat on `GET`, the follow-up, delete and the click route: the same
  404 as a random uuid and as `not-a-uuid`; Bob's chat unchanged (message and reference counts); the service never
  called.
- **A question**: one chat with two messages; 303 to its page; the page shows the statements, chips and quotes as
  22a's tests expect them; the title cut at a word to 200 characters with «…».
- **A follow-up** (SPEC-05): the search text holds the last question and the follow-up; the history is the earlier
  turns in order, statements only (no quote or passage text in any message the fake receives), the follow-up last;
  32 messages at most.
- **Long chats**: 40 turns: at most 32 messages, each within 50,000 bytes, all within the budget; a history made of
  numbers stays within 8,192 tokens at one token per digit (FU-06); a last answer of 4,000 bytes is sent cut, not
  dropped (FU-07).
- **`busy` and `failed`**, new and follow-up: nothing stored (row counts), the plain message, the question in the
  box.
- **Delete**: messages and references gone (row counts); 303; a second delete answers 404.
- **The list**: newest first; 20 and «Alle Gespräche»; page 2 of `/fragen/verlauf`; another person's chats never
  listed; a title holding `<script>` shown as text; the search page's first `<nav>` still «Suche» alone; a store
  that raises: no list, the page 200.
- **The click route**: readable, then one grant and a redirect to the preview at the page; not readable, 404 and
  nothing granted; a turn or number out of range, 404.
- **FU-01**: a question with a NUL and a page of 2^40 are stored without error; a store that fails on the second
  message: 200 with «nicht gespeichert» and no row left.
- A stored answer that is not the JSON shape: a plain paragraph, no chip.
- **Logs**: with the `web_interface`, `assistant` and `identity` loggers set to DEBUG explicitly (FU-46), no record
  of the whole run holds a question, title, statement, quote, pointer or passage.
- The follow-up's service call runs with the assistant's claim (as 22a's and RB-6's tests show it).

22a's own tests (`tests/test_assistant_page_routes.py`, not frozen) expect `POST /fragen` to answer 200 with the
page. Change those that pin it to follow the `303`, keep what each one checks, and list them in your notes.

## Files you may change

- `P/src/web_interface/assistant_routes.py`, `P/src/web_interface/templates/ask.html`,
  `P/src/web_interface/static/js/ask.js`, `P/src/web_interface/static/css/ask.css`
- `P/src/web_interface/templates/_sidebar.html`: one block after the main `<nav>`, in the Workspace area only;
  `P/src/web_interface/static/css/shell.css`: rules for that block only (the sidebar is on every page, and `ask.css`
  is not)
- `P/src/web_interface/app.py`: the `_CSRF_EXEMPT_ENDPOINTS` entries only
- `P/src/assistant/service.py`: item 5 only; `P/src/assistant/README.md` (the service's API, the known limit on
  follow-ups, FU-06 and FU-07)
- `P/src/identity/chats.py`: `list_chats`'s `limit` and `offset`; `RETENTION_DAYS`, `purge_expired_chats` and
  `ChatRetentionJob` (item 12); nothing else
- `P/tests/test_assistant_chats_*.py` (new); `P/tests/test_assistant_page_routes.py` as said above
- the two cards, their pages, `docs/components/boundaries.yaml`
- `docs/platform-mvp/management/assistant.md`, its row in `management/README.md`, and
  `management/data-protection.md` (the decisions of 22 November are there already: say what was built, and keep it
  true)
- this order's "Builder's notes"

## Files you must not change

The frozen files (`docs/platform-mvp/frozen-tests.txt`; among them **`test_pmvp23_chats_followups_acceptance.py`**,
`test_pmvp21_chats_acceptance.py`,
`test_pmvp20_statements_acceptance.py`, `test_pmvp11_navigation_acceptance.py`, `test_rb67_agents_acceptance.py`),
`P/tests/conftest.py`, every migration, `P/src/assistant/check.py`, `core.py`, `providers.py` (PMVP-24's),
`P/src/knovas_client.py`, `P/src/identity/agents.py`, the parts (`_parts.html`, `parts.css`), `P/src/brain_views/**`
and `brain_views_routes.py` (another builder), `platform_settings.py`. If the work needs one of these, stop and write
the reason under "Builder's notes".

## Done when

1. The frozen `tests/test_pmvp23_chats_followups_acceptance.py` green and unchanged (its blob id); your tests,
   `tests/test_assistant_*.py`, `tests/test_identity_chats_store.py`, `tests/test_shell_*.py` and the frozen files
   `test_pmvp21_chats_acceptance.py`, `test_pmvp20_statements_acceptance.py`,
   `test_pmvp11_navigation_acceptance.py`, `test_rb67_agents_acceptance.py` green; the frozen check all OK. **Do not
   run the whole suite.** Own test database `knovas_platform_test_pmvp23`, one pytest process at a time, dropped at
   the end.
2. In the browser (headless Chrome, the real app, a fake service, a database of its own and not the test one): a
   question, a follow-up, the list, a chat opened from the list, a delete; at 1440 and 390 px nothing scrolls
   sideways. Screenshots beside `01-assistent.png`, outside the repository (say where).
3. Card checks clean (`card check`, `card render --check`, `map-check --scope touched` against
   `origin/feat/platform-mvp`).
4. The management pages updated as listed above; FU-01, FU-02, FU-03, FU-06 and FU-07 named as taken up.
5. Commits `PMVP-23: …`, ending with the co-author line; `feat/pmvp-23` pushed. No pull request.

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)
