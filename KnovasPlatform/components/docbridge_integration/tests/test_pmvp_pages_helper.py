"""PMVP-03, the builder's own tests: the page checks of ``pmvp_pages`` pass on a page that refuses the way the
Platform does and fail on one that does not, and a failure names the address and the role and never page text.

A small Flask app stands in for the Platform here, with one route per behaviour (right and wrong). No database.
``test_pmvp_pages_examples.py`` runs the same checks against the real app.
"""
from __future__ import annotations

import pytest
from flask import Flask, jsonify, redirect, request, url_for

from conftest import _ApiClient
from pmvp_pages import (assert_missing_token_refused, assert_signed_out_refused, assert_wrong_owner_looks_missing,
                        assert_wrong_role_refused)

PAGE_TEXT = "GEHEIMER SEITENTEXT der Kanzlei"
TOKEN = "the-session-token"
MINE, THEIRS, NOBODYS = "akte-1", "akte-2", "akte-9"


def _app():
    app = Flask(__name__)
    app.testing = True
    app.changes = []

    def role():
        return request.headers.get("X-Test-Role")

    @app.route("/login")
    def login():
        return "Anmelden"

    # -- signed out ---------------------------------------------------------------------------------
    @app.route("/page")
    def page():
        if role() is None:
            return redirect(url_for("login", next=request.full_path))
        if role() != "admin":
            return PAGE_TEXT, 403
        return PAGE_TEXT

    @app.route("/page-open")
    def page_open():
        return PAGE_TEXT

    @app.route("/page-home")
    def page_home():
        return redirect("/")

    @app.route("/page-no-way-back")
    def page_no_way_back():
        return redirect(url_for("login"))

    @app.route("/api/thing")
    def api_thing():
        if role() is None:
            return jsonify({"success": False, "error": "Anmeldung erforderlich"}), 401
        return jsonify({"success": True, "text": PAGE_TEXT})

    @app.route("/api/thing-redirects")
    def api_thing_redirects():
        return redirect(url_for("login", next=request.full_path))

    @app.route("/api/thing-open")
    def api_thing_open():
        return jsonify({"success": True, "text": PAGE_TEXT})

    @app.route("/hidden")
    def hidden():
        return (PAGE_TEXT, 200) if role() == "admin" else ("Not found", 404)

    # -- wrong owner: akte-1 is the caller's, akte-2 somebody else's, anything else does not exist ------
    def _akte(akte, foreign, missing=("Nicht gefunden", 404)):
        if akte == MINE:
            return PAGE_TEXT
        return foreign if akte == THEIRS else missing

    @app.route("/akten/<akte>")
    def akte_same(akte):
        return _akte(akte, ("Nicht gefunden", 404))

    @app.route("/akten-403/<akte>")
    def akte_status(akte):
        return _akte(akte, ("Nicht gefunden", 403))

    @app.route("/akten-text/<akte>")
    def akte_text(akte):
        return _akte(akte, (f"{PAGE_TEXT}: gesperrt", 404))

    @app.route("/akten-json/<akte>")
    def akte_type(akte):
        return _akte(akte, (jsonify({"error": "Nicht gefunden"}), 404))

    @app.route("/akten-served/<akte>")
    def akte_served(akte):
        return _akte(akte, PAGE_TEXT)

    @app.route("/akten-echo/<akte>")
    def akte_echo(akte):
        said = (f"Keine Akte {akte} unter {request.path}", 404)
        return _akte(akte, said, said)

    # -- missing token ------------------------------------------------------------------------------
    def _token_ok():
        return request.headers.get("X-CSRF-Token") == TOKEN

    @app.route("/change", methods=["POST", "DELETE"])
    def change():
        if not _token_ok():
            return jsonify({"success": False, "error": "CSRF token invalid or missing"}), 403
        app.changes.append(request.method)
        return jsonify({"success": True})

    @app.route("/change-open", methods=["POST"])
    def change_open():
        app.changes.append("POST")
        return jsonify({"success": True, "text": PAGE_TEXT})

    @app.route("/change-then-refuse", methods=["POST"])
    def change_then_refuse():
        app.changes.append("POST")
        return jsonify({"success": False}), 403

    @app.route("/admin/people/create", methods=["POST"])
    def admin_form():
        if request.form.get("csrf_token") != TOKEN:
            return f"{PAGE_TEXT}: Formular ist abgelaufen", 400
        app.changes.append("POST")
        return PAGE_TEXT

    # Routes that check no token at all, and still answer the status a token refusal has.
    @app.route("/change-admins-only", methods=["POST"])
    def change_admins_only():
        if role() != "admin":
            return jsonify({"success": False, "error": "Forbidden"}), 403
        app.changes.append("POST")
        return jsonify({"success": True})

    @app.route("/admin/form-without-token-check", methods=["POST"])
    def admin_form_without_token_check():
        if not request.form.get("email"):
            return f"{PAGE_TEXT}: E-Mail fehlt", 400
        app.changes.append("POST")
        return PAGE_TEXT

    return app


@pytest.fixture
def app():
    return _app()


@pytest.fixture
def nobody(app):
    return app.test_client()


def _as(app, role):
    client = app.test_client()
    client.environ_base["HTTP_X_TEST_ROLE"] = role
    return client


@pytest.fixture
def member(app):
    """Signed in the way conftest's clients are: a wrapper that adds the token to every request."""
    return _ApiClient(_as(app, "member"), TOKEN)


@pytest.fixture
def admin(app):
    return _ApiClient(_as(app, "admin"), TOKEN)


def _failure(check, *args, **kwargs):
    with pytest.raises(AssertionError) as failed:
        check(*args, **kwargs)
    message = str(failed.value)
    assert "GEHEIM" not in message and "Kanzlei" not in message and "Formular" not in message
    return message


# --- signed out ---------------------------------------------------------------------------------------


def test_signed_out_a_page_sends_the_browser_to_sign_in_and_back(nobody):
    response = assert_signed_out_refused(nobody, "/page?ansicht=liste")
    assert response.status_code == 302


def test_signed_out_an_api_address_answers_401(nobody):
    assert assert_signed_out_refused(nobody, "/api/thing").status_code == 401


@pytest.mark.parametrize("url,what", [
    ("/page-open", "expected 302 to /login, got 200 (text/html)"),
    ("/page-home", "expected 302 to /login, got 302 (text/html) to /"),
    ("/page-no-way-back", "the sign-in page is not told where to come back to"),
    ("/api/thing-open", "expected 401 as JSON, got 200 (application/json)"),
    ("/api/thing-redirects", "expected 401 as JSON, got 302 (text/html) to /login"),
])
def test_signed_out_a_page_that_does_not_refuse_fails_with_its_address(nobody, url, what):
    message = _failure(assert_signed_out_refused, nobody, url)
    assert message == f"signed out: GET {url} as nobody: {what}"


# --- wrong role ---------------------------------------------------------------------------------------


def test_wrong_role_is_403(member):
    assert assert_wrong_role_refused(member, "/page", role="member").status_code == 403


def test_wrong_role_a_module_that_hides_itself_is_404(member):
    assert_wrong_role_refused(member, "/hidden", role="member", status=404)


def test_wrong_role_a_page_that_serves_everyone_fails_with_address_and_role(member, admin):
    message = _failure(assert_wrong_role_refused, member, "/page-open", role="member")
    assert message == "wrong role: GET /page-open as member: expected 403, got 200 (text/html)"
    assert "as admin" in _failure(assert_wrong_role_refused, admin, "/page", role="admin")
    assert "expected 403, got 404" in _failure(assert_wrong_role_refused, member, "/hidden", role="member")


# --- wrong owner --------------------------------------------------------------------------------------


def test_wrong_owner_looks_like_a_thing_that_does_not_exist(member):
    response = assert_wrong_owner_looks_missing(member, f"/akten/{THEIRS}", f"/akten/{NOBODYS}", role="member")
    assert response.status_code == 404


def test_wrong_owner_the_address_itself_may_differ_and_the_id_when_named(member):
    urls = (f"/akten-echo/{THEIRS}", f"/akten-echo/{NOBODYS}")
    assert_wrong_owner_looks_missing(member, *urls, role="member", ids=(THEIRS, NOBODYS))
    message = _failure(assert_wrong_owner_looks_missing, member, *urls, role="member")
    assert "body differs" in message


@pytest.mark.parametrize("route,what", [
    ("akten-403", "status differs (403 and 404)"),
    ("akten-text", "body differs (lengths 41 and 14)"),
    ("akten-json", "content type differs (application/json and text/html)"),
])
def test_wrong_owner_a_difference_a_person_could_tell_fails(member, route, what):
    message = _failure(assert_wrong_owner_looks_missing, member, f"/{route}/{THEIRS}", f"/{route}/{NOBODYS}",
                       role="member")
    assert message == (f"wrong owner: GET /{route}/{THEIRS} as member: a person can tell it from "
                       f"/{route}/{NOBODYS}, which does not exist: {what}")


def test_wrong_owner_a_thing_that_is_served_fails(member):
    message = _failure(assert_wrong_owner_looks_missing, member, f"/akten-served/{THEIRS}",
                       f"/akten-served/{NOBODYS}", role="member")
    assert message.endswith("expected a refusal (403 or 404), got 200 (text/html)")


# --- missing token ------------------------------------------------------------------------------------


def test_missing_token_the_request_is_sent_without_the_token_and_then_with_it(app, admin):
    seen = []

    def unchanged():
        seen.append(list(app.changes))
        return list(app.changes)

    response = assert_missing_token_refused(admin, "/change", role="admin", unchanged=unchanged)
    assert response.status_code == 403
    assert seen == [[], []]                                  # read before and after the refused request ...
    assert app.changes == ["POST"]                           # ... and the control, with the token, was taken
    assert_missing_token_refused(admin, "/change", role="admin", method="DELETE")
    assert app.changes == ["POST", "DELETE"]
    assert admin.post("/change").status_code == 200          # the client keeps its token afterwards


def test_missing_token_the_admin_consoles_forms_answer_400(app, admin):
    """The form's token is the field ``csrf_token``; the control sends it, so the person is made once."""
    assert_missing_token_refused(admin, "/admin/people/create", role="admin", data={"email": "neu@kanzlei.ch"},
                                 unchanged=lambda: len(app.changes))
    assert app.changes == ["POST"]


def test_missing_token_a_client_without_a_token_of_its_own_takes_one(app):
    plain = _as(app, "admin")
    assert "the control needs the session's token" in _failure(assert_missing_token_refused, plain, "/change",
                                                                role="admin")
    assert app.changes == []
    assert_missing_token_refused(plain, "/change", role="admin", token=TOKEN)
    assert app.changes == ["POST"]


def test_missing_token_a_refusal_for_the_wrong_role_does_not_pass_as_a_token_check(app, member):
    """The verifier's finding: a route that checks the role and no token answered 403, as expected, and passed."""
    message = _failure(assert_missing_token_refused, member, "/change-admins-only", role="member")
    assert message == ("missing token: POST /change-admins-only as member: with its token the request comes back "
                       "with 403 as well, so the refusal is not about the token")
    assert app.changes == []


def test_missing_token_a_form_refused_for_a_missing_field_does_not_pass_as_a_token_check(app, admin):
    message = _failure(assert_missing_token_refused, admin, "/admin/form-without-token-check", role="admin", data={})
    assert "comes back with 400 as well" in message
    # With the form filled in, the route is seen for what it is: it takes the request without a token.
    message = _failure(assert_missing_token_refused, admin, "/admin/form-without-token-check", role="admin",
                       data={"email": "neu@kanzlei.ch"})
    assert message.endswith("expected 400, got 200 (text/html)")


def test_missing_token_an_address_that_does_not_exist_does_not_pass(app, admin):
    message = _failure(assert_missing_token_refused, admin, "/api/gibt-es-nicht", role="admin", status=404)
    assert "comes back with 404 as well" in message
    assert "expected 403, got 404" in _failure(assert_missing_token_refused, admin, "/api/gibt-es-nicht", role="admin")


def test_missing_token_a_route_that_takes_the_request_fails(app, admin):
    message = _failure(assert_missing_token_refused, admin, "/change-open", role="admin")
    assert message == "missing token: POST /change-open as admin: expected 403, got 200 (application/json)"


def test_missing_token_a_refusal_that_still_changed_something_fails(app, admin):
    message = _failure(assert_missing_token_refused, admin, "/change-then-refuse", role="admin",
                       unchanged=lambda: len(app.changes))
    assert message == ("missing token: POST /change-then-refuse as admin: the request was refused and still "
                       "changed something")


def test_missing_token_is_not_asked_of_a_request_that_changes_nothing(admin):
    assert "not a changing request" in _failure(assert_missing_token_refused, admin, "/page", role="admin",
                                                method="GET")
