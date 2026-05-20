import pytest

from config import load_config, reset_config

_REQUIRED = (
    "KNOVAS_INTERNAL_API_URL",
    "RC_INSTANCE_TOKEN",
    "RC_CLIENT_ID",
    "RC_WATCH_ROOTS",
    "SEMANTIX_SECURE_BASE_URL",
    "SEMANTIX_CLIENT_CERT_PATH",
    "SEMANTIX_CLIENT_KEY_PATH",
    "SEMANTIX_CA_CERT_PATH",
)


def test_load_config_exits_when_required_env_missing(monkeypatch):
    monkeypatch.delenv("RC_SKIP_CONFIG_VALIDATION", raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    for key in _REQUIRED:
        monkeypatch.delenv(key, raising=False)
    reset_config()
    with pytest.raises(SystemExit) as exc:
        load_config(validate=True, force_reload=True)
    assert exc.value.code == 1
