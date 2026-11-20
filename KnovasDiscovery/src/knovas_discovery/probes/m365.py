"""m365: licences, people, SharePoint sites, drive sizes, Exchange Online and Teams, from Microsoft Graph.

Client-credentials flow with an app registration (application permissions, read-only: User.Read.All,
Organization.Read.All, Sites.Read.All, Group.Read.All). Written against Graph's documented answers and tested with a
fake transport; it has NOT run against a tenant yet.

The HTTP access is one object with `post(url, data=, timeout=)` and `get(url, headers=, timeout=)` (a
requests.Session); the tests replace it.
"""

from __future__ import annotations

import time
from urllib.parse import quote

from ..util import ProbeError, error_code, result
from . import Context, Output

FACTS = {
    "people": "enabled member accounts with at least one licence (guests, disabled and unlicensed accounts are not people)",
    "licences": "licences in use per product (subscribedSkus: skuPartNumber -> consumedUnits)",
    "sharepoint_sites": "SharePoint sites, without personal (OneDrive) sites",
    "drives_bytes": "bytes used in the document libraries of those sites and in the people's OneDrives",
    "exchange_online": "whether any account has an enabled Exchange Online plan",
    "teams": "groups that are Microsoft Teams",
}

LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
MAX_PAGES = 200
TIMEOUT = 30


class Graph:
    def __init__(self, session, tenant_id: str, client_id: str, secret: str):
        self._s = session
        token_url = f"{LOGIN}/{quote(tenant_id, safe='')}/oauth2/v2.0/token"
        r = self._s.post(token_url, data={"client_id": client_id, "client_secret": secret,
                                          "scope": "https://graph.microsoft.com/.default",
                                          "grant_type": "client_credentials"}, timeout=TIMEOUT)
        body = _json(r)
        if r.status_code != 200 or "access_token" not in body:
            raise ProbeError("m365_token_failed:" + str(body.get("error") or r.status_code))
        self._headers = {"Authorization": "Bearer " + body["access_token"], "Accept": "application/json"}

    def get(self, path: str) -> dict:
        url = path if path.startswith("https://") else GRAPH + path
        for attempt in (1, 2):
            r = self._s.get(url, headers=self._headers, timeout=TIMEOUT)
            if r.status_code == 429 and attempt == 1:
                time.sleep(min(int(getattr(r, "headers", {}).get("Retry-After", "5") or 5), 30))
                continue
            if r.status_code != 200:
                raise ProbeError(f"graph_http_{r.status_code}")
            return _json(r)
        raise ProbeError("graph_http_429")

    def all(self, path: str) -> list[dict]:
        items, url, pages = [], path, 0
        while url and pages < MAX_PAGES:
            body = self.get(url)
            items.extend(body.get("value") or [])
            url = body.get("@odata.nextLink")
            pages += 1
        return items


def _json(response) -> dict:
    try:
        body = response.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}


def _personal(site: dict) -> bool:
    return bool(site.get("isPersonalSite")) or "-my.sharepoint.com" in str(site.get("webUrl", "")).lower()


def m365_facts(graph: Graph) -> tuple[dict, dict, list[str]]:
    """Facts from Graph; a call that fails leaves its fact null and adds a code to the errors."""
    facts: dict = {k: None for k in FACTS}
    detail: dict = {}
    errors: list[str] = []

    def attempt(name: str, fn):
        try:
            fn()
        except Exception as exc:
            errors.append(f"{name}:{error_code(exc)}")

    users: list[dict] = []

    def do_users():
        users.extend(graph.all("/users?$select=id,accountEnabled,userType,assignedLicenses,assignedPlans&$top=999"))
        people = [u for u in users if u.get("accountEnabled") and (u.get("userType") or "Member") == "Member"
                  and u.get("assignedLicenses")]
        facts["people"] = len(people)
        facts["exchange_online"] = any(p.get("service", "").lower() == "exchange"
                                       and p.get("capabilityStatus") == "Enabled"
                                       for u in users for p in (u.get("assignedPlans") or []))

    def do_licences():
        skus = graph.all("/subscribedSkus")
        facts["licences"] = {s["skuPartNumber"]: int(s.get("consumedUnits") or 0)
                             for s in sorted(skus, key=lambda s: s.get("skuPartNumber", "")) if s.get("skuPartNumber")}

    sites: list[dict] = []

    def do_sites():
        try:
            found = graph.all("/sites/getAllSites?$select=id,webUrl,isPersonalSite&$top=999")
        except ProbeError:
            found = graph.all("/sites?search=*")
        sites.extend(s for s in found if not _personal(s))
        facts["sharepoint_sites"] = len(sites)
        detail["sites"] = sorted(str(s.get("webUrl", "")) for s in sites)

    def do_drives():
        total = 0
        for s in sites:
            for d in graph.all(f"/sites/{quote(s['id'], safe=',')}/drives?$select=id,quota"):
                total += int((d.get("quota") or {}).get("used") or 0)
        for u in users:
            if not (u.get("accountEnabled") and u.get("assignedLicenses")):
                continue
            try:
                d = graph.get(f"/users/{quote(u['id'], safe='')}/drive?$select=id,quota")
            except ProbeError as exc:
                if exc.code == "graph_http_404":  # no OneDrive for this account
                    continue
                raise
            total += int((d.get("quota") or {}).get("used") or 0)
        facts["drives_bytes"] = total

    def do_teams():
        teams = graph.all("/groups?$filter=resourceProvisioningOptions/Any(x:x%20eq%20'Team')&$select=id&$top=999")
        facts["teams"] = len(teams)

    attempt("users", do_users)
    attempt("licences", do_licences)
    attempt("sites", do_sites)
    if facts["sharepoint_sites"] is not None:
        attempt("drives", do_drives)
    attempt("teams", do_teams)
    return facts, detail, errors


def run(target: dict, ctx: Context) -> list[Output]:
    key = target["key"]
    if not target.get("tenant_id") or not target.get("client_id"):
        return [Output(result(key, "skipped", "no_tenant_in_intake"))]
    secret = ctx.secrets.get(target.get("secret_env"), f"Microsoft 365 {key} (client secret)")
    if not secret:
        return [Output(result(key, "failed", f"secret_missing:{target.get('secret_env') or 'secret_env'}"))]
    session = ctx.http_session
    if session is None:
        import requests

        session = requests.Session()
    try:
        graph = Graph(session, str(target["tenant_id"]), str(target["client_id"]), secret)
        facts, detail, errors = m365_facts(graph)
        status = "ok" if not errors else ("failed" if all(facts[k] is None for k in FACTS) else "partial")
        ctx.log(f"m365 {key}: {status}")
        return [Output(result(key, status, ",".join(errors) or None, facts), detail)]
    except Exception as exc:
        ctx.log(f"m365 {key}: failed ({error_code(exc)})")
        return [Output(result(key, "failed", error_code(exc)),
                       {"message": ctx.secrets.scrub(f"{type(exc).__name__}: {exc}")})]
