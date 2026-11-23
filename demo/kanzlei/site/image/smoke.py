#!/usr/bin/env python3
"""Smoke checks 1-7 of the demo-kanzlei site, run in the client container (smoke.sh runs check 8 on the host).

Test cases come from /in/site/directory.json and acl.json, so the same checks run against the fixture and against
generated data. One PASS/FAIL line per check; exit code 1 if any check failed. The last line (CIFS-TARGET) tells
smoke.sh what to mount for check 8.
"""
import base64
import datetime
import json
import os
import random
import re
import subprocess
import tempfile

DC, SQLHOST, SRC, VOL = '172.28.215.10', '172.28.215.20', '/in/share', '/srv/shares'
D = json.load(open('/in/site/directory.json', encoding='utf-8'))
ACL = json.load(open('/in/site/acl.json', encoding='utf-8'))
DOM, SHARE, DB, DNS = D['netbios'], D['share']['name'], D['database'], D['dns_domain'].lower()
BASE = ','.join(f'DC={p}' for p in DNS.split('.'))
USERS = {u['sam'].lower(): u for u in D['users']}
PEOPLE = [u['sam'] for u in D['users'] if u.get('enabled', True) and not u.get('service')]
RANK = {'read': 1, 'modify': 2, 'full': 3}
failures = 0


def check(label, ok, detail=''):
    global failures
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f'  [{detail}]' if detail else ''), flush=True)


def norm(path):
    return '/'.join(c for c in path.split('/') if c)


def show(path):
    return f'"{path}"' if path else 'share root'


# --- membership and effective access, computed from the input files -----------------------------------------------
def parents(name):
    """Groups that list `name` directly (as member or member group)."""
    n = name.lower()
    return [g['name'] for g in D['groups']
            if n in [m.lower() for m in g.get('members', []) + g.get('member_groups', [])]]


def chain(sam):
    """{group (lower): 'GG_A > GG_B'} for every group sam is in, directly or nested; Domain Users for every user."""
    seen, todo = {}, [(g, g) for g in parents(sam)]
    while todo:
        g, path = todo.pop(0)
        if g.lower() not in seen:
            seen[g.lower()] = path
            todo += [(p, f'{path} > {p}') for p in parents(g)]
    seen['domain users'] = 'Domain Users'
    return seen


ENTRIES = sorted(ACL, key=lambda e: len(norm(e['path']).split('/')) if norm(e['path']) else 0)


def acl_at(path, skip=None):
    grants = []
    for e in ENTRIES:
        ep = norm(e['path'])
        if e is not skip and (ep == '' or path == ep or path.startswith(ep + '/')):
            grants = (e['grants'] + [{'principal': 'Domain Admins', 'access': 'full'}]) if e.get('protect') \
                else grants + e['grants']
    return grants


def access(sam, path, skip=None):
    who = {sam.lower(), *chain(sam)}
    return max((RANK[g['access']] for g in acl_at(path, skip) if g['principal'].lower() in who), default=0)


def via(sam, path):
    """How sam gets its access to path: the granted principal and the nesting chain."""
    groups = chain(sam)
    best = max((g for g in acl_at(path) if g['principal'].lower() in {sam.lower(), *groups}),
               key=lambda g: RANK[g['access']], default=None)
    if not best:
        return 'no grant'
    p = best['principal']
    return 'own grant' if p.lower() == sam.lower() else f'{groups[p.lower()]} ({best["access"]})'


# --- clients ----------------------------------------------------------------------------------------------------------
def password(sam):
    if sam == 'Administrator':
        return os.environ['PW_DOMAIN_ADMIN']
    if USERS[sam.lower()].get('service'):
        return os.environ['PW_' + re.sub(r'[^A-Z0-9]', '_', sam.upper())]
    return os.environ['PW_PEOPLE']


def secret_file(text):
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, 'w') as f:
        f.write(text)
    return path


def smb(sam, cmd=None, folder='', list_shares=False):
    """Run smbclient as sam over SMB3 only; returns (first NT_STATUS or 'OK', output)."""
    auth = secret_file(f'username = {sam}\npassword = {password(sam)}\ndomain = {DOM}\n')
    args = ['smbclient', '-A', auth, '-m', 'SMB3', '--option=client min protocol=SMB3']
    args += ['-g', '-L', f'//{DC}'] if list_shares else [f'//{DC}/{SHARE}'] + (['-D', folder] if folder else []) + ['-c', cmd]
    p = subprocess.run(args, capture_output=True, text=True)
    os.unlink(auth)
    out = p.stdout + p.stderr
    found = re.findall(r'NT_STATUS_[A-Z_]+', out)
    return (found[0] if found else ('OK' if p.returncode == 0 else f'exit {p.returncode}')), out


def can_write(sam, folder):
    name = f'smoke-{random.randrange(10**9)}.txt'
    return smb(sam, f'put /etc/hostname {name}; del {name}', folder)[0]


def first_file(folder):
    path = os.path.join(VOL, folder)
    names = sorted(n for n in os.listdir(path) if os.path.isfile(os.path.join(path, n)) and not set(n) & set('";'))
    return names[0] if names else None


def ldap(flt, attrs, base=BASE):
    pw = secret_file(password('svc-knovas'))
    p = subprocess.run(['ldapsearch', '-LLL', '-o', 'ldif-wrap=no', '-x', '-H', f'ldaps://{DC}',
                        '-D', f'svc-knovas@{DNS}', '-y', pw, '-b', base, flt, *attrs],
                       capture_output=True, text=True, env=dict(os.environ, LDAPTLS_REQCERT='never'))
    os.unlink(pw)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or f'ldapsearch exit {p.returncode}')
    entries = []
    for block in p.stdout.strip().split('\n\n'):
        e = {}
        for line in block.splitlines():
            key, _, value = line.partition(':')
            value = base64.b64decode(value[1:].strip()).decode('utf-8') if value.startswith(':') else value.strip()
            e.setdefault(key.lower(), []).append(value)
        if 'dn' in e:                                             # skip search referrals (ref: lines)
            entries.append(e)
    return entries


def sql(user, query):
    p = subprocess.run(['sqlcmd', '-S', SQLHOST, '-U', user, '-C', '-b', '-f', '65001', '-d', DB, '-h', '-1', '-W',
                        '-Q', 'SET NOCOUNT ON; ' + query], capture_output=True, text=True,
                       env=dict(os.environ, SQLCMDPASSWORD=os.environ['PW_SQL_KNOVAS_RO']))
    return p.returncode, (p.stdout + p.stderr).strip()


def run(label, fn):
    try:
        fn()
    except Exception as e:                                        # a crash in one check must not hide the others
        check(label, False, f'{type(e).__name__}: {e}')


SVC = 'svc-knovas'
FOLDERS = list(dict.fromkeys([''] + [norm(e['path']) for e in ENTRIES]))


def check1():
    st, _ = smb(SVC, 'ls')
    check(f'1a {SVC} lists //{DC}/{SHARE} by IP, SMB3 only', st == 'OK', st)
    st, out = smb(SVC, list_shares=True)
    names = [line.split('|')[1] for line in out.splitlines() if line.count('|') >= 2]
    check(f'1b {SHARE} is not in the share listing', bool(names) and SHARE not in names, 'listed: ' + ' '.join(names))


def check2():
    ro = [p for p in FOLDERS if access(SVC, p) == 1 and os.path.isdir(os.path.join(VOL, p))]
    kinds = {}                       # one folder each: share root, a protected entry, an added-grant entry
    for p in ro:
        e = next(e for e in ENTRIES if norm(e['path']) == p) if p else None
        kinds.setdefault('root' if not p else ('protect' if e.get('protect') else 'add'), p)
    for p in kinds.values():
        st = can_write(SVC, p)
        check(f'2a {SVC} cannot create a file in read-only {show(p)}', st == 'NT_STATUS_ACCESS_DENIED', st)
    if not ro:
        check(f'2a {SVC} has a read-only folder to test', False, 'none found in acl.json')
    none = [norm(e['path']) for e in ENTRIES if e.get('protect') and access(SVC, norm(e['path'])) == 0]
    picks = none[:1] + [p for p in none[1:] if not p.isascii() or ' ' in p][:1]
    for p in picks:
        st = smb(SVC, 'ls', p)[0]
        check(f'2b {SVC} gets access denied on protected {show(p)}', st == 'NT_STATUS_ACCESS_DENIED', st)
    if not picks:
        check(f'2b {SVC} has a protected folder without grant to test', False, 'none found in acl.json')


def check3():
    def group_grants(p):
        return [g['principal'] for g in acl_at(p) if g['principal'].lower() not in USERS and g['principal'] != 'Domain Admins']
    prot = [norm(e['path']) for e in ENTRIES if e.get('protect') and norm(e['path']) and group_grants(norm(e['path']))]
    prot.sort(key=lambda p: 0 if any('partner' in g.lower() for g in group_grants(p)) else 1)
    for p in prot:
        members = [u for u in PEOPLE if access(u, p) >= 1]
        outsiders = sorted((u for u in PEOPLE if access(u, p) == 0),
                           key=lambda u: 0 if any('assoc' in g or 'anw' in g for g in chain(u)) else 1)
        if members and outsiders:
            m, o = members[0], outsiders[0]
            st = smb(m, 'ls', p)[0]
            f = first_file(p)
            if st == 'OK' and f:
                st = smb(m, f'get "{f}" /dev/null', p)[0]
            check(f'3a {m} reads {show(p)} via {via(m, p)}', st == 'OK', st + (f', read "{f}"' if f else ''))
            st = smb(o, 'ls', p)[0]
            check(f'3b {o} gets access denied on {show(p)}', st == 'NT_STATUS_ACCESS_DENIED', st)
            break
    else:
        check('3a/3b protected group folder with a member and an outsider', False, 'none found in acl.json')

    # Nested membership: someone who reads a folder only through a group inside a group.
    nested = [(u, p) for p in FOLDERS for u in PEOPLE
              if access(u, p) >= 1 and ' > ' in via(u, p) and os.path.isdir(os.path.join(VOL, p))]
    nested.sort(key=lambda up: (-via(*up).count(' > '), 'partner' not in ' '.join(chain(up[0]))))
    if nested:
        u, p = nested[0]
        st = smb(u, 'ls', p)[0]
        check(f'3c nested: {u} reads {show(p)} via {via(u, p)}', st == 'OK', st)
    else:
        check('3c nested group grant to test', False, 'no folder is reachable only through nested groups')

    # protect:false adds rights: someone who can write only because of such an entry.
    added = [(u, norm(e['path'])) for e in ENTRIES if not e.get('protect') for u in PEOPLE
             if access(u, norm(e['path'])) >= 2 > access(u, norm(e['path']), skip=e)]
    if added:
        u, p = added[0]
        st = can_write(u, p)
        check(f'3d protect:false grant lets {u} write in {show(p)} via {via(u, p)}', st == 'OK', st)

    homes = {u: norm(e['path']) for e in ENTRIES for u in PEOPLE
             if e.get('protect') and norm(e['path']).split('/')[-1].lower() == u.lower()
             and access(u, norm(e['path'])) >= 2}
    for u, home in homes.items():
        others = [h for v, h in homes.items() if v != u and access(u, h) == 0]
        if others:
            st = can_write(u, home)
            check(f'3e {u} writes in own home {show(home)}', st == 'OK', st)
            st = smb(u, 'ls', others[0])[0]
            check(f'3f {u} gets access denied on {show(others[0])}', st == 'NT_STATUS_ACCESS_DENIED', st)
            break
    else:
        check('3e/3f home folders (path ending in the sam name) to test', False, 'none found in acl.json')


def check4():
    off = [u['sam'] for u in D['users'] if not u.get('enabled', True) and not u.get('service')]
    if not off:
        return check('4 disabled account to test', False, 'no enabled=false person in directory.json')
    st = smb(off[0], 'ls')[0]
    check(f'4 disabled {off[0]} cannot log in (correct password)', st in ('NT_STATUS_ACCOUNT_DISABLED',
                                                                          'NT_STATUS_LOGON_FAILURE'), st)


def check5():
    users = {e['samaccountname'][0].lower(): e for e in
             ldap('(&(objectCategory=person)(objectClass=user))', ['sAMAccountName', 'mail', 'title', 'department'])}
    bad = [u for u in PEOPLE if u.lower() not in users or any(
        (USERS[u.lower()].get(k) or '') != (users[u.lower()].get(a, [''])[0])
        for k, a in (('mail', 'mail'), ('title', 'title'), ('department', 'department')))]
    check(f'5a LDAPS bind as {SVC}; people carry mail/title/department', not bad,
          f'{len(users)} user objects' + (f'; wrong: {", ".join(bad)}' if bad else ''))
    groups = ldap('(objectClass=group)', ['sAMAccountName', 'member'])
    dn2name = {e['dn'][0].lower(): e['samaccountname'][0].lower() for e in groups}
    dn2name.update({e['dn'][0].lower(): k for k, e in users.items()})
    found = {e['samaccountname'][0].lower(): {dn2name.get(m.lower(), m) for m in e.get('member', [])} for e in groups}
    bad = [g['name'] for g in D['groups'] if found.get(g['name'].lower()) !=
           {m.lower() for m in g.get('members', []) + g.get('member_groups', [])}]
    check('5b groups list their members as in directory.json', not bad, f'{len(D["groups"])} groups'
          + (f'; wrong: {", ".join(bad)}' if bad else ''))
    def depth(name):
        g = next((g for g in D['groups'] if g['name'].lower() == name.lower()), {})
        return 1 + max((depth(c) for c in g.get('member_groups', [])), default=-1)
    outer = max(D['groups'], key=lambda g: depth(g['name']))
    want = {u.lower() for u in USERS if outer['name'].lower() in chain(u) and not USERS[u].get('service')}
    gdn = next(e['dn'][0] for e in groups if e['samaccountname'][0].lower() == outer['name'].lower())
    got = {e['samaccountname'][0].lower() for e in ldap(
        f'(&(objectCategory=person)(memberOf:1.2.840.113556.1.4.1941:={gdn}))', ['sAMAccountName'])} - set(
        u for u in USERS if USERS[u].get('service'))
    check(f'5c nested membership: people in {outer["name"]} (any depth)', got == want, f'{len(got)} found')
    enabled = ldap('(&(objectCategory=person)(objectClass=user)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))',
                   ['sAMAccountName'], base=f'OU=Mitarbeiter,OU=Kanzlei,{BASE}')
    check('5d enabled people in LDAP = directory.json', len(enabled) == len(PEOPLE),
          f'{len(enabled)} in OU=Mitarbeiter, {len(PEOPLE)} expected')


def check6():
    rc, table = sql('knovas_ro', "SELECT TOP 1 QUOTENAME(TABLE_SCHEMA) + '.' + QUOTENAME(TABLE_NAME) FROM "
                                 "INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY 1")
    rc2, n = sql('knovas_ro', f'SELECT COUNT(*) FROM {table}') if rc == 0 and table else (1, table)
    check(f'6a knovas_ro: SELECT COUNT(*) FROM {table}', rc2 == 0 and n.isdigit(), f'{n} rows')
    rc, out = sql('knovas_ro', f'BEGIN TRAN; INSERT INTO {table} DEFAULT VALUES; ROLLBACK')
    check('6b knovas_ro: INSERT is denied', rc != 0 and 'permission was denied' in out,
          next((ln for ln in out.splitlines() if 'denied' in ln), out[:120]))


def check7():
    def files(root):
        return {os.path.relpath(os.path.join(d, f), root) for d, _, fs in os.walk(root) for f in fs}
    src, dst = files(SRC), files(VOL)
    check('7a file count in the share = source tree', len(src) == len(dst) and src == dst,
          f'{len(dst)} in share, {len(src)} in source' + (f', differ: {len(src ^ dst)}' if src != dst else ''))
    sample = random.choice(sorted(p for p in src if not set(p) & set('";')))
    s, v = os.stat(os.path.join(SRC, sample)).st_mtime, os.stat(os.path.join(VOL, sample)).st_mtime
    folder, name = os.path.split(sample)
    _, out = smb('Administrator', f'allinfo "{name}"', folder)
    m = re.search(r'write_time:\s+(\w+ \w+ +\d+ [\d:]+ \d+)', out)
    seen = datetime.datetime.strptime(m.group(1), '%a %b %d %H:%M:%S %Y').replace(
        tzinfo=datetime.timezone.utc).timestamp() if m else None
    when = datetime.datetime.fromtimestamp(s, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    check('7b sampled file mtime = source (volume and over SMB)',
          abs(s - v) < 1 and seen is not None and abs(s - seen) < 1, f'"{sample}" {when}')


for label, fn in (('1', check1), ('2', check2), ('3', check3), ('4', check4), ('5', check5), ('6', check6),
                  ('7', check7)):
    run(label, fn)

top = sorted(n for n in os.listdir(VOL) if os.path.isdir(os.path.join(VOL, n)) and access(SVC, n) >= 1)
print(f"CIFS-TARGET\t{SHARE}\t{DOM}\t{top[0] if top else ''}")
raise SystemExit(1 if failures else 0)
