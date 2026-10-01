"""Credential storage for local and managed-container runtimes.

Local secrets are stored in the OS keyring:
  - Windows  → Credential Manager (DPAPI-backed)
  - macOS    → Keychain
  - Linux    → SecretStorage (GNOME Keyring / KWallet)
  - Headless → encrypted file via ``keyrings.alt`` (install with the
               ``[headless]`` extra: ``pip install tastytrade-mcp[headless]``)

Managed container platforms may inject the explicitly supported environment
variables from their secret store. They take precedence over the local keyring.
Do not put those variables in ``.env`` or commit their values.

Secrets are never logged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import keyring
import keyring.backend
import keyring.errors

logger = logging.getLogger(__name__)

SERVICE_NAME = "tastytrade-mcp"

# Logical secret keys.
CLIENT_SECRET = "client_secret"
REFRESH_TOKEN = "refresh_token"
ACCOUNT_NUMBER = "account_number"

# The optional/required secrets needed to build an OAuth session.
REQUIRED_SECRETS = (CLIENT_SECRET, REFRESH_TOKEN)
ALL_SECRETS = (CLIENT_SECRET, REFRESH_TOKEN, ACCOUNT_NUMBER)

_ENV_SECRET_NAMES = {
    CLIENT_SECRET: "TASTYTRADE_CLIENT_SECRET",
    REFRESH_TOKEN: "TASTYTRADE_REFRESH_TOKEN",
    ACCOUNT_NUMBER: "TASTYTRADE_ACCOUNT_NUMBER",
}

# Keyring username prefix — kept for backward compatibility with existing entries.
_PREFIX = "production"

KEYRING_BACKEND_UNAVAILABLE = "KEYRING_BACKEND_UNAVAILABLE"
CREDENTIAL_BACKEND_ERROR = "CREDENTIAL_BACKEND_ERROR"
CREDENTIALS_MISSING = "CREDENTIALS_MISSING"


class CredentialError(RuntimeError):
    """Raised when a keyring operation fails due to missing backend or secret."""

    def __init__(self, message: str, *, code: str = CREDENTIAL_BACKEND_ERROR):
        super().__init__(message)
        self.code = code


def _entry(key: str) -> str:
    return f"{_PREFIX}:{key}"


def _environment_secret(key: str) -> str | None:
    name = _ENV_SECRET_NAMES.get(key)
    if name is None:
        return None
    value = os.getenv(name)
    return value if value else None


def _managed_environment_ready() -> bool:
    return all(_environment_secret(key) is not None for key in REQUIRED_SECRETS)


def _no_keyring_hint() -> str:
    return (
        "No keyring backend is available on this system.\n"
        "  Linux (headless / server / Docker):\n"
        "    pip install 'tastytrade-mcp[headless]'   # installs keyrings.alt\n"
        "    export PYTHON_KEYRING_BACKEND=keyrings.alt.file.EncryptedKeyring\n"
        "  Linux (desktop): ensure gnome-keyring or kwallet is running.\n"
        "  macOS / Windows: the native keyring should work; "
        "check that Python has keyring >= 24."
    )


def get_backend_name() -> str:
    """Return the name of the active keyring backend (for diagnostics)."""
    try:
        backend = keyring.get_keyring()
        return type(backend).__name__
    except Exception:  # noqa: BLE001
        return "unknown"


def _get_keyring_secret(key: str) -> str | None:
    try:
        return keyring.get_password(SERVICE_NAME, _entry(key))
    except keyring.errors.NoKeyringError as exc:
        raise CredentialError(
            _no_keyring_hint(),
            code=KEYRING_BACKEND_UNAVAILABLE,
        ) from exc
    except keyring.errors.KeyringError as exc:
        raise CredentialError(
            f"Keyring read failed: {exc}",
            code=CREDENTIAL_BACKEND_ERROR,
        ) from exc


def get_secret(key: str) -> str | None:
    """Fetch a managed environment secret, then fall back to the keyring."""
    environment_value = _environment_secret(key)
    if environment_value is not None:
        return environment_value
    return _get_keyring_secret(key)


def get_optional_secret(key: str) -> str | None:
    """Fetch an optional secret without making an unavailable keyring fatal."""
    environment_value = _environment_secret(key)
    if environment_value is not None:
        return environment_value
    if _managed_environment_ready():
        return None
    try:
        return _get_keyring_secret(key)
    except CredentialError as exc:
        logger.warning(
            "Optional credential %s is unavailable (%s); using runtime default.",
            key,
            exc.code,
        )
        return None


def set_secret(key: str, value: str) -> None:
    """Store a secret in the keyring."""
    try:
        keyring.set_password(SERVICE_NAME, _entry(key), value)
    except keyring.errors.NoKeyringError as exc:
        raise CredentialError(
            _no_keyring_hint(),
            code=KEYRING_BACKEND_UNAVAILABLE,
        ) from exc
    except keyring.errors.KeyringError as exc:
        raise CredentialError(
            f"Keyring write failed: {exc}",
            code=CREDENTIAL_BACKEND_ERROR,
        ) from exc


def delete_secret(key: str) -> bool:
    """Delete a secret. Returns True if it existed, False otherwise."""
    try:
        keyring.delete_password(SERVICE_NAME, _entry(key))
        return True
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.NoKeyringError as exc:
        raise CredentialError(
            _no_keyring_hint(),
            code=KEYRING_BACKEND_UNAVAILABLE,
        ) from exc
    except keyring.errors.KeyringError as exc:
        raise CredentialError(
            f"Keyring delete failed: {exc}",
            code=CREDENTIAL_BACKEND_ERROR,
        ) from exc


def credential_store_health() -> dict[str, Any]:
    """Report credential-source readiness without exposing secret values."""
    if _managed_environment_ready():
        return {
            "status": "ready",
            "broker_auth_usable": True,
            "required_credentials_present": True,
            "missing_required": [],
            "sources": {
                CLIENT_SECRET: "environment",
                REFRESH_TOKEN: "environment",
                ACCOUNT_NUMBER: (
                    "environment"
                    if _environment_secret(ACCOUNT_NUMBER) is not None
                    else "missing"
                ),
            },
            "credential_mode": "managed_environment",
            "keyring_backend": get_backend_name(),
            "keyring_available": None,
            "keyring_required": False,
        }

    sources: dict[str, str] = {}
    missing_required: list[str] = []
    keyring_available = True
    backend_error_code: str | None = None

    for key in ALL_SECRETS:
        if _environment_secret(key) is not None:
            sources[key] = "environment"
            continue
        try:
            value = _get_keyring_secret(key)
        except CredentialError as exc:
            sources[key] = "unavailable"
            keyring_available = False
            backend_error_code = backend_error_code or exc.code
            if key in REQUIRED_SECRETS:
                missing_required.append(key)
            continue

        if value:
            sources[key] = "keyring"
        else:
            sources[key] = "missing"
            if key in REQUIRED_SECRETS:
                missing_required.append(key)

    result: dict[str, Any] = {
        "status": "ready",
        "broker_auth_usable": not missing_required,
        "required_credentials_present": not missing_required,
        "missing_required": missing_required,
        "sources": sources,
        "credential_mode": "keyring_or_mixed",
        "keyring_backend": get_backend_name(),
        "keyring_available": keyring_available,
        "keyring_required": True,
    }

    if missing_required:
        result["status"] = "unusable"
        result["error_code"] = (
            backend_error_code or CREDENTIALS_MISSING
        )
    elif sources.get(ACCOUNT_NUMBER) == "unavailable":
        result["status"] = "degraded"
        result["warning_code"] = (
            backend_error_code or CREDENTIAL_BACKEND_ERROR
        )

    return result


def secrets_present() -> bool:
    """True when all required secrets for an OAuth session are present."""
    return bool(credential_store_health()["required_credentials_present"])


def missing_secrets() -> list[str]:
    """Return the list of required secrets that are not yet stored."""
    return list(credential_store_health()["missing_required"])
