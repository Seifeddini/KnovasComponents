"""Compiling one ingestion profile into the two Knovas Connector documents.

KC-IN-6. The point of the compiler is that the "two configuration layers" in
KnovasConnector/docs/configuration.md stop being the administrator's problem.
These tests pin the properties that make that true — above all that the
compiled documents validate against the schemas Knovas Connector itself ships,
and that max_document_age_seconds is written to exactly one of them.
"""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from identity import ingestion_compiler as ic
from identity import ingestion_presets as presets

_CONTRACTS = (
    Path(__file__).resolve().parents[4] / "KnovasConnector" / "contracts"
)


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((_CONTRACTS / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


@pytest.fixture(scope="module")
def sync_config_validator():
    return _validator("knovas_connector_sync_config.schema.json")


@pytest.fixture(scope="module")
def sync_request_validator():
    return _validator("sync_request.schema.json")


def a_profile(**overrides) -> ic.IngestionProfile:
    """A profile shaped like what the Ingestion tab produces."""
    fields = dict(
        identifier_prefix="kanzlei",
        sources=[ic.SourceFolder(path="/mnt/mandate/litigation", access_groups=["litigation"])],
        file_types=["documents", "email"],
        schedule="nightly",
        throughput="normal",
    )
    fields.update(overrides)
    return ic.IngestionProfile(**fields)


class TestCompiledDocumentsAreValid:
    def test_sync_config_validates_against_the_shipped_schema(self, sync_config_validator):
        compiled = ic.compile_profile(a_profile())
        sync_config_validator.validate(compiled.sync_config)

    def test_sync_request_validates_against_the_shipped_schema(self, sync_request_validator):
        compiled = ic.compile_profile(a_profile())
        sync_request_validator.validate(compiled.sync_request)

    def test_every_schedule_and_throughput_combination_validates(
        self, sync_config_validator, sync_request_validator
    ):
        for schedule in presets.SCHEDULE_PRESETS:
            for throughput in presets.THROUGHPUT_PRESETS:
                compiled = ic.compile_profile(
                    a_profile(schedule=schedule, throughput=throughput)
                )
                sync_config_validator.validate(compiled.sync_config)
                sync_request_validator.validate(compiled.sync_request)


class TestTheAgeCutOffLivesInExactlyOnePlace:
    """The precedence rule in configuration.md is the confusion we remove."""

    def test_age_cut_off_goes_to_the_sync_request(self):
        compiled = ic.compile_profile(a_profile(max_document_age_days=30))
        assert compiled.sync_request["filters"]["max_document_age_seconds"] == 30 * 86400

    def test_age_cut_off_is_never_written_to_the_sync_config(self):
        compiled = ic.compile_profile(a_profile(max_document_age_days=30))
        assert "max_document_age_seconds" not in compiled.sync_config

    def test_no_age_cut_off_means_the_key_is_absent_not_zero(self):
        compiled = ic.compile_profile(a_profile(max_document_age_days=None))
        assert "max_document_age_seconds" not in compiled.sync_request["filters"]


class TestTheTwoModesAreNeverCrossed:
    """Both documents have a field called `mode` with disjoint vocabularies."""

    def test_sync_config_mode_is_from_the_scheduler_vocabulary(self):
        compiled = ic.compile_profile(a_profile(schedule="continuous"))
        assert compiled.sync_config["mode"] in {"one_time", "continuous"}

    def test_sync_request_mode_is_from_the_scan_vocabulary(self):
        compiled = ic.compile_profile(a_profile(schedule="continuous"))
        assert compiled.sync_request["mode"] in {"incremental", "full"}

    def test_full_rescan_is_a_profile_choice_not_a_schedule_side_effect(self):
        assert ic.compile_profile(a_profile(full_rescan=True)).sync_request["mode"] == "full"
        assert (
            ic.compile_profile(a_profile(full_rescan=False)).sync_request["mode"]
            == "incremental"
        )


class TestSchedulePresets:
    def test_continuous_runs_the_scheduler_loop(self):
        config = ic.compile_profile(a_profile(schedule="continuous")).sync_config
        assert config["enabled"] is True
        assert config["mode"] == "continuous"
        assert config["scan_interval_seconds"] >= 5

    def test_nightly_is_continuous_within_an_out_of_hours_window(self):
        config = ic.compile_profile(a_profile(schedule="nightly")).sync_config
        assert config["mode"] == "continuous"
        assert config["window"]["start_local"] == "19:00"
        assert config["window"]["end_local"] == "06:00"

    def test_manual_stays_enabled_so_the_start_button_works(self):
        """`enabled: false` makes _run_once return 'disabled' immediately, so a
        manual profile must not use it — that state is Pause, not Manual."""
        config = ic.compile_profile(a_profile(schedule="manual")).sync_config
        assert config["enabled"] is True
        assert config["mode"] == "one_time"

    def test_pausing_a_profile_disables_it_whatever_the_schedule(self):
        config = ic.compile_profile(a_profile(schedule="continuous", paused=True)).sync_config
        assert config["enabled"] is False


class TestThroughputPresets:
    def test_gentle_is_slower_than_normal_which_is_slower_than_fast(self):
        rate = lambda t: ic.compile_profile(a_profile(throughput=t)).sync_config[  # noqa: E731
            "rate_limit"
        ]["max_ingestion_requests_per_minute"]
        assert rate("gentle") < rate("normal") < rate("fast")

    def test_every_preset_states_its_consequence_in_words(self):
        """The form shows this string; a preset without one is a bare number."""
        for name, preset in presets.THROUGHPUT_PRESETS.items():
            assert preset["description"].strip(), name


class TestFileTypes:
    def test_chosen_file_types_become_include_globs(self):
        compiled = ic.compile_profile(a_profile(file_types=["documents"]))
        assert "**/*.docx" in compiled.sync_request["filters"]["include_globs"]

    def test_unchosen_file_types_are_absent(self):
        compiled = ic.compile_profile(a_profile(file_types=["documents"]))
        assert "**/*.eml" not in compiled.sync_request["filters"]["include_globs"]

    def test_the_globs_are_deduplicated_and_ordered(self):
        globs = ic.compile_profile(
            a_profile(file_types=["documents", "documents", "email"])
        ).sync_request["filters"]["include_globs"]
        assert globs == sorted(set(globs))


class TestPerSourceAccessGroups:
    """KC-IN-4: documents from a walled folder are born walled."""

    def test_access_groups_are_carried_into_the_source_entry(self):
        compiled = ic.compile_profile(a_profile())
        assert compiled.sync_request["sources"][0]["access_groups"] == ["litigation"]

    def test_a_folder_without_a_group_omits_the_key(self):
        compiled = ic.compile_profile(
            a_profile(sources=[ic.SourceFolder(path="/mnt/allgemein")])
        )
        assert "access_groups" not in compiled.sync_request["sources"][0]


class TestRejectionsAreReadable:
    def test_no_folders_is_rejected_before_any_schema_error(self):
        with pytest.raises(ic.ProfileError) as excinfo:
            ic.compile_profile(a_profile(sources=[]))
        assert "folder" in str(excinfo.value).lower()

    def test_an_unknown_schedule_names_the_valid_choices(self):
        with pytest.raises(ic.ProfileError) as excinfo:
            ic.compile_profile(a_profile(schedule="hourly"))
        assert "nightly" in str(excinfo.value)

    def test_an_unknown_file_type_names_the_valid_choices(self):
        with pytest.raises(ic.ProfileError) as excinfo:
            ic.compile_profile(a_profile(file_types=["spreadsheets"]))
        assert "documents" in str(excinfo.value)

    def test_a_missing_identifier_prefix_is_rejected(self):
        with pytest.raises(ic.ProfileError):
            ic.compile_profile(a_profile(identifier_prefix=""))


class TestNoSecretsAreEverCompiled:
    def test_compiled_config_carries_none_of_the_forbidden_keys(self):
        """sync_config.py refuses these outright; never send them."""
        forbidden = {
            "rc_instance_token",
            "semantix_client_cert_path",
            "semantix_client_key_path",
            "semantix_ca_cert_path",
        }
        compiled = ic.compile_profile(a_profile())
        assert forbidden.isdisjoint(compiled.sync_config)
        assert forbidden.isdisjoint(compiled.sync_request)


class TestTheSchemaChangeIsAdditive:
    """KC-IN-4 widened sources[] with access_groups. Every request that was
    valid before must still be valid, or existing Knovas Connector deployments
    break on upgrade."""

    @pytest.mark.parametrize(
        "example",
        ["sync-request.json", "sync-request-corpus.json", "sync-request-winjur.json"],
    )
    def test_shipped_examples_still_validate(self, example, sync_request_validator):
        path = _CONTRACTS.parent / "examples" / example
        sync_request_validator.validate(json.loads(path.read_text(encoding="utf-8")))

    def test_a_source_without_access_groups_is_still_valid(self, sync_request_validator):
        sync_request_validator.validate(
            {
                "mode": "incremental",
                "sources": [{"path": "/data/docs", "recursive": True}],
                "filters": {},
                "ingestion": {"identifier_prefix": "rc-sync"},
            }
        )

    def test_access_groups_must_be_unique_strings(self, sync_request_validator):
        """Duplicates would silently double-assign; the schema catches it."""
        with pytest.raises(Exception):
            sync_request_validator.validate(
                {
                    "mode": "incremental",
                    "sources": [{"path": "/d", "access_groups": ["a", "a"]}],
                    "filters": {},
                    "ingestion": {"identifier_prefix": "p"},
                }
            )


class TestRedactionForSupport:
    def test_redacted_profile_is_json_serialisable(self):
        text = ic.redact_for_support(a_profile())
        assert json.loads(text)

    def test_redacted_profile_keeps_the_shape_but_not_the_paths(self):
        """A support ticket needs the structure, not where the firm's files are."""
        text = ic.redact_for_support(a_profile())
        assert "/mnt/mandate/litigation" not in text
        assert "nightly" in text


_BUNDLED_SCHEMA_NAMES = (
    "sync_request.schema.json",
    "knovas_connector_sync_config.schema.json",
)


class TestContractsAreAvailableWithoutAMonorepoCheckout:
    """The Platform image copies only docbridge_integration/src (Dockerfile).

    Walking up from /app/src/identity never finds KnovasConnector/contracts,
    which is how 'Speichern' on the Ingestion tab fails in Docker. The
    compiler must ship the two schemas it validates against, and keep them
    byte-identical to the checkout when that checkout is present.
    """

    def test_the_compiler_ships_the_two_schemas_it_validates_against(self):
        bundled = Path(ic.__file__).resolve().parent / "rc_contracts"
        for name in _BUNDLED_SCHEMA_NAMES:
            assert (bundled / name).is_file(), name

    def test_bundled_schemas_match_the_checkout_when_present(self):
        checkout = Path(__file__).resolve().parents[4] / "KnovasConnector" / "contracts"
        if not checkout.is_dir():
            pytest.skip("Knovas Connector is not in this checkout")
        bundled = Path(ic.__file__).resolve().parent / "rc_contracts"
        for name in _BUNDLED_SCHEMA_NAMES:
            assert (bundled / name).read_bytes() == (checkout / name).read_bytes(), name

    def test_compile_still_validates_when_the_walk_finds_no_checkout(self, monkeypatch):
        monkeypatch.setattr(ic, "_checkout_contracts_dir", lambda: None)
        ic._validator.cache_clear()
        compiled = ic.compile_profile(a_profile())
        assert compiled.sync_request["ingestion"]["identifier_prefix"] == "kanzlei"


class TestAFolderAssignedToOneGroupTwice:
    """A group list that repeats an id stopped the ingest with a schema error.

    The sync-request schema declares uniqueItems on access_groups, so a folder
    carrying the same group twice failed compilation entirely — and the message
    an administrator saw was about JSON, not about anything they had done.
    Assigning a folder to a group twice says what assigning it once says.
    """

    def test_duplicates_compile_instead_of_failing(self, sync_request_validator):
        compiled = ic.compile_profile(a_profile(sources=[
            ic.SourceFolder(path="/mnt/documents", access_groups=["g-1", "g-1", "g-1"]),
        ]))
        sync_request_validator.validate(compiled.sync_request)

    def test_the_group_survives_exactly_once(self):
        compiled = ic.compile_profile(a_profile(sources=[
            ic.SourceFolder(path="/mnt/documents", access_groups=["g-1", "g-1"]),
        ]))
        assert compiled.sync_request["sources"][0]["access_groups"] == ["g-1"]

    def test_order_is_kept_for_the_rest(self):
        compiled = ic.compile_profile(a_profile(sources=[
            ic.SourceFolder(path="/mnt/documents",
                            access_groups=["litigation", "hr", "litigation"]),
        ]))
        assert compiled.sync_request["sources"][0]["access_groups"] == ["litigation", "hr"]

    def test_blank_entries_still_disappear(self):
        compiled = ic.compile_profile(a_profile(sources=[
            ic.SourceFolder(path="/mnt/documents", access_groups=["g-1", "  ", ""]),
        ]))
        assert compiled.sync_request["sources"][0]["access_groups"] == ["g-1"]


# --- Document fields per source (spec 4.8) -----------------------------------

# What the compiler emitted before document fields existed, frozen from the
# previous commit's compile_profile (json.dumps of the dicts, key order and
# all): a profile without fields must reach Knovas Connector byte-identical
# (D8, spec 2.5 "New Platform + old RC").
_FROZEN = {
    "walled": (
        '{"mode": "incremental", "sources": [{"path": "/mnt/mandate/litigation", '
        '"recursive": true, "access_groups": ["litigation"]}, {"path": "/mnt/allgemein", '
        '"recursive": false}], "filters": {"include_globs": ["**/*.docx", "**/*.eml", '
        '"**/*.md", "**/*.msg", "**/*.pdf", "**/*.txt"], "exclude_globs": ["**/*.tmp", '
        '"**/.DS_Store", "**/.git/**", "**/Thumbs.db", "**/~$*"]}, "ingestion": '
        '{"identifier_prefix": "kanzlei", "delete_on_remove": true}}',
        '{"schema_version": 1, "enabled": true, "mode": "continuous", "window": '
        '{"start_local": "19:00", "end_local": "06:00"}, "rate_limit": '
        '{"max_ingestion_requests_per_minute": 30, "burst": 5}, "max_files_per_cycle": 500, '
        '"max_scan_entries_per_cycle": 10000, "pause_policy": '
        '"finish_current_unit_then_pause", "scan_interval_seconds": 300}',
    ),
    "everything": (
        '{"mode": "full", "sources": [{"path": "/data/docs", "recursive": true, '
        '"access_groups": ["g-1", "g-2"]}], "filters": {"include_globs": ["**/*.docx", '
        '"**/*.md", "**/*.pdf", "**/*.txt"], "exclude_globs": ["**/*.tmp", "**/.DS_Store", '
        '"**/.git/**", "**/Archiv/**", "**/Thumbs.db", "**/~$*"], '
        '"max_document_age_seconds": 2592000, "max_file_bytes": 52428800}, "ingestion": '
        '{"identifier_prefix": "rc-sync", "delete_on_remove": false, "description": '
        '"Beispiel GmbH Akten"}}',
        '{"schema_version": 1, "enabled": false, "mode": "continuous", "window": '
        '{"start_local": "00:00", "end_local": "23:59"}, "rate_limit": '
        '{"max_ingestion_requests_per_minute": 120, "burst": 20}, "max_files_per_cycle": 2000, '
        '"max_scan_entries_per_cycle": 50000, "pause_policy": '
        '"finish_current_unit_then_pause", "scan_interval_seconds": 120}',
    ),
}


def _frozen_profiles():
    return {
        "walled": ic.IngestionProfile(
            identifier_prefix="kanzlei",
            sources=[ic.SourceFolder(path="/mnt/mandate/litigation", access_groups=("litigation",)),
                     ic.SourceFolder(path="/mnt/allgemein", recursive=False)],
            file_types=["documents", "email"], schedule="nightly", throughput="normal"),
        "everything": ic.IngestionProfile(
            identifier_prefix=" rc-sync ",
            sources=[ic.SourceFolder(path="/data/docs", access_groups=("g-1", "g-1", " ", "g-2"))],
            file_types=["documents"], schedule="continuous", throughput="fast", paused=True,
            full_rescan=True, max_document_age_days=30, max_file_megabytes=50,
            exclude_globs=["**/Archiv/**"], delete_on_remove=False,
            description="  Beispiel GmbH Akten  "),
    }


SENTINEL = "Sentinel-Muster-AG-7781"


def _registry():
    """The sanitized registry shape (doc_fields_view.sanitize_registry)."""
    from doc_fields_fakes import core_fields
    from doc_fields_view import sanitize_registry

    fields = core_fields()
    fields.append(dict(fields[0], key="old_key", status="deprecated", labels={"de": "Alt"}))
    return sanitize_registry(fields)


def _with_fields(**source_kw):
    source = ic.SourceFolder(path="/mnt/mandate", **source_kw)
    return a_profile(sources=[source])


class TestAProfileWithoutFieldsIsUnchanged:
    @pytest.mark.parametrize("name", sorted(_FROZEN))
    def test_it_compiles_to_the_frozen_bytes(self, name):
        compiled = ic.compile_profile(_frozen_profiles()[name])
        assert (json.dumps(compiled.sync_request), json.dumps(compiled.sync_config)) == _FROZEN[name]

    def test_no_source_entry_gains_a_key(self):
        for profile in _frozen_profiles().values():
            for entry in ic.compile_profile(profile).sync_request["sources"]:
                assert set(entry) <= {"path", "recursive", "access_groups"}


class TestFieldsReachTheSyncBody:
    def test_static_values_templates_and_metadata_are_emitted(self, sync_request_validator):
        compiled = ic.compile_profile(_with_fields(
            access_groups=("g-1",),
            fields={"doc_type": "invoice", "keywords": ["Beleg", "Kreditor"]},
            field_templates=("{mandant}/{period}/**",),
            metadata_fields=("email_date", "email_doc_type"),
        ))
        entry = compiled.sync_request["sources"][0]
        assert list(entry) == ["path", "recursive", "access_groups", "fields",
                               "field_templates", "metadata_fields"]
        assert entry["fields"] == {"doc_type": "invoice", "keywords": ["Beleg", "Kreditor"]}
        assert entry["field_templates"] == ["{mandant}/{period}/**"]
        assert entry["metadata_fields"] == ["email_date", "email_doc_type"]
        sync_request_validator.validate(compiled.sync_request)

    def test_the_bundled_schema_accepts_them_too(self, monkeypatch):
        monkeypatch.setattr(ic, "_checkout_contracts_dir", lambda: None)
        ic._validator.cache_clear()
        try:
            compiled = ic.compile_profile(_with_fields(
                fields={"doc_type": "invoice"}, field_templates=("{mandant}/**",),
                metadata_fields=("language",)))
        finally:
            ic._validator.cache_clear()
        assert compiled.sync_request["sources"][0]["fields"] == {"doc_type": "invoice"}

    @pytest.mark.parametrize("kw,present", [
        ({"fields": {"doc_type": "invoice"}}, {"fields"}),
        ({"field_templates": ("{mandant}/**",)}, {"field_templates"}),
        ({"metadata_fields": ("language",)}, {"metadata_fields"}),
        ({"fields": {}, "field_templates": (), "metadata_fields": ()}, set()),
    ])
    def test_empty_keys_are_omitted(self, kw, present):
        entry = ic.compile_profile(_with_fields(**kw)).sync_request["sources"][0]
        assert set(entry) & {"fields", "field_templates", "metadata_fields"} == present

    def test_a_template_that_does_not_compile_is_refused_by_position(self):
        with pytest.raises(ic.ProfileError) as excinfo:
            ic.compile_profile(_with_fields(field_templates=("{mandant}/**", f"{SENTINEL}*/x")))
        message = str(excinfo.value)
        assert "Ordner 1, Pfadvorlage 2" in message
        assert SENTINEL not in message

    def test_a_schema_error_under_fields_never_repeats_the_value(self):
        """jsonschema embeds the instance in its message; a ProfileError can
        reach a log line through a failed approval execution."""
        with pytest.raises(ic.ProfileError) as excinfo:
            ic.compile_profile(_with_fields(fields={"doc_type": SENTINEL * 20}))
        message = str(excinfo.value)
        assert SENTINEL not in message
        # The place and the schema keyword (here the value union), no instance.
        assert message.endswith("sources/0/fields/doc_type: oneOf")

    def test_a_schema_error_on_a_key_names_no_key_it_cannot_vouch_for(self):
        with pytest.raises(ic.ProfileError) as excinfo:
            ic.compile_profile(_with_fields(fields={"title": "x"}))
        assert "sources/0/fields" in str(excinfo.value)

    def test_redaction_counts_fields_and_names_none(self):
        text = ic.redact_for_support(_with_fields(
            fields={"mandant": SENTINEL}, field_templates=(f"{SENTINEL}/{{period}}/**",),
            metadata_fields=("language",)))
        data = json.loads(text)
        assert SENTINEL not in text and "mandant" not in text and "period" not in text
        assert data["folders_with_fields"] == 1
        assert data["field_templates"] == 1
        assert data["folders_with_metadata_fields"] == 1


class TestSourceFolderStaysAValue:
    def test_it_imports_with_defaults_and_is_hashable(self):
        assert hash(ic.SourceFolder(path="/x")) == hash(ic.SourceFolder(path="/x"))

    def test_fields_become_sorted_frozen_pairs(self):
        folder = ic.SourceFolder(path="/x", fields={"z": ["a", "b"], "a": "1"},
                                 field_templates=["{mandant}/**"],
                                 metadata_fields=["language", "language"])
        assert folder.fields == (("a", "1"), ("z", ("a", "b")))
        assert folder.field_templates == ("{mandant}/**",)
        assert folder.metadata_fields == ("language",)
        hash(folder)
        assert folder == ic.SourceFolder(path="/x", fields=(("z", ("a", "b")), ("a", "1")),
                                         field_templates=("{mandant}/**",),
                                         metadata_fields=("language",))
        assert folder.fields_json() == {"a": "1", "z": ["a", "b"]}

    def test_has_fields(self):
        assert not ic.SourceFolder(path="/x").has_fields
        assert ic.SourceFolder(path="/x", metadata_fields=("language",)).has_fields


class TestValidationAgainstTheRegistry:
    def test_a_valid_profile_passes_and_enum_labels_become_codes(self):
        profile = _with_fields(fields={"doc_type": "Rechnung", "keywords": ("a", "b"),
                                       "status": "final"},
                               field_templates=("{mandant}/{period}/**",),
                               metadata_fields=("email_date", "email_doc_type"))
        checked = ic.validate_profile_fields(profile, _registry())
        assert dict(checked.sources[0].fields) == {
            "doc_type": "invoice", "keywords": ("a", "b"), "status": "final"}

    @pytest.mark.parametrize("fields,expected", [
        ({"unbekannt": "x"}, "nicht angelegt"),
        ({"old_key": "x"}, "stillgelegt"),
        ({"doc_type": SENTINEL}, "nicht in dessen Auswahl"),
        ({"document_date": ("01.01.2024", "02.01.2024")}, "nur einen Wert"),
        ({"keywords": "x" * 300}, "l\u00e4nger als 256"),
        ({"keywords": tuple(str(i) for i in range(33))}, "mehr als 32"),
        ({"title": "x"}, "Systemfeld"),
    ])
    def test_problems_are_named_by_folder_and_key(self, fields, expected):
        with pytest.raises(ic.ProfileError) as excinfo:
            ic.validate_profile_fields(_with_fields(fields=fields), _registry())
        message = str(excinfo.value)
        assert "Ordner 1" in message and expected in message
        assert SENTINEL not in message

    def test_template_problems(self):
        with pytest.raises(ic.ProfileError) as excinfo:
            ic.validate_profile_fields(_with_fields(field_templates=(
                "{mandant}/**", "{unbekannt}/**", "{old_key}/**", "{a}/{a}")), _registry())
        message = str(excinfo.value)
        assert "Pfadvorlage 2" in message and "unbekannt" in message
        assert "Pfadvorlage 3" in message and "stillgelegt" in message
        assert "Pfadvorlage 4" in message and "zweimal" in message

    def test_metadata_targets_must_be_registered(self):
        registry = [f for f in _registry() if f["key"] != "language"]
        with pytest.raises(ic.ProfileError) as excinfo:
            ic.validate_profile_fields(_with_fields(metadata_fields=("language",)), registry)
        assert "language" in str(excinfo.value)

    def test_caps_per_source(self):
        registry = _registry() + [{"key": f"k{i}", "status": "active", "enum": [],
                                   "cardinality": "one"} for i in range(70)]
        with pytest.raises(ic.ProfileError) as excinfo:
            ic.validate_profile_fields(_with_fields(
                fields={f"k{i}": "x" for i in range(65)},
                field_templates=tuple(f"x{i}/{{mandant}}" for i in range(9))), registry)
        assert "64 feste Felder" in str(excinfo.value)
        assert "8 Pfadvorlagen" in str(excinfo.value)


class TestFolderRuleConflicts:
    def _profile(self):
        return ic.IngestionProfile(
            identifier_prefix="rc-sync",
            sources=[ic.SourceFolder(path="/a", fields={"doc_type": "invoice"},
                                     field_templates=("{mandant}/**",),
                                     metadata_fields=("language",))])

    def test_rules_inside_and_over_the_prefix_count_others_do_not(self):
        rules = [
            {"pointer_prefix": "rc-sync/Muster AG/", "set": {"mandant": "Muster AG"}},
            {"pointer_prefix": "rc-sync/", "set": {"doc_type": "contract", "status": "final"}},
            {"pointer_prefix": "rc", "set": {"mandant": None}},
            {"pointer_prefix": "other/", "set": {"doc_type": "report"}},
            {"pointer_prefix": "rc-sync/x/", "set": {"language": "de"}},
        ]
        assert ic.folder_rule_conflicts(self._profile(), rules) == {"doc_type": 1, "mandant": 2}

    def test_no_upload_keys_means_no_conflict(self):
        profile = ic.IngestionProfile(identifier_prefix="rc-sync", sources=[
            ic.SourceFolder(path="/a", metadata_fields=("language",))])
        assert ic.folder_rule_conflicts(
            profile, [{"pointer_prefix": "rc-sync/", "set": {"language": "de"}}]) == {}


class TestWhatAChangeReSends:
    def _p(self, *sources):
        return ic.IngestionProfile(identifier_prefix="p", sources=list(sources))

    def test_unchanged_new_changed_and_removed(self):
        old = self._p(ic.SourceFolder(path="/a", fields={"doc_type": "invoice"}),
                      ic.SourceFolder(path="/b", metadata_fields=("language", "email_date")),
                      ic.SourceFolder(path="/c", field_templates=("{mandant}/**",)),
                      ic.SourceFolder(path="/d"))
        new = self._p(ic.SourceFolder(path="/a", fields={"doc_type": "contract"}),
                      ic.SourceFolder(path="/b", metadata_fields=("email_date", "language")),
                      ic.SourceFolder(path="/c"),
                      ic.SourceFolder(path="/d", access_groups=("g",)),
                      ic.SourceFolder(path="/e", fields={"doc_type": "invoice"}))
        assert ic.field_config_changes(old, new) == ["/a", "/c"]

    def test_a_first_profile_re_sends_nothing(self):
        assert ic.field_config_changes(None, self._p(
            ic.SourceFolder(path="/a", fields={"doc_type": "invoice"}))) == []

    def test_counts_agree_for_objects_and_stored_json(self):
        from identity.ingestion_profiles import profile_to_json

        profile = self._p(ic.SourceFolder(path="/a", fields={"doc_type": "invoice"},
                                          field_templates=("{x}/**", "{y}/**")),
                          ic.SourceFolder(path="/b", metadata_fields=("language",)),
                          ic.SourceFolder(path="/c"))
        expected = {"folders_with_fields": 2, "folders_with_static_fields": 1,
                    "field_templates": 2, "folders_with_metadata_fields": 1}
        assert ic.field_config_counts(profile.sources) == expected
        assert ic.field_config_counts(profile_to_json(profile)["sources"]) == expected


class TestFilePropertyOptIns:
    """Spec L1: keywords and Word content status from file properties."""

    def test_the_offered_order(self):
        assert ic.METADATA_ITEMS == ("language", "email_date", "email_doc_type", "email_author",
                                     "document_author", "keywords", "document_status")

    def test_they_compile_and_validate_against_the_shipped_schema(self, sync_request_validator):
        compiled = ic.compile_profile(_with_fields(metadata_fields=("keywords", "document_status")))
        assert compiled.sync_request["sources"][0]["metadata_fields"] == ["keywords", "document_status"]
        sync_request_validator.validate(compiled.sync_request)

    def test_their_targets_must_be_registered(self):
        checked = ic.validate_profile_fields(
            _with_fields(metadata_fields=("keywords", "document_status")), _registry())
        assert checked.sources[0].metadata_fields == ("keywords", "document_status")
        registry = [f for f in _registry() if f["key"] != "status"]
        with pytest.raises(ic.ProfileError) as excinfo:
            ic.validate_profile_fields(_with_fields(metadata_fields=("document_status",)), registry)
        assert "status" in str(excinfo.value)

    def test_enabling_one_re_sends_the_folder(self):
        old = ic.IngestionProfile(identifier_prefix="p", sources=[
            ic.SourceFolder(path="/a", metadata_fields=("language",))])
        new = ic.IngestionProfile(identifier_prefix="p", sources=[
            ic.SourceFolder(path="/a", metadata_fields=("language", "keywords"))])
        assert ic.field_config_changes(old, new) == ["/a"]
