"""audit.record() when the write fails: the failure is logged, the row never is.

PostgreSQL's message for a failed insert carries a DETAIL line that quotes the
whole row ("Failing row contains (..., a@kanzlei.ch, ...)"), the actor's
e-mail with it, and a traceback carries that message. So a failed write logs
the marker, the action, the target type, the exception's type and, from
psycopg, the SQLSTATE and the constraint's name -- and no traceback.
"""

from __future__ import annotations

import logging

import pytest

from conftest import platform_db_reachable
from identity import audit

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN"
)

EMAIL = "ida.muster@kanzlei.ch"


@pytest.fixture
def actor(identity_repo):
    return identity_repo.create(
        email=EMAIL, display_name="Ida Muster", password="korrektes-pferd-batterie"
    )


def _failures(caplog):
    return [r for r in caplog.records if "AUDIT WRITE FAILED" in r.getMessage()]


def test_a_failed_write_logs_its_error_code_and_never_the_row(platform_db, actor, caplog):
    caplog.set_level(logging.DEBUG)
    audit.record(platform_db, action="user.created", actor=actor, target_type="user",
                 target_id="ziel-7f3a", outcome="vielleicht",       # not ok, denied or error
                 detail={"vermerk": "Vertraulicher Vermerk"}, request_id="req-4711",
                 ip="198.51.100.7", user_agent="Agent/9.9")
    assert audit.recent(platform_db) == []                     # nothing written, and nothing raised

    [failure] = _failures(caplog)
    message = failure.getMessage()
    assert failure.levelno == logging.ERROR
    assert "action=user.created" in message and "target_type=user" in message
    assert "CheckViolation" in message
    assert "sqlstate=23514" in message and "constraint=audit_log_outcome_check" in message
    assert failure.exc_info is None and failure.exc_text is None       # no traceback: it carries the message
    for value in (EMAIL, str(actor.id), "ziel-7f3a", "vielleicht", "Vertraulicher Vermerk", "req-4711",
                  "198.51.100.7", "Agent/9.9", "DETAIL", "Failing row"):
        assert value not in caplog.text


def test_a_failure_without_a_database_code_logs_its_type_only(caplog):
    class _Refusing:
        def execute(self, *args, **kwargs):
            raise RuntimeError(f"insert refused: {EMAIL}")

    caplog.set_level(logging.DEBUG)
    audit.record(_Refusing(), action="user.created", target_type="user", target_id="ziel-7f3a")
    [failure] = _failures(caplog)
    assert "RuntimeError" in failure.getMessage() and "sqlstate=-" in failure.getMessage()
    assert failure.exc_info is None
    assert EMAIL not in caplog.text and "ziel-7f3a" not in caplog.text
