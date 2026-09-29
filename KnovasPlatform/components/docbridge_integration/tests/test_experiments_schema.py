"""Experiment type definitions: validation, safe parsing, field values, scope
and the lifecycle rules (transitions, requirements, roles).

Type definitions are written by hand, so the tests pin both halves of the
job: everything the rest of the module could trip over is refused, and every
refusal names the path it belongs to.
"""

from __future__ import annotations

import copy
import json
import os
import time

import pytest
from jsonschema import Draft202012Validator

from experiments.errors import Forbidden, ValidationError
from experiments.schema import (
    FIELD_TYPES,
    REQUIREMENT_KEYS,
    TYPE_DEFINITION_SCHEMA,
    check_transition,
    display_field_value,
    evaluator_refs,
    initial_state,
    metric_refs,
    normalize_dims,
    parse_definition_text,
    phase_state,
    state_label,
    state_phase,
    transitions_from,
    validate_field_values,
    validate_metric_definition,
    validate_scope,
    validate_type_definition,
)

#: The example of the plan, section 4.
EXAMPLE = {
    "fields": [
        {"key": "channel", "label": "Kanal", "type": "enum", "options": ["LinkedIn", "E-Mail"],
         "required": False, "help": "Wo die Varianten laufen."},
    ],
    "states": [
        {"key": "draft", "label": "Entwurf"},
        {"key": "running", "label": "L\u00e4uft", "phase": "running"},
        {"key": "analysis", "label": "Auswertung"},
        {"key": "decided", "label": "Entschieden", "phase": "decided"},
        {"key": "stopped", "label": "Abgebrochen", "phase": "stopped"},
    ],
    "initial": "draft",
    "transitions": [
        {"from": "draft", "to": "running", "label": "Starten",
         "requires": ["hypothesis", "primary_metric", "variants:2"]},
        {"from": "running", "to": "analysis", "label": "Zur Auswertung", "requires": ["measurements"]},
        {"from": "analysis", "to": "decided", "label": "Entscheiden", "requires": ["decision"]},
        {"from": "*", "to": "stopped", "label": "Abbrechen"},
    ],
    "variants": {"min": 2, "max": 10,
                 "defaults": [{"key": "A", "name": "Kontrolle", "is_control": True},
                              {"key": "B", "name": "Variante B"}]},
    "metrics": [{"metric": "ctr", "role": "primary"},
                {"metric": "bounce_rate", "role": "guardrail", "op": "max", "value": 0.7}],
    "evaluation": [{"evaluator": "builtin.bayes_proportion", "metric": "primary", "params": {},
                    "scope": {}}],
    "decision": {"require_learning": True},
}

MINIMAL = {
    "states": [{"key": "open", "label": "Offen"}, {"key": "done", "label": "Erledigt"}],
    "transitions": [{"from": "open", "to": "done", "label": "Abschliessen"}],
}


def example(**changes):
    definition = copy.deepcopy(EXAMPLE)
    definition.update(changes)
    return definition


def refused(definition):
    with pytest.raises(ValidationError) as info:
        validate_type_definition(definition)
    return info.value


# -- vocabulary -----------------------------------------------------------------


def test_vocabulary():
    assert FIELD_TYPES == ("text", "longtext", "number", "integer", "enum", "multi_enum",
                           "date", "url", "boolean")
    assert set(REQUIREMENT_KEYS) == {"hypothesis", "primary_metric", "variants", "measurements",
                                     "evaluation", "decision", "learning", "field", "n_planned"}


def test_type_definition_schema_is_a_valid_json_schema():
    Draft202012Validator.check_schema(TYPE_DEFINITION_SCHEMA)


# -- validate_type_definition: accepted -------------------------------------------


def test_contract_example_is_valid_and_normalised():
    normalised = validate_type_definition(example())
    assert list(normalised) == ["fields", "states", "initial", "transitions", "variants",
                                "metrics", "evaluation", "decision"]
    assert normalised["fields"][0] == {
        "key": "channel", "label": "Kanal", "type": "enum", "required": False,
        "help": "Wo die Varianten laufen.", "options": ["LinkedIn", "E-Mail"],
    }
    assert normalised["transitions"][3] == {
        "from": "*", "to": "stopped", "label": "Abbrechen", "requires": [], "roles": [],
    }
    assert normalised["variants"]["defaults"][1] == {"key": "B", "name": "Variante B",
                                                     "is_control": False}
    assert normalised["metrics"][1] == {"metric": "bounce_rate", "role": "guardrail",
                                        "op": "max", "value": 0.7}
    assert normalised["decision"] == {"require_learning": True}


def test_normalisation_is_idempotent_and_conforms_to_the_schema():
    normalised = validate_type_definition(example())
    assert validate_type_definition(normalised) == normalised
    assert list(Draft202012Validator(TYPE_DEFINITION_SCHEMA).iter_errors(normalised)) == []
    assert json.loads(json.dumps(normalised)) == normalised


def test_input_is_not_modified():
    definition = example()
    before = copy.deepcopy(definition)
    validate_type_definition(definition)
    assert definition == before


def test_minimal_definition_gets_defaults():
    normalised = validate_type_definition(MINIMAL)
    assert normalised["fields"] == []
    assert normalised["initial"] == "open"
    assert normalised["variants"] == {"min": 0, "max": 10, "defaults": []}
    assert normalised["metrics"] == [] and normalised["evaluation"] == []
    assert normalised["decision"] == {"require_learning": False}


def test_default_initial_skips_terminal_states():
    definition = {
        "states": [{"key": "dropped", "label": "Verworfen", "phase": "stopped"},
                   {"key": "idea", "label": "Idee"}],
        "transitions": [{"from": "idea", "to": "dropped", "label": "Verwerfen"}],
    }
    assert validate_type_definition(definition)["initial"] == "idea"


def test_labels_and_options_are_trimmed():
    definition = example()
    definition["fields"][0]["label"] = "  Kanal "
    definition["fields"][0]["options"] = [" LinkedIn", "E-Mail "]
    definition["states"][0]["label"] = " Entwurf"
    normalised = validate_type_definition(definition)
    assert normalised["fields"][0]["label"] == "Kanal"
    assert normalised["fields"][0]["options"] == ["LinkedIn", "E-Mail"]
    assert normalised["states"][0]["label"] == "Entwurf"


def test_every_field_type_is_accepted():
    fields = [{"key": f"f_{t}", "label": t, "type": t} for t in FIELD_TYPES]
    for field in fields:
        if field["type"] in ("enum", "multi_enum"):
            field["options"] = ["a", "b"]
    fields.append({"key": "n", "label": "N", "type": "integer", "min": 1, "max": 10.0})
    normalised = validate_type_definition(example(fields=fields))
    assert [f["type"] for f in normalised["fields"][:len(FIELD_TYPES)]] == list(FIELD_TYPES)
    assert normalised["fields"][-1]["max"] == 10 and isinstance(normalised["fields"][-1]["max"], int)


def test_scope_in_evaluation_is_normalised():
    definition = example(evaluation=[{"evaluator": "builtin.paired_t", "metric": "ndcg_at_10",
                                      "params": {"pair_by": "query"},
                                      "scope": {"runs": "latest", "since": "2026-09-01",
                                                "dims": {}}}])
    entry = validate_type_definition(definition)["evaluation"][0]
    assert entry["scope"] == {"runs": "latest", "since": "2026-09-01T00:00:00+00:00"}


def test_roles_and_all_requirement_forms():
    definition = example()
    definition["fields"].append({"key": "planned_n", "label": "Geplant", "type": "integer"})
    definition["transitions"][2]["roles"] = ["experiments_manager"]
    definition["transitions"][1]["requires"] = [
        "measurements", "evaluation", "learning", "field:channel", "n_planned:planned_n",
    ]
    normalised = validate_type_definition(definition)
    assert normalised["transitions"][2]["roles"] == ["experiments_manager"]


# -- validate_type_definition: refused --------------------------------------------


def _states(n):
    return [{"key": f"s{i}", "label": f"S{i}"} for i in range(n)]


CASES = [
    # (description, mutate, path, message fragment)
    ("unknown top-level key", lambda d: d.update(foo=1), "foo", "Unbekannter Eintrag."),
    ("states missing", lambda d: d.pop("states"), "states", "Pflichtangabe fehlt."),
    ("too few states", lambda d: d.update(states=_states(1)), "states", "Mindestens 2"),
    ("too many states", lambda d: d.update(states=_states(13)), "states", "H\u00f6chstens 12"),
    ("bad state key", lambda d: d["states"][0].update(key="Draft"), "states[0].key",
     "Kleinbuchstaben"),
    ("state key too long", lambda d: d["states"][0].update(key="d" * 33), "states[0].key",
     "32 Zeichen"),
    ("state label empty", lambda d: d["states"][0].update(label=""), "states[0].label",
     "Darf nicht leer sein."),
    ("state label blank", lambda d: d["states"][0].update(label="   "), "states[0].label",
     "Darf nicht leer sein."),
    ("state label too long", lambda d: d["states"][0].update(label="x" * 41),
     "states[0].label", "H\u00f6chstens 40 Zeichen."),
    ("state label with newline", lambda d: d["states"][0].update(label="a\nb"),
     "states[0].label", "Steuerzeichen"),
    ("unknown phase", lambda d: d["states"][0].update(phase="done"), "states[0].phase",
     "Erlaubt sind: running, decided, stopped."),
    ("duplicate state", lambda d: d["states"][1].update(key="draft"), "states[1].key",
     "Den Status \u00abdraft\u00bb gibt es schon."),
    ("phase twice", lambda d: d["states"][2].update(phase="running"), "states[2].phase",
     "Die Phase \u00abrunning\u00bb hat schon der Status \u00abrunning\u00bb."),
    ("initial unknown", lambda d: d.update(initial="nope"), "initial",
     "Unbekannter Status \u00abnope\u00bb."),
    ("initial terminal", lambda d: d.update(initial="decided"), "initial",
     "entschiedenen oder abgebrochenen"),
    ("no transitions", lambda d: d.update(transitions=[]), "transitions",
     "Mindestens einen Eintrag."),
    ("too many transitions",
     lambda d: d.update(transitions=[{"from": "*", "to": "stopped", "label": "x"}] * 41),
     "transitions", "H\u00f6chstens 40"),
    ("transition from unknown", lambda d: d["transitions"][0].update(**{"from": "idea"}),
     "transitions[0].from", "Unbekannter Status \u00abidea\u00bb."),
    ("transition to unknown", lambda d: d["transitions"][0].update(to="idea"),
     "transitions[0].to", "Unbekannter Status \u00abidea\u00bb."),
    ("transition to itself", lambda d: d["transitions"][0].update(to="draft"),
     "transitions[0].to", "zwei verschiedene Status"),
    ("duplicate transition", lambda d: d["transitions"].append(dict(d["transitions"][0])),
     "transitions[4]", "Den Wechsel \u00abdraft\u00bb \u2192 \u00abrunning\u00bb gibt es schon."),
    ("bad from pattern", lambda d: d["transitions"][0].update(**{"from": "**"}),
     "transitions[0].from", "jeder andere Status"),
    ("transition label missing", lambda d: d["transitions"][0].pop("label"),
     "transitions[0].label", "Pflichtangabe fehlt."),
    ("unknown requirement", lambda d: d["transitions"][0].update(requires=["budget"]),
     "transitions[0].requires[0]", "Erlaubt sind hypothesis"),
    ("variants:0", lambda d: d["transitions"][0].update(requires=["variants:0"]),
     "transitions[0].requires[0]", "Erlaubt sind hypothesis"),
    ("variants above max", lambda d: d["transitions"][0].update(requires=["variants:11"]),
     "transitions[0].requires[0]", "h\u00f6chstens 10 Varianten"),
    ("field requirement unknown", lambda d: d["transitions"][0].update(requires=["field:nope"]),
     "transitions[0].requires[0]", "Unbekanntes Feld \u00abnope\u00bb."),
    ("n_planned on an enum", lambda d: d["transitions"][0].update(requires=["n_planned:channel"]),
     "transitions[0].requires[0]", "braucht ein Zahlenfeld"),
    ("n_planned unknown", lambda d: d["transitions"][0].update(requires=["n_planned:nope"]),
     "transitions[0].requires[0]", "Unbekanntes Feld \u00abnope\u00bb."),
    ("duplicate requirement", lambda d: d["transitions"][0].update(requires=["hypothesis"] * 2),
     "transitions[0].requires", "nicht wiederholen"),
    ("admin is no transition role", lambda d: d["transitions"][0].update(roles=["admin"]),
     "transitions[0].roles[0]", "experimenter, experiments_manager"),
    ("field key pattern", lambda d: d["fields"][0].update(key="Kanal"), "fields[0].key",
     "Kleinbuchstaben"),
    ("duplicate field", lambda d: d["fields"].append(dict(d["fields"][0])), "fields[1].key",
     "Das Feld \u00abchannel\u00bb gibt es schon."),
    ("enum without options", lambda d: d["fields"][0].pop("options"), "fields[0].options",
     "Pflichtangabe fehlt."),
    ("options on text", lambda d: d["fields"][0].update(type="text"), "fields[0].options",
     "Optionen gibt es nur bei Auswahlfeldern"),
    ("empty options", lambda d: d["fields"][0].update(options=[]), "fields[0].options",
     "Mindestens einen Eintrag."),
    ("too many options", lambda d: d["fields"][0].update(options=[str(i) for i in range(51)]),
     "fields[0].options", "H\u00f6chstens 50"),
    ("duplicate options", lambda d: d["fields"][0].update(options=["a", "a"]),
     "fields[0].options", "nicht wiederholen"),
    ("duplicate options after trim", lambda d: d["fields"][0].update(options=["a", " a"]),
     "fields[0].options", "nicht wiederholen"),
    ("blank option", lambda d: d["fields"][0].update(options=["a", "  "]),
     "fields[0].options", "Leere Optionen"),
    ("option too long", lambda d: d["fields"][0].update(options=["x" * 81]),
     "fields[0].options[0]", "H\u00f6chstens 80 Zeichen."),
    ("unknown field type", lambda d: d["fields"][0].update(type="money"), "fields[0].type",
     "Erlaubt sind: text"),
    ("min on enum", lambda d: d["fields"][0].update(min=1), "fields[0].min",
     "nur bei Zahlenfeldern"),
    ("fractional integer bound",
     lambda d: d["fields"].append({"key": "n", "label": "N", "type": "integer", "min": 1.5}),
     "fields[1].min", "Muss eine ganze Zahl sein."),
    ("min above max",
     lambda d: d["fields"].append({"key": "n", "label": "N", "type": "number", "min": 5, "max": 1}),
     "fields[1].min", "Das Minimum ist gr\u00f6sser als das Maximum."),
    ("required not bool", lambda d: d["fields"][0].update(required="ja"), "fields[0].required",
     "Muss true oder false sein."),
    ("help too long", lambda d: d["fields"][0].update(help="x" * 301), "fields[0].help",
     "H\u00f6chstens 300 Zeichen."),
    ("label too long", lambda d: d["fields"][0].update(label="x" * 81), "fields[0].label",
     "H\u00f6chstens 80 Zeichen."),
    ("too many fields",
     lambda d: d.update(fields=[{"key": f"f{i}", "label": "F", "type": "text"} for i in range(41)]),
     "fields", "H\u00f6chstens 40"),
    ("unknown field property", lambda d: d["fields"][0].update(default="x"),
     "fields[0].default", "Unbekannter Eintrag."),
    ("variants min too high", lambda d: d["variants"].update(min=11), "variants.min",
     "H\u00f6chstens 10."),
    ("variants max zero", lambda d: d["variants"].update(max=0), "variants.max", "Mindestens 1."),
    ("variants min above max", lambda d: d["variants"].update(min=5, max=3), "variants.min",
     "Das Minimum ist gr\u00f6sser als das Maximum."),
    ("more defaults than max", lambda d: d["variants"].update(max=1), "variants.defaults",
     "h\u00f6chstens 1"),
    ("two controls", lambda d: d["variants"]["defaults"][1].update(is_control=True),
     "variants.defaults[1].is_control", "Nur eine Variante kann die Kontrolle sein."),
    ("duplicate default", lambda d: d["variants"]["defaults"][1].update(key="A"),
     "variants.defaults[1].key", "Die Variante \u00abA\u00bb gibt es schon."),
    ("bad variant key", lambda d: d["variants"]["defaults"][1].update(key="-B"),
     "variants.defaults[1].key", "Buchstaben, Ziffern"),
    ("allocation above one", lambda d: d["variants"]["defaults"][0].update(allocation=1.5),
     "variants.defaults[0].allocation", "H\u00f6chstens 1."),
    ("allocations sum above one",
     lambda d: [v.update(allocation=0.6) for v in d["variants"]["defaults"]],
     "variants.defaults", "Die Anteile ergeben zusammen mehr als 1."),
    ("variant name too long", lambda d: d["variants"]["defaults"][0].update(name="x" * 121),
     "variants.defaults[0].name", "H\u00f6chstens 120 Zeichen."),
    ("guardrail without op", lambda d: d["metrics"][1].pop("op"), "metrics[1].op",
     "Pflichtangabe fehlt."),
    ("op on secondary", lambda d: d["metrics"][0].update(role="secondary", op="max"),
     "metrics[0].op", "Nur Leitplanken"),
    ("two primaries", lambda d: d["metrics"].__setitem__(1, {"metric": "clicks", "role": "primary"}),
     "metrics[1].role", "Es kann nur eine prim\u00e4re Metrik geben."),
    ("duplicate metric", lambda d: d["metrics"].append({"metric": "ctr", "role": "secondary"}),
     "metrics[2].metric", "Die Metrik \u00abctr\u00bb steht schon in der Liste."),
    ("reserved metric key", lambda d: d["metrics"].append({"metric": "all", "role": "secondary"}),
     "metrics[2].metric", "\u00aball\u00bb ist kein Metrik-Schl\u00fcssel."),
    ("bad metric key", lambda d: d["metrics"][0].update(metric="CTR"), "metrics[0].metric",
     "Kleinbuchstaben"),
    ("unknown role", lambda d: d["metrics"][0].update(role="main"), "metrics[0].role",
     "Erlaubt sind: primary, secondary, guardrail."),
    ("too many metrics",
     lambda d: d.update(metrics=[{"metric": f"m{i}", "role": "secondary"} for i in range(31)]),
     "metrics", "H\u00f6chstens 30"),
    ("bad evaluator key", lambda d: d["evaluation"][0].update(evaluator="Builtin.x"),
     "evaluation[0].evaluator", "Kleinbuchstaben, Ziffern"),
    ("evaluation without metric", lambda d: d["evaluation"][0].pop("metric"),
     "evaluation[0].metric", "Pflichtangabe fehlt."),
    ("params not an object", lambda d: d["evaluation"][0].update(params=[1]),
     "evaluation[0].params", "Muss ein Objekt sein."),
    ("params too large", lambda d: d["evaluation"][0].update(params={"x": "y" * 5000}),
     "evaluation[0].params", "gr\u00f6sser als 4 KB"),
    ("scope with unknown key", lambda d: d["evaluation"][0].update(scope={"last": 3}),
     "evaluation[0].scope.last", "Unbekannter Eintrag."),
    ("scope bad run id", lambda d: d["evaluation"][0].update(scope={"runs": ["x"]}),
     "evaluation[0].scope.runs[0]", "Keine g\u00fcltige Lauf-ID."),
    ("duplicate evaluation", lambda d: d["evaluation"].append(dict(d["evaluation"][0])),
     "evaluation[1]", "Diese Auswertung steht schon in der Liste."),
    ("too many evaluations",
     lambda d: d.update(evaluation=[{"evaluator": "builtin.describe", "metric": f"m{i}"}
                                    for i in range(21)]),
     "evaluation", "H\u00f6chstens 20"),
    ("require_learning not bool", lambda d: d["decision"].update(require_learning="yes"),
     "decision.require_learning", "Muss true oder false sein."),
    ("NaN guardrail", lambda d: d["metrics"][1].update(value=float("nan")), "metrics[1].value",
     "Nur endliche Zahlen sind erlaubt."),
    ("NUL in a label", lambda d: d["states"][0].update(label="a\x00b"), "states[0].label",
     "Nullzeichen"),
    ("lone surrogate", lambda d: d["states"][0].update(label="a\ud800"), "states[0].label",
     "ung\u00fcltige Zeichen"),
    ("unsupported value", lambda d: d["decision"].update(require_learning={1, 2}),
     "decision.require_learning", "Nicht unterst\u00fctzter Wert."),
    ("non-text key", lambda d: d["decision"].update({None: True}), "decision.None",
     "Schl\u00fcssel m\u00fcssen Text sein."),
]


@pytest.mark.parametrize("description, mutate, path, fragment", CASES,
                         ids=[c[0] for c in CASES])
def test_definition_refused_with_path(description, mutate, path, fragment):
    definition = example()
    mutate(definition)
    error = refused(definition)
    assert path in error.fields, error.fields
    assert fragment in error.fields[path], error.fields[path]
    assert error.message.startswith("Die Typdefinition ist ung\u00fcltig")


def test_root_must_be_an_object():
    error = refused([1, 2])
    assert error.fields == {"definition": "Muss ein Objekt sein."}


def test_no_possible_initial_state():
    error = refused({
        "states": [{"key": "a", "label": "A", "phase": "decided"},
                   {"key": "b", "label": "B", "phase": "stopped"}],
        "transitions": [{"from": "a", "to": "b", "label": "x"}],
    })
    assert "initial" in error.fields


def test_one_error_is_repeated_in_the_message():
    error = refused(example(foo=1))
    assert error.message == "Die Typdefinition ist ung\u00fcltig: foo: Unbekannter Eintrag."


def test_several_errors_are_counted_in_the_message():
    error = refused(example(foo=1, bar=2))
    assert error.message.startswith("Die Typdefinition ist ung\u00fcltig (2 Fehler). ")
    assert set(error.fields) == {"foo", "bar"}


def test_reported_errors_are_capped():
    definition = example(fields=[{"key": f"F{i}", "label": "", "type": "x"} for i in range(40)])
    error = refused(definition)
    assert len(error.fields) <= 50


def test_excessive_nesting_is_refused():
    params = {}
    inner = params
    for _ in range(40):
        inner["x"] = {}
        inner = inner["x"]
    error = refused(example(evaluation=[{"evaluator": "builtin.describe", "metric": "all",
                                         "params": params}]))
    assert any("Zu tief verschachtelt." == m for m in error.fields.values())


def test_huge_definitions_are_refused_quickly():
    definition = example(fields=[{"key": "a", "label": "A", "type": "text"}] * 200_000)
    started = time.monotonic()
    error = refused(definition)
    assert time.monotonic() - started < 2
    assert "zu gross" in error.fields["definition"]


def test_fewer_default_variants_than_the_minimum_are_refused():
    # review-backend-3: a new experiment starts with the defaults (the create
    # form sends no variants), so such a type could never be instantiated.
    definition = example()
    definition["variants"]["defaults"] = definition["variants"]["defaults"][:1]
    error = refused(definition)
    assert error.fields["variants.defaults"].startswith(
        "Weniger Vorgaben als Varianten verlangt sind (mindestens 2).")
    error = refused(example(variants={"min": 2, "max": 4}))
    assert "variants.defaults" in error.fields
    assert validate_type_definition(example(variants={"min": 0, "max": 4}))["variants"]["defaults"] == []
    # min above max is reported once, on min.
    error = refused(example(variants={"min": 5, "max": 4}))
    assert "variants.min" in error.fields and "variants.defaults" not in error.fields


def test_integral_floats_count_as_integers():
    normalised = validate_type_definition(example(variants={
        "min": 2.0, "max": 4.0, "defaults": [{"key": "A"}, {"key": "B"}]}))
    assert normalised["variants"]["min"] == 2 and isinstance(normalised["variants"]["min"], int)


# -- parse_definition_text ----------------------------------------------------------

YAML_TEXT = """
states:
  - {key: draft, label: Entwurf}
  - {key: running, label: L\u00e4uft, phase: running}
  - {key: stopped, label: Abgebrochen, phase: stopped}
initial: draft
transitions:
  - {from: draft, to: running, label: Starten, requires: [hypothesis]}
  - {from: "*", to: stopped, label: Abbrechen}
evaluation:
  - {evaluator: builtin.describe, metric: all, scope: {since: 2026-09-01}}
"""


def test_yaml_and_json_text_give_the_same_definition():
    from_yaml = parse_definition_text(YAML_TEXT)
    import yaml

    as_json = json.dumps(yaml.safe_load(YAML_TEXT.replace("2026-09-01", "'2026-09-01'")))
    assert parse_definition_text(as_json) == from_yaml
    assert from_yaml["evaluation"][0]["scope"] == {"since": "2026-09-01T00:00:00+00:00"}
    assert from_yaml["states"][1]["label"] == "L\u00e4uft"


def test_parse_accepts_bytes_and_a_bom():
    assert parse_definition_text(("\ufeff" + YAML_TEXT).encode("utf-8"))["initial"] == "draft"


def test_alias_is_refused_with_the_contract_message():
    with pytest.raises(ValidationError) as info:
        parse_definition_text("a: &x 1\nb: *x\n")
    assert info.value.message == "Anker und Verweise (&/*) sind nicht erlaubt."


def test_anchor_alone_is_refused():
    with pytest.raises(ValidationError) as info:
        parse_definition_text("states: &s []\n")
    assert info.value.message == "Anker und Verweise (&/*) sind nicht erlaubt."


def test_alias_bomb_is_refused_before_expansion():
    bomb = "a: &a [x, x, x, x, x, x, x, x, x]\n" + "".join(
        f"{chr(98 + i)}: &{chr(98 + i)} [*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, "
        f"*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}]\n"
        for i in range(9)
    )
    started = time.monotonic()
    with pytest.raises(ValidationError) as info:
        parse_definition_text(bomb)
    assert time.monotonic() - started < 1
    assert info.value.message == "Anker und Verweise (&/*) sind nicht erlaubt."


def test_merge_keys_are_refused():
    with pytest.raises(ValidationError) as info:
        parse_definition_text("x:\n  <<: {a: 1}\n  b: 2\n")
    assert "<<" in info.value.message


def test_python_tags_are_refused_and_never_executed(monkeypatch):
    called = []
    monkeypatch.setattr(os, "system", lambda *a, **k: called.append(a))
    with pytest.raises(ValidationError) as info:
        parse_definition_text("!!python/object/apply:os.system ['echo pwned']\n")
    assert "nicht erlaubten Typ" in info.value.message
    assert called == []


@pytest.mark.parametrize("text", [
    "x: !!python/name:os.system\n",
    "x: !custom value\n",
    "!!python/object/new:dict {}\n",
])
def test_other_unsafe_tags_are_refused(text):
    with pytest.raises(ValidationError):
        parse_definition_text(text)


def test_binary_and_sets_are_not_json():
    with pytest.raises(ValidationError) as info:
        parse_definition_text("x: !!binary aGVsbG8=\n")
    assert "Nicht unterst\u00fctzter Wert." in info.value.fields.values()
    with pytest.raises(ValidationError):
        parse_definition_text("x: !!set {a, b}\n")


def test_duplicate_keys_are_refused_in_yaml_and_json():
    with pytest.raises(ValidationError) as info:
        parse_definition_text("initial: draft\ninitial: running\n")
    assert info.value.message == "Der Schl\u00fcssel \u00abinitial\u00bb kommt doppelt vor (Zeile 2)."
    with pytest.raises(ValidationError) as info:
        parse_definition_text('{"initial": "a", "initial": "b"}')
    assert info.value.message == "Der Schl\u00fcssel \u00abinitial\u00bb kommt doppelt vor."


@pytest.mark.parametrize("text", [
    '{"states": NaN}',
    '{"states": [1e400]}',
    '{"states": -Infinity}',
    "states: .inf\n",
    "states: [.nan]\n",
])
def test_non_finite_numbers_are_refused(text):
    with pytest.raises(ValidationError) as info:
        parse_definition_text(text)
    assert "Nur endliche Zahlen sind erlaubt." in info.value.fields.values()


def test_yaml_boolean_keys_are_refused():
    with pytest.raises(ValidationError) as info:
        parse_definition_text("on: 1\n")
    assert "Schl\u00fcssel m\u00fcssen Text sein." in info.value.fields.values()


def test_size_cap_before_parsing():
    text = "x: " + "a" * (200 * 1024)
    with pytest.raises(ValidationError) as info:
        parse_definition_text(text)
    assert info.value.message == "Die Typdefinition ist zu gross (h\u00f6chstens 200 KB)."
    with pytest.raises(ValidationError):
        parse_definition_text(text.encode("utf-8"))
    # Multi-byte characters count as bytes.
    with pytest.raises(ValidationError):
        parse_definition_text("x: " + "\u00e4" * (101 * 1024))


@pytest.mark.parametrize("text, message", [
    ("", "Die Typdefinition ist leer."),
    ("   \n  ", "Die Typdefinition ist leer."),
    (None, "Die Typdefinition fehlt."),
    (b"\xff\xfe", "Die Typdefinition ist kein g\u00fcltiger UTF-8-Text."),
])
def test_empty_or_unreadable_text(text, message):
    with pytest.raises(ValidationError) as info:
        parse_definition_text(text)
    assert info.value.message == message


def test_syntax_errors_name_line_and_column():
    with pytest.raises(ValidationError) as info:
        parse_definition_text("states:\n  - {key: a\n")
    assert info.value.message.startswith("Das YAML ist fehlerhaft (Zeile ")
    with pytest.raises(ValidationError) as info:
        parse_definition_text('{"states": [}')
    assert info.value.message == "Das JSON ist fehlerhaft (Zeile 1, Spalte 13)."


def test_multiple_documents_are_refused():
    with pytest.raises(ValidationError):
        parse_definition_text("a: 1\n---\nb: 2\n")


def test_top_level_list_is_refused():
    with pytest.raises(ValidationError) as info:
        parse_definition_text("- a\n- b\n")
    assert info.value.fields == {"definition": "Muss ein Objekt sein."}


@pytest.mark.parametrize("text", ["[" * 50_000, "{\"a\": " * 20_000, "x: " + "[" * 50_000])
def test_deep_nesting_in_text_is_a_validation_error(text):
    with pytest.raises(ValidationError):
        parse_definition_text(text)


def test_giant_integer_is_a_validation_error():
    with pytest.raises(ValidationError):
        parse_definition_text("x: " + "9" * 5000 + "\n")


def test_parsed_text_is_validated():
    with pytest.raises(ValidationError) as info:
        parse_definition_text("states: []\ntransitions: []\n")
    assert "states" in info.value.fields


# -- validate_metric_definition -------------------------------------------------------


def test_metric_definition_defaults_and_accepted_shapes():
    assert validate_metric_definition("mean", None) == {}
    assert validate_metric_definition("proportion", {}) == {}
    assert validate_metric_definition("mean", {"decimals": 3, "min": 0, "max": 1}) == {
        "decimals": 3, "min": 0, "max": 1,
    }


@pytest.mark.parametrize("definition, path, fragment", [
    ({"decimals": 7}, "decimals", "H\u00f6chstens 6."),
    ({"decimals": -1}, "decimals", "Mindestens 0."),
    ({"decimals": 2.5}, "decimals", "Muss eine ganze Zahl sein."),
    ({"decimals": True}, "decimals", "Muss eine ganze Zahl sein."),
    ({"min": "0"}, "min", "Muss eine Zahl sein."),
    ({"min": 1, "max": 1}, "min", "kleiner als das Maximum"),
    ({"_name": "x"}, "_name", "Unbekannter Eintrag."),
    ({"unit": "ms"}, "unit", "Unbekannter Eintrag."),
])
def test_metric_definition_refused(definition, path, fragment):
    with pytest.raises(ValidationError) as info:
        validate_metric_definition("mean", definition)
    assert fragment in info.value.fields[path]


def test_bounds_only_for_bounded_kinds():
    with pytest.raises(ValidationError) as info:
        validate_metric_definition("proportion", {"min": 0})
    assert "nur bei Mittelwert" in info.value.fields["min"]
    assert validate_metric_definition("ordinal", {"min": 1, "max": 5}) == {"min": 1, "max": 5}


def test_levels_are_required_for_categorical_and_refused_elsewhere():
    with pytest.raises(ValidationError) as info:
        validate_metric_definition("categorical", {})
    assert "levels" in info.value.fields
    with pytest.raises(ValidationError) as info:
        validate_metric_definition("mean", {"levels": {"1": "a", "2": "b"}})
    assert "Stufen gibt es nur" in info.value.fields["levels"]


def test_level_keys_are_canonical_and_sorted_by_value():
    result = validate_metric_definition(
        "ordinal", {"levels": {"10": "zehn", "9": "neun", "3.0": "drei", "01": "eins", "2.5": "x"}}
    )
    assert list(result["levels"]) == ["1", "2.5", "3", "9", "10"]
    assert result["levels"]["3"] == "drei"


@pytest.mark.parametrize("kind, levels, fragment", [
    ("ordinal", {"1": "a"}, "Mindestens 2"),
    ("ordinal", {str(i): "x" for i in range(51)}, "H\u00f6chstens 50"),
    ("ordinal", {"a": "x", "1": "y"}, "mit Zahlen bezeichnet"),
    ("ordinal", {"1e3": "x", "1": "y"}, "mit Zahlen bezeichnet"),
    ("ordinal", {"1": "a", "1.0": "b"}, "kommt doppelt vor"),
    ("ordinal", {"1": "", "2": "b"}, "Darf nicht leer sein."),
    ("ordinal", {"1": "  ", "2": "b"}, "Darf nicht leer sein."),
    ("ordinal", {"1": "x" * 81, "2": "b"}, "H\u00f6chstens 80 Zeichen."),
    ("categorical", {"1.5": "a", "2": "b"}, "ganzen Zahlen"),
])
def test_levels_refused(kind, levels, fragment):
    with pytest.raises(ValidationError) as info:
        validate_metric_definition(kind, {"levels": levels})
    assert any(fragment in m for m in info.value.fields.values()), info.value.fields


def test_levels_must_lie_within_bounds():
    with pytest.raises(ValidationError) as info:
        validate_metric_definition("ordinal", {"min": 1, "max": 5, "levels": {"0": "a", "1": "b"}})
    assert "levels.0" in info.value.fields


def test_unknown_kind_and_non_object_definition():
    with pytest.raises(ValidationError) as info:
        validate_metric_definition("quantile", {})
    assert "kind" in info.value.fields
    with pytest.raises(ValidationError):
        validate_metric_definition("mean", [1])


# -- validate_scope and dims --------------------------------------------------------


def test_empty_scope():
    assert validate_scope(None) == {}
    assert validate_scope({}) == {}


def test_scope_runs():
    assert validate_scope({"runs": "latest"}) == {"runs": "latest"}
    upper = "6F9619FF-8B86-D011-B42D-00C04FC964FF"
    result = validate_scope({"runs": [upper, upper.lower()]})
    assert result == {"runs": [upper.lower()]}


@pytest.mark.parametrize("scope, path", [
    ({"runs": []}, "runs"),
    ({"runs": "all"}, "runs"),
    ({"runs": ["not-a-uuid"]}, "runs[0]"),
    ({"runs": [7]}, "runs"),
    ({"since": "yesterday"}, "since"),
    ({"since": "2026-02-30"}, "since"),
    ({"until": 20260901}, "until"),
    ({"since": "2026-09-02", "until": "2026-09-01"}, "until"),
    ({"latest": True}, "latest"),
    ({"dims": {"bad key": "x"}}, "dims.bad key"),
    ({"dims": {"query": True}}, "dims.query"),
    ({"dims": {"query": None}}, "dims.query"),
    ({"dims": {"query": "x" * 201}}, "dims.query"),
    ({"dims": {f"k{i}": "v" for i in range(21)}}, "dims"),
    ({"dims": ["query"]}, "dims"),
])
def test_scope_refused(scope, path):
    with pytest.raises(ValidationError) as info:
        validate_scope(scope)
    assert path in info.value.fields, info.value.fields
    assert info.value.message.startswith("Der Datenbereich ist ung\u00fcltig")


def test_scope_times_are_utc_iso():
    result = validate_scope({"since": "2026-09-01", "until": "2026-09-30T18:00:00+02:00"})
    assert result == {"since": "2026-09-01T00:00:00+00:00", "until": "2026-09-30T16:00:00+00:00"}
    assert validate_scope({"since": "01.09.2026"}) == {"since": "2026-09-01T00:00:00+00:00"}
    assert validate_scope({"since": "2026-09-01T08:00:00"}) == {"since": "2026-09-01T08:00:00+00:00"}
    assert validate_scope({"since": "2026-09-01T08:00:00Z"})["since"] == "2026-09-01T08:00:00+00:00"


def test_scope_date_out_of_range_is_refused_not_raised():
    with pytest.raises(ValidationError):
        validate_scope({"since": "0001-01-01T00:00:00+01:00"})


def test_scope_dims_are_stringified_and_empty_dims_dropped():
    assert validate_scope({"dims": {"query": 17, "k": 17.0, "x": 2.5, "s": "a"}}) == {
        "dims": {"query": "17", "k": "17", "x": "2.5", "s": "a"}
    }
    assert validate_scope({"dims": {}}) == {}


def test_normalize_dims():
    assert normalize_dims(None) == {}
    assert normalize_dims({"query": 3, "run.name": "nightly"}) == {"query": "3", "run.name": "nightly"}
    with pytest.raises(ValidationError) as info:
        normalize_dims({"bad key": 1})
    assert "dims.bad key" in info.value.fields


# -- field values ---------------------------------------------------------------------

FIELDS = {"fields": [
    {"key": "channel", "label": "Kanal", "type": "enum", "options": ["LinkedIn", "E-Mail"],
     "required": True, "help": ""},
    {"key": "tags", "label": "Formate", "type": "multi_enum", "options": ["Text", "Bild", "Video"],
     "required": False, "help": ""},
    {"key": "note", "label": "Notiz", "type": "text", "required": False, "help": ""},
    {"key": "goal", "label": "Ziel", "type": "longtext", "required": False, "help": ""},
    {"key": "budget", "label": "Budget", "type": "number", "min": 0, "required": False, "help": ""},
    {"key": "planned_n", "label": "Geplant", "type": "integer", "min": 1, "max": 100000,
     "required": False, "help": ""},
    {"key": "start", "label": "Start", "type": "date", "required": False, "help": ""},
    {"key": "link", "label": "Link", "type": "url", "required": False, "help": ""},
    {"key": "paid", "label": "Bezahlt", "type": "boolean", "required": False, "help": ""},
]}


def test_complete_field_values():
    values = validate_field_values(FIELDS, {
        "channel": "linkedin", "tags": ["Video", "Text", "video"], "note": "  kurz ",
        "goal": "Zeile 1\nZeile 2", "budget": "1'250,5", "planned_n": "400", "start": "01.09.2026",
        "link": "https://example.test/a?b=c", "paid": "ja",
    })
    assert values == {
        "channel": "LinkedIn", "tags": ["Text", "Video"], "note": "kurz",
        "goal": "Zeile 1\nZeile 2", "budget": 1250.5, "planned_n": 400, "start": "2026-09-01",
        "link": "https://example.test/a?b=c", "paid": True,
    }


def test_numbers_come_back_as_int_when_whole():
    values = validate_field_values(FIELDS, {"channel": "E-Mail", "budget": 5000.0, "planned_n": 3.0})
    assert values["budget"] == 5000 and isinstance(values["budget"], int)
    assert values["planned_n"] == 3 and isinstance(values["planned_n"], int)


def test_empty_values_are_dropped_on_create():
    values = validate_field_values(FIELDS, {"channel": "E-Mail", "note": "", "tags": [],
                                            "budget": None})
    assert values == {"channel": "E-Mail"}


def test_required_missing_on_create():
    with pytest.raises(ValidationError) as info:
        validate_field_values(FIELDS, {})
    assert info.value.fields == {"channel": "Bitte \u00abKanal\u00bb ausf\u00fcllen."}
    assert info.value.message == "\u00abKanal\u00bb: Bitte \u00abKanal\u00bb ausf\u00fcllen."


def test_partial_update_checks_only_sent_keys_and_marks_removals():
    assert validate_field_values(FIELDS, {"note": "", "budget": 12}, partial=True) == {
        "note": None, "budget": 12,
    }
    assert validate_field_values(FIELDS, {}, partial=True) == {}


def test_required_field_cannot_be_cleared():
    with pytest.raises(ValidationError) as info:
        validate_field_values(FIELDS, {"channel": ""}, partial=True)
    assert info.value.fields == {"channel": "Bitte \u00abKanal\u00bb ausf\u00fcllen."}


def test_false_is_a_value_not_a_removal():
    values = validate_field_values(FIELDS, {"paid": False}, partial=True)
    assert values == {"paid": False}


@pytest.mark.parametrize("key, value, fragment", [
    ("channel", "Fax", "keine der vorgesehenen Optionen"),
    ("channel", 3, "Muss Text sein."),
    ("tags", ["Text", "Audio"], "\u00abAudio\u00bb ist keine"),
    ("tags", {"a": 1}, "Muss eine Liste sein."),
    ("note", "x" * 501, "H\u00f6chstens 500 Zeichen."),
    ("note", 12, "Muss Text sein."),
    ("note", "a\x00b", "Nullzeichen"),
    ("goal", "x" * 20001, "H\u00f6chstens 20'000 Zeichen."),
    ("budget", "abc", "Muss eine endliche Zahl sein."),
    ("budget", float("nan"), "Muss eine endliche Zahl sein."),
    ("budget", "inf", "Muss eine endliche Zahl sein."),
    ("budget", "1e999", "Muss eine endliche Zahl sein."),
    ("budget", True, "Muss eine endliche Zahl sein."),
    ("budget", -1, "Mindestens 0."),
    ("planned_n", 2.5, "Muss eine ganze Zahl sein."),
    ("planned_n", 0, "Erlaubt ist 1 bis 100'000."),
    ("planned_n", 2 ** 60, "zu gross"),
    ("start", "2026-02-30", "Kein g\u00fcltiges Datum"),
    ("start", "tomorrow", "Kein g\u00fcltiges Datum"),
    ("link", "javascript:alert(1)", "http:// oder https://"),
    ("link", "ftp://example.test", "http:// oder https://"),
    ("link", "https://", "http:// oder https://"),
    ("link", "https://exa mple.test", "Leer- oder Steuerzeichen"),
    ("link", "https://example.test/" + "a" * 2000, "H\u00f6chstens 2'000 Zeichen."),
    ("paid", "vielleicht", "Muss ja oder nein sein."),
    ("paid", 1, "Muss ja oder nein sein."),
])
def test_field_value_refused(key, value, fragment):
    with pytest.raises(ValidationError) as info:
        validate_field_values(FIELDS, {key: value}, partial=True)
    assert fragment in info.value.fields[key], info.value.fields


def test_unknown_field_and_non_object_values():
    with pytest.raises(ValidationError) as info:
        validate_field_values(FIELDS, {"channel": "E-Mail", "colour": "rot"})
    assert info.value.fields == {"colour": "Unbekanntes Feld."}
    with pytest.raises(ValidationError) as info:
        validate_field_values(FIELDS, ["channel"])  # type: ignore[arg-type]
    assert info.value.message == "Die Angaben m\u00fcssen ein Objekt sein."


def test_several_field_errors():
    with pytest.raises(ValidationError) as info:
        validate_field_values(FIELDS, {"budget": "x", "paid": "x"})
    assert info.value.message == "Bitte die markierten Angaben pr\u00fcfen."
    assert set(info.value.fields) == {"budget", "paid", "channel"}


def test_type_without_fields_accepts_only_empty_values():
    assert validate_field_values({"fields": []}, {}) == {}
    assert validate_field_values({"fields": []}, None) == {}  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        validate_field_values({"fields": []}, {"x": 1})


@pytest.mark.parametrize("field, value, text", [
    ({"type": "boolean"}, True, "ja"),
    ({"type": "boolean"}, False, "nein"),
    ({"type": "date"}, "2026-09-01", "01.09.2026"),
    ({"type": "number"}, 1234.5, "1'234,5"),
    ({"type": "integer"}, 1000, "1'000"),
    ({"type": "multi_enum"}, ["Text", "Bild"], "Text, Bild"),
    ({"type": "enum"}, "LinkedIn", "LinkedIn"),
    ({"type": "text"}, "hallo", "hallo"),
    ({"type": "url"}, "https://example.test", "https://example.test"),
    ({"type": "text"}, None, "\u2013"),
    ({"type": "text"}, "", "\u2013"),
    ({"type": "multi_enum"}, [], "\u2013"),
])
def test_display_field_value(field, value, text):
    assert display_field_value(field, value) == text


# -- lifecycle ------------------------------------------------------------------------

AB = validate_type_definition(example())


def test_state_helpers():
    assert initial_state(AB) == "draft"
    assert state_label(AB, "running") == "L\u00e4uft"
    assert state_label(AB, "gone") == "gone"
    assert state_phase(AB, "decided") == "decided"
    assert state_phase(AB, "analysis") is None
    assert phase_state(AB, "stopped") == "stopped"
    assert phase_state(AB, "paused") is None


def test_initial_state_of_a_definition_without_initial():
    assert initial_state({"states": [{"key": "x", "label": "X"}]}) == "x"


def test_transitions_from_expands_star_except_into_itself():
    assert [t["to"] for t in transitions_from(AB, "draft")] == ["running", "stopped"]
    assert [t["to"] for t in transitions_from(AB, "analysis")] == ["decided", "stopped"]
    assert [t["to"] for t in transitions_from(AB, "decided")] == ["stopped"]
    assert transitions_from(AB, "stopped") == []
    assert transitions_from(AB, "nowhere") == []
    assert transitions_from(AB, "draft")[0] == {
        "to": "running", "label": "Starten",
        "requires": ["hypothesis", "primary_metric", "variants:2"], "roles": [],
    }


def test_explicit_transition_wins_over_star():
    definition = example()
    definition["transitions"].append({"from": "draft", "to": "stopped", "label": "Verwerfen",
                                      "roles": ["experiments_manager"]})
    normalised = validate_type_definition(definition)
    from_draft = {t["to"]: t for t in transitions_from(normalised, "draft")}
    assert from_draft["stopped"]["label"] == "Verwerfen"
    assert from_draft["stopped"]["roles"] == ["experiments_manager"]
    assert transitions_from(normalised, "running")[-1]["label"] == "Abbrechen"


FULL_FACTS = {
    "hypothesis": True, "primary_metric": True, "variants": 2, "measurements": 10,
    "evaluations": 1, "decision": True, "learning": True, "fields": {}, "variant_n": {},
}


def test_undefined_transition_is_refused():
    with pytest.raises(ValidationError) as info:
        check_transition(AB, "draft", "decided", facts=FULL_FACTS, roles=frozenset())
    assert info.value.message == "Dieser Statuswechsel ist nicht vorgesehen."
    with pytest.raises(ValidationError):
        check_transition(AB, "stopped", "stopped", facts=FULL_FACTS, roles=frozenset())
    with pytest.raises(ValidationError):
        check_transition(AB, "unknown", "stopped", facts=FULL_FACTS, roles=frozenset())


def test_all_requirements_met():
    assert check_transition(AB, "draft", "running", facts=FULL_FACTS,
                            roles=frozenset({"experimenter"})) == []
    assert check_transition(AB, "decided", "stopped", facts={}, roles=frozenset()) == []


def test_unmet_requirements_in_order_with_contract_messages():
    assert check_transition(AB, "draft", "running", facts={"variants": 1},
                            roles=frozenset()) == [
        "Die Hypothese fehlt.",
        "Es ist keine prim\u00e4re Metrik festgelegt.",
        "Es braucht mindestens 2 Varianten.",
    ]
    assert check_transition(AB, "running", "analysis", facts={"measurements": 0},
                            roles=frozenset()) == ["Es gibt noch keine Messwerte."]
    assert check_transition(AB, "analysis", "decided", facts={}, roles=frozenset()) == [
        "Es ist noch keine Entscheidung festgehalten."
    ]


def _one_transition(requires, fields=None):
    return validate_type_definition({
        "fields": fields or [],
        "states": [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}],
        "transitions": [{"from": "a", "to": "b", "label": "Weiter", "requires": requires}],
    })


def test_evaluation_learning_and_single_variant_messages():
    definition = _one_transition(["evaluation", "learning", "variants:1"])
    assert check_transition(definition, "a", "b", facts={}, roles=frozenset()) == [
        "Es gibt noch keine abgeschlossene Auswertung.",
        "Die Erkenntnis fehlt.",
        "Es braucht mindestens eine Variante.",
    ]


def test_field_requirement():
    definition = _one_transition(
        ["field:channel", "field:paid"],
        fields=[{"key": "channel", "label": "Kanal", "type": "text"},
                {"key": "paid", "label": "Bezahlt", "type": "boolean"}],
    )
    assert check_transition(definition, "a", "b", facts={"fields": {"channel": "  "}},
                            roles=frozenset()) == [
        "Das Feld \u00abKanal\u00bb ist leer.", "Das Feld \u00abBezahlt\u00bb ist leer.",
    ]
    assert check_transition(definition, "a", "b",
                            facts={"fields": {"channel": "LinkedIn", "paid": False}},
                            roles=frozenset()) == []


PLANNED = _one_transition(["n_planned:planned_n"],
                          fields=[{"key": "planned_n", "label": "Geplant", "type": "integer"}])


def test_n_planned_unset_is_unmet():
    assert check_transition(PLANNED, "a", "b", facts={"fields": {}}, roles=frozenset()) == [
        "Die geplante Stichprobe ist noch nicht festgelegt (Feld \u00abGeplant\u00bb)."
    ]


def test_n_planned_counts_the_laggard_variant():
    facts = {"fields": {"planned_n": 1000}, "variants": 2,
             "variant_n": {"A": 1200, "B": 500, None: 99999}}
    assert check_transition(PLANNED, "a", "b", facts=facts, roles=frozenset()) == [
        "Die geplante Stichprobe ist noch nicht erreicht (500 von 1'000 je Variante)."
    ]
    facts["variant_n"]["B"] = 1000
    assert check_transition(PLANNED, "a", "b", facts=facts, roles=frozenset()) == []


def test_n_planned_variant_without_rows_counts_as_zero():
    facts = {"fields": {"planned_n": "300"}, "variants": 3, "variant_n": {"A": 400, "B": 400}}
    assert check_transition(PLANNED, "a", "b", facts=facts, roles=frozenset()) == [
        "Die geplante Stichprobe ist noch nicht erreicht (0 von 300 je Variante)."
    ]
    assert check_transition(PLANNED, "a", "b", facts={"fields": {"planned_n": 5}},
                            roles=frozenset())[0].startswith(
        "Die geplante Stichprobe ist noch nicht erreicht (0 von 5")


def test_roles_gate_the_transition_before_requirements():
    definition = example()
    definition["transitions"][2]["roles"] = ["experiments_manager"]
    normalised = validate_type_definition(definition)
    with pytest.raises(Forbidden) as info:
        check_transition(normalised, "analysis", "decided", facts={},
                         roles=frozenset({"experimenter"}))
    assert info.value.message == (
        "Diesen Statuswechsel d\u00fcrfen nur Verantwortliche der Experimente ausf\u00fchren."
    )
    assert check_transition(normalised, "analysis", "decided", facts={},
                            roles=frozenset({"experiments_manager"})) == [
        "Es ist noch keine Entscheidung festgehalten."
    ]
    assert check_transition(normalised, "analysis", "decided", facts=FULL_FACTS,
                            roles=frozenset({"admin"})) == []
    with pytest.raises(Forbidden):
        check_transition(normalised, "analysis", "decided", facts=FULL_FACTS, roles=None)


def test_both_roles_are_named():
    definition = example()
    definition["transitions"][0]["roles"] = ["experimenter", "experiments_manager"]
    normalised = validate_type_definition(definition)
    with pytest.raises(Forbidden) as info:
        check_transition(normalised, "draft", "running", facts=FULL_FACTS, roles=frozenset())
    assert info.value.message == (
        "Diesen Statuswechsel d\u00fcrfen nur Experimentierende oder Verantwortliche der "
        "Experimente ausf\u00fchren."
    )


def test_unknown_requirement_in_a_hand_built_definition_fails_closed():
    definition = {"states": [{"key": "a"}, {"key": "b"}],
                  "transitions": [{"from": "a", "to": "b", "requires": ["telepathy"]}]}
    assert check_transition(definition, "a", "b", facts=FULL_FACTS, roles=frozenset()) == [
        "Unbekannte Bedingung \u00abtelepathy\u00bb."
    ]


# -- references -----------------------------------------------------------------------


def test_metric_and_evaluator_refs():
    definition = example(evaluation=[
        {"evaluator": "builtin.describe", "metric": "all"},
        {"evaluator": "builtin.bayes_proportion", "metric": "primary"},
        {"evaluator": "builtin.ratio_delta", "metric": "cost_per_click"},
        {"evaluator": "builtin.describe", "metric": "ctr"},
    ])
    normalised = validate_type_definition(definition)
    assert metric_refs(normalised) == ["ctr", "bounce_rate", "cost_per_click"]
    assert evaluator_refs(normalised) == [
        "builtin.describe", "builtin.bayes_proportion", "builtin.ratio_delta",
    ]
    assert metric_refs({}) == [] and evaluator_refs({}) == []
