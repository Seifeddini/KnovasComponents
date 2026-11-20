"""The four checks the plan asks of every page (FEAT-2026-11-19-platform-mvp section 11, PMVP-03):
signed out, wrong role, wrong owner, missing token.

One function per check, on the clients ``conftest.py`` already offers (``anon_client``, ``member_client``,
``alice_client``, ``bob_client``, ``admin_client``, ``admin_client_no_csrf``). ``test_pmvp_pages_examples.py`` shows
them on one page per area.

A check that fails names the address, the role and what came back as numbers (status, content type, lengths),
never the page's text: a failure is printed into a build log.

How the Platform refuses today (measured on ``feat/platform-mvp``, 2026-11-20); the checks expect exactly this:

=============  ====================================================================================================
signed out     an address under ``/api/``: 401 as JSON. Every other address, the Builder's JSON routes under
               ``/builder/api/`` included: 302 to ``/login?next=<the address>``.
wrong role     403: the Admin console with the server's plain page, the Builder with a page of its own, JSON routes
               with JSON. Experimente is the exception: 404, the module hides itself (pass ``status=404``).
wrong owner    the same response as for an address that does not exist. Documents: 404 as JSON for a document the
               person's own search did not return. A Builder workspace someone holds no grant for: 403 like any
               other workspace id, because the role check answers before the lookup.
missing token  a changing request without ``X-CSRF-Token``: 403 as JSON, before the route runs. The Admin console's
               forms carry the token as the field ``csrf_token`` instead: 400, the page again, nothing changed.
=============  ====================================================================================================
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence
from urllib.parse import parse_qs, urlsplit

SIGN_IN = "/login"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _fail(check: str, method: str, url: str, role: str, detail: str):
    raise AssertionError(f"{check}: {method} {url} as {role}: {detail}")


def _came_back(response) -> str:
    """A response as numbers and addresses: status, content type, where it sends the browser."""
    location = response.headers.get("Location")
    return f"{response.status_code} ({response.mimetype})" + (f" to {urlsplit(location).path}" if location else "")


def _without_token(client):
    """The signed-in session of ``client`` without the token ``conftest._ApiClient`` adds to every request."""
    return getattr(client, "_client", client)


def assert_signed_out_refused(client, url: str, *, method: str = "GET", **request: Any):
    """Nobody is signed in: an API address answers 401, a page sends the browser to sign in and back.

    ``client`` is ``anon_client``. Returns the response.
    """
    response = client.open(url, method=method, **request)
    path = urlsplit(url).path
    if path.startswith("/api/"):
        if response.status_code != 401 or response.mimetype != "application/json":
            _fail("signed out", method, url, "nobody", f"expected 401 as JSON, got {_came_back(response)}")
        return response
    target = urlsplit(response.headers.get("Location") or "")
    back = (parse_qs(target.query).get("next") or [""])[0]
    if response.status_code != 302 or target.path != SIGN_IN:
        _fail("signed out", method, url, "nobody", f"expected 302 to {SIGN_IN}, got {_came_back(response)}")
    if urlsplit(back).path != path:
        _fail("signed out", method, url, "nobody", "the sign-in page is not told where to come back to")
    return response


def assert_wrong_role_refused(client, url: str, *, role: str, method: str = "GET", status: int = 403,
                              **request: Any):
    """Someone signed in without the role the address needs is refused with ``status`` (403 today).

    ``role`` says who ``client`` is, for the message. Returns the response.
    """
    response = client.open(url, method=method, **request)
    if response.status_code != status:
        _fail("wrong role", method, url, role, f"expected {status}, got {_came_back(response)}")
    return response


def _body(response, own: Sequence[str]) -> str:
    """The body with this response's own address and id blanked: those are allowed to differ."""
    text = response.get_data(as_text=True)
    for part in own:
        if part:
            text = text.replace(part, "<own>")
    return text


def assert_wrong_owner_looks_missing(client, url: str, missing_url: str, *, role: str, method: str = "GET",
                                     ids: Optional[Sequence[str]] = None, **request: Any):
    """Somebody else's thing answers exactly like a thing that does not exist.

    ``url`` is the other person's thing, ``missing_url`` the same address with an id nobody has. Both must be
    refused (403 or 404) and be the same response: status, content type, redirect and body. Each response's own
    address is blanked before the bodies are compared; ``ids`` (theirs, the missing one) blanks more where a page
    repeats the id on its own. Returns the response for ``url``.
    """
    theirs = client.open(url, method=method, **request)
    nothing = client.open(missing_url, method=method, **request)
    if theirs.status_code not in (403, 404):
        _fail("wrong owner", method, url, role, f"expected a refusal (403 or 404), got {_came_back(theirs)}")
    their_id, missing_id = (list(ids) + ["", ""])[:2] if ids else ("", "")
    same = {
        "status": (theirs.status_code, nothing.status_code),
        "content type": (theirs.mimetype, nothing.mimetype),
        "redirect": (bool(theirs.headers.get("Location")), bool(nothing.headers.get("Location"))),
        "body": (_body(theirs, [url, urlsplit(url).path, their_id]),
                 _body(nothing, [missing_url, urlsplit(missing_url).path, missing_id])),
    }
    for part, (left, right) in same.items():
        if left != right:
            shown = f"lengths {len(left)} and {len(right)}" if part == "body" else f"{left} and {right}"
            _fail("wrong owner", method, url, role,
                  f"a person can tell it from {missing_url}, which does not exist: {part} differs ({shown})")
    return theirs


def assert_missing_token_refused(client, url: str, *, role: str, method: str = "POST", status: Optional[int] = None,
                                 unchanged: Optional[Callable[[], Any]] = None, **request: Any):
    """A changing request without its CSRF token is refused and changes nothing.

    ``client`` is signed in and allowed to make the request; it is sent without the ``X-CSRF-Token`` header,
    whatever client is handed in. ``status`` defaults to what the Platform does today: 400 for the Admin
    console's forms (leave ``csrf_token`` out of ``data``), 403 everywhere else. ``unchanged`` is a function
    without arguments that reads what the request would change (a row count, a list); it is called before and
    after, and both readings must be equal. Returns the response.
    """
    if method.upper() in SAFE_METHODS:
        _fail("missing token", method, url, role, "not a changing request, so there is nothing to refuse")
    expected = status if status is not None else (400 if urlsplit(url).path.startswith("/admin/") else 403)
    before = unchanged() if unchanged is not None else None
    response = _without_token(client).open(url, method=method, **request)
    if response.status_code != expected:
        _fail("missing token", method, url, role, f"expected {expected}, got {_came_back(response)}")
    if unchanged is not None and unchanged() != before:
        _fail("missing token", method, url, role, "the request was refused and still changed something")
    return response
