#!/bin/bash
# dc container: provision the domain on first start (state lives in the dc-state volume), then run Samba.
set -euo pipefail
KEEP=/var/lib/samba/kanzlei

if [ ! -f "$KEEP/provisioned" ]; then
  [ -f /in/site/directory.json ] || { echo "dc: /in/site/directory.json not found (DEMO_SITE_DIR in .env)" >&2; exit 1; }
  echo "dc: first start - provisioning the domain from directory.json"
  find /var/lib/samba -mindepth 1 -delete      # leftovers of an interrupted attempt
  mkdir -p /var/lib/samba/private
  rm -f /etc/samba/smb.conf
  python3 /opt/site/provision.py
elif [ -f /in/site/directory.json ] && ! cmp -s /in/site/directory.json "$KEEP/directory.json"; then
  echo "dc: WARNING directory.json differs from the one this domain was provisioned from;" \
       "run reset.sh --all and up.sh to provision again" >&2
fi

. "$KEEP/site.env"
# smb.conf = provisioned [global] + the hidden share. POSIX ACLs are its only permission store (no acl_xattr, see
# apply_acls.py); "inherit acls" lets new files take the folder's default ACL in full, as on Windows.
{ cat "$KEEP/smb.conf"
  printf '\n[%s]\n\tpath = /srv/shares\n\tcomment = %s\n\tread only = no\n\tbrowseable = no\n' "$SHARE" "$SHARE_COMMENT"
  printf '\tvfs objects = dfs_samba4\n\tinherit acls = yes\n'
} > /etc/samba/smb.conf
cp /var/lib/samba/private/krb5.conf /etc/krb5.conf
grep -q "srv-dc01.$DNS_DOMAIN" /etc/hosts || echo "172.28.215.10 srv-dc01.$DNS_DOMAIN srv-dc01" >> /etc/hosts
# The DC resolves through its own DNS (AD zone); Samba forwards all other names to Docker's resolver 127.0.0.11.
printf 'search %s\nnameserver 172.28.215.10\n' "$DNS_DOMAIN" > /etc/resolv.conf
mkdir -p /srv/shares
exec samba --interactive
