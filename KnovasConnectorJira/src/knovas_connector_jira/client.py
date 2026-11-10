"""The few calls of Jira Cloud's REST API (version 3) the connector needs, for one site, with the source's credential.

Everything that reaches Jira goes through ``JiraClient.call``: the credential goes into the ``Authorization`` header
and nowhere else; a 429 waits as ``Retry-After`` says (at most ``max_wait`` seconds, at most ``retries`` times); a read
(GET) that answers 5xx is retried the same way; a write is never repeated by the client, because a lost answer is
recovered through the permit marker (``connector.py``); a redirect is never followed (a 3xx is ``jira.http_3xx``).
Every failure becomes a ``JiraError`` carrying a code only: an exception text from the HTTP library may quote the URL, a
header or the credential, so it is dropped (``from None``) and never logged (GI-DATA-04, GI-CONN-07).

A source's settings (``source["settings"]``) name the site (``https://<host>``, no user, no path), the project key, and
optionally the fields to read and the fields an automation computes. There is no default site and no default project:
both are configuration at the site, never code. The credential is read at the site from the environment
(``credential_from_environment``): ``<account email>:<API token>`` for basic authentication, or a bare token for a
bearer token, together with the one host it may be sent to, which the site names beside it. The settings come from
the Platform, so they only have to agree: a site whose host is not the credential's is refused ``jira.bad_settings``
before any request, and every request goes to the credential's host (GI-CONN-07, SPEC-write-back-20).
"""
from __future__ import annotations

import base64
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

import requests

API = "/rest/api/3"
_HOST = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
HOST = re.compile(f"^{_HOST}$")
SITE = re.compile(f"^https://{_HOST}$")
PROJECT = re.compile(r"^[A-Z][A-Z0-9_]{1,9}$")
ISSUE_KEY = re.compile(r"^([A-Z][A-Z0-9_]{1,9})-([1-9][0-9]{0,9})$")
FIELD_ID = re.compile(r"^(?:[a-z][A-Za-z]{0,40}|customfield_[0-9]{1,10})$")
#: The fields read when the settings name none, by Jira's field ids: what a Brain mapping of an issue reads, and no
#: more (the recommendation for the founder's decision D-34): no reporter or other person but the assignee (as an
#: account id), no resolution, no comment, attachment or worklog. The parent comes as a link (``LINK_FIELDS``).
DEFAULT_FIELDS = ("summary", "description", "status", "assignee", "duedate", "priority", "labels", "issuetype",
                  "created", "resolutiondate")
#: Read for the record's links, never as a field: the parent, which the mapping reads. Issue links and sub-tasks are
#: neither read nor carried.
LINK_FIELDS = ("parent",)
#: Never a field of the settings: the links Jira keeps between issues (other issues' keys), read as links or not at all.
NOT_FIELDS = ("issuelinks", "parent", "subtasks")
MAX_FIELDS = 100


class JiraError(Exception):
    """A call failed; ``code`` is a code of the protocol's shape (``jira.http_404``, ``jira.rate_limited``), never a
    sentence, and ``status`` the result status it answers as."""

    def __init__(self, code: str, status: str = "failed"):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Settings:
    site: str
    project: str
    fields: tuple[str, ...]
    computed_fields: frozenset
    page_size: int


def settings_of(source: Mapping[str, Any]) -> Settings:
    """The source's settings, checked; ``jira.bad_settings`` when the site, the project or a field id is not of its
    form (a site with a user, a password, a path or plain http is refused)."""
    raw = source.get("settings") if isinstance(source.get("settings"), Mapping) else {}
    site, project = str(raw.get("site") or "").rstrip("/"), str(raw.get("project") or "")
    fields = raw.get("fields") or list(DEFAULT_FIELDS)
    computed = raw.get("computed_fields") or []
    page_size = raw.get("page_size", 100)
    if not SITE.match(site) or not PROJECT.match(project):
        raise JiraError("jira.bad_settings")
    if not isinstance(fields, list) or not isinstance(computed, list) or len(fields) > MAX_FIELDS:
        raise JiraError("jira.bad_settings")
    if any(not isinstance(f, str) or not FIELD_ID.match(f) or f in NOT_FIELDS for f in fields + computed):
        raise JiraError("jira.bad_settings")
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 100:
        raise JiraError("jira.bad_settings")
    return Settings(site, project, tuple(dict.fromkeys(fields)), frozenset(computed), page_size)


@dataclass(frozen=True)
class Credential:
    """A source's credential at the site and the one host it may be sent to; ``repr`` never shows the secret."""
    secret: str = field(repr=False)
    host: str


def credential_from_environment(source: Mapping[str, Any],
                                environ: Mapping[str, str] = os.environ) -> Optional[Credential]:
    """The source's credential at the site: ``SYNC_CREDENTIAL_<KEY>`` (the source key in upper case, other characters
    as ``_``), else ``SYNC_CREDENTIAL_JIRA`` for every Jira source, as the sync component names a source's credential;
    bound to the host on the line beside it, ``SYNC_CREDENTIAL_<KEY>_HOST`` (``SYNC_CREDENTIAL_JIRA_HOST``), the host
    name only. A host is never taken from the other line. A credential without its host line, or with one that is not
    a host name, is no credential: it goes nowhere (``jira.no_credential``)."""
    for name in (str(source.get("key") or ""), "jira"):
        if name:
            line = "SYNC_CREDENTIAL_" + "".join(c if c.isalnum() else "_" for c in name).upper()
            found = environ.get(line)
            if found:
                host = (environ.get(line + "_HOST") or "").strip().lower()
                return Credential(found, host) if HOST.match(host) else None
    return None


def authorization(credential: Optional[str]) -> str:
    """The ``Authorization`` header for a credential: basic for ``email:token``, bearer for a bare token."""
    if not credential or any(c in credential for c in "\r\n"):
        raise JiraError("jira.no_credential")
    if ":" in credential:
        return "Basic " + base64.b64encode(credential.encode("utf-8")).decode("ascii")
    return "Bearer " + credential


class JiraClient:
    """Calls to the credential's host only: ``site`` (from the settings) must be ``https://`` and that host, else
    ``jira.bad_settings`` before any request; no credential, or one bound to no host, is ``jira.no_credential``."""

    def __init__(self, site: str, credential: Optional[Credential], *, session: Any = None, timeout: float = 60,
                 sleep: Callable[[float], None] = time.sleep, retries: int = 3, max_wait: float = 30):
        if not isinstance(credential, Credential) or not HOST.match(credential.host or ""):
            raise JiraError("jira.no_credential")
        header = authorization(credential.secret)
        if site != "https://" + credential.host:
            raise JiraError("jira.bad_settings")
        self.site = "https://" + credential.host
        self.headers = {"Accept": "application/json", "Authorization": header}
        self.session = session or requests.Session()
        self.timeout, self.sleep, self.retries, self.max_wait = timeout, sleep, retries, max_wait

    def _wait(self, answer: Any, attempt: int) -> float:
        try:
            wanted = float((getattr(answer, "headers", None) or {}).get("Retry-After", ""))
        except (TypeError, ValueError):
            wanted = 2.0 ** attempt
        return max(1.0, min(self.max_wait, wanted))

    def call(self, method: str, path: str, *, params: Optional[dict] = None, body: Any = None,
             ok: tuple = (200,)) -> tuple[int, Any]:
        """One call; ``(status, JSON or None)`` when the status is in ``ok``, else ``JiraError``."""
        url = self.site + API + path
        attempt = 0
        while True:
            try:
                answer = self.session.request(method, url, params=params, json=body, headers=dict(self.headers),
                                              timeout=self.timeout, allow_redirects=False)
            except requests.RequestException:
                raise JiraError("jira.unreachable") from None
            status = answer.status_code
            retry = status == 429 or (status >= 500 and method == "GET")
            if retry and attempt < self.retries:
                self.sleep(self._wait(answer, attempt))
                attempt += 1
                continue
            if status in ok:
                if status == 204 or not getattr(answer, "content", b"x"):
                    return status, None
                try:
                    return status, answer.json()
                except ValueError:
                    raise JiraError("jira.bad_answer") from None
            if status == 429:
                raise JiraError("jira.rate_limited")
            if status == 401:
                raise JiraError("jira.unauthorized")
            raise JiraError(f"jira.http_{status}")


def issue_key(target: Any, settings: Settings) -> Optional[str]:
    """``target`` when it is an issue key of the source's project, else None (never another project's issue)."""
    if not isinstance(target, str):
        return None
    m = ISSUE_KEY.match(target)
    return target if m and m.group(1) == settings.project else None


def key_order(key: str) -> tuple:
    m = ISSUE_KEY.match(key)
    return (m.group(1), int(m.group(2))) if m else (key, 0)
