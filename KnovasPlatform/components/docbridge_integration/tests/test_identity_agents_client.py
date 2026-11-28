"""RB-6: what leaves the Platform's Knovas client while an agent acts (the Knovas Assistent answering a person).

Inside ``asserting_for(AgentPrincipal(...))`` every call carries the agent's claim (the broker signs it) and declares
the agent as its actor: in the body of a POST, as ``?actor=<base64url JSON>`` on a GET and never both (the Core
refuses a request that declares it twice, ``services/request_actor.py``). Outside the block nothing changes: a person
declares no actor. Work order docs/platform-mvp/work-orders/RB-6-7.md § 3.

No PostgreSQL: the broker is a stub that signs with a real key.
"""
from __future__ import annotations

import base64
import json
import uuid

import pytest

from identity.agents import AgentPrincipal
from identity.assertion import AssertionVerifier
from identity.broker_key import load_or_create_signer, public_pem
from knovas_client import ACTOR_FIELD, ASSERTION_FIELD, KnovasAPIClient

TENANT = "tenant-a"
PERSON = uuid.UUID("11111111-1111-1111-1111-111111111111")
AGENT = uuid.UUID("22222222-2222-2222-2222-222222222222")
ACTOR = {"kind": "agent", "id": str(AGENT), "on_behalf_of": {"kind": "user", "id": str(PERSON)}}


class _Config:
    def __init__(self, values):
        self._v = dict(values)

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_bool(self, key, default=False):
        return bool(self._v.get(key, default))

    def get_int(self, key, default=0):
        return int(self._v.get(key, default))


class _Person:
    id = PERSON
    roles = ("member",)


class _Broker:
    """Signs the person, or the agent for an AgentPrincipal (as PrincipalBroker does), with a real key."""

    def __init__(self, signer):
        self._signer = signer

    def current_user(self):
        return _Person()

    def assertion_for(self, user):
        if isinstance(user, AgentPrincipal):
            return self._signer.mint(subject=str(user.agent_id), tenant=TENANT, groups=["g-1"], roles=["member"])
        return self._signer.mint(subject=str(user.id), tenant=TENANT, groups=["g-1", "g-direct"], roles=["member"])


class _Response:
    status_code = 200
    content = b"{}"
    headers: dict = {}
    text = "{}"

    def json(self):
        return {"status": "success", "node_types": [], "results": [], "content": "ok", "model": "m",
                "prompt_tokens": 1, "completion_tokens": 1}

    def raise_for_status(self):
        return None


class _Wire:
    def __init__(self):
        self.sent = []

    def request(self, method=None, url=None, json=None, params=None, **kwargs):
        self.sent.append({"method": method, "url": url, "json": json, "params": params})
        return _Response()


@pytest.fixture
def key_dir(tmp_path):
    path = tmp_path / "broker"
    path.mkdir()
    return path


@pytest.fixture
def verifier(key_dir):
    load_or_create_signer(key_dir)
    kid = (key_dir / "broker_ed25519.kid").read_text().strip()
    return AssertionVerifier({kid: public_pem(key_dir)})


@pytest.fixture
def client(key_dir):
    built = KnovasAPIClient(config_loader=_Config({"api.base_url": "http://example.test",
                                                   "api.customer_id": TENANT}))
    built._request_interval = 0
    built._session = _Wire()
    built.attach_principal_broker(_Broker(load_or_create_signer(key_dir)))
    return built


def _decoded(text):
    return json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode("utf-8"))


def test_the_principal_declares_the_agent_and_the_person():
    assert AgentPrincipal(agent_id=AGENT, on_behalf_of=PERSON).declared_actor == ACTOR
    assert AgentPrincipal(agent_id=AGENT).declared_actor == {"kind": "agent", "id": str(AGENT)}


def test_a_graph_get_declares_the_actor_in_the_query_string_and_not_in_the_body(client, verifier):
    with client.asserting_for(AgentPrincipal(agent_id=AGENT, on_behalf_of=PERSON)):
        client.graph_node_types()
    (sent,) = client._session.sent
    assert sent["method"] == "GET" and sent["url"].endswith("/secured/graph/node-types")
    assert ACTOR_FIELD not in sent["json"]
    assert _decoded(sent["params"][ACTOR_FIELD]) == ACTOR
    assert "=" not in sent["params"][ACTOR_FIELD]                        # base64url without padding
    assert verifier.verify(sent["json"][ASSERTION_FIELD], tenant=TENANT).subject == str(AGENT)


def test_a_post_declares_the_actor_in_the_body_and_not_in_the_query_string(client, verifier):
    with client.asserting_for(AgentPrincipal(agent_id=AGENT, on_behalf_of=PERSON)):
        client.llm_generate([{"role": "user", "content": "x"}])
    (sent,) = client._session.sent
    assert sent["method"] == "POST"
    assert sent["json"][ACTOR_FIELD] == ACTOR
    assert not (sent["params"] or {}).get(ACTOR_FIELD)
    claim = verifier.verify(sent["json"][ASSERTION_FIELD], tenant=TENANT)
    assert (claim.subject, claim.groups) == (str(AGENT), ("g-1",))


def test_the_quiet_path_and_the_parallel_graph_reads_declare_it_too(client):
    with client.asserting_for(AgentPrincipal(agent_id=AGENT, on_behalf_of=PERSON)):
        client._request_quiet("GET", "/secured/graph/nodes/n-1")
        client._request_quiet("POST", "/secured/graph/doc-values/find", {})
        client.graph_get_many([("/nodes/n-1", {"x": "1"}), ("/nodes/n-2", None)], workers=2)
    quiet_get, quiet_post, *many = client._session.sent
    assert _decoded(quiet_get["params"][ACTOR_FIELD]) == ACTOR and ACTOR_FIELD not in quiet_get["json"]
    assert quiet_post["json"][ACTOR_FIELD] == ACTOR and not quiet_post["params"]
    assert len(many) == 2
    for sent in many:
        assert _decoded(sent["params"][ACTOR_FIELD]) == ACTOR and ACTOR_FIELD not in sent["json"]
    assert {(s["params"] or {}).get("x") for s in many} == {"1", None}


def test_a_person_declares_no_actor_as_before(client, verifier):
    client.graph_node_types()
    client.llm_generate([{"role": "user", "content": "x"}])
    for sent in client._session.sent:
        assert ACTOR_FIELD not in sent["json"] and not (sent["params"] or {}).get(ACTOR_FIELD)
        assert verifier.verify(sent["json"][ASSERTION_FIELD], tenant=TENANT).subject == str(PERSON)


def test_the_block_ends_with_the_agent(client):
    with client.asserting_for(AgentPrincipal(agent_id=AGENT, on_behalf_of=PERSON)):
        pass
    client.llm_generate([{"role": "user", "content": "x"}])
    (sent,) = client._session.sent
    assert ACTOR_FIELD not in sent["json"]


def test_a_principal_without_a_dict_actor_declares_nothing(client):
    class Odd:
        id = PERSON
        roles = ("member",)
        declared_actor = "agent"                                          # not a dict: ignored

    with client.asserting_for(Odd()):
        client.llm_generate([{"role": "user", "content": "x"}])
    (sent,) = client._session.sent
    assert ACTOR_FIELD not in sent["json"]
