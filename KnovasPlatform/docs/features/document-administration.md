# Document administration and folder access rules

The administration console (sidebar entry **Verwaltung**, visible to users
holding the `admin` role) has three tabs: **Personen**, **Dokumente** and
**Zugriffsgruppen**. This page describes the last two and the access model
behind them.

Design: `docs/superpowers/specs/2026-08-29-admin-document-rbac-design.md`.
Backend invariant: GI-ACCESSROLES-10 (folder indirection preserves
visibility), checked by the Alloy model `document_acl_filter.als` in
KnowledgeBase.

## Dokumente

Lists every document the tenant has uploaded — **as far as the signed-in
administrator may see it**. Ethical walls bind the administrator too (design
decision D1): a document the administrator's own access groups exclude does
not appear here, and there is no "show everything" switch anywhere in the
console. The count shown above the list is the backend's aggregate for the
active filter, not the number of rows loaded.

Filters: folder prefix, access group, *nur ohne Gruppe* (unrestricted
documents) and *nur Konflikte* (see Deduplication below).

### Per-document access

Select one or more rows, tick the groups in *Auswahl einer Zugriffsgruppe
zuordnen*, and submit. Semantics:

- **Replace, not merge.** The selected documents end up with exactly the
  ticked groups. Ticking nothing releases them (unrestricted).
- **You may only assign groups you dominate.** The backend refuses a group
  outside the administrator's own subtree, and the console never asks for a
  system principal to get around that.
- Every change is audited (`document.acl_changed`) with the acting
  administrator, the pointers and the resulting groups.

## Zugriffsgruppen

The group tree, and the **Ordnerregeln** (folder rules).

### Ordnerregeln

A folder rule maps a pointer prefix (for example `rc-sync/mandate/muster/`)
to a set of groups.

- **Longest matching prefix wins.** A rule on `rc-sync/mandate/` and a rule
  on `rc-sync/mandate/muster/` both exist; a document under `muster/` is
  governed by the more specific one.
- **New documents inherit at ingest.** A document filed under a governed
  prefix is born with that rule; it is never ingested unrestricted and then
  repaired.
- **Changing a rule is one write and takes effect immediately.** Documents
  carry a reference to the rule, not a copy of its groups, so re-classifying a
  folder rewrites one row. This is the point of the design and the screen says
  so.
- **Existing documents adopt a rule only through a backfill.** Documents that
  were ingested before the rule existed keep their own ACL until a backfill
  runs. The backfill is streaming and resumable and reports progress; a
  partially applied rule is never shown as applied.

### Deduplication

Identical content filed in two folders is **one document with one ACL**.
Where two folder rules would govern the same content, the most restrictive
applies. Where the two rules leave **no reader at all**, the document is
parked as a conflict for a human decision rather than silently released or
silently hidden. The *nur Konflikte* filter on the Dokumente tab finds those.

## Freigaben (four-eyes)

Access changes made in the console — per-document groups, folder rules — are
guarded actions. Whether they run immediately depends on who acts:

- **An administrator acts alone**, by decision (2026-08-14). The change runs and
  an `approval.bypassed` row records who did what. The Freigaben tab lists these
  under *Umgehungen durch Administratoren*; they are never hidden.
- **Strict mode** (*Strikt* on the Freigaben tab) makes administrators queue like
  everyone else.
- A queued request waits for a second person holding the `approver` or `admin`
  role. The requester cannot confirm their own. On approval the console carries
  the change out and marks the request executed; on rejection the reason is kept.
- An approved request whose execution failed or whose kind the console cannot
  execute stays visible under *Freigegeben, noch nicht ausgeführt* with an
  *Ausführen* button to retry where possible.
- Requests expire after 24 hours.

State this to a buyer as it is: with the bypass on, four-eyes covers ordinary
users and not the most privileged account.

## RemoteController

`sources[].access_groups` in the sync request assigns groups to every
document ingested from that source, and RemoteController passes them to
`/secured/init_document_transmission` so the documents are born walled.
Omit the key for unrestricted folders; an empty array is treated as omitted so
that a backend folder rule can still apply.

**Caveat — `sequential_subfolders`.** With that option enabled,
RemoteController processes exactly one source per cycle and logs a warning if
more than one is configured. Per-source `access_groups` still apply, but only
the first source is walked, so put the walled folder in its own sync
configuration rather than relying on a second source entry.

## Scale

The Dokumente list pages by **keyset cursor**: the backend returns
`next_after`, the browser hands it straight back for the next page. There is
no page number and no total page count, deliberately — an offset walk fails
on a large tenant, and the console must never hold the corpus. The screen
holds one page at a time.

## What is not enforced yet

Until the principal assertion is minted on every Knovas call and verified by
the backend (SS-340, SS-342, SS-343), search still resolves every request as
unasserted and returns unrestricted documents only. Rules and ACLs set here
are recorded and will take effect once that chain is live. The console must
not be read as proof of enforcement before then — see REQ-A4 on SS-326.
