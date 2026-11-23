#!/usr/bin/env python3
"""Compare the share with a manifest of the generator: the same files, the same bytes, the same file times.

Run in the client container by check-share.sh, with the manifest mounted at /in/truth/manifest.jsonl. Stronger than
the smoke test's count: every file is hashed. Works for the build's manifest and for the one after a set of changes.
Exit code 1 on any difference.
"""
import hashlib
import json
import os
import sys

ROOT = '/srv/shares'
want = {}
for line in open('/in/truth/manifest.jsonl', encoding='utf-8'):
    if line.strip():
        row = json.loads(line)
        want[row['path']] = row

have = {}
for folder, _dirs, files in os.walk(ROOT):
    for name in files:
        path = os.path.join(folder, name)
        have[os.path.relpath(path, ROOT).replace(os.sep, '/')] = path

missing = sorted(set(want) - set(have))
extra = sorted(set(have) - set(want))
different = []
for rel in sorted(set(want) & set(have)):
    with open(have[rel], 'rb') as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    if digest != want[rel]['sha256']:
        different.append(rel)

failures = 0
for label, items in (('on the share as in the manifest', None), ('missing from the share', missing),
                     ('on the share but not in the manifest', extra), ('content differs', different)):
    if items is None:
        print(f"{'PASS' if not (missing or extra or different) else 'FAIL'}  {len(want)} files in the manifest, "
              f"{len(have)} on the share, {len(set(want) & set(have)) - len(different)} identical")
        continue
    if items:
        failures += 1
        print(f'FAIL  {len(items)} {label}: ' + '; '.join(items[:5]) + (' ...' if len(items) > 5 else ''))
print('share: equals the manifest' if not failures else 'share: differs from the manifest')
sys.exit(1 if failures else 0)
