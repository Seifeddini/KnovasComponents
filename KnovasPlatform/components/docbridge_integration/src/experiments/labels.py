"""German labels for the codes the experiments module stores.

The database, the JSON API and the Knovas documents all carry English codes
(``ship``, ``guardrail``, ``finished``); people read these labels instead.
They live in one place so the page, the API ``meta`` answer and the indexed
Markdown never disagree about what a code is called.

Source stays ASCII (repository rule), so umlauts are written as escapes.
"""

from __future__ import annotations

from typing import Dict

#: What was decided about an experiment (exp_decisions.verdict).
DECISION_VERDICT_LABELS: Dict[str, str] = {
    "ship": "\u00dcbernehmen",
    "iterate": "Weiterentwickeln",
    "stop": "Verwerfen",
    "inconclusive": "Ohne klares Ergebnis",
}

#: What an evaluation concluded (evaluator output ``verdict``).
EVALUATION_VERDICT_LABELS: Dict[str, str] = {
    "better": "besser",
    "worse": "schlechter",
    "inconclusive": "offen",
    "n/a": "\u2013",
}

#: exp_notes.kind. 'status' notes are written by the service when a state
#: change carries a comment.
NOTE_KIND_LABELS: Dict[str, str] = {
    "note": "Notiz",
    "observation": "Beobachtung",
    "interview": "Interview",
    "feedback": "R\u00fcckmeldung",
    "status": "Statuswechsel",
}

#: exp_metrics.direction.
DIRECTION_LABELS: Dict[str, str] = {
    "higher": "h\u00f6her ist besser",
    "lower": "tiefer ist besser",
    "none": "ohne Richtung",
}

#: exp_experiment_metrics.role.
METRIC_ROLE_LABELS: Dict[str, str] = {
    "primary": "prim\u00e4r",
    "secondary": "sekund\u00e4r",
    "guardrail": "Leitplanke",
}

#: exp_runs.status.
RUN_STATUS_LABELS: Dict[str, str] = {
    "running": "l\u00e4uft",
    "finished": "abgeschlossen",
    "failed": "fehlgeschlagen",
    "cancelled": "abgebrochen",
}

#: exp_evaluations.status.
EVALUATION_STATUS_LABELS: Dict[str, str] = {
    "queued": "wartet",
    "running": "l\u00e4uft",
    "done": "fertig",
    "failed": "fehlgeschlagen",
}

#: exp_experiments.index_state: where the Knovas copy of an experiment stands.
INDEX_STATE_LABELS: Dict[str, str] = {
    "pending": "ausstehend",
    "indexed": "aktuell",
    "error": "Fehler",
    "off": "aus",
}

#: exp_batches.source / exp_measurements.source / exp_runs.source.
SOURCE_LABELS: Dict[str, str] = {
    "manual": "von Hand",
    "api": "API",
    "csv": "CSV",
}
