"""Packs: the shipped YAML packs, safe parsing, validation of references,
export/import round trips and the two example evaluators in core.yaml.

Most tests stand in for evaluators.BUILTINS with the contract's table (plan,
section 7) so they do not depend on the statistics code; one test validates
every shipped pack against the real module when it is importable.
"""

from __future__ import annotations

import copy
import json
import math
import random
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

import pytest
import yaml

from experiments import kinds, packs
from experiments.errors import NotFound, ValidationError
from experiments.packs import (
    PACKS_DIR,
    available_packs,
    dump_pack,
    load_pack,
    parse_pack_text,
    validate_pack,
    validate_params_schema,
)

SHIPPED = ["core", "engineering", "marketing", "sales", "product"]


# -- stand-in for evaluators.BUILTINS ---------------------------------------------


@dataclass(frozen=True)
class FakeSpec:
    key: str
    input_kinds: Tuple[str, ...]
    params_schema: Dict[str, Any] = field(default_factory=dict)
    needs_rows: bool = False


def _closed(properties):
    return {"type": "object", "additionalProperties": False, "properties": properties}


_ALPHA = {"alpha": {"type": "number", "minimum": 0.001, "maximum": 0.2},
          "correction": {"enum": ["holm", "none"]}}
_MEAN_LIKE = ("mean", "duration", "currency", "ordinal")

FAKE_BUILTINS = {
    spec.key: spec for spec in [
        FakeSpec("builtin.describe", tuple(kinds.KINDS), _closed({"target": {"type": "number"}})),
        FakeSpec("builtin.two_proportion", ("proportion",), _closed(_ALPHA)),
        FakeSpec("builtin.bayes_proportion", ("proportion",), _closed({
            "threshold": {"type": "number", "minimum": 0.5, "maximum": 0.999},
            "prior_a": {"type": "number", "minimum": 0.01, "maximum": 1000},
            "prior_b": {"type": "number", "minimum": 0.01, "maximum": 1000},
        })),
        FakeSpec("builtin.welch_t", _MEAN_LIKE, _closed(_ALPHA)),
        FakeSpec("builtin.paired_t", _MEAN_LIKE, _closed({
            **_ALPHA, "pair_by": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,40}$"},
        }), needs_rows=True),
        FakeSpec("builtin.poisson_rate", ("count",), _closed(_ALPHA)),
        FakeSpec("builtin.ratio_delta", ("ratio",), _closed(_ALPHA), needs_rows=True),
        FakeSpec("builtin.chi_square", ("categorical", "ordinal"), _closed({
            **_ALPHA, "expected": {"type": "array", "maxItems": 50,
                                   "items": {"type": "number", "exclusiveMinimum": 0}},
        })),
    ]
}


#: The real lazy importer, before the autouse fixture replaces it.
REAL_BUILTIN_SPECS = packs._builtin_specs


@pytest.fixture(autouse=True)
def fake_builtins(monkeypatch):
    monkeypatch.setattr(packs, "_builtin_specs", lambda: dict(FAKE_BUILTINS))
    return FAKE_BUILTINS


# -- a small valid pack to mutate ---------------------------------------------------

AB_STATES = [
    {"key": "draft", "label": "Entwurf"},
    {"key": "running", "label": "L\u00e4uft", "phase": "running"},
    {"key": "analysis", "label": "Auswertung"},
    {"key": "decided", "label": "Entschieden", "phase": "decided"},
    {"key": "stopped", "label": "Abgebrochen", "phase": "stopped"},
]


def _ab_transitions(start_requires, analysis_requires=("measurements",)):
    return [
        {"from": "draft", "to": "running", "label": "Starten", "requires": list(start_requires),
         "roles": []},
        {"from": "running", "to": "analysis", "label": "Zur Auswertung",
         "requires": list(analysis_requires), "roles": []},
        {"from": "analysis", "to": "decided", "label": "Entscheiden", "requires": ["decision"],
         "roles": []},
        {"from": "analysis", "to": "running", "label": "Weiterlaufen lassen", "requires": [],
         "roles": []},
        {"from": "*", "to": "stopped", "label": "Abbrechen", "requires": [], "roles": []},
    ]


def small_pack():
    return {
        "pack": "research",
        "title": "Recherche",
        "description": "Test",
        "version": 1,
        "domain": {"key": "research", "name": "Recherche", "id_prefix": "RES"},
        "metrics": [
            {"key": "hit_rate", "name": "Trefferquote", "kind": "proportion", "direction": "higher"},
            {"key": "minutes", "name": "Minuten", "kind": "duration", "unit": "min",
             "direction": "lower", "definition": {"decimals": 1}},
        ],
        "types": [{
            "key": "study",
            "name": "Studie",
            "definition": {
                "states": copy.deepcopy(AB_STATES),
                "transitions": _ab_transitions(["hypothesis"]),
                "metrics": [{"metric": "hit_rate", "role": "primary"},
                            {"metric": "minutes", "role": "guardrail", "op": "max", "value": 30}],
                "evaluation": [{"evaluator": "builtin.describe", "metric": "all"},
                               {"evaluator": "builtin.bayes_proportion", "metric": "primary",
                                "params": {"threshold": 0.9}}],
            },
        }],
        "evaluators": [{
            "key": "research.custom", "name": "Eigener", "language": "python",
            "input_kinds": ["duration"], "code": "def evaluate(data):\n    return {}\n",
            "params_schema": _closed({"k": {"type": "integer"}}),
        }],
    }


def refused(pack, **kw):
    with pytest.raises(ValidationError) as info:
        validate_pack(pack, **kw)
    return info.value


# -- listing and loading ------------------------------------------------------------


def test_packs_dir_holds_the_shipped_files():
    assert sorted(p.stem for p in PACKS_DIR.glob("*.yaml")) == sorted(SHIPPED)


def test_available_packs_core_first_with_metadata():
    listed = available_packs()
    assert [p["name"] for p in listed] == SHIPPED
    by_name = {p["name"]: p for p in listed}
    assert by_name["core"]["domain_key"] is None
    assert by_name["marketing"] == {
        "name": "marketing", "title": "Marketing",
        "description": "A/B-Tests, Kampagnen und Content-Tests.", "version": 1,
        "domain_key": "marketing",
    }
    assert {p["domain_key"] for p in listed} == {None, "engineering", "marketing", "sales",
                                                 "product"}
    for p in listed:
        assert set(p) == {"name", "title", "description", "version", "domain_key"}


@pytest.mark.parametrize("name", SHIPPED)
def test_every_shipped_pack_validates_and_round_trips(name):
    pack = load_pack(name)
    assert pack["pack"] == name
    text = dump_pack(pack)
    assert parse_pack_text(text) == pack
    assert validate_pack(pack) == pack
    # Real umlauts in the export, never \xe4 escapes.
    assert "\\x" not in text and "\\u" not in text


@pytest.mark.parametrize("name", [
    "nope", "../core", "core.yaml", "", "CORE", "a" * 40, "core/../core", None, 7,
])
def test_load_pack_refuses_unknown_names(name):
    with pytest.raises(NotFound) as info:
        load_pack(name)
    assert info.value.message == "Das Paket gibt es nicht."


def test_shipped_packs_with_the_real_builtins(monkeypatch):
    evaluators = pytest.importorskip("experiments.evaluators")
    monkeypatch.setattr(packs, "_builtin_specs", lambda: dict(evaluators.BUILTINS))
    for name in SHIPPED:
        load_pack(name)


def test_every_builtin_the_packs_name_is_in_the_contract_table():
    for name in SHIPPED:
        for t in load_pack(name)["types"]:
            for entry in t["definition"]["evaluation"]:
                if entry["evaluator"].startswith("builtin."):
                    assert entry["evaluator"] in FAKE_BUILTINS


def test_directory_listing_skips_bad_names_and_broken_files(tmp_path, monkeypatch, caplog):
    good = dump_pack(validate_pack(small_pack()))
    (tmp_path / "research.yaml").write_text(good, encoding="utf-8")
    (tmp_path / "Bad.yaml").write_text(good, encoding="utf-8")
    (tmp_path / "notes.txt").write_text(good, encoding="utf-8")
    (tmp_path / "broken.yaml").write_text("pack: [\n", encoding="utf-8")
    (tmp_path / "other.yaml").write_text(good, encoding="utf-8")
    monkeypatch.setattr(packs, "PACKS_DIR", tmp_path)
    with caplog.at_level("WARNING"):
        names = [p["name"] for p in available_packs()]
    assert names == ["other", "research"]
    assert "broken" in caplog.text
    assert load_pack("research")["domain"]["key"] == "research"
    with pytest.raises(ValidationError):
        load_pack("broken")
    with pytest.raises(ValidationError) as info:
        load_pack("other")
    assert "research" in info.value.message
    with pytest.raises(NotFound):
        load_pack("Bad")


# -- shipped content (plan, section 5) ----------------------------------------------


def _type(pack_name, key):
    pack = load_pack(pack_name)
    return next(t for t in pack["types"] if t["key"] == key)


ALL_TYPES = [
    ("core", "hypothesis"),
    ("engineering", "offline_eval"), ("engineering", "performance"), ("engineering", "rollout"),
    ("marketing", "ab_test"), ("marketing", "campaign"), ("marketing", "content_test"),
    ("sales", "playbook"), ("sales", "pricing"),
    ("product", "usability"), ("product", "feature"),
]


def test_the_packs_hold_exactly_the_contract_types():
    found = [(name, t["key"]) for name in SHIPPED for t in load_pack(name)["types"]]
    assert found == ALL_TYPES


@pytest.mark.parametrize("pack_name, key", ALL_TYPES)
def test_every_type_uses_the_ab_states_describe_first_and_requires_learning(pack_name, key):
    definition = _type(pack_name, key)["definition"]
    assert definition["states"] == AB_STATES
    assert definition["initial"] == "draft"
    # offline_eval is fed from CI, one run per push: its describe (and with
    # it the latency guardrail) judges the newest run of each variant.
    scope = {"runs": "latest"} if key == "offline_eval" else {}
    assert definition["evaluation"][0] == {"evaluator": "builtin.describe", "metric": "all",
                                           "params": {}, "scope": scope}
    assert definition["decision"] == {"require_learning": True}
    transitions = [(t["from"], t["to"], t["label"]) for t in definition["transitions"]]
    assert transitions == [
        ("draft", "running", "Starten"),
        ("running", "analysis", "Zur Auswertung"),
        ("analysis", "decided", "Entscheiden"),
        ("analysis", "running", "Weiterlaufen lassen"),
        ("*", "stopped", "Abbrechen"),
    ]
    requires = {(t["from"], t["to"]): t["requires"] for t in definition["transitions"]}
    assert requires[("analysis", "decided")] == ["decision"]
    assert requires[("analysis", "running")] == [] and requires[("*", "stopped")] == []
    if (pack_name, key) != ("sales", "playbook"):
        assert requires[("running", "analysis")] == ["measurements"]
    if key == "hypothesis":
        assert requires[("draft", "running")] == ["hypothesis"]
    elif definition["variants"]["min"] == 0:
        assert requires[("draft", "running")] == ["hypothesis", "primary_metric"]
    else:
        assert requires[("draft", "running")] == ["hypothesis", "primary_metric", "variants:2"]


TYPE_NAMES = {
    "hypothesis": "Allgemeine Hypothese", "offline_eval": "Offline-Evaluation",
    "performance": "Performance-\u00c4nderung", "rollout": "Feature-Rollout", "ab_test": "A/B-Test",
    "campaign": "Kampagne", "content_test": "Content-Test", "playbook": "Playbook-Test",
    "pricing": "Preis-Test", "usability": "Nutzertest", "feature": "Feature-Rollout",
}

A_B = [("A", "Kontrolle", True), ("B", "Variante B", False)]
VARIANTS = {
    "hypothesis": (0, []), "campaign": (0, []), "usability": (0, []),
    "offline_eval": (2, [("baseline", "Ausgangsstand", True), ("candidate", "Kandidat", False)]),
    "performance": (2, [("vorher", "Vorher", True), ("nachher", "Nachher", False)]),
    "rollout": (2, [("aus", "Flag aus", True), ("an", "Flag an", False)]),
    "ab_test": (2, A_B), "content_test": (2, A_B), "playbook": (2, A_B),
    "pricing": (2, [("A", "Aktueller Preis", True), ("B", "Neuer Preis", False)]),
    "feature": (2, [("A", "Ohne Feature", True), ("B", "Mit Feature", False)]),
}

METRICS = {
    "hypothesis": [],
    "offline_eval": [("ndcg_at_10", "primary"), ("recall_at_20", "secondary"), ("mrr", "secondary"),
                     ("latency_p95_ms", "guardrail", "max", 250)],
    "performance": [("latency_p95_ms", "primary"), ("error_rate", "guardrail", "max", 0.01)],
    "rollout": [("error_rate", "primary"), ("latency_p95_ms", "guardrail", "max", 300)],
    "ab_test": [("ctr", "primary"), ("conversion_rate", "secondary"), ("cost_per_click", "secondary"),
                ("bounce_rate", "guardrail", "max", 0.7)],
    "campaign": [("demo_request_rate", "primary"), ("ctr", "secondary"),
                 ("cost_per_lead", "secondary")],
    "content_test": [("ctr", "primary")],
    "playbook": [("meeting_rate", "primary"), ("reply_rate", "secondary"),
                 ("pipeline_value", "secondary"), ("unsubscribe_rate", "guardrail", "max", 0.02)],
    "pricing": [("win_rate", "primary"), ("deal_value", "secondary")],
    "usability": [("task_success", "primary"), ("sus_score", "secondary"),
                  ("satisfaction", "secondary"), ("time_to_value_s", "secondary")],
    "feature": [("task_success", "primary")],
}

_PAIRED = {"pair_by": "query"}
EVALUATION = {
    "hypothesis": [],
    "offline_eval": [("builtin.paired_t", "ndcg_at_10", _PAIRED, {"runs": "latest"}),
                     ("builtin.paired_t", "recall_at_20", _PAIRED, {"runs": "latest"}),
                     ("builtin.paired_t", "mrr", _PAIRED, {"runs": "latest"})],
    "performance": [("builtin.welch_t", "primary", {}, {})],
    "rollout": [("builtin.two_proportion", "primary", {}, {}),
                ("builtin.bayes_proportion", "primary", {}, {})],
    "ab_test": [("builtin.bayes_proportion", "primary", {}, {}),
                ("builtin.two_proportion", "primary", {}, {}),
                ("builtin.ratio_delta", "cost_per_click", {}, {})],
    "campaign": [],
    "content_test": [("builtin.bayes_proportion", "primary", {}, {})],
    "playbook": [("builtin.bayes_proportion", "primary", {}, {}),
                 ("builtin.two_proportion", "primary", {}, {})],
    "pricing": [("builtin.bayes_proportion", "primary", {}, {}),
                ("builtin.welch_t", "deal_value", {}, {})],
    "usability": [("builtin.describe", "task_success", {"target": 0.8}, {})],
    "feature": [("builtin.bayes_proportion", "primary", {}, {})],
}

COMPONENTS = ["Suche", "Ingestion", "Vorschau", "Cortex", "RemoteController", "Plattform",
              "Sonstiges"]
CHANNELS = ["LinkedIn", "Google Ads", "E-Mail", "Website", "Webinar", "Messe", "Sonstiges"]
SEGMENTS = ["Kanzlei klein", "Kanzlei mittel", "Kanzlei gross", "Rechtsabteilung", "Sonstiges"]
FIELDS = {
    "hypothesis": [], "feature": [],
    "offline_eval": [("component", "enum", COMPONENTS), ("query_set", "text", None),
                     ("baseline_ref", "text", None), ("candidate_ref", "text", None)],
    "performance": [("component", "enum", COMPONENTS),
                    ("environment", "enum", ["lokal", "CI", "Staging", "Produktion"])],
    "rollout": [("feature_flag", "text", None), ("rollout_percent", "number", None)],
    "ab_test": [("channel", "enum", CHANNELS), ("audience", "text", None),
                ("budget", "number", None), ("campaign_ref", "text", None)],
    "campaign": [("channel", "enum", CHANNELS), ("audience", "text", None),
                 ("budget", "number", None), ("goal", "longtext", None)],
    "content_test": [("channel", "enum", CHANNELS),
                     ("format", "enum", ["Text", "Bild", "Karussell", "Video", "Dokument"])],
    "playbook": [("segment", "enum", SEGMENTS),
                 ("territory", "enum", ["Schweiz", "Deutschland", "\u00d6sterreich", "Sonstiges"]),
                 ("sequence", "text", None), ("planned_n", "integer", None)],
    "pricing": [("segment", "enum", SEGMENTS), ("price_model", "text", None)],
    "usability": [("sessions", "integer", None), ("persona", "text", None),
                  ("script", "longtext", None)],
}


@pytest.mark.parametrize("pack_name, key", ALL_TYPES)
def test_type_content_matches_the_contract(pack_name, key):
    t = _type(pack_name, key)
    definition = t["definition"]
    assert t["name"] == TYPE_NAMES[key]
    assert t["description"]

    vmin, defaults = VARIANTS[key]
    assert definition["variants"]["min"] == vmin
    assert [(v["key"], v["name"], v["is_control"]) for v in definition["variants"]["defaults"]] \
        == defaults

    got = []
    for m in definition["metrics"]:
        entry = (m["metric"], m["role"])
        if m["role"] == "guardrail":
            entry += (m["op"], m["value"])
        got.append(entry)
    assert got == METRICS[key]

    evaluation = [(e["evaluator"], e["metric"], e["params"], e["scope"])
                  for e in definition["evaluation"][1:]]
    assert evaluation == EVALUATION[key]

    fields = [(f["key"], f["type"], f.get("options")) for f in definition["fields"]]
    expected = [(k, ty, opts) for k, ty, opts in FIELDS[key]]
    assert fields == expected
    for f in definition["fields"]:
        assert f["label"] and f["required"] is False


def test_field_bounds_of_the_contract():
    rollout = {f["key"]: f for f in _type("engineering", "rollout")["definition"]["fields"]}
    assert (rollout["rollout_percent"]["min"], rollout["rollout_percent"]["max"]) == (0, 100)
    playbook = {f["key"]: f for f in _type("sales", "playbook")["definition"]["fields"]}
    assert playbook["planned_n"]["min"] == 1


def test_playbook_waits_for_the_planned_sample():
    definition = _type("sales", "playbook")["definition"]
    running = next(t for t in definition["transitions"] if t["from"] == "running")
    assert running["requires"] == ["measurements", "n_planned:planned_n"]


DOMAINS = {
    "engineering": ("engineering", "Engineering", "ENG", "#2a78d6"),
    "marketing": ("marketing", "Marketing", "MKT", "#eb6834"),
    "sales": ("sales", "Vertrieb", "SAL", "#1baf7a"),
    "product": ("product", "Produkt", "PRD", "#4a3aa7"),
}


@pytest.mark.parametrize("name", list(DOMAINS))
def test_domains(name):
    domain = load_pack(name)["domain"]
    assert (domain["key"], domain["name"], domain["id_prefix"], domain["color"]) == DOMAINS[name]
    assert domain["description"]


def test_core_is_global():
    core = load_pack("core")
    assert "domain" not in core
    assert core["metrics"] == [] and core["requires_metrics"] == []
    hypothesis = core["types"][0]["definition"]
    assert hypothesis["fields"] == [] and hypothesis["metrics"] == []
    assert hypothesis["variants"] == {"min": 0, "max": 10, "defaults": []}


PACK_METRICS = {
    "engineering": [
        ("recall_at_20", "Recall@20", "mean", "higher", ""),
        ("ndcg_at_10", "NDCG@10", "mean", "higher", ""),
        ("mrr", "MRR", "mean", "higher", ""),
        ("latency_p95_ms", "Latenz p95", "duration", "lower", "ms"),
        ("latency_ms", "Latenz (Mittel)", "duration", "lower", "ms"),
        ("index_size_gb", "Indexgr\u00f6sse", "mean", "lower", "GB"),
        ("ci_minutes", "CI-Dauer", "duration", "lower", "min"),
        ("error_rate", "Fehlerrate", "proportion", "lower", "%"),
    ],
    "marketing": [
        ("ctr", "Klickrate", "proportion", "higher", "%"),
        ("conversion_rate", "Konversionsrate", "proportion", "higher", "%"),
        ("demo_request_rate", "Demo-Anfragequote", "proportion", "higher", "%"),
        ("bounce_rate", "Absprungrate", "proportion", "lower", "%"),
        ("cost_per_click", "Kosten pro Klick", "ratio", "lower", "CHF"),
        ("cost_per_lead", "Kosten pro Lead", "ratio", "lower", "CHF"),
    ],
    "sales": [
        ("reply_rate", "Antwortrate", "proportion", "higher", "%"),
        ("meeting_rate", "Terminquote", "proportion", "higher", "%"),
        ("pilot_conversion", "Pilot \u2192 Vertrag", "proportion", "higher", "%"),
        ("win_rate", "Abschlussquote", "proportion", "higher", "%"),
        ("unsubscribe_rate", "Abmelderate", "proportion", "lower", "%"),
        ("pipeline_value", "Pipeline-Wert", "currency", "higher", "CHF"),
        ("deal_value", "Vertragswert", "currency", "higher", "CHF"),
        ("cycle_days", "Verkaufszyklus", "duration", "lower", "Tage"),
    ],
    "product": [
        ("task_success", "Aufgabenerfolg", "proportion", "higher", "%"),
        ("sus_score", "SUS-Wert", "mean", "higher", "Punkte"),
        ("time_to_value_s", "Zeit bis Ergebnis", "duration", "lower", "s"),
        ("satisfaction", "Zufriedenheit", "ordinal", "higher", ""),
        ("preferred_option", "Bevorzugte Variante", "categorical", "none", ""),
    ],
}


@pytest.mark.parametrize("name", list(PACK_METRICS))
def test_pack_metrics(name):
    metrics = load_pack(name)["metrics"]
    assert [(m["key"], m["name"], m["kind"], m["direction"], m["unit"]) for m in metrics] \
        == PACK_METRICS[name]
    for m in metrics:
        assert m["description"]


def test_metric_definitions_of_the_contract():
    eng = {m["key"]: m for m in load_pack("engineering")["metrics"]}
    for key in ("recall_at_20", "ndcg_at_10", "mrr"):
        assert eng[key]["definition"] == {"decimals": 3, "min": 0, "max": 1}
        assert eng[key]["description"] == "Ein Messwert je Anfrage (dims.query) und Lauf."
    assert eng["latency_p95_ms"]["definition"] == {"decimals": 0}
    assert eng["latency_p95_ms"]["description"] == (
        "Ein Messwert je Benchmark-Lauf: das 95. Perzentil dieses Laufs. Die Sch\u00e4tzung ist "
        "das Mittel \u00fcber die L\u00e4ufe."
    )
    assert eng["latency_ms"]["description"] == "Ein Wert je Anfrage."
    assert load_pack("marketing")["metrics"][0]["definition"] == {"decimals": 2}
    product = {m["key"]: m for m in load_pack("product")["metrics"]}
    assert product["sus_score"]["definition"] == {"decimals": 1, "min": 0, "max": 100}
    assert product["satisfaction"]["definition"]["levels"] == {
        "1": "sehr unzufrieden", "2": "unzufrieden", "3": "neutral", "4": "zufrieden",
        "5": "sehr zufrieden",
    }
    assert product["preferred_option"]["definition"]["levels"] == {
        "0": "Keine Pr\u00e4ferenz", "1": "Variante A", "2": "Variante B", "3": "Variante C",
    }


def test_shipped_yaml_uses_ss_not_sharp_s():
    for path in PACKS_DIR.glob("*.yaml"):
        assert "\u00df" not in path.read_text(encoding="utf-8"), path.name


def test_core_evaluators():
    evaluators = {e["key"]: e for e in load_pack("core")["evaluators"]}
    assert list(evaluators) == ["example.bootstrap_mean_py", "example.beta_binomial_jl"]
    py = evaluators["example.bootstrap_mean_py"]
    jl = evaluators["example.beta_binomial_jl"]
    assert (py["language"], py["input_kinds"]) == ("python", ["mean", "duration", "currency",
                                                              "ordinal"])
    assert (jl["language"], jl["input_kinds"]) == ("julia", ["proportion"])
    assert "def evaluate(data)" in py["code"] and "import numpy as np" in py["code"]
    assert "function evaluate(data)" in jl["code"]
    for e in (py, jl):
        assert e["params_schema"]["additionalProperties"] is False
        assert e["name"] and e["description"]


# -- parse_pack_text ------------------------------------------------------------------


def test_json_and_yaml_packs_are_equivalent():
    pack = validate_pack(small_pack())
    assert parse_pack_text(json.dumps(pack)) == pack
    assert parse_pack_text(dump_pack(pack)) == pack


def test_pack_alias_bomb_is_refused_quickly():
    bomb = "pack: x\nlol: &a [1, 1, 1, 1, 1, 1, 1, 1, 1]\n" + "".join(
        f"l{i}: &a{i} [*a, *a, *a, *a, *a, *a, *a, *a]\n" for i in range(12)
    )
    started = time.monotonic()
    with pytest.raises(ValidationError) as info:
        parse_pack_text(bomb)
    assert time.monotonic() - started < 1
    assert info.value.message == "Anker und Verweise (&/*) sind nicht erlaubt."


def test_pack_python_tag_is_refused():
    with pytest.raises(ValidationError) as info:
        parse_pack_text("pack: !!python/object/apply:subprocess.call [['id']]\n")
    assert "nicht erlaubten Typ" in info.value.message


def test_pack_size_cap():
    with pytest.raises(ValidationError) as info:
        parse_pack_text("pack: core\ndescription: " + "x" * (2 * 1024 * 1024))
    assert info.value.message == "Das Paket ist zu gross (h\u00f6chstens 2048 KB)."


def test_pack_duplicate_keys_are_refused():
    text = dump_pack(validate_pack(small_pack())) + "title: Nochmals\n"
    with pytest.raises(ValidationError) as info:
        parse_pack_text(text)
    assert "\u00abtitle\u00bb kommt doppelt vor" in info.value.message


# -- validate_pack --------------------------------------------------------------------


def test_small_pack_normalised():
    pack = validate_pack(small_pack())
    assert list(pack) == ["pack", "title", "description", "version", "domain", "metrics",
                          "types", "evaluators", "requires_metrics"]
    assert pack["domain"] == {"key": "research", "name": "Recherche", "id_prefix": "RES",
                              "color": "#5A6B80", "description": ""}
    assert pack["metrics"][0] == {"key": "hit_rate", "name": "Trefferquote", "kind": "proportion",
                                  "unit": "", "direction": "higher", "description": "",
                                  "definition": {}}
    assert pack["evaluators"][0]["description"] == ""
    assert pack["requires_metrics"] == []
    assert validate_pack(pack) == pack


def test_minimal_pack_defaults():
    pack = validate_pack({"pack": "empty", "title": "Leer", "version": 3})
    assert pack == {"pack": "empty", "title": "Leer", "description": "", "version": 3,
                    "metrics": [], "types": [], "evaluators": [], "requires_metrics": []}


def _mutated(mutate):
    pack = small_pack()
    mutate(pack)
    return pack


def _type_def(pack):
    return pack["types"][0]["definition"]


PACK_CASES = [
    ("pack name not text", lambda p: p.update(pack=[]), "pack", "Muss Text sein."),
    ("unknown key", lambda p: p.update(author="x"), "author", "Unbekannter Eintrag."),
    ("title missing", lambda p: p.pop("title"), "title", "Pflichtangabe fehlt."),
    ("title blank", lambda p: p.update(title="  "), "title", "Darf nicht leer sein."),
    ("version zero", lambda p: p.update(version=0), "version", "Mindestens 1."),
    ("bad pack name", lambda p: p.update(pack="Research"), "pack", "Kleinbuchstaben"),
    ("bad id prefix", lambda p: p["domain"].update(id_prefix="res"), "domain.id_prefix",
     "Grossbuchstaben"),
    ("bad color", lambda p: p["domain"].update(color="red"), "domain.color", "#RRGGBB"),
    ("domain key missing", lambda p: p["domain"].pop("key"), "domain.key", "Pflichtangabe"),
    ("bad metric kind", lambda p: p["metrics"][0].update(kind="rate"), "metrics[0].kind",
     "Erlaubt sind: proportion"),
    ("bad direction", lambda p: p["metrics"][0].update(direction="up"), "metrics[0].direction",
     "Erlaubt sind: higher, lower, none."),
    ("reserved metric key", lambda p: p["metrics"][0].update(key="primary"), "metrics[0].key",
     "reserviert"),
    ("duplicate metric", lambda p: p["metrics"].append(dict(p["metrics"][0])), "metrics[2].key",
     "Die Metrik \u00abhit_rate\u00bb gibt es schon."),
    ("metric definition error", lambda p: p["metrics"][1].update(definition={"decimals": 9}),
     "metrics[1].definition.decimals", "H\u00f6chstens 6."),
    ("levels on a proportion", lambda p: p["metrics"][0].update(
        definition={"levels": {"1": "a", "2": "b"}}), "metrics[0].definition.levels",
     "Stufen gibt es nur"),
    ("unit too long", lambda p: p["metrics"][1].update(unit="x" * 21), "metrics[1].unit",
     "H\u00f6chstens 20 Zeichen."),
    ("duplicate type", lambda p: p["types"].append(copy.deepcopy(p["types"][0])), "types[1].key",
     "Den Typ \u00abstudy\u00bb gibt es schon."),
    ("bad type key", lambda p: p["types"][0].update(key="S"), "types[0].key", "Kleinbuchstaben"),
    ("type definition error", lambda p: _type_def(p)["states"][0].update(key="Draft"),
     "types[0].definition.states[0].key", "Kleinbuchstaben"),
    ("unknown metric in type", lambda p: _type_def(p)["metrics"][0].update(metric="ctr"),
     "types[0].definition.metrics[0].metric", "Die Metrik \u00abctr\u00bb gibt es weder im Paket"),
    ("unknown metric in evaluation", lambda p: _type_def(p)["evaluation"].append(
        {"evaluator": "builtin.describe", "metric": "ctr"}),
     "types[0].definition.evaluation[2].metric", "gibt es weder im Paket"),
    ("unknown builtin", lambda p: _type_def(p)["evaluation"][1].update(evaluator="builtin.magic"),
     "types[0].definition.evaluation[1].evaluator", "Den Auswerter \u00abbuiltin.magic\u00bb gibt es nicht."),
    ("unknown custom evaluator", lambda p: _type_def(p)["evaluation"][1].update(
        evaluator="other.eval"), "types[0].definition.evaluation[1].evaluator", "gibt es nicht"),
    ("evaluator kind mismatch", lambda p: _type_def(p)["evaluation"].append(
        {"evaluator": "builtin.welch_t", "metric": "hit_rate"}),
     "types[0].definition.evaluation[2].metric",
     "Der Auswerter \u00abbuiltin.welch_t\u00bb nimmt keine Metriken der Art \u00abAnteil\u00bb."),
    ("primary kind mismatch", lambda p: _type_def(p)["evaluation"].append(
        {"evaluator": "builtin.welch_t", "metric": "primary"}),
     "types[0].definition.evaluation[2].metric", "der Art \u00abAnteil\u00bb"),
    ("custom evaluator kind mismatch", lambda p: _type_def(p)["evaluation"].append(
        {"evaluator": "research.custom", "metric": "hit_rate"}),
     "types[0].definition.evaluation[2].metric", "der Art \u00abAnteil\u00bb"),
    ("builtin params refused", lambda p: _type_def(p)["evaluation"][1].update(
        params={"threshold": 0.1}), "types[0].definition.evaluation[1].params.threshold",
     "Mindestens 0,5."),
    ("unknown builtin param", lambda p: _type_def(p)["evaluation"][1].update(
        params={"draws": 10}), "types[0].definition.evaluation[1].params.draws",
     "Unbekannter Eintrag."),
    ("custom params refused", lambda p: _type_def(p)["evaluation"].append(
        {"evaluator": "research.custom", "metric": "minutes", "params": {"k": "x"}}),
     "types[0].definition.evaluation[2].params.k", "Muss eine ganze Zahl sein."),
    ("proportion guardrail as percent", lambda p: _type_def(p)["metrics"].__setitem__(
        0, {"metric": "hit_rate", "role": "guardrail", "op": "min", "value": 70}),
     "types[0].definition.metrics[0].value", "Anteile werden als Bruch angegeben"),
    ("builtin key for a custom evaluator", lambda p: p["evaluators"][0].update(
        key="builtin.custom"), "evaluators[0].key", "vorbehalten"),
    ("duplicate evaluator", lambda p: p["evaluators"].append(dict(p["evaluators"][0])),
     "evaluators[1].key", "Den Auswerter \u00abresearch.custom\u00bb gibt es schon."),
    ("unknown language", lambda p: p["evaluators"][0].update(language="r"),
     "evaluators[0].language", "Erlaubt sind: python, julia."),
    ("builtin language", lambda p: p["evaluators"][0].update(language="builtin"),
     "evaluators[0].language", "Erlaubt sind: python, julia."),
    ("empty code", lambda p: p["evaluators"][0].update(code=""), "evaluators[0].code",
     "Darf nicht leer sein."),
    ("blank code", lambda p: p["evaluators"][0].update(code="  \n "), "evaluators[0].code",
     "Darf nicht leer sein."),
    ("code too long", lambda p: p["evaluators"][0].update(code="#" * 200_001),
     "evaluators[0].code", "H\u00f6chstens 200'000 Zeichen."),
    ("no input kinds", lambda p: p["evaluators"][0].update(input_kinds=[]),
     "evaluators[0].input_kinds", "Mindestens einen Eintrag."),
    ("unknown input kind", lambda p: p["evaluators"][0].update(input_kinds=["quantile"]),
     "evaluators[0].input_kinds[0]", "Erlaubt sind"),
    ("input kinds missing", lambda p: p["evaluators"][0].pop("input_kinds"),
     "evaluators[0].input_kinds", "Pflichtangabe fehlt."),
    ("pattern in params schema", lambda p: p["evaluators"][0].update(
        params_schema=_closed({"k": {"type": "string", "pattern": "^(a+)+$"}})),
     "evaluators[0].params_schema.properties.k.pattern", "nicht erlaubt"),
    ("patternProperties in params schema", lambda p: p["evaluators"][0].update(
        params_schema={"type": "object", "patternProperties": {"^x": {}}}),
     "evaluators[0].params_schema.patternProperties", "nicht erlaubt"),
    ("remote ref in params schema", lambda p: p["evaluators"][0].update(
        params_schema={"type": "object", "properties": {"k": {"$ref": "https://evil.test/s"}}}),
     "evaluators[0].params_schema.properties.k.$ref", "Nur Verweise innerhalb"),
    ("params schema for a non-object", lambda p: p["evaluators"][0].update(
        params_schema={"type": "array"}), "evaluators[0].params_schema.type", "type: object"),
    ("requires_metrics also defined", lambda p: p.update(requires_metrics=["hit_rate"]),
     "requires_metrics[0]", "im Paket selbst definiert"),
    ("requires_metrics bad key", lambda p: p.update(requires_metrics=["Hit"]),
     "requires_metrics[0]", "Kleinbuchstaben"),
    ("NaN anywhere", lambda p: p["metrics"][1]["definition"].update(max=float("nan")),
     "metrics[1].definition.max", "Nur endliche Zahlen"),
]


@pytest.mark.parametrize("description, mutate, path, fragment", PACK_CASES,
                         ids=[c[0] for c in PACK_CASES])
def test_pack_refused_with_path(description, mutate, path, fragment):
    error = refused(_mutated(mutate))
    assert path in error.fields, error.fields
    assert fragment in error.fields[path], error.fields[path]
    assert error.message.startswith("Das Paket ist ung\u00fcltig")


def test_pack_must_be_an_object():
    assert refused([1]).fields == {"pack": "Muss ein Objekt sein."}


def test_guardrail_outside_the_metrics_bounds():
    pack = small_pack()
    pack["metrics"][1]["definition"] = {"min": 0, "max": 20}
    error = refused(pack)
    assert "ausserhalb von Minimum und Maximum" in error.fields[
        "types[0].definition.metrics[1].value"]


def test_global_metrics_resolve_through_known_or_declared_keys():
    pack = small_pack()
    _type_def(pack)["metrics"].append({"metric": "ctr", "role": "secondary"})
    _type_def(pack)["evaluation"].append({"evaluator": "builtin.describe", "metric": "nps"})
    assert set(refused(pack).fields) == {"types[0].definition.metrics[2].metric",
                                         "types[0].definition.evaluation[2].metric"}
    normalised = validate_pack(pack, known_metrics=["ctr", "nps", "unused"])
    assert normalised["requires_metrics"] == ["ctr", "nps"]
    declared = copy.deepcopy(pack)
    declared["requires_metrics"] = ["nps", "ctr"]
    assert validate_pack(declared)["requires_metrics"] == ["nps", "ctr"]


def test_known_evaluators_resolve_without_kind_checks():
    pack = small_pack()
    _type_def(pack)["evaluation"].append({"evaluator": "team.special", "metric": "hit_rate",
                                          "params": {"anything": 1}})
    assert "types[0].definition.evaluation[2].evaluator" in refused(pack).fields
    validate_pack(pack, known_evaluators=["team.special"])


def test_builtins_are_imported_only_when_a_type_needs_one(monkeypatch):
    def boom():
        raise AssertionError("evaluators must not be imported")

    monkeypatch.setattr(packs, "_builtin_specs", boom)
    pack = small_pack()
    _type_def(pack)["evaluation"] = []
    validate_pack(pack)
    available_packs()


def test_lazy_import_reads_evaluators_builtins(monkeypatch):
    import sys
    import types

    import experiments

    fake = types.ModuleType("experiments.evaluators")
    fake.BUILTINS = {"builtin.describe": FAKE_BUILTINS["builtin.describe"]}
    monkeypatch.setitem(sys.modules, "experiments.evaluators", fake)
    monkeypatch.setattr(experiments, "evaluators", fake, raising=False)
    assert REAL_BUILTIN_SPECS() == fake.BUILTINS


def test_evaluator_specs_given_as_dicts_are_understood(monkeypatch):
    monkeypatch.setattr(packs, "_builtin_specs", lambda: {
        key: {"input_kinds": list(spec.input_kinds), "params_schema": spec.params_schema}
        for key, spec in FAKE_BUILTINS.items()
    })
    for name in SHIPPED:
        load_pack(name)
    pack = small_pack()
    _type_def(pack)["evaluation"].append({"evaluator": "builtin.welch_t", "metric": "hit_rate"})
    assert "types[0].definition.evaluation[2].metric" in refused(pack).fields


def test_validate_pack_does_not_modify_its_input():
    pack = small_pack()
    before = copy.deepcopy(pack)
    validate_pack(pack)
    assert pack == before


# -- params schemas -------------------------------------------------------------------


def test_validate_params_schema():
    assert validate_params_schema(None) == {}
    assert validate_params_schema({}) == {}
    local_ref = {"type": "object", "$defs": {"p": {"type": "number"}},
                 "properties": {"alpha": {"$ref": "#/$defs/p"}}}
    assert validate_params_schema(local_ref) == local_ref
    # A parameter that happens to be called "pattern" is fine.
    named = _closed({"pattern": {"type": "string", "maxLength": 20}})
    assert validate_params_schema(named) == named
    with pytest.raises(ValidationError) as info:
        validate_params_schema({"type": "object", "allOf": [{"properties": {
            "x": {"type": "string", "pattern": "a"}}}]})
    assert "params_schema.allOf[0].properties.x.pattern" in info.value.fields
    with pytest.raises(ValidationError):
        validate_params_schema({"type": "object", "description": "x" * 17000})
    with pytest.raises(ValidationError):
        validate_params_schema(["type"])  # type: ignore[arg-type]


# -- dump_pack ----------------------------------------------------------------------


def test_dump_pack_is_safe_yaml_with_literal_code_blocks():
    core = load_pack("core")
    text = dump_pack(core)
    assert "code: |" in text
    assert "!!python" not in text and "&" not in text.split("code:")[0]
    assert "L\u00e4uft" in text
    assert text.startswith("pack: core\n")
    assert yaml.safe_load(text) == core


def test_dump_pack_quotes_what_yaml_would_misread():
    pack = validate_pack(small_pack())
    pack["types"][0]["definition"]["fields"] = [
        {"key": "flag", "label": "on", "type": "enum", "required": False, "help": "",
         "options": ["yes", "no", "*", "1.0", "~", "- x", "#1"]},
    ]
    text = dump_pack(pack)
    assert parse_pack_text(text)["types"][0]["definition"]["fields"][0]["options"] == \
        ["yes", "no", "*", "1.0", "~", "- x", "#1"]


def test_dump_pack_accepts_tuples():
    text = dump_pack({"pack": "x", "items": ("a", "b")})
    assert yaml.safe_load(text) == {"pack": "x", "items": ["a", "b"]}


def test_invalid_params_schema_is_refused():
    pack = small_pack()
    pack["evaluators"][0]["params_schema"] = {"type": "object",
                                              "properties": {"k": {"type": "banana"}}}
    error = refused(pack)
    assert any(path.startswith("evaluators[0].params_schema")
               and "kein g\u00fcltiges JSON Schema" in message
               for path, message in error.fields.items()), error.fields


def test_dump_pack_never_writes_anchors_for_shared_objects():
    shared = {"decimals": 2}
    pack = validate_pack(small_pack())
    pack["metrics"][0]["definition"] = shared
    pack["metrics"][1]["definition"] = shared
    text = dump_pack(pack)
    assert "&" not in text and "*id" not in text
    assert parse_pack_text(text)["metrics"][1]["definition"] == {"decimals": 2}


# -- the Python example evaluator ------------------------------------------------------

CONTRACT_KEYS = {"verdict", "headline", "summary", "comparisons", "variants", "values", "table",
                 "warnings"}
COMPARISON_KEYS = {"variant", "baseline", "label", "estimate", "ci_low", "ci_high", "p_value",
                   "prob_better", "relative", "unit", "verdict"}
VARIANT_KEYS = {"variant", "n", "value", "sd", "sum", "ci_low", "ci_high"}


def _python_evaluator():
    pytest.importorskip("numpy")
    code = next(e for e in load_pack("core")["evaluators"] if e["language"] == "python")["code"]
    namespace: Dict[str, Any] = {}
    exec(compile(code, "evaluator.py", "exec"), namespace)  # noqa: S102 - our own template
    return namespace["evaluate"]


def _rows(variant, values, count=1, dims=None):
    return [{"variant": variant, "run": None, "value": v, "count": count, "denominator": None,
             "sum_sq": None, "observed_at": "2026-09-16T00:00:00+00:00",
             "dims": dict(dims or {}, query=str(i))} for i, v in enumerate(values)]


def _input(rows, *, kind="mean", direction="higher", unit="ms", params=None, variants=None):
    return {
        "experiment": {"key": "ENG-1", "title": "t", "hypothesis": "", "domain": "engineering",
                       "type": "offline_eval", "status": "running", "fields": {}, "tags": []},
        "metric": {"key": "m", "name": "M", "kind": kind, "unit": unit, "direction": direction,
                   "role": "primary", "definition": {"decimals": 1}, "guardrail": None},
        "variants": variants or [{"key": "A", "name": "Kontrolle", "is_control": True},
                                 {"key": "B", "name": "B", "is_control": False}],
        "aggregates": [], "rows": rows, "rows_truncated": False, "scope": {},
        "params": params or {},
    }


def _assert_contract(output):
    assert set(output) == CONTRACT_KEYS
    json.dumps(output, allow_nan=False)
    assert output["verdict"] in {"better", "worse", "inconclusive", "n/a"}
    assert 0 < len(output["headline"]) <= 200
    for c in output["comparisons"]:
        assert set(c) == COMPARISON_KEYS
    for v in output["variants"]:
        assert set(v) == VARIANT_KEYS
    assert len(output["table"]["headers"]) == len(output["table"]["rows"][0])
    assert all(isinstance(cell, str) for row in output["table"]["rows"] for cell in row)


def _gauss(mean, sd, n, seed):
    rnd = random.Random(seed)
    return [rnd.gauss(mean, sd) for _ in range(n)]


def test_python_example_finds_a_clear_improvement():
    evaluate = _python_evaluator()
    data = _input(_rows("A", _gauss(100, 10, 150, 1)) + _rows("B", _gauss(110, 10, 150, 2)),
                  params={"draws": 1000})
    output = evaluate(data)
    _assert_contract(output)
    assert output["verdict"] == "better"
    comparison = output["comparisons"][0]
    assert (comparison["variant"], comparison["baseline"], comparison["unit"]) == ("B", "A", "ms")
    assert comparison["ci_low"] > 0 and comparison["prob_better"] > 0.99
    assert 8 < comparison["estimate"] < 12
    assert output["headline"].startswith("B besser als A: +")
    assert "95 %-KI" in output["headline"]
    assert output["variants"][0]["sd"] is not None
    assert output["values"]["control"] == "A"


def test_python_example_respects_direction_lower_and_none():
    evaluate = _python_evaluator()
    rows = _rows("A", _gauss(100, 10, 150, 1)) + _rows("B", _gauss(110, 10, 150, 2))
    lower = evaluate(_input(rows, direction="lower", params={"draws": 500}))
    assert lower["verdict"] == "worse"
    assert lower["comparisons"][0]["prob_better"] < 0.01
    none = evaluate(_input(rows, direction="none", params={"draws": 500}))
    assert none["verdict"] == "n/a" and none["comparisons"][0]["prob_better"] is None


def test_python_example_is_deterministic_and_inconclusive_without_a_difference():
    evaluate = _python_evaluator()
    same = _gauss(100, 10, 80, 1)
    rows = _rows("A", same) + _rows("B", list(reversed(same)))
    first = evaluate(_input(rows, params={"draws": 500, "seed": 3}))
    assert first == evaluate(_input(rows, params={"draws": 500, "seed": 3}))
    assert first["verdict"] == "inconclusive"


def test_python_example_with_too_little_data_warns_instead_of_failing():
    evaluate = _python_evaluator()
    output = evaluate(_input(_rows("A", [1.0]) + _rows("B", [2.0, 3.0])))
    _assert_contract(output)
    assert output["verdict"] == "inconclusive"
    assert output["comparisons"] == []
    assert any("zu wenige" in w for w in output["warnings"])
    empty = evaluate(_input([]))
    _assert_contract(empty)
    assert empty["verdict"] == "inconclusive"


def test_python_example_weights_aggregated_and_ordinal_rows():
    evaluate = _python_evaluator()
    # Ordinal: value is the level, count the people on it.
    rows = (_rows("A", [2.0], count=40) + _rows("A", [3.0], count=60)
            + _rows("B", [4.0], count=70) + _rows("B", [5.0], count=30))
    output = evaluate(_input(rows, kind="ordinal", unit="", params={"draws": 500}))
    means = {v["variant"]: v["value"] for v in output["variants"]}
    assert means == {"A": pytest.approx(2.6), "B": pytest.approx(4.3)}
    assert output["comparisons"][0]["estimate"] == pytest.approx(1.7)
    # Mean-like: an aggregated row carries the sum of its observations.
    rows = _rows("A", [300.0, 330.0], count=3) + _rows("B", [400.0, 440.0], count=4)
    output = evaluate(_input(rows, params={"draws": 500}))
    assert {v["variant"]: v["value"] for v in output["variants"]} == {
        "A": pytest.approx(105.0), "B": pytest.approx(105.0),
    }
    assert output["variants"][0]["sd"] is None


def test_python_example_caps_the_work_on_large_inputs():
    evaluate = _python_evaluator()
    rows = _rows("A", _gauss(1, 0.1, 30_000, 1)) + _rows("B", _gauss(1, 0.1, 30_000, 2))
    started = time.monotonic()
    output = evaluate(_input(rows, params={"draws": 20_000}))
    assert time.monotonic() - started < 30
    assert output["values"]["draws"] < 20_000
    assert any("Ziehungen" in w for w in output["warnings"])


def test_python_example_output_survives_sanitize_output():
    evaluators = pytest.importorskip("experiments.evaluators")
    sanitize = getattr(evaluators, "sanitize_output", None)
    if sanitize is None:
        pytest.skip("evaluators.sanitize_output is not there yet")
    evaluate = _python_evaluator()
    output = evaluate(_input(_rows("A", _gauss(100, 10, 60, 1)) + _rows("B", _gauss(120, 10, 60, 2)),
                             params={"draws": 300}))
    clean = sanitize(output, evaluator_name="Bootstrap")
    assert clean["verdict"] == output["verdict"] and clean["headline"] == output["headline"]
    assert not any("Nicht" in w for w in clean.get("warnings", []))


# -- the Julia example evaluator --------------------------------------------------------


def _julia_code():
    return next(e for e in load_pack("core")["evaluators"] if e["language"] == "julia")["code"]


def test_julia_example_uses_only_the_random_stdlib():
    imports = [line.strip() for line in _julia_code().splitlines()
               if re.match(r"\s*(using|import)\b", line)]
    assert imports == ["using Random"]


def test_julia_example_blocks_are_balanced():
    # A static stand-in for the parser where no Julia is installed: every
    # block opened at the start of a line is closed by its own "end" line.
    openers = closers = 0
    for line in _julia_code().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.match(r"(function|if|for|while|let|begin|try)\b", stripped):
            openers += 1
        if stripped == "end":
            closers += 1
    assert openers == closers > 10


def _to_julia(value):
    if value is None:
        return "nothing"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value).replace("$", "\\$")
    if isinstance(value, list):
        return "Any[" + ", ".join(_to_julia(v) for v in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(f"{_to_julia(k)} => {_to_julia(v)}" for k, v in value.items())
        return "Dict{String,Any}(" + items + ")"
    raise TypeError(value)


_JULIA_DRIVER = r'''
function tojson(io, x)
    if x === nothing
        print(io, "null")
    elseif x isa Bool
        print(io, x ? "true" : "false")
    elseif x isa Integer
        print(io, x)
    elseif x isa AbstractFloat
        print(io, isfinite(x) ? repr(Float64(x)) : "null")
    elseif x isa AbstractString
        print(io, '"')
        for c in x
            if c == '"'
                print(io, "\\\"")
            elseif c == '\\'
                print(io, "\\\\")
            elseif c == '\n'
                print(io, "\\n")
            elseif c < ' '
                print(io, "\\u", lpad(string(UInt32(c), base = 16), 4, '0'))
            else
                print(io, c)
            end
        end
        print(io, '"')
    elseif x isa AbstractDict
        print(io, "{")
        isfirst = true
        for (k, v) in x
            isfirst || print(io, ",")
            isfirst = false
            tojson(io, string(k))
            print(io, ":")
            tojson(io, v)
        end
        print(io, "}")
    else
        print(io, "[")
        for (i, v) in enumerate(x)
            i > 1 && print(io, ",")
            tojson(io, v)
        end
        print(io, "]")
    end
end

evaluator = Module(:Evaluator)
Base.include_string(evaluator, read(ARGS[1], String))

data = include_string(Main, read(ARGS[2], String))
result = Base.invokelatest(getproperty(evaluator, :evaluate), data)
tojson(stdout, result)
'''


@pytest.mark.skipif(shutil.which("julia") is None, reason="julia is not installed here")
def test_julia_example_runs(tmp_path):
    data = _input([], kind="proportion", unit="%", params={"draws": 4000})
    data["aggregates"] = [
        {"variant": "A", "rows": 12, "n": 10688, "value_sum": 129.0, "denominator_sum": None,
         "sum_sq": None, "estimate": 0.01207, "levels": None},
        {"variant": "B", "rows": 12, "n": 10714, "value_sum": 175.0, "denominator_sum": None,
         "sum_sq": None, "estimate": 0.016334, "levels": None},
        {"variant": None, "rows": 1, "n": 5, "value_sum": 1.0, "denominator_sum": None,
         "sum_sq": None, "estimate": 0.2, "levels": None},
    ]
    (tmp_path / "code.jl").write_text(_julia_code(), encoding="utf-8")
    (tmp_path / "data.jl").write_text(_to_julia(data), encoding="utf-8")
    (tmp_path / "driver.jl").write_text(_JULIA_DRIVER, encoding="utf-8")
    done = subprocess.run(
        ["julia", "--startup-file=no", "--history-file=no", str(tmp_path / "driver.jl"),
         str(tmp_path / "code.jl"), str(tmp_path / "data.jl")],
        capture_output=True, text=True, timeout=600,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    output = json.loads(done.stdout)
    assert set(output) == CONTRACT_KEYS
    assert output["verdict"] == "better"
    assert output["headline"].startswith("P(B besser als A) = ")
    comparison = output["comparisons"][0]
    assert comparison["unit"] == "Pp." and comparison["prob_better"] > 0.99
    assert math.isclose(comparison["estimate"], 175 / 10714 - 129 / 10688, abs_tol=0.002)
    assert [v["variant"] for v in output["variants"]] == ["A", "B"]
