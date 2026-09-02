"""Helpers for driving the administration console through the real login."""

from __future__ import annotations

PASSWORD = "korrektes-pferd-batterie"


def csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


def sign_in(client, email: str, password: str = PASSWORD):
    """Sign in through the real /login route, so the session is built the way
    production builds it. Returns the client for chaining."""
    page = client.get("/login")
    client.post(
        "/login",
        data={"login_name": email, "password": password,
              "csrf_token": csrf_from(page.data.decode("utf-8"))},
    )
    return client


def post_form(client, path: str, page: str = "/admin/people", **fields):
    """POST ``fields`` to ``path`` with a CSRF token read from ``page``."""
    token = csrf_from(client.get(page).data.decode("utf-8"))
    fields["csrf_token"] = token
    return client.post(path, data=fields, follow_redirects=False)
