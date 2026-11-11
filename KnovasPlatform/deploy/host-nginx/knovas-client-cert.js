// The client-certificate terminator of the connector protocol (S-6; KnovasPlatform/docs/connector-protocol-v1.md):
// X-Client-Cert-Fingerprint is the SHA-256 (hex) of the DER of the client certificate nginx verified. nginx's own
// $ssl_client_fingerprint is SHA-1, so this njs function computes it. Empty unless nginx verified the certificate
// against ssl_client_certificate: a client never chooses the value, and a call without a certificate (Knovas Link's
// enrolment, its heartbeat before the certificate is recorded) carries none.
//
// Installed by scripts/host-https.sh with a connector client CA, as /etc/nginx/njs/knovas-client-cert.js; loaded by
// knovas-connector-mtls.conf (needs nginx's njs module: Ubuntu/Debian package libnginx-mod-http-js).
var crypto = require('crypto');

function sha256(r) {
    if (r.variables.ssl_client_verify !== 'SUCCESS') {
        return '';
    }
    var pem = r.variables.ssl_client_raw_cert || '';
    var b64 = pem.replace(/-----BEGIN CERTIFICATE-----|-----END CERTIFICATE-----|\s/g, '');
    if (!b64) {
        return '';
    }
    return crypto.createHash('sha256').update(Buffer.from(b64, 'base64')).digest('hex');
}

export default { sha256 };
