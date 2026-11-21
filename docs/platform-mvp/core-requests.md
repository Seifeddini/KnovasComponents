# What the Platform MVP needs from the Knovas Core

Written by the coordinator on 2026-11-21 for whoever takes the Core change (founder, 2026-11-21: "Someone else does
it"). The Platform side is built and waits on these two reads. Follow-ups FU-37 and FU-38.

## Why

The assistant now runs end to end on the demo tenant (tenant `817f2330-…`, production Core): the person's search
through `POST /secured/query`, Claude answering, the Platform's check. Both test questions came back "Dazu finde ich
nichts in Ihren Quellen", correctly: each hit carries only its chunk's own text, measured at 2 to 172 characters
(mostly about 50), and no answer can be quoted word for word from that. The dossier has the same gap: a fact names
its evidence chunk (`provenance_chunk_id`), but no route returns that chunk's text or page.

## CR-1: a few sentences around each hit, in the search answer

- **Where**: `POST /secured/query`, per entry of a hit's `top_chunks`.
- **What**: a field `context` (name to be chosen by the Core): the chunk's text together with the neighbouring
  sentences **of the same document**, up to 1,200 characters, cut at a word boundary, centred on the chunk; and the
  location of that text: `context_page_number`, `context_sentence_number`, `context_sentence_number_end`.
- **Opt-in**: only when the request asks for it (e.g. `"include_context": true`), so the search page's answers stay
  as small as today.
- **Rights**: the context comes only from the chunk's own document, which the caller may already read (the hit is
  there). Nothing from another document.
- **Logs**: no context text in any log line. The Core's INFO line that logs raw `top_chunks` (Platform follow-up FU-09
  found the Platform's side; check the Core's) must not log it.
- **Invariant**: GI-QUERY-04's containment rule needs the Core's own amendment for this.

## CR-2: one passage by its id

- **Where**: a per-thing read, e.g. `GET /secured/chunks/<chunk_id>`.
- **What**: the chunk's text (with the same context as CR-1 if asked), its document's pointer, `page_number`,
  `sentence_number`, `sentence_number_end`.
- **Rights**: as the caller (the signed actor, like search). A chunk of a document the caller may not read answers
  exactly like a chunk that does not exist (404, same body).
- **Use**: the dossier opens a fact's passage under the fact (B-04, BRAIN-VIEWS POST-02); later the evidence trail.

## How the Platform will use them

- CR-1: inside `keeping_snippets()` (`knovas_client.py`, the passage round of PMVP-20) the passage becomes the
  `context` and its location; everything else stays. The check (quote word for word, numbers in the quote) is
  unchanged.
- CR-2: the dossier (PMVP-30a) shows the passage text and page under a fact, and opens the document at that page.

## How to know it works

On the demo tenant, as a member: "Welche Kündigungsfrist gilt im Mietvertrag?" and a question about the Abmahnung in
matter 2023-041 get answers with a quote under each statement, through the local demo copy (the coordinator's
launcher). Nothing is ever written to the Knovas GmbH tenant for this.

## Added on 2026-11-21: what roles and "enforcement on" need from the Core (CR-R1 to CR-R6)

From the role builder's design (`design/role-builder.md`, § 2 and § 7, with the code lines that show each point). The
founder decided the same day that these go with CR-1 and CR-2 to whoever takes the Core work. **The most urgent is
CR-R1**: today, when a tenant is switched to `enforcing`, the Core ignores the Platform's signed identity
(`principal_resolver.py` reads the body field `access_groups`, which the Platform never sends), so every Platform
search would see open documents only. The Platform's claims are verified only in the posture `brokered`, and no route
can switch a tenant to it. Until CR-R1 and CR-R2 land, the pilot runs with enforcement off and roles' data do not act.

| No. | What | Why |
|---|---|---|
| CR-R1 | The posture route accepts `brokered` once a key is registered and the backfill is complete (DEC-0077 point 5; bet item SA-03) | Under `enforcing` the Platform's claim is ignored, so neither roles' data nor "Ein" act for the Platform |
| CR-R2 | Folder-rule routes take the caller's signed claim under `brokered` (today they build the caller from `acting_as` only, and `brokered` refuses a caller without a claim) | Without it, under "Ein" no folder rule can be set from the Platform |
| CR-R3 | A new folder rule reaches documents already read in (a backfill for one rule, started by the route) | Without it, a new folder covers only documents read in later |
| CR-R4 | Probe: evaluate without applying; count per day what would be hidden, no names | A-06's middle position |
| CR-R5 | **No code** (updated 2026-11-21): external agents go through the Platform (founder: "treat them the same as our own agent"; design § 5), and the Core already accepts any non-empty subject. Needed instead: an amendment of DEC-0077 that allows a Platform-registered agent as a claim's subject | Agents enforced like people and the assistant |
| CR-R6 | **Not needed** (updated 2026-11-21): agents no longer call the Core directly | — |

Also from the design: **the Core has no deny** (its filter is a disjunction without negation), so A-04's "a denial
wins" is not "Exist" as the plan says; the founder decided on 2026-11-21: no deny for the pilot.

**Needed for the pilot (15 December):** CR-R1 and CR-R2; CR-R3 for folders over documents already read in. Later: CR-R4.
