"""«Freigaben»: write proposals accepted or rejected in the Builder (connection layer MVP-27, KC-7b; decision CL-4).

The rows come from the tenant's GET /secured/source-writes?status=proposed, read through the Platform's tenant
client (a fake here); «Annehmen» and «Ablehnen» post to /secured/source-writes/<id>/accept|reject with the person as
the declared actor ({"kind": "user", "id": <user id>}). Accepting or rejecting needs the permission ``publish``,
checked on the server. The row follows the outcome, in German; the navigation counts the open proposals.
"""
import datetime as dt

import pytest

from builder import connection_api, editing, package
from builder_harness import (SAMPLE_PACKAGE, FakeEngine, MemorySecrets, MemoryWorkspaceStore, StandInUser, make_app)

TENANT = "t-approvals"
NOW = dt.datetime.now(dt.timezone.utc)


class FakeTenant:
    """The tenant's source-write routes as BrainConnection reaches them, in memory."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.refuse = None

    def source_writes(self, status=None):
        self.calls.append(("list", status))
        return [dict(r) for r in self.rows if status is None or r["status"] == status]

    def _decide(self, proposal_id, to):
        if self.refuse:
            raise connection_api.ConnectionApiError(*self.refuse)
        row = next((r for r in self.rows if r["id"] == proposal_id), None)
        if row is None:
            raise connection_api.ConnectionApiError(404, "not_found")
        row["status"] = to
        return dict(row)

    def accept_source_write(self, proposal_id, actor):
        self.calls.append(("accept", proposal_id, actor))
        return self._decide(proposal_id, "queued")

    def reject_source_write(self, proposal_id, actor, reason):
        self.calls.append(("reject", proposal_id, actor, reason))
        return self._decide(proposal_id, "rejected")

    def job_stats(self, since):
        return {}


def _proposal(pid, status="proposed", hours=1, **extra):
    return {"id": pid, "node_id": f"n-{pid}", "node_name": "Merkblatt Fristen", "mapping": "merkblaetter",
            "source": "akten", "external_id": "merkblaetter/fristen.md", "expected_version": "abc123",
            "facts": {"satz": "Fristen laufen ab Zustellung."}, "current": {"satz": "Fristen laufen."},
            "reason": "Nach Rückfrage präzisiert", "status": status,
            "actor": {"kind": "agent", "id": "agent-recherche", "on_behalf_of": {"kind": "user", "id": "u-17"}},
            "created_at": (NOW - dt.timedelta(hours=hours)).isoformat(), **extra}


@pytest.fixture
def world():
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    tenant = FakeTenant([_proposal("p1"), _proposal("p2", hours=60),
                         _proposal("p3", status="applied", result_ref="https://github.example/muster-partner/akten/pull/7"),
                         _proposal("p4", status="conflict", code="conflict")])
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=MemorySecrets(),
                   tenant_client=tenant)
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    model["ontology"]["types"].append({"key": "Merkblatt", "level": "company", "attributes": [
        {"name": "satz", "datatype": "text", "level": "company"}]})
    for op in ({"op": "source.create", "kind": "github", "name": "Akten", "component": "c1",
                "settings": {"repository": "muster-partner/akten"}},
               {"op": "mapping.create", "id": "merkblaetter", "source": "akten",
                "collection": "markdown_front_matter:merkblaetter/*.md", "type": "Merkblatt"}):
        editing.apply(model, op)
    entry = next(m for m in model["mappings"] if m["definition"]["id"] == "merkblaetter")
    entry["definition"]["writes"] = {"Merkblatt": {"satz": {"field": "front_matter.summary"}}}
    store.create(name="Muster & Partner", model=model, actor_id=admin.id, source={"kind": "blank"}, tenant=TENANT)
    return {"http": app.test_client(), "tenant": tenant, "gate": app.extensions["builder_gate"], "admin": admin}


def test_the_open_proposals_with_what_they_change(world):
    page = world["http"].get("/builder/betrieb/freigaben").get_data(as_text=True)
    assert ("list", "proposed") in world["tenant"].calls
    row = page.split('data-proposal="p1"', 1)[1].split("</tr>", 1)[0]
    for text in ("Merkblatt Fristen", "satz", "Fristen laufen.", "Fristen laufen ab Zustellung.", "akten",
                 "merkblaetter/fristen.md", "agent-recherche", "u-17", "angegeben", "Nach Rückfrage präzisiert",
                 "Annehmen", "Ablehnen", "Offen", 'data-allowed="true"'):
        assert text in row, text
    # The rows that were decided follow their outcome.
    assert "Pull Request" in page and "https://github.example/muster-partner/akten/pull/7" in page
    assert "Konflikt" in page


def test_accepting_declares_the_person_and_shows_the_outcome(world):
    answer = world["http"].post("/builder/api/freigaben/p1/accept")
    assert answer.status_code == 200, answer.get_json()
    assert world["tenant"].calls[-1] == ("accept", "p1", {"kind": "user", "id": str(world["admin"].id)})
    body = answer.get_json()
    assert body["status"] == "queued" and "Knovas Link" in body["status_text"]


def test_rejecting_needs_a_reason(world):
    assert world["http"].post("/builder/api/freigaben/p1/reject", json={}).status_code == 400
    answer = world["http"].post("/builder/api/freigaben/p1/reject", json={"reason": "Belegt ist etwas anderes."})
    assert answer.status_code == 200 and answer.get_json()["status_text"] == "Abgelehnt"
    assert world["tenant"].calls[-1] == ("reject", "p1", {"kind": "user", "id": str(world["admin"].id)},
                                         "Belegt ist etwas anderes.")


@pytest.mark.parametrize("roles", [{"builder_author"}, {"builder_reviewer"}, {"builder_viewer"}])
def test_deciding_needs_publish(world, roles):
    world["gate"].user = StandInUser(roles)
    assert world["http"].post("/builder/api/freigaben/p1/accept").status_code == 403
    assert world["http"].post("/builder/api/freigaben/p1/reject", json={"reason": "nein"}).status_code == 403
    assert not any(c[0] in ("accept", "reject") for c in world["tenant"].calls)


def test_a_publisher_decides(world):
    world["gate"].user = StandInUser({"builder_publisher"})
    assert world["http"].post("/builder/api/freigaben/p1/accept").status_code == 200


def test_a_refusal_of_the_brain_is_said_in_german(world):
    world["tenant"].refuse = (409, "previously_rejected")
    answer = world["http"].post("/builder/api/freigaben/p1/accept")
    assert answer.status_code == 409 and "Schon abgelehnt" in answer.get_json()["error"]


def test_the_badge_counts_open_proposals(world):
    assert world["http"].get("/builder/api/freigaben/count").get_json() == {"open": 2}
    page = world["http"].get("/builder/verbinden").get_data(as_text=True)
    assert "data-approvals-badge" in page and "/builder/api/freigaben/count" in page


def test_a_consultant_with_a_grant_sees_no_proposals(world):
    world["gate"].user = StandInUser(frozenset(), "Bea Berater", "bea@extern.example")
    assert world["http"].get("/builder/betrieb/freigaben").status_code == 403
    assert world["http"].get("/builder/api/freigaben/count").status_code == 403


def test_without_a_tenant_client_the_page_says_so(world):
    admin = StandInUser({"admin"})
    app = make_app(admin, MemoryWorkspaceStore(), tenant=TENANT, engine=FakeEngine())
    page = app.test_client().get("/builder/betrieb/freigaben").get_data(as_text=True)
    assert "liest keine Änderungsvorschläge" in page


def test_the_brain_connection_calls_the_routes():
    class Answer:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def json(self):
            return self._body

    class Client:
        def __init__(self):
            self.calls = []

        def _request_quiet(self, method, endpoint, data=None, timeout=None):
            self.calls.append((method, endpoint, data))
            if endpoint.endswith("/reject"):
                return Answer(409, {"error": "previously_rejected"})
            return Answer(200, {"proposals": [{"id": "p1"}]} if method == "GET" else {"status": "queued"})

    client = Client()
    brain = connection_api.BrainConnection(client)
    assert brain.source_writes("proposed") == [{"id": "p1"}]
    assert brain.accept_source_write("p 1", {"kind": "user", "id": "u"}) == {"status": "queued"}
    with pytest.raises(connection_api.ConnectionApiError) as refused:
        brain.reject_source_write("p1", {"kind": "user", "id": "u"}, "nein")
    assert refused.value.code == "previously_rejected" and refused.value.status == 409
    brain.job_stats("2026-11-04T00:00:00+00:00")
    bare = connection_api.BrainConnection(type("C", (), {"_request_quiet": lambda self, *a, **k: Answer(200, [{"id": "p1"}])})())
    with pytest.raises(connection_api.ConnectionApiError):
        bare.source_writes("proposed")          # the route answers {"proposals": [...]}, nothing else
    assert client.calls == [
        ("GET", "/secured/source-writes?status=proposed", None),
        ("POST", "/secured/source-writes/p%201/accept", {"actor": {"kind": "user", "id": "u"}}),
        ("POST", "/secured/source-writes/p1/reject", {"actor": {"kind": "user", "id": "u"}, "reason": "nein"}),
        ("GET", "/secured/connector/jobs/stats?since=2026-11-04T00%3A00%3A00%2B00%3A00", None)]
