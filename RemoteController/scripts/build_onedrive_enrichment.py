"""One-shot Microsoft Graph walker that writes the search-enrichment JSONL
for KnovasPlatform's docbridge-web (SEARCH_ENRICHMENT_PATH).

Use this when you do not want to wait for the OneDrive mirror's first full
pass (which downloads file content). This script only fetches metadata,
so it finishes in a few minutes even for large drives.

Env vars (re-used from the OneDrive mirror config):
  ONEDRIVE_TENANT_ID, ONEDRIVE_CLIENT_ID, ONEDRIVE_CLIENT_SECRET   required
  ONEDRIVE_DRIVE_ID                                                required
  ONEDRIVE_ROOT_PATH                                               optional
  ONEDRIVE_IDENTIFIER_PREFIX                                       optional
  ONEDRIVE_ALLOWED_EXTENSIONS                                      optional
  OUT                                                              optional, default /mirror/.search_enrichment.jsonl

Typical invocation inside the RC container:
  docker compose exec remote-controller python3 \\
    /app/scripts/build_onedrive_enrichment.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import quote


def _require(name: str) -> str:
    val = (os.environ.get(name) or "").strip()
    if not val:
        print(f"missing required env var {name}", file=sys.stderr)
        sys.exit(2)
    return val


TENANT = _require("ONEDRIVE_TENANT_ID")
CLIENT_ID = _require("ONEDRIVE_CLIENT_ID")
CLIENT_SECRET = _require("ONEDRIVE_CLIENT_SECRET")
DRIVE_ID = _require("ONEDRIVE_DRIVE_ID")
ROOT_PATH = (os.environ.get("ONEDRIVE_ROOT_PATH") or "").strip().strip("/")
PREFIX = (os.environ.get("ONEDRIVE_IDENTIFIER_PREFIX") or "").strip().strip("/")
OUT = os.environ.get("OUT") or "/mirror/.search_enrichment.jsonl"
EXT = {
    e.strip().lower().lstrip(".")
    for e in (
        os.environ.get("ONEDRIVE_ALLOWED_EXTENSIONS") or "pdf,docx,txt,md,eml,msg"
    ).split(",")
    if e.strip()
}


def fetch_token() -> str:
    body = (
        f"client_id={CLIENT_ID}"
        f"&client_secret={quote(CLIENT_SECRET)}"
        f"&scope=https%3A%2F%2Fgraph.microsoft.com%2F.default"
        f"&grant_type=client_credentials"
    ).encode()
    url = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
    req = urllib.request.Request(url, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())["access_token"]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"token request failed: HTTP {exc.code} {body[:400]}", file=sys.stderr)
        sys.exit(3)


TOKEN = fetch_token()


def graph_get(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"graph error {exc.code} on {url}: {body[:400]}", file=sys.stderr)
        raise


def paginate(url: str):
    while url:
        data = graph_get(url)
        for item in data.get("value", []):
            yield item
        url = data.get("@odata.nextLink")


def doc_id_for(rel_posix: str) -> str:
    rel = rel_posix.replace("\\", "/").strip("/")
    return f"{PREFIX}/{rel}" if PREFIX else rel


def emit_file(item: dict, rel_posix: str, out, counter: list[int]) -> None:
    name = item.get("name") or ""
    if not name:
        return
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in EXT:
        return
    web = (item.get("webUrl") or "").strip()
    if not web:
        return
    row = {
        "doc_id": doc_id_for(rel_posix),
        "web_url": web,
        "title": name,
        "modified_at": item.get("lastModifiedDateTime"),
    }
    out.write(json.dumps(row, ensure_ascii=False) + "\n")
    counter[0] += 1
    if counter[0] % 200 == 0:
        print(f"  {counter[0]} entries…", flush=True)


def walk(item_id: str, rel: str, out, counter: list[int]) -> None:
    url = f"https://graph.microsoft.com/v1.0/drives/{DRIVE_ID}/items/{item_id}/children"
    for it in paginate(url):
        name = it.get("name") or ""
        if not name or "/" in name or "\\" in name:
            continue
        child_rel = f"{rel}/{name}" if rel else name
        if "folder" in it:
            walk(it["id"], child_rel, out, counter)
        elif "file" in it:
            emit_file(it, child_rel, out, counter)


def main() -> None:
    if ROOT_PATH:
        start = (
            f"https://graph.microsoft.com/v1.0/drives/{DRIVE_ID}/root:/"
            f"{quote(ROOT_PATH)}:/children"
        )
    else:
        start = f"https://graph.microsoft.com/v1.0/drives/{DRIVE_ID}/root/children"

    counter = [0]
    out_dir = os.path.dirname(OUT)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as out:
        for it in paginate(start):
            name = it.get("name") or ""
            if not name or "/" in name or "\\" in name:
                continue
            if "folder" in it:
                walk(it["id"], name, out, counter)
            elif "file" in it:
                emit_file(it, name, out, counter)
    print(f"wrote {counter[0]} entries to {OUT}", flush=True)


if __name__ == "__main__":
    main()
