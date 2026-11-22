# Work order PMVP-30b: the dossier's related things by kind, as counts and lists

**Decided by the coordinator on 2026-11-22** (see "Decisions").

| | |
|---|---|
| Item | PMVP-30b (Jira: filed by the coordinator), plan `FEAT-2026-11-19-platform-mvp` § 4 **B-01** (a count that opens a list), **B-03** (sets as lists that can be sorted and filtered), B-02 (one starting point); § 5.2 ("Brain views"); spec item SPEC-platform-mvp-09; contract L2-PLATFORM-BRAIN-VIEWS RESP-01, PRE-01, POST-01, POST-03 |
| Follow-ups | none name this item. FU-34 (hrefs from `url_for` only) and FU-26 (what "refused" is) apply. FU-38 (a fact's passage) stays open; this item adds no fact |
| Class | **routine** (Jannik or Siran): a read and a page, behind the existing switch, as the person. No rights logic of its own (the Core narrows what the person sees), no storage, no grant |
| Depends on | PMVP-30a (the dossier, integrated `405022da`); **PMVP-34** (`brain_views/limit.py`: `paged`, `more_text`, the marks `data-bv-node`, `data-bv-row`, `data-bv-more`) |
| Branch / worktree | `feat/pmvp-30b` from `origin/feat/pmvp-34` while PMVP-34 is not integrated (merge `origin/feat/platform-mvp` in once it is), else from `origin/feat/platform-mvp`. Worktree `.worktrees/pmvp-30b` |
| Visual contract | `docs/platform-mvp/reference-screens/02-dossier.html` and `out/02-dossier.png`: the tabs «Überblick», «Mandate 7», «Verträge 34», «Personen 6», «Dokumente 412», the count after the label in a lighter weight. `04-umgebung.html` for a related thing's row: its name, and under it its relation («Vermieterin», «Mieterin») |
| Card | `docs/components/L2-platform-brain-views.card.yaml` (its page, `boundaries.yaml`) |
| Core requests | None needed. CR-2 (one passage by id) is for facts, not for related things. Until **CR-R1** the counts and lists are what the Core hands the caller: under «Aus» (every tenant today) every person sees every related thing. B-07 ("facts and counts follow the person's rights") is a 12 January row |
| Frozen tests | none (routine); the verifier checks "Done when" |

`P` = `KnovasPlatform/components/docbridge_integration`.

## Goal

On a thing's dossier a person sees what is connected to it, sorted by kind: one tab per kind with its count, as the
reference draws them. A tab opens the list of that kind, in which she sorts and filters, and each row opens that
thing's own dossier. Nothing is drawn as a graph. Everything is read for this one thing, as this person.

## What to build

1. **The read**, `P/src/brain_views/related.py` (new): `read_related(client, node_id) -> Related`, with `kinds` (each
   a key, a name, a count and its things) and `total`.
   - **One** `graph_neighbors(node_id, depth=1, include_edges=True)` as the person. The Core walks only what this
     person may see (`graph_api.node_neighbors`). Leave out the starting thing itself if the answer holds it (hop 0),
     and any row whose hop is not 1 or whose id is not usable.
   - **Kind names**: one `graph_get_many` round over the distinct `node_type_id`s that are uuids
     (`/node-types/<id>`, as `dossier._kind_and_fields` reads them). At most 20 distinct kinds are named; the rest,
     and things without a kind, fall under one kind «Weitere Einträge».
   - **Relation**: from the edges between the starting thing and the row (`relation`). Several relations are joined
     («Vermieterin, Partei»). Without edges in the answer, the rows go without a relation.
   - Never `graph_export`, `graph_edges`, `graph_nodes` or `graph_node_types` (POST-03).
   - Failures: `KnowledgeGraphDisabled` and a 404 read as "nothing related". Any other failure raises an
     `Unavailable` of its own, which the page shows as one line (item 3); the rest of the dossier still opens.
2. **The tabs.** The dossier's tabs become «Überblick», then one tab per kind with its count (by count, highest
   first, then by name), then «Dokumente» with its count. A kind's tab is `?art=<key>`: the type's uuid, or `ohne`
   for «Weitere Einträge». An `art` the thing does not have shows the «Überblick». The key is never echoed into the
   page as text. A tab shows the kind's name **in the plural** with its count, «Mandate 7», «Dokumente 12»
   (decision D2).
3. **A kind's list** (B-03), through `limit.paged`:
   - rows: the thing's name, a link to its dossier (`url_for('brain_views.dossier', …)`), with the relation under it
     in the muted style, each row marked `data-bv-row`;
   - **sort**: «Name A–Z» (the default) and «Name Z–A», plus «Beziehung» when relations exist (`?sort=`);
   - **filter**: one text box «Filtern …» over the names (`?filter=`, case and spacing ignored, at most 100
     characters, shown back escaped);
   - **pages** of 50 rows (`?seite=`), with «Weitere 50» / page links; a page out of range shows the last one;
   - a GET form, so it works without JavaScript;
   - the empty filter result: «Kein Eintrag passt zu diesem Filter.»; no related things at all: no kind tabs, and on
     the «Überblick» nothing new;
   - neighbours that cannot be read: one line on the page «Verbundene Einträge sind gerade nicht verfügbar.», and the
     facts and documents as before.
4. **«Dokumente»** gets the same list treatment (sort by name, filter, pages of 50, rows marked `data-bv-row`). A
   document's open link stays its position in the **whole** list sorted by name (`/dossier/<id>/dokument/<n>`, as
   PMVP-30a built it). Sorting, filtering or paging never changes which document a position opens.
5. **Nothing drawn beyond the thing itself.** The «Überblick» keeps one `data-bv-node` (the thing). PMVP-34's test
   must pass on every new tab and page; it follows the tab links by itself.
6. **The card**: the dossier's operation gains the tabs, the lists and their arguments; lines for PRE-01 (one
   starting point), POST-01 (counts open lists; with PMVP-34's test as evidence), POST-03 (one per-thing read); the
   new file in `source_paths`; `boundaries.yaml`.

## Rules

1. **As the person, per thing.** All reads go through the web app's one client, which signs as the person signed in
   (as in PMVP-30a). One neighbours read per page view, and one round of kind reads.
2. **What the Core did not hand over does not exist.** No count, name or relation comes from anywhere else. A
   related thing the person may not see is neither counted nor listed. The Core decides that; nothing here filters
   by rights.
3. **Text stays text**; every address from `url_for` (FU-34); the filter text and the sort key are never put into
   an href unescaped and never into a log line.
4. **No grant.** The lists link to dossiers, not to documents. The documents' open links stay as PMVP-30a built them.
5. **Logs**: counts and error types only; never a name, a relation, a filter text or an id beyond the route's own.
6. The switch, the sign-in and the 404 for a hidden thing stay as PMVP-30a built them.

## Decisions (coordinator, 2026-11-22)

- **D1, tabs only, as the reference.** The «Überblick» draws no related things (02-dossier draws none). The related
  things are counted in the tabs and listed behind them. The neighbourhood view with circles (04-umgebung) is the
  plan's «Umgebung», which follows only if G3 is met early.
- **D2, kind names in the plural** («Mandate 7», «Dokumente 12»), as German interfaces show them. The Brain has no
  plural (a node type has a name and a description only), and no rule makes German plurals («Vertrag» →
  «Verträge»). So the plurals come from **one table** in `brain_views/related.py`: the kinds of the legal pack
  (`builder/packs/legal/pack.json`) and of the pilot's tenant (Knovas GmbH; its kind names are in KnowledgeBase
  `docs/ModernDocs/client-zero/ontology/`, read with `git show`, and they are English). A kind not in the table
  keeps the name the Brain gives it. List in your notes the pilot's kinds and the plurals you chose, for the
  coordinator to check.
- **D3, at most 20 named kinds.** One round of 20 reads is bounded; a thing with more kinds is unusual.
- **Lists are exempt from the 25 cap** (coordinator): they are paged at 50 (PMVP-34's D1).
- **D4, the list's sort and filter run over what was read**, on the Platform; the Core has no paged neighbours
  read. For a hub with thousands of neighbours the page reads them all each time. That is accepted for the MVP and
  noted on the card.

## Your tests (`P/tests/test_brain_views_related_*.py`)

With the shared `FakeGraphApi` (its `neighbours` map; extend it only in your own test file):

- Signed out 302; switch off 404; a hidden or missing thing 404 (as in PMVP-30a); without accounts 404.
- Counts per kind; the tab order; a kind in the table shown in its plural, one outside it with its own name; «Weitere Einträge» for things without a kind and beyond 20 kinds; the starting
  thing never counted.
- A kind's list: sorted A–Z and Z–A; the filter; page 2; a page out of range; an unknown `art` shows the
  «Überblick»; a name, relation or filter holding `<script>` comes out as text.
- Relations from the edges; none without edges.
- «Dokumente» sorted, filtered and paged; position *n* opens the same document whatever the sort, the filter and the
  page.
- A neighbours read that fails: the dossier opens with the one line; a 404 or a switched-off graph: no tabs.
- No whole-tenant call (the fake raises on them); every link on the pages from `url_for`.
- Logs at DEBUG (`web_interface`, `brain_views`, set explicitly: FU-46): no name, relation or filter text.
- PMVP-34's `test_brain_views_node_limit.py` green with your pages in it.

## Files you may change

`P/src/brain_views/related.py` (new); `P/src/web_interface/brain_views_routes.py`;
`P/src/web_interface/templates/dossier.html`; `P/src/web_interface/static/css/brain_views.css` (tokens only);
`P/tests/test_brain_views_related_*.py` (new); the card, its page, `docs/components/boundaries.yaml`;
`docs/platform-mvp/management/brain-views.md` and its row in `management/README.md`; this order's "Builder's notes".

## Files you must not change

The frozen files, `P/tests/conftest.py`, `P/src/brain_views/limit.py` (PMVP-34's: use it; if it lacks something,
stop and say what), `P/src/brain_views/dossier.py` (except a shared helper moved without changing its behaviour; say
which), the parts, `_sidebar.html`, `app.py`, `platform_settings.py`, `knovas_client.py` (if `graph_neighbors` lacks
something, stop and say what), everything under `P/src/assistant/` and `P/src/identity/`.

## Done when

1. Your tests green; `tests/test_brain_views_*.py` (PMVP-30a's and PMVP-34's included), `tests/test_shell_*.py` and
   `tests/test_pmvp11_navigation_acceptance.py` green; the frozen check all OK. **Do not run the whole suite.** Own
   test database `knovas_platform_test_pmvp30b`.
2. Screenshots, outside the repository (say where), at 1440 and 390 px, beside `02-dossier.png`: the dossier with
   its kind tabs, one kind's list filtered, and «Dokumente» on page 2. Nothing scrolls sideways; a tab row that does
   not fit scrolls in itself.
3. Card checks clean.
4. `brain-views.md`: what a person can do now; PMVP-30b's row.
5. Commits `PMVP-30b: …`, ending with the co-author line; `feat/pmvp-30b` pushed. No pull request.

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)
