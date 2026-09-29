"""experiments.service against the real PostgreSQL.

The whole life of an experiment (create -> measurements -> evaluation ->
gated transitions -> decision -> snapshot), the permission wall (404 for
people without a viewing role, 403 for configuration), row_version and audit
rules, the evaluation pipeline with its digest reuse and retention, custom
evaluators through a fake runner, search with a fake Knovas, packs, tokens,
and one end-to-end pass through the real job worker.
"""

import dataclasses
import datetime as dt
import json

import pytest

from conftest import FakeIndexClient, _person, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN"
)

from experiments import service as service_mod, store, tasks  # noqa: E402
from experiments.errors import (  # noqa: E402
    Conflict, Forbidden, NotFound, Unavailable, ValidationError,
)
from experiments.jobs import JOB_KINDS, JobWorker, RetryLater  # noqa: E402
from experiments.service import ExperimentService  # noqa: E402
from experiments.settings import ExperimentsSettings  # noqa: E402

ZERO = "00000000-0000-0000-0000-000000000000"
SETTINGS = ExperimentsSettings(enabled=True, index_enabled=True, index_access_groups=("g-exp",),
                               index_debounce_seconds=60, worker_enabled=False)
META = {"ip": "10.1.2.3", "user_agent": "pytest", "token_id": None}
CUSTOM_CODE = "def evaluate(data):\n    return {'verdict': 'better', 'headline': 'Eigen'}\n"


class FakeRunner:
    """Stands in for runner_client.RunnerClient."""

    def __init__(self):
        self.healthy = True
        self.unavailable = False
        self.result = None
        self.calls = []

    def health(self):
        return {"configured": True, "ok": self.healthy, "languages": {"python": "3.11"}, "busy": 0}

    def run(self, *, language, code, data, timeout_seconds):
        self.calls.append({"language": language, "code": code, "data": data,
                           "timeout_seconds": timeout_seconds})
        if self.unavailable:
            raise Unavailable("Die Rechenumgebung ist nicht erreichbar.")
        if self.result is not None:
            return self.result
        return {"ok": True, "output": {"verdict": "better", "headline": "Eigen", "summary": "S",
                                       "extra": 3},
                "error": None, "logs": "rechne\n", "duration_ms": 42}


class World:
    def __init__(self, conn, repo):
        self.conn = conn
        self.repo = repo
        store.ensure_builtin_evaluators(conn)
        store.ensure_core_pack(conn)
        self.eva = _person(repo, "eva@knovas.ch", "Eva", "experimenter")
        self.ina = _person(repo, "ina@knovas.ch", "Ina", "experimenter")
        self.max = _person(repo, "max@knovas.ch", "Max", "experiments_manager")
        self.mia = _person(repo, "mia@kanzlei.ch", "Mia", "member")
        self.chef = _person(repo, "chef@kanzlei.ch", "Chef", "admin")
        self.runner = FakeRunner()
        self.manager = self.svc(self.max)
        self.manager.install_pack("marketing")

    def svc(self, actor, settings=SETTINGS, **kwargs):
        kwargs.setdefault("request_meta", dict(META))
        return ExperimentService(self.conn, actor, settings, **kwargs)

    @property
    def experimenter(self):
        return self.svc(self.eva, runner=self.runner)

    def create(self, **data):
        body = {"domain": "marketing", "type": "ab_test", "title": "LinkedIn Karussell",
                "hypothesis": "Karussell-Posts erh\u00f6hen die Klickrate."}
        body.update(data)
        return self.experimenter.create_experiment(body)

    def jobs(self, kind=None):
        sql = "SELECT kind, dedupe_key, payload, priority, status, " \
              "EXTRACT(EPOCH FROM run_after - now())::float8 FROM exp_jobs"
        rows = self.conn.execute(sql + " ORDER BY id").fetchall()
        keys = ("kind", "dedupe_key", "payload", "priority", "status", "due_in")
        return [dict(zip(keys, r)) for r in rows if kind is None or r[0] == kind]

    def audit(self, action=None):
        rows = self.conn.execute(
            "SELECT action, target_type, target_id, detail, host(ip), user_agent, actor_user_id::text "
            "FROM audit_log WHERE action LIKE 'experiments.%%' ORDER BY id").fetchall()
        keys = ("action", "target_type", "target_id", "detail", "ip", "user_agent", "actor")
        return [dict(zip(keys, r)) for r in rows if action is None or r[0] == action]

    def row(self, key):
        return self.conn.execute(
            "SELECT row_version, updated_at, status, started_at, ended_at, decided_at, index_state "
            "FROM exp_experiments WHERE key = %s", (key,)).fetchone()


@pytest.fixture
def w(platform_db, identity_repo):
    return World(platform_db, identity_repo)


def measurements(**counts):
    """ctr rows: counts as {variant: (successes, trials)}."""
    return {"rows": [{"metric": "ctr", "variant": v, "value": s, "count": n}
                     for v, (s, n) in counts.items()]}


# -- permissions --------------------------------------------------------------------------

CALLS = [
    ("list_domains", ()), ("create_domain", ({},)), ("update_domain", ("marketing", {})),
    ("export_domain", ("marketing",)), ("list_types", ()), ("get_type", (ZERO,)),
    ("validate_type", ({},)), ("create_type", ({},)), ("add_type_version", (ZERO, {})),
    ("set_type_archived", (ZERO, True)), ("list_metrics", ()), ("create_metric", ({},)),
    ("update_metric", (ZERO, {})), ("list_evaluators", ()), ("get_evaluator", (ZERO,)),
    ("create_evaluator", ({},)), ("add_evaluator_version", (ZERO, {})),
    ("test_evaluator", (ZERO, {})), ("sample_size", ({},)), ("list_experiments", ()),
    ("create_experiment", ({},)), ("get_experiment", ("MKT-1",)),
    ("update_experiment", ("MKT-1", {})), ("transition", ("MKT-1", {})),
    ("set_variants", ("MKT-1", {})), ("set_metrics", ("MKT-1", {})),
    ("add_measurements", ("MKT-1", {})), ("import_csv", ("MKT-1", b"", "x.csv")),
    ("list_batches", ("MKT-1",)), ("delete_batch", ("MKT-1", ZERO)), ("add_run", ("MKT-1", {})),
    ("list_runs", ("MKT-1",)), ("add_note", ("MKT-1", {})), ("delete_note", ("MKT-1", ZERO)),
    ("run_evaluation", ("MKT-1", {})), ("run_pipeline", ("MKT-1",)),
    ("get_evaluation", ("MKT-1", ZERO)), ("decide", ("MKT-1", {})),
    ("delete_experiment", ("MKT-1",)), ("reindex", ("MKT-1",)), ("activity", ("MKT-1",)),
    ("get_timeseries", ("MKT-1", "ctr")), ("search", ("karussell",)), ("list_tokens", ()),
    ("create_token", ({},)), ("revoke_token", (ZERO,)), ("get_preferences", ()),
    ("update_preferences", ({},)), ("get_settings", ()), ("update_settings", ({},)),
    ("index_status", ()), ("reindex_all", ()), ("list_packs", ()),
    ("install_pack", ("marketing",)), ("import_pack", ({},)),
]
MANAGE_ONLY = {"create_domain", "update_domain", "export_domain", "validate_type", "create_type",
               "add_type_version", "set_type_archived", "create_metric", "update_metric",
               "create_evaluator", "add_evaluator_version", "test_evaluator", "delete_experiment",
               "update_settings", "index_status", "reindex_all", "install_pack", "import_pack"}


def test_every_public_method_is_covered_by_the_permission_tests():
    public = {n for n in dir(ExperimentService) if not n.startswith("_")}
    assert public == {name for name, _ in CALLS}


def test_people_without_a_viewing_role_get_not_found_everywhere(w):
    w.create()
    audit_before = len(w.audit())
    for actor in (w.mia, None):
        svc = w.svc(actor)
        for name, args in CALLS:
            with pytest.raises(NotFound) as info:
                getattr(svc, name)(*args)
            assert info.value.message == "Nicht gefunden.", name
    assert len(w.audit()) == audit_before


def test_experimenters_cannot_configure(w):
    svc = w.experimenter
    for name, args in CALLS:
        if name not in MANAGE_ONLY:
            continue
        with pytest.raises(Forbidden) as info:
            getattr(svc, name)(*args)
        assert info.value.message == "Nur f\u00fcr Verantwortliche der Experimente.", name
    # An administrator may.
    assert w.svc(w.chef).index_status()["enabled"] is True


# -- the life of an experiment ----------------------------------------------------------------


def test_experiment_lifecycle_end_to_end(w):
    svc = w.experimenter
    exp = w.create(fields={"channel": "linkedin", "budget": "1'500"}, tags=["LinkedIn", " q3 ", "LinkedIn"])
    key = exp["key"]
    assert key == "MKT-1"
    assert exp["status"] == "draft" and exp["status_label"] == "Entwurf" and exp["row_version"] == 1
    assert exp["field_values"] == {"channel": "LinkedIn", "budget": 1500}
    assert exp["tags"] == ["LinkedIn", "q3"]
    assert [(v["key"], v["is_control"]) for v in exp["variants"]] == [("A", True), ("B", False)]
    assert [(m["key"], m["role"]) for m in exp["metrics"]] == [
        ("ctr", "primary"), ("conversion_rate", "secondary"), ("cost_per_click", "secondary"),
        ("bounce_rate", "guardrail")]
    assert exp["metrics"][3]["guardrail_op"] == "max" and exp["metrics"][3]["guardrail_value"] == 0.7
    assert exp["owner"]["display_name"] == "Eva"
    assert exp["index"]["state"] == "pending"
    index_jobs = w.jobs("index")
    assert len(index_jobs) == 1 and index_jobs[0]["dedupe_key"] == f"index:{exp['id']}"
    assert index_jobs[0]["priority"] == 10 and 50 < index_jobs[0]["due_in"] <= 60

    detail = svc.get_experiment(key)
    assert detail["definition"]["initial"] == "draft"
    transitions = {t["to"]: t for t in detail["transitions"]}
    assert transitions["running"] == {"to": "running", "label": "Starten", "allowed": True,
                                      "missing": [], "needs_comment": False, "decides": False}
    assert transitions["stopped"]["needs_comment"] is True
    assert {e["key"] for e in detail["evaluators"]} >= {"builtin.describe", "builtin.bayes_proportion",
                                                        "builtin.ratio_delta"}
    assert "builtin.welch_t" not in {e["key"] for e in detail["evaluators"]}
    json.dumps(detail, allow_nan=False)

    with pytest.raises(ValidationError) as info:
        svc.transition(key, {"to": "analysis"})
    assert info.value.message == "Dieser Statuswechsel ist nicht vorgesehen."
    exp = svc.transition(key, {"to": "running", "row_version": 1})
    assert exp["status"] == "running" and exp["started_at"] and exp["row_version"] == 2
    assert exp["status_phase"] == "running"
    with pytest.raises(ValidationError) as info:
        svc.transition(key, {"to": "analysis"})
    assert info.value.message == "Es gibt noch keine Messwerte."
    assert info.value.fields == {"to": "Es gibt noch keine Messwerte."}

    result = svc.add_measurements(key, measurements(A=(129, 10688), B=(175, 10714)))
    assert result["inserted"] == 2
    after = svc.get_experiment(key)
    assert after["row_version"] == 2  # child writes never bump row_version
    assert after["measurement_count"] == 2 and after["batch_count"] == 1
    pipeline = w.jobs("pipeline")
    assert len(pipeline) == 1 and 20 < pipeline[0]["due_in"] <= 30

    exp = svc.transition(key, {"to": "analysis"})
    assert exp["status"] == "analysis"
    evaluation = svc.run_evaluation(key, {"evaluator": "builtin.bayes_proportion", "metric": "ctr"})
    assert evaluation["status"] == "done" and evaluation["verdict"] == "better"
    assert evaluation["trigger"] == "manual" and evaluation["requested_by"] == {"display_name": "Eva"}

    detail = svc.get_experiment(key)
    decided = {t["to"]: t for t in detail["transitions"]}["decided"]
    assert decided["decides"] is True and decided["allowed"] is True
    with pytest.raises(ValidationError) as info:
        svc.transition(key, {"to": "decided"})
    assert info.value.message == "Bitte die Entscheidung \u00fcber das Formular festhalten."
    with pytest.raises(ValidationError) as info:
        svc.decide(key, {"verdict": "ship", "rationale": "klar besser"})
    assert info.value.fields == {"learning": "Bitte die Erkenntnis festhalten."}
    with pytest.raises(ValidationError):
        svc.decide(key, {"verdict": "maybe", "learning": "x"})
    with pytest.raises(Conflict):
        svc.decide(key, {"verdict": "ship", "learning": "x", "row_version": 1})

    exp = svc.decide(key, {"verdict": "ship", "rationale": "P(besser) 99,6 %",
                           "learning": "Karussells wirken bei Juristen."})
    assert exp["status"] == "decided" and exp["status_phase"] == "decided"
    assert exp["decided_at"] and exp["ended_at"]
    assert exp["decisions"][0]["verdict_label"] == "\u00dcbernehmen"
    assert exp["decisions"][0]["decided_by"]["display_name"] == "Eva"
    assert exp["row_version"] == 4
    with pytest.raises(ValidationError) as info:
        svc.decide(key, {"verdict": "stop", "learning": "x"})
    assert info.value.message == "Eine Entscheidung ist in diesem Status nicht vorgesehen."

    labels = [a["label"] for a in svc.activity(key)]
    assert labels == ["Entscheidung festgehalten", "Auswertung gestartet", "Status gewechselt",
                      "Messwerte erfasst", "Status gewechselt", "Experiment angelegt"]
    assert {a["actor"] for a in svc.activity(key)} == {"Eva"}


def test_transition_requirements_and_roles(w):
    svc = w.experimenter
    key = w.create(hypothesis="  ")["key"]
    missing = {t["to"]: t for t in svc.get_experiment(key)["transitions"]}["running"]
    assert missing["allowed"] is False and missing["missing"] == ["Die Hypothese fehlt."]
    with pytest.raises(ValidationError) as info:
        svc.transition(key, {"to": "running"})
    assert info.value.message == "Die Hypothese fehlt."
    snapshot = svc.get_experiment(key)
    svc.set_variants(key, {"variants": [{"key": "A", "is_control": True}, {"key": "B"}, {"key": "C"}]})
    svc.set_metrics(key, {"metrics": [{"metric": "conversion_rate", "role": "secondary"}]})
    with pytest.raises(ValidationError) as info:
        svc.transition(key, {"to": "running"})
    assert info.value.message == "Die Hypothese fehlt. Es ist keine prim\u00e4re Metrik festgelegt."
    assert snapshot["row_version"] == 1


def test_stopping_needs_a_reason_that_becomes_a_status_note(w):
    svc = w.experimenter
    key = w.create()["key"]
    with pytest.raises(ValidationError) as info:
        svc.transition(key, {"to": "stopped", "comment": "   "})
    assert info.value.fields == {"comment": "Bitte einen Grund f\u00fcr den Abbruch angeben."}
    exp = svc.transition(key, {"to": "stopped", "comment": "Budget gestrichen"})
    assert exp["status"] == "stopped" and exp["ended_at"] and exp["decided_at"] is None
    note = exp["notes"][0]
    assert note["kind"] == "status" and note["kind_label"] == "Statuswechsel"
    assert note["body"] == "\u00abEntwurf\u00bb \u2192 \u00abAbgebrochen\u00bb: Budget gestrichen"
    audit = w.audit("experiments.experiment.transition")[-1]
    assert audit["detail"]["from"] == "draft" and audit["detail"]["to"] == "stopped"
    assert "Budget" not in json.dumps(audit["detail"])


def test_reopening_clears_the_end_date(w):
    manager = w.manager
    definition = manager.get_type(store.find_type(w.conn, None, "hypothesis")["id"])["definition"]
    definition = dict(definition)
    definition["transitions"] = list(definition["transitions"]) + [
        {"from": "stopped", "to": "draft", "label": "Wieder aufnehmen", "requires": [], "roles": []}]
    type_ = manager.create_type({"domain": "marketing", "key": "reopenable", "name": "Wiederaufnahme",
                                 "definition": definition})
    svc = w.experimenter
    key = w.create(type=type_["id"])["key"]
    assert svc.transition(key, {"to": "stopped", "comment": "Pause"})["ended_at"]
    exp = svc.transition(key, {"to": "draft"})
    assert exp["status"] == "draft" and exp["ended_at"] is None


def test_transition_roles_are_enforced(w):
    manager = w.manager
    definition = dict(manager.get_type(store.find_type(w.conn, None, "hypothesis")["id"])["definition"])
    definition["transitions"] = [dict(t, roles=["experiments_manager"]) if t["to"] == "running" else t
                                 for t in definition["transitions"]]
    type_ = manager.create_type({"domain": "marketing", "key": "gated", "name": "Mit Freigabe",
                                 "definition": definition})
    key = w.create(type="gated")["key"]
    detail = w.experimenter.get_experiment(key)
    running = {t["to"]: t for t in detail["transitions"]}["running"]
    assert running["allowed"] is False and "Verantwortliche" in running["missing"][0]
    with pytest.raises(Forbidden):
        w.experimenter.transition(key, {"to": "running"})
    assert w.svc(w.max).transition(key, {"to": "running"})["status"] == "running"
    assert type_["versions"][0]["created_by"] == "Max"


def test_record_only_decisions_for_types_without_a_decided_phase(w):
    manager = w.manager
    definition = {
        "states": [{"key": "open", "label": "Offen"}, {"key": "closed", "label": "Zu"}],
        "transitions": [{"from": "open", "to": "closed", "label": "Schliessen"}],
    }
    manager.create_type({"domain": "marketing", "key": "log", "name": "Logbuch", "definition": definition})
    key = w.create(type="log")["key"]
    exp = w.experimenter.decide(key, {"verdict": "iterate", "learning": ""})
    assert exp["status"] == "open" and exp["decided_at"] is None
    assert exp["decisions"][0]["verdict"] == "iterate" and exp["row_version"] == 2


# -- row_version, updates, variants, metrics ------------------------------------------------------


def test_update_experiment_row_version_rules(w):
    svc = w.experimenter
    exp = w.create()
    key = exp["key"]
    with pytest.raises(ValidationError) as info:
        svc.update_experiment(key, {"title": "Neu"})
    assert "row_version" in info.value.fields
    updated = svc.update_experiment(key, {"row_version": 1, "title": "Neu", "fields": {"audience": "GL"},
                                          "tags": ["a"], "owner_id": str(w.ina.id), "archived": True})
    assert updated["title"] == "Neu" and updated["row_version"] == 2 and updated["archived"] is True
    assert updated["owner"]["display_name"] == "Ina"
    assert updated["field_values"] == {"audience": "GL"}
    with pytest.raises(Conflict) as info:
        svc.update_experiment(key, {"row_version": 1, "title": "Zu sp\u00e4t"})
    assert info.value.message == "Das Experiment wurde inzwischen ge\u00e4ndert. Bitte neu laden."
    # Partial field update: "" removes, others stay.
    updated = svc.update_experiment(key, {"row_version": 2, "fields": {"audience": "", "channel": "E-Mail"}})
    assert updated["field_values"] == {"channel": "E-Mail"}
    # Nothing changed: no new version, no audit entry.
    before = len(w.audit("experiments.experiment.update"))
    assert svc.update_experiment(key, {"row_version": 3, "title": "Neu"})["row_version"] == 3
    assert len(w.audit("experiments.experiment.update")) == before
    for bad in ({"row_version": 3, "title": ""}, {"row_version": 3, "title": "x" * 301},
                {"row_version": 3, "title": "a\nb"}, {"row_version": 3, "fields": {"nope": 1}},
                {"row_version": 3, "owner_id": ZERO}, {"row_version": 3, "archived": "ja"},
                {"row_version": 3, "tags": [f"t{i}" for i in range(21)]}, {"row_version": 3, "hypothesis": "\x00"},
                {"row_version": "drei", "title": "x"}):
        with pytest.raises(ValidationError):
            svc.update_experiment(key, bad)
    with pytest.raises(NotFound):
        svc.update_experiment("MKT-99", {"row_version": 1})


def test_child_writes_and_index_state_leave_row_version_alone(w):
    svc = w.experimenter
    key = w.create()["key"]
    rv, updated, *_ = w.row(key)
    svc.add_measurements(key, measurements(A=(1, 10)))
    svc.add_note(key, {"body": "Beobachtung"})
    svc.add_run(key, {"name": "r"})
    rv2, updated2, *_ = w.row(key)
    assert rv2 == rv and updated2 > updated
    store.set_index_state(w.conn, svc.get_experiment(key)["id"], "indexed")
    assert w.row(key)[:2] == (rv2, updated2)


def test_variants_are_kept_by_key_and_data_protects_them(w):
    svc = w.experimenter
    key = w.create()["key"]
    svc.add_measurements(key, measurements(B=(1, 10)))
    exp = svc.set_variants(key, {"variants": [
        {"key": "A", "name": "Alt"}, {"key": "B", "name": "Neu", "is_control": True, "allocation": 0.5},
        {"key": "C", "allocation": "0,5"}]})
    assert [(v["key"], v["name"], v["is_control"], v["allocation"]) for v in exp["variants"]] == [
        ("A", "Alt", False, None), ("B", "Neu", True, 0.5), ("C", "", False, 0.5)]
    assert exp["row_version"] == 2
    with pytest.raises(ValidationError) as info:
        svc.set_variants(key, {"variants": [{"key": "A"}, {"key": "C"}]})
    assert info.value.message == "Die Variante \u00abB\u00bb hat Messwerte und kann nicht entfernt werden."
    assert svc.set_variants(key, {"variants": [{"key": "A"}, {"key": "B"}]})["row_version"] == 3
    for bad in ([{"key": "A"}], [{"key": "A"}, {"key": "A"}], [{"key": "A", "is_control": True},
                {"key": "B", "is_control": True}], [{"key": "A"}, {"key": "-x"}],
                [{"key": "A", "allocation": 0.7}, {"key": "B", "allocation": 0.7}],
                [{"key": "A"}] + [{"key": f"V{i}"} for i in range(10)], "A,B"):
        with pytest.raises(ValidationError):
            svc.set_variants(key, {"variants": bad})
    with pytest.raises(Conflict):
        svc.set_variants(key, {"variants": [{"key": "A"}, {"key": "B"}], "row_version": 1})


def test_metrics_assignment_rules(w):
    svc = w.experimenter
    key = w.create()["key"]
    svc.add_measurements(key, measurements(A=(1, 10)))
    exp = svc.set_metrics(key, {"metrics": [
        {"metric": "ctr", "role": "primary"},
        {"metric": "cost_per_lead", "role": "guardrail", "guardrail_op": "max", "guardrail_value": "80"}]})
    assert [(m["key"], m["role"], m["guardrail_value"]) for m in exp["metrics"]] == [
        ("ctr", "primary", None), ("cost_per_lead", "guardrail", 80.0)]
    with pytest.raises(ValidationError) as info:
        svc.set_metrics(key, {"metrics": [{"metric": "conversion_rate", "role": "primary"}]})
    assert info.value.message == "Die Metrik \u00abKlickrate\u00bb hat Messwerte und kann nicht entfernt werden."
    for bad in (
        [{"metric": "ctr", "role": "primary"}, {"metric": "bounce_rate", "role": "primary"}],
        [{"metric": "ctr", "role": "primary"}, {"metric": "bounce_rate", "role": "guardrail"}],
        [{"metric": "ctr", "role": "primary"},
         {"metric": "bounce_rate", "role": "guardrail", "guardrail_op": "max", "guardrail_value": 70}],
        [{"metric": "ctr", "role": "primary"}, {"metric": "nope", "role": "secondary"}],
        [{"metric": "ctr", "role": "boss"}], [{"metric": "ctr", "role": "primary"}] * 2,
        [{"metric": "ctr", "role": "primary"},
         {"metric": "bounce_rate", "role": "guardrail", "guardrail_op": "max", "guardrail_value": "NaN"}],
    ):
        with pytest.raises(ValidationError):
            svc.set_metrics(key, {"metrics": bad})
    metric_id = next(m["id"] for m in w.manager.list_metrics() if m["key"] == "conversion_rate")
    w.manager.update_metric(metric_id, {"archived": True})
    with pytest.raises(ValidationError) as info:
        svc.set_metrics(key, {"metrics": [{"metric": "ctr", "role": "primary"},
                                          {"metric": "conversion_rate", "role": "secondary"}]})
    assert "archiviert" in info.value.message


# -- measurements, CSV, batches, runs, notes ----------------------------------------------------


def test_measurements_are_all_or_nothing_with_row_errors(w):
    svc = w.experimenter
    key = w.create()["key"]
    other = w.create()
    foreign_run = svc.add_run(other["key"], {"name": "fremd"})["id"]
    with pytest.raises(ValidationError) as info:
        svc.add_measurements(key, {"rows": [
            {"metric": "ctr", "variant": "A", "value": 1, "count": 10},
            {"metric": "ctr", "variant": "A", "value": 11, "count": 10},
            {"metric": "demo_request_rate", "value": 1},
            {"metric": "ctr", "variant": "Z", "value": 1},
            {"metric": "ctr", "value": 1, "run_id": foreign_run},
            {"metric": "ctr", "value": 1, "colour": "red"},
            {"metric": "ctr", "value": float("nan")},
            {"metric": "cost_per_click", "value": 1},
            {"metric": "ctr", "value": 1, "observed_at": "gestern"},
            {"metric": "ctr", "value": 1, "dims": {"a b": 1}},
            {"metric": "ctr", "value": 1, "count": 2, "observed_at": "2026-09-01T10:00:00+02:00",
             "dims": {"query": 17}},
        ]})
    message = info.value.message
    assert message.startswith("Messwert 2: Es kann nicht mehr Erfolge als Versuche geben.")
    for fragment in ("Messwert 3: Die Metrik \u00abdemo_request_rate\u00bb ist diesem Experiment nicht zugeordnet.",
                     "Messwert 4: Unbekannte Variante \u00abZ\u00bb.",
                     "Messwert 5: Diesen Lauf gibt es in diesem Experiment nicht.",
                     "Messwert 6: Unbekannte Angabe \u00abcolour\u00bb.",
                     "Messwert 7: Der Wert muss eine endliche Zahl sein.",
                     "Messwert 8: Der Nenner fehlt.",
                     "Messwert 9: Kein g\u00fcltiger Zeitpunkt"):
        assert fragment in message
    assert "Messwert 11" not in message
    assert info.value.fields["rows.1.value"] == "Es kann nicht mehr Erfolge als Versuche geben."
    assert svc.get_experiment(key)["measurement_count"] == 0
    assert w.conn.execute("SELECT count(*) FROM exp_measurements").fetchone()[0] == 0

    stored = svc.add_measurements(key, {"rows": [
        {"metric": "ctr", "value": 1, "count": 2, "observed_at": "2026-09-01T10:00:00+02:00",
         "dims": {"query": 17}}]}, source="api")
    row = w.conn.execute("SELECT observed_at, dims, source, variant_id FROM exp_measurements").fetchone()
    assert row[0] == dt.datetime(2026, 9, 1, 8, tzinfo=dt.timezone.utc)
    assert row[1] == {"query": "17"} and row[2] == "api" and row[3] is None
    assert stored["inserted"] == 1
    for bad in ({}, {"rows": []}, {"rows": "x"}, None):
        with pytest.raises(ValidationError):
            svc.add_measurements(key, bad)
    small = dataclasses.replace(SETTINGS, max_rows_per_request=2)
    with pytest.raises(ValidationError) as info:
        w.svc(w.eva, settings=small).add_measurements(key, {"rows": [{"metric": "ctr", "value": 0}] * 3})
    assert "H\u00f6chstens 2 Messwerte" in info.value.message
    with pytest.raises(ValueError):
        svc.add_measurements(key, measurements(A=(1, 2)), source="ftp")


def test_csv_import_and_undo(w):
    svc = w.experimenter
    key = w.create()["key"]
    content = ("variant;observed_at;ctr;ctr.count;cost_per_click;cost_per_click.denominator;Kampagne\n"
               "A;01.09.2026;11;900;12,5;4;Herbst\n"
               "B;01.09.2026;15;910;;;Herbst\n").encode("utf-8")
    result = svc.import_csv(key, content, "C:\\Users\\eva\\linkedin\x07.csv")
    assert result["inserted"] == 3 and result["ignored_columns"] == ["Kampagne"]
    batches = svc.list_batches(key)
    assert batches["next_after"] is None
    batch = batches["items"][0]
    assert batch["source"] == "csv" and batch["filename"] == "linkedin.csv" and batch["rows"] == 3
    assert batch["metric_keys"] == ["ctr", "cost_per_click"]
    audit = w.audit("experiments.measurements.import")[-1]
    assert audit["target_id"] == key and audit["detail"]["rows"] == 3 and "Herbst" not in json.dumps(audit)

    with pytest.raises(ValidationError) as info:
        svc.import_csv(key, b"metric,variant,value\nctr,C,1\n", "x.csv")
    assert info.value.message == "Zeile 2: Unbekannte Variante \u00abC\u00bb."
    small = dataclasses.replace(SETTINGS, max_csv_rows=1)
    with pytest.raises(ValidationError) as info:
        w.svc(w.eva, settings=small).import_csv(key, content, "x.csv")
    assert info.value.message == "Die Datei hat mehr als 1 Zeilen; bitte aufteilen."

    run_id = svc.add_run(key, {"name": "r"})["id"]
    ok = svc.import_csv(key, f"metric,variant,value,count,run\nctr,A,1,5,{run_id}\n".encode(), "r.csv")
    assert ok["inserted"] == 1
    with pytest.raises(ValidationError) as info:
        svc.import_csv(key, f"metric,value,run\nctr,1,{ZERO}\n".encode(), "r.csv")
    assert info.value.message == "Zeile 2: Diesen Lauf gibt es in diesem Experiment nicht."

    deleted = svc.delete_batch(key, batch["batch_id"])
    assert deleted == {"deleted": 3}
    assert svc.get_experiment(key)["measurement_count"] == 1
    for bad in (batch["batch_id"], "kaputt", ZERO):
        with pytest.raises(NotFound):
            svc.delete_batch(key, bad)
    assert w.audit("experiments.batch.delete")[-1]["detail"] == {"batch_id": batch["batch_id"], "rows": 3}


def test_batches_page(w):
    svc = w.experimenter
    key = w.create()["key"]
    for i in range(3):
        svc.add_measurements(key, measurements(A=(i, 10)))
    page = svc.list_batches(key, limit=2)
    assert len(page["items"]) == 2 and page["next_after"]
    rest = svc.list_batches(key, after=page["next_after"], limit=2)
    assert len(rest["items"]) == 1 and rest["next_after"] is None
    with pytest.raises(ValidationError):
        svc.list_batches(key, after="%%%")


def test_runs_log_metrics_rows_and_a_note_in_one_go(w):
    svc = w.experimenter
    key = w.create()["key"]
    run = svc.add_run(key, {
        "name": "CI #812", "variant": "B", "status": "finished", "commit": "abc123",
        "params": {"k": 20}, "environment": {"python": "3.11"},
        "started_at": "2026-09-01T10:00:00Z", "ended_at": "2026-09-01T10:05:00Z",
        "metrics": {"ctr": {"value": 3, "count": 30}, "cost_per_click": {"value": 10, "denominator": 5}},
        "rows": [{"metric": "ctr", "value": 2, "count": 20, "dims": {"query": "q1"}},
                 {"metric": "ctr", "variant": "A", "value": 1, "count": 20}],
        "note": "Index neu gebaut",
    }, source="api")
    assert run["name"] == "CI #812" and run["variant"] == "B" and run["source"] == "api"
    assert run["commit"] == "abc123" and run["params"] == {"k": 20}
    assert run["started_at"] == "2026-09-01T10:00:00+00:00"
    assert run["metrics"] == {"cost_per_click": 2.0, "ctr": pytest.approx(6 / 70)}
    exp = svc.get_experiment(key)
    assert exp["runs"][0]["id"] == run["id"] and exp["run_count"] == 1
    assert exp["notes"][0]["run_id"] == run["id"] and exp["notes"][0]["body"] == "Index neu gebaut"
    ctr = next(m for m in exp["metrics"] if m["key"] == "ctr")
    assert {a["variant"]: a["n"] for a in ctr["aggregates"]} == {"A": 20, "B": 50}
    batch = svc.list_batches(key)["items"][0]
    assert batch["source"] == "api" and batch["rows"] == 4
    assert len(w.jobs("pipeline")) == 1

    bare = svc.add_run(key, {})
    assert bare["status"] == "finished" and bare["metrics"] == {}
    manager = w.manager
    manager.create_metric({"domain": "marketing", "key": "latency_ms", "name": "Latenz",
                           "kind": "duration", "unit": "ms", "direction": "lower"})
    metrics = [{"metric": m["key"], "role": m["role"]} for m in exp["metrics"] if m["role"] != "guardrail"]
    svc.set_metrics(key, {"metrics": metrics + [{"metric": "latency_ms", "role": "secondary"}]})
    timed = svc.add_run(key, {"metrics": {"latency_ms": 120.5}})
    assert timed["metrics"] == {"latency_ms": 120.5}
    for bad, fragment in (
        ({"metrics": {"ctr": 0.5}}, "braucht value und count"),
        ({"rows": [{"metric": "ctr", "value": 1, "run_id": run["id"]}]}, "keine eigene run_id"),
        ({"status": "running"}, "finished"),
        ({"variant": "Z"}, "Unbekannte Variante"),
        ({"params": {"x": "y" * 17000}}, "16 KB"),
        ({"params": [1]}, "JSON-Objekt"),
        ({"environment": {"x": float("inf")}}, "endliche"),
        ({"started_at": "2026-09-02", "ended_at": "2026-09-01"}, "vor dem Beginn"),
        ({"metrics": {"ctr": {"value": 1, "count": 2, "variant": "A"}}}, "Unbekannte Angabe"),
        ({"name": "x" * 201}, "200"),
    ):
        with pytest.raises(ValidationError) as info:
            svc.add_run(key, bad)
        assert fragment in (info.value.message + json.dumps(info.value.fields)), bad
    assert svc.get_experiment(key)["run_count"] == 3
    page = svc.list_runs(key, limit=2)
    assert len(page["items"]) == 2 and page["next_after"]


def test_notes_can_be_deleted_by_their_author_and_managers_only(w):
    svc = w.experimenter
    key = w.create()["key"]
    note = svc.add_note(key, {"body": "  Interview mit der GL  ", "kind": "interview", "variant": "B"})
    assert note["body"] == "Interview mit der GL" and note["kind_label"] == "Interview"
    assert note["variant"] == "B" and note["can_delete"] is True
    assert set(note) == {"id", "kind", "kind_label", "body", "variant", "run_id", "created_by",
                         "created_at", "can_delete"}
    with pytest.raises(Forbidden):
        w.svc(w.ina).delete_note(key, note["id"])
    assert w.svc(w.max).delete_note(key, note["id"]) == {"deleted": note["id"]}
    mine = svc.add_note(key, {"body": "x"})
    assert svc.delete_note(key, mine["id"])["deleted"] == mine["id"]
    for bad in ({"body": ""}, {"body": "x", "kind": "status"}, {"body": "x", "variant": "Z"},
                {"body": "x", "run_id": ZERO}, {"body": "x" * 50001}, {"body": 5}):
        with pytest.raises(ValidationError):
            svc.add_note(key, bad)
    with pytest.raises(NotFound):
        svc.delete_note(key, mine["id"])
    audit = w.audit("experiments.note.add")
    assert all("Interview" not in json.dumps(a["detail"]) for a in audit)


# -- evaluations ----------------------------------------------------------------------------------


def make_custom_evaluator(w, **extra):
    data = {"key": "team.custom_py", "name": "Eigener Test", "language": "python",
            "description": "Vorlage", "code": CUSTOM_CODE, "input_kinds": ["proportion"],
            "params_schema": {"type": "object", "properties": {"alpha": {"type": "number"}},
                              "additionalProperties": False}}
    data.update(extra)
    return w.manager.create_evaluator(data)


def test_builtin_evaluation_input_errors(w):
    svc = w.experimenter
    key = w.create()["key"]
    for bad, field in (({"evaluator": "builtin.welch_t", "metric": "ctr"}, "metric"),
                       ({"evaluator": "builtin.nope", "metric": "ctr"}, "evaluator"),
                       ({"evaluator": "builtin.describe", "metric": "demo_request_rate"}, "metric"),
                       ({"evaluator": "builtin.two_proportion", "metric": "ctr",
                         "params": {"alpha": 0.9}}, "params.alpha"),
                       ({"evaluator": "builtin.describe", "metric": "ctr",
                         "scope": {"runs": "all"}}, "runs")):
        with pytest.raises(ValidationError) as info:
            svc.run_evaluation(key, bad)
        assert field in json.dumps(info.value.fields) or field in info.value.message
    evaluation = svc.run_evaluation(key, {"evaluator": "builtin.describe", "metric": "ctr",
                                          "params": {"target": 0.1}, "scope": {"since": "01.09.2026"}},
                                    trigger="api")
    assert evaluation["params"] == {"target": 0.1}
    assert evaluation["scope"] == {"since": "2026-09-01T00:00:00+00:00"}
    assert evaluation["trigger"] == "api" and evaluation["metric_key"] == "ctr"
    fetched = svc.get_evaluation(key, evaluation["id"])
    assert fetched["logs"] is None and fetched["output"]["headline"]
    other = w.create()["key"]
    with pytest.raises(NotFound):
        svc.get_evaluation(other, evaluation["id"])
    with pytest.raises(ValueError):
        svc.run_evaluation(key, {"evaluator": "builtin.describe", "metric": "ctr"}, trigger="cron")


def test_custom_evaluation_needs_a_reachable_runner(w):
    make_custom_evaluator(w)
    key = w.create()["key"]
    request = {"evaluator": "team.custom_py", "metric": "ctr", "params": {"alpha": 0.1}}
    with pytest.raises(Unavailable) as info:
        w.svc(w.eva).run_evaluation(key, request)
    assert info.value.message == "Die Rechenumgebung ist nicht eingerichtet."
    w.runner.healthy = False
    with pytest.raises(Unavailable) as info:
        w.experimenter.run_evaluation(key, request)
    assert info.value.message == "Die Rechenumgebung ist nicht erreichbar."
    assert w.conn.execute("SELECT count(*) FROM exp_evaluations").fetchone()[0] == 0
    w.runner.healthy = True
    queued = w.experimenter.run_evaluation(key, request)
    assert queued["status"] == "queued" and queued["language"] == "python"
    job = w.jobs("evaluate")[0]
    assert job["dedupe_key"] == f"evaluate:{queued['id']}" and job["payload"] == {"evaluation_id": queued["id"]}


def test_execute_evaluation_outcomes(w, monkeypatch):
    make_custom_evaluator(w)
    svc = w.experimenter
    key = w.create()["key"]
    svc.add_measurements(key, measurements(A=(3, 30), B=(5, 30)))

    def queue():
        return svc.run_evaluation(key, {"evaluator": "team.custom_py", "metric": "ctr"})["id"]

    ok = queue()
    service_mod.execute_evaluation(w.conn, ok, settings=SETTINGS, runner=w.runner)
    call = w.runner.calls[-1]
    assert call["language"] == "python" and call["code"] == CUSTOM_CODE and call["timeout_seconds"] == 90
    assert call["data"]["experiment"]["key"] == key and len(call["data"]["rows"]) == 2
    assert {a["variant"] for a in call["data"]["aggregates"]} == {"A", "B"}
    done = svc.get_evaluation(key, ok)
    assert done["status"] == "done" and done["verdict"] == "better" and done["logs"] == "rechne\n"
    assert done["output"]["values"] == {"extra": 3} and done["duration_ms"] == 42
    assert "logs" not in svc.get_experiment(key)["evaluations"][0]
    service_mod.execute_evaluation(w.conn, ok, settings=SETTINGS, runner=w.runner)  # done: no rerun
    assert len(w.runner.calls) == 1

    failed = queue()
    w.runner.result = {"ok": False, "output": None, "error": "Der Auswerter ist mit einem Fehler abgebrochen.",
                       "logs": "Traceback", "duration_ms": 5}
    service_mod.execute_evaluation(w.conn, failed, settings=SETTINGS, runner=w.runner)
    record = svc.get_evaluation(key, failed)
    assert record["status"] == "failed" and record["error"].startswith("Der Auswerter")
    assert record["logs"] == "Traceback"

    not_an_object = queue()
    w.runner.result = {"ok": True, "output": [1, 2], "error": None, "logs": "", "duration_ms": 1}
    service_mod.execute_evaluation(w.conn, not_an_object, settings=SETTINGS, runner=w.runner)
    assert svc.get_evaluation(key, not_an_object)["error"] == "Der Auswerter hat kein Objekt zur\u00fcckgegeben."

    later = queue()
    w.runner.unavailable = True
    with pytest.raises(RetryLater) as info:
        service_mod.execute_evaluation(w.conn, later, settings=SETTINGS, runner=w.runner)
    assert info.value.delay_seconds == 60
    assert svc.get_evaluation(key, later)["status"] == "queued"
    w.conn.execute("UPDATE exp_evaluations SET created_at = now() - interval '31 minutes' WHERE id = %s",
                   (later,))
    service_mod.execute_evaluation(w.conn, later, settings=SETTINGS, runner=w.runner)
    assert svc.get_evaluation(key, later)["error"] == "Die Rechenumgebung war 30 Minuten nicht erreichbar."

    no_runner = queue()
    service_mod.execute_evaluation(w.conn, no_runner, settings=SETTINGS, runner=None)
    assert svc.get_evaluation(key, no_runner)["error"] == "Die Rechenumgebung ist nicht eingerichtet."

    dead = queue()
    service_mod.on_evaluation_dead(w.conn, dead)
    assert svc.get_evaluation(key, dead)["error"] == "Die Auswertung konnte nicht ausgef\u00fchrt werden."
    service_mod.on_evaluation_dead(w.conn, ok)
    assert svc.get_evaluation(key, ok)["status"] == "done"
    service_mod.execute_evaluation(w.conn, ZERO, settings=SETTINGS, runner=w.runner)
    service_mod.on_evaluation_dead(w.conn, "kaputt")


def test_pipeline_reuses_unchanged_input_and_skips_custom_without_runner(w):
    svc = w.svc(w.eva)
    key = w.create()["key"]
    svc.add_measurements(key, measurements(A=(129, 10688), B=(175, 10714)))
    first = svc.run_pipeline(key)
    ids = [e["id"] for e in first]
    assert first[0]["evaluator_key"] == "builtin.describe"
    assert [e["evaluator_key"] for e in first].count("builtin.describe") == 4
    assert {e["status"] for e in first} == {"done"}
    assert svc.run_pipeline(key) == first  # nothing changed: the same evaluations come back
    assert w.conn.execute("SELECT count(*) FROM exp_evaluations").fetchone()[0] == len(ids)
    svc.add_measurements(key, measurements(B=(1, 10)))
    second = svc.run_pipeline(key)
    by_pair = {(e["evaluator_key"], e["metric_key"]): e["id"] for e in second}
    assert by_pair[("builtin.bayes_proportion", "ctr")] not in ids
    # Describe on metrics without new rows is unchanged and reused.
    assert by_pair[("builtin.describe", "conversion_rate")] in ids
    scoped = svc.run_pipeline(key, {"scope": {"since": "2030-01-01"}})
    assert all(e["scope"] == {"since": "2030-01-01T00:00:00+00:00"} for e in scoped)

    make_custom_evaluator(w)
    manager = w.manager
    type_ = store.find_type(w.conn, None, "hypothesis")
    definition = dict(manager.get_type(type_["id"])["definition"])
    definition["evaluation"] = [{"evaluator": "team.custom_py", "metric": "primary", "params": {},
                                 "scope": {}}, {"evaluator": "builtin.describe", "metric": "all",
                                                "params": {}, "scope": {}}]
    manager.create_type({"domain": "marketing", "key": "custom", "name": "Eigen", "definition": definition})
    key2 = w.create(type="custom")["key"]
    svc.set_metrics(key2, {"metrics": [{"metric": "ctr", "role": "primary"}]})
    result = svc.run_pipeline(key2)
    assert result[0]["evaluator_key"] == "builtin.describe"
    assert result[1] == {"evaluator_key": "team.custom_py", "metric_key": "ctr", "status": "skipped",
                         "warning": "Die Rechenumgebung ist nicht eingerichtet."}
    queued = w.svc(w.eva, runner=w.runner).run_pipeline(key2)
    custom = next(e for e in queued if e["evaluator_key"] == "team.custom_py")
    assert custom["status"] == "queued"
    again = w.svc(w.eva, runner=w.runner).run_pipeline(key2)
    assert next(e for e in again if e["evaluator_key"] == "team.custom_py")["id"] == custom["id"]
    assert len(w.jobs("evaluate")) == 1


def test_pipeline_job_runs_without_a_person_and_keeps_ten(w):
    svc = w.experimenter
    manager = w.manager
    manager.create_type({"domain": "marketing", "key": "simple", "name": "Einfach",
                         "copy_from": store.find_type(w.conn, None, "hypothesis")["id"]})
    exp = w.create(type="simple")
    key = exp["key"]
    svc.set_metrics(key, {"metrics": [{"metric": "ctr", "role": "primary"}]})
    svc.set_variants(key, {"variants": [{"key": "A", "is_control": True}]})
    for i in range(12):
        svc.add_measurements(key, measurements(A=(i, 100)))
        service_mod.run_pipeline_job(w.conn, exp["id"], settings=SETTINGS, runner=None)
    rows = w.conn.execute("SELECT trigger, requested_by FROM exp_evaluations").fetchall()
    assert len(rows) == 10 and {r[0] for r in rows} == {"pipeline"} and {r[1] for r in rows} == {None}
    runs = w.audit("experiments.pipeline.run")
    assert len(runs) == 12 and runs[-1]["actor"] is None and runs[-1]["detail"]["trigger"] == "pipeline"
    assert svc.activity(key)[0]["actor"] == "System"
    service_mod.run_pipeline_job(w.conn, exp["id"], settings=SETTINGS, runner=None)  # unchanged: reused
    assert len(w.audit("experiments.pipeline.run")) == 12
    service_mod.run_pipeline_job(w.conn, ZERO, settings=SETTINGS, runner=None)


def test_offline_eval_pairs_the_latest_ci_runs(w):
    w.manager.install_pack("engineering")
    svc = w.experimenter
    exp = svc.create_experiment({"domain": "engineering", "type": "offline_eval",
                                 "title": "Neuer Reranker", "hypothesis": "Besseres NDCG"})
    key = exp["key"]
    assert key == "ENG-1"
    for variant, scores in (("baseline", [0.40, 0.50, 0.60]), ("candidate", [0.30, 0.30, 0.30]),
                            ("baseline", [0.50, 0.55, 0.60]), ("candidate", [0.55, 0.60, 0.70])):
        svc.add_run(key, {"variant": variant, "rows": [
            {"metric": "ndcg_at_10", "value": v, "dims": {"query": f"q{i}"}} for i, v in enumerate(scores)]},
            source="api")
    results = svc.run_pipeline(key, trigger="api")
    paired = next(e for e in results if e["evaluator_key"] == "builtin.paired_t" and e["metric_key"] == "ndcg_at_10")
    assert paired["scope"] == {"runs": "latest"} and paired["params"] == {"pair_by": "query"}
    output = svc.get_evaluation(key, paired["id"])["output"]
    # Only the newest run of each variant is paired: +0.05, +0.05, +0.10.
    assert output["comparisons"][0]["estimate"] == pytest.approx(0.2 / 3)
    assert paired["trigger"] == "api"


# -- deletion, index, audit -----------------------------------------------------------------------


def test_delete_experiment_queues_unindex_and_drops_pending_work(w):
    svc = w.experimenter
    exp = w.create()
    svc.add_measurements(exp["key"], measurements(A=(1, 10)))
    other = w.create()
    assert {j["kind"] for j in w.jobs()} == {"index", "pipeline"}
    result = w.manager.delete_experiment(exp["key"])
    assert result == {"deleted": exp["key"]}
    jobs = w.jobs()
    unindex = [j for j in jobs if j["kind"] == "unindex"]
    assert len(unindex) == 1
    assert unindex[0]["payload"] == {"pointer": "experiments/marketing/MKT-1", "experiment_id": exp["id"]}
    assert unindex[0]["priority"] == 10 and 320 < unindex[0]["due_in"] <= 330
    assert [j["dedupe_key"] for j in jobs if j["kind"] == "index"] == [f"index:{other['id']}"]
    assert not [j for j in jobs if j["kind"] == "pipeline"]
    with pytest.raises(NotFound):
        svc.get_experiment(exp["key"])
    assert w.audit("experiments.experiment.delete")[-1]["target_id"] == exp["key"]

    off = dataclasses.replace(SETTINGS, index_enabled=False)
    quiet = w.svc(w.eva, settings=off).create_experiment(
        {"domain": "marketing", "type": "ab_test", "title": "Ohne Index"})
    assert quiet["index"]["state"] == "off"
    w.svc(w.max, settings=off).delete_experiment(quiet["key"])
    assert len([j for j in w.jobs() if j["kind"] == "unindex"]) == 1


def test_reindex_and_reindex_all(w):
    svc = w.experimenter
    exp = w.create()
    w.create()
    store.set_index_state(w.conn, exp["id"], "error", "Fehler")
    assert svc.reindex(exp["key"]) == {"queued": True}
    job = next(j for j in w.jobs("index") if j["dedupe_key"] == f"index:{exp['id']}")
    assert job["due_in"] <= 0.5 and job["priority"] == 10  # pulled forward
    assert svc.get_experiment(exp["key"])["index"]["state"] == "pending"
    off = dataclasses.replace(SETTINGS, index_enabled=False)
    assert w.svc(w.eva, settings=off).reindex(exp["key"]) == {"queued": False}
    assert svc.get_experiment(exp["key"])["index"]["state"] == "off"
    assert w.manager.reindex_all() == {"queued": 2}
    assert w.svc(w.max, settings=off).reindex_all() == {"queued": 0}


def test_audit_entries_hold_ids_and_counts_but_no_bodies(w):
    svc = w.svc(w.eva, request_meta={"ip": "not an ip", "user_agent": "sdk\x00/1", "token_id": "tok-1"})
    already = len(w.audit())
    exp = svc.create_experiment({"domain": "marketing", "type": "ab_test", "title": "Geheimer Titel",
                                 "hypothesis": "Geheime Hypothese"})
    svc.add_note(exp["key"], {"body": "Geheime Notiz"})
    svc.transition(exp["key"], {"to": "stopped", "comment": "Geheimer Grund"})
    token = svc.create_token({"name": "CI"})
    entries = w.audit()[already:]
    assert [e["action"] for e in entries] == [
        "experiments.experiment.create", "experiments.note.add", "experiments.experiment.transition",
        "experiments.token.create"]
    text = json.dumps(entries, default=str)
    for secret in ("Geheim", token["token"], "hypothesis"):
        assert secret not in text
    experiment_entries = [e for e in entries if e["target_type"] == "experiment"]
    assert {e["target_id"] for e in experiment_entries} == {exp["key"]}
    assert all(e["detail"]["token_id"] == "tok-1" for e in entries)
    assert all(e["ip"] is None and e["user_agent"] == "sdk/1" for e in entries)
    assert all(e["actor"] == str(w.eva.id) for e in entries)
    good = w.audit("experiments.experiment.create")
    w.create()
    assert w.audit("experiments.experiment.create")[-1]["ip"] == "10.1.2.3"
    assert good


# -- tokens, preferences, settings ------------------------------------------------------------------


def test_tokens_are_personal_and_shown_once(w):
    svc = w.experimenter
    token = svc.create_token({"name": "GitHub Actions", "expires_days": "30"})
    plaintext = token["token"]
    assert plaintext.startswith("kxp_") and len(plaintext) > 40
    assert token["hint"] == "kxp_\u2026" + plaintext[-4:]
    expires = dt.datetime.fromisoformat(token["expires_at"]) - dt.datetime.fromisoformat(token["created_at"])
    assert expires == dt.timedelta(days=30)
    listed = svc.list_tokens()
    assert [t["id"] for t in listed] == [token["id"]] and "token" not in listed[0]
    assert store.resolve_api_token(w.conn, plaintext)["user_id"] == str(w.eva.id)
    assert w.svc(w.ina).list_tokens() == []
    with pytest.raises(NotFound):
        w.svc(w.ina).revoke_token(token["id"])
    revoked = svc.revoke_token(token["id"])
    assert revoked["revoked"] is True and store.resolve_api_token(w.conn, plaintext) is None
    assert svc.create_token({"name": "Standard"})["expires_at"]
    for bad in ({}, {"name": ""}, {"name": "x", "expires_days": 0}, {"name": "x", "expires_days": 366},
                {"name": "x", "expires_days": 1.5}, {"name": "a\nb"}):
        with pytest.raises(ValidationError):
            svc.create_token(bad)
    with pytest.raises(NotFound):
        svc.revoke_token("kaputt")


def test_preferences_and_global_settings(w):
    svc = w.experimenter
    assert svc.get_preferences() == {"show_in_search": True}
    assert svc.update_preferences({"show_in_search": False}) == {"show_in_search": False}
    assert w.svc(w.ina).get_preferences() == {"show_in_search": True}
    for bad in ({}, {"show_in_search": "false"}, {"show_in_search": False, "access_groups": ["x"]}):
        with pytest.raises(ValidationError):
            svc.update_preferences(bad)
    assert svc.get_settings() == {"show_in_search": True}
    assert w.manager.update_settings({"show_in_search": False}) == {"show_in_search": False}
    assert svc.get_settings() == {"show_in_search": False}
    for bad in ({}, {"show_in_search": 0}, {"show_in_search": True, "other": 1}, None):
        with pytest.raises(ValidationError):
            w.manager.update_settings(bad)
    assert w.audit("experiments.settings.update")[-1]["target_id"] == "experiments.show_in_search"


def test_index_status(w):
    w.create()
    status = w.svc(w.max, runner=w.runner).index_status()
    assert status["enabled"] is True and status["unrestricted"] is False
    assert status["access_groups"] == ["g-exp"]
    assert status["counts"]["pending"] == 1 and status["jobs"]["pending"] == 1
    assert status["failures"] == []
    assert {x["user"] for x in status["access_warnings"]} == {
        "Eva (eva@knovas.ch)", "Ina (ina@knovas.ch)", "Max (max@knovas.ch)", "Chef (chef@kanzlei.ch)"}
    assert status["runner"]["ok"] is True
    assert w.manager.index_status()["runner"] == {"configured": False}


# -- search ---------------------------------------------------------------------------------------


def test_search_merges_knovas_hits_with_the_database(w):
    exp = w.create()
    other = w.create(title="Newsletter", hypothesis="Betreffzeilen mit Zahlen")
    w.experimenter.add_note(other["key"], {"body": "Karussell im Newsletter testen"})
    calls = []

    def knovas(query, limit):
        calls.append((query, limit))
        return {"results": [
            {"doc_id": "experiments/marketing/MKT-1", "top_chunks": [{"text": "Aus Knovas: Karussell"}]},
            {"doc_id": "Akten/Vertrag.pdf", "top_chunks": ["fremd"]},
            {"pointer": "/experiments/marketing/MKT-99"},
            {"doc_id": "experiments/marketing/MKT-1"},
        ]}

    w.repo.set_access_groups(w.eva.id, ["g-exp"])
    result = w.svc(w.eva, knovas_search=knovas).search("Karussell", limit=10)
    assert calls == [("Karussell", 50)]
    assert result["source"] == "knovas+database" and result["warning"] is None
    assert [i["key"] for i in result["items"]] == [exp["key"], other["key"]]
    assert result["items"][0]["snippet"] == "Aus Knovas: Karussell"
    assert result["items"][1]["snippet"] == "Karussell im Newsletter testen"
    assert result["items"][0]["domain"]["key"] == "marketing"

    only_knovas = w.svc(w.eva, knovas_search=knovas).search("Aus Knovas")
    assert only_knovas["source"] == "knovas"

    def broken(query, limit):
        raise RuntimeError("secret stack trace")

    fallback = w.svc(w.eva, knovas_search=broken).search("Karussell")
    assert fallback["source"] == "database" and "secret" not in json.dumps(fallback)
    assert fallback["warning"] == "Die Knovas-Suche ist gerade nicht verf\u00fcgbar; gezeigt werden Datenbanktreffer."
    no_group = w.svc(w.ina, knovas_search=knovas).search("Karussell")
    assert no_group["source"] == "database"
    assert no_group["warning"] == ("Ihnen fehlt die Knovas-Zugriffsgruppe f\u00fcr Experimente; "
                                   "gezeigt werden Datenbanktreffer.")
    off = dataclasses.replace(SETTINGS, index_enabled=False)
    assert w.svc(w.eva, settings=off, knovas_search=knovas).search("Karussell")["source"] == "database"
    assert w.svc(w.eva).search("   ") == {"source": "database", "warning": None, "items": []}
    assert len(w.svc(w.eva).search("Karussell", limit=1)["items"]) == 1


def test_list_experiments_filters_and_pages(w):
    svc = w.experimenter
    first = w.create(tags=["q3"])
    second = w.create(title="Newsletter", hypothesis="Zahlen")
    page = svc.list_experiments(limit=1)
    assert page["total"] == 2 and [i["key"] for i in page["items"]] == [second["key"]]
    rest = svc.list_experiments(limit=1, after=page["next_after"])
    assert [i["key"] for i in rest["items"]] == [first["key"]] and rest["next_after"] is None
    assert [i["key"] for i in svc.list_experiments(tag="q3")["items"]] == [first["key"]]
    assert [i["key"] for i in svc.list_experiments(q="newsletter zahlen")["items"]] == [second["key"]]
    assert svc.list_experiments(domain="sales")["total"] == 0
    assert svc.list_experiments(status="draft")["total"] == 2
    with pytest.raises(ValidationError):
        svc.list_experiments(after="kaputt")


def test_timeseries_through_the_service(w):
    svc = w.experimenter
    key = w.create()["key"]
    svc.add_measurements(key, {"rows": [{"metric": "ctr", "variant": "A", "value": 1, "count": 10,
                                         "observed_at": "2026-09-01"}]})
    series = svc.get_timeseries(key, "ctr", "month")
    assert series == [{"bucket_start": "2026-09-01T00:00:00+00:00", "variant": "A", "n": 10,
                       "value_sum": 1.0, "denominator_sum": None, "estimate": 0.1}]
    with pytest.raises(NotFound):
        svc.get_timeseries(key, "demo_request_rate")
    with pytest.raises(ValidationError):
        svc.get_timeseries(key, "ctr", "year")


# -- configuration --------------------------------------------------------------------------------


def test_domains(w):
    manager = w.manager
    domain = manager.create_domain({"key": "recruiting", "name": "Recruiting", "id_prefix": "rec",
                                    "description": "Stellen"})
    assert domain == {"id": domain["id"], "key": "recruiting", "name": "Recruiting",
                      "description": "Stellen", "color": "#5A6B80", "id_prefix": "REC", "pack": None,
                      "archived": False, "experiment_count": 0, "running_count": 0}
    with pytest.raises(Conflict):
        manager.create_domain({"key": "recruiting", "name": "X", "id_prefix": "RCX"})
    with pytest.raises(Conflict):
        manager.create_domain({"key": "rec2", "name": "X", "id_prefix": "REC"})
    for bad in ({"key": "R", "name": "X", "id_prefix": "RR"}, {"key": "abc", "name": "", "id_prefix": "RR"},
                {"key": "abc", "name": "X", "id_prefix": "1R"}, {"key": "abc", "name": "X", "id_prefix": "RR",
                                                                 "color": "red"}):
        with pytest.raises(ValidationError):
            manager.create_domain(bad)
    exp = w.create()
    w.experimenter.transition(exp["key"], {"to": "running"})
    listed = {d["key"]: d for d in w.experimenter.list_domains()}
    assert listed["marketing"]["experiment_count"] == 1 and listed["marketing"]["running_count"] == 1
    w.conn.execute("DELETE FROM exp_jobs")
    updated = manager.update_domain("marketing", {"name": "Werbung", "color": "#123456"})
    assert updated["name"] == "Werbung" and updated["color"] == "#123456"
    jobs = w.jobs("index")
    assert len(jobs) == 1 and jobs[0]["priority"] == 200
    manager.update_domain("recruiting", {"archived": True})
    assert "recruiting" not in {d["key"] for d in w.experimenter.list_domains()}
    assert "recruiting" in {d["key"] for d in w.experimenter.list_domains(include_archived=True)}
    with pytest.raises(ValidationError):
        w.experimenter.create_experiment({"domain": "recruiting", "type": "hypothesis", "title": "x"})
    with pytest.raises(NotFound):
        manager.update_domain("nope", {"name": "x"})


def test_types_validation_versions_and_archive(w):
    manager = w.manager
    base = manager.get_type(store.find_type(w.conn, None, "hypothesis")["id"])["definition"]
    broken = dict(base, metrics=[{"metric": "nope", "role": "primary"}],
                  evaluation=[{"evaluator": "builtin.welch_t", "metric": "ctr", "params": {"alpha": 5},
                               "scope": {}}, {"evaluator": "builtin.none", "metric": "all"}])
    with pytest.raises(ValidationError) as info:
        manager.validate_type({"domain": "marketing", "definition": broken})
    fields = info.value.fields
    assert fields["metrics[0].metric"] == "Die Metrik \u00abnope\u00bb gibt es nicht."
    assert "Mittelwert" not in fields["evaluation[0].metric"] and "Anteil" in fields["evaluation[0].metric"]
    assert "evaluation[0].params.alpha" in fields and "evaluation[1].evaluator" in fields
    with pytest.raises(ValidationError):
        manager.validate_type({"domain": None, "definition": dict(base, metrics=[{"metric": "ctr",
                                                                                   "role": "primary"}])})
    text = "states:\n  - {key: a, label: A}\n  - {key: b, label: B}\ntransitions:\n  - {from: a, to: b, label: Los}\n"
    assert manager.validate_type({"definition_text": text})["initial"] == "a"
    with pytest.raises(ValidationError):
        manager.validate_type({"definition_text": "x: &a [1]\ny: *a\n"})
    with pytest.raises(ValidationError):
        manager.validate_type({})

    created = manager.create_type({"domain": "marketing", "key": "flow", "name": "Ablauf",
                                   "definition_text": text})
    assert created["current_version"] == 1 and created["domain_key"] == "marketing"
    assert [v["version"] for v in created["versions"]] == [1]
    with pytest.raises(Conflict):
        manager.create_type({"domain": "marketing", "key": "flow", "name": "X", "definition_text": text})
    exp = w.create(type="flow")
    same = manager.add_type_version(created["id"], {"definition_text": text})
    assert same["current_version"] == 1
    w.conn.execute("DELETE FROM exp_jobs")
    changed = manager.add_type_version(created["id"], {
        "name": "Ablauf 2", "definition": dict(created["definition"], decision={"require_learning": True})})
    assert changed["current_version"] == 2 and changed["name"] == "Ablauf 2"
    assert w.experimenter.get_experiment(exp["key"])["type"]["version"] == 1  # pinned
    assert [j["priority"] for j in w.jobs("index")] == [200]
    assert w.create(type="flow")["type"]["version"] == 2
    archived = manager.set_type_archived(created["id"], {"archived": True})
    assert archived["archived"] is True
    with pytest.raises(ValidationError):
        w.create(type="flow")
    assert "flow" not in {t["key"] for t in w.experimenter.list_types(domain="marketing")}
    assert "flow" in {t["key"] for t in w.experimenter.list_types(domain="marketing", include_archived=True)}
    assert {t["domain_key"] for t in w.experimenter.list_types(domain="marketing")} == {"marketing", None}
    with pytest.raises(NotFound):
        w.experimenter.get_type("kaputt")
    with pytest.raises(ValidationError):
        manager.set_type_archived(created["id"], "ja")


def test_metrics_configuration(w):
    manager = w.manager
    metric = manager.create_metric({"domain": None, "key": "nps", "name": "NPS", "kind": "mean",
                                    "unit": "Punkte", "definition": {"min": -100, "max": 100}})
    assert metric["domain_key"] is None and metric["version"] == 1 and metric["in_use"] is False
    assert metric["kind_label"] == "Mittelwert" and metric["direction_label"] == "h\u00f6her ist besser"
    for bad in ({"key": "all", "name": "x", "kind": "mean"}, {"key": "x", "name": "x", "kind": "mean"},
                {"key": "abc", "name": "x", "kind": "nope"}, {"key": "abc", "name": "x", "kind": "mean",
                                                              "direction": "up"},
                {"key": "abc", "name": "x", "kind": "categorical"}):
        with pytest.raises(ValidationError):
            manager.create_metric(bad)
    ctr = next(m for m in manager.list_metrics(domain="marketing") if m["key"] == "ctr")
    key = w.create()["key"]
    w.experimenter.add_measurements(key, measurements(A=(1, 10)))
    with pytest.raises(ValidationError) as info:
        manager.update_metric(ctr["id"], {"kind": "count"})
    assert info.value.message == "\u00abArt\u00bb: Die Art l\u00e4sst sich nicht mehr \u00e4ndern, es gibt schon Messwerte."
    w.conn.execute("DELETE FROM exp_jobs")
    renamed = manager.update_metric(ctr["id"], {"name": "CTR", "definition": {"decimals": 3}})
    assert renamed["version"] == 2 and renamed["definition"] == {"decimals": 3}
    assert len(w.jobs("index")) == 1
    changed_kind = manager.update_metric(metric["id"], {"kind": "ordinal"})
    assert changed_kind["kind"] == "ordinal" and changed_kind["definition"] == {"min": -100, "max": 100}
    with pytest.raises(ValidationError):
        manager.update_metric(metric["id"], {"kind": "proportion"})  # min/max do not fit
    assert manager.update_metric(metric["id"], {"name": "NPS"})["version"] == 2  # unchanged
    with pytest.raises(NotFound):
        manager.update_metric(ZERO, {"name": "x"})


def test_evaluators_configuration_and_test_runs(w):
    manager = w.svc(w.max, runner=w.runner)
    created = make_custom_evaluator(w)
    assert created["current_version"] == 1 and created["code"] == CUSTOM_CODE
    assert created["builtin"] is False and created["needs_rows"] is True
    for bad in ({"key": "builtin.mine"}, {"key": "Bad Key"}, {"language": "r"}, {"code": ""},
                {"input_kinds": []}, {"input_kinds": ["nope"]},
                {"params_schema": {"type": "object", "properties": {"x": {"pattern": "(a+)+$"}}}}):
        with pytest.raises(ValidationError):
            make_custom_evaluator(w, **dict({"key": "team.other"}, **bad))
    same = manager.add_evaluator_version(created["id"], {})
    assert same["current_version"] == 1
    newer = manager.add_evaluator_version(created["id"], {"code": CUSTOM_CODE + "# v2\n"})
    assert newer["current_version"] == 2 and [v["version"] for v in newer["versions"]] == [2, 1]
    builtin = next(e for e in manager.list_evaluators() if e["key"] == "builtin.describe")
    with pytest.raises(ValidationError):
        manager.add_evaluator_version(builtin["id"], {"code": "x"})

    key = w.create()["key"]
    w.experimenter.add_measurements(key, measurements(A=(3, 30), B=(4, 30)))
    result = manager.test_evaluator(created["id"], {"experiment": key, "metric": "ctr",
                                                    "code": "def evaluate(d):\n    return {}\n"})
    assert result["ok"] is True and result["output"]["verdict"] == "better"
    assert w.runner.calls[-1]["timeout_seconds"] == 60
    assert w.runner.calls[-1]["code"] == "def evaluate(d):\n    return {}\n"
    entry = w.audit("experiments.evaluator.test")[-1]
    assert entry["target_type"] == "exp_evaluator" and entry["target_id"] == created["id"]
    assert entry["detail"]["unsaved"] is True and len(entry["detail"]["code_sha256"]) == 64
    assert entry["detail"]["experiment"] == key and entry["detail"]["metric"] == "ctr"
    assert "evaluate" not in json.dumps(entry["detail"])
    w.runner.result = {"ok": False, "output": None, "error": None, "logs": "boom", "duration_ms": 3}
    failed = manager.test_evaluator(created["id"], {"experiment": key, "metric": "ctr"})
    assert failed == {"ok": False, "output": None, "error": "Die Auswertung ist fehlgeschlagen.",
                      "logs": "boom", "duration_ms": 3}
    in_process = manager.test_evaluator(builtin["id"], {"experiment": key, "metric": "ctr"})
    assert in_process["ok"] is True and in_process["output"]["headline"]
    with pytest.raises(Unavailable):
        w.manager.test_evaluator(created["id"], {"experiment": key, "metric": "ctr"})
    with pytest.raises(ValidationError):
        manager.test_evaluator(created["id"], {"experiment": key, "metric": "cost_per_click"})
    with pytest.raises(NotFound):
        manager.test_evaluator(created["id"], {"experiment": "MKT-99", "metric": "ctr"})


def test_sample_size(w):
    svc = w.experimenter
    assert svc.sample_size({"kind": "proportion", "base": "0,05", "mde": "0.01"})["per_variant"] > 7000
    assert svc.sample_size({"kind": "mean", "sd": 1, "mde": 0.5, "alpha": 0.05, "power": 0.8}) == {
        "per_variant": 64}
    for bad in ({"kind": "ratio"}, {"kind": "proportion", "mde": 0.01}, {"kind": "proportion", "base": 2,
                                                                         "mde": 0.1},
                {"kind": "mean", "sd": 0, "mde": 1}, {"kind": "mean", "sd": 1e9, "mde": 1e-3},
                {"kind": "mean", "sd": 1, "mde": 1, "alpha": 0.7}, {"kind": "mean", "sd": 1, "mde": "x"}):
        with pytest.raises(ValidationError):
            svc.sample_size(bad)


# -- packs ------------------------------------------------------------------------------------------


def test_packs_install_list_export_and_import(w):
    manager = w.manager
    assert manager.install_pack("marketing") == {"domain": 0, "types": 0, "metrics": 0, "evaluators": 0}
    installed = {p["name"]: p["installed"] for p in w.experimenter.list_packs()}
    assert installed == {"core": True, "engineering": False, "marketing": True, "sales": False,
                         "product": False}
    with pytest.raises(NotFound):
        manager.install_pack("../etc/passwd")
    make_custom_evaluator(w)
    type_ = store.find_type(w.conn, None, "hypothesis")
    definition = dict(manager.get_type(type_["id"])["definition"],
                      metrics=[{"metric": "ctr", "role": "primary"}],
                      evaluation=[{"evaluator": "team.custom_py", "metric": "primary", "params": {},
                                   "scope": {}}])
    manager.create_type({"domain": "marketing", "key": "eigen", "name": "Eigen", "definition": definition})
    exp = w.create()

    text = manager.export_domain("marketing")
    assert text.startswith("pack: marketing\n") and "team.custom_py" in text
    from experiments import packs

    exported = packs.parse_pack_text(text, known_metrics=[], known_evaluators=[])
    assert [e["key"] for e in exported["evaluators"]] == ["team.custom_py"]
    assert exported["requires_metrics"] == [] and exported["domain"]["id_prefix"] == "MKT"
    assert {t["key"] for t in exported["types"]} == {"ab_test", "campaign", "content_test", "eigen"}
    edited = text.replace("name: Marketing\n", "name: Werbung\n", 1).replace(
        "name: Eigen\n", "name: Eigen 2\n", 1)
    w.conn.execute("DELETE FROM exp_jobs")
    counts = manager.import_pack({"text": edited})
    assert counts == {"domain": 1, "types": 1, "metrics": 0, "evaluators": 0}
    assert store.get_domain(w.conn, "marketing")["name"] == "Werbung"
    assert [j["priority"] for j in w.jobs("index")] == [200]  # its experiment shows the new name
    assert manager.import_pack({"text": edited}) == {"domain": 0, "types": 0, "metrics": 0, "evaluators": 0}
    assert exp

    copy = edited.replace("pack: marketing", "pack: werbung").replace("key: marketing", "key: werbung") \
        .replace("id_prefix: MKT", "id_prefix: WRB")
    assert manager.import_pack({"text": copy})["domain"] == 1
    assert w.experimenter.create_experiment({"domain": "werbung", "type": "ab_test", "title": "x"})["key"] == "WRB-1"
    bad_prefix = edited.replace("id_prefix: MKT", "id_prefix: MKX")
    with pytest.raises(Conflict):
        manager.import_pack({"text": bad_prefix})
    builtin_pack = ("pack: x1\ntitle: X\nversion: 1\nevaluators:\n"
                    "  - {key: builtin.evil, name: E, language: python, input_kinds: [mean], code: 'x'}\n")
    with pytest.raises(ValidationError):
        manager.import_pack({"text": builtin_pack})
    for bad in ({}, {"text": ""}, {"text": "a: &x 1\nb: *x\n"}):
        with pytest.raises(ValidationError):
            manager.import_pack(bad)
    with pytest.raises(NotFound):
        manager.export_domain("nope")


# -- end to end through the worker -------------------------------------------------------------------


def drain(conn, worker, limit=60):
    ran = 0
    for _ in range(limit):
        conn.execute("UPDATE exp_jobs SET run_after = clock_timestamp() - interval '1 second' "
                     "WHERE status = 'pending'")
        conn.execute("UPDATE exp_rate_slots SET next_at = clock_timestamp() - interval '1 second'")
        if not worker.run_once(conn):
            return ran
        ran += 1
    raise AssertionError("the queue did not drain")


def test_end_to_end_with_worker_fake_runner_and_fake_knovas(w):
    index_client = FakeIndexClient()
    handlers, on_dead, _ = tasks.build_handlers(settings=SETTINGS, index_client=index_client,
                                                runner=w.runner)
    worker = JobWorker(connect=lambda: w.conn, handlers=handlers, on_dead=on_dead, kinds=JOB_KINDS,
                       lease_seconds=60, poll_seconds=1, worker_id="test-worker")
    make_custom_evaluator(w)
    manager = w.manager
    type_ = store.find_type(w.conn, None, "hypothesis")
    definition = dict(manager.get_type(type_["id"])["definition"],
                      evaluation=[{"evaluator": "builtin.describe", "metric": "all", "params": {},
                                   "scope": {}},
                                  {"evaluator": "team.custom_py", "metric": "primary", "params": {},
                                   "scope": {}}])
    manager.create_type({"domain": "marketing", "key": "ci", "name": "Mit Eigenem", "definition": definition})
    svc = w.experimenter
    exp = w.create(type="ci")
    key = exp["key"]
    svc.set_metrics(key, {"metrics": [{"metric": "ctr", "role": "primary"}]})
    svc.set_variants(key, {"variants": [{"key": "A", "is_control": True}, {"key": "B"}]})
    svc.add_measurements(key, measurements(A=(10, 100), B=(20, 100)), source="api")

    drain(w.conn, worker)
    jobs = {(j["kind"], j["status"]) for j in w.jobs()}
    assert jobs <= {("index", "done"), ("pipeline", "done"), ("evaluate", "done")}
    snapshot = svc.get_experiment(key)
    by_evaluator = {e["evaluator_key"]: e for e in snapshot["evaluations"]}
    assert by_evaluator["builtin.describe"]["status"] == "done"
    assert by_evaluator["builtin.describe"]["trigger"] == "pipeline"
    assert by_evaluator["team.custom_py"]["status"] == "done"
    assert by_evaluator["team.custom_py"]["headline"] == "Eigen"
    assert len(w.runner.calls) == 1
    assert snapshot["index"]["state"] == "indexed" and snapshot["index"]["indexed_at"]
    upload = index_client.uploads[-1]
    assert upload["identifier"] == f"experiments/marketing/{key}"
    assert upload["access_groups"] == ["g-exp"]
    body = "\n".join(p["snippet"] for p in upload["parts"])
    assert "Karussell-Posts" in body and "Eigen" in body and "eva@knovas.ch" not in body
    assert store.index_documents(w.conn) == [f"experiments/marketing/{key}"]

    w.manager.delete_experiment(key)
    drain(w.conn, worker)
    assert index_client.deleted == [f"experiments/marketing/{key}"]
    assert store.index_documents(w.conn) == []


# -- smaller guarantees ---------------------------------------------------------------------------


def test_a_queued_builtin_evaluation_runs_in_process(w):
    svc = w.experimenter
    key = w.create()["key"]
    svc.add_measurements(key, measurements(A=(3, 30), B=(5, 30)))
    snapshot = svc.get_experiment(key)
    evaluator = store.get_evaluator(w.conn, key="builtin.describe")
    metric = next(m for m in snapshot["metrics"] if m["key"] == "ctr")

    def queued(params):
        return store.insert_evaluation(
            w.conn, experiment_id=snapshot["id"], evaluator_id=evaluator["id"], evaluator_version=1,
            metric_id=metric["id"], params=params, scope={}, trigger="api", status="queued")

    good = queued({})
    service_mod.execute_evaluation(w.conn, good, settings=SETTINGS, runner=None)
    done = svc.get_evaluation(key, good)
    assert done["status"] == "done" and done["headline"].startswith("Klickrate")
    refused = queued({"alpha": 5})
    service_mod.execute_evaluation(w.conn, refused, settings=SETTINGS, runner=None)
    failed = svc.get_evaluation(key, refused)
    assert failed["status"] == "failed" and failed["error"] == "Die Parameter sind ung\u00fcltig."


def test_service_calls_compose_into_a_callers_transaction(w):
    svc = w.experimenter
    with w.conn.transaction():
        exp = w.create()
        assert svc.get_experiment(exp["key"])["key"] == exp["key"]
        svc.add_measurements(exp["key"], measurements(A=(1, 10)))
    with pytest.raises(RuntimeError):
        with w.conn.transaction():
            w.create(title="Verworfen")
            raise RuntimeError("abort")
    listed = svc.list_experiments()
    assert listed["total"] == 1 and listed["items"][0]["key"] == exp["key"]
    # The rolled-back creation returned its number.
    assert w.create()["key"] == "MKT-2"


def test_lists_refuse_unknown_domains(w):
    with pytest.raises(NotFound):
        w.experimenter.list_types(domain="nope")
    with pytest.raises(NotFound):
        w.experimenter.list_metrics(domain="nope")
    metrics = w.experimenter.list_metrics(domain="marketing")
    assert {m["key"] for m in metrics} >= {"ctr", "cost_per_click"}
    assert set(metrics[0]) == {"id", "key", "name", "kind", "kind_label", "unit", "direction",
                               "direction_label", "description", "definition", "domain_key",
                               "archived", "version", "in_use"}
    evaluators = w.experimenter.list_evaluators()
    assert set(evaluators[0]) == {"id", "key", "name", "language", "description", "input_kinds",
                                  "current_version", "archived", "builtin", "params_schema",
                                  "needs_rows"}
    types = w.experimenter.list_types()
    assert set(types[0]) == {"id", "key", "name", "description", "domain_key", "current_version",
                             "archived", "definition", "experiment_count"}


def test_csv_import_needs_assigned_metrics(w):
    manager = w.manager
    manager.create_type({"domain": "marketing", "key": "bare", "name": "Leer",
                         "copy_from": store.find_type(w.conn, None, "hypothesis")["id"]})
    key = w.create(type="bare")["key"]
    with pytest.raises(ValidationError) as info:
        w.experimenter.import_csv(key, b"metric,value\nctr,1\n", "x.csv")
    assert "keine Metriken" in info.value.message


def test_snippets_are_cut_around_the_first_hit():
    text = "Anfang " + "x " * 400 + "Treffer hier " + "y " * 400
    snippet = service_mod._snippet(text, ["treffer"])
    assert "Treffer hier" in snippet and snippet.startswith("\u2026") and snippet.endswith("\u2026")
    assert len(snippet) <= 300
    assert service_mod._snippet("kurz", ["x"]) == "kurz"
    assert service_mod._chunk_text([{"content": "  a   b "}, "c"]) == "a b"
    assert service_mod._chunk_text("nope") == ""


def test_broken_text_is_refused_in_german_never_as_a_database_error(w):
    svc = w.svc(w.eva, runner=w.runner)
    exp = w.create(tags=["alt"])
    key = exp["key"]
    w.conn.execute("UPDATE exp_experiments SET archived = TRUE WHERE key = %s", (key,))
    lone = "\ud800"
    attempts = [
        lambda: svc.update_experiment(key, {"row_version": 1, "title": "a" + lone}),
        lambda: svc.add_measurements(key, {"rows": [{"metric": "ctr", "value": 1, "dims": {"q": lone}}]}),
        lambda: svc.add_measurements(key, {"rows": [{"metric": "ctr" + lone, "value": 1}]}),
        lambda: svc.add_measurements(key, {"rows": [{"metric": "ctr", "variant": "A\x00", "value": 1}]}),
        lambda: svc.run_evaluation(key, {"evaluator": "builtin.describe", "metric": "ctr",
                                         "params": {"target": lone}}),
        lambda: svc.run_evaluation(key, {"evaluator": "builtin.describe\x00", "metric": "ctr"}),
        lambda: svc.create_experiment({"domain": "marketing\x00", "type": "ab_test", "title": "x"}),
        lambda: svc.create_experiment({"domain": "marketing", "type": "ab\x00test", "title": "x"}),
        lambda: svc.set_metrics(key, {"metrics": [{"metric": "ctr\x00", "role": "primary"}]}),
        lambda: svc.list_experiments(domain="x\x00"),
        lambda: svc.add_note(key, {"body": "a" + lone}),
        lambda: svc.add_run(key, {"params": {"k": lone}}),
        lambda: svc.search("a\x00b"),
        lambda: w.manager.create_evaluator({"key": "team.x", "name": "X", "language": "python",
                                           "code": "x", "input_kinds": ["mean"],
                                           "params_schema": {"title": lone}}),
        lambda: w.manager.create_domain({"key": "abc", "name": "N" + lone, "id_prefix": "ABC"}),
    ]
    for i, attempt in enumerate(attempts):
        with pytest.raises((ValidationError, NotFound)):
            attempt()
    with pytest.raises(NotFound):
        w.manager.update_domain("marketing\x00", {"name": "x"})
    with pytest.raises(NotFound):
        w.experimenter.list_types(domain="x" + lone)
    # Flags from a query string: "0" is false.
    assert svc.list_experiments(include_archived="0")["total"] == 0
    assert svc.list_experiments(include_archived="1")["total"] == 1
    assert svc.list_experiments(include_archived=True, tag="alt")["total"] == 1


def test_evaluators_need_only_key_name_language_code_and_kinds(w):
    minimal = w.manager.create_evaluator({"key": "team.minimal", "name": "Minimal", "language": "julia",
                                          "code": "evaluate(data) = Dict()", "input_kinds": ["count"]})
    assert minimal["description"] == "" and minimal["params_schema"] == {}
    assert minimal["language"] == "julia" and minimal["versions"][0]["created_by"] == "Max"
    renamed = w.manager.add_evaluator_version(minimal["id"], {"name": "Minimal 2"})
    assert renamed["name"] == "Minimal 2" and renamed["current_version"] == 2
    assert renamed["code"] == "evaluate(data) = Dict()"
    assert w.experimenter.get_evaluator(minimal["id"])["versions"][0]["version"] == 2
    with pytest.raises(Conflict):
        w.manager.create_evaluator({"key": "team.minimal", "name": "X", "language": "python",
                                    "code": "x", "input_kinds": ["count"]})


def test_one_pipeline_per_experiment_at_a_time(w, platform_db):
    import psycopg

    from conftest import PLATFORM_DB_TEST_DSN

    svc = w.experimenter
    exp = w.create()
    svc.add_measurements(exp["key"], measurements(A=(1, 10)))
    schema_name = platform_db.execute("SELECT current_schema()").fetchone()[0]
    with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True,
                         options=f"-c search_path={schema_name}") as other:
        assert store.try_advisory_lock(other, f"experiments.pipeline:{exp['id']}")
        with pytest.raises(RetryLater) as info:
            service_mod.run_pipeline_job(w.conn, exp["id"], settings=SETTINGS, runner=None)
        assert info.value.delay_seconds == 30
        assert w.conn.execute("SELECT count(*) FROM exp_evaluations").fetchone()[0] == 0
        store.advisory_unlock(other, f"experiments.pipeline:{exp['id']}")
    service_mod.run_pipeline_job(w.conn, exp["id"], settings=SETTINGS, runner=None)
    assert w.conn.execute("SELECT count(*) FROM exp_evaluations").fetchone()[0] > 0
    # The job released its lock: another session can take it now.
    assert store.try_advisory_lock(w.conn, f"experiments.pipeline:{exp['id']}")
    store.advisory_unlock(w.conn, f"experiments.pipeline:{exp['id']}")


@pytest.fixture
def connect(platform_db):
    import psycopg

    from conftest import PLATFORM_DB_TEST_DSN

    schema_name = platform_db.execute("SELECT current_schema()").fetchone()[0]
    opened = []

    def _connect():
        conn = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True,
                               options=f"-c search_path={schema_name}")
        opened.append(conn)
        return conn

    yield _connect
    for conn in opened:
        if not conn.closed:
            conn.close()


def test_metric_update_and_a_measurement_insert_do_not_deadlock(w, connect, monkeypatch):
    """The manager renames a metric while CI inserts rows of it: the rename
    holds the metric's row lock, the insert holds its experiment and waits
    for the metric (foreign key). The rename must not then wait for the
    experiment inside the same transaction."""
    import threading
    import time

    key = w.create()["key"]
    metric_id = next(m["id"] for m in w.experimenter.get_experiment(key)["metrics"] if m["key"] == "ctr")
    holding = threading.Event()
    real_update = store.update_metric

    def slow_update(conn, mid, changes):
        version = real_update(conn, mid, changes)
        holding.set()
        time.sleep(0.5)  # the insert starts now and queues behind the metric lock
        return version

    monkeypatch.setattr(store, "update_metric", slow_update)
    errors = []

    def rename():
        try:
            ExperimentService(connect(), w.max, SETTINGS).update_metric(metric_id, {"name": "CTR neu"})
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    def insert():
        try:
            holding.wait(5)
            ExperimentService(connect(), w.eva, SETTINGS).add_measurements(key, measurements(A=(1, 10)))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=rename), threading.Thread(target=insert)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    assert w.experimenter.get_experiment(key)["measurement_count"] == 1
    assert store.get_metric(w.conn, metric_id)["name"] == "CTR neu"
