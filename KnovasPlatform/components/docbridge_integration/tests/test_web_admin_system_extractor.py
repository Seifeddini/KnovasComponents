"""System tab: the extractor of the Platform and of the Knovas Connector side
by side (spec L5).

The admin upload here and the Connector's sync extract into one index, so
the tab names both knovas-extract versions and warns when they differ. The
Connector's version comes from the /sync/status answer its own line already
fetched -- never a second request. The checks run without an app.
"""

from __future__ import annotations

import pytest

from conftest import DummyKnovasClient


class _Connector:
    """What the System tab's ping sees: health() is /sync/status."""

    def __init__(self, status=None, error=None):
        self._status = {} if status is None else status
        self._error = error
        self.calls = 0

    def health(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._status


PIN = "b5d45404a6df0aa5fb2b934c8ae4efab9fe764a1"
BUMP = "23f30ccddd12b08ddaeebf79ae528598443f8f27"


def _status(version, commit=None):
    block = {"knovas_extract_version": version, "pdf_text_mode": "layout",
             "docx_text_mode": "layout", "ocr_engine": "auto"}
    if commit is not None:
        block["knovas_extract_commit"] = commit
    return {"capabilities": [], "extraction": block}


def _extractor(rc=None):
    from web_interface import admin_system

    checks = admin_system.collect(lambda: DummyKnovasClient(None),
                                  rc_client_factory=(lambda: rc) if rc is not None else None)
    return next(c for c in checks if c["key"] == "extractor")


@pytest.fixture(autouse=True)
def _healthy(monkeypatch):
    monkeypatch.setattr(DummyKnovasClient, "health_result", True)


@pytest.fixture
def platform_version(monkeypatch):
    """The Platform's own knovas-extract, as the tab reads it."""
    from web_interface import admin_system

    def use(version, commit=None):
        monkeypatch.setattr(admin_system, "platform_extractor_version", lambda: version)
        monkeypatch.setattr(admin_system, "platform_extractor_commit", lambda: commit)

    use("0.4.0a1")
    return use


class TestBothSides:
    def test_the_same_version_is_ok(self, platform_version):
        check = _extractor(_Connector(_status("0.4.0a1")))
        assert check["state"] == "ok"
        assert check["detail"] == "Plattform 0.4.0a1, Knovas Connector 0.4.0a1"

    def test_different_versions_warn(self, platform_version):
        check = _extractor(_Connector(_status("0.3.0")))
        assert check["state"] == "warn"
        assert check["detail"] == "Plattform 0.4.0a1, Knovas Connector 0.3.0"
        assert "verschiedenen Versionen" in check["hint"]

    def test_a_connector_that_does_not_report_it_is_called_out(self, platform_version):
        check = _extractor(_Connector({"capabilities": []}))
        assert check["state"] == "warn"
        assert check["detail"] == "Plattform 0.4.0a1, Knovas Connector: keine Angabe"
        assert "Aktualisieren" in check["hint"]

    def test_an_unreachable_connector_skips_the_comparison(self, platform_version):
        from knovas_connector_client import KnovasConnectorError

        check = _extractor(_Connector(error=KnovasConnectorError("down")))
        assert check["state"] == "skip"
        assert check["detail"] == "Plattform 0.4.0a1, Knovas Connector nicht erreichbar"

    def test_without_a_connector_the_platform_alone(self, platform_version):
        check = _extractor()
        assert check["state"] == "ok" and check["detail"] == "Plattform 0.4.0a1"

    def test_a_platform_without_the_library_warns(self, platform_version):
        platform_version(None)
        check = _extractor(_Connector(_status("0.4.0a1")))
        assert check["state"] == "warn"
        assert check["detail"] == "Plattform: nicht installiert, Knovas Connector 0.4.0a1"

    def test_the_connector_is_asked_once(self, platform_version):
        rc = _Connector(_status("0.4.0a1"))
        _extractor(rc)
        assert rc.calls == 1, "the Connector line's ping answer is reused"

    def test_the_platform_side_is_the_installed_library(self):
        import json
        from importlib.metadata import distribution

        import knovas_extract

        from web_interface import admin_system

        assert admin_system.platform_extractor_version() == knovas_extract.__version__
        url = json.loads(distribution("knovas-extract").read_text("direct_url.json") or "{}")
        assert admin_system.platform_extractor_commit() == (url.get("vcs_info") or {}).get("commit_id")


class TestBuilds:
    """Before a release two pins share one version string (0.4.0a1): the
    commit of a git install tells the builds apart."""

    def test_the_same_build_is_ok_and_names_the_commit(self, platform_version):
        platform_version("0.4.0a1", PIN)
        check = _extractor(_Connector(_status("0.4.0a1", PIN)))
        assert check["state"] == "ok"
        assert check["detail"] == "Plattform 0.4.0a1 (git b5d4540), Knovas Connector 0.4.0a1 (git b5d4540)"

    def test_one_version_from_two_commits_warns(self, platform_version):
        platform_version("0.4.0a1", BUMP)
        check = _extractor(_Connector(_status("0.4.0a1", PIN)))
        assert check["state"] == "warn"
        assert check["detail"] == "Plattform 0.4.0a1 (git 23f30cc), Knovas Connector 0.4.0a1 (git b5d4540)"
        assert "Beide Images mit demselben Stand neu bauen" in check["hint"]

    def test_a_git_build_and_a_release_of_one_version_warn(self, platform_version):
        platform_version("0.4.0a1", None)
        check = _extractor(_Connector(_status("0.4.0a1", PIN)))
        assert check["state"] == "warn"
        assert check["detail"] == "Plattform 0.4.0a1, Knovas Connector 0.4.0a1 (git b5d4540)"


class TestCommitFromStatus:
    @pytest.mark.parametrize("status", [
        None, {}, {"extraction": {}}, _status("0.4.0a1"),
        {"extraction": {"knovas_extract_commit": None}},
        {"extraction": {"knovas_extract_commit": "main"}},
        {"extraction": {"knovas_extract_commit": PIN.upper()}},
        {"extraction": {"knovas_extract_commit": PIN + "0"}},
        {"extraction": {"knovas_extract_commit": "<b>" + PIN[3:]}},
    ])
    def test_anything_but_a_full_commit_is_none(self, status):
        from knovas_connector_client import extractor_commit_from_status

        assert extractor_commit_from_status(status) is None

    def test_a_commit_is_returned_as_given(self):
        from knovas_connector_client import extractor_commit_from_status

        assert extractor_commit_from_status(_status("0.4.0a1", PIN)) == PIN


class TestVersionFromStatus:
    @pytest.mark.parametrize("status", [
        None, [], "0.4.0a1", {}, {"extraction": None}, {"extraction": {}},
        {"extraction": {"knovas_extract_version": 4}},
        {"extraction": {"knovas_extract_version": ""}},
        {"extraction": {"knovas_extract_version": "0.4.0a1 <b>"}},
        {"extraction": {"knovas_extract_version": "9" * 41}},
    ])
    def test_anything_but_a_plain_version_is_none(self, status):
        from knovas_connector_client import extractor_version_from_status

        assert extractor_version_from_status(status) is None

    def test_a_version_is_returned_as_given(self):
        from knovas_connector_client import extractor_version_from_status

        assert extractor_version_from_status(_status("0.4.0a1")) == "0.4.0a1"
