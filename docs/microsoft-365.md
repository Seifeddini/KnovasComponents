# Documents in OneDrive or SharePoint

Knovas can index a folder in **OneDrive or SharePoint** directly. The same
setting handles both, and no copy of the documents is kept on the server.

```bash
# knovas.env -- instead of KNOVAS_DOCUMENTS_PATH
KNOVAS_DOCUMENTS_URL=https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Akten
M365_CLIENT_ID=00000000-0000-0000-0000-000000000000
M365_CLIENT_SECRET=...
```

Then run `./scripts/setup.sh && ./scripts/start.sh`. `start.sh` checks the
folder right away, and `./scripts/doctor.sh` checks it again at any time.

## What happens

| | |
|---|---|
| **Sync** | Knovas Connector asks Microsoft 365 what changed since its last cycle: one request when nothing did. It downloads a file only when the file is new or modified, into a private temporary folder. It indexes the file and deletes the temporary copy. |
| **On the server** | No documents. A file is held only in memory between download and indexing. What stays is the snippet text shown under each result, the sync state, and the library's item list: names, dates, sizes, IDs and web addresses. The item list covers **the whole library** the folder is in, because Microsoft's change feed only works on a whole library. It holds no content. The server's disk does not grow with the size of the documents. |
| **Deleted, moved, renamed** | Deleting a file in OneDrive/SharePoint removes it from Knovas at the next cycle. Moving or renaming a file, or a whole folder, re-indexes it under its new path. |
| **Search** | Unchanged. Results show their text and "Fundstellen" (the places where the search matched) as before. |
| **Öffnen** | Opens the document in OneDrive/SharePoint (Word, Excel and the PDF viewer in the browser). Microsoft's own permissions decide who may open it there. |
| **Vorschau** (preview) | Microsoft 365's own viewer, embedded in the preview dialog. It opens on the page of the match, and clicking another match opens that page. If Microsoft is unreachable, the dialog shows the indexed text instead. |
| **Admin: Übernahme** (ingestion profile) | The folder picker shows the OneDrive/SharePoint subfolders. Per-folder access groups, file types and schedules work as for a file share. |

## The address

Open the folder in the browser, in OneDrive or SharePoint, and copy the
address bar. All of these work:

- `https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Akten`
- `https://contoso.sharepoint.com/sites/Kanzlei/Shared%20Documents/Forms/AllItems.aspx?id=%2Fsites%2FKanzlei%2FShared%20Documents%2FAkten`
- `https://contoso-my.sharepoint.com/my?id=%2Fpersonal%2Fanna_contoso_ch%2FDocuments%2FAkten` (OneDrive)
- `https://contoso-my.sharepoint.com/personal/anna_contoso_ch/Documents/Akten` (OneDrive)
- a library root: `.../Shared%20Documents`. A whole OneDrive: `.../personal/anna_contoso_ch`.

A **"Copy link" sharing link** such as `https://contoso.sharepoint.com/:f:/s/Kanzlei/EgAb...` does
**not** work. It hides the folder's location, and Microsoft only resolves such
links for apps with write access. Paste the address bar instead; setup tells
you if you pasted a sharing link.

One installation indexes one folder with everything below it. If the documents
are spread across several people's OneDrives, put them in one shared
SharePoint or Teams library first.

## The Entra app (the firm's Microsoft 365 administrator, once)

1. **Entra admin center → App registrations → New registration.** Name it
   `Knovas`, single tenant, no redirect URI.
2. **API permissions → Add a permission → Microsoft Graph → Application
   permissions → `Sites.Read.All`.** Then **Grant admin consent**.
   - That is the only permission needed. It is read-only: Knovas never changes
     anything in Microsoft 365.
3. **Certificates & secrets → New client secret.** Choose 12 or 24 months
   and note the expiry date in a calendar.
4. Put the **Application (client) ID** into `M365_CLIENT_ID` and the secret's
   **Value** (not its ID) into `M365_CLIENT_SECRET`.

`M365_TENANT_ID` is optional. For `contoso.sharepoint.com` the tenant is
`contoso.onmicrosoft.com`, and setup uses that automatically.

**Scope, plainly.** `Sites.Read.All` lets the app read every SharePoint site
and OneDrive in the firm's tenant, not only the configured folder.
- **Content:** Knovas downloads and indexes only files inside the configured
  folder.
- **Item list:** for the library that folder is in, it keeps names, dates,
  sizes and addresses (see above).

A firm that wants Microsoft to enforce a narrower limit can grant
`Sites.Selected` instead and give the app read access to that one site. This
setup has not been tested with that permission; check it with `doctor.sh`
before relying on it.

**Large libraries:** the item list covers the whole library, so a library with
millions of items takes long for its first read. Point it at a dedicated
library for Knovas rather than at a sprawling archive.

## Where the secret goes

The secret is in `knovas.env` only. Docker Compose passes it to the
Knovas Connector container, which is reachable only from the server itself. It
is never written into the generated `.env.generated` files, and the Platform,
which faces the users, never receives it. The Platform asks Knovas Connector
for preview links instead.

**Rotating the secret:** create a new one in Entra, replace
`M365_CLIENT_SECRET` in `knovas.env`, run `./scripts/start.sh`, then delete the
old secret in Entra.

**Stopping access:** delete the app registration in Entra. Microsoft 365 access
ends immediately.

## Network

Outbound HTTPS from the server to `login.microsoftonline.com` and
`graph.microsoft.com`, in addition to the Knovas API. Users' browsers load the
preview from `*.sharepoint.com`, which is where they open their documents
anyway.

## When it does not work

```bash
docker compose --env-file knovas.env exec knovas-connector python -m m365.check
```

It signs in, resolves the address to a library and folder, reads the folder and
counts what would be indexed. Every failure names its fix:

| Message | Fix |
|---|---|
| `sharing link` | Paste the address bar, not "Copy link". |
| `sign-in: Microsoft rejected the app's credentials` | Wrong client ID or secret, or the secret expired. |
| `HTTP 403 … Sites.Read.All` | The permission is missing, or admin consent was not granted. |
| `No OneDrive or SharePoint document library matches` | The address does not name a library. Open the folder itself and copy again. |
| `is a file` | The address points at a document; use the folder that contains it. |
| `The configured … folder no longer exists` | The folder was deleted or access was withdrawn. Nothing is removed from Knovas. The address is resolved again each cycle, so a folder re-created at the same address is picked up by itself. |

The first sync of a large library takes a while. Search finds documents as
they are indexed, and `doctor.sh` shows how many have snippet text so far.

## Compared with the earlier OneDrive mirror

The `ONEDRIVE_*` mirror in Knovas Connector copied the whole drive onto the
server, and a separate sync then indexed the copy. `KNOVAS_DOCUMENTS_URL`
replaces it: no copy, a single setting, SharePoint as well as OneDrive, and
correct handling of renamed folders. When both are configured, the mirror is not
started.
