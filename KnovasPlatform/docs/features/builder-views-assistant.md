# Brain Builder: views by description

On the **Ansichten** stage, "Ansicht beschreiben" takes a description in plain words, for example
"Zeig die Komponenten nach Systembereich, mit ihren Aufrufen. Ein Klick auf eine Komponente zeigt ihre
Einstellungen und Regeln. Für die Entwicklung." Claude builds the hierarchy, the pages, the profile and its
views, checks them and corrects itself, and the result appears in the tabs and the preview below.

- **One answer is one undo step.** "Rückgängig" takes back everything the answer changed.
- **Only the draft changes.** Nothing reaches the tenant without the usual dry run and approval.
- **You can keep talking.** "Füge noch die Tests hinzu" or "nur für die Rolle member" refines the last answer.
  Claude asks back only when the description leaves something open it cannot settle sensibly.

## Set up

| Setting (Einstellungen) | Pinned by | Default |
|---|---|---|
| Schlüssel für die Claude API (`builder.assistant.api_key`, secret) | `ANTHROPIC_API_KEY` | none: no assistant |
| Modell des Ansichten-Assistenten (`builder.assistant.model`) | `BUILDER_ASSISTANT_MODEL` | `claude-opus-5-5` (`claude-sonnet-5-5` is cheaper and faster) |

Without a key, the stage says so and the manual editor works as before. The image needs the `anthropic`
package (requirements.txt).

## What is sent, what is logged

- **Sent to the Claude API:** the workspace's definitions (types and their attributes, relations, hierarchies,
  pages, profiles, the role mapping), the description, and the earlier turns of the conversation. Never data
  from the tenant or from a source.
- **Logged:** the number of rounds, operations and changes, token counts and the API request id. Never the
  description or the reply.

## How it works

`builder.assistant` runs a tool loop on the Messages API with one tool, `try_operations`. Claude sends the
builder's own operations: the views stage's operations, plus `relation.create` and `relation.update` for a link
the ontology lacks. They are tried in order on a scratch copy of the draft. The tool answers with what each
operation refused and what the checks (`builder.views.validate`) find. Claude corrects until nothing of its own
is left, then answers.

The route `POST /builder/api/w/<id>/views/assistant` (role: `edit`) applies the operations Claude kept to the
draft as one batch. If the draft changed in the meantime, the whole answer is refused.

Limits:
- one answer takes at most 150 s, under the web server's 180 s;
- at most 10 model calls per answer;
- descriptions are at most 4000 characters.

When time runs out, what was already checked is kept.

## Next (not built): an agent with access to the Brain

Requested 2026-11-01. The assistant would also read the tenant's Brain: search, nodes and neighbours, through
the same read-only calls as the Knovas MCP tools. With that it could build views on what the graph actually
holds, for example propose the relations it finds between types.

Requirements:

1. **Off by default.** An administrator switches it on.
2. **The agent has its own RBAC role**, for example "KI-Assistent". Its access groups and rights are set like any
   other role's.
3. **The rights that apply are the intersection.** A call may do and see only what *both* the person using the
   agent *and* the agent's role may. Their access groups are intersected, and so are their Platform rights,
   such as `builder_edit`.
   - The server computes the intersection from the session and the agent's role, never from a request field.
   - Every Brain read passes the intersected access groups (`access_groups`).
   - An empty intersection means the agent sees nothing.
4. **Logs carry hashes and counts only** (GI-DATA-02/04): no node names, no document text.

Open questions:
- Does the Brain's read API accept access groups on behalf of a principal from this Platform's certificate,
  or does it need its own grant?
- Should the result of a Brain read be cut down before it goes to the Claude API (names only, no snippets)?
- Does the intersection also limit which builder operations the agent may run, or only what it may read?
