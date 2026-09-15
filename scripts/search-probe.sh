#!/usr/bin/env bash
# Ask Knovas one query, and say whether a disappointing answer is coverage or ranking.
#
# "The results are nonsense" has two completely different causes and they need
# opposite responses:
#
#   coverage — the documents that contain the words were never ingested, so no
#              amount of tuning will surface them. Wait for the sync, or re-run it.
#   ranking  — they ARE indexed and still did not come back, which is a
#              retrieval problem worth escalating with evidence.
#
# Telling them apart by hand took a round trip each time. This asks the API with
# the same certificates the app uses, then checks the local context sidecars —
# which hold the text of everything actually indexed — for the same words.
#
# Read-only. Usage: ./scripts/search-probe.sh "Sophie Keller"
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
KNOVAS_ENV="$ROOT_DIR/knovas.env"
[[ -f "$KNOVAS_ENV" ]] || { echo "Missing knovas.env — run ./scripts/setup.sh" >&2; exit 1; }
# shellcheck source=lib/stack_identity.sh
source "$ROOT_DIR/scripts/lib/stack_identity.sh"
knovas_load_compose_project "$KNOVAS_ENV" "$ROOT_DIR"
DC=(docker compose --env-file "$KNOVAS_ENV")

QUERY="${*:-}"
if [[ -z "$QUERY" ]]; then
  echo "Usage: $0 \"search words\"" >&2
  exit 2
fi

printf '\n\033[1mWhat Knovas returns for %s\033[0m\n' "\"$QUERY\""
KNOVAS_QUERY="$QUERY" "${DC[@]}" exec -T -e KNOVAS_QUERY docbridge-web python - <<'PY' 2>&1 | sed 's/^/  /'
import json, os, ssl, urllib.error, urllib.request

query = os.environ.get("KNOVAS_QUERY", "")
base = (os.environ.get("SEMANTIX_API_URL") or "").rstrip("/")
ctx = ssl.create_default_context(cafile=os.environ.get("SEMANTIX_CA_CERT") or None)
cert, key = os.environ.get("SEMANTIX_CLIENT_CERT"), os.environ.get("SEMANTIX_CLIENT_KEY")
if cert and key:
    ctx.load_cert_chain(cert, key)

body = json.dumps({"Input": query, "limit": 10, "top_k": 10}).encode()
request = urllib.request.Request(
    base + "/secured/query", data=body,
    headers={"Content-Type": "application/json"}, method="POST",
)
try:
    with urllib.request.urlopen(request, context=ctx, timeout=60) as response:
        payload = json.loads(response.read() or b"{}")
except urllib.error.HTTPError as exc:
    raise SystemExit(f"FAIL /secured/query -> {exc.code}: {(exc.read() or b'')[:300]!r}")
except Exception as exc:
    raise SystemExit(f"FAIL /secured/query unreachable: {exc}")

rows = payload.get("results")
if rows is None and isinstance(payload.get("data"), dict):
    rows = payload["data"].get("results")
rows = rows or []
print(f"{len(rows)} hit(s)")
terms = [t for t in query.lower().split() if t]
for row in rows[:10]:
    pointer = str(row.get("pointer") or row.get("identifier") or "?")
    score = row.get("final_score")
    if score is None:
        score = row.get("cosine_similarity")
    mark = "  "
    print(f"{mark} {score if score is not None else '—'}  {pointer}")
# Stash the pointers so the shell half can compare against them.
with open("/tmp/probe-pointers.txt", "w", encoding="utf-8") as handle:
    for row in rows:
        handle.write(str(row.get("pointer") or row.get("identifier") or "") + "\n")
PY

printf '\n\033[1mWhat is actually indexed\033[0m\n'
# The sidecars hold the text of every document the sync has put into Knovas, so
# they answer "is it even in there" without asking the API a second time.
KNOVAS_QUERY="$QUERY" "${DC[@]}" exec -T -e KNOVAS_QUERY docbridge-web python - <<'PY' 2>&1 | sed 's/^/  /'
import json, os, pathlib

query = os.environ.get("KNOVAS_QUERY", "")
terms = [t for t in query.lower().split() if t]
store = pathlib.Path(os.environ.get("SEARCH_CONTEXT_STORE_PATH")
                     or "/var/rc-state/search_context")
if not store.is_dir():
    raise SystemExit(f"   no context store at {store} — cannot tell coverage from ranking")

try:
    returned = set(
        line.strip() for line in open("/tmp/probe-pointers.txt", encoding="utf-8")
        if line.strip()
    )
except OSError:
    returned = set()

total = matched = 0
examples = []
for path in store.iterdir():
    if path.suffix != ".json":
        continue
    total += 1
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    text = " ".join(
        str(s.get("t") or s.get("text") or "") if isinstance(s, dict) else str(s)
        for s in (entry.get("sentences") or [])
    ).lower()
    if terms and all(term in text for term in terms):
        matched += 1
        if len(examples) < 5:
            examples.append(entry.get("pointer") or path.name)

print(f"{total} document(s) indexed")
print(f"{matched} of them contain every word of the query")
if matched == 0:
    print("   FAIL  COVERAGE: nothing indexed contains these words. The documents")
    print("         exist on disk but the sync has not reached them yet, so no")
    print("         amount of search tuning will surface them. Let the ingest")
    print("         finish, or re-run it, and try again.")
else:
    for example in examples:
        print(f"   e.g. {example}")
    if returned:
        print("   FAIL  RANKING: those documents are indexed and did not come back.")
        print("         That is a retrieval problem, not coverage — worth reporting")
        print("         with this output.")
    else:
        print("   WARN  indexed, and the query returned nothing at all.")
PY
printf '\n'
