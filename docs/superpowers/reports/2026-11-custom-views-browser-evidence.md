# Custom views: browser evidence (SS-628; FEAT-2026-11-13-custom-views)

Plan Task 12, step 1: the probe view of Task 11 run in a Chromium browser against a local development server, plus
the six checks the reviews added. Steps 2 and 3 (the other browsers, the founder's decision on failures) are open;
the checklist for them is at the end.

## Summary

| | |
| --- | --- |
| Date | 2026-11-13, run 1 14:04–14:26 (original probe), run 2 14:34–14:36 (probe fixed after the controller's rulings) (Europe/Zurich) |
| Browser | **Microsoft Edge 157.0.4341.46** (Chromium 157; user agent `… Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0`), Windows 11 Pro 10.0.26300, the founder's everyday profile with its extensions, driven by the Claude in Chrome extension. Google Chrome 157.0.8103.51 is installed on the machine but was not running and was **not** tested. |
| Server | `create_app().run(host="127.0.0.1", port=5077)` from branch `feat/custom-views` (head `0397e1d`), accounts on (own PostgreSQL container), `ONTOLOGY_SOURCE=graph`, the Knovas tenant's Brain over mTLS (read only). |
| Probe | Run 1 only (run 2 uses the fixed probe): `tests/fixtures/custom_views/probe/` unchanged (fixture run), and a **diagnostic variant** (`probe-diag`, outside the repository and not committed, described below) to read the lines the fixture run cannot show. Both were in the views folder for this run only and were removed afterwards. |
| Result, run 1 | 13 of the 15 probe lines PASS, **2 FAIL**: `location.href` replaces the view with the browser's "blocked" page (F-1); the probe's form-post line reports FAIL although the post is blocked (F-2). All six review checks PASS (check 6 by code reading, see its row). |
| Result, run 2 | With the fixed probe (section "Run 2" below): all 11 unattended lines PASS (`PROBE DONE 11/12`), the allowed-link click makes it `12/12`, and the navigation probe, run last and judged from the page, is **blocked** as expected. |

## Run 1: why a diagnostic variant

In the fixture run the probe's last probe sets `window.location.href = "https://example.com/"`. Edge refuses that
navigation (the page's `frame-src 'none'`) and **replaces the frame's document with its "blocked content" page**
(screenshot 01). Everything the probe had written is gone and `PROBE DONE` never appears, so the other lines cannot be
read from the fixture run. The fixture run itself did reach the server: the log shows
`GET /api/custom-views/probe/document 200` and two `POST /api/custom-views/probe/read 200` (the first probe and the
self-posted-port probe), so the earlier probes ran.

The variant `probe-diag` is the fixture with these changes only (kept for the founder in
`E:\Knovas\knovas-custom-views\_harness\probe-diag\`, sha256 of `probe.js` starts `3ecc305bf46a`):

- the last probe without its `location.href` line (the link with `target=_top` is kept and judged on its own);
- the `location.href` change on a third button, "Change location", so its effect can be seen on its own;
- a second button, "Open a not-allowed link": a click, then `knovas.open("https://not-allowed.example.net/")`;
- two `DIAG` lines after 6 s: the `{type: "knovas.port"}` window messages the view received (from the parent; from
  other windows before and after its port arrived), and every `securitypolicyviolation` (directive, blocked address).

## Run 1: the probe lines

"diag" = observed in the diagnostic variant; "fixture" = observed in the fixture run (the probe as it was then).

| # | Line (Task 12 brief / probe text) | Result | How it was observed |
| --- | --- | --- | --- |
| 1 | read returns nodes of the one type | PASS | diag line PASS; fixture run: `POST /api/custom-views/probe/read 200` in the server log |
| 2 | `document.cookie === ""` | PASS | diag line PASS |
| 3 | `fetch("/api/custom-views")` rejects | PASS | diag line PASS; CSP violation `connect-src http://127.0.0.1:5077/api/custom-views`; the server log has no request from the frame (the one `GET /api/custom-views` at load is the page's own file list) |
| 4 | `fetch("https://example.com")` rejects | PASS | diag line PASS; CSP violation `connect-src https://example.com/` |
| 5 | `new Image().src = "https://example.com/x.png"` errors without a request | PASS | diag line PASS; CSP violation `img-src https://example.com/x.png` (CSP refuses before any request); no `example.com` request in the tab's network list. The server log cannot show a request to another host. |
| 6 | `location.href = "https://example.com"` leaves the frame unchanged | **FAIL** | fixture run and the diag "Change location" button: the frame's document is replaced by Edge's "blocked" page (screenshots 01, 07). The page tab stays at `/custom-views/…`, no `example.com` request was seen. See F-1. |
| 7 | a link click with `target=_top` does nothing | PASS | diag line PASS (marker still in the frame after 1.5 s); the tab's address did not change |
| 8 | `window.open(...)` returns null | PASS | diag line PASS |
| 9 | a form post is blocked | **FAIL (probe line)** | diag line FAIL. The post itself is blocked: the frame did not navigate (a navigation to `https://example.com/post` would have replaced the frame as in line 6) and no request was seen. The probe judges by a `securitypolicyviolation` event; none fires, because the frame's sandbox (no `allow-forms`) stops the submission before CSP `form-action` is consulted (the diag violation list has no `form-action` entry). See F-2. |
| 10 | `<script>` and `<style>` inserted through `innerHTML` do not run / apply | PASS | diag line PASS "(refused by Trusted Types)"; CSP violation `require-trusted-types-for trusted-types-sink` |
| 11 | a self-posted port is ignored | PASS | diag line PASS (a read after the self-post still answers) |
| 12 | `knovas.open` of a host the manifest does not name is refused | PASS | without a click: diag line PASS; after a click ("Open a not-allowed link"): line PASS with "Dieser Link ist für diese Ansicht nicht freigegeben.", no tab opened (screenshot 06) |
| 13 | `knovas.open("https://example.com/")` (allowed host) opens a tab | PASS | after a real click on "Open the allowed link": a new tab `https://example.com/` opened, not popup-blocked; in it `window.opener === null` and `document.referrer === ""`; line PASS (screenshot 05). Tab closed afterwards. |
| 14 | a `postMessage` from another tab/window to the frame is ignored | PASS | review check 5 below |
| 15 | `/api/custom-views/probe/document` opened directly as a page shows JSON and runs nothing | PASS | the address in the tab shows `{"error":"Diese Anfrage ist nicht erlaubt.","success":false}` with `Content-Type: application/json`, no script, no frame (server log: `403`). It is refused, not the document (screenshot 02). |

## The review checks

| # | Check | Result | How it was observed |
| --- | --- | --- | --- |
| 1 | No iframe `load` fires for the initial `about:blank` before the srcdoc document (the port must reach the view) | PASS | The page posts its port once, on the frame's first `load`. The view's diag line reads "from the parent 1" and every read answered; had the first `load` been `about:blank`'s, the srcdoc document would have received no port and shown nothing. The fixture run's two reads show the same. |
| 2 | The page's `Content-Security-Policy: frame-ancestors 'self'; frame-src 'none'; …` does not block the srcdoc frame | PASS | The srcdoc view rendered and ran in every load (both probes, Ziele, Wetten). That the policy is in force is shown by line 6: Edge refuses the frame's navigation to `https://example.com/` under it. |
| 3 | `window.open` from the bridge's `open` after a click is not popup-blocked for an allowed host and is refused for others | PASS | lines 13 and 12 |
| 4 | Opening `/api/custom-views/probe/document` directly shows JSON / is refused and runs nothing | PASS | line 15 |
| 5 | A `postMessage` from another tab/window to the frame is ignored | PASS | A second same-origin window (opened from the page by a click) posted `{type: "knovas.port"}` with a fresh `MessagePort` to the view's frame, as fast as it could, from before the view was reloaded until 5 s after: 4,000 messages. The view counted **35 arriving before its real port and 3,898 after**; it took the parent's port, every read answered, and the second window's 4,000 ports received **0** messages (screenshot 08). A slower first run (25 messages, all after the port) gave the same. |
| 6 | `frame-src` is never relaxed to network origins; the port is posted with target `"*"` only to the `srcdoc` frame | PASS (code reading) | **A code-reading check, not a separate browser observation.** `KnovasPlatform/components/docbridge_integration/src/web_interface/custom_views_routes.py` line 38 (inside `PAGE_HEADERS`, which starts at line 36) is the constant `Content-Security-Policy: frame-ancestors 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'`; no network origin appears in it. `src/web_interface/static/js/custom_views.js` line 494 posts the port with `frame.contentWindow.postMessage({type: 'knovas.port'}, '*', [channel.port2])`, and line 496 (`frame.srcdoc = documentAnswer.html`) shows that frame is a `srcdoc` frame. The browser observation behind it is check 2: the view loaded and worked under `frame-src 'none'`. |

## Findings for the controller

- **F-1 (line 6).** A view's own `location.href = …` (or any self-navigation) does not leave the frame unchanged in
  Edge 157: the navigation is refused by the page's `frame-src 'none'`, and Edge then shows its "blocked" error page in
  the frame instead of the view. Nothing leaves the machine and the Platform page is not affected, but the view is gone
  until it is reloaded. Consequences: the fixture probe never reaches `PROBE DONE` in Chromium, so
  `KnovasPlatform/docs/custom-views.md` § 10 ("an unattended run ends with `PROBE DONE 11/12`") does not hold there.
  Reproduce: put the probe fixture into the views folder, open «Eigene Ansichten» → «Prüfansicht»; or use
  `probe-diag` and click "Change location". Not changed in run 1 (no product or test code changes in this task); see the rulings and run 2 below.
- **F-2 (line 9).** The probe's form-post check counts `securitypolicyviolation` events. In Chromium the frame's
  sandbox blocks the submission first, so no event fires and the line reads FAIL while the post is blocked. The check
  needs another signal (for example: the frame is still there and the view's script still runs afterwards).
  Reproduce: the probe's own line, or `probe-diag` (the violation list has no `form-action`).
  Not changed in run 1; see the rulings and run 2 below.
- **F-3 (browser).** The automated browser was Edge, not Google Chrome. Chrome, Firefox and Safari are still open
  (checklist below).
- **F-4 (not a browser line; seen while running the views).** On this tenant (19,974 nodes, 25,006 edges; the edge
  list is 12 MB and takes 3.2–3.7 s) the views' scale guard (`views.edge_budget_seconds`, default 2 s) fires on every
  topology read. Every custom-view read is then marked `topology_partial` («Nicht alles gelesen»), and reads with
  links walk node by node: the first load of «Ziele» showed its tree after about 2.5 minutes, with the banner; a
  second load had single reads of 53 s and 99 s and was still reading after four minutes. With
  `VIEWS_EDGE_BUDGET_SECONDS=10` the same view was complete in about 5 s. The demo sets 10; the product default
  is unchanged.
- **F-5 (Brain access).** The local Platform signs each Brain read with a broker key that is not registered with
  Knovas; the API answered every read with data (no 401/403). So the Knovas tenant does not check that signature
  today, and what a view shows is bounded by the organisation certificate, not by the signed-in person.

Other observations: the probe asks for `knovas.resize` before it writes its lines, so its frame stays at the 200 px
minimum and scrolls (a property of the probe, not of the Platform). The browser's extensions (a password manager and
the automation extension) were active; none of the results depends on them.

## Rulings (controller, fix round 1)

- **F-1:** the product behaviour is accepted. A view that navigates itself is blocked by `frame-src 'none'`; nothing
  leaves, the page stays, the view only ends itself. The probe was changed instead: the navigation probe runs last,
  after every other line and `PROBE DONE`, and its expected outcome is **blocked (the frame shows the browser's blocked
  page; the Platform page and other views are unaffected)**, judged from the page and recorded here, not by the probe.
- **F-2:** the form-post probe now observes what the sandbox does (the document is still there after `form.submit()`,
  which threw or did nothing; no request in the network log) instead of waiting for a CSP event.
- **F-4:** the product default stays; the server's existing setting is raised (the founder's step); the demo keeps
  `VIEWS_EDGE_BUDGET_SECONDS=10`.
- **F-5:** recorded for the founder (existing Platform behaviour, tied to the RBAC work); no change.

## Run 2: the fixed probe

Same browser (Edge 157.0.4341.46), same server setup, 14:34–14:36. The fixture as committed with this report
(`probe.js` sha256 starts `8871263a9d9c`, `index.html` `a5d24571069f`) was copied into the views folder for the run
and removed afterwards. The `_top` link is now its own line in the chain; the self-navigation sits on a last button,
"Last: leave the frame (ends this view)", which the entry ships disabled and the probe enables after `PROBE DONE`.
The probe also resizes its frame once `PROBE DONE` is written, so every line is visible.

| Line | Result | How it was observed |
| --- | --- | --- |
| read returns nodes of the one type | PASS | probe line; `POST /api/custom-views/probe/read 200` |
| `document.cookie` is empty | PASS | probe line |
| fetch of the Platform API rejects | PASS | probe line; no request from the frame in the server log |
| fetch of an outside address rejects | PASS | probe line |
| image from an outside address errors | PASS | probe line; no `example.com` request in the tab's network log |
| `window.open` returns null | PASS | probe line |
| form post is blocked | PASS | probe line "(submit did nothing; …)": the document was still there after 1.5 s; no `example.com` request in the network log |
| markup string with script and style does not run or apply | PASS | probe line "(refused by Trusted Types)" |
| a self-posted port is ignored | PASS | probe line; the second `POST …/read 200` |
| open of a host the manifest does not name is refused | PASS | probe line |
| link with target `_top` leaves the page and the frame unchanged | PASS | probe line; the address bar stayed on `/custom-views/probe` |
| unattended run | `PROBE DONE 11/12` | screenshot 10; the last button became enabled |
| open of an allowed host opens a tab (click) | PASS | a new tab `https://example.com/` opened (not blocked), closed again; `PROBE DONE 12/12` (screenshot 11) |
| navigation probe (last button), expected **blocked** | **blocked, as expected** | judged from the page: the frame's document was replaced by Edge's blocked page (screenshot 12); the frame element fired exactly one more `load` (the browser's page) and is the same element; the address bar stayed `http://127.0.0.1:5077/custom-views/probe`; no `example.com` request in the network log. Then «Wetten» opened and read normally (`POST /api/custom-views/wetten/read 200`), and «Prüfansicht» opened again with all lines back. |

The review checks are unchanged by the fix (run 1). Tests: `tests/test_custom_views_probe.py` and
`tests/test_custom_views_generic.py`, 23 passed on Python 3.13.5 and on 3.11.15 (`python:3.11-slim`).

## Screenshots

Data-free, in `docs/superpowers/reports/2026-11-custom-views-evidence/` (04–07 were taken with the variant before
its two `DIAG` lines were added; its probes were the same):

| File | Shows |
| --- | --- |
| `01-probe-fixture-frame-replaced-by-blocked-page.jpg` | fixture run: the frame shows Edge's blocked page (F-1) |
| `02-document-address-opened-directly-refused.jpg` | the document address opened as a page: JSON refusal |
| `03-probe-diag-results-top.jpg` | diag: lines 1–11 (form post FAIL, F-2) |
| `04-probe-diag-results-bottom.jpg` | diag: the rest, `PROBE DONE 11/13` before the two clicks |
| `05-probe-diag-allowed-open-after-click.jpg` | diag: allowed open after a click, PASS |
| `06-probe-diag-refused-open-after-click.jpg` | diag: refused open after a click, PASS, `13/13` |
| `07-probe-diag-location-change-frame-replaced.jpg` | diag: after "Change location", the frame shows the blocked page |
| `08-probe-diag-foreign-window-ports-ignored.jpg` | diag: 35 foreign ports before, 3,898 after the real one |
| `09-probe-diag-csp-violations.jpg` | diag: the CSP violations seen |
| `10-fixed-probe-unattended-11-of-12.jpg` | run 2: every line PASS, `PROBE DONE 11/12`, the last button enabled |
| `11-fixed-probe-after-allowed-open-12-of-12.jpg` | run 2: after the allowed-link click, `12/12` |
| `12-fixed-probe-navigation-blocked.jpg` | run 2: after the last button, the frame shows Edge's blocked page; the page is unchanged |

With Brain data, kept outside the repository in `E:\Knovas\knovas-custom-views\_harness\` (named only):
`browser-evidence-2026-11-13-ziele.jpg`, `browser-evidence-2026-11-13-ziele-goals.jpg`,
`browser-evidence-2026-11-13-wetten.jpg`, `browser-evidence-2026-11-13-wetten-all-groups.jpg`,
`browser-evidence-2026-11-13-wetten-product-default.jpg` (the last with the product default edge budget, banner
«Nicht alles gelesen»). Their counts were compared with the Brain (Knovas MCP `knovas_list_nodes`, 14:20–14:23):
13 Bets (now 4, next 4, later 4, no priority 1), 10 Goals, 1 Vision; both views show exactly these.

## Checklist for the founder: the other browsers

Edge 157 is done (above). Repeat in **Google Chrome** and **Firefox** on this machine. **Safari needs a Mac**; it
cannot be run here.

Before: start the demo (`E:\Knovas\knovas-custom-views\README.txt`), then copy into `E:\Knovas\knovas-custom-views\`
the folder `KnovasPlatform\components\docbridge_integration\tests\fixtures\custom_views\probe` (as `probe`), and for
the optional step 5 also `E:\Knovas\knovas-custom-views\_harness\probe-diag` (as `probe-diag`). That variant lives
outside the repository, on the founder's machine, and is not committed; the committed probe is
`tests/fixtures/custom_views/probe/`. It still has the old form check, so ignore its form line). Wait 30 s. Sign in as `admin@demo.local`. Open the browser's developer tools,
tab Network, to see requests to `example.com` (there must be none, except the tab of step 2).

For each browser, write down the version (Chrome: `chrome://version`; Firefox: Menu → Help → About Firefox) and then:

1. «Eigene Ansichten» → «Prüfansicht». After 5 s the frame shows eleven lines and `PROBE DONE 11/12`. Expected: all
   PASS (Firefox may write "inert" instead of "refused by Trusted Types" on the markup line; both are PASS).
2. Click "Open the allowed link": a tab with `https://example.com/` must open (no pop-up-blocked notice); the line
   turns PASS and the count reads `12/12`. Close that tab.
3. Click "Last: leave the frame (ends this view)". Expected: **blocked**. The address bar stays on
   `/custom-views/probe`; no request to `example.com`; the frame shows the browser's blocked page (Edge) or something
   else that is not `example.com` (note what). Then open «Wetten» (it must load) and «Prüfansicht» again (the lines
   come back).
4. Open `http://127.0.0.1:5077/api/custom-views/probe/document` in the address bar: a JSON line
   `{"error":"Diese Anfrage ist nicht erlaubt.","success":false}`, nothing else.
5. Optional, the foreign-window check (developer tools, Console of the «Eigene Ansichten» tab, view «Prüfansicht
   (Diagnose)» open). Paste and run:
   ```js
   var w = window.open('', 'cv-attacker', 'popup,width=420,height=300');
   var end = Date.now() + 5000, frame = function () { return document.querySelector('#cvFrameHost iframe.cv-frame'); };
   w.eval('window.go = function (f, end, out) { var t = new MessageChannel(); t.port2.onmessage = function () { var fr = f(); if (fr && fr.contentWindow) { var c = new MessageChannel(); c.port1.onmessage = function () { out.got++; }; fr.contentWindow.postMessage({type: "knovas.port"}, "*", [c.port2]); out.sent++; } if (Date.now() < end && out.sent < 4000) t.port1.postMessage(0); }; t.port1.postMessage(0); };');
   var out = {got: 0, sent: 0}; w.go(frame, end, out); document.querySelector('a.cv-pick[data-view-id="probe-diag"]').click();
   ```
   If the browser blocks the small window, allow pop-ups for `127.0.0.1:5077` for this test and run it again.
   After 10 s run `out` and close the small window (`w.close()`). Expected: `got: 0`, and the view's `DIAG` line shows
   "from the parent 1" with a number of messages from other windows; the read lines still PASS.
6. Remove `probe` and `probe-diag` from `E:\Knovas\knovas-custom-views\` again (only `ziele` and `wetten` stay).

Record per browser: version, each step PASS/FAIL with a note, in a new section of this report.
