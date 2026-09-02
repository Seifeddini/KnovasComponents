"""ingestion_profiles: versioned, attributed, reversible (KC-IN-2, KC-IN-7)."""

from __future__ import annotations

import pytest

from conftest import platform_db_reachable
from identity.ingestion_compiler import IngestionProfile, SourceFolder
from identity.ingestion_profiles import (
    IngestionProfileRepository,
    profile_from_json,
    profile_to_json,
)


def _profile(prefix="kanzlei", schedule="nightly"):
    return IngestionProfile(
        identifier_prefix=prefix,
        sources=[SourceFolder(path="/mnt/autodoc/mandate", access_groups=("g-lit",))],
        schedule=schedule,
    )


def test_json_round_trip_is_lossless():
    p = _profile()
    assert profile_from_json(profile_to_json(p)) == IngestionProfile(
        identifier_prefix="kanzlei",
        sources=[SourceFolder(path="/mnt/autodoc/mandate", recursive=True,
                              access_groups=("g-lit",))],
        schedule="nightly",
    )


_needs_db = pytest.mark.skipif(
    not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN"
)


@pytest.fixture
def by(identity_repo):
    return identity_repo.create(email="ing@kanzlei.ch", display_name="I",
                                password="korrektes-pferd-batterie")


@_needs_db
def test_first_save_is_version_one_and_current(platform_db, by):
    repo = IngestionProfileRepository(platform_db)
    assert repo.current() is None
    v = repo.save_new_version(_profile(), by=by)
    assert (v.version, v.is_current, v.pushed_at) == (1, True, None)
    assert repo.current().id == v.id


@_needs_db
def test_a_second_save_supersedes_the_first(platform_db, by):
    repo = IngestionProfileRepository(platform_db)
    repo.save_new_version(_profile(), by=by)
    v2 = repo.save_new_version(_profile(schedule="continuous"), by=by)
    assert v2.version == 2 and repo.current().version == 2
    assert [v.version for v in repo.versions()] == [2, 1]
    assert repo.versions()[1].is_current is False


@_needs_db
def test_restore_copies_an_old_version_as_a_new_current_one(platform_db, by):
    repo = IngestionProfileRepository(platform_db)
    repo.save_new_version(_profile(schedule="nightly"), by=by)
    repo.save_new_version(_profile(schedule="continuous"), by=by)
    v3 = repo.restore("default", 1, by=by)
    assert v3.version == 3 and v3.profile.schedule == "nightly"
    assert repo.current().version == 3


@_needs_db
def test_mark_pushed_records_the_moment(platform_db, by):
    repo = IngestionProfileRepository(platform_db)
    v = repo.save_new_version(_profile(), by=by)
    repo.mark_pushed(v.id)
    assert repo.current().pushed_at is not None
