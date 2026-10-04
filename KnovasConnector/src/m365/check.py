"""``python -m m365.check`` -- does the configured OneDrive/SharePoint folder work?

Run inside the RemoteController container (``./scripts/doctor.sh`` does)::

    docker compose --env-file knovas.env exec remote-controller python -m m365.check

It signs in as the app, resolves the address to a library and folder, reads
the folder's change feed and counts what would be indexed. Every failure is
printed with what to do about it, and the exit code is 0 only if all of it
worked.
"""
from __future__ import annotations

import sys
from pathlib import Path

from m365.location import LocationError, default_tenant_for_host, parse_folder_url
from m365.source import M365Error, M365Settings, M365Source
from onedrive_mirror.graph import GraphAuthError
from sync.document_text import is_syncable_extension


def main() -> int:
    settings = M365Settings.from_env()
    if settings is None:
        print("SKIP  M365_FOLDER_URL is not set -- documents come from a folder on this server.")
        return 0

    try:
        location = parse_folder_url(settings.folder_url)
    except LocationError as exc:
        print(f"FAIL  address: {exc}")
        return 1
    kind = "OneDrive" if location.is_onedrive else "SharePoint"
    print(f"ok    address: {kind} folder {location.server_relative_path} on {location.hostname}")

    problems = settings.problems()
    if problems:
        for problem in problems:
            print(f"FAIL  {problem}")
        return 1
    tenant = settings.tenant_id or default_tenant_for_host(location.hostname)
    print(f"ok    app: client {settings.client_id[:8]}... in tenant {tenant}")

    source = M365Source(settings)
    try:
        resolved = source.resolved()
    except M365Error as exc:
        cause = exc.__cause__
        if isinstance(cause, GraphAuthError) or "AADSTS" in str(exc):
            print(
                "FAIL  sign-in: Microsoft rejected the app's credentials. Check "
                "M365_CLIENT_ID, M365_CLIENT_SECRET (not expired?) and the tenant."
            )
        print(f"FAIL  resolve: {exc}")
        return 1
    print(f"ok    library: {resolved.drive_web_url}")
    print(f"ok    folder:  /{resolved.folder_path}" if resolved.folder_path else "ok    folder:  (library root)")

    try:
        source.refresh()
        files = source.files()
        folders = source.folders()
    except M365Error as exc:
        print(f"FAIL  change feed: {exc}")
        return 1
    indexable = [f for f in files.values() if is_syncable_extension(Path(f.name).suffix)]
    print(
        f"ok    contents: {len(folders)} folder(s), {len(files)} file(s), "
        f"{len(indexable)} of a type Knovas indexes"
    )
    if files and not indexable:
        print("WARN  none of the files is a type Knovas reads (pdf, docx, txt, md, eml, msg, ...).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
