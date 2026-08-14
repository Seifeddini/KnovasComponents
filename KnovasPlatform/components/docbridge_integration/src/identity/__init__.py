"""Per-user identity for the Knovas Platform (Pflichtenheft B1, B2, B5).

The Platform is the firm's own machine. Everything in this package — accounts,
credentials, roles, access-group grants, approvals — lives in a PostgreSQL
instance the firm hosts and holds. Knovas never learns a lawyer's name; it
learns a signed opaque subject id and a group list.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md
"""
