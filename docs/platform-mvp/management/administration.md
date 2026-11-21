# Administration (Verwaltung) and settings

State on 21 November 2026. Overview and status words: [README.md](README.md).

## What it is

The area where a firm's administrator manages the people who use the Platform, their roles and access groups, the
folder rules and document rights, and where a second person confirms risky actions. The installation's settings page,
where an administrator switches features on and chooses the assistant's provider, belongs here too, although a person
reaches it through her own block at the foot of the menu. Most of the console exists already on main; the MVP gives it
the new navigation and look and adds the assistant's and the views' settings.

## What a person can do with it

**Exists today (on main):**

- **People:** create an account, deactivate and activate it, reset a password, sign a person out everywhere, set roles
  and access groups. Sign-in is with e-mail address and password only.
- **Access:** access groups as a tree, folder rules, a document's access groups, the document list.
- **Approvals:** a guarded action (a document's access groups, a folder rule, erasing all documents) waits for a second
  person to confirm or reject it; an administrator acting alone is recorded as a bypass, and only an administrator
  switches the bypass.
- Ingestion, document fields and system checks (pages without a component card yet).

**New in the MVP:**

- The "Verwaltung" link and menu show each person exactly the Admin pages she may open (PMVP-11, integrated).
- **Settings page** (built, not yet verified): the assistant on or off (off by default), the provider (the Knovas Core
  or Anthropic), the model (Opus 5.5 by default, Sonnet 5.5, Haiku 5.5), the provider's key, and "Verbindung testen",
  which answers "funktioniert" or a plain reason ("Schlüssel abgelehnt", "ausgelastet", "keine Verbindung"); the Brain
  views switch (PMVP-30a). A value set in the deployment wins over the page; changes apply without a restart.
- **The new look (PMVP-40, in build):** Personen, Zugriffsgruppen and Dokumente as one column of the shared parts, the
  main action beside the title, roles in plain words, the group tree drawn as a tree, and marks only for exceptions
  (deactivated, locked, password change pending, a document that is not active).

**Planned:** "Was sieht …?" (what does a given person see) moves here from the Builder (PMVP-41); the enforcement
position is shown and switched with a second administrator (PMVP-42); the pilot's rights test set (PMVP-43).

## Roles (the role builder, RB-1 and RB-2)

**What a role is.** A bundle with a name and a description: **functions** (what its members may do, from the ten the
Platform knows, such as "Freigaben bestätigen" or "Einlesen"), at most one **Core group** (what its members may read)
and **members** (people for now; the Knovas Assistent and external agents come with RB-6 to RB-8). A person may do
everything any of her roles allows. The Rollen page itself comes with RB-3 (read only) and RB-4 (changes).

**The two roles.** Every installation has exactly «Administration» and «Mitglied», and neither can be deleted.
Administration alone holds "Verwaltung" (the old administrator key), so an administrator is exactly a member of
Administration. Mitglied holds "Fragen und suchen". A new installation starts with these two only; its first
administrator joins Administration at the first start. Every other role is the firm's own.

**The move of an existing installation.** When the new version starts, the database is moved once, inside the
installation: everyone joins Mitglied; every administrator joins Administration; and for each combination of the other
functions that people hold, one role is made, named after its functions (for example «Freigaben bestätigen» or
«Experimente + Experimente verwalten»), and exactly those people join it. Nobody loses a function. The one change, as
intended, is that everyone gains "Fragen und suchen" (`member`), because everyone joins Mitglied. No standard page
checks it: every person passes exactly the console, Builder and Experimente pages and buttons they passed before (tested
for each function and for a sample firm). But an installation can make `member` count where it configures it: a
custom view or a profile view open to `member` (`views.profile_roles`), or editing document fields
(`web.doc_fields.edit_roles`). There, someone who held other functions but not `member` before the move now sees or
edits what members do. An installation that uses these settings should check them before the move. A very long
combination keeps its first words and a number, so every name is unique and at most 80 characters. The old grants
table stays untouched for one release as the way back: going back to the previous version restores everyone's
functions as they were at the move, but changes made after the move are lost.

**Safety rules built now.**

- **The last administrator stays.** Removing the last active person from Administration, or taking "Verwaltung" from
  her on the people page, is refused with a plain message and nothing changes; two removals at the same moment cannot
  both pass. (Deactivating the last administrator through the people page stays as today until RB-4: only deactivating
  oneself is refused.)
- **At most 32 access groups per person**, directly and through her roles together, because the Knovas Core refuses a
  longer list. A change that would give a 33rd is refused with a plain message and nothing is written; the Platform
  never signs a request with more than 32 and never silently drops any.
- **Granting oneself a role needs no second administrator** (founder, 21 November).
- **Nothing reaches the Core.** A role's Core group is only recorded; creating and linking the groups in the Core (also
  Administration's and Mitglied's) is RB-4's, on an administrator's action, never at start-up.
- No role name, description, person's name or address is written to a log.

## How it protects data and access

- **Each page checks on the server.** Every Admin route has its own role gate; the menu only reflects it. Signed out
  means the sign-in page; a wrong role is refused; a form without its security token is refused and nothing changes.
- **Gate tests for every route** of the three pages, the console's entry and System (signed out, member, administrator)
  are on PMVP-40's branch; on the build branch eight routes' gates are still seen in the code only (FU-17).
- **Two people for risky actions**, as above; a bypass is recorded.
- **Secrets** on the settings page are stored encrypted, never sent back to the browser and never logged. Only
  administrators may change installation settings or test the connection.
- **PMVP-40 changes no gate, route, rule or message**: look only.

## Where it stands

| Item | What | State | Approves before main |
|---|---|---|---|
| (existing) | the console | on main | |
| PMVP-11 | navigation of the area; the old tab row removed | verified 21 Nov (with notes); integrated | Siran (sensitive) |
| PMVP-25 | the assistant's settings and connection test | built; no verification verdict yet | management (critical) |
| PMVP-30a | the Brain views switch | built; no verification verdict yet | Jannik or Siran (routine) |
| PMVP-40 | the three pages in the new look; gate tests; the Admin card split in two (a new card "Zugriff" for the access pages) | in build (work pushed, builder's notes not written) | Jannik or Siran (routine) |
| PMVP-41 | "Was sieht …?" in Admin | not started | Siran (sensitive) |
| PMVP-42 | enforcement position and switch | not started; waits for two green rights runs of the Core | management (critical) |
| PMVP-43 | the pilot's rights test set | not started | Siran (sensitive) |
| RB-1, RB-2 | roles: tables, the two roles, the move of an existing installation; functions and groups read from roles; the last-administrator rule and the 32-group cap | built; no verification verdict yet | management (critical: a migration) |
| RB-3, RB-4 | the Rollen page; creating, renaming and deleting roles; the people page offers roles | not started; no work order yet | Jannik or Siran (RB-3, routine); Siran (RB-4, sensitive) |

## Decisions taken

| Date | Who | What |
|---|---|---|
| 19 Nov | founder | The Admin pages belong to the new domain "Workspace"; Admin may move later if it grows (DEC-0083) |
| 19 Nov | founder | The pilot runs with enforcement on; "Ein" only after two green end-to-end runs of the Core, so PMVP-42 is on the MVP's path and never cut (DEC-0082) |
| 20 Nov | founder | "Hide, place later": Einstellungen has no menu entry; the person's block leads there |
| 21 Nov | coordinator | The menu reads the roles each Admin gate lets in; as a result an ingestion manager now sees "Verwaltung", leading to Ingestion only |
| 21 Nov | coordinator | The assistant's key has no setting in the deployment files; it is entered on the settings page only |
| 21 Nov | founder | Roles: a new installation has only Administration and Mitglied; granting oneself a role needs no second administrator; the last-administrator rule stays; no deny |
| 21 Nov | coordinator (RB-1, RB-2 work order) | "Verwaltung" belongs to Administration alone; Administration's and Mitglied's Core groups are linked by an administrator in RB-4, not at start-up; a new account joins no role until the console makes it a member |

## Known limits and open points

- **Phone width (FU-23):** the five Admin pages and the settings page scroll sideways; PMVP-40 reworks the Admin ones.
- **Contrast of the old green tints (FU-28)** below 4.5 to 1; PMVP-40 replaces them with the brand's.
- **The Admin card was over its size budget (FU-17)**; PMVP-40 splits it.
- **"Was sieht …?" and the enforcement position** are still the Builder's files (FU-20).
- Ingestion, document fields and System have no card; their domain is open.
- Small findings: an approver who is also an ingestion manager lands on Ingestion; malformed roles give an error page;
  a signed-out call to the Builder's data routes is redirected to sign-in instead of refused (FU-24).
- The settings page's section title "Ansichten und Wissensbasis" now also holds the assistant's entries.
- **Roles until RB-4:** the people page still offers single functions; ticking one puts the person into a role holding
  just that function (made on first use), and unticking takes exactly that function away. Roles have no Core group
  until RB-4 links them, so for now roles decide only what a person may do, not what she may read.

## Depends on

The Platform's identity store (accounts, roles, sessions, audit, approval queue) in the Platform's database; the
Knovas Core's access routes (groups, folder rules, document access, erasing); the Core's rights engine for enforcement;
the shell (navigation, look, page parts).

## Owner

The engine owner role, proposed on the cards (Verwaltung, and "Zugriff" on PMVP-40's branch); a person has still to
confirm it.

**Where the detail is:** `docs/platform-mvp/work-orders/PMVP-11.md`, `PMVP-40.md` (branch `feat/pmvp-40`), `PMVP-25.md`
(branch `feat/pmvp-25`), `RB-1-2.md` (branch `feat/rb-1-2`); `docs/platform-mvp/design/role-builder.md`;
`docs/components/L2-platform-admin.md`; `docs/platform-mvp/follow-ups.md`.
