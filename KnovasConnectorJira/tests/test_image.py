"""J-4: what ships the Jira connector to a site. The image is built from the repository's root (compose's and the
release workflow's context), and its ``Dockerfile.dockerignore`` lets in only its requirements, its source and the
connector kit's package: it installs the one connector kit (``KnovasConnectorKit``, knovas-connector-kit at the
version the connector's pyproject pins) with the kit's pins; it holds no secret, runs as its own user and starts
``serve``. At a site it runs as its own component beside Knovas Link
(``deploy/docker-compose.jira.yml``): its own folder of Link's token volume, read only, the internal and egress
networks, its credentials from its own ``knovas-jira.env`` only, which no other service reads (verifier KC-3 N5: the
sync component never holds the Jira credential), and Link told to speak protocol 2.0 and to write its token. In the
repository's compose file it is off unless its profile is named and has no credential unless a file is named for it."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from knovas_connector_kit import contracts

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent
KIT = REPO / "KnovasConnectorKit"
SITE_FILE = PACKAGE / "deploy" / "docker-compose.jira.yml"


def kit_version() -> str:
    import tomllib

    return tomllib.loads((KIT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_the_image_installs_the_one_kit_at_the_version_the_connector_pins():
    """No copy of the kit beside the connector: the image installs KnovasConnectorKit itself, and the connector's
    pyproject pins exactly that package's version (CI checks the built image's installed version against it too)."""
    import tomllib

    assert not (PACKAGE / "vendor").exists()
    assert not [p for p in PACKAGE.rglob("knovas_connector_kit") if "__pycache__" not in p.parts]
    deps = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert [d for d in deps if d.startswith("knovas-connector-kit")] == [f"knovas-connector-kit=={kit_version()}"]
    text = (PACKAGE / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY KnovasConnectorKit/ /tmp/knovas-connector-kit/" in text
    assert "pip install --no-cache-dir --no-deps --no-build-isolation /tmp/knovas-connector-kit" in text
    assert "contracts.schemas(); print('kit', version('knovas-connector-kit'))" in text


def test_the_build_context_holds_only_what_the_image_needs():
    """The context is the repository's root: the Dockerfile's own ignore file leaves out everything but these."""
    lines = [line.strip() for line in (PACKAGE / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    assert lines[0] == "*"
    assert {line for line in lines if line.startswith("!")} == {
        "!KnovasConnectorJira/requirements.txt", "!KnovasConnectorJira/src", "!KnovasConnectorKit/pyproject.toml",
        "!KnovasConnectorKit/README.md", "!KnovasConnectorKit/src"}
    assert {"**/__pycache__", "**/*.pyc", "**/*.egg-info"} <= set(lines)
    copied = re.findall(r"^COPY\s+(\S+)\s", (PACKAGE / "Dockerfile").read_text(encoding="utf-8"), re.M)
    assert copied == ["KnovasConnectorJira/requirements.txt", "KnovasConnectorKit/", "KnovasConnectorJira/src/"]
    assert not (PACKAGE / ".dockerignore").exists(), "the context is no longer this folder: its ignore file is unused"


def requirement_lines(path: Path) -> set:
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "-r"))}


def test_the_image_installs_the_kits_pins_and_its_own():
    own = requirement_lines(PACKAGE / "requirements.txt")
    assert requirement_lines(KIT / "requirements.txt") <= own
    assert any(line.startswith("requests==") for line in own)
    assert not any("brain-runner" in line or "git+" in line for line in own)


def test_the_image_holds_no_secret_runs_as_its_own_user_and_serves_by_default():
    text = (PACKAGE / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM python:3\.11-slim$", text, re.M)
    assert "COPY KnovasConnectorJira/src/ ./src/" in text and "tests" not in "".join(re.findall(r"^COPY.*$", text, re.M))
    assert re.search(r"^ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1$", text, re.M) and "vendor" not in text
    assert not re.search(r"^\s*(ARG|ENV)\s+\S*(TOKEN|SECRET|CREDENTIAL|PASSWORD)", text, re.M | re.I)
    assert "--mount=type=secret" not in text and "SYNC_CREDENTIAL" not in text
    user = re.search(r"^USER (\S+)$", text, re.M)
    assert user and user.group(1) not in ("root", "0")
    assert 'ENTRYPOINT ["knovas-connector-jira"]' in text and 'CMD ["serve"]' in text


def image_env() -> dict:
    """The import path of the image: the connector's source (PYTHONPATH=/app/src) and the installed kit, here the
    kit's own source."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SYNC_") and k != "PYTHONPATH"}
    env.update(PYTHONPATH=os.pathsep.join([str(PACKAGE / "src"), str(KIT / "src")]), PYTHONDONTWRITEBYTECODE="1")
    return env


def test_in_the_image_layout_the_command_prints_the_declaration_from_the_kit(tmp_path):
    done = subprocess.run([sys.executable, "-m", "knovas_connector_jira", "declaration"], capture_output=True,
                          text=True, env=image_env(), cwd=tmp_path, timeout=60)
    assert done.returncode == 0, done.stderr
    declaration = json.loads(done.stdout)
    assert contracts.valid("declaration.schema.json", declaration) and declaration["source_kind"] == "jira"
    where = subprocess.run([sys.executable, "-c", "import knovas_connector_kit; print(knovas_connector_kit.__file__)"],
                           capture_output=True, text=True, env=image_env(), cwd=tmp_path, timeout=60).stdout
    assert Path(where.strip()).is_relative_to(KIT / "src")


# ------------------------------------------------------------------ at a site, beside the Builder's package

def test_at_a_site_it_is_its_own_component_beside_link_with_the_site_credentials_only():
    services = yaml.safe_load(SITE_FILE.read_text(encoding="utf-8"))["services"]
    assert set(services) == {"knovas-link", "jira-connector"}
    assert services["knovas-link"] == {"environment": {"LINK_PROTOCOL_V2": "true", "LINK_COMPONENTS": "jira-connector"}}
    jira = services["jira-connector"]
    assert jira["env_file"] == ["knovas-jira.env"], \
        "its own file: never knovas-link.env (no Knovas token reaches a component), never knovas-sources.env (the sync " \
        "component's)"
    assert jira["environment"] == {"SYNC_LINK_URL": "http://knovas-link:8460", "SYNC_NAME": "jira-connector"}
    assert jira["volumes"] == [{"type": "volume", "source": "link-tokens", "target": "/link", "read_only": True,
                                "volume": {"subpath": "jira-connector"}}], "only its own folder of the token volume"
    assert jira["networks"] == ["knovas-site", "knovas-egress"] and jira["depends_on"] == ["knovas-link"]
    assert jira["read_only"] is True and jira["cap_drop"] == ["ALL"]
    assert "ports" not in jira and "secrets" not in jira and "command" not in jira
    assert re.fullmatch(r"\$\{JIRA_CONNECTOR_IMAGE:\?[^}]*\}@\$\{JIRA_CONNECTOR_DIGEST:\?[^}]*\}", jira["image"]), \
        "by digest"


def test_the_site_file_names_the_builders_network_volume_and_link_address():
    """The names the Builder's install package uses (KnovasPlatform/.../builder/sites.py: INTERNAL_NETWORK,
    EGRESS_NETWORK, TOKEN_VOLUME, LINK_LOCAL_URL), read from its source so the two cannot drift apart."""
    sites = (REPO / "KnovasPlatform" / "components" / "docbridge_integration" / "src" / "builder" / "sites.py") \
        .read_text(encoding="utf-8")
    for constant, value in (("INTERNAL_NETWORK", "knovas-site"), ("EGRESS_NETWORK", "knovas-egress"),
                            ("TOKEN_VOLUME", "link-tokens"), ("LINK_LOCAL_URL", "http://knovas-link:8460"),
                            ("SOURCES_ENV_FILE", "knovas-sources.env"), ("CONNECTOR_ENV_FILE", "knovas-{kind}.env")):
        assert re.search(rf'^{constant} = "{re.escape(value)}"$', sites, re.M), constant
    declared = json.loads(subprocess.run([sys.executable, "-m", "knovas_connector_jira", "declaration"],
                                         capture_output=True, text=True, env=image_env(), timeout=60).stdout)
    assert yaml.safe_load(SITE_FILE.read_text(encoding="utf-8"))["services"]["jira-connector"]["env_file"] == \
        ["knovas-{kind}.env".format(kind=declared["source_kind"])], "the file the Builder names for a jira source"


def test_in_the_repositorys_compose_file_it_is_off_unless_named_and_has_no_credential_unless_a_file_is_named():
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    jira = compose["jira-connector"]
    assert jira["profiles"] == ["jira-connector"]
    assert jira["build"] == {"context": ".", "dockerfile": "KnovasConnectorJira/Dockerfile"}, \
        "the repository's root, so that the connector kit is in the context"
    assert jira["image"] == "jira-connector:0.1.0"
    assert jira["env_file"] == ["${JIRA_CONNECTOR_ENV_FILE:-/dev/null}"]
    assert "ports" not in jira and "secrets" not in jira and "environment" not in jira
    example = (REPO / "knovas.env.example").read_text(encoding="utf-8")
    assert "# JIRA_CONNECTOR_ENV_FILE=./knovas-jira.env" in example and "SOURCES_ENV" not in example
    assert not re.search(r"^SYNC_CREDENTIAL", example, re.M), "a credential never goes into knovas.env"
