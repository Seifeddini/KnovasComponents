# Work order PMVP-24: waiting and failure states of the assistant

**Decided by the coordinator on 2026-11-22** (see "Decisions").

| | |
|---|---|
| Item | PMVP-24 (Jira: filed by the coordinator), plan `FEAT-2026-11-19-platform-mvp` § 7 contract L2-PLATFORM-ASSISTANT **POST-06** ("a failed or slow Core call produces a plain message and no partial answer"); § 8.2 (the Core answers 4 generations at a time with a 120-second limit; "a waiting line and a plain «gerade ausgelastet» message instead of a spinner without end"); § 12 (the Core unstable in the pilot week); spec item SPEC-platform-mvp-07 |
| Follow-ups taken up | **FU-08** (search retries a timeout 3 × 120 s, then generation waits up to 120 s, while nginx and gunicorn stop at 180 s: the person can get a 504 instead of the plain message). Also FU-26, FU-46 |
| Class | **routine** (Jannik or Siran): it bounds time and draws states. It touches no sign-in, role check, chat storage, reference check, secret or deployment file. If the deadline cannot hold without a change to nginx, gunicorn, the Dockerfile or compose (deployment: sensitive), stop and write why |
| Depends on | PMVP-22a (integrated), PMVP-25 (the providers; integrated), **PMVP-23** (the chat page and follow-ups: the states must hold there too) |
| Branch / worktree | `feat/pmvp-24` from `origin/feat/platform-mvp` once PMVP-23 is integrated. If the builder starts earlier: from `origin/feat/pmvp-23`, and merge `origin/feat/platform-mvp` in once 23 is integrated. Worktree `.worktrees/pmvp-24` |
| Visual contract | `docs/platform-mvp/reference-screens/01-assistent.html`. No reference screen draws a waiting or failure state; the words are below |
| Card | `docs/components/L2-platform-assistant.card.yaml` (its page, `boundaries.yaml`) |
| Core requests | None. CR-1 and CR-2 change nothing here. A Core that is down or slow is exactly what this item handles |
| Frozen tests | none (routine); the verifier checks "Done when" |

`P` = `KnovasPlatform/components/docbridge_integration`.

## Goal

Whatever the Core or the language model does, a person who asks gets the answer or one plain sentence within a
bounded time. She never gets half an answer, a spinner without end, or the web server's 504 page. While she waits, a
line says what is happening.

## What to build

1. **One deadline per question** (FU-08). Today's worst case: the search tries 3 times with 120 s each, plus the
   waits between tries; then the generation waits 120 s (provider «Knovas») or 60 s (Claude). nginx
   (`proxy_read_timeout 180s`) and gunicorn (`--timeout 180`) stop at 180 s. The rule from now on:
   - The search with its retries **and** the generation end within `ANSWER_DEADLINE_SECONDS = 150`, counted from the
     route's start.
   - The generation is not started with less than `MIN_GENERATE_SECONDS = 30` left.
   - Each Core attempt's timeout is at most the time left. A retry happens only if time is left for it.

   Where it goes:
   - `P/src/assistant/core.py`: `make_service` takes the deadline; `make_search` and `make_generate` read it.
   - `P/src/assistant/providers.py`: Claude's timeout becomes the smaller of 60 s and the time left. Nothing else
     changes there.
   - `P/src/knovas_client.py`: one way, per thread, to bound the search's attempts and their timeout (a context
     manager like `keeping_snippets()`). It is off by default, so the search page searches exactly as before.
   - The route starts the clock (`assistant_routes.py`).
2. **Slow reads as busy** (decision D1). A timeout of the search or the generation becomes `busy` («Der Knovas
   Assistent ist gerade ausgelastet. Bitte versuchen Sie es in einem Moment noch einmal.»). It is mapped in `core.py`
   (raise `CoreBusy`), so the service stays as it is. Any other failure stays `failed`.
3. **The waiting line** in the browser. When a question is sent (the search page's box on «Fragen», and the chat's
   box from PMVP-23):
   - the button is disabled and the box keeps the question;
   - a line under the box (`role="status"`, `aria-live="polite"`) says «Der Knovas Assistent sucht in Ihren Quellen
     und prüft die Antwort …»;
   - after 20 s it says «Das dauert länger als sonst. Die Antwort kommt, sobald sie geprüft ist.»;
   - after 170 s, past the server's deadline, it says «Keine Antwort erhalten. Bitte versuchen Sie es noch einmal.»,
     and the button works again.

   The two times are read from `data-` attributes on the form, so a test can shorten them. There is no spinner
   without end. A quiet pulse is allowed if `prefers-reduced-motion` stops it. On `pageshow` (back and forward in the
   browser) the box is reset. Without JavaScript, the browser shows its own loading, and the server's deadline still
   holds.
4. **One question at a time per box.** A second Enter or click while waiting sends nothing.
5. **Every state, on both pages** (the search page's box, and PMVP-23's chat page):
   - a new question that is `busy` or `failed`: the plain message, the question back in the box, nothing stored;
   - a follow-up that is `busy` or `failed`: the chat as it was, the message under the last turn, the question back
     in the box, nothing stored;
   - a chat page whose chat cannot be read (the database is down): a plain page «Das Gespräch kann gerade nicht
     geladen werden. Bitte versuchen Sie es in einem Moment noch einmal.» with status 503, not a 500;
   - the expired form stays 22a's 400.

   PMVP-23 already covers a failed write. This item covers the failed read and checks the whole matrix.
6. **Status codes** stay as 22a set them (200 for `busy` and `failed` on the page), apart from the new 503 above.
7. **The card.** The evidence of POST-06's lines covers the deadline, "slow reads as busy" and the waiting line. The
   `review` entry that names FU-08 is replaced by that evidence. `assistant/README.md`'s table of knobs gains the
   two constants.

## Rules

1. **No partial answer.** The generation is called once, and its whole result is checked; nothing streams. Every
   timeout is within the deadline, so a reply can never arrive after the page has given up.
2. **No retry towards the language model** (the coordinator, 21 November). The Core's generation is never retried,
   as today.
3. **The search page is unchanged.** Its searches keep `http_read_timeout` and their three tries; a test shows it.
4. **Deployment files stay as they are**: nginx, gunicorn, the Dockerfile and compose. If 150 s cannot hold without
   them, stop.
5. **No customer text in logs.** The deadline's log line carries durations and which step ran out of time.
6. The constants live in `core.py` and are documented. No new setting.
7. `ask.js` writes no markup from text: only attributes, classes, `hidden`, and the fixed texts above.

## Decisions (coordinator, 2026-11-22)

- **D1, slow reads as «ausgelastet»:** a timeout means the Core or the provider is overloaded far more often than
  broken, and plan § 8.2 names that word. The alternative is a third text («hat zu lange gebraucht»).
- **D2, 150 s for the whole answer and 30 s at least for the generation**, set before the capacity test in the week
  of 1 December (§ 11). **Decided: the 150 s deadline holds**; the coordinator revisits both numbers after that test.
- **D3, no queue in the Platform.** The Core's 429 and 503, and Claude's 429 and 529, read as «ausgelastet» at once,
  as PMVP-25 decided. The plan's "waiting line" is read as a line of text, not a queue.

## Your tests (`P/tests/test_assistant_waiting_*.py`)

Use a fake clock (patch the monotonic time); no test waits for real:

- A search that times out on every attempt: `busy` within the deadline; no more attempts than fit; the generation
  not called.
- A search that takes 125 s: the generation not called (25 s left); `busy`.
- A search that takes 10 s: the generation's timeout is 120 s for «Knovas» and 60 s for Claude, never more than the
  time left.
- A generation that times out: `busy`. Nothing stored, for a new question and for a follow-up. The page shows only
  the message: no statement, chip, quote or passage.
- 429, 503 and 529 give `busy` at once; another HTTP error gives `failed`.
- The search page's own search still tries 3 times with `http_read_timeout`.
- A chat whose read raises: 503 with the plain page; no traceback text in the body.
- Static checks of `ask.js` and the templates: the three texts; `role="status"`; the button disabled on sending;
  the reset on `pageshow`; the two times read from `data-` attributes.
- In the browser (headless Chrome, as `tests/test_assistant_page_layout.py` does; skipped where no Chrome is
  installed), with a fake service that takes 2 s and the times shortened to 1 s and 3 s: the line appears; the
  button is disabled; the second text follows; the answer replaces the page; a second click sends nothing.
- **Logs**: with the `assistant`, `web_interface` and `knovas_client` loggers at DEBUG (FU-46), only durations and
  step names.

## Files you may change

- `P/src/assistant/core.py`; `P/src/assistant/providers.py` (Claude's timeout only); `P/src/assistant/README.md`
- `P/src/knovas_client.py`: the per-thread bound of the search's attempts only
- `P/src/web_interface/assistant_routes.py`, `templates/ask.html`, `static/js/ask.js`, `static/css/ask.css`;
  `templates/index.html` (the waiting line under the box, in the box's form only)
- `P/tests/test_assistant_waiting_*.py` (new)
- the card, its page, `docs/components/boundaries.yaml`
- `docs/platform-mvp/management/assistant.md` and its row in `management/README.md`
- this order's "Builder's notes"

## Files you must not change

The frozen files (PMVP-23's `test_pmvp23_chats_followups_acceptance.py` among them), `P/tests/conftest.py`, `P/src/assistant/service.py` and `check.py` (if the service must change,
stop and say why), `P/src/identity/**`, every migration, `_sidebar.html`, `app.py`, the parts, the nginx files, the
`Dockerfile`, compose files, `P/src/brain_views/**`.

## Done when

1. Your tests green; `tests/test_assistant_*.py`, `tests/test_knovas_client_*.py`, the search page's tests
   (`test_frontend_static.py`, `test_web_search_doc_fields.py`, `test_search_exact_match.py`,
   `test_web_open_paths.py`, `test_doc_fields_render.py`) and the frozen files `test_pmvp20_statements_acceptance.py`
   and `test_rb67_agents_acceptance.py` green, apart from ids in `docs/platform-mvp/baseline-failing-ids.txt`; the
   frozen check all OK. **Do not run the whole suite.** Own test database `knovas_platform_test_pmvp24`.
2. Screenshots, outside the repository (say where), at 1440 and 390 px: the waiting line (both texts), `busy` on a
   new question, and `busy` on a follow-up.
3. Card checks clean.
4. `assistant.md`: the states a person sees; FU-08 named as taken up.
5. Commits `PMVP-24: …`, ending with the co-author line; `feat/pmvp-24` pushed. No pull request.

## Builder's notes

### Builder A, 2026-11-22 (branch `feat/pmvp-24` from `origin/feat/pmvp-23` `6cbcacdd`; PMVP-23 is not integrated yet)

**What was built**, in the files the order allows:

- `assistant/core.py`: `ANSWER_DEADLINE_SECONDS = 150`, `MIN_GENERATE_SECONDS = 30`, `Deadline` (its clock is
  `time.monotonic`, a test passes its own); `make_service(..., deadline=None)` hands it to `make_search` and the
  generate. `make_search` runs the whole search (the document-fields plan and the query) inside
  `client.within_time(deadline.left)`; a timeout there (requests' own, or tenacity giving up after attempts that each
  timed out) raises `CoreBusy("search_timeout")`. `make_generate` runs `llm_generate` inside the same bound, so its
  120 s are at most the time left; `in_time` wraps either generate (the Core's or Claude's): not started with less
  than 30 s left (`CoreBusy("no_time_to_generate")`), and a `CoreFailed` whose reason is a timeout (`timeout`,
  `anthropic_timeout`) becomes `CoreBusy("generation_timeout")`. Every mapped error is raised outside its handler,
  so the timeout is neither its cause nor its context. One log line per deadline: `step=`, `elapsed_ms=`,
  `left_ms=`, `generate_ms=`.
- `assistant/providers.py` (Claude's timeout only): `make_anthropic_generate(..., time_left=None)`; the client
  waits `min(60, time_left())` (at least 1 s), else 60 as before.
- `knovas_client.py` (the per-thread bound only): `within_time(time_left)`, a context manager like
  `keeping_snippets()`, off by default. Inside it every request's timeout is at most the time left (in `_send`,
  which every request passes), with no time left nothing is sent (a `ReadTimeout` at once), and `_make_request`'s
  tenacity stop gains `_out_of_time`: a retry only when at least `MIN_ATTEMPT_SECONDS` (5) remain after its wait.
  Outside the block nothing changes: three tries of `http_read_timeout` (a test shows it).
- `assistant_routes.py`: the route starts the clock (`ask` and `follow_up`, first line: `g.assistant_deadline`),
  and the service factory builds the service with it. A chat that cannot be read or deleted (anything but
  `ChatNotFound` from the store) is the plain page «Das Gespräch kann gerade nicht geladen werden. …» with 503, the
  error's type logged; on the chat page, the follow-up, the delete and the click route.
- `ask.html`, `index.html`, `ask.js`, `ask.css`: the waiting line. An empty `<p class="ask-wait" role="status"
  aria-live="polite">` under each box (on the search page inside the box's form, only with the assistant on; on the
  ask page right after the box's form); `ask.js` writes the three fixed texts into it as text, disables the button,
  keeps the question, ignores a second submit while waiting, gives up at 170 s and resets on `pageshow`. A small
  dot before the text pulses quietly, and stops with `prefers-reduced-motion`.
- The card, its page; `assistant/README.md` (the knobs table gains the constants); `management/assistant.md`, its
  row in `management/README.md`.

**Nothing of the deployment changed** (nginx, gunicorn, Dockerfile, compose): 150 s hold without them. The service
(`service.py`) and `check.py` are unchanged; `_sidebar.html`, `app.py` and `identity/**` too.

**Decisions taken here (the verifier may disagree):**

1. **The two times are `data-` attributes of the status line, not of the form** (the order says "on the form").
   Reason: PMVP-22a's `test_the_page_offers_the_box_for_a_new_question` reads the chat box's start tag exactly
   (`<form class="ask-again" method="post" action="…">`), and that test is not this item's to change. The line sits
   in the search page's form and right after the ask page's form; `ask.js` finds it either way. The browser test
   shortens them there.
2. **The texts are written by `ask.js`** (allowed: "the fixed texts above"), not held in the page: 22a's busy test
   asserts that a busy page holds no «Quellen», which the first text contains.
3. **A retry needs 5 s** (`MIN_ATTEMPT_SECONDS`) after its wait. With the default read timeout of 120 s the worst
   search is 120 s, a wait of 2 s and a second try of 28 s: busy at 150 s, no third try.
4. **The bound covers the whole search step**, the document-fields plan included (it can call the Core), and the
   Core's generation; Claude's timeout is set by its own argument, as the order says.
5. **The deadline object is handed through `g`**, because the service factory is called without arguments (tests
   replace it with `lambda: fake`).
6. **A quiet pulse** on a dot, not on the text: pulsing the text took the muted text well below its 4.5 to 1 at the low point.

**Tests.** New: `test_assistant_waiting_deadline.py` (27; a fake clock moved by the fake Core and by tenacity's
sleep, nothing waits for real) and `test_assistant_waiting_pages.py` (15; the database, the static checks and the
browser). The order's groups in one process (`tests/test_assistant_*.py`, `tests/test_knovas_client_*.py`, `test_frontend_static.py`, `test_web_search_doc_fields.py`, `test_search_exact_match.py`, `test_web_open_paths.py`, `test_doc_fields_render.py`, the frozen `test_pmvp20_statements_acceptance.py` and `test_rb67_agents_acceptance.py`, and PMVP-23's frozen file as well): 1,636 passed, 0 failed, 0 skipped (nothing needed the baseline), on the own database `knovas_platform_test_pmvp24` (dropped after); the stylesheet checks once more after the last change to `ask.css` (`test_assistant_page_static.py`, `test_assistant_page_layout.py`, `test_shell_nav_styles.py`, the two style tests of this item): green. Frozen check: all 11 OK. ASCII check `origin/feat/pmvp-23..HEAD`: 0 lines. No existing test changed.

**Screenshots** (headless Chrome over the DevTools protocol, the real app with identity on, a fake service, the
database `knovas_platform_pmvp24_browser`, dropped after), outside the repository, in
`C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\pmvp24\shots\`:
`wait1-1440.png`, `wait1-390.png` (the first text), `wait2-1440.png`, `wait2-390.png` (the second, the slow time
shortened to 1 s), `chatwait-*.png` (the chat's box), `busy-*.png` (a busy new question), `busyfollow-*.png` (a busy
follow-up), `facts.json`. For the two waiting shots the request is held back by a listener that runs after
`ask.js`'s own (a real request would replace the page while it is photographed); the real request is in
`test_in_the_browser_the_line_follows_the_question_and_a_second_click_sends_nothing`. Nothing scrolled sideways at
either width; no request failed or left the installation. The tools (`serve.py`, `shoot.py`, `cdp.py`) are one
folder up.

**Card checks**: `card check`: valid, 0 errors, 0 warnings; `card render --check`: 21 cards, 0 not current. `map-check --scope touched --settings docs/components/map-check.yaml`, base `origin/feat/pmvp-23`, head `444a76c8`: problems 0; declared equals observed, compatible or breaking and complete: 0 findings in the scope (POST-06 kept its statement and gained evidence; `http.GET.fragen._chat_id/P-3` is a new line); `over_budget` on the assistant's card (66,503 tokens, budget 16,000). Touched without a card: `knovas_client.py` and `index.html` (the web app).

**For the verifier.**

- The browser test reads the page's own record (`sessionStorage`), because DevTools holds a call while a form's
  navigation is pending; the second click and submit are made by the page itself 1.2 s after the first.
- The capacity test in the week of 1 December may move 150 and 30 (D2); both are constants in `core.py`.
- PMVP-20 context was not on `origin/feat/platform-mvp`; nothing of it is merged here. When it lands, its change
  to `keeping_snippets` sits next to `within_time` in `knovas_client.py`.

### Rework 1, 2026-11-22 (after the first verification's FAIL)

`origin/feat/platform-mvp` merged first (PMVP-23 integrated, `eae92f1d`); one conflict, in the PMVP-23/24 row of
`management/README.md` (platform-mvp's wording kept, this item's state updated).

**Finding 1 (HIGH): an API the pinned tenacity lacks.** `_out_of_time` read `retry_state.upcoming_sleep`, which
tenacity 8.2.3 (`requirements.txt`, CI and the image) does not have: inside `within_time` the first timeout raised
`AttributeError`, a slow search answered `failed` instead of busy and lost its retries. Fixed: `_upcoming_wait` asks
the retry's own wait strategy (`retry_state.retry_object.wait(retry_state)`), which gives the wait either version is
about to make. `next_action.sleep` was not used: 8.2.3 asks `stop` before it computes the wait, so there it holds the
previous attempt's wait. The rest of the change checked for API that 8.2.3 or the pinned set lacks: `stop_any`
through `|`, `RetryError.last_attempt`, the `sleep` and `wait` of `_make_request.retry` exist in 8.2.3; the tests
imported `httpx`, which the pinned set does not install (Anthropic's SDK there uses `httpx2`), and now import
`httpx2`.

**Finding 2 (LOW): the deadline by the wall clock.** `assistant.core.call_within(seconds, call, on_overrun)` runs a
call in a daemon worker thread and waits for it no longer than `seconds`; past them it asks `on_overrun` to cut the
call short and raises `OutOfTime`. Inside `within_time` every request of the Knovas client goes through it
(`_send_in_time`): `stream=True`, the body read with `read1` in the pieces the peer sends and the time checked after
each, the caller waiting no longer than the request's read timeout or the time left. Past it the caller gets a
`ReadTimeout` (busy; the search makes no retry without time for it) and the worker stops at the peer's next byte and
closes the connection. Claude's call, with the answer's deadline, goes through it as well (`min(60 s, time left)`;
past it the client is closed and the answer fails as `anthropic_timeout`, which the deadline reads as busy). PMVP-20
context's helper is not on `feat/platform-mvp` (its branch is not merged), so this is the only one; when it lands,
one of the two should go (for the coordinator).

Left open: a peer that trickles even its headers keeps the worker thread, never the person, until it stops or the
connection drops; it is a daemon thread and holds one connection. With a trickled body the worker stops right after
the deadline (tested: the server sees the connection dropped).

New: `tests/test_assistant_waiting_trickle.py` (8): the search, the Core's generation and Claude through Anthropic's
real SDK, each against a real socket server on 127.0.0.1 that trickles its body or its headers (a byte every 0.2 s,
a deadline of 2 s); each gives up by the deadline and makes one request; the control shows the same search outside
the bound still waiting. `test_outside_the_bound_the_stop_never_cuts_a_retry` now hands the stop the state both
tenacity versions hand it.

**Under the pinned versions.** A venv in the scratchpad (`pmvp24\venv-pinned`), `pip install -r
requirements.txt` after CI's knovas-extract pin (git `2c95cbc0`): tenacity 8.2.3, requests 2.32.0, urllib3 2.8.0,
anthropic 2.12.0 with httpx2 2.16.2, Flask 3.0.0, pytest 7.4.3, on Python 3.13 (the only Python on this machine; CI
uses 3.11). The frozen check: all 13 OK. In one process, the frozen PMVP-23, 20, 21, 11 and RB-67 files, `tests/test_assistant_*.py`, `tests/test_web_*.py` and `tests/test_shell_*.py`: 2,744 tests, 0 failed, 0 errors, 1 skipped, on the own database `knovas_platform_test_pmvp24`. The browser test needs `websockets`, which `requirements.txt` does not pin: it skips in the
pinned venv and passes in the development environment.
