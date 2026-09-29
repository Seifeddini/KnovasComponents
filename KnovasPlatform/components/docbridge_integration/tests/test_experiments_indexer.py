"""The Knovas copy of an experiment: its Markdown, its parts, its upload.

Properties that matter beyond formatting: the document names no person, the
upload is refused without an access group (fail closed), Knovas' errors turn
into fixed German messages (exception text only in the log), and a purge can
only ever delete pointers of the experiment shape.
"""

import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import requests

from conftest import FakeIndexClient, platform_db_reachable
from experiments import indexer, jobs
from experiments.errors import Unavailable
from experiments.jobs import PermanentError, RetryLater
from experiments.settings import ExperimentsSettings

needs_db = pytest.mark.skipif(not platform_db_reachable(),
                              reason="No PostgreSQL at the identity test DSN")

EXPERIMENT_ID = "0b6f6c1e-35d1-4c8a-9d65-2f2a4f8f1a01"


def make_snapshot(**overrides):
    snap = {
        "id": EXPERIMENT_ID, "key": "MKT-1", "title": "Betreffzeile mit Frage",
        "hypothesis": "Eine Frage in der Betreffzeile erh\u00f6ht die Klickrate.",
        "description": "Wir testen zwei Betreffzeilen.\r\n\r\nZweiter Absatz.",
        "status": "running", "status_label": "L\u00e4uft", "status_phase": "running",
        "archived": False,
        "domain": {"id": "d1", "key": "marketing", "name": "Marketing", "color": "#eb6834",
                   "id_prefix": "MKT"},
        "type": {"id": "t1", "key": "ab_test", "name": "A/B-Test", "version": 1},
        "fields": [
            {"key": "channel", "label": "Kanal", "type": "enum", "value": "LinkedIn",
             "display": "LinkedIn"},
            {"key": "audience", "label": "Zielgruppe", "type": "text", "value": None,
             "display": "\u2013"},
            {"key": "budget", "label": "Budget", "type": "number", "value": 1500,
             "display": "1'500"},
        ],
        "field_values": {"channel": "LinkedIn", "budget": 1500},
        "tags": ["linkedin", "q4"],
        "owner": {"id": "u1", "display_name": "Eva Muster"},
        "variants": [
            {"id": "v1", "key": "A", "name": "Kontrolle", "description": "", "is_control": True,
             "allocation": 0.5, "position": 0, "has_data": True},
            {"id": "v2", "key": "B", "name": "Frage", "description": "Betreff\nals Frage",
             "is_control": False, "allocation": 0.5, "position": 1, "has_data": True},
        ],
        "metrics": [
            {"id": "m1", "key": "ctr", "name": "Klickrate", "kind": "proportion",
             "kind_label": "Anteil", "unit": "%", "direction": "higher",
             "direction_label": "h\u00f6her ist besser", "role": "primary", "role_label": "prim\u00e4r",
             "guardrail_op": None, "guardrail_value": None, "definition": {"decimals": 2},
             "guardrail_status": None,
             "aggregates": [
                 {"variant": "A", "rows": 12, "n": 10688, "value_sum": 129.0,
                  "denominator_sum": None, "sum_sq": None, "estimate": 0.01207, "levels": None},
                 {"variant": "B", "rows": 12, "n": 10714, "value_sum": 175.0,
                  "denominator_sum": None, "sum_sq": None, "estimate": 0.016334, "levels": None},
             ]},
            {"id": "m2", "key": "bounce_rate", "name": "Absprungrate", "kind": "proportion",
             "kind_label": "Anteil", "unit": "%", "direction": "lower",
             "direction_label": "tiefer ist besser", "role": "guardrail",
             "role_label": "Leitplanke", "guardrail_op": "max", "guardrail_value": 0.7,
             "definition": {}, "guardrail_status": "violated",
             "aggregates": [{"variant": "B", "rows": 1, "n": 100, "estimate": 0.78}]},
            {"id": "m3", "key": "cost_per_click", "name": "Kosten pro Klick", "kind": "ratio",
             "kind_label": "Verh\u00e4ltnis", "unit": "CHF", "direction": "lower",
             "direction_label": "tiefer ist besser", "role": "secondary",
             "role_label": "sekund\u00e4r", "guardrail_op": None, "guardrail_value": None,
             "definition": {}, "guardrail_status": None, "aggregates": []},
        ],
        "evaluations": [
            {"id": "e4", "evaluator_key": "builtin.bayes_proportion",
             "evaluator_name": "Bayes-Vergleich (Anteile)", "language": "builtin",
             "evaluator_version": 1, "metric_key": "ctr", "params": {}, "scope": {},
             "trigger": "manual", "status": "done", "status_label": "fertig",
             "verdict": "better", "headline": "P(B besser als A) = 99,6 %",
             "output": {"summary": "B ist **klar** besser."}, "error": None,
             "created_at": "2026-09-27T09:59:00+00:00",
             "finished_at": "2026-09-27T10:00:00+00:00", "duration_ms": 12,
             "requested_by": {"display_name": "Eva Muster"}},
            {"id": "e3", "evaluator_key": "builtin.bayes_proportion",
             "evaluator_name": "Bayes-Vergleich (Anteile)", "metric_key": "ctr", "params": {},
             "scope": {}, "status": "done", "verdict": "inconclusive",
             "headline": "\u00c4ltere Auswertung", "output": None,
             "finished_at": "2026-09-20T10:00:00+00:00"},
            {"id": "e2", "evaluator_key": "example.bootstrap_mean_py",
             "evaluator_name": "Bootstrap", "metric_key": "ctr", "scope": {},
             "status": "queued", "verdict": None, "headline": None, "output": None},
            {"id": "e1", "evaluator_key": "builtin.describe",
             "evaluator_name": "Beschreibung je Variante", "metric_key": "ctr",
             "params": {}, "scope": {"runs": "latest"}, "status": "done", "verdict": "n/a",
             "headline": "Klickrate je Variante", "output": None,
             "created_at": "2026-09-26T08:00:00+00:00", "finished_at": None},
        ],
        "decisions": [
            {"id": "dec1", "verdict": "ship", "verdict_label": "\u00dcbernehmen",
             "rationale": "Klar besser.", "learning": "Fragen wirken.",
             "decided_by": {"id": "u2", "display_name": "Max Muster"},
             "decided_at": "2026-09-28T09:00:00+00:00"},
        ],
        "notes": [
            {"id": "n1", "kind": "observation", "kind_label": "Beobachtung",
             "body": "Mehr Antworten am Montag.", "variant": "B", "run_id": None,
             "created_by": {"id": "u1", "display_name": "Eva Muster"},
             "created_at": "2026-09-20T08:00:00+00:00", "can_delete": True},
        ],
        "runs": [
            {"id": "r1", "name": "Woche 38", "variant": "B", "status": "finished",
             "status_label": "abgeschlossen", "params": {}, "environment": {}, "commit": "",
             "source": "api", "started_at": "2026-09-15T00:00:00+00:00", "ended_at": None,
             "created_at": "2026-09-16T00:00:00+00:00", "metrics": {"ctr": 0.0163}},
        ],
        "run_count": 1, "measurement_count": 24, "batch_count": 2,
        "created_at": "2026-09-01T08:00:00+00:00", "updated_at": "2026-09-28T10:15:00+00:00",
        "started_at": "2026-09-02T08:00:00+00:00", "ended_at": None, "decided_at": None,
        "row_version": 3,
        "index": {"state": "pending", "state_label": "ausstehend", "indexed_at": None,
                  "error": None},
    }
    snap.update(overrides)
    return snap


EXPECTED_MARKDOWN = """# MKT-1 \u00b7 Betreffzeile mit Frage

Experiment im Bereich Marketing \u00b7 Typ A/B-Test \u00b7 Status L\u00e4uft \u00b7 aktualisiert 28.09.2026 \u00b7 Schlagw\u00f6rter: linkedin, q4

## Hypothese

Eine Frage in der Betreffzeile erh\u00f6ht die Klickrate.

## Beschreibung

Wir testen zwei Betreffzeilen.

Zweiter Absatz.

## Angaben

- Kanal: LinkedIn
- Budget: 1'500

## Varianten

- A (Kontrolle): Kontrolle
- B: Frage \u2013 Betreff als Frage

## Metriken

- Klickrate (Anteil, h\u00f6her ist besser, prim\u00e4r): A: 1,21 % (n = 10'688); B: 1,63 % (n = 10'714)
- Absprungrate (Anteil, tiefer ist besser, Leitplanke <= 70,00 %): B: 78,00 % (n = 100) \u2013 Leitplanke verletzt
- Kosten pro Klick (Verh\u00e4ltnis, tiefer ist besser, sekund\u00e4r): noch keine Messwerte

## Auswertungen

### Bayes-Vergleich (Anteile) \u2013 Klickrate \u2013 27.09.2026

P(B besser als A) = 99,6 % (besser)

B ist **klar** besser.

### Beschreibung je Variante \u2013 Klickrate \u2013 26.09.2026

Klickrate je Variante

## L\u00e4ufe

- Woche 38 (B, 15.09.2026): Klickrate: 1,63 %

## Notizen

### Beobachtung \u2013 20.09.2026

Mehr Antworten am Montag.

## Entscheidungen

### \u00dcbernehmen \u2013 28.09.2026

Begr\u00fcndung: Klar besser.

Erkenntnis: Fragen wirken.
"""


def settings(**kw):
    kw.setdefault("enabled", True)
    kw.setdefault("index_access_groups", ("g-experimente",))
    return ExperimentsSettings(**kw)


# -- render_markdown -------------------------------------------------------------------


def test_render_markdown_matches_the_contract_layout():
    assert indexer.render_markdown(make_snapshot()) == EXPECTED_MARKDOWN


def test_markdown_names_no_person_and_no_address():
    snap = make_snapshot(owner={"id": "u1", "display_name": "Eva Muster",
                                "email": "eva@knovas.ch"})
    snap["notes"][0]["created_by"]["email"] = "eva@knovas.ch"
    markdown = indexer.render_markdown(snap)
    for secret in ("Eva", "Max", "Muster", "@knovas.ch", "u1", "u2"):
        assert secret not in markdown


def test_empty_sections_are_left_out():
    snap = make_snapshot(hypothesis="", description="  ", fields=[], variants=[], metrics=[],
                         evaluations=[], runs=[], notes=[], decisions=[], tags=[])
    markdown = indexer.render_markdown(snap)
    assert markdown == ("# MKT-1 \u00b7 Betreffzeile mit Frage\n\nExperiment im Bereich Marketing"
                        " \u00b7 Typ A/B-Test \u00b7 Status L\u00e4uft \u00b7 aktualisiert 28.09.2026\n")
    assert "##" not in markdown


def test_only_the_newest_done_evaluation_per_evaluator_metric_scope():
    markdown = indexer.render_markdown(make_snapshot())
    assert "\u00c4ltere Auswertung" not in markdown
    assert "Bootstrap" not in markdown  # still queued
    # The same evaluator with another scope is a different series.
    snap = make_snapshot()
    snap["evaluations"][1]["scope"] = {"runs": "latest"}
    assert "\u00c4ltere Auswertung (offen)" in indexer.render_markdown(snap)


def test_evaluation_without_metric_or_date():
    snap = make_snapshot(evaluations=[{
        "evaluator_key": "x.y", "evaluator_name": "Eigener Auswerter", "metric_key": None,
        "scope": {}, "status": "done", "verdict": "worse", "headline": "Schlechter",
        "output": {"summary": ""}, "finished_at": None, "created_at": None}])
    assert "### Eigener Auswerter\n\nSchlechter (schlechter)\n" in indexer.render_markdown(snap)


def test_categorical_metrics_show_their_levels():
    snap = make_snapshot(metrics=[{
        "key": "preferred_option", "name": "Bevorzugte Variante", "kind": "categorical",
        "kind_label": "Kategorie", "unit": "", "direction": "none",
        "direction_label": "ohne Richtung", "role": "secondary", "role_label": "sekund\u00e4r",
        "definition": {"levels": {"1": "Variante A", "2": "Variante B", "0": "Keine Pr\u00e4ferenz"}},
        "aggregates": [{"variant": "A", "n": 30, "estimate": None,
                        "levels": {"1": 20, "0": 10}},
                       {"variant": None, "n": 5, "estimate": None, "levels": {"9": 5}}]}])
    markdown = indexer.render_markdown(snap)
    assert ("- Bevorzugte Variante (Kategorie, ohne Richtung, sekund\u00e4r): "
            "A: Variante A 20, Keine Pr\u00e4ferenz 10 (n = 30); ohne Variante: 9 5 (n = 5)") in markdown


def test_min_guardrail_and_metric_decimals():
    snap = make_snapshot(metrics=[{
        "key": "sus_score", "name": "SUS-Wert", "kind": "mean", "kind_label": "Mittelwert",
        "unit": "Punkte", "direction": "higher", "direction_label": "h\u00f6her ist besser",
        "role": "guardrail", "role_label": "Leitplanke", "guardrail_op": "min",
        "guardrail_value": 68, "definition": {"decimals": 1}, "guardrail_status": "ok",
        "aggregates": [{"variant": "A", "n": 12, "estimate": 71.25}]}])
    assert ("- SUS-Wert (Mittelwert, h\u00f6her ist besser, Leitplanke >= 68,0 Punkte): "
            "A: 71,3 Punkte (n = 12)\n") in indexer.render_markdown(snap)


def test_runs_are_capped_at_fifty_and_show_non_finished_status():
    runs = [{"name": f"Lauf {i}", "variant": None, "status": "finished",
             "created_at": "2026-09-16T00:00:00+00:00", "metrics": {}} for i in range(80)]
    runs[0].update(name="", status="failed", status_label="fehlgeschlagen",
                   metrics={"unknown_metric": 3.5})
    markdown = indexer.render_markdown(make_snapshot(runs=runs))
    assert "- Lauf (16.09.2026, fehlgeschlagen): unknown_metric: 3,50\n" in markdown
    assert "Lauf 49 " in markdown and "Lauf 50 " not in markdown


def test_titles_and_labels_cannot_break_the_layout():
    snap = make_snapshot(title="Zeile eins\n## Gef\u00e4lscht", tags=["a\nb"])
    markdown = indexer.render_markdown(snap)
    assert markdown.startswith("# MKT-1 \u00b7 Zeile eins ## Gef\u00e4lscht\n")
    assert "Schlagw\u00f6rter: a b" in markdown


def test_dates_are_swiss_local_days():
    if indexer._LOCAL_TZ is timezone.utc:
        pytest.skip("no tz database")
    snap = make_snapshot(updated_at="2026-09-27T22:30:00+00:00")
    assert "aktualisiert 28.09.2026" in indexer.render_markdown(snap)
    assert indexer._date(datetime(2026, 1, 31, 23, 30, tzinfo=timezone.utc)) == "01.02.2026"
    assert indexer._date("kein Datum") == ""
    assert indexer._date(None) == ""


# -- the 400 000 character cap ------------------------------------------------------------


def big_snapshot():
    evaluations = [{"evaluator_key": f"x.e{i}", "evaluator_name": f"Auswerter {i}",
                    "metric_key": "ctr", "scope": {}, "status": "done", "verdict": "better",
                    "headline": f"Auswertung {i}", "output": {"summary": "E" * 900},
                    "finished_at": "2026-09-20T10:00:00+00:00"} for i in range(10)]
    runs = [{"name": f"Lauf {i}", "variant": "A", "status": "finished",
             "created_at": "2026-09-16T00:00:00+00:00", "metrics": {}} for i in range(10)]
    notes = [{"kind": "note", "kind_label": "Notiz", "body": f"Notiz {i} " + "N" * 900,
              "created_at": "2026-09-20T08:00:00+00:00"} for i in range(10)]
    return make_snapshot(evaluations=evaluations, runs=runs, notes=notes)


def test_cap_drops_oldest_evaluations_first():
    full = indexer.render_markdown(big_snapshot())
    capped = indexer.render_markdown(big_snapshot(), max_chars=len(full) - 2500)
    assert len(capped) <= len(full) - 2500
    kept = [i for i in range(10) if f"Auswertung {i} (besser)" in capped]
    assert kept == list(range(7))  # 7, 8, 9 (the oldest) went first
    assert all(f"- Lauf {i} " in capped for i in range(10))
    assert all(f"Notiz {i} " in capped for i in range(10))
    assert "Gek\u00fcrzt." not in capped


def test_cap_then_drops_runs_then_notes_oldest_first():
    full = indexer.render_markdown(big_snapshot())
    evaluations_size = sum(len(f"### Auswerter {i} \u2013 Klickrate \u2013 20.09.2026\n\n"
                               f"Auswertung {i} (besser)\n\n" + "E" * 900) + 2 for i in range(10))
    cap = len(full) - evaluations_size - 300 - 1900
    capped = indexer.render_markdown(big_snapshot(), max_chars=cap)
    assert len(capped) <= cap
    assert "## Auswertungen" not in capped
    assert "## L\u00e4ufe" not in capped
    notes_kept = [i for i in range(10) if f"Notiz {i} " in capped]
    # The newest notes stay, the oldest went.
    assert notes_kept == list(range(len(notes_kept))) and 0 < len(notes_kept) < 10
    assert "## Entscheidungen" in capped
    assert "Gek\u00fcrzt." not in capped


def test_cap_ends_with_a_hard_cut_marker():
    snap = make_snapshot(description="Absatz " * 30000)  # far above any cap below
    capped = indexer.render_markdown(snap, max_chars=5000)
    assert len(capped) <= 5000
    assert capped.endswith("\n\nGek\u00fcrzt.\n")
    assert capped.startswith("# MKT-1 \u00b7 Betreffzeile mit Frage")


def test_default_cap_is_400000():
    snap = make_snapshot(description="x" * 45000, notes=[
        {"kind": "note", "kind_label": "Notiz", "body": "n" * 49000,
         "created_at": "2026-09-20T08:00:00+00:00"} for _ in range(12)])
    markdown = indexer.render_markdown(snap)
    assert len(markdown) <= 400_000
    assert markdown.count("### Notiz") < 12


# -- split_parts ------------------------------------------------------------------------


def test_small_document_is_one_part():
    parts = indexer.split_parts(EXPECTED_MARKDOWN)
    assert parts == [EXPECTED_MARKDOWN.strip("\n")]


def test_parts_are_cut_at_section_boundaries():
    parts = indexer.split_parts(EXPECTED_MARKDOWN, max_chars=400)
    assert len(parts) > 1
    assert all(len(p) <= 400 for p in parts)
    assert all(p.startswith("## ") for p in parts[1:])
    # Nothing is lost: every non-empty line appears in order.
    lines = [line for line in EXPECTED_MARKDOWN.splitlines() if line.strip()]
    rejoined = [line for p in parts for line in p.splitlines() if line.strip()]
    assert rejoined == lines
    # "### " subsections are not section boundaries.
    assert not any(p.startswith("### ") for p in parts)


def test_long_sections_split_at_paragraphs_then_hard():
    paragraph = "Satz. " * 20  # 120 chars
    section = "## Notizen\n\n" + "\n\n".join([paragraph] * 5) + "\n\n" + "Y" * 700
    parts = indexer.split_parts("# T\n\nkopf\n\n" + section, max_chars=300)
    assert all(len(p) <= 300 for p in parts)
    # The short header is packed with the first paragraphs of the section.
    assert parts[0].startswith("# T\n\nkopf\n\n## Notizen\n\nSatz.")
    assert sum(p.count("Satz.") for p in parts) == 100
    assert "".join(parts).count("Y") == 700
    assert max(len(p) for p in parts if "Y" in p) == 300  # the hard cut


def test_split_of_nothing_still_gives_one_part():
    assert indexer.split_parts("") == ["-"]
    assert indexer.split_parts("\n\n\n") == ["-"]


# -- document_for ------------------------------------------------------------------------


def test_document_for():
    doc = indexer.document_for(make_snapshot(), settings(index_access_groups=("g1", "g2")))
    assert doc["identifier"] == "experiments/marketing/MKT-1"
    assert doc["title"] == "MKT-1 \u00b7 Betreffzeile mit Frage"
    assert doc["description"] == "Eine Frage in der Betreffzeile erh\u00f6ht die Klickrate."
    assert doc["path"] == "/Experimente/Marketing/A-B-Test/MKT-1 Betreffzeile mit Frage"
    assert doc["parts"] == [{"snippet": EXPECTED_MARKDOWN.strip("\n")}]
    assert doc["access_groups"] == ("g1", "g2")
    assert set(doc) == {"identifier", "title", "description", "path", "parts", "access_groups"}


def test_document_for_without_groups_sends_none():
    doc = indexer.document_for(make_snapshot(), settings(index_access_groups=(),
                                                         index_unrestricted=True))
    assert doc["access_groups"] is None


def test_document_for_limits_and_path_segments():
    snap = make_snapshot(title="Q3\\Q4 / Test   mit\tLeerraum " + "t" * 600,
                         hypothesis="h" * 3000)
    snap["domain"] = dict(snap["domain"], name="Vertrieb / DACH")
    snap["type"] = dict(snap["type"], name="Playbook\\Test")
    doc = indexer.document_for(snap, settings(pointer_prefix="lab"))
    assert doc["identifier"] == "lab/marketing/MKT-1"
    assert len(doc["title"]) == 500
    assert len(doc["description"]) == 2000
    assert doc["path"].startswith("/Experimente/Vertrieb - DACH/Playbook-Test/"
                                  "MKT-1 Q3-Q4 - Test mit Leerraum ttt")
    assert len(doc["path"]) <= 2000
    long_names = make_snapshot(title="t" * 300)
    long_names["domain"] = dict(long_names["domain"], name="d" * 1500)
    long_names["type"] = dict(long_names["type"], name="y" * 1500)
    assert len(indexer.document_for(long_names, settings())["path"]) == 2000


def test_document_for_refuses_a_pointer_the_search_could_not_recognise():
    bad = make_snapshot(key="mkt-1")
    with pytest.raises(PermanentError):
        indexer.document_for(bad, settings())
    bad = make_snapshot()
    bad["domain"] = dict(bad["domain"], key="Marketing/x")
    with pytest.raises(PermanentError):
        indexer.document_for(bad, settings())


def test_pointer_for():
    assert indexer.pointer_for(settings(), "sales", "SAL-9") == "experiments/sales/SAL-9"


# -- index_experiment ----------------------------------------------------------------------


class IndexStore:
    def __init__(self):
        self.snapshots = {}
        self.existing = set()
        self.calls = []
        self.pointers = []
        self.isolation = []

    def load_snapshot(self, conn, key_or_id, *, actor=None):
        self.isolation.append(conn.execute("SHOW transaction_isolation").fetchone()[0])
        snap = self.snapshots.get(key_or_id)
        return copy.deepcopy(snap) if snap is not None else None

    def set_index_state(self, conn, experiment_id, state, error=None, *, if_updated_at=None):
        self.calls.append(("set_index_state", experiment_id, state, error, if_updated_at))

    def record_index_document(self, conn, pointer, experiment_id):
        self.calls.append(("record", pointer, experiment_id))
        if pointer not in self.pointers:
            self.pointers.append(pointer)

    def forget_index_document(self, conn, pointer):
        self.calls.append(("forget", pointer))
        if pointer in self.pointers:
            self.pointers.remove(pointer)

    def lookup_by_keys(self, conn, keys):
        return {k: {"key": k} for k in keys if k in self.existing}

    def index_documents(self, conn, after=None, limit=500):
        return sorted(p for p in self.pointers if after is None or p > after)[:limit]

    def states(self):
        return [c[2:] for c in self.calls if c[0] == "set_index_state"]


@pytest.fixture
def store(monkeypatch):
    import experiments

    fake = IndexStore()
    fake.snapshots[EXPERIMENT_ID] = make_snapshot()
    fake.existing.add("MKT-1")
    monkeypatch.setitem(sys.modules, "experiments.store", fake)
    monkeypatch.setattr(experiments, "store", fake, raising=False)
    return fake


@pytest.fixture
def conn(platform_db):
    platform_db.execute("UPDATE exp_rate_slots SET next_at = clock_timestamp() "
                        "- interval '1 second'")
    return platform_db


def http_error(status, headers=None):
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    err = requests.exceptions.HTTPError(f"{status} Error: secret-internal-detail")
    err.response = response
    return err


@needs_db
def test_index_experiment_uploads_and_marks_indexed(conn, store):
    client = FakeIndexClient()
    indexer.index_experiment(conn, EXPERIMENT_ID, client, settings())
    assert len(client.uploads) == 1
    upload = client.uploads[0]
    assert upload == {
        "identifier": "experiments/marketing/MKT-1",
        "title": "MKT-1 \u00b7 Betreffzeile mit Frage",
        "description": "Eine Frage in der Betreffzeile erh\u00f6ht die Klickrate.",
        "path": "/Experimente/Marketing/A-B-Test/MKT-1 Betreffzeile mit Frage",
        "parts": [{"snippet": EXPECTED_MARKDOWN.strip("\n")}],
        "access_groups": ["g-experimente"],
    }
    assert ("record", "experiments/marketing/MKT-1", EXPERIMENT_ID) in store.calls
    assert store.states() == [("indexed", None, "2026-09-28T10:15:00+00:00")]
    assert store.isolation == ["repeatable read"]
    # The upload took the shared slot.
    assert jobs.take_rate_slot(conn, "knovas_init", 2) > 0


@needs_db
def test_index_experiment_when_indexing_is_off(conn, store):
    client = FakeIndexClient()
    indexer.index_experiment(conn, EXPERIMENT_ID, client, settings(index_enabled=False))
    assert client.uploads == []
    assert store.states() == [("off", None, None)]
    assert jobs.take_rate_slot(conn, "knovas_init", 2) == 0.0  # no slot used


@needs_db
def test_index_experiment_refuses_without_access_group(conn, store):
    client = FakeIndexClient()
    indexer.index_experiment(conn, EXPERIMENT_ID, client, settings(index_access_groups=()))
    assert client.uploads == []
    assert store.states() == [("error", "F\u00fcr Experimente ist keine Knovas-Zugriffsgruppe "
                                        "festgelegt (EXPERIMENTS_ACCESS_GROUPS).", None)]


@needs_db
def test_index_experiment_unrestricted_uploads_without_groups(conn, store):
    client = FakeIndexClient()
    indexer.index_experiment(conn, EXPERIMENT_ID, client,
                             settings(index_access_groups=(), index_unrestricted=True))
    assert client.uploads[0]["access_groups"] == []


@needs_db
def test_index_experiment_waits_for_the_rate_slot(conn, store):
    conn.execute("UPDATE exp_rate_slots SET next_at = clock_timestamp() + interval '20 seconds'")
    client = FakeIndexClient()
    with pytest.raises(RetryLater) as exc:
        indexer.index_experiment(conn, EXPERIMENT_ID, client, settings())
    assert 15 < exc.value.delay_seconds <= 20
    assert exc.value.reason == "Warten auf den n\u00e4chsten freien Upload-Platz."
    assert client.uploads == [] and store.calls == []


@needs_db
def test_index_experiment_without_client_waits(conn, store):
    with pytest.raises(RetryLater) as exc:
        indexer.index_experiment(conn, EXPERIMENT_ID, None, settings())
    assert exc.value.delay_seconds == 3600


@needs_db
def test_index_experiment_of_a_missing_experiment_is_done(conn, store):
    client = FakeIndexClient()
    indexer.index_experiment(conn, "11111111-2222-3333-4444-555555555555", client, settings())
    assert client.uploads == [] and store.calls == []


@needs_db
def test_experiment_deleted_during_upload_is_taken_out_again(conn, store):
    store.existing.clear()
    client = FakeIndexClient()
    indexer.index_experiment(conn, EXPERIMENT_ID, client, settings())
    assert client.deleted == ["experiments/marketing/MKT-1"]
    assert store.pointers == []
    assert store.states() == []


@needs_db
def test_failed_delete_after_concurrent_removal_keeps_the_record(conn, store):
    store.existing.clear()

    class Client(FakeIndexClient):
        def delete_information_object(self, pointer):
            raise http_error(500)

    indexer.index_experiment(conn, EXPERIMENT_ID, Client(), settings())
    assert store.pointers == ["experiments/marketing/MKT-1"]  # the unindex job will retry


@needs_db
@pytest.mark.parametrize("error, expected_type, message", [
    (requests.exceptions.ConnectionError("dns failure secret-internal-detail"), Unavailable,
     "Knovas nicht erreichbar."),
    (requests.exceptions.ReadTimeout("secret-internal-detail"), Unavailable,
     "Knovas nicht erreichbar."),
    (http_error(500), Unavailable, "Knovas nicht erreichbar."),
    (http_error(502), Unavailable, "Knovas nicht erreichbar."),
    (http_error(409), Unavailable,
     "Knovas hat die Anfrage vor\u00fcbergehend nicht angenommen (HTTP 409)."),
    (http_error(408), Unavailable,
     "Knovas hat die Anfrage vor\u00fcbergehend nicht angenommen (HTTP 408)."),
    (http_error(425), Unavailable,
     "Knovas hat die Anfrage vor\u00fcbergehend nicht angenommen (HTTP 425)."),
])
def test_transient_knovas_errors_are_retried(conn, store, error, expected_type, message):
    client = FakeIndexClient()
    client.fail_with = error
    with pytest.raises(expected_type) as exc:
        indexer.index_experiment(conn, EXPERIMENT_ID, client, settings())
    assert exc.value.message == message
    assert store.states() == []  # still pending


@needs_db
@pytest.mark.parametrize("headers, delay", [
    ({"Retry-After": "120"}, 120),
    ({}, 60),
    ({"Retry-After": "garbage"}, 60),
    ({"Retry-After": "999999"}, 3600),
    ({"Retry-After": "0"}, 1),
])
@pytest.mark.parametrize("status", [429, 503])
def test_busy_knovas_defers(conn, store, status, headers, delay):
    client = FakeIndexClient()
    client.fail_with = http_error(status, headers)
    with pytest.raises(RetryLater) as exc:
        indexer.index_experiment(conn, EXPERIMENT_ID, client, settings())
    assert exc.value.delay_seconds == delay
    assert exc.value.reason == "Knovas ist ausgelastet; neuer Versuch folgt."


def test_retry_after_as_http_date():
    when = datetime.now(timezone.utc) + timedelta(seconds=300)
    delay = indexer._retry_after(http_error(503, {"Retry-After": format_datetime(when, usegmt=True)}))
    assert 290 <= delay <= 301


@needs_db
@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_rejected_document_is_dead_with_a_fixed_message(conn, store, status):
    client = FakeIndexClient()
    client.fail_with = http_error(status)
    with pytest.raises(PermanentError) as exc:
        indexer.index_experiment(conn, EXPERIMENT_ID, client, settings())
    message = f"Knovas hat das Dokument abgelehnt (HTTP {status})."
    assert str(exc.value) == message
    assert exc.value.handled is True  # on_dead must not overwrite the message
    assert store.states() == [("error", message, None)]
    assert "secret" not in json.dumps(store.calls)


@needs_db
def test_unrecognisable_pointer_is_recorded_and_never_uploaded(conn, store):
    store.snapshots[EXPERIMENT_ID] = make_snapshot(key="mkt-1")
    client = FakeIndexClient()
    with pytest.raises(PermanentError) as exc:
        indexer.index_experiment(conn, EXPERIMENT_ID, client, settings())
    assert exc.value.handled is True
    assert client.uploads == []
    assert store.states() == [("error", indexer.MSG_BAD_KEY, None)]


@needs_db
def test_unexpected_upload_errors_reach_the_worker_unchanged(conn, store):
    client = FakeIndexClient()
    client.fail_with = ValueError("tables[0] must be an object")
    with pytest.raises(ValueError):
        indexer.index_experiment(conn, EXPERIMENT_ID, client, settings())
    assert store.states() == []


@needs_db
def test_index_job_end_to_end_through_the_worker(conn, store):
    """tasks + JobWorker + indexer, as app.py wires them."""
    from experiments import tasks

    client = FakeIndexClient()
    handlers, on_dead, _ = tasks.build_handlers(settings=settings(), index_client=client,
                                                runner=None)
    worker = jobs.JobWorker(connect=lambda: None, handlers=handlers, on_dead=on_dead,
                            kinds=jobs.INDEX_THREAD_KINDS, lease_seconds=60, poll_seconds=1)
    queue = jobs.JobQueue(conn)
    job_id = queue.enqueue("index", {"experiment_id": EXPERIMENT_ID},
                           dedupe_key=f"index:{EXPERIMENT_ID}")
    assert worker.run_once(conn)
    assert len(client.uploads) == 1
    assert conn.execute("SELECT status FROM exp_jobs WHERE id = %s",
                        (job_id,)).fetchone()[0] == "done"
    # A second edit right away: the job exists but must wait for the slot.
    queue.enqueue("index", {"experiment_id": EXPERIMENT_ID}, dedupe_key=f"index:{EXPERIMENT_ID}")
    assert worker.run_once(conn) is False
    # A rejection is recorded once, specifically, and not overwritten by on_dead.
    conn.execute("UPDATE exp_rate_slots SET next_at = clock_timestamp() - interval '1 second'")
    client.fail_with = http_error(400)
    assert worker.run_once(conn)
    assert store.states()[-1] == ("error", "Knovas hat das Dokument abgelehnt (HTTP 400).", None)
    failures = queue.recent_failures()
    assert failures[0]["error"] == "Knovas hat das Dokument abgelehnt (HTTP 400)."


@needs_db
def test_exhausted_index_job_marks_knovas_unreachable(conn, store):
    from experiments import tasks

    client = FakeIndexClient()
    handlers, on_dead, _ = tasks.build_handlers(settings=settings(), index_client=client,
                                                runner=None)
    worker = jobs.JobWorker(connect=lambda: None, handlers=handlers, on_dead=on_dead,
                            kinds=("index",), lease_seconds=60, poll_seconds=1)
    jobs.JobQueue(conn).enqueue("index", {"experiment_id": EXPERIMENT_ID}, max_attempts=1)
    client.fail_with = requests.exceptions.ConnectionError("refused")
    assert worker.run_once(conn)
    assert store.states() == [("error", "Knovas war nicht erreichbar.", None)]


# -- unindex_pointer ------------------------------------------------------------------


@needs_db
def test_unindex_deletes_and_forgets(conn, store):
    client = FakeIndexClient()
    store.pointers = ["experiments/marketing/MKT-1"]
    indexer.unindex_pointer(conn, client, "experiments/marketing/MKT-1")
    assert client.deleted == ["experiments/marketing/MKT-1"]
    assert store.pointers == []


@needs_db
def test_unindex_treats_404_as_done(conn, store):
    client = FakeIndexClient()
    client.fail_with = http_error(404)
    store.pointers = ["experiments/marketing/MKT-1"]
    indexer.unindex_pointer(conn, client, "experiments/marketing/MKT-1")
    assert store.pointers == []


@needs_db
@pytest.mark.parametrize("error, expected", [
    (http_error(500), Unavailable),
    (requests.exceptions.ConnectionError("x"), Unavailable),
    (http_error(503), RetryLater),
    (http_error(400), PermanentError),
])
def test_unindex_failures_keep_the_record(conn, store, error, expected):
    client = FakeIndexClient()
    client.fail_with = error
    store.pointers = ["experiments/marketing/MKT-1"]
    with pytest.raises(expected):
        indexer.unindex_pointer(conn, client, "experiments/marketing/MKT-1")
    assert store.pointers == ["experiments/marketing/MKT-1"]


@needs_db
def test_unindex_without_client_or_pointer(conn, store):
    with pytest.raises(RetryLater) as exc:
        indexer.unindex_pointer(conn, None, "experiments/marketing/MKT-1")
    assert exc.value.delay_seconds == 3600
    client = FakeIndexClient()
    indexer.unindex_pointer(conn, client, "  ")
    assert client.deleted == [] and store.calls == []


# -- purge_all -----------------------------------------------------------------------------


class ListingClient(FakeIndexClient):
    def __init__(self, listed, refuse=(), missing=()):
        super().__init__()
        self.listed = listed
        self.refuse = set(refuse)
        self.missing = set(missing)
        self.list_calls = []

    def delete_information_object(self, pointer):
        if pointer in self.refuse:
            raise http_error(400)
        if pointer in self.missing:
            raise http_error(404)
        return super().delete_information_object(pointer)

    def iter_documents(self, **kwargs):
        self.list_calls.append(kwargs)
        yield from self.listed


def test_purge_all_deletes_recorded_then_listed_experiment_documents(store):
    store.pointers = [f"experiments/marketing/MKT-{i}" for i in range(1, 1201)]
    listed = [
        {"pointer": "experiments/marketing/MKT-5"},        # already gone in phase 1
        {"pointer": "experiments/sales/SAL-7"},            # unrecorded leftover
        {"identifier": "experiments/product/PRD-2"},
        {"pointer": "experiments/readme.txt"},             # not the experiment shape
        {"pointer": "experiments/marketing/notes/x.pdf"},
        {"pointer": "corpus/Akten/Vertrag.pdf"},
        "garbage",
    ]
    client = ListingClient(listed, missing={"experiments/marketing/MKT-3"})
    deleted = indexer.purge_all("conn", client, settings())
    assert deleted == 1202
    assert store.pointers == []
    assert client.list_calls == [{"prefix": "experiments/"}]
    assert "experiments/sales/SAL-7" in client.deleted
    assert "experiments/product/PRD-2" in client.deleted
    for untouched in ("experiments/readme.txt", "experiments/marketing/notes/x.pdf",
                      "corpus/Akten/Vertrag.pdf"):
        assert untouched not in client.deleted
    assert client.deleted.count("experiments/marketing/MKT-5") == 1


def test_purge_all_skips_refusals_and_can_skip_the_listing(store):
    store.pointers = ["experiments/marketing/MKT-1", "experiments/marketing/MKT-2"]
    client = ListingClient([{"pointer": "experiments/sales/SAL-9"}],
                           refuse={"experiments/marketing/MKT-1"})
    assert indexer.purge_all("conn", client, settings(), knovas_listing=False) == 1
    assert store.pointers == ["experiments/marketing/MKT-1"]
    assert client.list_calls == []


def test_purge_all_stops_when_knovas_is_unreachable(store):
    store.pointers = ["experiments/marketing/MKT-1", "experiments/marketing/MKT-2"]
    client = FakeIndexClient()
    client.fail_with = requests.exceptions.ConnectionError("down")
    with pytest.raises(Unavailable):
        indexer.purge_all("conn", client, settings())
    assert len(store.pointers) == 2
    with pytest.raises(Unavailable):
        indexer.purge_all("conn", None, settings())


def test_purge_all_waits_once_on_rate_limits(store, monkeypatch):
    slept = []
    monkeypatch.setattr(indexer.time, "sleep", slept.append)
    store.pointers = ["experiments/marketing/MKT-1"]
    client = FakeIndexClient()
    client.fail_with = http_error(429, {"Retry-After": "7"})
    assert indexer.purge_all("conn", client, settings()) == 1
    assert slept == [7.0]


# -- the index client and upload_text_document ------------------------------------------------


def test_make_index_client_is_an_unsigned_knovas_client():
    from knovas_client import KnovasAPIClient
    from test_knovas_client_hardening import StubConfig

    client = indexer.make_index_client(StubConfig({"api.base_url": "https://knovas.test"}))
    assert isinstance(client, KnovasAPIClient)
    assert client._principal_broker is None
    assert client.base_url == "https://knovas.test"


def recording_client(init_answer=None, fail_at=None):
    from test_knovas_client_hardening import FakeResponse, FakeSession, make_secured_client

    client = make_secured_client()

    def responder(method, url, **kw):
        if fail_at is not None and len(client._session.calls) - 1 == fail_at:
            return FakeResponse(500)
        if url.endswith("/secured/init_document_transmission"):
            return FakeResponse(200, {"transmission_key_id": "tk-1"} if init_answer is None
                                else init_answer)
        return FakeResponse(200, {"status": "success"})

    client._session = FakeSession(responder)
    return client


def test_upload_text_document_sends_init_then_parts():
    client = recording_client()
    result = client.upload_text_document(
        "experiments/marketing/MKT-1", title="MKT-1 \u00b7 Titel", description="Hypothese",
        path="Experimente\\Marketing/MKT-1 Titel",
        parts=[{"snippet": "# Teil 1"}, {"snippet": "   "}, {"snippet": "## Teil 2"}],
        access_groups=("g-exp", " g-exp ", "", "g-two"),
    )
    assert result == {"status": "success", "identifier": "experiments/marketing/MKT-1",
                      "part_count": 2}
    calls = client._session.calls
    assert [c["method"] for c in calls] == ["POST", "POST", "POST"]
    assert calls[0]["url"].endswith("/secured/init_document_transmission")
    assert calls[0]["json"] == {
        "identifier": "experiments/marketing/MKT-1", "part_count": 2,
        "title": "MKT-1 \u00b7 Titel", "description": "Hypothese",
        "path": "/Experimente/Marketing/MKT-1 Titel",
        "access_groups": ["g-exp", "g-two"],
    }
    assert [c["json"] for c in calls[1:]] == [
        {"key": "tk-1", "snippet": "# Teil 1", "part_number": 0},
        {"key": "tk-1", "snippet": "## Teil 2", "part_number": 1},
    ]
    assert all(c["url"].endswith("/secured/transmit_document_part") for c in calls[1:])
    assert all("principal_assertion" not in c["json"] for c in calls)


@pytest.mark.parametrize("groups", [None, (), [], ["", "  "]])
def test_upload_text_document_omits_empty_access_groups(groups):
    client = recording_client()
    client.upload_text_document("p/d/K-1", title="t", description="", path="",
                                parts=[{"snippet": "x"}], access_groups=groups)
    init = client._session.calls[0]["json"]
    assert "access_groups" not in init
    assert set(init) == {"identifier", "part_count", "title"}


def test_upload_text_document_validates_before_sending():
    client = recording_client()
    with pytest.raises(ValueError):
        client.upload_text_document(" ", title="t", description="", path="",
                                    parts=[{"snippet": "x"}])
    with pytest.raises(ValueError):
        client.upload_text_document("p/d/K-1", title="t", description="", path="",
                                    parts=[{"snippet": ""}, "not a dict"])
    assert client._session.calls == []


def test_upload_text_document_does_not_retry_failures():
    client = recording_client(fail_at=0)
    with pytest.raises(requests.exceptions.HTTPError) as exc:
        client.upload_text_document("p/d/K-1", title="t", description="", path="",
                                    parts=[{"snippet": "x"}])
    assert exc.value.response.status_code == 500
    assert len(client._session.calls) == 1
    client = recording_client(fail_at=1)
    with pytest.raises(requests.exceptions.HTTPError):
        client.upload_text_document("p/d/K-1", title="t", description="", path="",
                                    parts=[{"snippet": "x"}, {"snippet": "y"}])
    assert len(client._session.calls) == 2  # stopped at the failing part


def test_upload_text_document_needs_a_transmission_key():
    client = recording_client(init_answer={"status": "success"})
    with pytest.raises(RuntimeError):
        client.upload_text_document("p/d/K-1", title="t", description="", path="",
                                    parts=[{"snippet": "x"}])
    assert len(client._session.calls) == 1


def test_upload_text_document_clips_title_and_description():
    client = recording_client()
    client.upload_text_document("p/d/K-1", title="T" * 900, description="D" * 5000,
                                path="/x", parts=[{"snippet": "x"}])
    init = client._session.calls[0]["json"]
    assert len(init["title"]) == 500 and len(init["description"]) == 2000


def test_the_shared_fake_matches_the_real_signature():
    import inspect

    from knovas_client import KnovasAPIClient

    real = inspect.signature(KnovasAPIClient.upload_text_document)
    fake = inspect.signature(FakeIndexClient.upload_text_document)
    assert list(real.parameters) == list(fake.parameters)
    for name in ("title", "description", "path", "parts", "access_groups"):
        assert real.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
