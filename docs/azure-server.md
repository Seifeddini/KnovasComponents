# One client, one Azure server, documents in OneDrive/SharePoint

Setting up a dedicated server for one client whose documents live in OneDrive
or SharePoint takes about an hour. Nothing of the client's documents is stored
on it.

**You need:**

- the client's tenant certificates (`client-cert.pem`, `client-key.pem`,
  `ca-root.pem`)
- from the client: an Entra app (client ID and secret) and the folder address
  (see step 1)
- the Azure CLI, logged in
- a DNS name you control, for example `acme.knovas.ch`

## 1. The client (Microsoft 365 admin, once)

- **Entra admin center → App registrations → New registration**, then
  **API permissions → Microsoft Graph → Application → `Sites.Read.All`** →
  **Grant admin consent**.
- **Certificates & secrets → New client secret**, valid for 12 months.
- Send over a secure channel: the **Application (client) ID**, the secret's
  **Value**, and the **folder address**. The folder address is the browser's
  address bar while the folder is open; a "Copy link" sharing link does not
  work.
- Share the folder with everyone who should **Öffnen** documents. The preview
  inside Knovas works without that.

Details and troubleshooting: [microsoft-365.md](microsoft-365.md).

## 2. The server (your machine)

```bash
./scripts/azure/create-server.sh --name acme --ssh-from <your office IP>/32 \
    --https-from <client office IP>/32     # omit to allow the whole internet
```

- **What it creates:** a Switzerland North VM with secure boot, encrypted
  disks, SSH key and Entra ID login only, daily backups, and a firewall with
  just 443, 80 (for Let's Encrypt) and 22 (from your address).
- **At first boot** the VM installs Docker, nginx, certbot and automatic
  security updates, then clones this repository to `/opt/knovas`.
- **Afterwards:** point `acme.knovas.ch` at the IP it prints.

## 3. The stack (on the server)

```bash
scp client-cert.pem client-key.pem ca-root.pem knovas@<ip>:/opt/knovas/certs/
ssh knovas@<ip>
cd /opt/knovas
cp knovas.env.example knovas.env && chmod 600 knovas.env
```

Edit `knovas.env`. Delete the `KNOVAS_DOCUMENTS_PATH` line and set:

```bash
KNOVAS_PLATFORM_URL=https://acme.knovas.ch
KNOVAS_DOCUMENTS_URL=<folder address from the client>
M365_CLIENT_ID=<application id>
M365_CLIENT_SECRET=<secret value>
PLATFORM_ADMIN_EMAIL=<the client's administrator>
```

Then:

```bash
./scripts/setup.sh && ./scripts/start.sh     # ends by checking the OneDrive/SharePoint folder
sudo ./scripts/host-https.sh acme.knovas.ch <your email>
./scripts/doctor.sh
```

`start.sh` prints `ok` for the sign-in, the address and the folder. A `FAIL`
line names its fix. The first sync starts by itself.

## 4. Hand over

```bash
docker compose --env-file knovas.env exec docbridge-web cat /app/data/platform-admin-bootstrap
```

1. Give the client's administrator the one-time password above, over a secure
   channel.
2. They sign in at `https://acme.knovas.ch` and change the password.
3. Delete the file.
4. They create an account for each person under **Verwaltung**.

## A client already served by another server

The certificates from the other server work here unchanged: copy them in step 3.

If the other server indexed the same documents (from a share, or an older
OneDrive mirror), **stop its sync first** (`./scripts/stop.sh` there). Otherwise
both servers index into the same tenant and search shows every document twice.

To start the index clean, remove the old one once, with the tenant certificate.
Only do this when the OneDrive/SharePoint folder replaces everything that was
indexed:

```bash
curl -X DELETE --cert client-cert.pem --key client-key.pem --cacert ca-root.pem \
  -H 'Content-Type: application/json' \
  -d "{\"confirm_client_id\": \"$(openssl x509 -in client-cert.pem -noout -subject -nameopt sep_multiline | sed -n 's/^ *CN=//p')\"}" \
  https://api.knovas.ch:8443/secured/delete_all_documents
```

Accounts do not move with the certificates: people get new accounts on this
server (step 4).

## Afterwards

| When | What |
|---|---|
| Updates | `cd /opt/knovas && git pull && ./scripts/setup.sh && ./scripts/start.sh` |
| Before the secret expires | New secret in Entra → `M365_CLIENT_SECRET` in `knovas.env` → `./scripts/start.sh` → delete the old secret |
| Something looks wrong | `./scripts/doctor.sh` |
| The client leaves | They delete the Entra app; you revoke the tenant certificate and `az group delete -n rg-knovas-acme` |

## What is protected, and how

| For | How |
|---|---|
| **The client's documents** | They stay in OneDrive/SharePoint. The server keeps only search snippets and a file list; a file is held only in memory while it is indexed. Disks are encrypted, and the server is in Switzerland. |
| **Microsoft 365 access** | The app is read-only (`Sites.Read.All`). Its secret reaches only the sync container, never the internet-facing Platform or the generated config files. Deleting the Entra app ends access at once. |
| **Who can sign in** | Personal accounts, and a login throttle in nginx. With `--https-from`, only the client's office can reach the Platform at all. |
| **Knovas** | The server holds one tenant's certificate and nothing else of Knovas: no staff credentials, no CA, no link to other networks. Revoking the certificate ends its access within about a minute. SSH is key-only, Entra-audited, and allowed only from your address. |
