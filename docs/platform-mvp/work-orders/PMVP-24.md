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

(the builder writes here: what was decided, what is open, what the verifier should look at)
