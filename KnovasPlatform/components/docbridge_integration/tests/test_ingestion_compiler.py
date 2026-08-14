"""Compiling one ingestion profile into the two RemoteController documents.

KC-IN-6. The point of the compiler is that the "two configuration layers" in
RemoteController/docs/configuration.md stop being the administrator's problem.
These tests pin the properties that make that true — above all that the
compiled documents validate against the schemas RemoteController itself ships,
and that max_document_age_seconds is written to exactly one of them.
"""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from identity import ingestion_compiler as ic
from identity import ingestion_presets as presets

_CONTRACTS = (
    Path(__file__).resolve().parents[4] / "RemoteController" / "contracts"
)


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((_CONTRACTS / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


@pytest.fixture(scope="module")
def sync_config_validator():
    return _validator("remote_controller_sync_config.schema.json")


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
    valid before must still be valid, or existing RemoteController deployments
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
