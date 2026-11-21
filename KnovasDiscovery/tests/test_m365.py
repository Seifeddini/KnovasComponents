"""Microsoft Graph against recorded answers (fixtures/m365_graph.json). No tenant was used: see the README."""

from knovas_discovery.probes import Context, m365
from knovas_discovery.secrets import Secrets

from conftest import NOW, FakeSession

TARGET = {"key": "tenant", "tenant_id": "00000000-0000-0000-0000-000000000000", "client_id": "app",
          "secret_env": "SECRET"}


def ctx(session):
    return Context(now=NOW, secrets=Secrets(environ={"SECRET": "s3cr3t"}, interactive=False), http_session=session)


def test_facts_from_recorded_graph_answers():
    session = FakeSession()
    [out] = m365.run(TARGET, ctx(session))
    assert out.result["status"] == "ok", out.result
    assert out.result["facts"] == {
        "people": 2,                                   # enabled members with a licence; guest and disabled not
        "licences": {"FLOW_FREE": 1, "SPB": 12},
        "sharepoint_sites": 2,                         # the personal site is not counted
        "drives_bytes": 1500 + 500 + 250,              # two libraries and one OneDrive (the other has none: 404)
        "exchange_online": True,
        "teams": 3,
    }
    assert "GET https://graph.microsoft.com/v1.0/users?$skiptoken=page2" in session.requests   # paging followed
    assert out.detail["sites"][0].startswith("https://example.sharepoint.com/sites/")


def test_token_failure_is_a_failed_probe_without_the_secret():
    [out] = m365.run(TARGET, ctx(FakeSession(token_status=401)))
    assert out.result["status"] == "failed"
    assert out.result["error"] == "m365_token_failed:invalid_client"
    assert "s3cr3t" not in str(out.result) + str(out.detail)


def test_a_forbidden_call_leaves_its_fact_null():
    session = FakeSession()
    session.routes["get"]["/groups?"] = {"status": 403, "body": {"error": {"code": "Authorization_RequestDenied"}}}
    [out] = m365.run(TARGET, ctx(session))
    assert out.result["status"] == "partial"
    assert out.result["error"] == "teams:graph_http_403"
    assert out.result["facts"]["teams"] is None and out.result["facts"]["people"] == 2


def test_no_tenant_is_skipped():
    [out] = m365.run({"key": "tenant", "tenant_id": "", "client_id": ""}, ctx(FakeSession()))
    assert (out.result["status"], out.result["error"]) == ("skipped", "no_tenant_in_intake")
