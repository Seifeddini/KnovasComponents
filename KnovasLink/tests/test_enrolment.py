"""Knovas Link's skeleton, identity and enrolment (MVP-15, KC-7a; CL-3: DEC-0056).

Link enrols as kind knovas_link with the site's one-time token; its key is made on site and never leaves it. With a
certificate in the enrolment answer it works; without one it runs its control plane but waits for its certificate
and claims nothing. The tenant's own certificate is honoured only behind the test-only switch.
"""
from __future__ import annotations

import asyncio
import json
import logging
import stat
import time

import pytest

from conftest import CA, CONTROL, LINK_ID, SITE_TOKEN, eventually, make_job
from knovas_link import KIND, VERSION, contracts
from knovas_link.errors import StartupError
from knovas_link.settings import Settings


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _prepare(link):
    async def go():
        try:
            await link.prepare()
        finally:
            await link.stop()
    asyncio.run(go())


# ------------------------------------------------------------------ settings

def test_settings_come_from_the_environment_only():
    s = Settings.from_env({"LINK_PLATFORM_URL": "https://platform.example.test/api/connectors/v1",
                           "LINK_ENROL_TOKEN": " kce_abc ", "LINK_PLATFORM_CA": "/ca.pem"})
    assert (s.platform_url, s.enrol_token, s.platform_ca) == ("https://platform.example.test/api/connectors/v1", "kce_abc", "/ca.pem")
    assert (str(s.data_dir), str(s.tokens_dir), s.port) == ("/data", "/link", 8460)
    assert s.default_components == ("sync-component", "knovas-connector")
    assert s.test_only_shared_certificate is False


def test_settings_refuse_a_missing_platform_url():
    with pytest.raises(StartupError, match="settings.link_platform_url"):
        Settings.from_env({})
    with pytest.raises(StartupError):
        Settings.from_env({"LINK_PLATFORM_URL": "platform.example.test"})


def test_the_shared_certificate_switch_reads_only_true():
    on = Settings.from_env({"LINK_PLATFORM_URL": "https://p.test", "LINK_TEST_ONLY_SHARED_CERTIFICATE": "true"})
    off = Settings.from_env({"LINK_PLATFORM_URL": "https://p.test", "LINK_TEST_ONLY_SHARED_CERTIFICATE": "maybe"})
    assert on.test_only_shared_certificate and not off.test_only_shared_certificate


# ------------------------------------------------------------------ enrolment

def test_first_start_enrols_as_knovas_link_with_a_key_made_on_site(world):
    with world.running() as (client, link):
        assert link.certificate == "present"
        eventually(lambda: link.channel.state == "connected")
        ping = client.get("/local/v1/ping", headers=world.headers())
        assert ping.json() == {"version": VERSION, "certificate": "present", "work_channel": "connected"}
    (enrolment,) = world.platform.enrolments
    assert enrolment["token"] == SITE_TOKEN and enrolment["kind"] == KIND == "knovas_link"
    assert enrolment["version"] == VERSION and "work_channel" in enrolment["capabilities"]
    assert "BEGIN CERTIFICATE REQUEST" in enrolment["csr"]
    assert "PRIVATE KEY" not in json.dumps(enrolment), "the key never leaves the site"
    folder = world.data_dir / "identity"
    assert _mode(folder) == 0o700
    for name in ("component.key", "component.crt", "chain.pem", "identity.json"):
        assert _mode(folder / name) == 0o600, name
    assert "PRIVATE KEY" in (folder / "component.key").read_text()
    kept = json.loads((folder / "identity.json").read_text())
    assert kept["component"] == LINK_ID and kept["control_token"] == CONTROL
    assert SITE_TOKEN not in (folder / "identity.json").read_text(), "of the one-time token only its hash is kept"
    assert (folder / "chain.pem").read_text().strip() == CA.pem.strip()


def test_a_restart_with_the_same_token_does_not_enrol_again(world):
    with world.running():
        pass
    with world.running() as (_, link):
        assert link.certificate == "present"
    assert len(world.platform.enrolments) == 1


def test_a_new_token_enrols_again(world):
    with world.running():
        pass
    renewed = "kce_" + "r" * 40
    world.platform.tokens.add(renewed)
    with world.running(enrol_token=renewed) as (_, link):
        assert link.certificate == "present"
    assert [e["token"] for e in world.platform.enrolments] == [SITE_TOKEN, renewed]


def test_without_a_token_and_not_enrolled_link_does_not_start(world):
    with pytest.raises(StartupError, match="enrol.no_token"):
        _prepare(world.link(enrol_token=""))


def test_a_refused_token_keeps_nothing(world, caplog):
    caplog.set_level(logging.INFO)
    world.platform.tokens.clear()
    with pytest.raises(StartupError, match="enrol.bad_token"):
        _prepare(world.link())
    folder = world.data_dir / "identity"
    assert not (folder / "identity.json").exists() and not (folder / "component.key").exists()


def test_a_refused_renewal_keeps_the_old_key_with_its_certificate(world):
    with world.running():
        pass
    folder = world.data_dir / "identity"
    key, cert = (folder / "component.key").read_bytes(), (folder / "component.crt").read_bytes()
    with pytest.raises(StartupError):
        _prepare(world.link(enrol_token="kce_" + "x" * 40))
    assert (folder / "component.key").read_bytes() == key and (folder / "component.crt").read_bytes() == cert


def test_an_unreachable_platform_at_first_start_is_a_clear_refusal(world):
    world.platform.transport.down = True
    with pytest.raises(StartupError, match="enrol.unreachable"):
        _prepare(world.link())


def test_contracts_must_match_their_lock(tmp_path):
    for name in ("contracts.lock", *contracts.USED):
        (tmp_path / name).write_bytes((contracts.FOLDER / name).read_bytes())
    contracts.verify(tmp_path)
    (tmp_path / "job.schema.json").write_bytes(b"{}")
    with pytest.raises(StartupError, match="contracts.stale"):
        contracts.verify(tmp_path)


# ------------------------------------------------------------------ waiting for the certificate (DEC-0056)

def test_without_a_certificate_link_runs_its_control_plane_but_claims_nothing(world, caplog):
    caplog.set_level(logging.INFO)
    world.platform.issue_certificate = False
    job = world.secure.enqueue(make_job("fetch"))
    with world.running(loops=("channel", "heartbeat")) as (client, link):
        assert link.certificate == "waiting" and link.brain is None
        assert client.get("/local/v1/ping", headers=world.headers()).json()["certificate"] == "waiting"
        beat = eventually(lambda: world.platform.heartbeats)[0]
        assert beat["site"]["certificate"] == "waiting" and beat["state"] == "degraded"
        assert client.post("/local/v1/register", headers=world.headers(),
                           json={"component": "sync-component", "kind": "sync_component", "version": "0.2.0",
                                 "capabilities": [], "job_kinds": ["fetch"]}).status_code == 200
        assert client.get("/local/v1/jobs?wait=0", headers=world.headers()).status_code == 204
        assert client.portal.call(link.channel.probe_now) is False
        time.sleep(0.2)
    assert world.secure.claims == [] and world.secure.queued == [job], "no job claimed, no probe sent"
    assert "certificate.waiting" in caplog.text
    assert not (world.data_dir / "identity" / "component.crt").exists()


# ------------------------------------------------------------------ the test-only exception

def _tenant_files(tmp_path):
    cert, key = tmp_path / "tenant.crt", tmp_path / "tenant.key"
    cert.write_text(CA.pem)
    key.write_text("not used by the in-process transport")
    return str(cert), str(key)


def test_the_tenant_certificate_is_honoured_only_behind_the_test_switch(world, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    world.platform.issue_certificate = False
    cert, key = _tenant_files(tmp_path)
    with world.running(loops=("heartbeat",), brain_cert=cert, brain_key=key) as (_, link):
        assert link.certificate == "waiting"
    assert "certificate.shared_ignored" in caplog.text


def test_the_test_only_shared_certificate_warns_at_start_and_in_every_heartbeat(world, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    world.platform.issue_certificate = False
    cert, key = _tenant_files(tmp_path)
    with world.running(loops=("heartbeat", "channel"), brain_cert=cert, brain_key=key,
                       test_only_shared_certificate=True) as (_, link):
        assert link.certificate == "present" and link.certificate_mode == "test_only_shared"
        beat = eventually(lambda: world.platform.heartbeats)[0]
        assert beat["warnings"] == ["certificate.test_only_shared"]
        assert beat["site"]["certificate"] == "present"
        eventually(lambda: world.secure.claims)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "NEVER FOR A COMPANY" in r.getMessage()]
    assert warnings


def test_an_issued_certificate_wins_over_the_shared_one(world, tmp_path):
    cert, key = _tenant_files(tmp_path)
    with world.running(loops=("heartbeat",), brain_cert=cert, brain_key=key,
                       test_only_shared_certificate=True) as (_, link):
        assert link.certificate_mode == "issued"
        beat = eventually(lambda: world.platform.heartbeats)[0]
        assert "warnings" not in beat


# ------------------------------------------------------------------ token files and logs

def test_link_writes_a_token_per_component_and_keeps_it_across_restarts(world):
    with world.running():
        names = sorted(p.name for p in world.tokens_dir.iterdir())
        assert names == ["knovas-connector", "records-reader", "sync-component"]
        first = {n: world.token(n) for n in names}
        for n in names:
            assert _mode(world.tokens_dir / n / "token") == 0o644
            assert len(first[n]) >= 32
        assert len(set(first.values())) == 3, "one random token per component"
    with world.running():
        assert {n: world.token(n) for n in names} == first


def test_logs_carry_no_token_key_or_certificate(world, caplog):
    caplog.set_level(logging.DEBUG)
    with world.running(loops=("channel", "heartbeat")) as (client, _):
        client.get("/local/v1/ping", headers=world.headers())
        eventually(lambda: world.platform.heartbeats)
    text = caplog.text
    for secret in (SITE_TOKEN, CONTROL, world.token("sync-component"), "BEGIN", "example-org"):
        assert secret not in text, secret
