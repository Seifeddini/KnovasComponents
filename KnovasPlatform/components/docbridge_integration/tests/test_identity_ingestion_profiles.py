"""ingestion_profiles: versioned, attributed, reversible (KC-IN-2, KC-IN-7)."""

from __future__ import annotations

import json

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


@_needs_db
def test_a_confirmed_change_records_both_people(platform_db, by, identity_repo):
    """I1: created_by is the person who asked, approved_by the one who
    confirmed. Before this, both were the approver and approved_by was NULL
    although the column exists for exactly this."""
    approver = identity_repo.create(email="pruefer@kanzlei.ch", display_name="P",
                                    password="korrektes-pferd-batterie")
    repo = IngestionProfileRepository(platform_db)
    v = repo.save_new_version(_profile(), by=by, approved_by=approver)
    assert str(v.created_by) == str(by.id)
    assert str(v.approved_by) == str(approver.id)


# --- Document fields inside sources[] (spec 4.8, 2.5) ------------------------

def _profile_with_fields():
    return IngestionProfile(
        identifier_prefix="kanzlei",
        sources=[
            SourceFolder(path="/mnt/autodoc/mandate", access_groups=("g-lit",),
                         fields={"doc_type": "invoice", "keywords": ["Beleg", "Kreditor"],
                                 "privileged": True},
                         field_templates=("{mandant}/{period}/**", "Archiv/**"),
                         metadata_fields=("email_date", "email_doc_type")),
            SourceFolder(path="/mnt/autodoc/allgemein", recursive=False),
        ],
        schedule="nightly",
    )


def _old_profile_from_json(data):
    """profile_from_json as the previous release had it: what an older
    Platform does with a row this one wrote (the downgrade row of 2.5)."""
    fields = dict(data)
    fields["sources"] = [
        SourceFolder(path=str(s["path"]), recursive=bool(s.get("recursive", True)),
                     access_groups=tuple(str(g) for g in (s.get("access_groups") or ())))
        for s in fields.get("sources") or []
    ]
    return IngestionProfile(**fields)


def test_a_round_trip_with_fields_is_lossless():
    profile = _profile_with_fields()
    data = profile_to_json(profile)
    assert profile_from_json(data) == profile
    # And through JSON text, the way the jsonb column and the approvals
    # payload carry it.
    assert profile_from_json(json.loads(json.dumps(data))) == profile


def test_fields_are_stored_inside_sources_as_plain_json():
    entry = profile_to_json(_profile_with_fields())["sources"][0]
    assert entry["fields"] == {"doc_type": "invoice", "keywords": ["Beleg", "Kreditor"],
                               "privileged": True}
    assert entry["field_templates"] == ["{mandant}/{period}/**", "Archiv/**"]
    assert entry["metadata_fields"] == ["email_date", "email_doc_type"]


def test_no_new_top_level_key():
    """An older Platform builds IngestionProfile(**fields) and would crash on
    one; everything new lives inside sources[]."""
    assert set(profile_to_json(_profile_with_fields())) == set(profile_to_json(_profile()))
    assert set(profile_to_json(_profile())) == {
        "identifier_prefix", "sources", "file_types", "schedule", "throughput", "paused",
        "full_rescan", "max_document_age_days", "max_file_megabytes", "exclude_globs",
        "delete_on_remove", "description",
    }


def test_a_profile_without_fields_stores_exactly_as_before():
    assert profile_to_json(_profile())["sources"] == [
        {"path": "/mnt/autodoc/mandate", "recursive": True, "access_groups": ["g-lit"]},
    ]


def test_an_old_row_still_loads():
    old_row = {"identifier_prefix": "kanzlei", "schedule": "nightly",
               "sources": [{"path": "/mnt/autodoc/mandate", "recursive": True,
                            "access_groups": ["g-lit"]}]}
    loaded = profile_from_json(old_row)
    assert loaded == _profile()
    assert loaded.sources[0].fields == () and loaded.sources[0].field_templates == ()


def test_an_older_platform_reads_a_new_row_and_drops_the_fields():
    loaded = _old_profile_from_json(profile_to_json(_profile_with_fields()))
    assert [s.path for s in loaded.sources] == ["/mnt/autodoc/mandate", "/mnt/autodoc/allgemein"]
    assert not any(s.has_fields for s in loaded.sources)


def test_a_shape_this_code_does_not_understand_reads_as_not_set():
    row = {"identifier_prefix": "kanzlei",
           "sources": [{"path": "/x", "fields": ["not", "a", "dict"],
                        "field_templates": "{mandant}/**", "metadata_fields": None}]}
    (source,) = profile_from_json(row).sources
    assert (source.fields, source.field_templates, source.metadata_fields) == ((), (), ())


@_needs_db
def test_a_profile_with_fields_survives_the_database(platform_db, by):
    repo = IngestionProfileRepository(platform_db)
    repo.save_new_version(_profile_with_fields(), by=by)
    assert repo.current().profile == _profile_with_fields()
