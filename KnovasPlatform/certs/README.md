# mTLS certificates

Place here (gitignored): `client.crt`, `client.key`, `ca.crt` — paths must match `.env`.

These are the same three files Knovas ships as `client-cert.pem`,
`client-key.pem`, and `ca-root.pem` (the names RemoteController uses). Rename
them on copy; KnovasPlatform's `verify_deploy` checks for the `.crt` / `.key`
spelling.

See [docs/setup.md](../docs/setup.md) step 4 and the cross-component reference
in [docs/certificates.md](../../docs/certificates.md).
