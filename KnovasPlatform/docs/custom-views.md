# Custom views (Eigene Ansichten): operator's page

A custom view is a small web page that an installation's operator puts into a folder on the server. The Platform
shows it on the page **Eigene Ansichten** inside a locked frame, and the view reads the Knowledge Graph (the Brain)
through one narrow channel, as the signed-in user. The Platform itself ships no view. This page is for the person
who runs a Platform installation and for whoever writes a view.

## 1. What a custom view is, and the risk in plain words

**View files are trusted code.** A view is HTML, JavaScript and CSS that you chose to run inside your Platform
page. The frame it runs in protects against *script that arrives in data* (a title or a description that comes from
the Brain, or from a source behind it, and carries markup). It does **not** protect against the author of the view:
a view's own script can send out whatever it reads, for instance by opening a link that carries the data in its
address, or by an address the manifest lists under `links.hosts`. Treat a view like any program you install:
read it, or trust who wrote it.

Because of that:

- The feature is **off by default** (`CUSTOM_VIEWS_ENABLED=false`).
- It **must stay off at client installations** until an administrator can approve view files before they run
  (the approval step is tracked as SS-621). Until then, switch it on only where the people who put files into the
  folder are the same people who run the installation.

What the frame and the bridge do guarantee, whatever the view contains:

- The view runs in an iframe with an opaque origin: no cookies, no access to the Platform page or its storage.
- A Content-Security-Policy in the view's own document allows only the scripts and styles the Platform put there
  (by a one-time nonce), no network (`default-src 'none'`), images and fonts only as `data:`, no forms, no `<base>`,
  and Trusted Types for scripts (a string cannot be turned into markup).
- The view talks to the Platform through one `MessageChannel` and nothing else (section 5).
- Every read is limited to the node types and facts the manifest names, and runs with the rights of the person who
  is signed in.
- Nothing is written to the Brain.

## 2. The switch and the folder

These settings are read from `config.yaml` or the environment only. They are not on the settings page.

| Setting | Environment | Default |
| --- | --- | --- |
| `custom_views.enabled` | `CUSTOM_VIEWS_ENABLED` | `false` |
| `custom_views.dir` | `CUSTOM_VIEWS_DIR` | `/mnt/custom-views` |
| `custom_views.facts_per_minute` | `CUSTOM_VIEWS_FACTS_PER_MINUTE` | `60` |

The feature is on only when **all three** hold: the switch is true, user accounts are on, and the ontology source is
the Knowledge Graph (`ONTOLOGY_SOURCE=graph`). Otherwise there is no menu entry and every custom-view address
answers 404.

With Docker Compose (`docker-compose.yml`, service `docbridge-web`) the container always sees the folder at
`/mnt/custom-views`, read-only. Set, in `knovas.env`:

```bash
CUSTOM_VIEWS_ENABLED=true
CUSTOM_VIEWS_PATH=/srv/knovas/custom-views     # the folder on the host; one sub-folder per view
CUSTOM_VIEWS_FACTS_PER_MINUTE=60               # optional
```

Then run `scripts/setup.sh` and restart the Platform (`docker compose --env-file knovas.env up -d docbridge-web`).
How the values reach the container:

- `scripts/setup.sh` runs `scripts/lib/expand_knovas_env.sh`, which writes every key of `knovas.env` that it does not
  interpret itself (the list `CONSUMED_KEYS`, `expand_knovas_env.sh:156`) and that does not start with `RC_` into
  `KnovasPlatform/.env.generated` (`passthrough_overrides`, `expand_knovas_env.sh:167` and `:285`). The `docbridge-web`
  service loads that file through `env_file`. So `CUSTOM_VIEWS_ENABLED` and `CUSTOM_VIEWS_FACTS_PER_MINUTE` arrive
  with no further step. (`CUSTOM_VIEWS_PATH` is copied too; the container never reads it.)
  `.env.generated` is rewritten on every `setup.sh`: never edit it by hand.
- `CUSTOM_VIEWS_FACTS_PER_MINUTE` reaches the container only this way: `docker-compose.yml` does not name it.
- `CUSTOM_VIEWS_ENABLED` and `CUSTOM_VIEWS_DIR` are also named in the `environment:` block of `docbridge-web`, and
  `environment:` wins over `env_file`. The switch there is `${CUSTOM_VIEWS_ENABLED:-false}`, which compose fills from
  its own environment: the shell, or `--env-file knovas.env`. The one place to set the switch is therefore
  `knovas.env`, and the Platform must be started with `docker compose --env-file knovas.env ...` (as every command in
  this documentation does). Started without `--env-file`, compose does not see `knovas.env`, uses `false`, and
  that overrides the value in `.env.generated`: the feature stays off. That fails in the safe direction, but it can
  puzzle you; if the menu entry is missing, check this first.
- `CUSTOM_VIEWS_PATH` is used only by compose, for the mount, so it too needs `--env-file knovas.env`; without it
  the stand-in folder below is mounted and there are no views.

If `CUSTOM_VIEWS_PATH` is not set, compose mounts `./data/no-custom-views`. The repository does not create it: Docker
makes an empty folder there on the first start (owned by root). Inside the container it is read-only and empty, so
there are no views, and the Platform accepts it. It is a stand-in, not a place to put views.

Folder rules (a folder that breaks one is refused as a whole, and the administrator sees why):

- it must exist, must not be a link (symlink or junction), and must be a directory;
- it must not lie inside the documents folder and must not contain it;
- the Platform must not be able to write to it (the read-only mount takes care of that). This is not checked on
  Windows.

Layout: one sub-folder per view; the folder name is the view id and must match `^[a-z0-9][a-z0-9-]{0,39}$`.

```
/mnt/custom-views/
  my-board/
    view.json
    index.html
    board.js
    board.css
```

File rules: only the endings `.html`, `.js` and `.css`; no links; every file must be UTF-8. The folder is re-read
at most every 30 seconds, so a change shows up within that time without a restart. A view is identified by a
fingerprint of its files; a change is logged.

## 3. The manifest (`view.json`)

| Field | Meaning |
| --- | --- |
| `format` | Always `1`. |
| `title` | `{"de": "...", "en": "..."}`, each up to 120 characters. Both are required. |
| `description` | Optional, the same shape, up to 500 characters. |
| `order` | Optional number; views are listed by `order`, then by title. |
| `roles` | Who may open the view. `["*"]` is every signed-in user, `[]` is nobody. Anything else is a list of role names. |
| `entry` | The HTML fragment, for example `index.html`. |
| `scripts` | Optional list of `.js` files, run in this order after the bridge. |
| `styles` | Optional list of `.css` files. |
| `reads` | Required: an object that maps each node type the view may read to the list of fact names it may read (at most 40 facts a type). |
| `links.hosts` | Optional list of host names (lower case, no port) that `knovas.open` may open. |

A bad manifest makes only that view invalid. The reason names the field and never quotes the file.

The entry is a *fragment*: no `<html>`, `<head>`, `<body>`, `<script>`, `<link>`, `<style>`, `<meta>`, `<base>`,
`<iframe>`, `<form>`, `<object>`, `<embed>`, `<textarea>`, `<title>`, `<noscript>` and similar. It may not hold event
attributes (`onclick` and the like), a `style` attribute, or an address (`src`, `href`, `action`, `srcset`, ...) that is
not a `#` anchor, except a `data:` address on `img`. The Platform refuses such an entry (the view is invalid), and
refuses markup it cannot read the way a browser does (unclosed `svg`, `math` or `template`, CDATA, declarations).
Put scripts and styles in their own files.

## 4. Rules for authors

1. **Draw data with `textContent`.** Everything that comes from the Brain is text, never markup. Build the page with
   `document.createElement`, `textContent`, `appendChild`.
2. **No `innerHTML`.** The frame enforces Trusted Types: assigning a string to `innerHTML`, `outerHTML`,
   `insertAdjacentHTML`, `document.write` or `srcdoc` throws.
3. **No inline styles in markup, no style attribute.** Style with classes from your `.css` file, or set single
   properties from script through the style object (`element.style.left = "40%"`, which is allowed because it does
   not parse markup). A timeline bar or a progress width works that way.
4. **No `href="#..."` anchors.** The view runs in a `srcdoc` frame, which resolves `#name` against the address of the
   Platform page, so the click leaves the frame's page instead of scrolling. Use a button and `scrollIntoView`.
5. **No network from the view.** `fetch`, `XMLHttpRequest`, images and fonts from outside are blocked. Use
   `knovas.read`; to send a person to another site, use `knovas.open`.
6. A view must work with an empty answer and with an answer that says it is incomplete (section 6).

### Four JavaScript constructs that the Platform rewrites

The Platform puts each script into a `<script>` element of the view's document. To keep a script from ending its
element early, it rewrites four constructs. Avoid them if you can; if you need them, know what changes.

1. A regular expression with the `u` or `v` flag that contains `<!--`: the escaping makes it a SyntaxError.
2. An old-style HTML-like comment, `<!--`, used as a comment in code: it becomes a SyntaxError.
3. `</script` used as code, for example `x</script/g` (a less-than followed by a regular expression literal): it
   becomes a SyntaxError.
4. `String.raw` and tagged templates whose text holds `</script` or `<!--`: they now see an extra backslash in the
   raw text. This one is **silent**; the other three fail loudly.

Ordinary strings, ordinary templates and normal regular expressions are not affected.

## 5. The bridge calls

The Platform provides one object, `window.knovas`, before your script runs. It is frozen. It has three calls, `read`,
`open` and `resize`, and one helper, `ready`.

- `knovas.read(query)` returns a promise of the answer (section 6). It rejects with an `Error` whose message is
  German text from the Platform and whose `status` is the HTTP status (for example 403 when the query leaves the
  manifest, 429 when the budget is used up or another read of yours is still running).
- `knovas.open(url)` asks the page to open an address in a new tab. It works only for `https` addresses without user
  name, password or port, to a host that `links.hosts` names exactly. Anything else rejects.
- `knovas.resize(height)` tells the page how tall the frame should be (clamped to a range); it returns nothing.
- `knovas.ready(callback)` runs the callback once the channel to the page is open. Call `read` and `open` from there
  or later; calls made earlier wait.

## 6. The read query and the answer

```js
knovas.read({
  type: "Bet",                               // required; a type named in "reads"
  ids: ["<node id>", ...],                   // optional: only these nodes
  linked: {to: ["<node id>"], relation: "...", direction: "out"},   // optional: nodes linked to these
  where: {status: ["open", "active"]},       // optional: facts that must equal one of the values
  facts: ["status", "priority"],             // optional: the facts to return (named in "reads")
  links: [{relation: "...", direction: "out"}],   // optional: links to return
  limit: 50                                  // optional, at most 200
})
```

The type, every fact in `facts` and every fact in `where` must be in `reads`; otherwise the read is refused (403)
and the message does not say which part. A query has at most 40 facts and 10 link specs.

The answer:

```js
{
  nodes: [{id, name, type, description, facts: {status: "..."}, links: [...]}],
  total: 12,              // how many nodes matched
  links_hidden: 0,        // links left out because the node at the other end has a type "reads" does not name
  complete: true,
  incomplete_why: [],     // "node_cap", "facts_unreadable" or "topology_partial"
  read_at: "2026-11-13T09:00:00+00:00"
}
```

A link is returned only when the node at its other end has a type the manifest names; the other ends' facts are
never returned. Never treat an answer as complete without looking at `complete`.

## 7. Limits

| What | Limit |
| --- | --- |
| One file | 512 KB |
| One view (all its files) | 2 MB |
| Views in the folder | 50 |
| View id | `^[a-z0-9][a-z0-9-]{0,39}$` |
| File endings | `.html`, `.js`, `.css` |
| Folder re-read | at most every 30 seconds |
| Nodes in one read | 200 |
| Read budget | 60 facts a minute per process (`custom_views.facts_per_minute`) |
| Reads at the same time | one per user |
| One query | at most 40 facts and 10 link specs |

The budget counts a node's facts only when they are not cached yet; a read that needs no facts takes nothing. When
it is used up, the read is refused with 429 and a wait time.

## 8. Who sees what

Every signed-in user may open every view whose `roles` allow them (`["*"]` is everyone; a finer rule comes with
role-based access later, SS-620). The *content* a view shows is whatever the Brain gives that user: a view cannot
read what the person could not.

Administrators see the page **Eigene Ansichten** as soon as the views folder holds any view folder, valid or not.

## 9. Where the audit entries are

Every read a view makes is written to the audit log of the Platform's account database with the action
**`custom_view.read`**: who read, which view, the node type asked for, how many nodes came back, whether the answer
was complete and the outcome. Reads that are refused (outside the manifest, `forbidden`) are written too.

## 10. How to check a view

1. Put the folder in place (section 2) and wait up to 30 seconds.
2. Sign in as an administrator and open **Eigene Ansichten**. Open **Dateien und Prüfung**: **Dateien** lists what the
   Platform read (with fingerprints and times), **Ungültige Ansichten** lists each refused view with the reason in
   German (for example, a manifest field that is wrong, an entry that holds a refused element, a file that is too
   large). If it says **Alle Ansichten im Ordner sind gültig.**, every view loaded.
3. Fix the file, wait for the re-read, and look again. If the folder itself is refused, the reason is shown for the
   folder.
4. The server log notes when a view appears, changes or becomes invalid.
5. Check that the view still works with an empty answer, and read the audit entries (section 9) after a test read.

A neutral view for checking the frame in a browser, `tests/fixtures/custom_views/probe/`, ships with the tests, not
with the product; it runs the browser probes and prints `PASS` or `FAIL` per probe. One probe (opening an allowed link
in a new tab) needs a click on its button, so an unattended run ends with `PROBE DONE 11/12`; after the click it
reads `12/12`.

## 11. Patterns that work

These come from a real view that has run against a production Brain; none of its files are part of the product.

- A **timeline**: bars positioned with `element.style.left` and `element.style.width` in percent, grouped by a fact
  such as a priority, with a marker for today.
- A **fold-open list**: a button that toggles a class on a section and shows the linked records read with `links`.
- **One read per screen**, then work on the answer in the script; use `where` and `limit` to keep it small.
- **Links out** through `knovas.open` to the one host the manifest names, built from a fact (an address stored as a fact
  on the node), never from markup.
