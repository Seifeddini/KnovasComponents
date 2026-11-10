"""«Freigaben» for change sets: write back v2 in the Builder (FEAT-2026-11-10-write-back C-2; BET-2026-11-write-back
WB-34 part, WB-03 part; SPEC-write-back -04, -06, -36; GI-CONN-10).

The change sets come from the tenant's GET /secured/change-sets, read through the Platform's tenant client (a fake
here, in the Brain's shape: Change Sets API). Each action carries the preview the connector at the site computed
without writing: per field the value before and after, the version it read, whether it is exact. «Freigeben» approves
with the published staging of the sources the actions write (``direct`` unless the source says otherwise, plan C-Q1,
decided A); «Ablehnen» rejects with a reason. **An inexact or missing preview is never offered for approval and the
route refuses it before calling the Brain** (Alloy ``write_back_actions.als::approvalNeedsExactPreview``). Everything
is behind BUILDER_PROTOCOL_V2 (off by default). The company, the repository and the people are made up.
"""
import copy
import datetime as dt
import io
import json
import zipfile

import pytest

from builder import approvals, connection_api, package
from builder import protocol_v2 as V2
from builder_harness import (SAMPLE_PACKAGE, FakeEngine, MemorySecrets, MemoryWorkspaceStore, ProtocolClient, StandInUser,
                             make_app)

TENANT = "t-change-sets"
NOW = dt.datetime.now(dt.timezone.utc)
WRITE = ["preview_equals_receipt", "exactly_once", "permit_refusals", "no_credential_leaves_site"]


def _preview(exact=True, read_version="b10b5a", stagings=("direct", "native_review"), code=None, changes=None):
    return {"id": "pv-1", "exact": exact, "inexact_code": code, "read_version": read_version, "source_kind": "github",
            "operation": "set_front_matter", "can_group": False,
            "stagings": list(stagings) if stagings is not None else None,
            "changes": changes if changes is not None else [{"field": "status", "before": "offen", "after": "erledigt"}],
            "before_params": None, "computed": [], "recorded_at": NOW.isoformat()}


def _action(aid="a1", preview="default", source="akten", **extra):
    return {"id": aid, "ordinal": 0,
            "action_type": {"id": "t1", "key": "set_status", "version": 1, "inverse": False, "risk_class": "low"},
            "node_id": "5f0c0000-0000-4000-8000-000000000001", "params": {"status": "erledigt", "mapping": "akten-fristen"},
            "source": source, "mapping": "akten-fristen", "external_id": "fristen/zustellung.md", "node_version": "b10b5a",
            "expected_version": None, "outcome": None,
            "preview": _preview() if preview == "default" else preview, "receipt": None, **extra}


def _change_set(cid, state="proposed", actions=None, hours=1, **extra):
    acts = actions if actions is not None else [_action()]
    body = {"id": cid, "state": state, "state_code": None,
            "proposer": {"kind": "agent", "id": "agent-recherche", "on_behalf_of": {"kind": "user", "id": "u-17"}},
            "reason": "Die Frist ist abgelaufen", "evidence": [], "evidence_missing": True, "actions": acts,
            "approvable": state == "proposed", "not_approvable": [], "irreversible": True, "writes_individually": True,
            "held": [], "staging": None, "created_at": (NOW - dt.timedelta(hours=hours)).isoformat()}
    body.update(extra)
    return body


class FakeTenant:
    """The tenant's source-write and change-set routes as BrainConnection reaches them, in memory."""

    def __init__(self, sets):
        self.sets = sets
        self.calls = []
        self.refuse = None

    def source_writes(self, status=None):
        return []

    def job_stats(self, since):
        return {}

    def change_sets(self, state=None):
        self.calls.append(("list", state))
        return [copy.deepcopy(c) for c in self.sets if state is None or c["state"] == state]

    def change_set(self, change_set_id):
        self.calls.append(("get", change_set_id))
        found = next((c for c in self.sets if c["id"] == change_set_id), None)
        if found is None:
            raise connection_api.ConnectionApiError(404, "not_found")
        return copy.deepcopy(found)

    def _move(self, change_set_id, to, **extra):
        if self.refuse:
            raise connection_api.ConnectionApiError(*self.refuse)
        found = next(c for c in self.sets if c["id"] == change_set_id)
        found.update(state=to, **extra)
        return copy.deepcopy(found)

    def approve_change_set(self, change_set_id, actor, staging):
        self.calls.append(("approve", change_set_id, actor, staging))
        return self._move(change_set_id, "approved", staging=staging)

    def reject_change_set(self, change_set_id, actor, note):
        self.calls.append(("reject", change_set_id, actor, note))
        return self._move(change_set_id, "refused", state_code="rejected")


def _declaration(stagings=("direct", "native_review")):
    return {"protocol": "2.0", "source_kind": "github", "streams": [], "fetch": True,
            "actions": [{"name": "set_front_matter", "target_kind": "file",
                         "params": [{"name": "mapping", "kind": "string", "required": True},
                                    {"name": "status", "kind": "string", "required": False}],
                         "preview": "exact", "staging": list(stagings), "grouping": "none", "inverse": None}],
            "conformance": {"suite": "2.0.0", "passed": WRITE + ["review_outcome"], "report_sha256": "0" * 64}}


def _writer(http, store, decl):
    """A site whose sync component reports ``decl`` through Link's heartbeat (as in test_builder_protocol_v2)."""
    made = http.post("/builder/api/sites", json={"name": "Kanzlei Basel", "options": ["github"]}).get_json()
    env = zipfile.ZipFile(io.BytesIO(http.post(f"/builder/api/sites/{made['id']}/package", json={}).data)) \
        .read("knovas-link.env").decode()
    link = ProtocolClient(http)
    link.enrol(next(line.split("=", 1)[1] for line in env.splitlines() if line.startswith("LINK_ENROL_TOKEN=")))
    component = {"name": "sync-component", "kind": "sync_component", "version": "0.4.0", "state": "online",
                 "capabilities": ["dry_run", "apply"], "declaration": decl}
    link.heartbeat(site={"certificate": "present", "work_channel": "connected",
                         "probe": {"last_at": NOW.isoformat(), "ok": True}, "components": [component]})
    return store.children(link.component)[0]


def _world(monkeypatch, sets, on=True, staging=None, decl=None):
    if on:
        monkeypatch.setenv("BUILDER_PROTOCOL_V2", "true")
    else:
        monkeypatch.delenv("BUILDER_PROTOCOL_V2", raising=False)
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    tenant = FakeTenant(sets)
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=MemorySecrets(),
                   secrets=MemorySecrets(), tenant_client=tenant)
    http = app.test_client()
    writer = _writer(http, store, decl if decl is not None else _declaration())
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    source = {"key": "akten", "name": "Akten", "kind": "github", "component": writer["id"], "sync_mode": "manual",
              "settings": {"repository": "muster-partner/akten", "ref": "main"}, "credential_set": False}
    if staging:
        source["staging"] = staging
    model["sources"] = [source]
    workspace = store.create(name="Muster & Partner", model=model, actor_id=admin.id, source={"kind": "blank"},
                             tenant=TENANT)
    store.publish(workspace, admin.id, store.draft(workspace)["content_hash"], {"sections": {}}, "h1")
    http.get(f"/builder/w/{workspace}")
    return {"http": http, "tenant": tenant, "gate": app.extensions["builder_gate"], "admin": admin, "store": store,
            "workspace": workspace}


def _row(page, cid):
    return page.split(f'data-change-set="{cid}"', 1)[1].split("</tr>", 1)[0]


def _approve_calls(world):
    return [c for c in world["tenant"].calls if c[0] == "approve"]


# ------------------------------------------------------------------ the switch


def test_with_the_switch_off_nothing_of_change_sets_is_read_or_offered(monkeypatch):
    world = _world(monkeypatch, [_change_set("c1")], on=False)
    page = world["http"].get("/builder/betrieb/freigaben").get_data(as_text=True)
    assert "data-change-sets" not in page and "Änderungssätze" not in page
    assert world["http"].post("/builder/api/freigaben/change-sets/c1/approve").status_code == 404
    assert world["http"].post("/builder/api/freigaben/change-sets/c1/reject", json={"reason": "nein"}).status_code == 404
    assert world["http"].get("/builder/api/freigaben/count").get_json() == {"open": 0}
    assert world["tenant"].calls == []


# ------------------------------------------------------------------ the page


def test_an_open_change_set_shows_each_action_with_its_preview(monkeypatch):
    world = _world(monkeypatch, [_change_set("c1"), _change_set("c2", state="applied", hours=30)])
    page = world["http"].get("/builder/betrieb/freigaben").get_data(as_text=True)
    assert ("list", "proposed") in world["tenant"].calls
    row = _row(page, "c1")
    for text in ("set_status", "akten", "fristen/zustellung.md", "status", "offen", "erledigt", "b10b5a",
                 'data-exact="true"', "genau", "agent-recherche", "u-17", "angegeben", "Die Frist ist abgelaufen",
                 V2.STAGING_LABELS["direct"], "ohne Beleg", "nicht umkehrbar", "wird einzeln geschrieben",
                 'data-approvable="true"', "Freigeben", "Ablehnen", "Offen"):
        assert text in row, text
    decided = _row(page, "c2")
    assert "Geschrieben" in decided and "Freigeben" not in decided


def test_the_whole_content_shows_its_diff(monkeypatch):
    whole = _preview(changes=[{"field": None, "before": "sha256:aa", "after": "sha256:bb",
                               "diff": "--- a\n+++ b\n-status: offen\n+status: erledigt\n"}])
    world = _world(monkeypatch, [_change_set("c1", actions=[_action(preview=whole)])])
    row = _row(world["http"].get("/builder/betrieb/freigaben").get_data(as_text=True), "c1")
    assert "ganzer Inhalt" in row and "+status: erledigt" in row


@pytest.mark.precondition
@pytest.mark.parametrize("preview,code", [
    (None, "preview_missing"),
    (_preview(exact=False, code="computed_field"), "computed_field"),
    (_preview(exact=False, code=None), "preview_not_exact"),
    (_preview(exact=True, read_version=None), "preview_not_exact"),     # exact, but read at no version
])
def test_an_action_without_an_exact_preview_is_never_offered_nor_approved(monkeypatch, preview, code):
    """GI-CONN-10; write_back_actions.als::approvalNeedsExactPreview: the Builder checks the marker itself, even when
    the Brain's answer says approvable."""
    cs = _change_set("c1", actions=[_action(preview=preview)], approvable=True)
    world = _world(monkeypatch, [cs])
    row = _row(world["http"].get("/builder/betrieb/freigaben").get_data(as_text=True), "c1")
    assert 'data-approvable="false"' in row and "Freigeben</button>" not in row and 'data-exact="false"' in row
    assert "Ablehnen" in row
    answer = world["http"].post("/builder/api/freigaben/change-sets/c1/approve")
    assert answer.status_code == 409 and answer.get_json()["code"] == "not_approvable"
    assert "genaue Vorschau" in answer.get_json()["error"]
    assert _approve_calls(world) == [], "nothing is sent to the Brain"
    assert approvals.change_set_row(cs, {})["actions"][0]["code"] == code


@pytest.mark.parametrize("change", [
    {"not_approvable": [{"action_id": "a1", "code": "preview_stale"}], "approvable": False},
    {"held": [{"id": "h1", "action_type": "set_status", "source": "akten", "code": "differing"}], "approvable": False},
    {"approvable": False},
])
def test_what_the_brain_does_not_approve_is_not_offered(monkeypatch, change):
    world = _world(monkeypatch, [_change_set("c1", **change)])
    row = _row(world["http"].get("/builder/betrieb/freigaben").get_data(as_text=True), "c1")
    assert 'data-approvable="false"' in row and "data-not-approvable" in row
    assert world["http"].post("/builder/api/freigaben/change-sets/c1/approve").status_code == 409
    assert _approve_calls(world) == []


def test_without_a_tenant_client_the_page_says_so(monkeypatch):
    monkeypatch.setenv("BUILDER_PROTOCOL_V2", "true")
    admin = StandInUser({"admin"})
    app = make_app(admin, MemoryWorkspaceStore(), tenant=TENANT, engine=FakeEngine())
    page = app.test_client().get("/builder/betrieb/freigaben").get_data(as_text=True)
    assert "liest keine Änderungssätze" in page


def test_a_brain_that_cannot_be_read_is_said_not_shown_as_empty(monkeypatch):
    world = _world(monkeypatch, [])

    def broken(state=None):
        raise connection_api.ConnectionApiError(503, "")
    world["tenant"].change_sets = broken
    page = world["http"].get("/builder/betrieb/freigaben").get_data(as_text=True)
    assert "Das Brain hat die Änderungssätze nicht geliefert (HTTP 503)" in page


# ------------------------------------------------------------------ deciding


def test_approving_sends_the_person_and_the_default_staging_direct(monkeypatch):
    world = _world(monkeypatch, [_change_set("c1")])
    answer = world["http"].post("/builder/api/freigaben/change-sets/c1/approve")
    assert answer.status_code == 200, answer.get_json()
    assert _approve_calls(world) == [("approve", "c1", {"kind": "user", "id": str(world["admin"].id)}, "direct")]
    body = answer.get_json()
    assert body["state"] == "approved" and body["staging"] == "direct" and "wird geschrieben" in body["status_text"]
    assert ("get", "c1") in world["tenant"].calls, "the change set is read again before the approval"


def test_a_source_published_with_native_review_is_approved_so(monkeypatch):
    world = _world(monkeypatch, [_change_set("c1")], staging="native_review")
    assert world["http"].post("/builder/api/freigaben/change-sets/c1/approve").status_code == 200
    assert _approve_calls(world)[0][3] == "native_review"
    row = _row(world["http"].get("/builder/betrieb/freigaben").get_data(as_text=True), "c1")
    assert V2.STAGING_LABELS["native_review"] in row


def test_a_staging_the_connector_did_not_declare_is_not_offered(monkeypatch):
    world = _world(monkeypatch, [_change_set("c1", actions=[_action(preview=_preview(stagings=["direct"]))])],
                   staging="native_review")
    row = _row(world["http"].get("/builder/betrieb/freigaben").get_data(as_text=True), "c1")
    assert 'data-approvable="false"' in row and "Schreibweise der Quelle" in row
    assert world["http"].post("/builder/api/freigaben/change-sets/c1/approve").status_code == 409
    assert _approve_calls(world) == []


def test_sources_that_write_differently_are_not_approved_together():
    cs = _change_set("c1", actions=[_action("a1", source="akten"), _action("a2", source="vertraege")])
    row = approvals.change_set_row(cs, {"akten": "direct", "vertraege": "native_review"})
    assert row["approvable"] is False and row["staging"] is None
    assert approvals.staging_by_source([{"sources": [{"key": "akten"}]},
                                        {"sources": [{"key": "akten", "staging": "native_review"}]}]) == {"akten": "ambiguous"}
    assert approvals.change_set_row(_change_set("c2"), {"akten": "ambiguous"})["approvable"] is False


def test_a_change_set_decided_meanwhile_is_not_approved(monkeypatch):
    world = _world(monkeypatch, [_change_set("c1", state="withdrawn")])
    assert world["http"].post("/builder/api/freigaben/change-sets/c1/approve").status_code == 409
    assert _approve_calls(world) == []


def test_rejecting_needs_a_reason_and_sends_it_as_the_note(monkeypatch):
    world = _world(monkeypatch, [_change_set("c1")])
    assert world["http"].post("/builder/api/freigaben/change-sets/c1/reject", json={}).status_code == 400
    answer = world["http"].post("/builder/api/freigaben/change-sets/c1/reject", json={"reason": "Belegt ist anderes."})
    assert answer.status_code == 200 and answer.get_json()["status_text"] == "Abgelehnt"
    assert world["tenant"].calls[-1] == ("reject", "c1", {"kind": "user", "id": str(world["admin"].id)},
                                         "Belegt ist anderes.")


def test_an_inexact_change_set_can_still_be_rejected(monkeypatch):
    world = _world(monkeypatch, [_change_set("c1", actions=[_action(preview=None)])])
    assert world["http"].post("/builder/api/freigaben/change-sets/c1/reject", json={"reason": "Keine Vorschau."}) \
        .status_code == 200


@pytest.mark.parametrize("roles", [{"builder_author"}, {"builder_reviewer"}, {"builder_viewer"}])
def test_deciding_needs_publish(monkeypatch, roles):
    world = _world(monkeypatch, [_change_set("c1")])
    world["gate"].user = StandInUser(roles)
    assert world["http"].post("/builder/api/freigaben/change-sets/c1/approve").status_code == 403
    assert world["http"].post("/builder/api/freigaben/change-sets/c1/reject", json={"reason": "nein"}).status_code == 403
    assert not any(c[0] in ("approve", "reject", "get") for c in world["tenant"].calls)


def test_a_consultant_with_a_grant_sees_no_change_sets(monkeypatch):
    world = _world(monkeypatch, [_change_set("c1")])
    world["gate"].user = StandInUser(frozenset(), "Bea Berater", "bea@extern.example")
    assert world["http"].get("/builder/betrieb/freigaben").status_code == 403
    assert world["http"].post("/builder/api/freigaben/change-sets/c1/approve").status_code == 403


def test_a_refusal_of_the_brain_is_said_in_german(monkeypatch):
    world = _world(monkeypatch, [_change_set("c1")])
    world["tenant"].refuse = (422, "staging_not_declared")
    answer = world["http"].post("/builder/api/freigaben/change-sets/c1/approve")
    assert answer.status_code == 422 and "Schreibweise nicht angeboten" in answer.get_json()["error"]


def test_the_badge_counts_open_change_sets_too(monkeypatch):
    world = _world(monkeypatch, [_change_set("c1"), _change_set("c2"), _change_set("c3", state="applied")])
    assert world["http"].get("/builder/api/freigaben/count").get_json() == {"open": 2}


# ------------------------------------------------------------------ the staging per source (C-Q1)


def _edit(world, **op):
    return world["http"].post(f"/builder/api/w/{world['workspace']}/ontology?view=sources",
                              json={"op": "source.switches", "key": "akten", **op})


def test_the_staging_is_chosen_per_source_and_shown(monkeypatch):
    world = _world(monkeypatch, [])
    answer = _edit(world, staging="native_review")
    assert answer.status_code == 200, answer.get_json()
    assert world["store"].model(world["workspace"])["sources"][0]["staging"] == "native_review"
    page = world["http"].get("/builder/quellen").get_data(as_text=True)
    raw = page.split('<script type="application/json" id="b-src-data">', 1)[1].split("</script>", 1)[0]
    [akten] = [s for s in json.loads(raw)["sources"] if s["key"] == "akten"]
    assert akten["staging"] == "native_review" and akten["staging_published"] == "direct"


@pytest.mark.parametrize("staging,declared", [("native_review", ["direct"]), ("proposal", ["direct", "native_review"])])
def test_a_staging_the_component_does_not_offer_is_refused(monkeypatch, staging, declared):
    world = _world(monkeypatch, [], decl=_declaration(stagings=declared))
    answer = _edit(world, staging=staging)
    assert answer.status_code in (400, 409) and "staging" not in world["store"].model(world["workspace"])["sources"][0]


def test_without_the_switch_no_staging_is_chosen(monkeypatch):
    world = _world(monkeypatch, [], on=False)
    assert _edit(world, staging="direct").status_code == 409
    assert V2.staging_of({}) == "direct" and V2.staging_of({"staging": "native_review"}) == "native_review"
    assert V2.staging_of({"staging": "proposal"}) == "direct"


# ------------------------------------------------------------------ the Brain's routes


def test_the_brain_connection_calls_the_change_set_routes():
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
                return Answer(409, {"status": "error", "error": "Only a proposed change set can be rejected",
                                    "error_code": "not_proposed"})
            if method == "GET" and endpoint.startswith("/secured/change-sets?") or endpoint == "/secured/change-sets":
                return Answer(200, {"status": "success", "change_sets": [{"id": "c1"}], "count": 1})
            return Answer(200, {"status": "success", "change_set": {"id": "c 1", "state": "approved"}})

    client = Client()
    brain = connection_api.BrainConnection(client)
    assert brain.change_sets("proposed") == [{"id": "c1"}]
    assert brain.change_set("c 1")["id"] == "c 1"
    assert brain.approve_change_set("c 1", {"kind": "user", "id": "u"}, "direct")["state"] == "approved"
    with pytest.raises(connection_api.ConnectionApiError) as refused:
        brain.reject_change_set("c1", {"kind": "user", "id": "u"}, "nein")
    assert refused.value.code == "not_proposed" and refused.value.status == 409
    bare = connection_api.BrainConnection(type("C", (), {"_request_quiet": lambda self, *a, **k: Answer(200, {"x": 1})})())
    for call in (lambda: bare.change_sets(), lambda: bare.change_set("c1")):
        with pytest.raises(connection_api.ConnectionApiError) as wrong:
            call()
        assert wrong.value.code == "bad_answer"
    assert client.calls == [
        ("GET", "/secured/change-sets?state=proposed", None),
        ("GET", "/secured/change-sets/c%201", None),
        ("POST", "/secured/change-sets/c%201/approve", {"actor": {"kind": "user", "id": "u"}, "staging": "direct"}),
        ("POST", "/secured/change-sets/c1/reject", {"actor": {"kind": "user", "id": "u"}, "note": "nein"})]


def test_changes_that_name_their_step_are_shown_per_step(monkeypatch):
    """Protocol 2.0 with ``step`` on each change (the founder's D-14): two actions on one item are told apart."""
    stepped = _preview(changes=[{"step": 1, "field": "owner", "before": "Max", "after": "Erika"},
                                {"step": 0, "field": "status", "before": "offen", "after": "erledigt"}])
    world = _world(monkeypatch, [_change_set("c1", actions=[_action(preview=stepped)])])
    row = _row(world["http"].get("/builder/betrieb/freigaben").get_data(as_text=True), "c1")
    assert row.index("Schritt 1:") < row.index("Schritt 2:")
    assert [c["step"] for c in approvals.change_set_row(_change_set("c2", actions=[_action(preview=stepped)]), {})
            ["actions"][0]["preview"]["changes"]] == [0, 1]
