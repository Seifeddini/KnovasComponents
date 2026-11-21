# Work order PMVP-22a: the ask page (one box; the answer with its sources)

| | |
|---|---|
| Item | PMVP-22a (Jira SS-734), plan `FEAT-2026-11-19-platform-mvp` W-01, W-03; contract L2-PLATFORM-ASSISTANT POST-01, POST-05, POST-06. The first half of W-04 (the quote under the statement) is pulled in from PMVP-22b, because the new design carries the quote in the reply |
| Class | routine |
| Branch / worktree | `feat/pmvp-22a` from **`origin/feat/pmvp-11`** (navigation), worktree `.worktrees/pmvp-22a`; merge `origin/feat/platform-mvp` in once (it holds the page parts and the assistant service, merge `49d4763`) |
| Visual contract | `docs/platform-mvp/reference-screens/01-assistent.html` and `out/01-assistent.png` |
| Card | `docs/components/L2-platform-assistant.card.yaml` (the page's operation) |

## Goal

A person types into one box. A question gets an answer made of statements, each with its source number; a click on
the number shows the quote from the passage right under the statement. A few keywords get the search results as
today. The person can switch between asking and searching.

## What to build

1. **One box (W-01)** on the search page: a switch "Fragen / Suchen" next to the box, defaulting to "Fragen" when
   the assistant is on and to "Suchen" when it is off or the input is one to three words without a question mark
   (the person's switch wins). Searching works exactly as today.
2. **The answer page.** `POST /fragen` (CSRF like the other forms; a route file of its own,
   `src/web_interface/assistant_routes.py`, blueprint, one wiring line in `app.py`), Workspace area. It calls the
   assistant service (`src/assistant/`) once for the signed-in person (the service searches as the person; read how
   `assistant/core.py` builds it) and renders `ask.html` from the page parts:
   - the question as the page head;
   - `answered`: each statement as a paragraph with its `ref_chip(number, controls=…)`; the chip opens the quote
     **under that statement** (`card`, the document's title and page, the quote in quotation marks, a link that
     opens the document the way a search hit opens it); then the list of passages (number, title, page);
   - `no_answer`: "Dazu finde ich nichts in Ihren Quellen" and the closest passages;
   - `busy` / `failed`: the plain message, nothing else (POST-06);
   - a small line with `removed` when it is not 0 ("2 Aussagen ohne Beleg ausgelassen"), never their text.
3. **Plain text only** (FU-11 and the hardening's warning): statement, quote and title are shown as text, escaped;
   no Markdown, no HTML, no links from the model. A statement holding `<script>`, `**bold**` or `[x](http://…)` comes
   out as those characters.
4. **The switch.** With the assistant off, `/fragen` answers 404 and the box offers only searching. Read the switch
   through one small function in `assistant_routes.py`. The setting `assistant.enabled` arrives with PMVP-25
   (`feat/pmvp-25`, being built: it adds the settings and chooses the provider in `assistant/core.py`'s factory).
   Until it is on your branch, the function returns off unless `ASSISTANT_ENABLED=true` is set in the environment;
   when `origin/feat/pmvp-25` has been pushed with the factory, merge it and use it, and say so in your notes.
5. **The chip** is a button that opens its quote without leaving the page; with JavaScript off, every quote is shown
   under its statement. The chip must not break away from its full stop at 390 px (FU-35).
6. **Hrefs only from `url_for`** (FU-34). No customer text in a log line: the route logs the status and counts only.
7. **The card**: the operation `POST /fragen`, its lines (POST-01, POST-05, POST-06 with your tests as evidence),
   source paths, `boundaries.yaml`; checks as in `work-orders/PMVP-01.md`.

## Your tests (`tests/test_assistant_page_*.py`)

With a fake service: signed out 302; without the CSRF token refused; switch off 404; each of the four statuses
renders as described; escaping of statement, quote and title; the chip's `aria-controls` points at its quote; the
search side of the box unchanged (existing search tests stay green); no customer text in the logs.

## Files you may change

`src/web_interface/assistant_routes.py` (new), `templates/ask.html` (new), the search page's template (the box and
the switch only), one wiring line in `app.py`, `static/css/ask.css` (new, tokens only) and a small `static/js/ask.js`
if needed, your tests, the card, its page, `boundaries.yaml`, the notes below.

## Files you must not change

The frozen files, `conftest.py`, `src/assistant/**` (the service; if it lacks something the page needs, stop and say
what), the parts, the sidebar, other routes.

## Done when

1. Your tests green; `tests/test_pmvp20_statements_acceptance.py`, `tests/test_pmvp11_navigation_acceptance.py`,
   the search page's tests and `tests/test_shell_*.py` green; the frozen check all OK. **Do not run the whole
   suite.** Own test database `knovas_platform_test_pmvp22a`.
2. Screenshots of an answer (with one quote open), a no-answer and a busy reply at 1440 and 390 px beside
   `01-assistent.png`, outside the repository.
3. Card checks clean. Commits `PMVP-22a: …` with the co-author line; `feat/pmvp-22a` pushed. No pull request.

## Builder's notes

Builder session of 2026-11-21 (AGENT-claude-code). `feat/pmvp-22a` from `origin/feat/pmvp-11` (`4b7791b`, which has
not moved since); `origin/feat/platform-mvp` (`5b0175b`) merged once (`f631586`; the conflicts in `style.css` and the
shell's card resolved exactly as PMVP-40's `1c6c7f2` resolved the same two sides); `origin/feat/pmvp-25` (`9183281`)
merged as soon as its factory was pushed (`33e0440`). Nothing here is verified; that is the verifier's.

### What was built

| File | What |
|---|---|
| `src/web_interface/assistant_routes.py` (new) | `POST /fragen` (blueprint `assistant`), `assistant_enabled()` (the switch), `shown_reply()` (what the page draws, the hrefs built here with `url_for`), `split_tail()`, `install_assistant()` (the one call app.py makes) |
| `templates/ask.html` (new) | the page from the parts: `page`, `page_head` (the question), `ref_chip`, `card`, `section`; the box for a new question |
| `static/css/ask.css` (new) | tokens only; the statement, the quote card's edge, the passages, the box, the switch |
| `static/js/ask.js` (new) | the switch in the search page's box; the chips on the ask page |
| `templates/index.html` | the box and the switch only, and only with the assistant on; off, the box has the same elements and attributes as before |
| `app.py` | the wiring statement (`install_assistant(...)`) and **one more line**: `'assistant.ask'` in `_CSRF_EXEMPT_ENDPOINTS` (decision 1) |
| `tests/test_assistant_page_routes.py` (71 with the static file), `tests/test_assistant_page_static.py` | below |
| the assistant's card, its page, `boundaries.yaml` | R-3, the operations `http.POST.fragen` and `install_assistant` with their lines, evidence for `*/N-1`; the four files in `source_paths` |

### How the switch is read

`assistant_enabled(settings=None)` in `assistant_routes.py` is the one place: PMVP-25's setting `assistant.enabled`
through the settings' Resolver (`app.extensions["platform_settings"]`), on every request; `ASSISTANT_ENABLED` in the
environment pins it, else the settings page's value, else off. A Resolver that is missing or raises counts as off. The
page's service is PMVP-25's `make_service` on the same Resolver, built per request; if it says `OFF` the route answers
404 too. The first commit (`49392aa`) read `ASSISTANT_ENABLED` directly, as the order said until PMVP-25 was in;
`4667333` replaced that with the setting.

### How the chip opens the quote

Each statement's chip is `parts.ref_chip(n, active=True, controls="zitat-<i>")`, a `<button type="button">` whose
`aria-controls` names the `<div class="ask-quote" id="zitat-<i>">` (a `card`) right under the statement. **Without
JavaScript** every quote stands open and every chip says `aria-expanded="true"`. **With JavaScript** `ask.js` closes
them all when the page loads (`hidden`, `aria-expanded="false"`, no `k-ref--on`), and a click opens or closes the
chip's own quote in place. The last word, the chip and the full stop are one `white-space: nowrap` span (`ask-tail`,
FU-35); in the browser at 390 px each of them is one line box.

### What I decided, and why (the verifier should look here first)

1. **The form's token, and a second line in app.py.** The order says "CSRF like the other forms". The app's form
   posts (login, logout, account password, the console) carry a hidden `csrf_token` field that the handler checks,
   and the app's header gate exempts them, because a form cannot send the `X-CSRF-Token` header. The ask box must
   work without JavaScript (the order's "with JavaScript off" clause), so it is a form, and `assistant.ask` joins
   `_CSRF_EXEMPT_ENDPOINTS` with a comment; the route refuses a missing or wrong field with the form's usual 400
   ("Formular ist abgelaufen") before anything is asked. The header alone does not stand in for the field (a test).
   That is two places in app.py, not one; the alternative (reordering the app's `before_request` functions from the
   blueprint) would hide the exemption.
2. **The box.** With the assistant on, the search section is a form posting to `url_for('assistant.ask')`; the switch
   (two radios, "Fragen" checked) is its own row above the unchanged box, so the box keeps its shape. The search
   button stays (app.js binds it) as `type="button"` and a new submit button "Fragen" joins it; `ask.js` shows the
   switch and the one button of the current mode. Without JavaScript the switch stays hidden and the box asks: the
   search needs app.js anyway. While asking, `ask.js` stops Enter on the form (capture) before it reaches app.js's
   handler on the field, and the browser sends the form; while searching, the form is not sent and app.js searches.
   The switch follows what is typed (one to three words without "?" → Suchen, else Fragen) until the person clicks
   it; then the person's choice holds.
3. **Opening the document** "the way a search hit opens it", without a script: a PDF with the inline preview on
   opens in the browser's viewer through the preview route, `s=` the passage's sentence (so it is marked) and
   `#page=` its page; anything else, or a PDF with the preview off, through the download route. Both in a new tab
   (`target="_blank" rel="noopener"`), so the answer, a POST result, is not lost. The search page's preview dialog
   is app.js's and is not on this page. `q=` is left out: it would put the question into the address and the access
   log. The pointer is also the `path` (the file routes resolve both to one file). Before the page renders, the
   route grants the reply's passages to the person (`_grant_for_current_user`), as `/api/search` grants its hits; a
   test shows the wall refusing before the question and letting the file route answer after.
4. **All passages are listed** under the answer ("Quellen"), in search order with their numbers, not only the cited
   ones: the order says "the list of passages", and the numbers must match the chips. No answer lists them as "Die
   nächsten Stellen".
5. **busy / failed** show the message and nothing else, even if a reply wrongly carried statements or passages
   (a test). A question over `MAX_QUESTION_BYTES` is refused before the service with the service's own text, and is
   not repeated as the page head. An empty question goes back to the search (303).
6. **The page head is the question.** The page is in the Workspace (`data-area="work"`); the navigation marks no
   entry as current (`active_nav="fragen"`): the ask page is not the search page.
7. **The quote card** is the parts' `card`; `ask.css` gives it the reference's accent edge and the sunken background
   (`.ask-quote .k-card`). Its head is the title in the mono face and "Seite n".
8. **Left for later**, as the reference shows them: "Wie kam die Antwort zustande?", the dotted name that opens a
   dossier, and follow-up questions (history, past chats: PMVP-22b, PMVP-23). The box under the answer asks a new
   question on its own ("Etwas Neues fragen …"), not "Nachfragen".

### Checks

- **Tests** (own database `knovas_platform_test_pmvp22a`, dropped at the end): `tests/test_assistant_page_routes.py`
  and `tests/test_assistant_page_static.py`, 71 passed. With `test_pmvp20_statements_acceptance.py`,
  `test_pmvp11_navigation_acceptance.py`, the search page's tests (`test_frontend_static.py`,
  `test_web_search_doc_fields.py`, `test_search_exact_match.py`, `test_web_open_paths.py`, `test_doc_fields_render.py`),
  `test_shell_*.py`, `test_assistant_*.py` and `test_settings_assistant.py`: **1,257 tests, 1,252 passed, 5 failed,
  none of them this item's**:
  - `test_shell_nav_styles.py::test_style_css_loads_the_tokens_first_and_then_the_navigation` (PMVP-11) and
    `test_shell_parts_css.py::test_the_base_stylesheet_loads_the_parts_right_after_the_tokens` (PMVP-12) contradict
    each other once both items are merged: `style.css` imports the tokens, `parts.css` and `shell.css`, and each test
    pins a list of two. The same holds on PMVP-40's merge (`1c6c7f2`). One of them has to change; not this item's
    files.
  - `test_frontend_static.py::TestEntityNamesWithASemicolon` (2) and `::TestSearchNotices::test_each_kind_reads_in_german`:
    node's output read in the Windows code page (`M�ller`); the four pass with `PYTHONUTF8=1`. This machine only.
- **Frozen check**: the five files of `frozen-tests.txt`, all OK. `conftest.py` and `src/assistant/**` unchanged by
  this item's commits (the `src/assistant` differences against `feat/platform-mvp` are PMVP-25's merge).
- **ASCII**: the new `.py` files hold no byte over 0x7F.
- **Cards** (from `KnowledgeBase-worktrees/pmvp-01/scripts`, read only): `card check` 16 cards valid, 0 errors, 0
  warnings; `card render --check` 0 not current. `map-check --scope touched`, base `origin/feat/platform-mvp`
  (`5b0175b`), head `b9e3b75`: problems 0. In the scope: `need_added` on `http.POST.fragen/N-1` and `/N-2` (a new
  operation's needs) and PMVP-25's two; `over_budget` on the assistant's card (33,450 tokens; it was 28,547 with
  PMVP-25) and on L2-PLATFORM-ADMIN; `no_contract` on three blueprint factories of the shell and the console (not
  this item's). Touched without a card: `app.py`, `index.html` (the web app) and `platform_settings.py` (PMVP-25).
- **In the browser** (headless Chrome, the real app with identity on, a fake service returning fixed replies, no
  model and no Knovas called): typed "Mietvertrag Seefeldstrasse" switches the box to Suchen, " kündigen?" back to
  Fragen; Enter in the box sends the question; the chips start closed and the first click opens exactly its quote
  (`aria-expanded` true, the others false); with scripts off the box still asks and all three quotes stand open;
  nothing scrolls sideways at 1440 or 390; no request failed or left the installation.
- **Screenshots**, outside the repository, in
  `C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\pmvp22a\shots\`:
  `answer-1440.png`, `answer-390.png` (the first quote opened by a click), `compare-answer-1440.png`,
  `compare-answer-390.png` (beside `01-assistent.png`), `noanswer-*.png`, `busy-*.png`, `answer-nojs-*.png`,
  `box-*.png`, `box-terms-*.png`, `box-nojs-*.png`; `facts.json`. The tools (`serve.py`, `shoot.py`) are one folder up.

### Open, for the coordinator

- The two contradicting import tests above (PMVP-11 against PMVP-12).
- Running the screenshot server on the test database while pytest ran broke every identity test (`type "citext"
  does not exist`): the server's schema took the extension. A builder who shows the app needs a database of its own
  for it, not the test one.
- `make_search` fails closed outside the secured API (mTLS); on a machine without it the page shows the plain
  failure for every question (a test pins this).
