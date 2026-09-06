"""audit.recent(): the append-only record gets its first read path.

The Approvals tab shows administrator bypasses from here. A bypass that is
recorded but unreadable is, to the person looking at the screen, an exemption.
"""

from __future__ import annotations

import pytest

from conftest import platform_db_reachable
from identity import audit

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN"
)


@pytest.fixture
def actor(identity_repo):
    return identity_repo.create(
        email="a@kanzlei.ch", display_name="A", password="korrektes-pferd-batterie"
    )


def test_recent_is_newest_first_and_filters_by_action(platform_db, actor):
    audit.record(platform_db, action="approval.bypassed", actor=actor,
                 target_type="acl_change", target_id="one")
    audit.record(platform_db, action="user.created", actor=actor,
                 target_type="user", target_id="x")
    audit.record(platform_db, action="approval.bypassed", actor=actor,
                 target_type="acl_change", target_id="two")

    rows = audit.recent(platform_db, action="approval.bypassed")
    assert [r["target_id"] for r in rows] == ["two", "one"]
    assert rows[0]["actor_email"] == "a@kanzlei.ch"
    assert rows[0]["detail"] == {} or isinstance(rows[0]["detail"], dict)


def test_limit_is_honoured(platform_db, actor):
    for i in range(3):
        audit.record(platform_db, action="user.created", actor=actor,
                     target_type="user", target_id=str(i))
    assert len(audit.recent(platform_db, limit=2)) == 2
