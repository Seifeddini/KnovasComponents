"""Monitoring at MVP level: «Betrieb» › «Verbindung» (connection layer MVP-28, KC-7b).

One card per site: Knovas Link (state, version, certificate, work channel, last probe and its round trip), each
component, each source, the last 24 hours from GET /secured/connector/jobs/stats (a fake tenant client here), open
proposals. A site is green only when a probe succeeded within the last 10 minutes; what the Builder cannot know is
«unbekannt», never green. Alerts come through components.alerts, each with a German text and its fix; every code the
MVP emits has a title, an explanation and a fix in builder/codes.py.
"""
import datetime as dt
import re
from pathlib import Path

import pytest

from builder import codes, components as C, connection_api, editing, package
from builder_harness import (SAMPLE_PACKAGE, FakeEngine, MemorySecrets, MemoryWorkspaceStore, ProtocolClient, StandInUser,
                             make_app)

TENANT = "t-monitoring"
NOW = dt.datetime.now(dt.timezone.utc)
SRC = Path(__file__).resolve().parents[1] / "src"

LISTED = ("unsupported_type", "unsupported", "unreachable", "lease_lost", "source_unreachable", "source_refused",
          "no_source_ref", "not_found", "conflict", "mode_unsupported", "token_cannot_write", "fetch_disabled",
          "write_disabled", "not_writable", "computed_attribute", "value_not_allowed", "not_invertible",
          "unknown_mapping", "previously_rejected", "waiting_for_certificate", "not_relayed")


class TestTheCodes:
    def test_every_listed_code_has_a_title_an_explanation_and_a_fix(self):
        for code in LISTED:
            found = codes.CODES.get(code)
            assert found, code
            assert all(found.get(k, "").strip() for k in ("title", "explanation", "fix")), code

    def test_every_code_the_builder_emits_has_an_entry(self):
        literals = set()
        patterns = (r'_coded\([^,]+,\s*[^,]+,\s*"([a-z_]+)"', r'codes\.describe\("([a-z_]+)"\)',
                    r'_refused_write\([^,]+,\s*"([a-z_]+)"\)', r'"reason":\s*"([a-z_]+)"')
        for path in list((SRC / "builder").glob("*.py")) + [SRC / "web_interface" / "builder_routes.py"]:
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                literals.update(re.findall(pattern, text))
        missing = sorted(c for c in literals if c not in codes.CODES and c not in codes.NOT_INVERTIBLE)
        assert literals and missing == []

    def test_an_unknown_code_is_named_not_hidden(self):
        assert codes.describe("neu_seit_gestern")["title"] == "Unbekannter Code"


class FakeTenant:
    def __init__(self, stats=None, proposals=(), fail=False):
        self.stats, self.proposals, self.fail = stats or {}, list(proposals), fail
        self.asked = []

    def job_stats(self, since):
        self.asked.append(since)
        if self.fail:
            raise connection_api.ConnectionApiError(503, "")
        return self.stats

    def source_writes(self, status=None):
        return [p for p in self.proposals if status is None or p["status"] == status]


STATS = {"since": (NOW - dt.timedelta(hours=24)).isoformat(), "truncated": False,
         "counts": [{"source": "akten", "kind": "fetch", "outcome": "ok", "count": 41},
                    {"source": "akten", "kind": "fetch", "outcome": "unreachable", "count": 2},
                    {"source": "akten", "kind": "write", "outcome": "ok", "count": 1},
                    {"source": "fremd", "kind": "fetch", "outcome": "ok", "count": 99}],
         "duration_ms": {"fetch": {"count": 43, "median": 380, "p95": 1900}, "write": {"count": 1, "median": 2100, "p95": 2100}},
         "queue": {"depth": 0, "in_flight": 0, "oldest_queued_age_s": None},
         "probe": {"count": 288, "last_round_trip_ms": 410, "last_at": NOW.isoformat(), "median_round_trip_ms": 395},
         "stale_found": {"akten": 1, "fremd": 4}}


def _telemetry(probe_minutes=3, **changes):
    out = {"certificate": "present", "work_channel": "connected",
           "probe": {"last_at": (NOW - dt.timedelta(minutes=probe_minutes)).isoformat(), "ok": True, "round_trip_ms": 410},
           "components": [{"name": "sync-component", "kind": "sync_component", "version": "0.2.0", "state": "online",
                           "capabilities": ["dry_run", "apply", "fetch:github", "write:github"], "local_ping_ms": 3}],
           "jobs": {}}
    out.update(changes)
    return out


def _world(tenant):
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=MemorySecrets(),
                   secrets=MemorySecrets(), tenant_client=tenant)
    http = app.test_client()
    import io
    import zipfile

    made = http.post("/builder/api/sites", json={"name": "Kanzlei Genf", "options": ["github"]}).get_json()
    env = zipfile.ZipFile(io.BytesIO(http.post(f"/builder/api/sites/{made['id']}/package", json={}).data)) \
        .read("knovas-link.env").decode()
    link = ProtocolClient(http)
    link.enrol(next(line.split("=", 1)[1] for line in env.splitlines() if line.startswith("LINK_ENROL_TOKEN=")),
               version="1.1.0")
    link.heartbeat(site=_telemetry(), version="1.1.0")
    [child] = store.children(link.component)
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    model["ontology"]["types"].append({"key": "Merkblatt", "level": "company", "attributes": []})
    for op in ({"op": "source.create", "kind": "github", "name": "Akten", "component": child["id"],
                "settings": {"repository": "muster-partner/akten"}},
               {"op": "mapping.create", "id": "merkblaetter", "source": "akten",
                "collection": "markdown_front_matter:merkblaetter/*.md", "type": "Merkblatt"}):
        editing.apply(model, op)
    workspace = store.create(name="Muster & Partner", model=model, actor_id=admin.id, source={"kind": "blank"},
                             tenant=TENANT)
    return {"http": http, "store": store, "link": link, "child": child, "site": made, "workspace": workspace,
            "gate": app.extensions["builder_gate"], "tenant": tenant}


def _card(world):
    page = world["http"].get("/builder/betrieb/verbindung").get_data(as_text=True)
    assert f'data-site-card="{world["site"]["id"]}"' in page
    return page.split(f'data-site-card="{world["site"]["id"]}"', 1)[1]


def _proposal(pid, status, hours, **extra):
    return {"id": pid, "source": "akten", "status": status, "facts": {"satz": "x"},
            "created_at": (NOW - dt.timedelta(hours=hours)).isoformat(), **extra}


class TestTheCard:
    def test_everything_green(self):
        world = _world(FakeTenant(STATS, [_proposal("p1", "proposed", 2), _proposal("p2", "applied", 3),
                                          _proposal("p3", "proposed", 5)]))
        card = _card(world)
        assert 'data-site-state="ok"' in card and "Alles in Ordnung" in card
        assert "Knovas Link" in card and "online" in card and "1.1.0" in card
        assert "Arbeitskanal geprüft vor 3 Min." in card and "0,4 s" in card and "Zertifikat vorhanden" in card
        assert "0.2.0" in card and "lesen, Original, Vorschlag schreiben" in card
        assert "Originale 41 abgerufen · 2 nicht erreichbar" in card
        assert "Vorschläge 3 · 1 als Pull Request" in card
        assert re.search(r"veraltet gefunden 1\s", card) and "im ganzen Mandanten" not in card     # its own sources only
        assert "Akten" in card and "Alarme" in card and "keine" in card
        assert world["tenant"].asked and dt.datetime.fromisoformat(world["tenant"].asked[0]) < NOW - dt.timedelta(hours=23)

    def test_no_probe_for_ten_minutes_turns_it_red_and_says_what_to_do(self):
        world = _world(FakeTenant(STATS))
        world["link"].heartbeat(site=_telemetry(probe_minutes=12))
        card = _card(world)
        assert 'data-site-state="critical"' in card and "Alles in Ordnung" not in card
        assert "Knovas Link nicht erreichbar" in card and "docker compose ps" in card
        # The same alert on the Verbinden page.
        assert 'data-alert="link_unreachable"' in world["http"].get("/builder/verbinden").get_data(as_text=True)

    def test_stale_found_may_be_absent(self):
        world = _world(FakeTenant({k: v for k, v in STATS.items() if k != "stale_found"}))
        card = _card(world)
        assert "Originale 41 abgerufen" in card and "veraltet gefunden unbekannt" in card

    def test_the_stats_are_read_as_the_route_answers_them(self):
        from builder import connection

        assert connection.stat_counts(STATS)[("akten", "fetch", "ok")] == 41
        assert connection.stat_counts({"counts": [{"source": "akten", "kind": "fetch", "outcome": "ok", "n": 5}]}) == {}
        assert connection.stat_counts({"since": "x"}) is None
        day = connection.last_day(STATS, {"akten", "fremd"}, None, NOW)
        assert day["stale"] == 5 and day["fetched"] == 140
        assert connection.last_day({k: v for k, v in STATS.items() if k != "stale_found"}, {"akten"}, None, NOW)["stale"] is None

    def test_link_s_own_failures_have_german_texts(self):
        for code in ("invalid_job", "part_not_accepted"):
            assert all(codes.CODES[code][k].strip() for k in ("title", "explanation", "fix")), code

    def test_a_failed_probe_is_not_green(self):
        world = _world(FakeTenant(STATS))
        world["link"].heartbeat(site=_telemetry(probe={"last_at": NOW.isoformat(), "ok": False, "round_trip_ms": None}))
        assert 'data-site-state="ok"' not in _card(world)

    def test_what_is_not_known_is_unbekannt_never_green(self):
        world = _world(FakeTenant(fail=True))
        telemetry = _telemetry()
        del telemetry["work_channel"]
        telemetry["probe"]["round_trip_ms"] = None
        world["link"].heartbeat(site=telemetry)
        card = _card(world)
        assert 'data-site-state="ok"' not in card
        assert "Arbeitskanal unbekannt" in card and "Umlaufzeit unbekannt" in card
        assert "Letzte 24 h unbekannt" in card

    def test_waiting_for_the_certificate(self):
        world = _world(FakeTenant(STATS))
        world["link"].heartbeat(site=_telemetry(certificate="waiting"))
        card = _card(world)
        assert 'data-site-state="warning"' in card and "wartet auf das zertifikat" in card.lower()
        assert 'data-alert="waiting_for_certificate"' in card

    def test_alerts_of_the_mvp(self):
        world = _world(FakeTenant(STATS, [_proposal("p1", "proposed", 50),
                                          _proposal("p2", "conflict", 30, decided_at=NOW.isoformat()),
                                          _proposal("p3", "refused", 30, decided_at=NOW.isoformat())]))
        world["link"].heartbeat(site=_telemetry(
            work_channel="down",
            sources=[{"workspace": world["workspace"], "key": "akten", "last_run_status": "ok",
                      "last_read_ok_at": (NOW - dt.timedelta(days=10)).isoformat()}]))
        page = world["http"].get("/builder/betrieb/verbindung").get_data(as_text=True)
        for code in ("work_channel_down", "proposal_waiting", "write_conflict", "write_refused", "mapping_stale"):
            assert f'data-alert="{code}"' in page, code

    def test_a_source_check_failing_on_a_component_behind_link(self):
        world = _world(FakeTenant(STATS))
        link = world["link"]
        headers = {"Authorization": f"Bearer {link.token}", "X-Component": link.component,
                   "X-Client-Cert-Fingerprint": __import__("builder.protocol", fromlist=["x"]).certificate_fingerprint(link.certificate),
                   "X-Sub-Component": "sync-component"}
        world["http"].post("/api/connectors/v1/checks", headers=headers, json={"checks": [
            {"workspace": world["workspace"], "source": "akten", "code": "fetch.http_401", "counts": {}}]})
        assert 'data-alert="source_check_failed"' in world["http"].get("/builder/betrieb/verbindung").get_data(as_text=True)

    def test_it_refreshes_every_30_seconds(self):
        world = _world(FakeTenant(STATS))
        page = world["http"].get("/builder/betrieb/verbindung").get_data(as_text=True)
        assert 'data-refresh-seconds="30"' in page and "connection.js" in page
        fragment = world["http"].get("/builder/betrieb/verbindung?teil=karten").get_data(as_text=True)
        assert "data-site-card" in fragment and "<html" not in fragment

    def test_a_consultant_with_a_grant_does_not_see_it(self):
        world = _world(FakeTenant(STATS))
        world["gate"].user = StandInUser(frozenset(), "Bea Berater", "bea@extern.example")
        assert world["http"].get("/builder/betrieb/verbindung").status_code == 403
        world["gate"].user = StandInUser({"builder_viewer"})
        assert world["http"].get("/builder/betrieb/verbindung").status_code == 200


class TestTheLimits:
    def test_link_is_unreachable_after_ten_minutes_other_kinds_keep_thirty(self):
        link = {"id": "l", "name": "Knovas Link · X", "kind": "knovas_link", "transport": "v1", "state": "enrolled",
                "last_heartbeat_at": NOW.isoformat(), "enrolled_at": (NOW - dt.timedelta(hours=2)).isoformat(),
                "telemetry": {"certificate": "present", "work_channel": "connected",
                              "probe": {"last_at": (NOW - dt.timedelta(minutes=11)).isoformat(), "ok": True}}}
        [alert] = C.alerts([link], {}, {}, NOW, C.LIMITS)
        assert alert["code"] == "link_unreachable" and alert["text"].startswith("Knovas Link nicht erreichbar")
        assert alert["fix"] == codes.CODES["link_unreachable"]["fix"]
        fresh = {**link, "telemetry": {**link["telemetry"], "probe": {"last_at": NOW.isoformat(), "ok": True}}}
        assert C.alerts([fresh], {}, {}, NOW, C.LIMITS) == []
        # Just enrolled: ten minutes to make its first probe.
        new = {**link, "enrolled_at": (NOW - dt.timedelta(minutes=4)).isoformat(), "telemetry": {}}
        assert C.alerts([new], {}, {}, NOW, C.LIMITS) == []
        other = {"id": "s", "name": "Sync", "kind": "sync_component", "transport": "v1", "state": "enrolled",
                 "last_heartbeat_at": (NOW - dt.timedelta(minutes=20)).isoformat()}
        assert C.alerts([other], {}, {}, NOW, C.LIMITS) == []
        old = {**other, "last_heartbeat_at": (NOW - dt.timedelta(minutes=31)).isoformat()}
        assert [a["code"] for a in C.alerts([old], {}, {}, NOW, C.LIMITS)] == ["offline"]
