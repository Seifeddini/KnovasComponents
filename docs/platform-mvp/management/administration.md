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
and **members** (people, and agents: the Knovas Assistent and external agents, RB-6 and RB-7). A person may do
everything any of her roles allows. The Rollen page (RB-3, RB-4) is described below under «Rollen bearbeiten».

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

- **The last administrator stays.** Removing the last active person from Administration, on the Rollen page or the
  people page, or deactivating her, is refused with a plain message and nothing changes. Removals and deactivations
  wait for each other, so two at the same moment cannot both pass (RB-4 closed the last gap: a deactivation now takes
  the same lock as a removal).
- **At most 32 access groups per person**, directly and through her roles together, because the Knovas Core refuses a
  longer list. A change that would give a 33rd is refused with a plain message and nothing is written; the Platform
  never signs a request with more than 32 and never silently drops any.
- **Granting oneself a role needs no second administrator** (founder, 21 November).
- **Nothing reaches the Core on its own.** The Core is written only when an administrator saves a form on the Rollen
  page (below), never at start-up and never by opening a page.
- No role name, description, person's name or address is written to a log.
- **Bad input is answered with the page, never an error page** (RB-FIX-1): a role's name or description, or an
  agent's name, holding an invalid character (a NUL byte, which no browser form sends) is refused with a plain message
  before Knovas is asked; deactivating or activating a person the installation does not know is refused and nothing
  changes. One's own account is recognised however its id is spelled, so it cannot be deactivated by a spelling trick.

## Rollen bearbeiten (RB-3, RB-4)

**The page.** Verwaltung › «Rollen», right after «Personen», for administrators only. It shows the roles as a row of
tabs (each with its number of people) and one role at a time: its name and description, «Darf tun» (its functions in
words, never the technical keys), «Darf lesen» (the folders and documents the role reads, as Knovas holds them; see
«Was eine Rolle lesen darf» below, RB-5) and «Mitglieder» (the people in it). Under the card: "Eine Rolle gibt nie
mehr, als Sie selbst dürfen. Jede Änderung steht im Protokoll."

**What an administrator can do there.**

- **«+ Rolle anlegen»:** a name (1 to 80 characters, unique regardless of upper and lower case), an optional
  description and the functions. Knovas first creates a group of the same name for the role; only when Knovas answers
  is the role saved. If Knovas refuses, no role is made and the page says so. The description is set once, when the
  role is created.
- **«Umbenennen»:** Knovas renames the role's group first (its id stays, so nothing that points to it breaks); if Knovas
  refuses, the role keeps its old name. A role without a group (one the move of an existing installation made) is
  renamed in the Platform alone.
- **«Darf tun» › «Ändern»:** tick the functions. "Verwaltung" belongs to Administration alone and is never offered;
  Administration keeps it. A change counts from each member's next click.
- **«+ Mitglied hinzufügen» and «Entfernen»:** people join and leave a role; it counts from their next click. No
  second administrator is needed, also not for giving oneself a role. A person can hold at most 32 access groups
  through her roles and directly together; a change that would give a 33rd is refused.
- **«Rolle löschen»:** first every member leaves the role, then Knovas deletes its group, then the role is deleted. If
  Knovas no longer knows the group (it was removed there directly), the role is deleted and the log says so. If Knovas
  keeps the group, for example because data still name it, the role stays, without its members, and the page says so;
  delete it again once Knovas lets the group go.
- **The people page offers roles.** Each person's form shows every role with a tick box (a new installation: exactly
  Administration and Mitglied; the firm's own roles as they are made). Saving makes the person's roles exactly the
  ticked ones, all at once or not at all. «Person anlegen» still puts the new person into Mitglied only.

**The two roles.** Administration and Mitglied keep their names and cannot be deleted. They get their groups at
Knovas when an administrator presses «Mit Knovas verbinden» (shown while either has none): Administration gets the
firm's administrator group at Knovas (it must be exactly one; otherwise nothing is linked and the page says so), and
Mitglied a new group «Mitglied». A second press changes nothing. If Knovas already has a group «Mitglied», Mitglied
adopts it instead («Gruppe übernehmen», RB-5).

**What a Knovas refusal means.** Knovas decides about its groups. When it refuses (it is not reachable, or keeps a
group that data still name), the Platform keeps what it had and the page names Knovas as the reason; nothing is half
done, except deleting, where the members have already left (as the design orders it).

**The last administrator** keeps her role on every path: the remove button, the people page, the functions of
Administration (it keeps "Verwaltung"), deleting (Administration cannot be deleted) and deactivating (refused when she
is the last active one; two administrators who act at the same moment wait for each other).

**Everything is in the log.** Every change writes its row in the audit log in the same step as the change: who did
it, which role or person (by id), and the functions before and after (by their technical keys). Never a role's name,
description, a person's name or address. If the row cannot be written, the change is not kept and the page says so.

## Was eine Rolle lesen darf (RB-5)

**What the part «Darf lesen» shows.** The folders and sources whose folder rule at Knovas names the role's group, and
the documents whose own rights name it, each in a row («Ordner» or «Dokument»). The page reads them from Knovas every
time it is opened: the Platform keeps no copy, so a rule changed at Knovas directly shows as Knovas holds it. Opening
the page changes nothing anywhere.

**Adding and removing data.** «+ Daten hinzufügen»: choose «Ordner», «Quelle» or «Dokument» and give its path at
Knovas (a folder or source usually ends with «/»). Each row has «Entfernen …». **Every change starts with a dry run**
(«Probelauf») that says how many people will see the folder or document afterwards and how many will no longer see
it, with their names on request; only its button carries the change out, and the change is planned again from what
Knovas holds at that moment (what the browser shows is never trusted).

- **Adding a walled folder** (it has a rule with a group) adds the role's group to that rule; nobody loses sight.
- **A folder without a rule of its own** gets a new rule with the groups that govern it today plus the role's group,
  so again nobody loses sight. If documents are already read in under it, the dry run says that the new rule reaches
  them only after Knovas has re-applied the rules to them (CR-R3); until then their present rule stays.
- **Adding an open folder or document** (no group) would wall it off for everyone outside the role once «Ein» is on:
  the dry run warns and names everyone who would lose sight, and the change needs a tick («Ich habe die Warnung
  gelesen …»); without it, nothing is sent.
- **A document** keeps its present groups and gains the role's group; it leaves its folder rule (later changes to the
  folder's rule no longer reach it), and the dry run says so.
- **Removing** takes the role's group out of a rule or a document's own rights. When the role's group is the rule's
  only group, the rule is deleted instead of being emptied, and the rule of the folder above governs again; if nothing
  above is walled, the folder becomes open for everyone, and the dry run warns and asks for the tick. A rule is never
  sent to Knovas with no group.
- **No deny** (founder): a role only grants; a wall is a rule that leaves a role out.
- Administration has no data (its group is the firm's administrator group, which may change rights but reads nothing
  walled). A role without a group at Knovas (Mitglied before «Mit Knovas verbinden») gets data once it has one.

**Four eyes.** Adding and removing data are rights changes and take the same path as the «Zugriff» pages
(`acl_change`): while the installation's setting "administrators act alone" is on (the default since 14 August), an
administrator's change is made at once and the approvals page records that she acted alone; with the setting off,
the change waits under «Freigaben» for a second person, and nothing reaches Knovas until then. Nobody confirms her own
change. **Only an administrator carries a data change out**: an approver who is not an administrator may confirm it,
and an administrator then presses «Ausführen». When it is carried out, the change is planned again; if a folder has
become open meanwhile and the request carries no tick, nothing is sent (plan it again). The founder may tighten this
(four eyes even with the setting on); then a firm with one administrator could not change a role's data at all.

**Before «Ein».** Data changes are written to Knovas at once, but they **hide nothing until enforcement is «Ein»**:
while a firm's enforcement is off, Knovas stores rules and rights and shows every document to everyone. The part says
"… wirkt mit «Ein»" until the Platform knows that the firm's Knovas checks the signed claims (`brokered`); today
Knovas has no way to tell it, so the words always show (the coordinator adds a posture read to CR-R1). What waits is
the effect, not the write: every data change acts only with CR-R1 and CR-R2; a new folder over documents already read
in only with CR-R3; and in the short time a firm is switched to `brokered` before CR-R2 is built, Knovas refuses the
folder-rule changes and the page says so.

**Adopting an existing Knovas group** («Gruppe übernehmen», on Mitglied and the firm's own roles). A role takes over a
group Knovas already has as its group, with everything that group may read; a dry run first names the members who
gain it. This is the way out when «Mit Knovas verbinden» cannot create the group «Mitglied» because Knovas already has
one (FU-45): Mitglied adopts it, and «Mit Knovas verbinden» then links Administration alone. Refused: the
administrator group, a group another role has (one role, one group), a group Knovas does not list, Administration, a
role whose present group still has data (remove them first, each with its dry run), and a change that would give a
person a 33rd group. Knovas creates no group; the role's previous group is deleted at Knovas afterwards (if Knovas
keeps it, the page says so and the log has it). No second person is needed: adopting only grants sight, like adding a
member.

**Moving direct group grants into roles** (FU-41). With «Ein» the Knovas Assistent reads only what a person's roles
give, not the groups she holds directly. While anyone holds a group directly without an end date, the page offers «In
Rollen übernehmen …». Its dry run shows the new roles, what goes and what stays; the move then makes **one role per
directly held group** (no functions, named like the group at Knovas, «… (2)» when the name is taken), puts the
holders into it and removes their direct grants; a direct grant of a group a role of hers already gives goes too.
**Stay direct:** grants with an end date (an access window; a role membership has none), the administrator group, a
group Knovas does not list, and the group of a role the person is not in (joining would give her that role's
functions). Everyone's groups and functions are compared before and after, and any difference undoes the whole move;
nothing is written to Knovas, so nobody's sight changes today. A second move changes nothing. The people page can still
grant groups directly; the role page then offers the move again. **Open:** whether the people who keep direct grants
must be handled before «Ein» (the assistant will not read through those grants) is for the founder or coordinator.

**Everything in the log, by id.** Each data change writes its row after Knovas answers (outcome "error" when Knovas
refused): the role, its group, the rule's or document's id, the kind and how many people gain or lose sight; never the
path, a document's name, a role's or group's name or a person. Adopting writes the role and both group ids; the move
writes the new roles, the people (by id) and how many grants moved and stayed. The approvals' rows name the role
(`role:<id>`), never the folder; the approvals page shows the folder to the approver (a page, not a log).

## Agents (the role builder, RB-6 and RB-7)

**What an agent is.** A member of roles that is not a person. Every agent reads what its roles allow; when it acts for a
person, only what that person may also read. No agent joins Administration, and a person's own direct access groups
reach no agent: only roles give an agent data. The Platform signs each agent's claim (its id, its roles' groups and
functions); the Knovas Core accepts it as it accepts a person's.

- **The Knovas Assistent** is a member of every role but Administration, also of roles made later. An administrator
  takes it out of a role on the role page, under «Mitglieder» («darf nicht von KI genutzt werden», for example
  «Personal»). It always acts
  for the person who asks: every call it makes to the Core for an answer carries its own claim, narrowed to that
  person, and says "the assistant, for this person". It is switched off in the settings, not revoked.
- **External agents** (Claude Code, Copilot …) are registered on the new page **Verwaltung → Agenten**: a name, and
  either a person they act for (fixed for good; deactivating her stops the agent) or none («selbständig», then at most
  32 access groups). A new agent has no role; it joins roles on the role page («Mitglieder» › «+ Agent hinzufügen»). The page shows each agent's id; Knovas
  issues the agent's key for its gateway (`mcp.knovas.ch`) with that id. **The Platform holds no agent key** and never
  sees it. How to connect Claude Code is in `KnovasPlatform/docs/agents.md` (German).
- **Revoke** an external agent on the Agenten page: from that moment it has no claim; with the switch on, the Core
  drops its claims in the same request.
- **The switch «Agenten über das Knovas-Gateway»** (settings; `AGENTS_CORE_GATEWAY` pins it), **off** by default. On,
  the Platform hands the Core every minute, and at once after a change on the agent pages, 20 signed claims per live
  external agent, each valid at most 300 seconds (so an agent makes at most 20 calls a minute); a revoked agent, one
  whose person is inactive and one over 32 groups get theirs dropped. Changes made on the role or people page reach the
  Core at the next minute. Founder, 21 November: **on a client's installation agents are switched on only once
  enforcement is «Ein»**; the Knovas GmbH pilot may use them before, as it uses Claude Code today.
- **Every change is in the log by id**: agent created, revoked, added to or taken out of a role, with the acting
  administrator; never a name.


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
| RB-3, RB-4 | the Rollen page; creating, renaming and deleting roles, their functions and people; the people page offers roles; deactivating waits for removals (the last-administrator race) | verified (with notes); integrated | Siran (sensitive) |
| RB-6 | one claim for every agent; the Knovas Assistent a member of every role but Administration, answering with its own claim | verified (with notes); not yet integrated | management (critical: a migration, with RB-7) |
| RB-7 | external agents: the Agenten page, agents on the role page («Mitglieder»), their signed claims handed to the Core behind a switch (off) | verified (with notes); the agent rows on the role page added after RB-3/RB-4 were integrated; not yet integrated | management (critical: a migration, signing and claims) |
| RB-5 | a role's data («Darf lesen»): adding and removing folders, sources and documents with a dry run and four eyes; adopting an existing Knovas group; moving direct group grants into roles | verified (with notes); two fixes after it (F1, the adopt under its lock); RB-FIX-1 merged in; not yet integrated; acts with the Core's CR-R1, CR-R2 (and CR-R3 for folders already read in) | Siran (sensitive) |
| RB-FIX-1 | clean-up after the role builder: who holds a group counts her roles' groups (FU-42); the Rollen and Agenten pages get cards of their own (FU-44); a role or agent name with an invalid character and an unknown person on «Deaktivieren»/«Aktivieren» answer with the page and change nothing | built; no verification verdict yet | Siran (sensitive) |

## Decisions taken

| Date | Who | What |
|---|---|---|
| 19 Nov | founder | The Admin pages belong to the new domain "Workspace"; Admin may move later if it grows (DEC-0083) |
| 19 Nov | founder | The pilot runs with enforcement on; "Ein" only after two green end-to-end runs of the Core, so PMVP-42 is on the MVP's path and never cut (DEC-0082) |
| 20 Nov | founder | "Hide, place later": Einstellungen has no menu entry; the person's block leads there |
| 21 Nov | coordinator | The menu reads the roles each Admin gate lets in; as a result an ingestion manager now sees "Verwaltung", leading to Ingestion only |
| 21 Nov | coordinator | The assistant's key has no setting in the deployment files; it is entered on the settings page only |
| 21 Nov | founder | Roles: a new installation has only Administration and Mitglied; granting oneself a role needs no second administrator; the last-administrator rule stays; no deny |
| 21 Nov | founder | Agents: the Platform signs agents' claims; external agents are in the pilot and enforced like the assistant; **"Core gateway only"** (the Platform gets no agent endpoint, RB-8 dropped); **"only after «Ein»"** on client installations |
| 21 Nov | coordinator (RB-6, RB-7 work order) | A pool of 20 claims a minute per external agent, each at most 300 seconds; Knovas issues the gateway key with the agent's id; the assistant joins every role but Administration and its claim rides on every Core call of an answer; the switch is off by default |
| 21 Nov | coordinator (RB-1, RB-2 work order) | "Verwaltung" belongs to Administration alone; Administration's and Mitglied's Core groups are linked by an administrator in RB-4, not at start-up; a new account joins no role until the console makes it a member |
| 21 Nov | coordinator (RB-3, RB-4 work order) | Linking Administration and Mitglied to Knovas is its own button; the two keep their names; a role's description is set when it is created; adopting an existing Knovas group as a role's group moves to RB-5 (it brings data); a group Knovas no longer knows counts as deleted; no second administrator for role changes |
| 21 Nov | coordinator (RB-5 work order) | A role's data changes follow the existing four-eyes path and its bypass setting (founder told; may tighten it); only an administrator carries one out; adopting a group and moving direct grants need no second person; data are written to Knovas before «Ein» and read back, never kept in the Platform; one role per directly held group; a removal never empties a rule (it deletes it) |

## Known limits and open points

- **Phone width (FU-23):** the five Admin pages and the settings page scroll sideways; PMVP-40 reworks the Admin ones.
- **Contrast of the old green tints (FU-28)** below 4.5 to 1; PMVP-40 replaces them with the brand's.
- **The Admin card was over its size budget (FU-17)**; PMVP-40 split it. After the role builder it was over again
  (FU-44); RB-FIX-1 gives the Rollen page and the Agenten page a card each («Rollen», «Agenten»), because one card for
  both would still have been over the budget.
- **"Was sieht …?" and the enforcement position** are still the Builder's files (FU-20).
- Ingestion, document fields and System have no card; their domain is open.
- Small findings: an approver who is also an ingestion manager lands on Ingestion; malformed roles give an error page;
  a signed-out call to the Builder's data routes is redirected to sign-in instead of refused (FU-24).
- The settings page's section title "Ansichten und Wissensbasis" now also holds the assistant's entries.
- **Roles and reading:** a role's group decides what its members may read only once enforcement is on («Ein»,
  PMVP-42, with the Core's CR-R1 and CR-R2) and data are attached to it (RB-5). Until then roles decide what a person
  may do; the part «Darf lesen» says "wirkt mit «Ein»".
- **A role's data, open points (RB-5):** a new folder rule reaches documents already read in only after Knovas
  re-applies the rules (CR-R3); a firm with many documents may see a note that not every document with its own rights
  is listed (the page reads a bounded number of pages from Knovas); the rows show no document counts per folder (one
  more Knovas call per row); the approvals page names the folder but not the role's name; people who keep direct
  grants after the move (end dates, the administrator group, unknown groups, groups of roles they are not in) are not
  handled yet (founder or coordinator).
- **Three readers of groups** (the Experimente search's "missing group" hint, the Builder's list of who holds a group,
  one check of viewers without groups) read only a person's direct groups until RB-FIX-1 (built, not yet verified):
  now each counts a group she holds through a role, and a direct group whose end date has passed no longer counts,
  as for what Knovas lets her read (FU-42).
- **Agents act only as far as the Core does.** Today the assistant's claim is signed and logged and narrows nothing
  (enforcement is off); agents are registered and members of roles, and the switch stays off. With CR-R1 («Ein»
  possible) the assistant reads only what its roles and the person hold; with CR-R5 the Core takes the external
  agents' claims; with CR-R6 an agent's own gateway key works. Until then the Knovas GmbH pilot keeps today's access
  for Claude Code (the internal MCP server, or a company key of the gateway).
- **At most 20 calls a minute per external agent** (the Core's pool maximum); more needs a higher maximum at the Core.
- **A deleted person's agent** disappears with her; the Core keeps its claims until they end (at most 300 seconds).

## Depends on

The Platform's identity store (accounts, roles, sessions, audit, approval queue) in the Platform's database; the
Knovas Core's access routes (groups, folder rules, document access, erasing); the Core's rights engine for enforcement;
the shell (navigation, look, page parts).

## Owner

The engine owner role, proposed on the cards (Verwaltung, "Zugriff", and «Rollen» and «Agenten» on RB-FIX-1's branch);
a person has still to confirm it.

**Where the detail is:** `docs/platform-mvp/work-orders/PMVP-11.md`, `PMVP-40.md` (branch `feat/pmvp-40`), `PMVP-25.md`
(branch `feat/pmvp-25`), `RB-1-2.md` (branch `feat/rb-1-2`), `RB-3-4.md` (branch `feat/rb-3-4`), `RB-6-7.md`
(branch `feat/rb-6-7`), `RB-FIX-1.md` (branch `maint/rb-fix-1`), `RB-5.md` (branch `feat/rb-5`); `KnovasPlatform/docs/agents.md`;
`docs/platform-mvp/design/role-builder.md`; `docs/components/L2-platform-admin.md`, `L2-platform-roles.md`,
`L2-platform-agents.md`; `docs/platform-mvp/follow-ups.md`.
