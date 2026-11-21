#!/bin/bash
# Healthcheck of the dc container: LDAP answers, winbind reaches the DC, SMB port is open.
ldapsearch -x -H ldap://127.0.0.1 -s base -b '' -LLL defaultNamingContext 2>/dev/null | grep -q defaultNamingContext \
  && wbinfo -P >/dev/null 2>&1 \
  && (exec 3<>/dev/tcp/127.0.0.1/445) 2>/dev/null
