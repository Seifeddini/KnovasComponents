"""The workspace store on PostgreSQL (build plan step B1, migration 0005)."""
import psycopg
import pytest

from builder import package
from builder.store import WorkspaceStore
from builder_harness import SAMPLE_PACKAGE, MemoryWorkspaceStore
from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)


@pytest.fixture(scope="module")
def model():
    return package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model


@pytest.fixture
def consultant(identity_repo):
    return identity_repo.create(email="beraterin@example.org", display_name="Beraterin",
                                password="korrektes-pferd-batterie")


@pytest.fixture(params=["postgres", "memory"])
def store(request, platform_db, consultant):
    """Both stores, held to the same behaviour: the click-dummy may not lie."""
    if request.param == "memory":
        return MemoryWorkspaceStore({str(consultant.id): consultant.display_name})
    return WorkspaceStore(platform_db)


def _source():
    return {"kind": "zip", "bytes": 10, "sha256": "0" * 64, "types": 4, "relations": 3}


def test_an_import_becomes_a_workspace_with_one_draft(store, model, consultant):
    workspace_id = store.create_from_import(name=" Beispiel AG ", model=model, actor_id=consultant.id, source=_source())
    workspace = store.get(workspace_id)
    assert workspace.name == "Beispiel AG"
    assert workspace.draft_number == 1
    assert workspace.published_number is None
    assert workspace.tenant is None
    assert workspace.summary["types"] == 4
    assert store.model(workspace_id) == model


def test_the_import_is_the_first_change(store, model, consultant):
    workspace_id = store.create_from_import(name="Beispiel AG", model=model, actor_id=consultant.id, source=_source())
    workspace = store.get(workspace_id)
    assert workspace.last_change["path"] == "import"
    assert workspace.last_change["actor_name"] == "Beraterin"
    [change] = store.changes(workspace_id)
    assert change["after"]["content_hash"] == package.content_hash(model)
    assert change["after"]["types"] == 4


def test_the_list_is_sorted_by_name(store, model, consultant):
    for name in ("zeta", "Alpha", "beta"):
        store.create_from_import(name=name, model=model, actor_id=consultant.id, source=_source())
    assert [w.name for w in store.list()] == ["Alpha", "beta", "zeta"]


def test_an_unknown_or_malformed_id_is_none(store):
    assert store.get("00000000-0000-0000-0000-000000000000") is None
    assert store.get("not-a-uuid") is None
    assert store.model("00000000-0000-0000-0000-000000000000") is None


def test_the_database_allows_one_draft_per_workspace(platform_db, model, consultant):
    store = WorkspaceStore(platform_db)
    workspace_id = store.create_from_import(name="Beispiel AG", model=model, actor_id=consultant.id, source=_source())
    with pytest.raises(psycopg.errors.UniqueViolation):
        platform_db.execute(
            """INSERT INTO builder_version (workspace, number, state, model, content_hash)
               VALUES (%s, 2, 'draft', '{}'::jsonb, %s)""",
            (workspace_id, "0" * 64),
        )


def test_a_published_version_must_say_when(platform_db, model, consultant):
    store = WorkspaceStore(platform_db)
    workspace_id = store.create_from_import(name="Beispiel AG", model=model, actor_id=consultant.id, source=_source())
    with pytest.raises(psycopg.errors.CheckViolation):
        platform_db.execute(
            """INSERT INTO builder_version (workspace, number, state, model, content_hash)
               VALUES (%s, 2, 'published', '{}'::jsonb, %s)""",
            (workspace_id, "0" * 64),
        )
