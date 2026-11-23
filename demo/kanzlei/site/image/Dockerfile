# One image for all site containers: dc (Samba AD DC + file server), loader and client (SMB, LDAP, SQL tools).
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC \
    PATH=/opt/mssql-tools18/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      samba samba-ad-dc samba-ad-provision samba-dsdb-modules samba-vfs-modules \
      winbind libnss-winbind krb5-user smbclient cifs-utils ldap-utils acl attr \
      python3 rsync dnsutils iproute2 procps ca-certificates curl gnupg \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list -o /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends mssql-tools18 unixodbc \
 && rm -rf /var/lib/apt/lists/* /etc/samba/smb.conf /etc/krb5.conf

COPY *.sh *.py /opt/site/
# Strip CR in case the files were checked out with Windows line endings.
RUN sed -i 's/\r$//' /opt/site/* && chmod 755 /opt/site/*
