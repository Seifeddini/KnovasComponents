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

(the builder writes here)
