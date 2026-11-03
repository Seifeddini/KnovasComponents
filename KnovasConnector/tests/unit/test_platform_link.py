"""The connector's real scan and upload under connector protocol v1: a dry run lists, an apply sends only that list.

Only the upload to Knovas stands in. The link's own traffic against the Platform's real protocol is tested in
KnovasPlatform/components/docbridge_integration/tests/test_connector_kinds.py.
"""
from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from platform_link.operations import ConnectorOperations
from sync.knovas_uploader import UploadResult
from sync.sync_scheduler import save_last_sync_body


def _config(folder="Akten", groups="alle", **more):
    return {"sources": [{"workspace": "w", "key": "ablage", "kind": "knovas_connector",
                         "settings": {"folder": folder, "access_groups": groups}}], "mappings": [], **more}


@pytest.fixture
def share(tmp_path, monkeypatch):
    root = tmp_path / "ablage"
    (root / "Akten").mkdir(parents=True)
    (root / "Akten" / "eins.md").write_text("Mietvertrag", encoding="utf-8")
    (root / "Akten" / "zwei.txt").write_text("Vollmacht", encoding="utf-8")
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / "state" / ".rc-sync-state.json"))
    monkeypatch.setenv("SEARCH_CONTEXT_STORE_PATH", str(tmp_path / "ctx"))
    monkeypatch.setenv("RC_WATCH_ROOTS", str(root))
    monkeypatch.setenv("RC_SYNC_CONFIG_PATH", str(tmp_path / "state" / "sync_config.json"))
    monkeypatch.delenv("M365_FOLDER_URL", raising=False)
    (tmp_path / "state").mkdir()
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    yield root
    reset_config()


@pytest.fixture
def uploads():
    sent = []
    uploader = MagicMock()
    uploader.delete_by_pointer.return_value = (True, None)

    def upload_file(path, rel, body, **kw):
        sent.append((rel, body))
        return UploadResult(rel, "key", 1, "ok", 1)

    uploader.upload_file.side_effect = upload_file
    with patch("sync.sync_scheduler.SemantixUploader", return_value=uploader), \
            patch("sync.sync_executor.ensure_mtls_certificate_freshness"):
        yield sent, uploader


@pytest.fixture
def ops(tmp_path):
    return ConnectorOperations(tmp_path / "state")


class TestDryRun:
    def test_it_counts_and_sends_nothing(self, share, ops, uploads):
        sent, _ = uploads
        outcome = ops.dry_run(_config())
        assert (outcome.status, outcome.added, outcome.changed) == ("ok", 2, 0), outcome.codes
        assert sent == []

    def test_what_it_reports_has_no_room_for_a_name(self, share, ops, uploads):
        assert "eins.md" not in json.dumps(ops.dry_run(_config()).__dict__)

    def test_its_list_stays_on_the_volume_readable_by_its_own_user(self, share, ops, uploads):
        ops.dry_run(_config())
        assert "eins.md" in ops.plan_path.read_text(encoding="utf-8")
        assert stat.S_IMODE(ops.plan_path.stat().st_mode) == 0o600

    @pytest.mark.parametrize("groups", ["", None])
    def test_a_folder_nobody_chose_access_for_is_refused_not_sent_open(self, share, ops, uploads, groups):
        outcome = ops.dry_run(_config(groups=groups))
        assert (outcome.status, outcome.codes) == ("failed", ["documents.no_access_choice"])
        assert not ops.plan_path.exists()

    def test_a_share_that_did_not_mount_is_refused(self, share, ops, uploads):
        assert ops.dry_run(_config(folder="Gibtsnicht")).codes == ["fetch.folder_missing"]

    def test_no_way_out_of_the_share(self, share, ops, uploads):
        assert ops.dry_run(_config(folder="../..")).codes == ["fetch.bad_folder"]


class TestApply:
    def test_without_a_dry_run_it_refuses(self, share, ops, uploads):
        sent, _ = uploads
        assert ops.apply(_config()).codes == ["apply.no_dry_run"]
        assert sent == []

    def test_it_sends_exactly_what_the_dry_run_listed_and_only_once(self, share, ops, uploads):
        sent, _ = uploads
        ops.dry_run(_config())
        (share / "Akten" / "neu.md").write_text("erschien nach dem Probelauf", encoding="utf-8")
        outcome = ops.apply(_config())
        assert {Path(rel).name for rel, _ in sent} == {"eins.md", "zwei.txt"}
        assert (outcome.status, outcome.added) == ("ok", 2), outcome.codes
        assert ops.apply(_config()).codes == ["apply.no_dry_run"], "a plan is used once"

    def test_the_next_dry_run_finds_what_appeared_and_not_what_was_sent(self, share, ops, uploads):
        ops.dry_run(_config())
        ops.apply(_config())
        (share / "Akten" / "neu.md").write_text("neu", encoding="utf-8")
        assert ops.dry_run(_config()).added == 1

    def test_it_removes_nothing_from_the_brain(self, share, ops, uploads):
        _, uploader = uploads
        ops.dry_run(_config())
        ops.apply(_config())
        (share / "Akten" / "eins.md").unlink()
        ops.dry_run(_config())
        ops.apply(_config())
        uploader.delete_by_pointer.assert_not_called()

    def test_access_groups_reach_the_upload(self, share, ops, uploads):
        sent, _ = uploads
        ops.dry_run(_config(groups="Partner, Sekretariat"))
        ops.apply(_config())
        assert sent[0][1]["sources"][0]["access_groups"] == ["Partner", "Sekretariat"]

    def test_the_whole_tenant_is_a_choice_someone_wrote_down(self, share, ops, uploads):
        sent, _ = uploads
        ops.dry_run(_config(groups="alle"))
        ops.apply(_config())
        assert "access_groups" not in sent[0][1]["sources"][0]

    def test_a_change_in_the_platform_after_the_dry_run_waits_for_the_next_one(self, share, ops, uploads):
        sent, _ = uploads
        ops.dry_run(_config(groups="Partner"))
        ops.apply(_config(groups="alle"))
        assert sent[0][1]["sources"][0]["access_groups"] == ["Partner"], "what is written is what was shown"

    def test_a_failed_upload_is_counted_without_its_name(self, share, ops, uploads):
        _, uploader = uploads
        uploader.upload_file.side_effect = lambda path, rel, body, **kw: UploadResult(rel, None, 0, "error", 0, error="HTTP 503")
        ops.dry_run(_config())
        outcome = ops.apply(_config())
        assert outcome.status == "failed" and outcome.errors >= 1
        assert "eins.md" not in json.dumps(outcome.__dict__)


class TestWhatToRead:
    def test_an_installation_keeps_its_saved_folder_list_with_its_access_groups(self, share, ops, uploads):
        sent, _ = uploads
        save_last_sync_body({"mode": "incremental", "sources": [{"path": str(share / "Akten"), "access_groups": ["Partner"]}],
                             "filters": {}, "ingestion": {"identifier_prefix": "kanzlei"}})
        assert ops.dry_run({"sources": [], "mappings": []}).added == 2
        ops.apply({"sources": [], "mappings": []})
        body = sent[0][1]
        assert body["sources"][0]["access_groups"] == ["Partner"] and body["ingestion"]["identifier_prefix"] == "kanzlei"
        assert body["ingestion"]["delete_on_remove"] is False, "a saved list may not remove what a dry run does not show"

    def test_nothing_configured_says_so(self, share, ops, uploads):
        assert ops.dry_run({"sources": [], "mappings": []}).codes == ["config.nothing_to_run"]


class TestCheck:
    def _source(self, **settings):
        return {"workspace": "w", "key": "ablage", "kind": "knovas_connector", "settings": settings}

    def test_it_counts_the_files(self, share, ops):
        assert ops.check(self._source(folder="Akten", access_groups="alle"), {}) == ("source.ok", {"files": 2})

    def test_what_is_wrong(self, share, ops):
        assert ops.check(self._source(folder="Akten"), {})[0] == "documents.no_access_choice"
        assert ops.check(self._source(folder="Nein", access_groups="alle"), {})[0] == "fetch.folder_missing"
        (share / "Leer").mkdir()
        assert ops.check(self._source(folder="Leer", access_groups="alle"), {})[0] == "fetch.folder_empty"

    def test_a_mapping_on_it_is_said_not_silently_dropped(self, share, ops):
        config = {"mappings": [{"workspace": "w", "source": "ablage", "id": "m"}]}
        assert ops.check(self._source(folder="Akten", access_groups="alle"), config)[0] == "documents.mappings_not_run"


class TestSwitchedOn:
    def test_it_starts_without_what_only_an_inbound_caller_needs(self, monkeypatch):
        from config import _missing_required_env

        for name in ("KNOVAS_INTERNAL_API_URL", "RC_INSTANCE_TOKEN", "RC_CLIENT_ID"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("RC_PLATFORM_URL", "https://platform.example.test/api/connectors/v1")
        monkeypatch.setenv("RC_WATCH_ROOTS", "/sources")       # bootstrap.prepare sets it
        assert _missing_required_env() == []

    def test_those_stay_required_without_it(self, monkeypatch):
        from config import _missing_required_env

        monkeypatch.delenv("RC_INSTANCE_TOKEN", raising=False)
        monkeypatch.delenv("RC_PLATFORM_URL", raising=False)
        monkeypatch.delenv("RC_INTERNAL_LOCAL_BYPASS", raising=False)
        assert "RC_INSTANCE_TOKEN" in _missing_required_env()

    @pytest.mark.parametrize("path,body", [("/sync/start", {}), ("/sync", {"mode": "incremental"}), ("/sync/stop", {}), ("/sync/body", {})])
    def test_nobody_can_start_a_transfer_from_outside(self, monkeypatch, tmp_path, path, body):
        """A linked connector has no way to check an inbound caller, so every such call is refused."""
        monkeypatch.setenv("RC_PLATFORM_URL", "https://platform.example.test/api/connectors/v1")
        monkeypatch.setenv("RC_SYNC_STATE_PATH", str(tmp_path / ".rc.json"))
        monkeypatch.setenv("KNOVAS_INTERNAL_API_URL", "")
        monkeypatch.setenv("RC_INSTANCE_TOKEN", "")
        monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
        monkeypatch.setenv("RC_RATE_LIMIT_ENABLED", "false")
        from config import load_config, reset_config

        reset_config()
        with patch("platform_link.bootstrap.prepare", return_value=None):
            from app import create_app

            app = create_app(skip_validation=True)
        load_config(validate=False, force_reload=True)
        answer = app.test_client().post(path, json=body, headers={"Authorization": "Bearer anything"})
        reset_config()
        assert answer.status_code in (401, 403, 503), (path, answer.status_code, answer.get_data(as_text=True)[:200])
