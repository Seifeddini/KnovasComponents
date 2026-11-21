#!/bin/bash
# loader container (one-shot). "reset": empty the share volume and drop the database.
# "load": reset, then copy the share tree (mtimes kept) and run db/*.sql in name order, then create the SQL logins.
# load.sh applies acl.json afterwards inside dc, where winbind maps names to ids.
set -euo pipefail
MODE=${1:-load}
SHARE=/srv/shares SRC=/in/share SITE=/in/site SQLHOST=172.28.215.20
DB=$(python3 -c 'import json; print(json.load(open("/in/site/directory.json", encoding="utf-8"))["database"])')
[[ $DB =~ ^[A-Za-z0-9_]+$ ]] || { echo "loader: database name must be letters, digits, _: $DB" >&2; exit 1; }
export SQLCMDPASSWORD=$PW_SA DB        # sqlcmd takes the sa password and $(DB) etc. from the environment
sql() { sqlcmd -S "$SQLHOST" -U sa -C -b -f 65001 "$@"; }
db_exists() { [ "$(sql -h -1 -W -Q "SET NOCOUNT ON; SELECT COUNT(*) FROM sys.databases WHERE name = N'$DB'")" = 1 ]; }

for _ in $(seq 90); do sql -Q 'SELECT 1' -o /dev/null 2>/dev/null && break; sleep 2; done
sql -Q 'SELECT 1' -o /dev/null

# 1. Reset: say what goes, then remove it.
echo "loader: will remove from the share volume: $(find "$SHARE" -mindepth 1 -type f | wc -l) files," \
     "$(find "$SHARE" -mindepth 1 -type d | wc -l) folders; top level: $(ls -A "$SHARE" | tr '\n' ' ')"
db_exists && echo "loader: will drop database [$DB]" || echo "loader: database [$DB] does not exist"
find "$SHARE" -mindepth 1 -delete
sql -Q "IF DB_ID(N'$DB') IS NOT NULL BEGIN ALTER DATABASE [$DB] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [$DB]; END"
[ "$MODE" = reset ] && { echo "loader: reset done"; exit 0; }

# 2. Share tree: -t keeps mtimes; folders 750 / files 640 until apply_acls sets the real ACLs.
t0=$SECONDS
rsync -rltp --chmod=D750,F640 "$SRC"/ "$SHARE"/
echo "loader: copied $(find "$SHARE" -type f | wc -l) files in $((SECONDS - t0)) s"

# 3. Database: every *.sql in name order; once the database exists, files run inside it (USE is optional).
t0=$SECONDS
while IFS= read -r -d '' f; do
  args=(); db_exists && args=(-d "$DB")
  echo "loader: running db/$(basename "$f")"
  sql "${args[@]}" -i "$f" > /tmp/sql.log 2>&1 || { tail -20 /tmp/sql.log; echo "loader: $f failed" >&2; exit 1; }
done < <(find "$SITE/db" -maxdepth 1 -name '*.sql' -print0 | LC_ALL=C sort -z)
db_exists || { echo "loader: no db/*.sql created database [$DB]" >&2; exit 1; }

# 4. Logins: knovas_ro reads, praxis_app owns. Passwords come from the environment as sqlcmd variables.
cat > /tmp/logins.sql <<'SQL'
IF SUSER_ID(N'knovas_ro') IS NULL CREATE LOGIN [knovas_ro] WITH PASSWORD = N'$(PW_SQL_KNOVAS_RO)'
ELSE ALTER LOGIN [knovas_ro] WITH PASSWORD = N'$(PW_SQL_KNOVAS_RO)';
IF SUSER_ID(N'praxis_app') IS NULL CREATE LOGIN [praxis_app] WITH PASSWORD = N'$(PW_SQL_PRAXIS_APP)'
ELSE ALTER LOGIN [praxis_app] WITH PASSWORD = N'$(PW_SQL_PRAXIS_APP)';
GO
USE [$(DB)];
IF USER_ID(N'knovas_ro') IS NULL CREATE USER [knovas_ro] FOR LOGIN [knovas_ro];
ALTER ROLE db_datareader ADD MEMBER [knovas_ro];
IF USER_ID(N'praxis_app') IS NULL CREATE USER [praxis_app] FOR LOGIN [praxis_app];
ALTER ROLE db_owner ADD MEMBER [praxis_app];
GO
SQL
sql -i /tmp/logins.sql
sql -d "$DB" -W -s ' ' -Q "SET NOCOUNT ON; SELECT s.name + '.' + t.name AS [table], SUM(p.rows) AS [rows]
  FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
  JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
  GROUP BY s.name, t.name ORDER BY 1"
echo "loader: database [$DB] loaded in $((SECONDS - t0)) s"
