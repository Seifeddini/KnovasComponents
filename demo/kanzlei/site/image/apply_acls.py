#!/usr/bin/env python3
"""Apply acl.json (read from stdin) to the share as POSIX ACLs. Runs inside the dc container (needs winbind).

POSIX ACLs are the only permission store of the share (smb.conf has no acl_xattr): the kernel enforces them when smbd
acts as the user, and Samba shows them to Windows clients as NT ACLs. Semantics:
  * Entries are applied parents first (by path depth, stable). protect=true replaces the ACL of the folder and
    everything below it with its grants plus "Domain Admins: full"; protect=false adds its grants below the folder.
  * read = r-x on folders, r-- on files; modify and full = rwx / rw- (POSIX cannot tell modify from full).
  * Every folder also gets --x for Domain Users, like Windows' "bypass traverse checking": a grant deep in the tree
    works without rights on the folders above it, and those folders still cannot be listed.
  * The whole tree is reset to "root only" first, so applying again gives the same result.
"""
import json
import os
import subprocess
import sys
import time

ROOT = '/srv/shares'
PERM = {'read': ('r-x', 'r--'), 'modify': ('rwx', 'rw-'), 'full': ('rwx', 'rw-')}   # (folder, file)
TRAVERSE = ('--x', '---')


def wb(*args):
    p = subprocess.run(['wbinfo', *args], capture_output=True, text=True)
    if p.returncode:
        raise LookupError((p.stdout + p.stderr).strip())
    return p.stdout.strip()


SEP, DOM = wb('--separator'), wb('--own-domain')
_ids = {}


def principal(name):
    """'GG_Partner' -> ('g', '3000015'); 'svc-knovas' -> ('u', '3000017'). winbind allocates new ids itself."""
    if name.lower() not in _ids:
        full = name.replace('\\', SEP) if '\\' in name else f'{DOM}{SEP}{name}'
        try:
            sid, kind = wb('--name-to-sid', full).split(' ', 1)
            _ids[name.lower()] = ('u', wb('--sid-to-uid', sid)) if 'SID_USER' in kind else ('g', wb('--sid-to-gid', sid))
        except LookupError as e:
            sys.exit(f'acl: unknown principal "{name}" ({e}); is it in directory.json?')
    return _ids[name.lower()]


def union(a, b):
    return ''.join(x if x != '-' else y for x, y in zip(a, b))


def specs(grants, base):
    """setfacl specs (folders, files) for the grants; base=True adds owner/group/other and default entries."""
    perms = {principal('Domain Users'): TRAVERSE} if base else {}
    for g in grants:
        key, (d, f) = principal(g['principal']), PERM[g['access']]
        old = perms.get(key, ('---', '---'))
        perms[key] = (union(old[0], d), union(old[1], f))
    named = [(f'{k}:{i}', p) for (k, i), p in perms.items()]
    dirs = [f'{n}:{p[0]}' for n, p in named]
    files = [f'{n}:{p[1]}' for n, p in named]
    if base:
        dirs = ['u::rwx', 'g::---', 'o::---'] + dirs
        files = ['u::rw-', 'g::---', 'o::---'] + files
    # Default (inherited) entries: the same, except the traverse-only entry, which new files must not get.
    dirs += ['d:' + e for e in dirs if not e.endswith(':--x')]
    return ','.join(dirs), ','.join(files)


def setfacl(path, op, dir_spec, file_spec):
    for kind, spec in (('d', dir_spec), ('f', file_spec)):
        if spec:
            subprocess.run(['find', path, '-type', kind, '-exec', 'setfacl', op, spec, '{}', '+'], check=True)


def depth(entry):
    return len([c for c in entry['path'].split('/') if c])


entries = json.load(sys.stdin)
t0 = time.time()
subprocess.run(['chown', '-R', 'root:root', ROOT], check=True)
setfacl(ROOT, '--set', *specs([], base=True))                      # reset: root only + traverse
for e in sorted(entries, key=depth):
    path = os.path.normpath(os.path.join(ROOT, e['path'].strip('/')))
    if path != ROOT and not path.startswith(ROOT + '/'):
        sys.exit(f'acl: path leaves the share: {e["path"]!r}')
    if not os.path.isdir(path):
        print(f'acl: note: folder {e["path"]!r} did not exist; created it empty')
        os.makedirs(path)
        setfacl(path, '--set', *specs([], base=True))
    grants = list(e['grants'])
    if e.get('protect'):
        grants.append({'principal': 'Domain Admins', 'access': 'full'})
        setfacl(path, '--set', *specs(grants, base=True))
    elif grants:
        setfacl(path, '-m', *specs(grants, base=False))
n = sum(len(files) + len(dirs) for _, dirs, files in os.walk(ROOT))
print(f'acl: {len(entries)} entries applied to {n} files and folders in {time.time() - t0:.1f} s')
