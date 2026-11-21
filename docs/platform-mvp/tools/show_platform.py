"""The real Platform app (identity on, a fake Knovas) for screenshots. Coordinator copy of the PMVP-10 builder's scratch tool, to show the app locally. Not product code.

    py -3 serve.py --p <.../docbridge_integration> --port 5810 --tmp <dir>

Invented people and documents only. The schema lives in the builder's own test database.
"""
import argparse
import os
import sys
import uuid
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--p", required=True)
ap.add_argument("--port", type=int, default=5810)
ap.add_argument("--tmp", required=True)
args = ap.parse_args()

P = Path(args.p)
sys.path[:0] = [str(P / "src"), str(P / "tests")]
os.chdir(P)

import psycopg  # noqa: E402
import pytest  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import conftest  # noqa: E402
from identity import migrate, users  # noqa: E402

dsn = conftest.PLATFORM_DB_TEST_DSN
assert dsn.endswith("/knovas_platform_show"), dsn
schema = f"shots{uuid.uuid4().hex[:8]}"
with psycopg.connect(dsn, autocommit=True) as setup:
    setup.execute(f'CREATE SCHEMA "{schema}"')
conn = psycopg.connect(dsn, autocommit=True)
conn.execute(f'SET search_path TO "{schema}"')
migrate.apply(conn)

mp = pytest.MonkeyPatch()
mp.setenv("ONTOLOGY_SOURCE", "graph")
tmp = Path(args.tmp)
tmp.mkdir(parents=True, exist_ok=True)
app = conftest._identity_app(conn, tmp, mp, client_cls=conftest.FakeGraphApi)

repo = users.UserRepository(conn)
conftest._person(repo, "chef@kanzlei.ch", "Lea Brunner", "admin")
conftest._person(repo, "mia@kanzlei.ch", "Jonas Meier", "member")


def hit(doc_id, score, text):
    return {"doc_id": doc_id, "path": doc_id, "title": doc_id.rsplit("/", 1)[-1], "score": score,
            "cosine_similarity": score, "top_chunks": [{"text": text}]}


conftest.FakeGraphApi.current.search_results = [
    hit("mandate/2026-0142/Mietvertrag_Seefeldstrasse_48.pdf", 0.91,
        "Die feste Mindestdauer des Mietverhaeltnisses betraegt fuenf Jahre. Mietvertrag Seefeldstrasse."),
    hit("mandate/2026-0142/Nachtrag_2_2023-03-14.pdf", 0.84,
        "Die feste Mindestdauer wird bis zum 30. September 2027 verlaengert. Mietvertrag, Nachtrag 2."),
    hit("mandate/2026-0142/Memo_Kuendigungsmoeglichkeiten.docx", 0.62,
        "Ordentliche Frist von sechs Monaten, jeweils auf Ende Maerz oder Ende September. Mietvertrag."),
]

import threading  # noqa: E402

inner = app.wsgi_app


def with_quit(environ, start_response):
    """GET /__quit stops the server, so the schema below is dropped (a killed process leaves it behind)."""
    if environ.get("PATH_INFO") == "/__quit":
        threading.Thread(target=server.shutdown, daemon=True).start()
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"bye"]
    return inner(environ, start_response)


app.wsgi_app = with_quit
app.jinja_env.auto_reload = True      # a changed template shows without a restart
server = make_server("127.0.0.1", args.port, app, threaded=True)
print(f"READY http://127.0.0.1:{args.port} schema={schema}", flush=True)
try:
    server.serve_forever()
finally:
    conn.close()
    with psycopg.connect(dsn, autocommit=True) as cleanup:
        cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')
