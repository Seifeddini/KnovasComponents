"""Argon2id hashing and the password policy for Platform accounts.

Why this module exists
----------------------
Until now the Platform had one credential for the whole firm, compared with
``hmac.compare_digest`` against a plaintext config value (``app.py:864``). Per-user
accounts need a stored verifier instead: a salted, memory-hard hash that is safe
to keep in a database the firm backs up and copies.

Policy lives here too, not at the call site, so the login form, the admin
console's "reset password", and the first-boot bootstrap all refuse the same
values. ``PLACEHOLDER_VALUES`` is deliberately the same set ``app.py:701``
already refuses for ``COMPANY_LOGIN_PASSWORD`` — an operator who worked around
that check by pasting the placeholder into the new admin account would otherwise
reintroduce exactly the credential the check exists to stop.

Module invariants
-----------------
    - ``hash_password`` never returns a hash for a password the policy rejects.
      Validating at the call site is optional; validating here is not.
    - ``verify_password`` returns a bool for every input. A malformed, empty or
      NULL-sourced stored value fails closed rather than raising, because it is
      read from a database column that may legitimately be NULL for
      federated-only accounts.
    - Whitespace is never trimmed. A password the user cannot retype is a
      support call, not a convenience.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

# OWASP's second-choice argon2id profile (46 MiB, t=1, p=1) rounded to 64 MiB.
# Chosen over the t=2/19 MiB profile because the Platform authenticates a few
# dozen people a day, not a few thousand a second: memory is the cheaper axis.
_TIME_COST = 2
_MEMORY_COST_KIB = 65536
_PARALLELISM = 1
_HASH_LENGTH = 32
_SALT_LENGTH = 16

MIN_LENGTH = 12

#: Values ``app.py:701`` already refuses for the shared company login. Kept
#: identical so the new account cannot become the old problem under a new name.
PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "change-me",
        "change-me-in-production",
        "change-me-company-password",
        "replace-with-strong-company-password",
        "replace-with-random-hex",
    }
)

_hasher = PasswordHasher(
    time_cost=_TIME_COST,
    memory_cost=_MEMORY_COST_KIB,
    parallelism=_PARALLELISM,
    hash_len=_HASH_LENGTH,
    salt_len=_SALT_LENGTH,
)


class WeakPasswordError(ValueError):
    """Raised by :func:`hash_password` when the policy rejects the password.

    Carries every reason, not the first, so a form can show them all at once.
    """

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def check_policy(password: str) -> list[str]:
    """Return every reason ``password`` is unacceptable; empty list means fine.

    Returns:
        list[str]: human-readable reasons, safe to show in a form.
    """
    reasons: list[str] = []
    if not isinstance(password, str):
        return ["Password must be text."]
    if len(password) < MIN_LENGTH:
        reasons.append(f"Password must be at least {MIN_LENGTH} characters long.")
    if password.strip() == "":
        reasons.append("Password must contain more than whitespace.")
    elif password != password.strip():
        reasons.append("Password must not start or end with a space.")
    if password.strip().lower() in PLACEHOLDER_VALUES:
        reasons.append("This is a placeholder value and must be changed.")
    return reasons


def hash_password(password: str) -> str:
    """Hash ``password`` with argon2id after enforcing :func:`check_policy`.

    Raises:
        WeakPasswordError: the policy rejected the password. Nothing is hashed.
    """
    reasons = check_policy(password)
    if reasons:
        raise WeakPasswordError(reasons)
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """Return whether ``password`` matches ``stored_hash``. Never raises.

    A missing, empty or unparseable ``stored_hash`` is False: the column is
    NULL for federated-only accounts, and a corrupted value must not become an
    authentication bypass or a 500.
    """
    if not stored_hash or not isinstance(stored_hash, str):
        return False
    try:
        return bool(_hasher.verify(stored_hash, password))
    except (Argon2Error, InvalidHashError, TypeError, ValueError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Whether ``stored_hash`` was made with weaker parameters than we use now.

    Call after a successful verify: that is the only moment the plaintext is
    available to re-hash with.
    """
    if not stored_hash or not isinstance(stored_hash, str):
        return False
    try:
        return bool(_hasher.check_needs_rehash(stored_hash))
    except (Argon2Error, InvalidHashError, TypeError, ValueError):
        return False
