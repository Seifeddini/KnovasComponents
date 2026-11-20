Knovas Platform - six reference screens, second pass (17 Nov 2026)

Open any NN-*.html in a browser. Each file is 1040 x 700 px.
  01-assistent.html  Workspace: question, answer, the cited passage under its sentence
  02-dossier.html    Workspace: dossier of a client, related things in tabs
  03-ablauf.html     Workspace: process line for one matter
  04-umgebung.html   Workspace: neighbourhood of a contract, one circle per kind of related thing
  05-zugriff.html    Admin: enforcement state, one access check, one table of rules
  06-quellen.html    Builder: one row per source, set-up strip

Five design rules behind them:
  1. One question per screen.
  2. One column and one main action.
  3. The navigation shows only the area a person is in.
  4. Details appear on request (passage under its reference, tabs, a source's own page).
  5. Marks are for exceptions: what is fine carries no badge.

app.css holds the layout. Colours, type and radii come from assets/knovas-tokens.css
(copied from brand/knovas-brand in the KnowledgeBase repository). shell.js draws the sidebar
for the area named in <body data-area="work|admin|builder">.
out/ holds the rendered PNGs. render.sh re-renders one screen with headless Chrome.
The firm, people and documents shown are invented.
