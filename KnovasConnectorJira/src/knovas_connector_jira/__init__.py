"""The Jira connector of connector protocol 2.0 (FEAT-2026-11-10-write-back J-1, J-2; BET-2026-11-write-back WB-07).

It reads one Jira Cloud project's issues as ``record`` streams and carries out three actions (``set_assignee``,
``transition_issue``, ``set_due_date``) under the Brain's permits, behind the site's Knovas Link, on the connector kit
(``knovas_connector_kit``). Nothing ships it yet: no image, deployment or Builder offer names it (J-4).
"""
VERSION = "0.1.0"
SOURCE_KIND = "jira"
