from config import AppConfig, load_config, reset_config
from sync.default_sync_body import build_default_sync_body


def test_build_default_sync_body_uses_watch_root_and_prefix(monkeypatch):
    monkeypatch.setenv("KNOVAS_IDENTIFIER_PREFIX", "pilot")
    monkeypatch.setenv("RC_WATCH_ROOTS", "/mnt/documents")
    reset_config()
    cfg = load_config(validate=False, force_reload=True)
    body = build_default_sync_body(cfg)
    assert body["sources"] == [{"path": "/mnt/documents", "recursive": True}]
    assert body["ingestion"]["identifier_prefix"] == "pilot"
    assert "**/*.pdf" in body["filters"]["include_globs"]


def test_knovas_api_url_alias(monkeypatch):
    monkeypatch.delenv("SEMANTIX_SECURE_BASE_URL", raising=False)
    monkeypatch.setenv("KNOVAS_API_URL", "https://api.example:8443")
    monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "true")
    for key in (
        "RC_CLIENT_ID",
        "RC_WATCH_ROOTS",
        "SEMANTIX_CLIENT_CERT_PATH",
        "SEMANTIX_CLIENT_KEY_PATH",
        "SEMANTIX_CA_CERT_PATH",
    ):
        monkeypatch.setenv(key, "x")
    monkeypatch.delenv("KNOVAS_INTERNAL_API_URL", raising=False)
    monkeypatch.delenv("RC_INSTANCE_TOKEN", raising=False)
    reset_config()
    cfg = load_config(validate=True, force_reload=True)
    assert cfg.semantix_secure_base_url == "https://api.example:8443"
