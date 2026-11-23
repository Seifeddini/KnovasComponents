from knovas_discovery.probes import Context, directory as d
from knovas_discovery.secrets import Secrets
from knovas_discovery.util import ProbeError

from conftest import BASE, NOW, FakeLdap, directory_entries, group, user


def test_facts_from_entries():
    e = directory_entries()
    facts, detail = d.directory_facts(e["root"], e["users"], e["groups"], e["computers"], e["ous"])
    assert facts == {"is_active_directory": True, "people_enabled": 3, "people_disabled": 1, "service_accounts": 2,
                     "groups": 4, "groups_empty": 1, "max_nesting": 2, "computers": 2}
    assert detail["groups"] == {"GG_All": 2, "GG_Empty": 0, "GG_Lawyers": 2, "GG_Partner": 1}
    assert detail["ous"] == ["Firm", "Firm/Groups", "Firm/Staff"]
    assert detail["service_accounts"] == ["scanner", "svc-backup"]


def test_rules():
    assert d.is_builtin_account(user("x", "renamed-admin", rid=500))
    assert d.is_builtin_account(user("krbtgt", "krbtgt", rid=1999))
    assert d.is_service_account(user("x", "sa-sql"))
    assert d.is_service_account(user("x", "ServiceDesk"))
    assert d.is_service_account(user("x", "backup", ou="OU=Dienstkonten,OU=Firm"))
    assert not d.is_service_account(user("x", "asvc"))
    assert d.is_disabled(user("x", "y", disabled=True)) and not d.is_disabled(user("x", "y"))
    assert d.is_default_group(group("Domain Users", [], ou="CN=Users", rid=513))
    assert d.is_default_group(group("DnsUpdateProxy", [], ou="CN=Users", rid=1102))
    assert d.is_default_group(group("Remote Desktop Users", [], ou="CN=Builtin", rid=555))
    assert not d.is_default_group(group("Firm-Made In Users", [], ou="CN=Users", rid=1500))
    assert not d.is_default_group(group("GG_X", [], rid=1500))
    assert d.ou_path(f"CN=a,OU=Mitarbeiter,OU=Kanzlei,{BASE}") == "Kanzlei/Mitarbeiter"
    assert d.is_active_directory({"defaultnamingcontext": [BASE]})
    assert not d.is_active_directory({"supportedcapabilities": ["1.3.6.1.4.1.4203.1.9.1.1"]})


def test_nesting_survives_a_cycle():
    a = group("A", [f"CN=B,OU=Groups,OU=Firm,{BASE}"])
    b = group("B", [f"CN=C,OU=Groups,OU=Firm,{BASE}"])
    c = group("C", [f"CN=A,OU=Groups,OU=Firm,{BASE}"])
    assert d.max_nesting([a, b, c]) == 2
    assert d.max_nesting([group("Solo", [])]) == 0
    assert d.max_nesting([]) == 0


def test_probe_with_a_fake_reader():
    fake = FakeLdap(directory_entries())
    ctx = Context(now=NOW, secrets=Secrets(environ={"PW": "x"}, interactive=False), ldap_factory=fake)
    target = {"key": "ad", "url": "ldaps://dc", "bind_user": "svc@x", "password_env": "PW", "base_dn": BASE,
              "verify_tls": False}
    [out] = d.run(target, ctx)
    assert out.result["status"] == "ok" and out.result["facts"]["people_enabled"] == 3
    assert fake.calls == [("ldaps://dc", "svc@x", False)]


def test_probe_without_password_or_with_a_bind_failure():
    ctx = Context(now=NOW, secrets=Secrets(environ={}, interactive=False), ldap_factory=FakeLdap(directory_entries()))
    [out] = d.run({"key": "ad", "password_env": "MISSING"}, ctx)
    assert out.result == {"key": "ad", "status": "failed", "error": "password_missing:MISSING", "facts": {}}
    ctx.secrets = Secrets(environ={"PW": "x"}, interactive=False)
    ctx.ldap_factory = FakeLdap({}, fail=ProbeError("ldap_bind_failed:invalidCredentials"))
    [out] = d.run({"key": "ad", "password_env": "PW"}, ctx)
    assert out.result["status"] == "failed" and out.result["error"] == "ldap_bind_failed:invalidCredentials"


def test_real_reader_refuses_plain_ldap():
    try:
        d.Ldap3Reader("ldap://dc.example.test", "u", "p")
    except ProbeError as exc:
        assert exc.code == "ldaps_required"
    else:
        raise AssertionError("plain LDAP was accepted")
