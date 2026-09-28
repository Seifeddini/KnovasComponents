"""Experiments module (UI label: "Experimente").

Records experiments of any domain -- engineering, marketing, sales, product,
and domains people add themselves -- with their hypotheses, variants, runs,
measurements, evaluations, decisions and learnings. Every experiment is also
written to the Knovas index, so the normal Knovas search finds it.

Flask-free: the web layer lives in web_interface/experiments_routes.py and
hands this package a database connection and the signed-in user.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md
"""

MODULE_VERSION = "1.0.0"
