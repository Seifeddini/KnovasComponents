"""Connection settings for the local identity database (KC-F1)."""

import pytest

from identity import db


class TestSettingsFromEnvironment:
    def test_the_password_is_read_from_the_secret_file(self, tmp_path, monkeypatch):
        secret = tmp_path / "pw"
        secret.write_text("s3cret-from-file\n", encoding="utf-8")
        monkeypatch.setenv("PLATFORM_DB_PASSWORD_FILE", str(secret))
        assert db.settings_from_env().password == "s3cret-from-file"

    def test_the_trailing_newline_is_stripped(self, tmp_path, monkeypatch):
        """Docker secrets and `echo >` both add one; a password with a newline
        appended fails authentication in a way nobody debugs quickly."""
        secret = tmp_path / "pw"
        secret.write_text("s3cret\n", encoding="utf-8")
        monkeypatch.setenv("PLATFORM_DB_PASSWORD_FILE", str(secret))
        assert db.settings_from_env().password == "s3cret"

    def test_the_file_wins_over_the_plain_variable(self, tmp_path, monkeypatch):
        secret = tmp_path / "pw"
        secret.write_text("from-file", encoding="utf-8")
        monkeypatch.setenv("PLATFORM_DB_PASSWORD_FILE", str(secret))
        monkeypatch.setenv("PLATFORM_DB_PASSWORD", "from-env")
        assert db.settings_from_env().password == "from-file"

    def test_the_plain_variable_is_used_when_there_is_no_file(self, monkeypatch):
        monkeypatch.delenv("PLATFORM_DB_PASSWORD_FILE", raising=False)
        monkeypatch.setenv("PLATFORM_DB_PASSWORD", "from-env")
        assert db.settings_from_env().password == "from-env"

    def test_a_missing_secret_file_is_a_named_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PLATFORM_DB_PASSWORD_FILE", str(tmp_path / "absent"))
        with pytest.raises(db.ConfigurationError) as excinfo:
            db.settings_from_env()
        assert "PLATFORM_DB_PASSWORD_FILE" in str(excinfo.value)

    def test_an_empty_secret_file_is_a_named_error(self, tmp_path, monkeypatch):
        secret = tmp_path / "pw"
        secret.write_text("   \n", encoding="utf-8")
        monkeypatch.setenv("PLATFORM_DB_PASSWORD_FILE", str(secret))
        with pytest.raises(db.ConfigurationError):
            db.settings_from_env()

    def test_no_password_at_all_is_a_named_error(self, monkeypatch):
        monkeypatch.delenv("PLATFORM_DB_PASSWORD_FILE", raising=False)
        monkeypatch.delenv("PLATFORM_DB_PASSWORD", raising=False)
        with pytest.raises(db.ConfigurationError):
            db.settings_from_env()

    def test_host_database_and_user_have_compose_matching_defaults(self, monkeypatch):
        monkeypatch.delenv("PLATFORM_DB_PASSWORD_FILE", raising=False)
        monkeypatch.setenv("PLATFORM_DB_PASSWORD", "x")
        for name in ("PLATFORM_DB_HOST", "PLATFORM_DB_NAME", "PLATFORM_DB_USER"):
            monkeypatch.delenv(name, raising=False)
        settings = db.settings_from_env()
        assert (settings.host, settings.database, settings.user) == (
            "platform-db", "knovas_platform", "platform",
        )


class TestTheDsnNeverLeaksTheSecret:
    def test_the_dsn_contains_the_password(self, monkeypatch):
        monkeypatch.delenv("PLATFORM_DB_PASSWORD_FILE", raising=False)
        monkeypatch.setenv("PLATFORM_DB_PASSWORD", "s3cret")
        assert "s3cret" in db.settings_from_env().dsn

    def test_the_safe_dsn_does_not(self, monkeypatch):
        """What goes in a log line or a support bundle."""
        monkeypatch.delenv("PLATFORM_DB_PASSWORD_FILE", raising=False)
        monkeypatch.setenv("PLATFORM_DB_PASSWORD", "s3cret")
        assert "s3cret" not in db.settings_from_env().safe_dsn

    def test_repr_does_not_leak_the_password(self, monkeypatch):
        """A settings object caught in a traceback must not publish it."""
        monkeypatch.delenv("PLATFORM_DB_PASSWORD_FILE", raising=False)
        monkeypatch.setenv("PLATFORM_DB_PASSWORD", "s3cret")
        assert "s3cret" not in repr(db.settings_from_env())

    def test_a_password_with_url_characters_survives_the_round_trip(self, monkeypatch):
        """Generated secrets contain @ / : # and would otherwise corrupt a DSN."""
        monkeypatch.delenv("PLATFORM_DB_PASSWORD_FILE", raising=False)
        monkeypatch.setenv("PLATFORM_DB_PASSWORD", "p@ss/w:rd#1")
        from urllib.parse import urlsplit, unquote
        assert unquote(urlsplit(db.settings_from_env().dsn).password) == "p@ss/w:rd#1"
