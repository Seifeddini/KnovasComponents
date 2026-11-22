# Work order RB-FIX-2: the verifier's notes on RB-FIX-1

| | |
|---|---|
| Item | RB-FIX-2, the notes of RB-FIX-1's verification (passed with notes; merged into `feat/platform-mvp` at `d655226f`: log records with customer text 859 on base, 0 after) |
| Context | `work-orders/RB-FIX-1.md` (its builder's notes and open points); `management/data-protection.md` |
| Class | **sensitive** (logging and what pages show of the Core's errors). No migration; nothing is written to any tenant |
| Branch / worktree | `maint/rb-fix-2` from `origin/feat/platform-mvp` (`d655226f`), worktree `.worktrees/rb-fix-2` |

`P` = `KnovasPlatform/components/docbridge_integration`.

## What to fix

1. **Medium (also on base): a connection error's text still carries the URL, query string included**
   (`knovas_client.py` ~1832, ~1884, ~1918). Repro: `graph_nodes(q=NAME)` against a closed port; the traceback shows
   `q=Anna+Zwiebelmeier`, and `app.py` ~2854-3122 log such tracebacks.
   - Raise the client's own errors with the route template only, chained without the original message, or with a
     sanitized cause, so the routes that log `exc_info` can no longer print a query or path ids.
   - Check every `logger.exception` / `exc_info=True` in `app.py` that wraps a client call.
2. **Medium: `GraphError` and `DocFieldsError` messages carry the Core's error text** (~1069), and the pages may show or
   log them. Carry the Core's short error code and the status, not its text; where a page shows a message, it uses its
   own German words. Correct `management/data-protection.md:51`.
3. **Low: urllib3's WARNING on a bad answer header logs the full URL.** A logging filter on the `urllib3` logger that
   drops or redacts URLs, or raise it to ERROR if nothing useful is lost; say which and why.
4. **Low: operators lose the Core's message text.** Keep the short code and the request id, and add a line to the
   management page on how an operator finds the Core's full message: by request id in the Core's own logs.

Tests for each point, including a test that a traceback logged by `app.py` for a failed client call contains no query
value and no name.

## Rules

- No frozen file edited; no migration; nothing written to a tenant; don't run the whole suite.
- Own test database `knovas_platform_test_rbfix2`, one pytest process at a time, dropped at the end.
- Commits `RB-FIX-2: …`, ending with the co-author line. Push `maint/rb-fix-2`. No pull request.

## Done when

- The nine frozen files (`tests/test_pmvp*_acceptance.py`, `tests/test_rb*_acceptance.py`) pass.
- `tests/test_knovas_client_*.py`, `tests/test_assistant_*.py`, `tests/test_web_*.py`, `tests/test_graph*.py` and
  `tests/test_doc_fields*.py` pass, apart from failures on `docs/platform-mvp/baseline-failing-ids.txt`.
- The card checks are clean.

## Builder's notes

Builder session of 2026-11-22. `maint/rb-fix-2` from `origin/feat/platform-mvp` @ `d655226f`. Commits: `d8915aa2`
(code, tests, this order), `a47b9177` (management pages), and the commit of the adapted tests and these notes. No
migration, no card changed; nothing reached a tenant (fakes, one closed local port and a test database only).

### What was done

1. **No answer: the error names the route only.** Every request path of the client (`_make_request`,
   `_request_no_retry`, `_request_quiet`, `graph_get_many`, `graph_facts_batch`) now sends through one method,
   `_send`. A `requests` error for no answer (no connection, a timeout, TLS, an invalid header, ...) is raised again as
   **the same class** with the text `Knovas API <method> <route template>: no answer (<class>)`, **outside the
   handler**, so neither its message nor its chain (`__cause__`, `__context__`) nor its `request` holds the URL, a
   path id or the query string. Keeping the class keeps the retries on `ConnectionError`/`Timeout` and every
   `except Timeout` as they were; a class that cannot be built from a message (a test double) falls back to its
   nearest plain base. The raised `HTTPError` of a refusal is now also raised outside the handler (RB-FIX-1 used
   `from None`, which left the original as the hidden context) and names the request id. The verifier's repro is a
   test: `graph_nodes(q="Anna Zwiebelmeier")` against a closed local port, through the real `requests` session: the
   whole formatted chain (tenacity's `RetryError` and its cause) shows `GET /secured/graph/nodes` and no name.
   **`app.py`:** every `exc_info=True` there was checked. The sixteen Cortex routes (~2895-3163) wrap the graph client:
   their tracebacks now hold only the client's sanitized errors, and the seven that printed the id from the path
   (`entity_id`, `type_id`, `filter_id`) no longer do (`... error (route id not logged)`). The search route's
   `f"Search error: {e}"` logs the class (its traceback stays). **Not client calls, not changed:** the download,
   preview and thumbnail routes (~2170-2390) log a file path (`file_path`, `full_path`) and an OSError's text; they
   read local files, and the log sweep of PR #55 rewrites those lines (FU-25, FU-31).
2. **The client's own errors: status, code, request id.** `GraphError` (and `DocFieldsError`) keep `status`,
   `error_code` (only a short identifier, else None), `request_id` and the whitelisted `details`; their `message` is
   `""` and their text is `Knovas graph call failed: HTTP <status>, error_code <code>, request_id <id>`. The
   constructor still accepts a message (callers and test doubles pass one) and does not keep it. `QueryRejected` and
   `DocFieldsUnavailable` keep a short code only; `QueryRejected` carries the request id. The pages that show a
   refusal already said it in their own words from the code (`doc_fields_view.error_message`,
   `admin_doc_fields.knovas_message`, `doc_fields_routes.filter_refusal` / `_edit_refusal`); a test holds them to it
   with a Core text that names a person. No page showed `GraphError`'s text; `ontology_graph.py` logged it (`%s`),
   which is now the code and the id. `data-protection.md` is corrected: RB-FIX-1 had said the client's errors were
   clean, which held only for a refusal.
3. **urllib3: a filter, not ERROR.** `RedactUrls` on each of urllib3's module loggers (`URLLIB3_LOGGERS`; a filter on
   a logger sees only that logger's own records), installed with the INFO hold in `_hold_quiet_loggers`. It keeps the
   warning and replaces every URL and path in it with `<url>`; an exception, whether an argument of the message or
   the record's own (the unparsed header text of "Failed to parse headers"), is named by its class only. **Why not
   ERROR:** urllib3's warnings are what an operator needs about the connection to Knovas - an answer whose headers do
   not parse, a full pool, a certificate that does not match its host, a retry - and at ERROR they would all be gone;
   none of them needs the address.
4. **Operators find the Core's message by the request id.** The id (`X-Request-ID`, the Core's `trace_id`) is in the
   client's log line for every refusal - also, new, for a refused write without retry (`_request_no_retry`, at INFO:
   its caller logs the warning, and some of these answers are expected) - in the raised `HTTPError`'s text and on
   `GraphError`, `DocFieldsError` and `QueryRejected`. `data-protection.md` says how to use it: look the number up in
   the Knovas Core's own logs.

### Tests

New (all 23 fail on the base; checked by putting the base's `knovas_client.py` and `app.py` back):

- `tests/test_knovas_client_errors.py` (21): the closed-port repro; each request path (`_make_request` with its
  retries, `_request_no_retry`, `_request_quiet` and the probes, the parallel reads and the facts batch, a
  document-fields read) raises an error whose formatted chain holds no name, query, path id or host; a timeout stays a
  `ReadTimeout`/`ConnectTimeout`, an `SSLError` and an `InvalidURL` keep their class; `GraphError`, `DocFieldsError`,
  `QueryRejected`, `DocFieldsUnavailable` carry status, short code and request id, never the Core's text (also not in
  `vars(exc)`), a textual code is dropped, the pages' words hold no Core text; a refused write without retry is logged
  with code and request id; urllib3's header warning and retry warning keep their news without the address, and a
  second install adds no second filter.
- `tests/test_web_client_failure_logs.py` (2): the real app in graph mode with the real client whose transport fails
  as `requests` does (its message quoting `/secured/graph/nodes/Anna%20Zwiebelmeier?q=Anna+Zwiebelmeier`): the
  traceback `app.py` logs for `/api/ontology/entities/<name>` and for `/api/ontology/summary` is still logged and holds
  no name, query value, encoded path id or host; the route's line names no id.

**Existing tests changed** (each pinned what point 2 or point 4 takes away):

- `tests/test_knovas_client_logs.py::test_a_core_failure_logs_no_body_and_raises_an_error_without_the_url` (RB-FIX-1's
  own): the raised message also names the request id, and the original is not the context (`__context__ is None`
  instead of `__suppress_context__`).
- `tests/test_knovas_client_doc_fields.py::TestDocFieldsAnswers::test_errors_carry_code_and_whitelisted_details`:
  `err.message` is `""`, not the Core's text.
- `tests/test_knovas_client_doc_fields.py::TestGraphErrorReadsError::test_message_falls_back_to_error_and_keeps_details`,
  renamed `test_neither_message_nor_error_is_kept_and_details_are`: the Core's `error` text is kept in neither field;
  the code and the details are.
- `tests/test_knovas_client_doc_fields.py::TestGraphErrorReadsError::test_the_constructor_stays_compatible`: the
  constructor still takes a message, and the error keeps `""`.
- `tests/test_knovas_client_secured_api.py::TestGraphError::test_a_422_raises_with_its_error_code`: `message` is `""`
  and `"Max 16"` is not in the error's text.

### Checks

Own database `knovas_platform_test_rbfix2`, `PLATFORM_DB_REQUIRED=true`; each group in one pytest process, one after
the other:

| Group | Result |
|---|---|
| `test_knovas_client_*.py` (7 files) | 308 passed (first run 304 passed, 4 failed: the four assertions listed above, which pinned the Core's text; adapted, then all green) |
| `test_graph*.py` (2 files) | 24 passed, 8 xfailed, 1 xpassed |
| `test_doc_fields*.py` (4 files) | 281 passed |
| `test_assistant_*.py` (10 files) | 905 passed |
| `test_web_*.py` (23 files, the two new ones included) | 979 passed |
| the frozen files (`test_pmvp*_acceptance.py`, `test_rb*_acceptance.py`) | the eight of PMVP-03 to PMVP-21 and RB-1 to RB-7: **420 passed**. `test_rb5_role_data_acceptance.py`: 77 failed: it pins RB-5, which is not built (frozen at `127fe2f5` before its build; its routes answer 404 and its pages have no `<main>`); no other frozen test failed |

- **Frozen check** (header of `frozen-tests.txt`): all nine OK.
- **Cards** (KnowledgeBase-worktrees `pmvp-01/scripts`, `py -3 -m brain_runner`, read only): `card check` on all 20
  cards valid, 0 errors, 0 warnings; `card render --check` 0 not current, 0 problems; `map-check --scope touched`
  (base `origin/feat/platform-mvp`, head `HEAD`): no carded component touched (`knovas_client.py` and `app.py` have
  no card); declared 0, compatible 0, budget 0, complete 0 in scope.

### Open, for the coordinator and the verifier

1. **Tracebacks of other code**: a traceback the Cortex routes log can still carry the text of an exception that is
   not the client's (one the ontology code or the filter engine raises with a value in it). The client's errors are
   clean; the sweep of PR #55 drops these tracebacks altogether (it logs the class only), which is the wider fix.
2. **Flask's own line** for an unhandled exception (`Exception on <request path> [GET]`) names the request's path,
   which can hold an id or a name; this branch has no `log_exception` of its own (PR #55 adds one).
