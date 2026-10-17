# Design canvas

This branch is the Knovas Platform with its visual design taken out.
Everything still works (search, preview, Cortex, Experiments, the console),
but every page renders in the browser's default look. It is a blank canvas
for a new design.

## What was taken out

- **The four stylesheets**, about 5,900 lines of tokens, fonts, layout and
  components, are now nearly empty:
  - `static/css/style.css` (every page)
  - `static/css/admin.css` (console)
  - `static/css/experiments.css` (Experiments)
  - `static/css/ontology.css` (Cortex)
- **Cosmetic inline styles** (spacing) in `static/js/experiments_detail.js`
  and `static/js/experiments_manage.js`.
- **A broken link:** `/account/password` pointed at a `login.css` that never
  existed. It now loads `style.css` like every other page.

Markup, scripts, routes and server code are the same as on `main`. The old
design is still in git:

```bash
git show main:KnovasPlatform/components/docbridge_integration/src/web_interface/static/css/style.css
git diff main -- KnovasPlatform/components/docbridge_integration/src/web_interface
```

## What was kept: behaviour

Each stylesheet starts with a short **Behaviour** block, followed by
`Your design starts here`. These rules don't carry any design. They are what
the scripts need to show, hide and size things. You can restyle them, but
keep what each one does. Where they need a colour, they use CSS system
colours (`Canvas`, `CanvasText`, `GrayText`, `Mark`, `Highlight`), which are
the browser's own defaults.

| Rule | File | Why it is there |
|------|------|-----------------|
| `[hidden] { display: none !important }` | style.css | Scripts show and hide elements with the `hidden` attribute. Without `!important`, any `display` you give a class would keep hidden elements on screen. |
| `.visually-hidden`, `.kx-visually-hidden` | style.css, experiments.css | Labels that are for screen readers only. |
| `.preview-body.is-pdf iframe` | style.css | PDFs in the search preview. Without a size, the browser draws the iframe at 300 × 150 px. |
| `.passage-hit`, `.preview-body .is-active` | style.css | Highlights findings in the preview text and marks the one the arrows jumped to. |
| `.toast-container` | style.css | Toasts float above the page instead of landing below the last result. |
| `.ontology-stage-body`, `#graphContainer` | ontology.css | Cytoscape draws into a box with a real size. That box's height is the graph's. |
| `.graph-add-type`, `#graphEmpty`, `.connect-handle` | ontology.css | Controls that sit over the graph canvas. They have to stay above it to be clickable. |
| `.ontology-drawer`, `.open`, `.doc-frame` | ontology.css | The entity and document drawers slide over the graph. `ontology.js` measures the open drawer. |
| `.doc-fields-drawer` | admin.css | The fields drawer on `/admin/documents` opens over the table instead of below it. |
| chart rules, `--kx-series-1…8` | experiments.css | The charts are SVG. Without these rules their lines fill in black and the hover targets cover the plot. The palette pairs each line with its legend swatch. |

## Where things are

Everything is under `KnovasPlatform/components/docbridge_integration/src/web_interface/`.

| Page | Template | Stylesheets | Scripts |
|------|----------|-------------|---------|
| `/login` | `login.html` | style | — |
| `/account/password` | `account_password.html` | style | — |
| `/` (search) | `index.html` | style | `markdown.js`, `app.js`, `doc_fields.js` |
| `/ontology` (Cortex) | `ontology.html` | style, ontology | `vendor/cytoscape.min.js`, `ontology_connect.js`, `ontology.js` |
| `/settings` | `settings.html` | style | inline |
| `/experiments` | `experiments_list.html` | style, experiments | `markdown.js`, `experiments_common.js`, `experiments_list.js` |
| `/experiments/<KEY>` | `experiments_detail.html` | style, experiments | `markdown.js`, `experiments_common.js`, `experiments_detail.js` |
| `/experiments/verwaltung` | `experiments_manage.html` | style, experiments | `markdown.js`, `experiments_common.js`, `experiments_manage.js` |
| `/admin/people` | `admin_people.html` | style, admin | `admin_people.js` |
| `/admin/access-groups` | `admin_access_groups.html` | style, admin | — |
| `/admin/approvals` | `admin_approvals.html` | style, admin | — |
| `/admin/documents` | `admin_documents.html` | style, admin | `admin_documents.js` |
| `/admin/doc-fields` | `admin_doc_fields.html` | style, admin | `admin_doc_fields.js` |
| `/admin/ingestion` | `admin_ingestion.html` | style, admin | `admin_ingestion.js` |
| `/admin/system` | `admin_system.html` | style, admin | — |

Shared pieces: `templates/_sidebar.html` (the navigation on every page) and
`templates/_admin_tabs.html` (the console's tabs).

Assets: `static/img/` holds the logo, the mark and the favicon. `static/fonts/`
holds IBM Plex Sans and Mono (OFL). Nothing references the fonts any more:
use them with `@font-face`, or replace them.

## Ground rules

1. **Keep the ids.** Scripts find elements with `getElementById`. If you
   rename an id, change the script in the same commit.
2. **Script-built markup has classes too.** A lot of the UI is built in
   JavaScript: result cards, the preview, all of Experiments, the console
   tables. Search the scripts for `class` and `className` to find those
   hooks. These are the state classes the scripts toggle:
   `.active`, `.is-active`, `.is-selected`, `.selected`, `.open`, `.is-pdf`,
   `.is-unlocatable`, `.kx-refreshing`, `.kx-tooltip--flip`,
   `.document-thumb--loading`, `.document-thumb--icon`,
   `.document-card--experiment`, and
   `.status-dot.is-online / is-offline / is-pending / is-unknown`. The
   scripts also set `aria-busy`, `aria-current`, `aria-expanded` and
   `aria-selected`, which you can style with attribute selectors.
3. **Show and hide with `hidden`,** not with classes. The global rule in
   `style.css` makes sure it always wins.
4. **Self-hosted only.** Clients run the Platform on their own servers, often
   without internet access. That means no CDN fonts, stylesheets or images.
   Put fonts in `static/fonts/` and reference them from `style.css` with
   `url(../fonts/...)`. A test keeps `@import` and `url()` out of
   `experiments.css`.
5. **Cortex's graph is styled in JavaScript, not CSS.** Node and edge colours,
   shapes and labels are in the `style:` list of `cytoscape({...})` in
   `static/js/ontology.js`.
6. **The UI text is German product copy.** Restyle it, but don't rewrite it.
7. **Cache busting is built in.** Stylesheets are linked with
   `?v=<newest change>`, so a reload picks up your edits.

## Seeing it

- **Full stack with the mock API (Docker):** follow
  [KnovasPlatform/docs/demo.md](../../../../docs/demo.md).
- **Tests:** run `pytest` in `KnovasPlatform/components/docbridge_integration`.

## Come back with the new design

Two layout regressions (e2e-ui-14) were tests of the old CSS, so this branch
drops them. The new design should solve both again, and their tests should
come back with it. The originals are in `tests/test_experiments_frontend.py`
on `main`, under `TestLayoutRegressions`.

- On a phone, titles in the Experiments list must not break mid-word. The
  old CSS gave `.kx-title-cell` a `min-width: 12rem` and gave
  `.kx-title-cell strong` `overflow-wrap: break-word`, never `anywhere`.
- Below 900 px, the sidebar navigation must scroll inside itself instead of
  widening the page. The old CSS gave `.app-nav` `overflow-x: auto;
  min-width: 0` and gave `.app-nav-item` `flex: 0 0 auto`.
