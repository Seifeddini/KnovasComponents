"""Authorisation on /api/graph/*, asserted on the route rather than the link.

Hiding a control is presentation; refusing the request is the control. Every
test here calls the endpoint directly for that reason.

Two halves, deliberately:

* TestTheGates* build a minimal Flask app with dummy routes and exercise the
  four decorators today. They are ordinary tests and they fail when a gate
  breaks — the module's own docstring promised this shape and it is the only
  coverage the gates have until D1-D3 land.
* The xfail classes below describe the real endpoints. They are the acceptance
  criteria for D1-D3 and they are expected to fail until then.

Alloy: models/alloy/node_grants.als (WriteGateMechanism, ReadGateMechanism).
"""
import uuid

import pytest
from flask import Flask, jsonify

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable
from web_interface.graph_routes import create_graph_blueprint

# C2 builds the blueprint and its four gates; the routes they guard arrive in
# D1-D3, so until then every call below lands on 404. The mark comes off class
# by class as each task adds its routes — it is not strict, because a class may
# start passing one route before the next.
_AWAITING_ROUTES = pytest.mark.xfail(reason="routes arrive in D1-D3", strict=False)


# ---------------------------------------------------------------------------
# The gates themselves, on a bare Flask app. No database, no identity schema:
# the decorators take their collaborators as arguments precisely so they can be
# constrained without one, and a suite that only ever xfails cannot tell a
# broken gate from an absent route.
# ---------------------------------------------------------------------------


class _User:
    def __init__(self, roles=frozenset(), user_id=None):
        self.id = user_id or uuid.uuid4()
        self.roles = frozenset(roles)


class _Gate:
    def __init__(self, user=None):
        self.user = user

    def current_user(self):
        return self.user


class _Grants:
    """The store as the guard uses it, recording what it was asked."""

    def __init__(self, answer=True):
        self.answer = answer
        self.asked = []

    def may_write(self, node_id, user):
        self.asked.append((node_id, user))
        return self.answer


def _app_with_gates(*, user=None, may_write=True, graph_mode=True):
    """A Flask app whose routes do nothing but pass or not pass the gates."""
    grants = _Grants(may_write)
    bp = create_graph_blueprint(
        _Gate(user), lambda: grants, lambda: None, graph_mode=lambda: graph_mode)

    @bp.route("/open")
    @bp.require_user
    def _open():
        return jsonify({"success": True})

    @bp.route("/admin-only", methods=["POST"])
    @bp.require_admin
    def _admin_only():
        return jsonify({"success": True}), 201

    @bp.route("/nodes/<node_id>", methods=["PATCH"])
    @bp.require_node_write
    def _write(node_id):
        return jsonify({"success": True, "node_id": node_id})

    @bp.route("/akten/<akte>", methods=["PATCH"])
    @bp.require_node_write(param="akte")
    def _write_named(akte):
        return jsonify({"success": True, "node_id": akte})

    @bp.route("/misconfigured", methods=["PATCH"])
    @bp.require_node_write
    def _misconfigured():
        return jsonify({"success": True})

    @bp.route("/graph-only")
    @bp.require_graph_mode
    @bp.require_user
    def _graph_only():
        return jsonify({"success": True})

    app = Flask(__name__)
    # So a programming error propagates out of the test client instead of being
    # rendered as a 500 the assertion cannot tell from a handled refusal.
    app.testing = True
    app.register_blueprint(bp)
    return app.test_client(), grants


class TestTheUserGate:
    def test_an_anonymous_caller_is_refused(self):
        client, _ = _app_with_gates(user=None)
        response = client.get("/api/graph/open")
        assert response.status_code == 401
        assert response.get_json()["error"] == "Nicht angemeldet."

    def test_a_signed_in_caller_passes(self):
        client, _ = _app_with_gates(user=_User({"member"}))
        assert client.get("/api/graph/open").status_code == 200


class TestTheAdminGate:
    def test_an_anonymous_caller_is_401_not_403(self):
        """Not signed in is a different answer from not permitted, and an
        operator reading a log needs to see which one happened."""
        client, _ = _app_with_gates(user=None)
        assert client.post("/api/graph/admin-only").status_code == 401

    def test_a_member_is_refused(self):
        client, _ = _app_with_gates(user=_User({"member"}))
        response = client.post("/api/graph/admin-only")
        assert response.status_code == 403
        assert response.get_json()["error"] == "Nur für Administratoren."

    def test_an_admin_passes(self):
        client, _ = _app_with_gates(user=_User({"admin"}))
        assert client.post("/api/graph/admin-only").status_code == 201


class TestTheNodeWriteGate:
    def test_an_anonymous_caller_is_refused_before_the_store_is_asked(self):
        client, grants = _app_with_gates(user=None)
        assert client.patch(f"/api/graph/nodes/{uuid.uuid4()}").status_code == 401
        assert grants.asked == []

    def test_the_store_decides_and_a_no_is_a_403(self):
        client, grants = _app_with_gates(user=_User({"member"}), may_write=False)
        response = client.patch(f"/api/graph/nodes/{uuid.uuid4()}")
        assert response.status_code == 403
        assert response.get_json()["error"] == \
            "Keine Bearbeitungsrechte für diesen Knoten."
        assert len(grants.asked) == 1

    def test_a_yes_reaches_the_view(self):
        node = str(uuid.uuid4())
        client, grants = _app_with_gates(user=_User({"member"}), may_write=True)
        response = client.patch(f"/api/graph/nodes/{node}")
        assert response.status_code == 200
        assert response.get_json()["node_id"] == node

    def test_the_id_the_store_is_asked_about_is_the_one_in_the_url(self):
        """The guard must not invent, normalise or drop it — a gate that asks
        about a different node than the one being written is not a gate."""
        node = str(uuid.uuid4())
        client, grants = _app_with_gates(user=_User({"member"}))
        client.patch(f"/api/graph/nodes/{node}")
        assert grants.asked[0][0] == node

    def test_a_malformed_id_is_the_store_s_refusal_not_an_exception(self):
        """NodeGrantStore.may_write returns False for an id that cannot name a
        node; the guard must turn that into 403 rather than let it through."""
        client, grants = _app_with_gates(user=_User({"member"}), may_write=False)
        assert client.patch("/api/graph/nodes/n1").status_code == 403
        assert grants.asked[0][0] == "n1"

    def test_the_path_parameter_can_be_named_something_else(self):
        client, grants = _app_with_gates(user=_User({"member"}), may_write=False)
        assert client.patch("/api/graph/akten/abc").status_code == 403
        assert grants.asked[0][0] == "abc"

    def test_a_route_without_the_parameter_fails_loudly(self):
        """Reading it with .get would hand may_write None: every request 403s
        and the screen blames the user's permissions for a wiring mistake."""
        client, grants = _app_with_gates(user=_User({"member"}))
        with pytest.raises(RuntimeError, match="no <node_id> parameter"):
            client.patch("/api/graph/misconfigured")
        assert grants.asked == []


class TestTheFixtureModeGate:
    def test_fixture_mode_refuses_with_409(self):
        client, _ = _app_with_gates(user=_User({"member"}), graph_mode=False)
        response = client.get("/api/graph/graph-only")
        assert response.status_code == 409
        assert response.get_json()["error"] == "Wissensnetz-Modus erforderlich"

    def test_graph_mode_lets_the_request_through(self):
        client, _ = _app_with_gates(user=_User({"member"}), graph_mode=True)
        assert client.get("/api/graph/graph-only").status_code == 200

    def test_it_refuses_before_asking_who_is_calling(self):
        """Nothing is broken and nothing is secret: the deployment is in
        fixture mode, and that answer does not depend on the caller."""
        client, _ = _app_with_gates(user=None, graph_mode=False)
        assert client.get("/api/graph/graph-only").status_code == 409


# ---------------------------------------------------------------------------
# The real endpoints. Acceptance criteria for D1-D3; expected to fail today.
# ---------------------------------------------------------------------------

_DB = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")



@_DB
@_AWAITING_ROUTES
class TestAuthentication:
    def test_an_anonymous_caller_gets_401(self, anon_client):
        """XPASSes today, and not because of this blueprint: the app-wide
        identity gate answers 401 for /api/* before routing, so this holds with
        the route still absent. require_user itself is constrained by
        TestTheUserGate above; this one only becomes evidence once D1 lands the
        endpoint."""
        assert anon_client.get("/api/graph/node-types").status_code == 401

    def test_a_member_may_read_the_type_list(self, member_client):
        assert member_client.get("/api/graph/node-types").status_code == 200


@_DB
@_AWAITING_ROUTES
class TestAdminGate:
    def test_a_member_may_not_create_a_node_type(self, member_client):
        response = member_client.post("/api/graph/node-types", json={"name": "Mandat"})
        assert response.status_code == 403

    def test_an_admin_may_create_a_node_type(self, admin_client):
        response = admin_client.post("/api/graph/node-types", json={"name": "Mandat"})
        assert response.status_code == 201


@_DB
@_AWAITING_ROUTES
class TestNodeWriteGate:
    def test_a_non_editor_may_not_patch_a_node(self, member_client, node_owned_by_alice):
        response = member_client.patch(f"/api/graph/nodes/{node_owned_by_alice}",
                                       json={"name": "Neu"})
        assert response.status_code == 403

    def test_the_owner_may_patch_their_node(self, alice_client, node_owned_by_alice):
        response = alice_client.patch(f"/api/graph/nodes/{node_owned_by_alice}",
                                      json={"name": "Neu"})
        assert response.status_code == 200

    def test_a_non_editor_may_still_read_it(self, member_client, node_owned_by_alice):
        """Read is the backend ACL's decision, never node_grants'."""
        assert member_client.get(
            f"/api/graph/nodes/{node_owned_by_alice}").status_code == 200


@_DB
@_AWAITING_ROUTES
class TestCsrf:
    def test_a_state_changing_request_without_the_header_is_refused(
            self, admin_client_no_csrf):
        response = admin_client_no_csrf.post("/api/graph/node-types",
                                             json={"name": "Mandat"})
        assert response.status_code == 403


@_DB
@_AWAITING_ROUTES
class TestFixtureMode:
    def test_every_graph_route_refuses_in_fixture_mode(self, fixture_mode_client):
        response = fixture_mode_client.get("/api/graph/node-types")
        assert response.status_code == 409
        assert response.get_json()["error"] == "Wissensnetz-Modus erforderlich"
