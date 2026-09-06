# mTLS certificates — moved

**Certificates no longer live here.** Both components read one directory now:
the repo root `certs/`, mounted into each container by the root
`docker-compose.yml`.

Put the files Knovas ships there under their **original** names:

```bash
cd KnovasComponents
mkdir -p certs
cp /path/to/{client-cert.pem,client-key.pem,ca-root.pem} certs/
chmod 600 certs/client-key.pem
```

`./scripts/setup.sh` then creates the `client.crt` / `client.key` / `ca.crt`
symlinks the Platform expects beside them, so nothing has to be renamed by hand.

See [docs/setup.md](../docs/setup.md) step 4 and the cross-component reference
in [docs/certificates.md](../../docs/certificates.md).
