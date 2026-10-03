"""Deployment and documentation leftovers of the Experimente fix round
(deploydocs group).

These tests read files outside the component -- the root docker-compose.yml,
both nginx configs, knovas.env.example and the German user guide -- and tie
them to the code that gives them meaning. They skip when the component is
used outside the repository.

* PLATFORM_TRUSTED_PROXY_HOPS: in production a request passes host nginx and
  docbridge-web-nginx, and both append to X-Forwarded-For. Compose therefore
  sets 2 for docbridge-web; with the code's default of 1 every session and
  audit row recorded the Docker gateway instead of the user.
* The user guide quotes messages people see (index states, purge-index,
  evaluator warnings, the CSV refusal) and lists the core pack's generic
  metrics. Each quote is produced from the code here, so a reworded message
  or a changed pack cannot leave the guide describing something else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

COMPONENT = Path(__file__).resolve().parents[1]
REPO = COMPONENT.parents[2]
COMPOSE = REPO / "docker-compose.yml"
ENV_EXAMPLE = REPO / "knovas.env.example"
HOST_NGINX = REPO / "KnovasPlatform" / "deploy" / "host-nginx" / "knovas-platform.conf.example"
DEPLOY_DOC = REPO / "KnovasPlatform" / "docs" / "deployment" / "host-nginx-internal.md"
GUIDE = REPO / "KnovasPlatform" / "docs" / "features" / "experiments.md"
CORE_PACK = COMPONENT / "src" / "experiments" / "packs" / "core.yaml"

pytestmark = pytest.mark.skipif(not COMPOSE.exists(), reason="not inside the repository")

HOPS = "PLATFORM_TRUSTED_PROXY_HOPS"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _service(compose: str, name: str) -> str:
    match = re.search(r"\n  %s:\n(.*?)(?=\n  \S|\n\S)" % re.escape(name), compose, re.S)
    assert match, f"no {name} service in docker-compose.yml"
    return match.group(1)


def _compose_hops() -> int:
    service = _service(_read(COMPOSE), "docbridge-web")
    match = re.search(r"^\s+%s:\s*\$\{%s:-(\d+)\}\s*$" % (HOPS, HOPS), service, re.M)
    assert match, "docbridge-web does not set PLATFORM_TRUSTED_PROXY_HOPS"
    return int(match.group(1))


def _adding_proxies() -> list:
    """The nginx layers between the user and gunicorn in the documented
    production setup, each checked to add exactly one entry to the header:
    host nginx sets it to the address it saw, docbridge-web-nginx appends."""
    nginx = _service(_read(COMPOSE), "docbridge-web-nginx")
    mounted = re.search(r"\./(\S+\.conf):/etc/nginx/conf\.d/default\.conf", nginx)
    assert mounted, "docbridge-web-nginx mounts no config"
    for config, value in ((HOST_NGINX, "$remote_addr"), (REPO / mounted.group(1), "$proxy_add_x_forwarded_for")):
        text = _read(config)
        passes = re.findall(r"^\s*proxy_pass\s", text, re.M)
        headers = re.findall(r"^\s*proxy_set_header\s+X-Forwarded-For\s+(\S+);", text, re.M)
        # One header line per location or one for the server block, never another value.
        assert passes and headers and set(headers) == {value}, (config, headers)
    return [HOST_NGINX, REPO / mounted.group(1)]


@pytest.fixture
def ip_in(monkeypatch):
    from flask import Flask

    from identity.webauth import client_ip

    app = Flask(__name__)

    def run(xff: str, *, hops: int, remote: str = "172.18.0.3") -> str:
        monkeypatch.setenv(HOPS, str(hops))
        with app.test_request_context("/", headers=[("X-Forwarded-For", xff)],
                                      environ_base={"REMOTE_ADDR": remote}):
            return client_ip()

    return run


# -- PLATFORM_TRUSTED_PROXY_HOPS ------------------------------------------------------


def test_compose_trusts_exactly_the_proxies_that_add_an_entry():
    # Too high lets a browser choose its address, too low records a proxy.
    assert _compose_hops() == len(_adding_proxies()) == 2


def test_behind_both_proxies_the_users_address_is_recorded(ip_in):
    hops = _compose_hops()
    # What reaches gunicorn: host nginx's entry (the user) and
    # docbridge-web-nginx's (the Docker gateway host nginx came from). An
    # older host nginx template appended, keeping the browser's own entry in
    # front; the result is the same.
    assert ip_in("203.0.113.7, 172.18.0.1", hops=hops) == "203.0.113.7"
    assert ip_in("6.6.6.6, 203.0.113.7, 172.18.0.1", hops=hops) == "203.0.113.7"
    # The code's own default, which the compose value replaces.
    assert ip_in("6.6.6.6, 203.0.113.7, 172.18.0.1", hops=1) == "172.18.0.1"


def test_the_setting_is_documented_with_the_compose_default():
    env = _read(ENV_EXAMPLE)
    assert re.search(r"^# %s=%d$" % (HOPS, _compose_hops()), env, re.M)
    assert "DOCBRIDGE_WEB_BIND=0.0.0.0" in env and "set 1" in env
    doc = _read(DEPLOY_DOC)
    for value in ("`2`", "`1`", "`0`"):
        assert value in doc.split("### Client addresses behind two proxies", 1)[1].split("## 2.", 1)[0]
    assert HOPS in _read(REPO / "RELEASE_NOTES.md")


# -- the user guide quotes the code -----------------------------------------------------


@pytest.fixture(scope="module")
def guide() -> str:
    # One line per paragraph and table row is not guaranteed; compare with
    # the line breaks folded to spaces.
    return " ".join(_read(GUIDE).split())


def test_the_index_states_in_the_guide_are_the_ones_the_page_shows(guide):
    from experiments import indexer, tasks

    for message in (tasks.MSG_INDEX_DEAD, tasks.MSG_INDEX_INCOMPLETE, indexer.MSG_NO_GROUP):
        assert f"| Fehler: {message} |" in guide, message
    assert "| Fehler: " + indexer.MSG_REJECTED.format(code="\u2026") + " |" in guide
    # A refusal is not repeated by the maintenance; the retryable ones are.
    assert indexer.MSG_REJECTED not in tasks.RETRYABLE_INDEX_ERRORS
    assert {tasks.MSG_INDEX_DEAD, tasks.MSG_INDEX_INCOMPLETE, indexer.MSG_NO_GROUP} <= set(
        tasks.RETRYABLE_INDEX_ERRORS)


def test_the_purge_index_messages_in_the_guide_are_the_commands(guide):
    from experiments import indexer

    assert f"\u00abAbgebrochen: {indexer.MSG_LISTING_UNREACHABLE}\u00bb" in guide
    assert f"\u00abAbgebrochen: {indexer.MSG_UNREACHABLE}\u00bb" in guide
    refused = indexer.MSG_LISTING_REFUSED.format(code="\u2026")
    assert f"\u00ab{refused}\u00bb" in guide


def test_the_csv_refusal_in_the_guide_is_the_routes(guide):
    from web_interface.experiments_routes import MSG_CSV_BUSY

    assert MSG_CSV_BUSY in guide
    assert "HTTP 503" in guide


def _evaluator_input(kind, aggregates, params=None):
    return {
        "experiment": {"key": "X-1", "title": "t", "hypothesis": "", "domain": "d", "type": "t",
                       "status": "running", "fields": {}, "tags": []},
        "metric": {"key": "m", "name": "Score", "kind": kind, "unit": "", "direction": "higher",
                   "role": "primary", "definition": {}, "guardrail": None},
        "variants": [{"key": "A", "name": "Kontrolle", "is_control": True},
                     {"key": "B", "name": "Variante B", "is_control": False}],
        "aggregates": aggregates, "rows": [], "rows_truncated": False, "scope": {},
        "params": params or {},
    }


def _agg(variant, n, value_sum, **extra):
    row = {"variant": variant, "rows": 1, "n": n, "value_sum": value_sum,
           "denominator_sum": None, "sum_sq": None, "estimate": None, "levels": None}
    row.update(extra)
    return row


def test_the_evaluator_warnings_in_the_guide_are_what_the_builtins_say(guide):
    from experiments import evaluators

    # describe: no spread, no interval.
    same = evaluators.run_builtin("builtin.describe", _evaluator_input(
        "mean", [_agg("A", 3, 6.0, sum_sq=14.0), _agg("B", 2, 160.0, sum_sq=12800.0)]))
    spread = [w for w in same["warnings"] if w.startswith("B: ")]
    assert spread and f"\u00ab{spread[0]}\u00bb" in guide
    # describe: a proportion target of 80 instead of 0.8.
    target = evaluators.run_builtin("builtin.describe", _evaluator_input(
        "proportion", [_agg("A", 100, 70.0), _agg("B", 100, 75.0)], params={"target": 80}))
    assert target["verdict"] == "n/a"
    assert target["warnings"] and all(f"\u00ab{w}\u00bb" in guide for w in target["warnings"])
    # chi_square on a scale without a distribution (more than 50 values).
    many = evaluators.run_builtin("builtin.chi_square", _evaluator_input(
        "ordinal", [_agg("A", 60, 1770.0, sum_sq=70000.0),
                    _agg("B", 8, 16.0, sum_sq=40.0, levels={"1": 2, "2": 4, "3": 2})]))
    assert many["verdict"] == "n/a"
    assert f"\u00ab{many['headline'].split(': ', 1)[1]}\u00bb" in guide


def test_the_guide_lists_the_generic_metrics_of_the_core_pack(guide):
    pack = yaml.safe_load(_read(CORE_PACK))
    generic = [m for m in pack["metrics"] if m["key"].startswith("generic_")]
    assert len(generic) == 5
    assert f"Grundpakets (Version {pack['version']})" in guide
    for metric in generic:
        assert f"| `{metric['key']}` | {metric['name']} |" in guide, metric["key"]
