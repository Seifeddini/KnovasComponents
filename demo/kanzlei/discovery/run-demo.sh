#!/usr/bin/env bash
# Knovas Discovery against the demo law firm's site (synthetic; FEAT-2026-11-19-demo-law-firm).
#
#   bash demo/kanzlei/discovery/run-demo.sh [--keep-running]
#
# 1. builds the image from KnovasDiscovery/, 2. starts the site if it is not running (site/up.sh), 3. mounts the
# share read-only as a Docker volume of type cifs (as site/smoke.sh check 8 does), 4. scans from a container on
# demo-kanzlei-net, 5. writes the report, 6. compares the findings with the generator's truth into
# out/verification.txt, and leaves the site as it found it (a site it started is stopped again, volumes kept).
# Re-runnable: out/ is overwritten and the cifs volume is removed every time.
#
# Needs Docker and Git Bash; no Python on the host (the verification runs in the image). The two passwords are read
# from site/.env into this script's environment and handed to the container by name (-e NAME): never printed,
# never written. The cifs volume carries the share password in its options, as in smoke.sh, and is removed on exit.
set -euo pipefail
export MSYS_NO_PATHCONV=1                     # Git Bash: do not rewrite container paths like /work
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
SITE="$REPO/demo/kanzlei/site"
IMAGE=knovas/discovery:dev
VOL=demo-kanzlei-discovery-cifs
NET=demo-kanzlei-net
keep=0; [ "${1:-}" = --keep-running ] && keep=1
host_path() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else echo "$1"; fi; }

[ -f "$SITE/.env" ] || { echo "No $SITE/.env: set the site up first (demo/kanzlei/README.md)" >&2; exit 1; }
# Sourced without `set -a`: nothing from .env is exported except the two variables below.
# shellcheck disable=SC1091
. "$SITE/.env"
export DISCOVERY_LDAP_PASSWORD="$PW_SVC_KNOVAS" DISCOVERY_SQL_PASSWORD="$PW_SQL_KNOVAS_RO"
WORK=$(dirname "$DEMO_SITE_DIR")              # the generator's work folder: truth/, site/, manifest.jsonl

running=$(docker ps -q --filter label=com.docker.compose.project=demo-kanzlei --filter label=com.docker.compose.service=dc)
cleanup() {
  docker volume rm -f "$VOL" >/dev/null 2>&1 || true
  if [ -z "$running" ] && [ "$keep" = 0 ]; then
    echo "== stopping the site again (volumes kept)"
    bash "$SITE/down.sh" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

t0=$(date +%s)
echo "== 1. image $IMAGE"
docker build -q -t "$IMAGE" "$(host_path "$REPO/KnovasDiscovery")" >/dev/null

echo "== 2. site (dc, sql healthy)"
bash "$SITE/up.sh" >/dev/null

echo "== 3. share //172.28.215.10/shares\$ as a read-only cifs volume"
docker volume rm -f "$VOL" >/dev/null 2>&1 || true
docker volume create -d local -o type=cifs -o 'device=//172.28.215.10/shares$' \
  -o "o=addr=172.28.215.10,username=svc-knovas,password=$PW_SVC_KNOVAS,domain=QUARZFELS,vers=3.0,sec=ntlmssp,ro" \
  "$VOL" >/dev/null

echo "== 4. scan"
mkdir -p "$HERE/out"
t1=$(date +%s)
docker run --rm --network "$NET" -e DISCOVERY_LDAP_PASSWORD -e DISCOVERY_SQL_PASSWORD \
  -v "$VOL:/mnt/kanzlei:ro" -v "$(host_path "$HERE"):/work" \
  "$IMAGE" scan --intake /work/intake.yaml --out /work/out
t2=$(date +%s)

echo "== 5. report"
docker run --rm -v "$(host_path "$HERE"):/work" "$IMAGE" report --out /work/out

echo "== 6. against the truth -> out/verification.txt"
docker run --rm --entrypoint python -v "$(host_path "$HERE"):/work" -v "$(host_path "$WORK"):/truth:ro" \
  "$IMAGE" /work/verify_against_truth.py --out /work/out --work /truth > "$HERE/out/verification.txt" || true
cat "$HERE/out/verification.txt"

t3=$(date +%s)
echo "== scan $((t2 - t1)) s; whole run $((t3 - t0)) s"
