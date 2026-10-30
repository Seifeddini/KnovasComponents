"""The Brain Builder's workspace model: a company's whole configuration as one record.

``package`` reads a configuration package (brain-config.yaml and the files it
lists) into the model and writes it back; ``store`` keeps the model's versions
in PostgreSQL. The model's JSON Schema is ``schema/workspace.schema.json``.

Plan: "Knovas Brain Builder -- build plan" (Claude Docs), step B1.
"""
