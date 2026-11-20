#!/bin/bash
# loader container (one-shot): apply one set of changes from the generator (/in/changes) to the running site.
# db.sql is what the practice software would have written; share/ holds files that are new or saved over;
# delete.txt lists files that go. apply-changes.sh applies acl.json afterwards. Undo: load.sh loads the build again.
set -euo pipefail
SHARE=/srv/shares CH=/in/changes SQLHOST=172.28.215.20
export SQLCMDPASSWORD=$PW_SA
[ -f "$CH/db.sql" ] || { echo "changes: no db.sql in the folder" >&2; exit 1; }
adds=0; [ -d "$CH/share" ] && adds=$(find "$CH/share" -type f | wc -l)
drops=0; [ -f "$CH/delete.txt" ] && drops=$(grep -c . "$CH/delete.txt" || true)
echo "changes: will run $(grep -c ';$' "$CH/db.sql") statements, add or save over $adds files, delete $drops files"

sqlcmd -S "$SQLHOST" -U sa -C -b -f 65001 -i "$CH/db.sql" > /tmp/sql.log 2>&1 || { tail -20 /tmp/sql.log; echo "changes: db.sql failed" >&2; exit 1; }
if [ "$adds" -gt 0 ]; then rsync -rlt --chmod=D750,F640 "$CH/share"/ "$SHARE"/; fi
if [ -f "$CH/delete.txt" ]; then
  while IFS= read -r rel; do
    rel=${rel%$'\r'}
    [ -n "$rel" ] || continue
    case "$rel" in /*|*..*) echo "changes: refused path $rel" >&2; exit 1;; esac
    [ -f "$SHARE/$rel" ] || { echo "changes: not on the share (already deleted?): $rel" >&2; exit 1; }
    rm -f -- "$SHARE/$rel"
  done < "$CH/delete.txt"
fi
echo "changes: database and share changed"
