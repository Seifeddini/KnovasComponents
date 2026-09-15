#!/usr/bin/env bash
# Ask Knovas one query and say why a disappointing answer is disappointing.
#
# Three causes, three different responses, and they are not guessable from the
# result list:
#
#   not extracted — RemoteController never read the file. Nothing downstream
#                   can help; fix the sync's folders or filters.
#   not indexed   — RemoteController read it and Knovas does not have it. The
#                   upload failed, silently, per document. Re-sync.
#   ranking       — Knovas has the words and still did not return the document.
#                   A retrieval problem, worth escalating with this output.
#
# How the last two are told apart without access to Weaviate: a ONE-WORD query
# is routed to pure BM25 (resolve_effective_alpha returns 0.0 below
# BM25_PURE_MIN_SEARCH_TERMS), so a single distinctive term is effectively a
# keyword probe of the index. If that returns nothing while the local context
# sidecars hold the word, the text never reached Knovas.
#
# The sidecars are what RemoteController extracted, NOT what Knovas stored:
# write_context_sidecar runs before init_document_transmission, so a sidecar
# exists even when every upload for it failed. Counting them as "indexed" is
# how this script once reported a ranking problem that was an upload problem.
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
[[ -n "$QUERY" ]] || { echo "Usage: $0 \"search words\"" >&2; exit 2; }

KNOVAS_QUERY="$QUERY" "${DC[@]}" exec -T -e KNOVAS_QUERY docbridge-web python - <<'PY' 2>&1 | sed 's/^/  /'
import json, os, pathlib, ssl, urllib.error, urllib.request

query = os.environ.get("KNOVAS_QUERY", "")
terms = [t for t in query.lower().split() if t]
base = (os.environ.get("SEMANTIX_API_URL") or "").rstrip("/")
ctx = ssl.create_default_context(cafile=os.environ.get("SEMANTIX_CA_CERT") or None)
cert, key = os.environ.get("SEMANTIX_CLIENT_CERT"), os.environ.get("SEMANTIX_CLIENT_KEY")
if cert and key:
    ctx.load_cert_chain(cert, key)


def ask(text, limit=10):
    """One /secured/query. Returns (pointers, error)."""
    body = json.dumps({"Input": text, "limit": limit, "top_k": limit}).encode()
    request = urllib.request.Request(
        base + "/secured/query", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, context=ctx, timeout=60) as response:
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return [], f"HTTP {exc.code}: {(exc.read() or b'')[:200]!r}"
    except Exception as exc:
        return [], str(exc)
    rows = payload.get("results")
    if rows is None and isinstance(payload.get("data"), dict):
        rows = payload["data"].get("results")
    return [str(r.get("pointer") or r.get("identifier") or "") for r in (rows or [])], None


print(f"\nWhat Knovas returns for {query!r}")
pointers, err = ask(query)
if err:
    raise SystemExit(f"   FAIL  /secured/query — {err}")
print(f"  {len(pointers)} hit(s)")
for pointer in pointers[:5]:
    print(f"    {pointer}")

# What RemoteController extracted locally. Says nothing about what Knovas holds.
store = pathlib.Path(os.environ.get("SEARCH_CONTEXT_STORE_PATH")
                     or "/var/rc-state/search_context")
local_total = 0
local_with_terms = 0
per_term = {t: 0 for t in terms}
if store.is_dir():
    for path in store.iterdir():
        if path.suffix != ".json":
            continue
        local_total += 1
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        text = " ".join(
            str(s.get("t") or s.get("text") or "") if isinstance(s, dict) else str(s)
            for s in (entry.get("sentences") or [])
        ).lower()
        for term in terms:
            if term in text:
                per_term[term] += 1
        if terms and all(term in text for term in terms):
            local_with_terms += 1

print(f"\nWhat RemoteController extracted locally")
print(f"  {local_total} document(s) extracted   ({local_with_terms} contain every word)")
print("  NOTE: extracted, not indexed — the sidecar is written before the upload,")
print("        so it survives an upload that failed.")

# A one-word query is pure BM25, so it is a keyword probe of the index itself.
print(f"\nIs the word in the Knovas index at all? (one-word query = pure BM25)")
indexed = {}
for term in terms:
    hits, term_err = ask(term, limit=5)
    indexed[term] = None if term_err else len(hits)
    shown = term_err if term_err else f"{len(hits)} hit(s)"
    print(f"  {term:24s} {shown:16s} locally extracted: {per_term[term]}")

print()
missing = [t for t in terms if indexed.get(t) == 0 and per_term[t] > 0]
never = [t for t in terms if per_term[t] == 0]
if never and not local_total:
    print("  FAIL  NOT EXTRACTED: no context store — RemoteController has read nothing.")
elif never:
    print(f"  FAIL  NOT EXTRACTED: {', '.join(never)} appears in nothing RemoteController")
    print("        read. Check the sync's folders and filters; nothing downstream helps.")
elif missing:
    print(f"  FAIL  NOT INDEXED: {', '.join(missing)} — RemoteController extracted files")
    print("        containing it, and a pure-BM25 query finds none of them. The text")
    print("        never reached Knovas: uploads failed, per document and silently.")
    print("        Look for them:")
    print("          docker compose --env-file knovas.env logs remote-controller \\")
    print("            | grep -iE 'init failed|transmit|error'")
    print("        Then re-run the sync. This is NOT a search-tuning problem.")
elif local_with_terms and not any(
    all(t in p.lower() for t in terms) for p in pointers
):
    print("  WARN  RANKING: the words are in the index and the documents holding them")
    print("        did not come back. Bisect on the Knovas side, reversible config:")
    print("          1. QUERY_COLBERT_STAGE2_ENABLED=false — if the query then works,")
    print("             Stage-2 rerank is discarding Stage-1's keyword evidence")
    print("             (BLEND_STAGE1_WEIGHT=0.0 makes the final order purely Stage-2).")
    print("          2. If it does not improve, Stage 1 is the suspect, not the reranker.")
    print("        And check knovas_stage2_reranker_backend_total{outcome=} in Prometheus.")
else:
    print("  OK    the words are indexed and the query returns documents holding them.")
print()
PY
