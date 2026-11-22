# Work order PMVP-20, brain-mcp: «Brain-Werkzeuge», Claude reads the Brain through the Knovas MCP gateway

| | |
|---|---|
| Item | PMVP-20 (Jira SS-732), plan `FEAT-2026-11-19-platform-mvp`; a second answer mode of the Knovas Assistent beside the passages of the person's own search |
| Asked | 2026-11-22, the founder: "I want the LLM with access to the brain mcp to work already"; the MVP **today** |
| Class | **critical** under this repository's CLAUDE.md: it stores and uses a secret, the gateway key of a whole company. Management approves before `main` |
| Branch / worktree | `feat/pmvp-20-brain-mcp` from `origin/feat/platform-mvp`, worktree `.worktrees/pmvp-20m` |
| Frozen tests | none new. `test_pmvp20_statements_acceptance.py` (88), `test_pmvp21_chats_acceptance.py` (13), `test_pmvp23_chats_followups_acceptance.py` (33) and the frozen check stay green and unchanged |
| Not here | `src/assistant/check.py` (unchanged), any migration (the settings registry needs none), the passages pipeline (`assistant.mode = passages` answers exactly as today), any write to a tenant (the gateway is read-only) |

`P` = `KnovasPlatform/components/docbridge_integration`.

## Goal

An administrator chooses the answer mode «Brain-Werkzeuge» (`brain_mcp`) on the settings page and enters the
company's gateway key. Claude then answers by calling the Knovas Brain's MCP tools `search` and `fetch` itself,
through the Claude API's MCP connector, against the deployed gateway. The answer is checked exactly as today: a
statement is shown only if its quote stands word for word in a text the tools returned in that same answer.

## The gateway (read it, do not change it)

KnowledgeBase `origin/master`, `knovas-software/client/knovas_mcp/gateway/` (`app.py`, `search.py`, `fetch.py`,
`auth.py`). Read with `git show`; check nothing out. Facts:

- Two read-only tools; each returns **one JSON text** (`json.dumps(answer)`), errors as an MCP tool error:
  - `search(query, max_results=5)`: `{"notice", "result_count", "results": [{"id": <grant>, "url"?, "title":
    <the Core's pointer>, "page"?, "score"?, "summary"?, "passages": [at most 2 snippets of at most 800
    characters]}]}`, at most 5 results and 6,000 characters; `{"busy": true, ...}` on the Core's 429;
  - `fetch(id)`: `{"id", "title": <pointer>, "text": <at most 20,000 characters>, "metadata": {"page_from",
    "page_to", "part_chars", "total_chars", "next_id", "source", "truncated"}, "notice"}`.
- The `id` is a grant (bound to the key, expiring), never a document id; the `title` is the Core's pointer, the
  same string the Platform's search rows carry as `doc_id`.
- The key: `Authorization: Bearer <key>` or `X-API-Key`. Today a **company key per tenant**: every call is
  `POST /secured/query` or the text route with **no access groups and no principal assertion**, so it reads what a
  caller without groups reads: everything under `disabled`, open documents under `enforcing`. CR-R5/CR-R6
  (`docs/platform-mvp/core-requests.md`) would bind a key to an agent's signed claims; neither exists yet.

## The API (read in the claude-api skill files, not from memory)

- `shared/tool-use-concepts.md`, "MCP Connector (Beta)": the beta `mcp-client-2025-11-20` on
  `client.beta.messages.create(...)`; `mcp_servers=[{"type": "url", "url", "name", "authorization_token"}]`;
  `tools=[{"type": "mcp_toolset", "mcp_server_name"}]`, optional `default_config` (`{"enabled": false}` for allowlist
  mode) and `configs` (per tool).
- `shared/tool-use-concepts.md`, "Stop reasons for server-side tools": `pause_turn`; continue by sending the
  assistant's content back, no extra user message.
- The pinned SDK (`anthropic==2.12.0`) has them: `mcp_servers` on `beta.messages.create`,
  `BetaRequestMCPServerURLDefinitionParam`, `BetaMCPToolsetParam`, the beta string in `AnthropicBetaParam`, and the
  response blocks `BetaMCPToolUseBlock` (`type "mcp_tool_use"`: `id`, `name`, `server_name`, `input`) and
  `BetaMCPToolResultBlock` (`type "mcp_tool_result"`: `tool_use_id`, `is_error`, `content` a string or text
  blocks).

## What to build

1. **Settings**, in the `platform_settings` REGISTRY (PMVP-25), installation-wide, German labels and help:
   - `assistant.mode`: `passages` (today's pipeline, the **default**) or `brain_mcp`; `ASSISTANT_MODE` pins it;
   - `assistant.brain_mcp_url`: default `https://mcp.knovas.ch/mcp`; `ASSISTANT_BRAIN_MCP_URL` pins it;
   - `assistant.brain_mcp_key`: a **secret**, Fernet-encrypted like `assistant.anthropic_api_key`, no pin.

   The settings page shows the mode and, under it, one German warning line: «Brain-Werkzeuge lesen alles, was der
   Schlüssel Ihres Unternehmens sieht, unabhängig von den Rollen der Person. Nur ohne «Ein» verwenden.»
   `brain_mcp` is possible only with provider `anthropic` and the keys set; a save that would leave `brain_mcp`
   without them is refused with the page's own words.
2. **The call**, in the provider module (`src/assistant/providers.py`), when the mode is `brain_mcp`: Claude with
   the MCP connector (the gateway URL, the key as `authorization_token`, the toolset of that server, the beta).
   Today's settings stay: the model, effort `medium`, the server-side fallback, the client's timeout, `max_retries`
   0, the base URL pinned. A **wall-clock deadline** as in PMVP-24's order (the context work's deadline is not
   merged): the answer ends within `ANSWER_DEADLINE_SECONDS` (150) of its start, a request is not started with less
   than `MIN_GENERATE_SECONDS` (30) left, each request's timeout is at most the time left, and a timeout is busy
   (PMVP-24, D1).
3. **The answer stays checked.** The model answers in the same JSON lines (`{"statement","source","quote"}` or
   `{"no_answer": true}`). The passages are the texts the MCP tool results of that same answer returned (the
   search results' passages and the fetched parts), each with its document's pointer and title. `source` names one
   of the returned documents; the existing check (`check.py`, unchanged) runs against those passages: quote word for
   word, numbers, length. What the tools did not return cannot be cited; no tool result is `no_answer`. The system
   instruction says: `search`, then `fetch` for the documents you cite, quote only from tool results.
4. **The page and the chats** stay as they are. A stored chat keeps the cited passages as PMVP-23 does. «Quelle
   öffnen» works where the passage has a document the Platform can open; otherwise the title only.
5. **Logs and data**: no question, answer, passage, key or token in any log line, DEBUG included (FU-46: the
   loggers set explicitly in the test); one log line counts the tool calls and the results (ids and counts only).
   `management/assistant.md` and `management/data-protection.md` say what goes to Anthropic, what the gateway reads,
   and when this mode must stay off.

## Rules

1. **The gateway key** is encrypted at rest (`SecretBox`), never returned to the browser, never in a log line, an
   exception, a page, the audit log (the registry writes `geändert`), a test's output or a commit. Tests use an
   obviously fake key.
2. **No real call in a test.** A fake Anthropic client returns MCP tool-use and tool-result blocks as the SDK types
   describe them; the real SDK only over an in-process transport.
3. `passages` mode is unchanged: the same request, the same check, the same page.
4. Nothing is written to any tenant.
5. **Never edit a frozen test file.** `check.py` does not change. No migration.

## Your tests

The request carries the MCP server, the toolset and the beta; the passages are taken from the tool results; a quote
outside them is removed; no tool result is `no_answer`; provider errors and the deadline behave as today; the secret
is encrypted at rest, never shown back, never logged; the mode is refused without the key or with another provider;
`passages` mode unchanged (the frozen PMVP-20, PMVP-21 and PMVP-23 files and the frozen check green).

## Files you may change

- `P/src/assistant/providers.py`, `P/src/assistant/core.py` (the factory's choice), a new `P/src/assistant/brain_tools.py`,
  `P/src/assistant/README.md`
- `P/src/platform_settings.py` (three entries), `P/config/config.yaml` (the two pins)
- `P/src/web_interface/settings_routes.py`, `static/js/platform_settings.js` (the warning, the refusal)
- `P/src/web_interface/assistant_routes.py`, `templates/ask.html`: only so a reply from the Brain's tools is never
  granted and shows a title without a link where it cannot be opened
- `P/tests/test_assistant_brain_tools*.py` (new); existing tests only where needed, each listed with its reason
- the assistant's card, its page, `docs/components/boundaries.yaml`; `management/assistant.md`,
  `management/data-protection.md`, `management/README.md`; this order's notes

## Done when

1. Your tests green; the frozen PMVP-20 (88), PMVP-21 (13), PMVP-23 (33) files and the frozen check green;
   `tests/test_assistant_*.py`, `tests/test_settings_*.py`, `tests/test_platform_settings.py` green. Under the
   pinned versions (FU-50). **Do not run the whole suite.** Own test database, dropped at the end.
2. Commits `PMVP-20 brain-mcp: …`, ending with the co-author line; `feat/pmvp-20-brain-mcp` pushed. No pull
   request. The builder does not verify the work.
3. **The real check, once:** if `E:\Knovas\.secrets\knovas_gw_key_demo.txt` exists, two questions end to end
   against the **demo tenant**'s gateway key with `claude-opus-5-5` (the Anthropic key from
   `E:\Knovas\.secrets\anthropic_api_key.txt.txt`; both read into the process, never printed, logged or committed).
   Per question: the tool calls, the statements kept and removed, the lengths. No text. Without the key file,
   skipped and said so.

## Builder's notes

Builder session, 2026-11-22, branch `feat/pmvp-20-brain-mcp` from `origin/feat/platform-mvp` @ `e4dca705`, worktree
`.worktrees/pmvp-20m`. Commits `075522a9` (this order), `cf47585f` (code and tests), `9f9abcd5` (cards and management
pages) and the commit of these notes. Nothing here is verified; that is the verifier's.

**The API, as read** (claude-api skill, bundled 2.1.329; and the pinned SDK's own types, `anthropic==2.12.0`):

| What | Used | Where read |
|---|---|---|
| beta | `mcp-client-2025-11-20` (in `betas`, beside `server-side-fallback-2026-07-01`; the SDK sends both in `anthropic-beta`) | `shared/tool-use-concepts.md`, "MCP Connector (Beta)"; `AnthropicBetaParam` lists it |
| server | `mcp_servers=[{"type": "url", "url": <assistant.brain_mcp_url>, "name": "knovas-brain", "authorization_token": <assistant.brain_mcp_key>}]` | the same section; `BetaRequestMCPServerURLDefinitionParam` |
| toolset | `tools=[{"type": "mcp_toolset", "mcp_server_name": "knovas-brain", "default_config": {"enabled": false}, "configs": {"search": {"enabled": true}, "fetch": {"enabled": true}}}]` (allowlist mode) | the same section ("Optional toolset fields"); `BetaMCPToolsetParam`, `BetaMCPToolConfigParam` |
| response blocks | `mcp_tool_use` (`id`, `name`, `server_name`, `input`), `mcp_tool_result` (`tool_use_id`, `is_error`, `content`: a string or text blocks) | the docs name `mcp_tool_use` only (`shared/opentelemetry.md`); both from the SDK: `BetaMCPToolUseBlock`, `BetaMCPToolResultBlock` |
| `pause_turn` | sent back as it came, no extra user message | `shared/tool-use-concepts.md`, "Stop reasons for server-side tools" |

The pinned SDK supports all of it (`mcp_servers` on `beta.messages.create`); nothing to stop on.

**Built.**

- `src/platform_settings.py`: `assistant.mode` (choice `passages`/`brain_mcp`, `passages` by default, `ASSISTANT_MODE`),
  `assistant.brain_mcp_url` (text, `https://mcp.knovas.ch/mcp`, `ASSISTANT_BRAIN_MCP_URL`, checked by
  `providers.brain_mcp_url_problem`), `assistant.brain_mcp_key` (secret, no pin); `config/config.yaml` reads the two
  variables (`test_platform_settings.py` requires it of every non-secret entry).
- `src/assistant/providers.py`: `make_brain_mcp_ask` (the request above plus today's: model, effort `medium`, the
  server-side fallback, `max_tokens` at least 8,000, the pinned base URL, `max_retries` 0, the client closed after each
  request), `unavailable_ask`, `BrainCall`, the deadline (`_request_by_deadline`), `_mapped_brain`,
  `_NoBrainRequestOptions`, `chosen_mode`, `brain_mcp_url_problem`, `settings_problems` (three more lines),
  `settings_warnings`, `brain_mcp_refusal`, the German texts.
- `src/assistant/brain_tools.py` (new): `BrainToolsService`, `BRAIN_SYSTEM_INSTRUCTION`, `compose`,
  `read_tool_results`, `final_text`, `resolve_sources`, `cited`, `closest`, `ToolsReply`, one log line per answer.
- `src/assistant/core.py`: `make_service` returns the brain service when the mode is not `passages`; an unset mode is
  `passages`.
- `web_interface/assistant_routes.py` and `templates/ask.html`: a reply with `opens_directly` False is never granted,
  and without an address its passages show the title alone (no «Im Dokument zeigen»). With an address the markup is
  the same as before (the frozen PMVP-22a/23 pages render as they did).
- `web_interface/settings_routes.py`, `static/js/platform_settings.js`, `static/css/style.css`: `warnings` in the
  page's answer and one line `p.platform-settings-warning` under the mode; the refusal before `resolver.save`.
- `src/assistant/README.md` (a new section and four knobs), the card (P-9, P-10, RISK-04, two operations, the use of
  `EXT-MCP-GATEWAY`), `EXT-mcp-gateway.card.yaml` (the operation `mcp` is now called for the Platform, through
  Anthropic), `boundaries.yaml`, both pages rendered; `management/assistant.md`, `data-protection.md`, `README.md`.

**Decided beyond the order, or against its letter (the verifier should look here first).**

1. **`source` is a document's title, not a number.** The tool results carry no passage numbers the model could cite,
   and `check.py` takes an integer source. So the model writes the document's title as the gateway gave it
   (the Core's pointer), and `resolve_sources` turns it into the number of one passage of that document before
   `check.check_answer` runs over all passages, unchanged. The passage chosen is the first, shortest first, on
   which `check.check_answer` alone shows the statement (else the document's first, so the check removes it). So a
   statement is shown if and only if one passage of the named document holds its quote word for word and every
   number of it. A quote is never put together from two passages (tested). A title matches exactly, then ignoring
   case, then by its last path part when one document has it. A number or a title no tool returned becomes 0
   (`unknown_source`).
2. **Which texts are passages:** each search result's `passages[]` and each fetched `text` (one passage per part,
   up to 20,000 characters), deduplicated by document and text, at most 60. The search result's `summary` is not a
   passage (it is the ingestion's summary, not the document's words); the model is told never to quote one.
3. **The reply keeps only the cited passages**, renumbered in the order first cited (a turn can return 30 passages
   of 8 documents; «Quellen» would repeat titles). A `no_answer` shows the first passage of each document (at most
   10). So a stored chat keeps the cited passages' text, a fetched part whole (up to 20,000 characters each).
4. **Only the gateway's server counts:** a result must answer an `mcp_tool_use` of the server `knovas-brain`, must
   not be an error, and its text must be the gateway's JSON. A gateway that answered only "busy" makes the reply
   `busy`, not `no_answer`.
5. **The answer is the text after the last tool block** (what Claude wrote before a call is not counted as removed).
6. **The deadline is PMVP-24's** (neither the context work's nor PMVP-24 is merged): 150 s from the service's start
   (not the route's: nothing slow happens before it), no request started with less than 30 s left, each request's
   client timeout at most the time left (60 s otherwise), and **a timeout is busy** (PMVP-24, D1), where the
   passages mode on this branch still says `failed` for Claude's timeout. The wall clock: the request runs on a
   daemon thread and the caller waits only until the deadline, then closes the client; the thread ends at its own
   timeout at the latest. The constants live in `brain_tools.py`; once PMVP-24 is merged they should be `core`'s.
7. **`pause_turn`** is continued at most twice (`BRAIN_MCP_MAX_CONTINUATIONS`), each within the deadline; a
   continuation without 30 s left is left out and the turn ends as it is.
8. **The gateway's address is checked** on the page and again before every call: `https`, `knovas.ch` or a name under
   it, letters/digits/dots/hyphens only, no user, query, fragment or backslash. Otherwise an administrator could
   point Anthropic, and with it the saved key (which the page never shows), at a server of her own.
9. **The refusal** looks at a save only when it touches the mode, the provider or one of the two keys, and reads the
   state the save would leave (a removed choice falls back to `passages`/`knovas`). So with `brain_mcp` chosen, neither
   key nor the provider can be taken away; back to `passages` first. A deployment that pins `brain_mcp` without what it
   needs is not refused (the page cannot change it); the page names what is missing (`settings_problems`) and every
   answer fails without sending anything. An unknown mode (only a deployment can set one) fails every answer.
10. **No grant, and the title only.** The order says "the page stays as it is", but `granted()` would let the person
    open, through the file routes, a document the company key read and her own search may not have returned. A
    `ToolsReply` is therefore never granted; a stored chat's links go through the existing click route (the Core's
    `document_readable` as the person, then the grant). An unsaved reply has no such route, so its passages show the
    title without a link: that is the order's "If it can't, it shows the title only". The readability is not asked
    at answer time (no extra Core calls); under `disabled`, where this mode belongs, the click route opens every
    document the Platform holds.
11. **The SDK's DEBUG "Request options" line is dropped** for requests that carry `mcp_servers`
    (`_NoBrainRequestOptions` on the logger `anthropic._base_client`, installed on the first brain call): that body
    holds the gateway key, the question and the chat, and FU-46 sets loggers to DEBUG explicitly. The app's hold of
    the SDK's loggers (FU-39) stays as it was; a control test shows the key would be logged without the filter.
12. **"Verbindung testen" for the gateway key** was not built (not ordered): a real test would send company data
    to Anthropic. Like other secrets, the key's Test button answers "Für assistant.brain_mcp_key gibt es keinen Test".
13. **The follow-up's `search_text` is not used**: Claude chooses its own searches; the chat's history is sent as the
    passages mode sends it (`service._usable_turns`, `service._fit_history`, private functions of the same package).
14. **The instruction** allows at most 3 searches and 3 fetches, so that a turn stays within the deadline and the
    server's tool loop; its example uses an invented title.

**Tests** (pinned venv, Python 3.13.5, `anthropic` 2.12.0; own database `knovas_platform_test_pmvp20m`, dropped at the
end):

- New: `test_assistant_brain_tools.py` 56, `test_assistant_brain_tools_settings.py` 32 (one through the real app and
  database), `test_assistant_brain_tools_pages.py` 5: **93 passed**. No test reaches Anthropic or a gateway: a fake
  client returns the SDK's `BetaMCPToolUseBlock`/`BetaMCPToolResultBlock`, or the real SDK runs over an in-process
  transport.
- The frozen files: PMVP-20 **88**, PMVP-21 **13**, PMVP-23 **33** passed. In one run with every
  `tests/test_assistant_*.py`, `tests/test_settings_*.py` and `tests/test_platform_settings.py`: **1,325 passed**,
  none failed or skipped. The frozen check: all 13 lines OK. `scripts/check_ascii_py.py`: 0 lines. The whole suite
  was not run.
- **Existing tests changed: none.**
- 10 single changes to the new code (the grant, the title-to-passage step, error results, unmatched results, the log
  filter, the wall clock, the address check, the refusal, the toolset's allowlist, the shortest-passage choice) each
  make a new test fail.
- Card checks (KnowledgeBase `origin/master` `scripts/brain_runner`, extracted to the scratchpad): `card check` 21
  cards valid, 0 errors, 0 warnings; `card render --check` 0 not current. `map-check` was not run.

**The real check: skipped.** `E:\Knovas\.secrets\knovas_gw_key_demo.txt` does not exist (the Anthropic key file
does). Nothing was sent to Anthropic or to the gateway. So nothing here shows yet that the API accepts this request
shape (the MCP connector with the server-side fallback and `fallbacks: "default"` in one request), how long a turn
takes against the 60-second client timeout, or that the gateway's text arrives as one text block of JSON (read in its
source, `app.py`: `json.dumps(answer)`, `structured_output=False`).

**Open, for the coordinator.**

- The demo tenant's gateway key for the real check (two questions; the script is ready to write in minutes).
- Whether the 60-second client timeout is enough for a turn of up to 6 tool calls with Opus at effort medium; if not,
  the order's "keep today's timeout" would have to give way to the deadline alone.
- Whether the server-side fallback combines with the MCP connector; if the API refuses it, drop `fallbacks` on this
  path only.
- The class is critical: management approves before `main`. The mode is safe only where every person of the firm may
  read everything, and never under «Ein» (CR-R5/CR-R6).
- PMVP-24 will conflict in `providers.py` and `core.py` (both add to `make_service` and the deadline); the brain
  service's constants then move to `core`.
