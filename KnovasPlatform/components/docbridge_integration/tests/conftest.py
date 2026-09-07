import os
import uuid

import pytest

# ── identity: a real PostgreSQL per test ───────────────────────────────────
#
# The identity schema uses citext, uuid, jsonb, inet and CHECK constraints, so
# these tests run against the real thing. psycopg is imported lazily inside the
# fixture: an environment without the identity extras must still collect and
# run the rest of the suite.

PLATFORM_DB_TEST_DSN = os.environ.get(
    "PLATFORM_DB_TEST_DSN",
    "postgresql://platform:testpw@127.0.0.1:55433/knovas_platform_test",
)


def platform_db_reachable() -> bool:
    """Whether the identity tests can run. Skipping is a developer convenience.

    In CI it is not: a database that failed to come up would turn roughly 180
    identity and admin tests into skips and leave the run green, which is the
    one outcome worse than a red build. PLATFORM_DB_REQUIRED (set by the
    workflow) turns an unreachable database into a loud collection error.
    """
    required = os.environ.get("PLATFORM_DB_REQUIRED", "").strip().lower() in {
        "1", "true", "yes",
    }
    try:
        import psycopg
    except ImportError:
        if required:
            raise RuntimeError(
                "PLATFORM_DB_REQUIRED is set but psycopg is not installed, so the "
                "identity and admin tests would silently skip. Install the identity "
                "extras from requirements.txt."
            )
        return False
    try:
        with psycopg.connect(PLATFORM_DB_TEST_DSN, connect_timeout=3):
            return True
    except Exception as exc:
        if required:
            raise RuntimeError(
                f"PLATFORM_DB_REQUIRED is set but {PLATFORM_DB_TEST_DSN} is "
                f"unreachable ({exc}). Refusing to skip the identity and admin "
                "tests into a green run."
            ) from exc
        return False


@pytest.fixture
def platform_db():
    """A migrated identity database in a schema of its own, dropped after.

    Per-test schema rather than a transaction rollback, because the migration
    runner commits DDL — a rollback would not undo it.
    """
    import psycopg

    from identity import migrate

    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as setup:
        setup.execute(f'CREATE SCHEMA "{schema}"')
    conn = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True)
    conn.execute(f'SET search_path TO "{schema}"')
    migrate.apply(conn)
    try:
        yield conn
    finally:
        conn.close()
        with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as cleanup:
            cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _identity_app(platform_db, tmp_path, monkeypatch, *, client_cls=None):
    """The real Flask app, with per-user identity on and pointed at platform_db.

    The app opens its own connections, so it must reach the same schema the
    fixture migrated — passed through as a search_path in the DSN.

    ``client_cls`` is the Knovas client the app is built with; it defaults to
    DummyKnovasClient, resolved in the body because that class is defined
    further down this module.
    """
    client_cls = client_cls or DummyKnovasClient
    schema = platform_db.execute("SELECT current_schema()").fetchone()[0]
    monkeypatch.setenv(
        "PLATFORM_DB_DSN", f"{PLATFORM_DB_TEST_DSN}?options=-csearch_path%3D{schema}"
    )
    monkeypatch.delenv("PLATFORM_DB_PASSWORD_FILE", raising=False)

    # create_app() boots the identity schema and the first administrator, the
    # way gunicorn does in production, so the fixture has to supply the same
    # bootstrap values a deployment does. The address is deliberately not one
    # the per-test `people` fixtures use, so a test that lists accounts sees
    # its own cast plus this one rather than a surprising collision.
    monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", "bootstrap@kanzlei.ch")
    monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "bootstrap-korrektes-pferd")
    monkeypatch.setenv(
        "PLATFORM_ADMIN_BOOTSTRAP_PATH", (tmp_path / "admin-bootstrap").as_posix()
    )

    broker_dir = (tmp_path / "broker").as_posix()
    (tmp_path / "broker").mkdir(exist_ok=True)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        'web:\n'
        '  secret_key: "a-strong-secret-for-tests-0123456789"\n'
        '  session_lifetime: 3600\n'
        '  session_cookie_secure: false\n'
        '  login:\n'
        '    enabled: true\n'
        '    company_name: "TestCo"\n'
        '  search:\n'
        '    results_per_page: 20\n'
        'identity:\n'
        '  enabled: true\n'
        f'  broker_key_dir: "{broker_dir}"\n'
        'api:\n'
        '  base_url: "http://example.test"\n'
        '  customer_id: "tenant-a"\n'
        'open:\n'
        '  companion_enabled: false\n',
        encoding="utf-8",
    )

    DummyKnovasClient.health_result = True
    from web_interface import app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", client_cls)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", DummyFileHandler)

    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def identity_app(platform_db, tmp_path, monkeypatch):
    return _identity_app(platform_db, tmp_path, monkeypatch)


@pytest.fixture
def identity_client(identity_app):
    return identity_app.test_client()


@pytest.fixture
def identity_repo(platform_db):
    from identity import users

    return users.UserRepository(platform_db)


def pytest_addoption(parser):
    parser.addoption(
        "--knovas-api",
        action="store_true",
        default=False,
        help="Also run tests that require real Knovas API connectivity",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--knovas-api"):
        skip = pytest.mark.skip(reason="Pass --knovas-api to run live Knovas API tests")
        for item in items:
            if "knovas_api" in item.keywords:
                item.add_marker(skip)


class DummyKnovasClient:
    """Controllable mock. Set DummyKnovasClient.health_result before creating the app."""

    health_result = True
    last_instance = None
    customer_id = "tenant-a"

    def __init__(self, config):
        self.config = config
        self.principal_broker = None
        self.acl_calls: list[tuple] = []
        self.fail_next = False
        # Pointers the content gate must refuse. Empty means "everything is
        # readable", which keeps every test that predates the wall unchanged.
        self.denied_pointers: set[str] = set()
        self.readable_calls: list[str] = []
        DummyKnovasClient.last_instance = self

    def document_readable(self, pointer):
        self.readable_calls.append(str(pointer))
        return str(pointer) not in self.denied_pointers

    def attach_principal_broker(self, broker):
        self.principal_broker = broker

    def health_check(self):
        return DummyKnovasClient.health_result

    def search_documents(self, query, limit=20, filters=None):
        return {"results": [], "total": 0}

    # -- what the console's Dokumente / Zugriffsgruppen tabs call --------
    def documents(self, **kw):
        return {"documents": [], "next_after": None, "total_count": 0}

    def access_groups(self):
        return [{"group_id": "g-lit", "name": "Litigation", "parent_id": None}]

    def folder_rules(self):
        return []

    def set_document_access(self, pointer, access_groups, acting_as=None):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated backend failure")
        self.acl_calls.append(("set_document_access", pointer, list(access_groups)))
        return {"pointer": pointer, "access_groups": list(access_groups)}

    def create_folder_rule(self, pointer_prefix, access_groups, acting_as=None):
        self.acl_calls.append(("create_folder_rule", pointer_prefix, list(access_groups)))
        return {"rule_id": "r-new", "pointer_prefix": pointer_prefix}

    def update_folder_rule(self, rule_id, access_groups, acting_as=None):
        self.acl_calls.append(("update_folder_rule", rule_id, list(access_groups)))
        return {"rule_id": rule_id}

    def delete_folder_rule(self, rule_id):
        self.acl_calls.append(("delete_folder_rule", rule_id, []))
        return True


class DummyFileHandler:
    autodoc_path = "/tmp"


@pytest.fixture
def docbridge_app(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-key-for-health-checks")
    monkeypatch.setenv("COMPANY_LOGIN_ENABLED", "true")
    monkeypatch.setenv("COMPANY_DISPLAY_NAME", "TestCo")
    monkeypatch.setenv("COMPANY_LOGIN_NAME", "healthuser")
    monkeypatch.setenv("COMPANY_LOGIN_PASSWORD", "healthpass123")

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
web:
  secret_key: "${WEB_SECRET_KEY}"
  session_lifetime: 3600
  login:
    enabled: "${COMPANY_LOGIN_ENABLED:-true}"
    company_name: "${COMPANY_DISPLAY_NAME:-Knovas}"
    username: "${COMPANY_LOGIN_NAME}"
    password: "${COMPANY_LOGIN_PASSWORD}"
  search:
    results_per_page: 20
identity:
  enabled: false
api:
  base_url: "http://example.test"
open:
  companion_enabled: false
""",
        encoding="utf-8",
    )

    DummyKnovasClient.health_result = True  # reset to healthy for each test

    from web_interface import app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", DummyFileHandler)

    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Under CI a PostgreSQL skip is a failure, not a pass.

    151 identity tests skipped silently for weeks because an unreachable
    database looked exactly like a green run. In CI we would rather be
    loudly broken than quietly untested. Locally the skip stays a skip.
    """
    outcome = yield
    report = outcome.get_result()
    if os.environ.get("CI") != "true":
        return
    if report.skipped and "No PostgreSQL" in str(report.longrepr):
        report.outcome = "failed"
        report.longrepr = (
            "PostgreSQL was unreachable in CI. Identity tests must execute, "
            "not skip -- a skipped security test is a test that does not exist."
        )


# ── typed-node workbench (SS-315): graph mode, people, grants ─────────────

PASSWORD = "korrektes-pferd-batterie"


class FakeGraphApi(DummyKnovasClient):
    """The Knowledge Graph client as the workbench sees it, in memory.

    Instance state, seeded by tests through the `fake_graph` fixture; the app
    holds the same instance because create_app constructs exactly one client.
    Response shapes follow Knowledge_Graph_API.md (`{"node": …}`,
    `{"attribute": …}`, `{"neighbors": …, "edges": …}`).
    """

    current = None

    def __init__(self, config, principal_broker=None):
        # DummyKnovasClient takes the config alone; a broker is attached after
        # construction, exactly as create_app does it.
        super().__init__(config)
        if principal_broker is not None:
            self.attach_principal_broker(principal_broker)
        self.node_types = [{"id": "t1", "name": "Mandat"}]
        self.schema = {}
        self.nodes = {"n1": {"id": "n1", "name": "Müller AG", "node_type_id": "t1"}}
        self.facts = {"n1": []}
        self.neighbours = {}
        self.last_attribute = self.last_node_filters = None
        self.last_fact = self.last_neighbours = None
        self.deprecated = []
        FakeGraphApi.current = self

    # node types + schema
    def graph_node_types(self):
        return list(self.node_types)

    def graph_create_node_type(self, name):
        created = {"id": f"t{len(self.node_types) + 1}", "name": name}
        self.node_types.append(created)
        return {"node_type": created}

    def graph_update_node_type(self, type_id, **fields):
        return {"node_type": {"id": type_id, **fields}}

    def graph_schema(self, type_id, include_deprecated=False):
        # None for a type that does not exist, [] for a type without fields —
        # the distinction the real client makes since it stopped folding 404
        # into an empty list.
        known = {t["id"] for t in self.node_types}
        if type_id not in known:
            return None
        return list(self.schema.get(type_id, []))

    def graph_create_schema_attribute(self, type_id, name, datatype="entity_ref",
                                      required=False, description=None, sort_order=0,
                                      enum_values=None, target_node_type_id=None):
        attribute = {"id": f"a{sum(len(v) for v in self.schema.values()) + 1}",
                     "name": name, "datatype": datatype, "required": required,
                     "sort_order": sort_order, "enum_values": enum_values,
                     "target_node_type_id": target_node_type_id}
        self.schema.setdefault(type_id, []).append(attribute)
        self.last_attribute = attribute
        return {"attribute": attribute}

    def graph_update_schema_attribute(self, type_id, attribute_id, **fields):
        return {"attribute": {"id": attribute_id, **fields}}

    def graph_deprecate_schema_attribute(self, type_id, attribute_id):
        self.deprecated.append((type_id, attribute_id))
        return {"status": "success"}

    # nodes
    def graph_nodes(self, node_type_id=None, q=None):
        self.last_node_filters = {k: v for k, v in (("node_type_id", node_type_id), ("q", q)) if v}
        return [n for n in self.nodes.values()
                if (not node_type_id or n.get("node_type_id") == node_type_id)
                and (not q or q.lower() in n["name"].lower())]

    def graph_create_node(self, name, node_type_id=None):
        # A real kg_nodes id is a UUID and node_grants.node_id is a UUID column,
        # so a created node must carry one — a grant on "n2" would not survive
        # the insert. The seeded "n1" keeps its short, readable id: nothing
        # grants on it.
        node = {"id": str(uuid.uuid4()), "name": name, "node_type_id": node_type_id}
        self.nodes[node["id"]] = node
        self.facts[node["id"]] = []
        return {"node": node}

    def graph_node(self, node_id):
        node = self.nodes.get(node_id)
        return None if node is None else {"node": node, "facts": self.facts.get(node_id, [])}

    def graph_update_node(self, node_id, **fields):
        # Mirrors the real client: a PATCH with nothing to write is a caller
        # error, not a read. A fake that quietly answered would let a route
        # ship the very bug the client refuses.
        if not fields:
            raise ValueError("graph_update_node braucht mindestens ein Feld")
        if node_id not in self.nodes:
            return None
        self.nodes[node_id].update(fields)
        return {"node": self.nodes[node_id]}

    # facts + neighbours
    def graph_facts(self, node_id):
        # None for a node that does not exist, [] for a node without facts.
        if node_id not in self.nodes:
            return None
        return list(self.facts.get(node_id, []))

    def graph_create_fact(self, node_id, value, attribute_id=None, label=None):
        # The server's CHECK, mirrored: a fake that accepted a fact with
        # neither would let a route ship what the real client refuses.
        if not attribute_id and not label:
            raise ValueError("a fact needs an attribute_id or a label")
        if node_id not in self.nodes:
            return None
        fact = {"id": f"f{len(self.facts[node_id]) + 1}", "attribute_id": attribute_id,
                "label": label, "value": value}
        self.facts[node_id].append(fact)
        self.last_fact = fact
        return {"fact": fact}

    def graph_update_fact(self, fact_id, **fields):
        return {"fact": {"id": fact_id, **fields}}

    def graph_delete_fact(self, fact_id):
        return {"status": "success"}

    def graph_neighbors(self, node_id, depth=1, include_edges=False):
        self.last_neighbours = {"node_id": node_id, "depth": depth,
                                "include_edges": include_edges}
        return self.neighbours.get(node_id, {"neighbors": [], "edges": []})


class _ApiClient:
    """A signed-in test client that sends the session's CSRF token on every
    request, exactly as static/js/app.js does."""

    def __init__(self, client, token):
        self._client, self._token = client, token

    def open(self, *args, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("X-CSRF-Token", self._token)
        return self._client.open(*args, headers=headers, **kwargs)

    def get(self, *a, **kw):
        return self.open(*a, method="GET", **kw)

    def post(self, *a, **kw):
        return self.open(*a, method="POST", **kw)

    def patch(self, *a, **kw):
        return self.open(*a, method="PATCH", **kw)

    def delete(self, *a, **kw):
        return self.open(*a, method="DELETE", **kw)


def _csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


def _signed_in(app, email, *, with_csrf=True):
    client = app.test_client()
    page = client.get("/login")
    client.post("/login", data={"login_name": email, "password": PASSWORD,
                                "csrf_token": _csrf_from(page.data.decode("utf-8"))})
    if not with_csrf:
        return client
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    return _ApiClient(client, token)


def _person(identity_repo, email, display_name, role):
    user = identity_repo.create(email=email, display_name=display_name, password=PASSWORD)
    identity_repo.grant_role(user.id, role)
    return identity_repo.get(user.id)


@pytest.fixture
def workbench_app(platform_db, tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_SOURCE", "graph")
    return _identity_app(platform_db, tmp_path, monkeypatch, client_cls=FakeGraphApi)


@pytest.fixture
def fixture_mode_app(platform_db, tmp_path, monkeypatch):
    monkeypatch.delenv("ONTOLOGY_SOURCE", raising=False)
    return _identity_app(platform_db, tmp_path, monkeypatch, client_cls=FakeGraphApi)


@pytest.fixture
def fake_graph(workbench_app):
    return FakeGraphApi.current


@pytest.fixture
def grants(platform_db):
    from identity.node_grants import NodeGrantStore

    return NodeGrantStore(platform_db)


@pytest.fixture
def alice(identity_repo):
    return _person(identity_repo, "alice@kanzlei.ch", "Alice", "member")


@pytest.fixture
def bob(identity_repo):
    return _person(identity_repo, "bob@kanzlei.ch", "Bob", "member")


@pytest.fixture
def carol(identity_repo):
    return _person(identity_repo, "carol@kanzlei.ch", "Carol", "member")


@pytest.fixture
def member(identity_repo):
    return _person(identity_repo, "mia@kanzlei.ch", "Mia", "member")


@pytest.fixture
def platform_admin(identity_repo):
    return _person(identity_repo, "chef@kanzlei.ch", "Chef", "admin")


@pytest.fixture
def anon_client(workbench_app):
    return workbench_app.test_client()


@pytest.fixture
def member_client(workbench_app, member):
    return _signed_in(workbench_app, member.email)


@pytest.fixture
def alice_client(workbench_app, alice):
    return _signed_in(workbench_app, alice.email)


@pytest.fixture
def bob_client(workbench_app, bob):
    return _signed_in(workbench_app, bob.email)


@pytest.fixture
def admin_client(workbench_app, platform_admin):
    return _signed_in(workbench_app, platform_admin.email)


@pytest.fixture
def admin_client_no_csrf(workbench_app, platform_admin):
    return _signed_in(workbench_app, platform_admin.email, with_csrf=False)


@pytest.fixture
def fixture_mode_client(fixture_mode_app, member):
    return _signed_in(fixture_mode_app, member.email)


@pytest.fixture
def node_owned_by_alice(fake_graph, grants, alice):
    node = fake_graph.graph_create_node("Alices Akte", node_type_id="t1")["node"]
    grants.set_owner(node["id"], alice.id)
    return node["id"]
