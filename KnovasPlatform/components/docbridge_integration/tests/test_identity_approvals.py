"""Four-eyes control, and the administrator's recorded bypass (KC-B5-1).

The bypass is a deliberate decision (14 August 2026): an administrator executes
destructive actions immediately rather than waiting for a second confirmer.
These tests pin the part that makes it defensible — a bypass is *recorded as a
bypass*, so the audit shows that a two-person control was skipped and by whom,
rather than showing nothing and letting a reader assume the control ran.
"""

import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)

from identity import approvals, users  # noqa: E402


@pytest.fixture
def repo(platform_db):
    return users.UserRepository(platform_db)


@pytest.fixture
def service(platform_db, repo):
    return approvals.ApprovalService(platform_db, repo)


def _user(repo, email, *roles):
    user = repo.create(email=email, display_name=email.split("@")[0],
                       password="korrektes-pferd-batterie")
    for role in roles:
        repo.grant_role(user.id, role)
    return repo.get(user.id)


def _audit(platform_db, action=None):
    sql = "SELECT action, actor_email_snapshot, detail FROM audit_log"
    params = ()
    if action:
        sql += " WHERE action = %s"
        params = (action,)
    return platform_db.execute(sql, params).fetchall()


class TestWhoNeedsApproval:
    def test_an_ordinary_member_needs_approval_to_delete_a_matter(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        assert service.requires_approval("matter_delete", member) is True

    def test_an_administrator_does_not(self, repo, service):
        """Decided 2026-08-14: admins execute destructive actions immediately."""
        admin = _user(repo, "admin@kanzlei.ch", "admin")
        assert service.requires_approval("matter_delete", admin) is False

    def test_an_approver_who_is_not_an_admin_still_needs_approval(self, repo, service):
        """Being allowed to confirm someone else's action is not the same as
        being allowed to act alone."""
        approver = _user(repo, "partner@kanzlei.ch", "approver")
        assert service.requires_approval("matter_delete", approver) is True

    def test_the_bypass_can_be_turned_off_for_a_firm_that_wants_it(self, repo, service, platform_db):
        admin = _user(repo, "admin@kanzlei.ch", "admin")
        service.set_admin_bypass(False)
        assert service.requires_approval("matter_delete", admin) is True

    def test_the_bypass_is_on_by_default(self, service):
        assert service.admin_bypass_enabled() is True

    def test_an_unguarded_action_needs_no_approval_from_anyone(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        assert service.requires_approval("search", member) is False


class TestTheBypassIsRecorded:
    def test_an_admin_bypass_writes_an_audit_row(self, repo, service, platform_db):
        admin = _user(repo, "admin@kanzlei.ch", "admin")
        service.record_bypass(admin, kind="matter_delete", target_ref="node:42")
        assert len(_audit(platform_db, "approval.bypassed")) == 1

    def test_the_audit_row_names_who_bypassed_and_what(self, repo, service, platform_db):
        admin = _user(repo, "admin@kanzlei.ch", "admin")
        service.record_bypass(admin, kind="matter_delete", target_ref="node:42")
        action, email, detail = _audit(platform_db, "approval.bypassed")[0]
        assert str(email) == "admin@kanzlei.ch"
        assert detail["kind"] == "matter_delete"
        assert detail["target_ref"] == "node:42"

    def test_the_audit_row_survives_deleting_the_account(self, repo, service, platform_db):
        """A dangling uuid is not an answer to 'who did this?'."""
        admin = _user(repo, "admin@kanzlei.ch", "admin")
        service.record_bypass(admin, kind="purge_all_documents", target_ref="tenant")
        platform_db.execute("DELETE FROM users WHERE id = %s", (str(admin.id),))
        _action, email, _detail = _audit(platform_db, "approval.bypassed")[0]
        assert str(email) == "admin@kanzlei.ch"


class TestTheApprovalWorkflow:
    def test_a_request_starts_pending(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        assert request.status == "pending"

    def test_a_pending_request_appears_in_the_queue(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        service.request(member, kind="matter_delete", target_ref="node:42")
        assert len(service.pending()) == 1

    def test_a_second_person_can_approve(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        approver = _user(repo, "partner@kanzlei.ch", "approver")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        assert service.approve(request.id, approver).status == "approved"

    def test_the_requester_cannot_approve_their_own(self, repo, service):
        both = _user(repo, "partner@kanzlei.ch", "approver")
        request = service.request(both, kind="matter_delete", target_ref="node:42")
        with pytest.raises(approvals.SelfApprovalError):
            service.approve(request.id, both)

    def test_an_admin_cannot_approve_their_own_either(self, repo, service):
        """The bypass is for acting directly. Once a request exists, the
        two-person rule is the point of it."""
        admin = _user(repo, "admin@kanzlei.ch", "admin")
        request = service.request(admin, kind="matter_delete", target_ref="node:42")
        with pytest.raises(approvals.SelfApprovalError):
            service.approve(request.id, admin)

    def test_someone_without_the_approver_role_cannot_approve(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        other = _user(repo, "sekretariat@kanzlei.ch", "member")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        with pytest.raises(approvals.NotAnApproverError):
            service.approve(request.id, other)

    def test_an_admin_may_approve_someone_elses_request(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        admin = _user(repo, "admin@kanzlei.ch", "admin")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        assert service.approve(request.id, admin).status == "approved"

    def test_rejecting_records_the_reason(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        approver = _user(repo, "partner@kanzlei.ch", "approver")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        rejected = service.reject(request.id, approver, reason="Frist läuft noch")
        assert rejected.status == "rejected"
        assert rejected.decision_reason == "Frist läuft noch"

    def test_an_approved_request_can_be_marked_executed_once(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        approver = _user(repo, "partner@kanzlei.ch", "approver")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        service.approve(request.id, approver)
        assert service.mark_executed(request.id, {"deleted": 1}).status == "executed"

    def test_marking_executed_twice_is_refused(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        approver = _user(repo, "partner@kanzlei.ch", "approver")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        service.approve(request.id, approver)
        service.mark_executed(request.id, {})
        with pytest.raises(approvals.InvalidTransitionError):
            service.mark_executed(request.id, {})

    def test_a_pending_request_cannot_be_executed(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        with pytest.raises(approvals.InvalidTransitionError):
            service.mark_executed(request.id, {})

    def test_an_expired_request_cannot_be_approved(self, repo, service, platform_db):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        approver = _user(repo, "partner@kanzlei.ch", "approver")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        platform_db.execute(
            "UPDATE approval_requests SET expires_at = now() - interval '1 hour' "
            "WHERE id = %s",
            (str(request.id),),
        )
        with pytest.raises(approvals.RequestExpiredError):
            service.approve(request.id, approver)

    def test_an_unguarded_kind_cannot_be_requested(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        with pytest.raises(approvals.UnknownKindError):
            service.request(member, kind="search", target_ref="x")


class TestApprovedQueue:
    """approved(): what is confirmed but not yet carried out (finding 2/6)."""

    def test_approved_returns_only_approved_newest_first(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        approver = _user(repo, "partner@kanzlei.ch", "approver")
        r1 = service.request(member, kind="matter_delete", target_ref="node:1")
        r2 = service.request(member, kind="matter_delete", target_ref="node:2")
        r3 = service.request(member, kind="matter_delete", target_ref="node:3")
        service.approve(r1.id, approver)
        service.approve(r2.id, approver)
        service.reject(r3.id, approver, reason="nein")

        approved_refs = [r.target_ref for r in service.approved()]
        assert approved_refs == ["node:2", "node:1"]
        assert all(r.status == "approved" for r in service.approved())

    def test_a_second_approval_of_the_same_request_is_refused(self, repo, service):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        approver = _user(repo, "partner@kanzlei.ch", "approver")
        third = _user(repo, "dritte@kanzlei.ch", "approver")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        service.approve(request.id, approver)
        with pytest.raises(approvals.InvalidTransitionError):
            service.approve(request.id, third)


class TestMarkExecutedIsAudited:
    def test_mark_executed_twice_raises_and_writes_one_audit_row(
        self, repo, service, platform_db
    ):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        approver = _user(repo, "partner@kanzlei.ch", "approver")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        service.approve(request.id, approver)

        service.mark_executed(request.id, {"deleted": 1}, by=approver)
        with pytest.raises(approvals.InvalidTransitionError):
            service.mark_executed(request.id, {"deleted": 1}, by=approver)

        rows = _audit(platform_db, "approval.executed")
        assert len(rows) == 1
        _action, email, detail = rows[0]
        assert str(email) == "partner@kanzlei.ch"
        assert detail["request_id"] == str(request.id)


class TestDecisionsAreAudited:
    def test_approving_writes_an_audit_row_naming_both_people(self, repo, service, platform_db):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        approver = _user(repo, "partner@kanzlei.ch", "approver")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        service.approve(request.id, approver)
        _action, email, detail = _audit(platform_db, "approval.approved")[0]
        assert str(email) == "partner@kanzlei.ch"
        assert detail["requested_by_email"] == "anwalt@kanzlei.ch"

    def test_requesting_is_audited(self, repo, service, platform_db):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        service.request(member, kind="matter_delete", target_ref="node:42")
        assert len(_audit(platform_db, "approval.requested")) == 1

    def test_rejecting_is_audited(self, repo, service, platform_db):
        member = _user(repo, "anwalt@kanzlei.ch", "member")
        approver = _user(repo, "partner@kanzlei.ch", "approver")
        request = service.request(member, kind="matter_delete", target_ref="node:42")
        service.reject(request.id, approver, reason="nein")
        assert len(_audit(platform_db, "approval.rejected")) == 1
