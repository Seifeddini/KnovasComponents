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

(the builder writes here)
