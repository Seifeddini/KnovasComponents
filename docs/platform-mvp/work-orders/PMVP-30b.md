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

Builder session of 2026-11-22 (builder B, AGENT-claude-code). `feat/pmvp-30b` from `feat/pmvp-34` (`db060a06`, not
yet integrated); `origin/feat/platform-mvp` merged in before the push (see the commits).

### What was built

| File | What |
|---|---|
| `src/brain_views/related.py` (new) | `read_related(client, node_id) -> Related(kinds, total)`; `Kind(key, name, things)`, `Thing(id, name, relation)`; `Unavailable`; the plural table `PLURALS` and `plural()`; the lists' helpers `sort_key`, `filter_text`, `matches`, `sort_choice`, `arrange` |
| `src/web_interface/brain_views_routes.py` | the dossier route reads the related things after the node (one neighbours read, one round of kind reads); the tabs; `?art=`, `?sort=`, `?filter=`, `?seite=`; `_listing` builds one list (form fields, page, page links, all through `url_for`) for a kind and for «Dokumente» |
| `templates/dossier.html` | kind tabs (from the routes); a kind's list and «Dokumente» as lists with a GET form (filter, sort, «Anwenden») and pages («Zurück», «Weitere 50», «51–100 von 120 · Seite 2 von 3»); the one line when the neighbours cannot be read; rows `data-bv-row`, the thing `data-bv-node` |
| `static/css/brain_views.css` | the form, a related thing's row, the pages; tokens only |
| `tests/test_brain_views_related_read.py` (24), `tests/test_brain_views_related_page.py` (36) | below |
| card, its page, `boundaries.yaml` | R-3; the dossier operation's name with the tabs, lists and arguments; evidence on `*/N-1` (PRE-01), `*/P-1` (POST-01), `*/P-3` (POST-03) and the route's N-1; new P-5 (what is counted and listed, the failures) and P-6 (text stays text, addresses, logs); `uses` with the neighbours read; a `hides` line for the plural table; a review entry on P-5 (CR-R1, D4) |

### What I decided, and why (the verifier should look here first)

1. **Read order.** The node first (`read_dossier`, PMVP-30a's reads unchanged), then `read_related`. A hidden or
   missing node answers 404 before its neighbours are asked (tested). The related read is skipped by nothing: every
   tab shows the kind tabs with their counts, so every page view makes the one neighbours read.
2. **Which kinds are named.** The 20 kinds with the most things (then by type id), one `graph_get_many` round of
   `/node-types/<id>`. A kind whose read fails, a type id that is not a uuid (`euo:Person`) and things without a kind
   go under «Weitere Einträge» (key `ohne`).
3. **«Weitere Einträge» is the last kind tab**, whatever its count. The order says "by count, highest first, then by
   name"; a catch-all as the first tab («Weitere Einträge 140» before «Mandate 7») reads as a kind of its own. The
   named kinds follow the order exactly. One line to change if the coordinator wants it sorted like the others.
4. **Relations** come only from edges whose two ends are the starting thing and the row (in any order; the Core's
   induced edges among the neighbours are ignored); several are joined and sorted («Kontakt, Partei»).
5. **A row one hop away** means `hop == 1` exactly (a missing hop or `True` is left out); the start (hop 0, or the
   same id in another case) and a row whose id would not pass the dossier's id check are left out; each id once.
6. **The lists' state in the address**: `?art=<type uuid|ohne>` (only a key this thing has; any other shows the
   «Überblick» and is not echoed), `?sort=name|name-ab|beziehung` (`beziehung` only when the kind has relations;
   anything else is `name`), `?filter=` (control characters dropped, spacing collapsed, cut at 100), `?seite=`
   (`limit.page_number`, then `limit.paged` clamps). The page links carry only those cleaned values, through
   `url_for`. `?tab=dokumente` wins over `?art=`.
7. **Sorting** ignores case, accents and spacing («Ärzte» beside «Arzt»; `ß` as `ss`); by relation: things with a
   relation first, by relation, then by name; things without one last.
8. **«Dokumente»**: the same list; a document's «Öffnen» stays `/dossier/<id>/dokument/<n>`, n its place in the
   whole list by name (PMVP-30a's), so the sort, the filter and the page never change which document opens (tested
   over seven combinations, and the click on n = 120 asks the Core about the 120th document).
9. **The counts are not `data-bv-more`**: they are the tabs' counts, drawn by `parts.tabs` (the parts may not be
   changed and take no attribute). Nothing on these pages is drawn beyond the thing (`data-bv-node` 1 on every tab),
   so there is no "rest" next to a drawing; the frozen test follows the tab links by itself.
10. **No grant**: the lists link to dossiers; the documents' links are PMVP-30a's.
11. **Logs**: one DEBUG line with the two counts; one WARNING with the error's type when the neighbours read fails.
12. `dossier.py` is unchanged; `related.py` imports `UNNAMED_NODE`, `valid_node_id` and `valid_type_id` from it.

### The plurals (D2), for the coordinator to check

The table is keyed by the kind's name as the Brain gives it (case and spacing ignored).

- **Legal pack** (`builder/packs/legal/pack.json`, by key and by German label): Matter / Mandat → Mandate;
  Proceeding / Verfahren → Verfahren; Court / Gericht → Gerichte; ProceduralDeadline / Prozessuale Frist →
  Prozessuale Fristen; Submission / Rechtsschrift → Rechtsschriften; CourtDecision / Urteil → Urteile;
  PowerOfAttorney / Vollmacht → Vollmachten; Forderung → Forderungen.
- **The reference screens' words**: Mandant → Mandanten, Vertrag → Verträge, Partei → Parteien, Dokument →
  Dokumente, Organisation → Organisationen (Person → Personen is in the pilot's list).
- **Knovas GmbH** (KnowledgeBase `origin/master` `docs/ModernDocs/client-zero/ontology/generated/node-types.json`,
  81 kinds, English names; the plural from each kind's `display_de`): Product → Produkte; PlatformTier →
  Plattform-Stufen; ApiServiceGroup → API-Dienstgruppen; Capability → Fähigkeiten; BoundedContext →
  Software-Bereiche; System → Systeme; Interface → Schnittstellen; Interaction → Komponentenaufrufe; Component →
  Software-Komponenten; ComponentContract → Komponentenverträge; Invariant → Goldene Invarianten; FeaturePlan →
  Feature-Pläne; Phase → Phasen; Gate → Gates; Milestone → Meilensteine; Goal → Ziele; KeyResult →
  Schlüsselergebnisse; Metric → Kennzahlen; MetricSample → Messwerte; Decision → Entscheidungen; Risk → Risiken;
  Hypothesis → Hypothesen; Experiment → Experimente; Result → Ergebnisse; PreMortem → Pre-Mortems; Tripwire →
  Auslöseschwellen; Vision → Visionen; Bet → Wetten; Forecast → Prognosen; **Claim → Aussagen**; Question →
  Prüffragen; SpecItem → Anforderungen; WorkItem → Aufgaben; Idea → Ideen; Issue → Tickets; IssueProject →
  Jira-Projekte; Process → Prozesse; ProcessStep → Prozessschritte; ProcessInstance → Prozessinstanzen; Event →
  Ereignisse; RunEvent → Laufereignisse; Function → Unternehmensfunktionen; Role → Rollen; Person → Personen; Agent
  → KI-Agenten; Market → Märkte; Segment → Kundensegmente; Persona → Personas; EvidenceRecord → Evidenzeinträge;
  Channel → Vertriebskanäle; Campaign → Kampagnen; MarketingAsset → Marketing-Materialien; Lead → Leads; Account →
  Kundenorganisationen; Opportunity → Verkaufschancen; Quote → Angebote; Contract → Verträge; Customer →
  Kundenbeziehungen; Record → Dokumentbeschreibungen; Repository → Code-Repositorys; SourceFile → Quelldateien;
  Symbol → Code-Symbole; Commit → Commits; TestRun → Testläufe; AlloyCheck → Alloy-Prüfungen; GlossaryTerm →
  Glossarbegriffe; Release → Releases; Spine → Strukturachsen; SpineLevel → Strukturebenen; Company → Unternehmen;
  ValueStream → Wertströme; Deployment → Deployments; Service → Dienste; Job → Geplante Jobs; Environment →
  Umgebungen; DataStore → Datenspeicher; DataSet → Datenbestände; Setting → Einstellungen; Workflow → CI-Workflows;
  WorkflowJob → CI-Jobs; Library → Fremdbibliotheken.
- **One clash**: the legal pack's key `Claim` means «Forderung», Knovas GmbH's `Claim` means «Aussage». One table
  keyed by name cannot hold both; it takes the pilot's reading («Aussagen»). A law-firm tenant whose kind is named
  `Claim` would show «Aussagen»; naming it «Forderung» in the Core shows «Forderungen».
- Where `display_de` has a bracket («Kunde (Organisation)», «Kunde (Beziehung)», «Dokument (Beschreibung)»), the
  plural is a compound so the tabs stay distinct; «Record» → «Dokumentbeschreibungen» is not «Dokumente», which is
  the documents' tab.

### Stopped on

Nothing. `limit.py` had what the lists need (`paged`, `page_number`, the `Page` properties); `graph_neighbors` gives
the rows and edges.

### Checks

- **Tests** (own database `knovas_platform_test_pmvpB`, as the coordinator set it for builder B, not
  `…_pmvp30b`; one process): `test_brain_views_related_read.py` 24 and `test_brain_views_related_page.py` 36
  passed. With the frozen `test_pmvp34_view_limit_acceptance.py` (17, unchanged; it now also crawls the kind tabs
  and a kind's page 2), `tests/test_brain_views_*.py` (PMVP-30a's 70 and PMVP-34's 28 unchanged and green),
  `tests/test_shell_*.py` and `tests/test_pmvp11_navigation_acceptance.py`: **618 passed, 0 failed**. No existing
  test was changed. The whole suite was not run.
- **Frozen check**: all eleven OK.
- **Card**: `card check` valid, 0 errors, 0 warnings; `card render --check` 21 cards, 0 not current; `map-check
  --scope touched`, base `origin/feat/platform-mvp`: problems 0. In scope: the earlier `no_contract` on
  `create_brain_views_blueprint` (review entry), and **`over_budget` on L2-PLATFORM-BRAIN-VIEWS: 17,839 tokens,
  budget 16,000** (report mode; the card's code grew with `related.py`, mostly the plural table). PMVP-32 adds more;
  if it matters, the process line could get a card of its own, which the coordinator decides.
- **In the browser** (headless Chrome, the real app, a fake Core with the reference screen's invented firm: 7
  Mandate, 34 Verträge, 6 Personen, 412 documents; 1440 and 390 px): one drawn thing on every page; the tabs
  «Überblick, Verträge 34, Mandate 7, Personen 6, Dokumente 412»; nothing scrolls sideways (the tab row scrolls in
  itself at 390); no failed request; lowest text contrast 4.76 to 1; a focus ring on tabs, links, the filter box,
  the sort choice and «Anwenden». Screenshots, outside the repository:
  `C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\builderB\shots30b\`:
  `dossier-*.png` (the kind tabs), `vertraege-gefiltert-*.png` (the Verträge filtered on «seefeld», sorted by
  relation), `dokumente-seite2-*.png` («Dokumente» on page 2, «51–100 von 412 · Seite 2 von 9», «Zurück»,
  «Weitere 50»), and `compare-*-*.png` beside `02-dossier.png`; `facts.json`. Tools: `serve.py`, `shoot.py` one
  folder up.
- **Commits**: `35c2ae34` (the code, tests, card), `0ce3b13f` (the management page and its row; its message names
  the notes, which came in the next commit), the merge of `origin/feat/platform-mvp` (`8485dfdb`, no conflict), and
  the notes.
