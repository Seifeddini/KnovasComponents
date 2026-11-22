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

(the builder writes here)
