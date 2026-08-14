"""Password hashing and policy for per-user Platform accounts (KC-B1-1)."""

import pytest

from identity import passwords


class TestHashing:
    def test_verify_accepts_the_password_that_was_hashed(self):
        stored = passwords.hash_password("korrektes-pferd-batterie-heftklammer")
        assert passwords.verify_password(stored, "korrektes-pferd-batterie-heftklammer") is True

    def test_verify_rejects_a_different_password(self):
        stored = passwords.hash_password("korrektes-pferd-batterie-heftklammer")
        assert passwords.verify_password(stored, "korrektes-pferd-batterie-heftklammee") is False

    def test_hash_is_argon2id(self):
        assert passwords.hash_password("korrektes-pferd-batterie-heftklammer").startswith(
            "$argon2id$"
        )

    def test_two_hashes_of_one_password_differ(self):
        """Distinct salts. Equal hashes would mean the salt is fixed."""
        a = passwords.hash_password("korrektes-pferd-batterie-heftklammer")
        b = passwords.hash_password("korrektes-pferd-batterie-heftklammer")
        assert a != b

    def test_verify_rejects_a_hash_it_cannot_parse(self):
        """A corrupted or truncated column value must fail closed, not raise."""
        assert passwords.verify_password("not-a-hash", "anything") is False

    def test_verify_rejects_an_empty_stored_hash(self):
        """Federated-only accounts store NULL; an empty value must never verify."""
        assert passwords.verify_password("", "anything") is False


class TestPolicy:
    def test_a_long_passphrase_passes(self):
        assert passwords.check_policy("korrektes-pferd-batterie-heftklammer") == []

    def test_too_short_is_rejected_with_the_required_length(self):
        errors = passwords.check_policy("kurz")
        assert errors
        assert str(passwords.MIN_LENGTH) in errors[0]

    def test_the_shipped_placeholders_are_rejected(self):
        """The values app.py:701 already refuses for the shared login."""
        for placeholder in (
            "change-me",
            "change-me-company-password",
            "replace-with-strong-company-password",
        ):
            assert passwords.check_policy(placeholder), placeholder

    def test_whitespace_only_is_rejected(self):
        assert passwords.check_policy("            ") != []

    def test_leading_and_trailing_whitespace_is_rejected(self):
        """Silently trimming would make a password unreproducible for the user."""
        assert passwords.check_policy(" korrektes-pferd-batterie-heftklammer ") != []

    def test_policy_rejection_is_reported_before_hashing(self):
        with pytest.raises(passwords.WeakPasswordError):
            passwords.hash_password("kurz")
