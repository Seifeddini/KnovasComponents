"""Configuration package <-> workspace model (build plan step B1).

The promise under test: import then export gives files that load to the same
data, and the same model always gives the same bytes. Checked on a small
fictitious package that carries the awkward cases on purpose, and -- when a
KnowledgeBase checkout is at hand -- on the Client Zero package itself, which
is the step's acceptance.
"""
import io
import os
import zipfile
from pathlib import Path

import pytest

from builder import package, schema
from builder_harness import SAMPLE_PACKAGE

#: The Client Zero package of a KnowledgeBase checkout (docs/ModernDocs/client-zero).
#: Set KNOWLEDGEBASE_CLIENT_ZERO to run the acceptance round trip.
CLIENT_ZERO = os.environ.get("KNOWLEDGEBASE_CLIENT_ZERO", "")


def _zip(files, prefix=""):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, data in files.items():
            archive.writestr(prefix + path, data)
    return buffer.getvalue()


def _semantic_differences(original_files, exported_files):
    """Paths whose data differ after the round trip, plus files gained or lost."""
    differences = []
    for path, data in exported_files.items():
        if path not in original_files:
            differences.append(f"{path}: only in the export")
        elif path.endswith(".json") and original_files[path] != data:
            differences.append(f"{path}: different bytes")
        elif not path.endswith(".json") and package.load_yaml(original_files[path]) != package.load_yaml(data):
            differences.append(f"{path}: different data")
    return differences


def _listed(files):
    """The files brain-config.yaml lists: what an export must give back."""
    manifest = package.load_yaml(files[package.PACKAGE_FILE])
    listed = {package.PACKAGE_FILE, *manifest["ontology"].values()}
    listed |= {manifest[p] for p in package.DOCUMENT_PARTS if p in manifest}
    for part in package.COLLECTION_PARTS:
        listed |= set(package._glob(files, manifest[part]))
    for part in ("generated", "reports"):  # kept byte for byte
        listed |= {path for path in (manifest.get(part) or {}).values() if path in files}
    return listed


@pytest.fixture(scope="module")
def sample_files():
    return package.files_from_directory(SAMPLE_PACKAGE)


@pytest.fixture(scope="module")
def sample(sample_files):
    return package.read_package(sample_files)


class TestTheRoundTrip:
    def test_export_gives_back_every_listed_file_with_the_same_data(self, sample_files, sample):
        exported = package.write_package(sample.model)
        assert set(exported) == _listed(sample_files)
        assert _semantic_differences(sample_files, exported) == []

    def test_the_same_model_gives_the_same_bytes(self, sample):
        first = package.zip_files(package.write_package(sample.model))
        again = package.read_package(package.files_from_zip(first))
        second = package.zip_files(package.write_package(again.model))
        assert first == second
        assert package.content_hash(again.model) == package.content_hash(sample.model)

    def test_keys_are_written_sorted(self, sample):
        exported = package.write_package(sample.model)[package.PACKAGE_FILE].decode("utf-8")
        top_level = [line.split(":")[0] for line in exported.splitlines() if line and not line.startswith(" ")]
        assert top_level == sorted(top_level)

    def test_a_date_is_text_in_the_model_and_a_date_again_in_the_export(self, sample):
        import datetime

        import yaml

        assert sample.model["health"]["types"]["Mandate"]["last_review"] == "2026-10-01"
        exported = package.write_package(sample.model)["health.yaml"]
        # Any other reader still sees the date the package had.
        assert yaml.safe_load(exported)["types"]["Mandate"]["last_review"] == datetime.date(2026, 10, 1)

    def test_reading_a_package_leaves_yaml_alone_for_everyone_else(self):
        import datetime

        import yaml

        package.load_yaml(b"a: 2026-10-01")
        assert yaml.safe_load("a: 2026-10-01")["a"] == datetime.date(2026, 10, 1)

    @pytest.mark.skipif(not CLIENT_ZERO, reason="KNOWLEDGEBASE_CLIENT_ZERO is not set")
    def test_client_zero_round_trips_without_a_semantic_difference(self):
        files = package.files_from_directory(Path(CLIENT_ZERO))
        result = package.read_package(files)
        exported = package.write_package(result.model)
        assert set(exported) == _listed(files)
        assert _semantic_differences(files, exported) == []
        assert result.summary["types"] == 71
        # And through a zip of the whole folder, as the import dialog takes it.
        again = package.read_package(package.files_from_zip(package.zip_files(exported)))
        assert package.content_hash(again.model) == package.content_hash(result.model)


class TestTheModel:
    def test_it_matches_the_schema(self, sample):
        assert schema.problems(sample.model) == []

    def test_types_join_the_terms_and_descriptions(self, sample):
        types = {t["key"]: t for t in sample.model["ontology"]["types"]}
        mandate = types["Mandate"]
        assert mandate["level"] == "pack"
        assert mandate["label"] == {"de": "Mandat", "en": "Mandate"}
        assert mandate["description"] == {"en": "One engagement the firm holds for a client."}
        assert mandate["example"] == "MAN-0001, annual accounts 2026."
        assert types["Organization"]["not_to_confuse"] == "A person is not an organisation."
        assert types["Organization"]["level"] == "core"
        assert types["Deadline"]["level"] == "company"
        assert types["Deadline"]["level_label"] == "beispiel"
        assert types["Deadline"]["parent"] == "Mandate"

    def test_attributes_carry_their_descriptions_and_enum_labels(self, sample):
        types = {t["key"]: t for t in sample.model["ontology"]["types"]}
        status = next(a for a in types["Mandate"]["attributes"] if a["name"] == "status")
        assert status["label"] == {"de": "Status", "en": "Status"}
        assert status["enum_labels"]["active"]["de"] == "aktiv"
        assert status["level"] == "pack"
        opened = next(a for a in types["Mandate"]["attributes"] if a["name"] == "opened_on")
        assert opened["note"] == "The day the mandate letter was signed."
        assert opened["example"] == "2026-01-15"

    def test_unknown_keys_are_kept_beside_the_item(self, sample):
        types = {t["key"]: t for t in sample.model["ontology"]["types"]}
        assert types["Deadline"]["extra"] == {"review_hint": "monthly"}
        assert types["Deadline"]["description_extra"] == {"reviewed": False}
        assert sample.model["ontology"]["descriptions_rest"]["types"]["Retired"]["en"] == "Retired"

    def test_relations_get_a_level_from_whether_they_map_to_a_core_relation(self, sample):
        relations = {r["key"]: r for r in sample.model["ontology"]["relations"]}
        assert relations["client_of"]["level"] == "core"
        assert relations["due_in"]["level"] == "company"
        assert relations["due_in"]["sentence"] == "Deadline is due in mandate."
        assert relations["due_in"]["not_to_confuse"] == "Not the mandate's own end date."
        assert "level_label" not in relations["due_in"]

    def test_a_new_type_is_written_with_the_package_s_level_names(self, sample):
        model = package.load_yaml(package.canonical_json(sample.model))
        model["ontology"]["types"].append({"key": "Invoice", "level": "company", "attributes": []})
        terms = package.load_yaml(package.write_package(model)["ontology/terms.yaml"])
        assert terms["node_types"][-1] == {"name": "Invoice", "level": "beispiel", "attributes": []}


class TestABlankPackage:
    def test_it_holds_the_company_and_nothing_else(self):
        result = package.read_package(package.blank_package(" Muster Treuhand AG "))
        assert result.model["company"] == {"key": "MUSTER-TREUHAND-AG", "name": "Muster Treuhand AG"}
        assert result.summary["types"] == result.summary["relations"] == 0
        # Every part brain-runner's manifest asks for is there; only the folders of mappings and profiles are empty.
        assert len(result.warnings) == 2 and all("findet keine Datei" in w for w in result.warnings)
        assert schema.problems(result.model) == []

    def test_it_exports_to_the_same_bytes(self):
        files = package.blank_package("Muster AG")
        assert package.write_package(package.read_package(files).model) == files

    @pytest.mark.parametrize("name,key", [
        ("Müller & Söhne GmbH", "MULLER-SOHNE-GMBH"),
        ("Straße 12 AG", "STRASSE-12-AG"),
        ("???", "COMPANY"),
    ])
    def test_the_company_key_comes_from_the_name(self, name, key):
        assert package.company_key(name) == key


class TestTheSummary:
    def test_it_counts_each_section(self, sample):
        s = sample.summary
        assert (s["types"], s["attributes"], s["relations"]) == (4, 5, 3)
        assert s["levels"] == {"core": 2, "pack": 1, "company": 1}
        assert (s["processes"], s["spines"], s["pages"], s["mappings"], s["profiles"], s["health"]) == (1, 1, 1, 1, 1, 1)
        assert s["company"] == "Beispiel AG"

    def test_it_warns_about_what_a_consultant_should_look_at(self, sample):
        warnings = " ".join(sample.summary["warnings"])
        assert "1 unbekannte Typen" in warnings
        assert "1 Beziehungen fehlt" in warnings


class TestReadingAZip:
    def test_the_package_may_sit_in_a_folder(self, sample_files):
        files = package.files_from_zip(_zip(sample_files, prefix="repo/docs/client/"))
        assert set(files) == set(sample_files)

    def test_other_files_are_left_out_and_counted(self, sample_files):
        files = package.files_from_zip(_zip({**sample_files, "reports/old.yaml": b"a: 1", "notes.md": b"# x"}))
        assert "notes.md" not in files
        assert package.read_package(files).summary["ignored_files"] == 1

    def test_not_a_zip(self):
        with pytest.raises(package.PackageError, match="kein ZIP"):
            package.files_from_zip(b"plain text")

    def test_no_package_file(self):
        with pytest.raises(package.PackageError, match="fehlt die Datei brain-config.yaml"):
            package.files_from_zip(_zip({"ontology/terms.yaml": b"version: 1"}))

    def test_two_packages_in_one_zip(self, sample_files):
        data = _zip({"a/brain-config.yaml": sample_files["brain-config.yaml"],
                     "b/brain-config.yaml": sample_files["brain-config.yaml"]})
        with pytest.raises(package.PackageError, match="mehrere"):
            package.files_from_zip(data)

    def test_a_path_out_of_the_package_is_refused(self, sample_files):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("brain-config.yaml", sample_files["brain-config.yaml"])
            archive.writestr("ontology/../../evil.yaml", b"a: 1")
        with pytest.raises(package.PackageError, match="unzulässigen Pfad"):
            package.files_from_zip(buffer.getvalue())

    def test_a_listed_file_that_is_missing(self, sample_files):
        files = {k: v for k, v in sample_files.items() if k != "ontology/spines.yaml"}
        with pytest.raises(package.PackageError, match="ontology/spines.yaml fehlt"):
            package.read_package(files)

    def test_broken_yaml_names_the_file_and_line(self, sample_files):
        files = {**sample_files, "ontology/owners.yaml": b"version: 1\naliases: [unclosed\n"}
        with pytest.raises(package.PackageError, match=r"ontology/owners.yaml ist kein gültiges YAML \(Zeile \d+\)"):
            package.read_package(files)

    def test_yaml_cannot_build_python_objects(self, sample_files):
        files = {**sample_files, "ontology/owners.yaml": b"x: !!python/object/apply:os.system ['echo hi']\n"}
        with pytest.raises(package.PackageError, match="kein gültiges YAML"):
            package.read_package(files)

    def test_a_model_that_breaks_the_schema_is_refused(self, sample_files):
        terms = sample_files["ontology/terms.yaml"].replace(b"meta: true", b"meta: sometimes")
        with pytest.raises(package.PackageError) as caught:
            package.read_package({**sample_files, "ontology/terms.yaml": terms})
        assert any("meta" in detail for detail in caught.value.details)


class TestTheZip:
    def test_entries_are_sorted_and_undated(self, sample):
        data = package.zip_files(package.write_package(sample.model))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            assert names == sorted(names)
            assert {info.date_time for info in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}
