#!/usr/bin/env python3
"""Read every table as the Knovas login does and compare it, row for row, with the truth files.

Run in the client container by check-records.sh, with the build's truth folder mounted at /in/truth. The truth holds
one JSON line per row (truth/records/<TABLE>.jsonl). A table passes when the database returns exactly those rows.
This is the oracle for a connector: whatever reads this database must end up with the same records.

Output names tables, keys and columns, never values. Exit code 1 if any table differs.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SQLHOST = '172.28.215.20'
DB = json.load(open('/in/site/directory.json', encoding='utf-8'))['database']
TRUTH = Path('/in/truth/records')
ENV = dict(os.environ, SQLCMDPASSWORD=os.environ['PW_SQL_KNOVAS_RO'])


def query(sql, as_json=True):
    """Run a query as knovas_ro. With ``as_json`` the answer is the query's FOR JSON text, put together again.

    sqlcmd cuts FOR JSON output into pieces and prints a header first; the JSON starts at the first bracket.
    """
    shape = ['-y', '0'] if as_json else ['-h', '-1', '-W']
    done = subprocess.run(
        ['sqlcmd', '-S', SQLHOST, '-U', 'knovas_ro', '-C', '-d', DB, '-b', '-f', '65001', *shape,
         '-Q', 'SET NOCOUNT ON; ' + sql],
        env=ENV, capture_output=True, text=True, encoding='utf-8')
    if done.returncode != 0:
        raise RuntimeError('query refused: ' + (done.stdout + done.stderr).strip().splitlines()[-1][:200])
    if not as_json:
        return done.stdout.strip()
    text = ''.join(line.rstrip(chr(13) + chr(10)) for line in done.stdout.splitlines(keepends=True))
    start = text.find('[')
    return text[start:] if start >= 0 else ''


def old_text_columns():
    """Columns of the old types text/ntext: FOR JSON cannot return them without a cast."""
    raw = query("SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE DATA_TYPE IN ('text', 'ntext') "
                "FOR JSON PATH")
    return {(row['TABLE_NAME'], row['COLUMN_NAME']) for row in (json.loads(raw) if raw.strip() else [])}


def same(expected, found):
    if expected is None or found is None:
        return expected is None and found is None
    if isinstance(expected, bool):
        return found is expected or (not isinstance(found, bool) and bool(found) == expected and found in (0, 1))
    if isinstance(expected, (int, float)) and isinstance(found, (int, float)):
        return abs(float(expected) - float(found)) < 0.005
    return expected == found


def check(table, old_text):
    truth = [json.loads(line) for line in (TRUTH / f'{table}.jsonl').read_text(encoding='utf-8').splitlines() if line]
    columns = list(truth[0]) if truth else None
    if columns is None:
        count = int(query(f'SELECT COUNT(*) FROM dbo.[{table}]', as_json=False))
        return count == 0, f'{count} rows, 0 expected'
    select = ', '.join(f'CAST([{c}] AS nvarchar(max)) AS [{c}]' if (table, c) in old_text else f'[{c}]' for c in columns)
    raw = query(f'SELECT {select} FROM dbo.[{table}] FOR JSON PATH, INCLUDE_NULL_VALUES')
    found = json.loads(raw) if raw.strip() else []
    if len(found) != len(truth):
        return False, f'{len(found)} rows, {len(truth)} expected'

    def order(row):
        return json.dumps([str(row.get(c)) for c in columns], ensure_ascii=False)

    for index, (want, have) in enumerate(zip(sorted(truth, key=order), sorted(found, key=order))):
        for column in columns:
            if not same(want[column], have.get(column)):
                # Sorting by all columns as text can pair rows differently when a number is printed differently
                # (420 and 420.0000); fall back to matching on the first column, which is the key or part of it.
                break
        else:
            continue
        break
    else:
        return True, f'{len(truth)} rows'
    key = columns[0]
    by_key = {}
    for row in found:
        by_key.setdefault(str(row.get(key)), []).append(row)
    for want in truth:
        candidates = by_key.get(str(want[key]), [])
        if not any(all(same(want[c], have.get(c)) for c in columns) for have in candidates):
            wrong = [c for c in columns if candidates and not same(want[c], candidates[0].get(c))]
            return False, f'row {key}={want[key]} differs in {wrong or "everything (no such row)"}'
    return True, f'{len(truth)} rows'


def main():
    if not TRUTH.is_dir():
        print(f'FAIL  no truth folder mounted at {TRUTH}')
        return 1
    old_text = old_text_columns()
    failures = 0
    in_db = {row['name'] for row in json.loads(query("SELECT name FROM sys.tables FOR JSON PATH"))}
    expected = sorted(path.stem for path in TRUTH.glob('*.jsonl'))
    for table in expected:
        if table not in in_db:
            ok, detail = False, 'table is not in the database'
        else:
            try:
                ok, detail = check(table, old_text)
            except Exception as exc:  # a refused query or unreadable answer is a failed check, not a crash
                ok, detail = False, f'{type(exc).__name__}: {str(exc)[:160]}'
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {table}  [{detail}]", flush=True)
    extra = sorted(in_db - set(expected))
    if extra:
        failures += 1
        print(f'FAIL  tables in the database without truth: {extra}')
    print('records: database equals truth' if not failures else f'records: {failures} table(s) differ')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
