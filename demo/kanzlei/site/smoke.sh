#!/usr/bin/env bash
# Smoke test of the running, loaded site. Checks 1-7 run in the client container (image/smoke.py); check 8 mounts the
# share with the kernel CIFS client through a Docker volume, as a Connector host would. Exits non-zero on any FAIL.
. "$(dirname "$0")/_lib.sh"
load_env
rc=0 out=$(mktemp) vol=demo-kanzlei-cifs-smoke
compose up -d --wait --wait-timeout 600 dc sql >/dev/null 2>&1 || { echo "FAIL  dc/sql not healthy"; exit 1; }
compose --profile tools run --rm -T client python3 /opt/site/smoke.py | tee "$out" | grep -v '^CIFS-TARGET' || rc=1
IFS=$'\t' read -r _ share netbios sub < <(grep '^CIFS-TARGET' "$out") || true
rm -f "$out"

# 8. local-driver volume of type cifs (the password sits in the volume options, so the volume is removed at once).
docker volume rm -f "$vol" >/dev/null 2>&1 || true
docker volume create -d local -o type=cifs -o "device=//172.28.215.10/$share/$sub" \
  -o "o=addr=172.28.215.10,username=svc-knovas,password=$PW_SVC_KNOVAS,domain=$netbios,vers=3.0,sec=ntlmssp,ro" \
  "$vol" >/dev/null
if list=$(docker run --rm -v "$vol:/mnt:ro" demo-kanzlei/site:1 ls -A /mnt 2>&1) && [ -n "$list" ]; then
  echo "PASS  8 kernel CIFS mount (Docker volume, vers=3.0, ntlmssp, ro) of //172.28.215.10/$share/$sub" \
       " [$(echo "$list" | wc -l) entries: $(echo "$list" | head -3 | tr '\n' ' ')]"
else
  echo "FAIL  8 kernel CIFS mount via Docker volume  [$list]"; rc=1
fi
docker volume rm -f "$vol" >/dev/null
[ $rc = 0 ] && echo "smoke: all checks passed" || echo "smoke: FAILED"
exit $rc
