"""mTLS certificate freshness checks and CSR-based auto-renewal for RemoteController."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional, Tuple

import requests
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from config import get_config

logger = logging.getLogger(__name__)

_cert_lock = threading.Lock()
_last_cert_check_at = 0.0


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _parse_cert_expiry(cert_path: str) -> Optional[datetime]:
    try:
        cert = x509.load_pem_x509_certificate(
            Path(cert_path).read_bytes(), default_backend()
        )
        return cert.not_valid_after_utc
    except Exception as exc:
        logger.warning("Could not parse cert expiry: %s", exc)
        return None


def _atomic_write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(target.parent), delete=False) as tmp:
        tmp.write(content)
        tmp_name = tmp.name
    os.replace(tmp_name, str(target))


def _write_temp_pem(target_path: str, content: str) -> str:
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(target.parent),
        delete=False,
        suffix=".pem.tmp",
    ) as tmp:
        tmp.write(content)
        return tmp.name


def _validate_pair(base_url: str, ca_path: str, cert_path: str, key_path: str) -> bool:
    session = requests.Session()
    session.cert = (cert_path, key_path)
    if ca_path:
        session.verify = ca_path
    try:
        resp = session.get(f"{base_url}/secured/health", timeout=30, allow_redirects=False)
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Renewed certificate validation failed: %s", exc)
        return False
    finally:
        session.close()


def _generate_csr_key_pair(cert_path: str) -> Tuple[str, str]:
    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes(), default_backend())
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(cert.subject)
        .sign(private_key, hashes.SHA256(), default_backend())
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return csr_pem, key_pem


def _sign_certificate_csr(base_url: str, cert_path: str, key_path: str, ca_path: str, csr_pem: str) -> dict:
    session = requests.Session()
    session.cert = (cert_path, key_path)
    if ca_path:
        session.verify = ca_path
    resp = session.post(
        f"{base_url}/secured/sign_certificate",
        json={"csr": csr_pem, "validity_days": 365},
        timeout=60,
        allow_redirects=False,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def _install_certificate_pair(
    cert_path: str,
    key_path: str,
    certificate_pem: str,
    private_key_pem: str,
    *,
    base_url: str,
    ca_path: str,
) -> bool:
    cert_tmp: Optional[str] = None
    key_tmp: Optional[str] = None
    try:
        cert_tmp = _write_temp_pem(cert_path, certificate_pem)
        key_tmp = _write_temp_pem(key_path, private_key_pem)
        if not _validate_pair(base_url, ca_path, cert_tmp, key_tmp):
            return False
        orig_cert = Path(cert_path).read_text(encoding="utf-8") if Path(cert_path).exists() else None
        orig_key = Path(key_path).read_text(encoding="utf-8") if Path(key_path).exists() else None
        try:
            os.replace(cert_tmp, cert_path)
            cert_tmp = None
            os.replace(key_tmp, key_path)
            key_tmp = None
        except Exception as exc:
            logger.error("RC cert install failed (%s); rolling back", exc)
            if orig_cert is not None:
                _atomic_write(cert_path, orig_cert)
            if orig_key is not None:
                _atomic_write(key_path, orig_key)
            return False
        logger.info("RC certificate auto-renewed via CSR")
        return True
    finally:
        for tmp in (cert_tmp, key_tmp):
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


def ensure_mtls_certificate_freshness() -> None:
    """Renew tenant mTLS cert when within threshold (CSR by default)."""
    global _last_cert_check_at
    if not _env_bool("SEMANTIX_CERT_AUTO_RENEW_ENABLED", True):
        return

    cfg = get_config()
    cert_path = cfg.semantix_client_cert_path
    key_path = cfg.semantix_client_key_path
    ca_path = cfg.semantix_ca_cert_path
    base_url = cfg.semantix_secure_base_url
    if not all([cert_path, key_path, ca_path, base_url]):
        return

    check_interval = _env_int("SEMANTIX_CERT_CHECK_INTERVAL_SECONDS", 3600)
    threshold_days = _env_int("SEMANTIX_CERT_RENEW_THRESHOLD_DAYS", 30)
    renew_method = (os.environ.get("SEMANTIX_CERT_RENEW_METHOD") or "csr").strip().lower()

    with _cert_lock:
        now_ts = time.time()
        if now_ts - _last_cert_check_at < check_interval:
            return
        _last_cert_check_at = now_ts

        not_after = _parse_cert_expiry(cert_path)
        if not not_after:
            return
        remaining_days = (not_after - datetime.now(timezone.utc)).total_seconds() / 86400
        if remaining_days > threshold_days:
            return

        logger.info(
            "RC certificate expires in %.2f days (threshold=%s), attempting auto-renew",
            remaining_days,
            threshold_days,
        )

        if renew_method == "legacy":
            logger.warning("RC legacy cert renew is not implemented; use SEMANTIX_CERT_RENEW_METHOD=csr")
            return

        try:
            csr_pem, key_pem = _generate_csr_key_pair(cert_path)
            payload = _sign_certificate_csr(base_url, cert_path, key_path, ca_path, csr_pem)
        except Exception as exc:
            logger.error("RC CSR auto-renew failed: %s", exc)
            return

        certificate_pem = payload.get("certificate")
        if not certificate_pem:
            logger.error("RC CSR auto-renew response missing certificate")
            return
        chain = payload.get("certificate_chain")
        if chain:
            certificate_pem = f"{str(certificate_pem).strip()}\n{str(chain).strip()}\n"
        _install_certificate_pair(
            cert_path,
            key_path,
            str(certificate_pem).strip() + "\n",
            str(key_pem).strip() + "\n",
            base_url=base_url,
            ca_path=ca_path,
        )
