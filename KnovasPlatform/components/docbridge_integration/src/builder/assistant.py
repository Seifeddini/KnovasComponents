"""Views by description: Claude writes the view definitions from what a person says they want to see.

The person describes a view in their own words ("die Komponenten nach Systembereich, mit ihren Aufrufen; ein Klick
zeigt Einstellungen und Regeln"). Claude turns it into the builder's own operations -- the ones the views stage sends
(builder.view_edits) and ``relation.create`` for a link the ontology lacks -- and tries them through one tool on a
scratch copy of the draft. The tool answers with what each operation refused and what the checks (builder.views.validate)
find afterwards, so Claude corrects itself until the definitions are clean. Only then are the operations it kept
applied to the draft, by the route, as one batch: one undo takes the whole answer back. Nothing is written to a
tenant here; publishing keeps its dry run and its approval.

What Claude is sent: the workspace's definitions (types and their attributes, relations, hierarchies, pages,
profiles, role mapping) and what the person writes. Never data from the tenant or from a source. Logs carry counts
and the model's request id, never the description or the reply.
"""
from __future__ import annotations

import copy
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

from builder import editing, views as V

logger = logging.getLogger(__name__)

#: The models an administrator may choose (settings registry ``builder.assistant.model``); the first is the default.
MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")
#: The operations Claude may use: how a company sees its Brain, and a relation a path or a level needs.
ALLOWED = frozenset({
    "spine.create", "spine.update", "spine.remove", "branch.create", "branch.remove",
    "level.add", "level.update", "level.remove", "level.move",
    "page.create", "page.update", "page.remove", "slot.add", "slot.update", "slot.remove", "slot.move",
    "profile.create", "profile.update", "profile.remove", "view.add", "view.update", "view.remove", "view.move",
    "roles.set", "relation.create", "relation.update",
})
MAX_ROUNDS = 10            # model calls per answer: enough to try, read the checks and correct twice or three times
MAX_HISTORY = 12           # earlier turns sent along (text only)
MAX_MESSAGE = 4000         # characters of one description
#: Seconds one answer may take: under the web server's 180 s (DOCBRIDGE_WEB_TIMEOUT, nginx proxy_read_timeout).
TIME_BUDGET = 150.0
TOOL = "try_operations"

SYSTEM = """You are the views assistant of a knowledge-graph builder. A consultant or administrator describes, in German \
or English, what they want to see of their company's knowledge graph; you build it from the builder's definitions. \
Reply to them in German, in plain words: no operation names, no keys unless they help them find something.

## What the definitions are
- **Ontology** (read it, do not redesign it): node types with attributes, and relations with domain (Von) and range \
(Nach). A relation `r` with domain A and range B links A -r-> B. Only types and relations listed below exist.
- **Hierarchy (spine)**: ordered levels from top to bottom. Each level names the node types on it and how a node \
links to its parent on the level above: `link: {relation, direction}`; direction `out` means the child points to the \
parent (child -r-> parent, e.g. part_of), `in` means the parent points to the child (parent -r-> child, e.g. contains). \
The top level has no link. A spine can have branches (side chains of levels).
- **Page**: what a click on a node of one type shows, as sections in order. A section is filled by one of: \
`field` (description or name of the node), `facts` (attribute names of that type), or `paths` (nodes reached over \
relations; each path is a list of steps `{relation, direction, to_type}` from the page's type; direction `out` follows \
the relation from domain to range, `in` against it). Section renders: {section_renders}.
- **Profile**: one audience (e.g. engineers). It has a lens (which spines, types and relations it may see), views \
in order, and the pages it shows. A view: `{id, label, render, definition}`; renders: {renders}. The definition takes \
`root_types` (where the view starts), `paths` (labelled paths from the root types, what it shows), `spine` (a spine \
key, for tree/map), `process` (a process key, for board/trace), `group_by` (an attribute), `columns` (attributes, for \
list/matrix), `color_by` (attributes the view colours by). A profile's lens must include what its views and pages use.
- **Roles**: which Platform roles see which profiles (`roles.set`).

## How you work
Call `{tool}` with a list of operations. They are tried in order on a scratch copy of the draft; an operation that is \
refused is skipped and its error is returned; the others stay. After the batch you get the checks' findings for the \
whole draft. Fix what is critical and what your operations caused, then call the tool again with only the new \
operations (what was accepted stays; pass `"reset": true` to start over from the draft). When the checks show \
nothing of yours left, stop and answer with a short summary of what now exists and where to find it (tab and name), \
and one sentence on what they might add next. If the ontology cannot express what they want (a relation that does \
not exist and cannot sensibly be added), say so instead of guessing. Ask a question only if the description leaves \
something open that you cannot settle sensibly yourself; otherwise build the sensible default.

Prefer few, complete definitions: one profile per audience, a page for every type its views show, a spine when the \
description has levels (areas, parts, sub-parts). Reuse what exists (extend a profile, add sections to a page) \
instead of making duplicates. Keys you make: spine `SPINE-` + lowercase words with hyphens; page `PAGE-` + lowercase \
with hyphens; profile and view ids lowercase with hyphens; section ids lowercase with underscores. Titles and labels \
in German (`{{"de": ..., "en": ...}}` where a title is asked for).

## Operations (JSON objects; the `op` field names it)
- `{{"op": "spine.create", "key": "SPINE-aufbau", "name": "Aufbau", "types": ["System"], "level_name": "Systeme"}}` \
  (creates the top level L1 with those types)
- `{{"op": "level.add", "spine": KEY, "level": {{"name": "Komponenten", "types": ["Component"], "link": \
{{"relation": "part_of", "direction": "out"}}}}}}` (appends at the bottom; `"at": n` inserts; `"branch": key` for a branch)
- `{{"op": "level.update", "spine": KEY, "level": "L2", "set": {{"name"?, "types"?, "link"?, "key_prefix"?}}}}`; \
`level.remove` / `level.move` (`"to": n`) with `spine`, `level`
- `{{"op": "branch.create", "spine": KEY, "key": "sales", "name": "Vertrieb"}}`
- `{{"op": "page.create", "type": "Component"}}` (key PAGE-<type>; starts with a description section; `"key"` to choose)
- `{{"op": "slot.add", "page": PAGE_KEY, "slot": {{"id": "aufrufe", "title": {{"de": "Ruft auf"}}, "render": "list", \
"content": {{"paths": [{{"label": "Ruft auf", "path": [{{"relation": "calls", "direction": "out", "to_type": \
"Component"}}]}}]}}}}}}`; content can instead be `{{"facts": ["status", "owner"]}}` or `{{"field": "description"}}`; \
path sections also take `columns`, `order_by` `{{"fact", "direction": "asc"|"desc"}}`, `limit`
- `slot.update` (`page`, `slot` id, `set` with the same fields), `slot.remove`, `slot.move` (`to`)
- `{{"op": "profile.create", "id": "engineering", "audience": "Entwicklung"}}`
- `{{"op": "profile.update", "id": ID, "set": {{"lens_spines": [...], "lens_types": [...], "lens_relations": [...], \
"pages": [PAGE keys], "description": "..."}}}}` (lists replace the old ones: send the whole list)
- `{{"op": "view.add", "profile": ID, "view": {{"id": "systemkarte", "label": "Systemkarte", "render": "map", \
"definition": {{"root_types": ["System"], "spine": "SPINE-aufbau", "paths": [...], "color_by": ["status"]}}}}}}`
- `view.update` (`profile`, `view` id, `set` like a view), `view.remove`, `view.move` (`to`)
- `{{"op": "roles.set", "role": "member", "profiles": [ID, ...]}}` (roles: {roles})
- `{{"op": "relation.create", "key": "part_of", "domain": ["Component"], "range": ["System"], "label": {{"de": "ist \
Teil von", "en": "is part of"}}}}`; `{{"op": "relation.update", "key": KEY, "set": {{"domain": [...], "range": [...]}}}}` \
(only when a level or path needs a link the ontology lacks; say so in your answer)
- `spine.update`, `spine.remove`, `page.update` (`set`: `name`, `type`), `page.remove`, `profile.remove` as expected
"""


class AssistantError(Exception):
    """The assistant cannot answer; the message is for the page."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class Answer:
    reply: str
    operations: list[dict] = field(default_factory=list)   # the ones kept, in order: what the route applies
    problems: list[dict] = field(default_factory=list)     # the checks after them
    rounds: int = 0


def definitions(model: Mapping[str, Any], roles: Iterable[str]) -> dict:
    """What Claude is told about the workspace: its definitions, never data."""
    onto = model.get("ontology") or {}
    types = []
    for t in onto.get("types") or []:
        if not isinstance(t, Mapping) or t.get("retired"):
            continue
        entry: dict[str, Any] = {"key": t.get("key"), "label": (t.get("label") or {}).get("de") or None}
        if t.get("parent"):
            entry["parent"] = t["parent"]
        attrs = [{k: a[k] for k in ("name", "datatype", "target", "values") if a.get(k)}
                 for a in t.get("attributes") or [] if isinstance(a, Mapping) and not a.get("retired")]
        if attrs:
            entry["attributes"] = attrs
        types.append({k: v for k, v in entry.items() if v is not None})
    relations = [{k: r[k] for k in ("key", "domain", "range") if k in r}
                 | ({"label": (r.get("label") or {}).get("de")} if (r.get("label") or {}).get("de") else {})
                 for r in onto.get("relations") or [] if isinstance(r, Mapping) and not r.get("retired")]
    processes = [p.get("key") for p in ((model.get("processes") or {}).get("processes") or []) if isinstance(p, Mapping)]
    return {"types": types, "relations": relations, "processes": [p for p in processes if p],
            "spines": V.spines_of(model),
            "pages": [p for p in ((model.get("pages") or {}).get("pages") or []) if isinstance(p, Mapping)],
            "profiles": [e.get("definition") for e in model.get("profiles") or [] if isinstance(e, Mapping)],
            "profile_roles": V.profile_roles(model), "roles": sorted(roles)}


def system_prompt(roles: Iterable[str]) -> str:
    """The instructions, the same for every request of an installation (so the cache holds them)."""
    text = SYSTEM
    for name, value in (("tool", TOOL), ("renders", ", ".join(f"{k} ({v})" for k, v in V.RENDERS.items())),
                        ("section_renders", ", ".join(V.SECTION_RENDERS)), ("roles", ", ".join(sorted(roles)) or "-")):
        text = text.replace("{" + name + "}", value)
    return text.replace("{{", "{").replace("}}", "}")


TOOLS = [{
    "name": TOOL,
    "description": ("Try builder operations on a scratch copy of the draft, in order. Returns, per operation, "
                    "whether it was accepted or why it was refused, and then the checks' findings for the whole "
                    "draft. Accepted operations stay for the next call; send only new ones, or reset."),
    "input_schema": {
        "type": "object",
        "properties": {
            "operations": {"type": "array", "items": {"type": "object"},
                           "description": "Operations, each an object with an `op` field."},
            "reset": {"type": "boolean", "description": "Drop everything accepted so far and start from the draft."},
        },
        "required": ["operations"],
    },
}]


class _Scratch:
    """The draft as the operations tried so far leave it."""

    def __init__(self, model: Mapping[str, Any], roles: list[str]):
        self.draft = copy.deepcopy(dict(model))
        self.roles = roles
        self.reset()

    def reset(self) -> None:
        self.model = copy.deepcopy(self.draft)
        self.kept: list[dict] = []

    def problems(self) -> list[dict]:
        return V.validate(self.model, roles=self.roles)

    def run(self, raw: Any) -> str:
        if not isinstance(raw, Mapping):
            return json.dumps({"error": "input must be an object with operations"})
        if raw.get("reset"):
            self.reset()
        results = []
        for n, op in enumerate(raw.get("operations") or []):
            name = op.get("op") if isinstance(op, Mapping) else None
            if name not in ALLOWED:
                results.append({"n": n, "op": name, "refused": "not an operation of the views assistant"})
                continue
            trial = copy.deepcopy(self.model)
            try:
                changes = editing.apply(trial, op)
            except editing.EditError as exc:
                results.append({"n": n, "op": name, "refused": exc.message,
                                **({"details": exc.details} if exc.details else {})})
                continue
            self.model = trial
            if changes:
                self.kept.append(dict(op))
            results.append({"n": n, "op": name, "accepted": True})
        found = self.problems()
        critical = [f for f in found if f["severity"] == "critical"]
        rest = [f for f in found if f["severity"] != "critical"]
        shown = [{k: f[k] for k in ("severity", "area", "item", "part", "message") if f.get(k) is not None}
                 for f in (critical + rest)[:40]]
        return json.dumps({"results": results, "kept_so_far": len(self.kept),
                           "checks": {"critical": len(critical), "warnings": len(rest), "findings": shown}},
                          ensure_ascii=False)


def _text_of(content: Iterable[Any]) -> str:
    return "\n".join(b.text for b in content if getattr(b, "type", None) == "text" and b.text).strip()


def _history(turns: Any) -> list[dict]:
    out = []
    for turn in (turns if isinstance(turns, list) else [])[-MAX_HISTORY:]:
        if not isinstance(turn, Mapping):
            continue
        role, text = turn.get("role"), str(turn.get("text") or "").strip()[:MAX_MESSAGE]
        if role in ("user", "assistant") and text:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def answer(client: Any, model_name: str, model: Mapping[str, Any], message: str, *, history: Any = None,
           roles: Iterable[str] = (), budget: float = TIME_BUDGET, clock: Callable[[], float] = time.monotonic) -> Answer:
    """Claude's answer to ``message``: its reply and the operations it kept, tried on a copy of ``model``."""
    import anthropic

    message = (message or "").strip()
    if not message:
        raise AssistantError("Beschreiben Sie, was die Ansicht zeigen soll.", 400)
    if len(message) > MAX_MESSAGE:
        raise AssistantError(f"Bitte kürzer: höchstens {MAX_MESSAGE} Zeichen.", 400)
    roles = sorted(set(roles))
    scratch = _Scratch(model, roles)
    system = [
        {"type": "text", "text": system_prompt(roles)},
        {"type": "text", "text": "The workspace's definitions now (JSON):\n"
                                 + json.dumps(definitions(model, roles), ensure_ascii=False, sort_keys=True)},
    ]
    messages: list[dict] = _history(history) + [{"role": "user", "content": message}]
    reply, rounds = "", 0
    deadline = clock() + budget
    out_of_time = ("Die Zeit für eine Antwort war um. Was bis dahin geprüft war, ist übernommen; beschreiben Sie "
                   "den Rest in einer neuen Nachricht.")
    while rounds < MAX_ROUNDS:
        left = deadline - clock()
        if left < 10:
            reply = out_of_time
            break
        rounds += 1
        try:
            response = client.with_options(timeout=left, max_retries=0).beta.messages.create(
                model=model_name, max_tokens=16000, system=system, tools=TOOLS, messages=messages,
                output_config={"effort": "medium"}, cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.APITimeoutError:
            reply = out_of_time
            break
        except anthropic.AuthenticationError:
            raise AssistantError("Der Schlüssel für die Claude API wird abgelehnt. Prüfen Sie ihn in den Einstellungen.", 503)
        except anthropic.PermissionDeniedError:
            raise AssistantError("Der Schlüssel für die Claude API hat keine Berechtigung für dieses Modell.", 503)
        except anthropic.NotFoundError:
            raise AssistantError(f"Das Modell {model_name} ist mit diesem Schlüssel nicht verfügbar.", 503)
        except anthropic.RateLimitError:
            raise AssistantError("Die Claude API ist gerade ausgelastet. Versuchen Sie es in einer Minute noch einmal.", 429)
        except anthropic.BadRequestError as exc:
            logger.warning("Views assistant: request refused by the API (%s)", getattr(exc, "status_code", "400"))
            raise AssistantError("Die Claude API hat die Anfrage abgelehnt.", 502)
        except anthropic.APIStatusError as exc:
            raise AssistantError(f"Die Claude API antwortet mit einem Fehler ({exc.status_code}). "
                                 "Versuchen Sie es noch einmal.", 502)
        except anthropic.APIConnectionError:
            raise AssistantError("Die Claude API ist von diesem Server aus nicht erreichbar.", 502)
        usage = getattr(response, "usage", None)
        logger.info("Views assistant: round %d, stop %s, request %s, tokens in %s (cached %s) out %s",
                    rounds, response.stop_reason, getattr(response, "_request_id", None),
                    getattr(usage, "input_tokens", None), getattr(usage, "cache_read_input_tokens", None),
                    getattr(usage, "output_tokens", None))
        if response.stop_reason == "refusal":
            reply = "Dazu kann ich keine Ansicht bauen."
            break
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if response.stop_reason != "tool_use" or not uses:
            reply = _text_of(response.content)
            if response.stop_reason == "max_tokens" and not reply:
                reply = "Die Antwort wurde zu lang und ist abgebrochen. Beschreiben Sie einen kleineren Teil."
            break
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": b.id,
             "content": scratch.run(b.input) if b.name == TOOL else json.dumps({"error": "unknown tool"})}
            for b in uses]})
    else:
        reply = ("Ich bin nach mehreren Versuchen nicht auf saubere Definitionen gekommen. Was bisher passt, ist "
                 "übernommen; die Prüfung zeigt den Rest.")
    return Answer(reply=reply or "Fertig.", operations=scratch.kept, problems=scratch.problems(), rounds=rounds)


def apply_all(model: dict, operations: list[Mapping[str, Any]]) -> list[dict]:
    """The kept operations on the draft, in order: its changes, one batch. An operation the draft refuses now
    (it moved on since) refuses the whole answer."""
    changes: list[dict] = []
    for op in operations:
        if op.get("op") not in ALLOWED:
            raise editing.EditError("Unbekannte Änderung.")
        changes += editing.apply(model, op)
    return changes


