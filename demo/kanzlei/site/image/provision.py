#!/usr/bin/env python3
"""First start of the dc container: provision the AD domain, then create OUs, users and groups.

Reads /in/site/directory.json and the passwords from the environment (.env). Runs offline, before samba starts.
"""
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

DIR_JSON = '/in/site/directory.json'
KEEP = '/var/lib/samba/kanzlei'          # our own files inside the dc-state volume
DC_IP = '172.28.215.10'
OUS = {'people': 'OU=Mitarbeiter,OU=Kanzlei', 'service': 'OU=Dienstkonten,OU=Kanzlei',
       'leavers': 'OU=Ehemalige,OU=Kanzlei', 'groups': 'OU=Gruppen,OU=Kanzlei'}


def pw_key(sam):
    """.env key of a service account's password: svc-knovas -> PW_SVC_KNOVAS."""
    return 'PW_' + re.sub(r'[^A-Z0-9]', '_', sam.upper())


def need(key):
    if not os.environ.get(key):
        sys.exit(f'provision: {key} is missing in .env - run init-site.sh again')
    return os.environ[key]


def run(args, **kw):
    """subprocess.run that never echoes its arguments (they can hold a password)."""
    rc = subprocess.run(args, **kw).returncode
    if rc:
        sys.exit(f'provision: {" ".join(args[:3])} failed with exit code {rc}')


def opt(obj, key):
    """A directory.json string field, or None when empty (LDAP has no empty values)."""
    value = (obj.get(key) or '').strip()
    return value or None


raw = open(DIR_JSON, 'rb').read()
D = json.loads(raw.decode('utf-8'))
admin_pw, people_pw = need('PW_DOMAIN_ADMIN'), need('PW_PEOPLE')
svc_pw = {u['sam']: need(pw_key(u['sam'])) for u in D['users'] if u.get('service')}
share = D['share']['name']

# 1. The domain itself.
run(['samba-tool', 'domain', 'provision', '--server-role=dc', '--dns-backend=SAMBA_INTERNAL',
     f"--realm={D['realm']}", f"--domain={D['netbios']}", '--host-name=srv-dc01',
     f'--host-ip={DC_IP}', f'--adminpass={admin_pw}',
     '--option=dns forwarder = 127.0.0.11',          # Docker's resolver for everything else
     '--option=server min protocol = SMB2_02',
     '--option=log level = 1'])

# 2. OUs, users, groups. (The share section is added by dc-entrypoint.sh on every start.)
import ldb                                              # noqa: E402 (samba modules only after provisioning)
from samba.auth import system_session                   # noqa: E402
from samba.param import LoadParm                        # noqa: E402
from samba.samdb import SamDB                           # noqa: E402

lp = LoadParm()
lp.load('/etc/samba/smb.conf')
samdb = SamDB(url=lp.samdb_url(), session_info=system_session(), lp=lp)
base = str(samdb.domain_dn())
for rdn in ['OU=Kanzlei'] + list(OUS.values()):
    samdb.add({'dn': f'{rdn},{base}', 'objectClass': 'organizationalUnit'})


def by_sam(sam):
    res = samdb.search(base, scope=ldb.SCOPE_SUBTREE, expression=f'(sAMAccountName={ldb.binary_encode(sam)})',
                       attrs=['dn'])
    return res[0].dn if res else None


for u in D['users']:
    sam, svc = u['sam'], bool(u.get('service'))
    ou = OUS['service'] if svc else (OUS['people'] if u.get('enabled', True) else OUS['leavers'])
    given, surname = opt(u, 'given'), opt(u, 'surname')
    kw = dict(userou=ou, givenname=given, surname=surname, jobtitle=opt(u, 'title'),
              department=opt(u, 'department'), description=opt(u, 'description'),
              mailaddress=opt(u, 'mail'), telephonenumber=opt(u, 'telephone'))
    pw = svc_pw[sam] if svc else people_pw
    try:   # people get CN "Given Surname", service accounts CN = sam
        samdb.newuser(sam, pw, useusernameascn=svc or not (given or surname), **kw)
    except ldb.LdbError as e:
        if e.args[0] != ldb.ERR_ENTRY_ALREADY_EXISTS:
            raise
        samdb.newuser(sam, pw, useusernameascn=True, **kw)   # two people with the same name in one OU
    if opt(u, 'display'):
        m = ldb.Message(by_sam(sam))
        m['displayName'] = ldb.MessageElement(u['display'], ldb.FLAG_MOD_REPLACE, 'displayName')
        samdb.modify(m)
    if svc:
        samdb.setexpiry(f'(sAMAccountName={ldb.binary_encode(sam)})', 0, no_expiry_req=True)
    if not u.get('enabled', True):
        samdb.disable_account(f'(sAMAccountName={ldb.binary_encode(sam)})')

for g in D['groups']:
    if by_sam(g['name']) is None:      # built-in groups (e.g. Domain Admins) already exist
        samdb.newgroup(g['name'], groupou=OUS['groups'], description=opt(g, 'description'))
for g in D['groups']:
    members = list(g.get('members', [])) + list(g.get('member_groups', []))
    if members:
        samdb.add_remove_group_members(g['name'], members, add_members_operation=True)

# 3. Passwords never expire (a demo site must not lock itself out after 42 days).
run(['samba-tool', 'domain', 'passwordsettings', 'set', '--max-pwd-age=0', '-H', lp.samdb_url()],
    stdout=subprocess.DEVNULL)

# 4. Remember what was provisioned; the entrypoint builds smb.conf from here on every start.
os.makedirs(KEEP, exist_ok=True)
with open('/etc/samba/smb.conf', encoding='utf-8') as src, open(f'{KEEP}/smb.conf', 'w', encoding='utf-8') as dst:
    dst.write(src.read())
with open(f'{KEEP}/directory.json', 'wb') as f:
    f.write(raw)
with open(f'{KEEP}/site.env', 'w', encoding='utf-8') as f:
    f.write(f"DNS_DOMAIN={shlex.quote(D['dns_domain'].lower())}\nNETBIOS={shlex.quote(D['netbios'])}\n"
            f"SHARE={shlex.quote(share)}\nSHARE_COMMENT={shlex.quote(D['share'].get('comment') or '')}\n")
with open(f'{KEEP}/provisioned', 'w', encoding='utf-8') as f:
    f.write(hashlib.sha256(raw).hexdigest() + '\n')
n_people = sum(1 for u in D['users'] if not u.get('service'))
print(f"provision: domain {D['realm']} ready: {n_people} people, {len(svc_pw)} service accounts, "
      f"{len(D['groups'])} groups, share [{share}]")
