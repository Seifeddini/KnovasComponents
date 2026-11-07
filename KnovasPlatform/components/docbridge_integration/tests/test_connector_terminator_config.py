"""The host nginx terminator of the connector protocol's client certificate (S-6, FEAT-2026-11-05-knovas-brain-v1).

Static checks on ``KnovasPlatform/deploy/host-nginx`` and ``scripts/host-https.sh``: the template never passes on an
``X-Client-Cert-Fingerprint`` a client sent (it sets the header empty), the terminator lines are off until host-https.sh
is given the client CA, and then the header is the SHA-256 that ``knovas-client-cert.js`` computes from the certificate
nginx verified, and nothing else. Run by hand in nginx with njs on 2026-11-07: a verified certificate gave
``sha256(DER)``, no certificate and a forged header gave an empty header, a certificate of another CA was refused (400).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
NGINX = REPO / "KnovasPlatform" / "deploy" / "host-nginx"
TEMPLATE = NGINX / "knovas-platform.conf.example"
HOST_HTTPS = REPO / "scripts" / "host-https.sh"


def _render_with_terminator(text: str) -> str:
    """The two substitutions host-https.sh makes when it has the client CA."""
    script = HOST_HTTPS.read_text(encoding="utf-8")
    exprs = re.findall(r"-e '(s/[^']+)'", script[script.index('if [[ "$CONNECTOR_MTLS" == true ]]; then'):])
    assert len(exprs) == 2, exprs
    out = subprocess.run(["sed", "-e", exprs[0], "-e", exprs[1]], input=text, capture_output=True, text=True, check=True)
    return out.stdout


def _active(text: str) -> list:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def test_the_template_strips_the_header_and_leaves_the_terminator_off():
    active = _active(TEMPLATE.read_text(encoding="utf-8"))
    assert 'proxy_set_header X-Client-Cert-Fingerprint "";' in active
    assert not any(line.startswith(("ssl_client_certificate", "ssl_verify_client")) for line in active)
    assert not any("knovas_client_cert_sha256" in line for line in active)


def test_with_the_client_ca_the_header_is_the_verified_certificates_hash():
    active = _active(_render_with_terminator(TEMPLATE.read_text(encoding="utf-8")))
    assert "ssl_client_certificate /etc/nginx/knovas/connector-client-ca.pem;" in active
    assert "ssl_verify_client optional;" in active                     # Link enrols without a certificate
    assert "proxy_set_header X-Client-Cert-Fingerprint $knovas_client_cert_sha256;" in active
    assert sum("X-Client-Cert-Fingerprint" in line for line in active) == 1
    assert not any(line.startswith("location") and "proxy_set_header" in line for line in active)


def test_the_hash_is_empty_unless_nginx_verified_the_certificate():
    js = (NGINX / "knovas-client-cert.js").read_text(encoding="utf-8")
    assert "r.variables.ssl_client_verify !== 'SUCCESS'" in js and "return '';" in js
    assert "createHash('sha256')" in js and "ssl_client_raw_cert" in js and ".digest('hex')" in js
    conf = _active((NGINX / "knovas-connector-mtls.conf").read_text(encoding="utf-8"))
    assert conf == ["js_import knovas_client_cert from /etc/nginx/njs/knovas-client-cert.js;",
                    "js_set $knovas_client_cert_sha256 knovas_client_cert.sha256;"]


def test_host_https_installs_the_terminator_only_with_a_client_ca():
    script = HOST_HTTPS.read_text(encoding="utf-8")
    assert 'CONNECTOR_CA_IN="${3:-}"' in script
    guarded = script[script.index('if [[ -f "$CONNECTOR_CA" ]]; then'):script.index("CONNECTOR_MTLS=true")]
    assert "/etc/nginx/njs/knovas-client-cert.js" in guarded and "/etc/nginx/conf.d/knovas-connector-mtls.conf" in guarded
    assert "libnginx-mod-http-js" in guarded and "grep -Rqs ngx_http_js_module" in guarded
