import keyring.errors
import pytest

from tastytrade_mcp import credentials
from tastytrade_mcp.credentials import CredentialError


def test_set_get_roundtrip():
    credentials.set_secret(credentials.CLIENT_SECRET, "abc")
    assert credentials.get_secret(credentials.CLIENT_SECRET) == "abc"


def test_managed_environment_secret_takes_precedence(monkeypatch):
    credentials.set_secret(credentials.CLIENT_SECRET, "keyring-value")
    monkeypatch.setenv("TASTYTRADE_CLIENT_SECRET", "managed-value")
    assert credentials.get_secret(credentials.CLIENT_SECRET) == "managed-value"


def test_empty_environment_secret_falls_back_to_keyring(monkeypatch):
    credentials.set_secret(credentials.REFRESH_TOKEN, "keyring-value")
    monkeypatch.setenv("TASTYTRADE_REFRESH_TOKEN", "")
    assert credentials.get_secret(credentials.REFRESH_TOKEN) == "keyring-value"


def test_secrets_present_and_missing():
    assert not credentials.secrets_present()
    assert set(credentials.missing_secrets()) == {
        credentials.CLIENT_SECRET,
        credentials.REFRESH_TOKEN,
    }
    credentials.set_secret(credentials.CLIENT_SECRET, "a")
    credentials.set_secret(credentials.REFRESH_TOKEN, "b")
    assert credentials.secrets_present()


def test_delete_secret():
    credentials.set_secret(credentials.CLIENT_SECRET, "x")
    assert credentials.delete_secret(credentials.CLIENT_SECRET)
    assert not credentials.delete_secret(credentials.CLIENT_SECRET)


def test_get_backend_name_returns_string():
    name = credentials.get_backend_name()
    assert isinstance(name, str) and name


def test_no_keyring_raises_credential_error(monkeypatch):
    def _raise(*a, **kw):
        raise keyring.errors.NoKeyringError

    monkeypatch.setattr(keyring, "get_password", _raise)
    with pytest.raises(CredentialError, match="No keyring backend"):
        credentials.get_secret(credentials.CLIENT_SECRET)


def test_keyring_error_raises_credential_error(monkeypatch):
    def _raise(*a, **kw):
        raise keyring.errors.KeyringError("backend exploded")

    monkeypatch.setattr(keyring, "set_password", _raise)
    with pytest.raises(CredentialError, match="Keyring write failed"):
        credentials.set_secret(credentials.CLIENT_SECRET, "x")


def test_unavailable_keyring_has_machine_readable_error_code(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise keyring.errors.NoKeyringError

    monkeypatch.setattr(keyring, "get_password", _raise)
    with pytest.raises(CredentialError) as exc_info:
        credentials.get_secret(credentials.CLIENT_SECRET)
    assert exc_info.value.code == "KEYRING_BACKEND_UNAVAILABLE"


def test_optional_secret_does_not_block_managed_container(monkeypatch):
    monkeypatch.setenv("TASTYTRADE_CLIENT_SECRET", "managed-client-secret")
    monkeypatch.setenv("TASTYTRADE_REFRESH_TOKEN", "managed-refresh-token")

    def _raise(*_args, **_kwargs):
        raise keyring.errors.NoKeyringError

    monkeypatch.setattr(keyring, "get_password", _raise)
    assert credentials.get_optional_secret(credentials.ACCOUNT_NUMBER) is None


def test_store_health_is_ready_with_environment_secrets(
    monkeypatch,
):
    monkeypatch.setenv("TASTYTRADE_CLIENT_SECRET", "managed-client-secret")
    monkeypatch.setenv("TASTYTRADE_REFRESH_TOKEN", "managed-refresh-token")

    def _raise(*_args, **_kwargs):
        raise keyring.errors.NoKeyringError

    monkeypatch.setattr(keyring, "get_password", _raise)
    health = credentials.credential_store_health()

    assert health["status"] == "ready"
    assert health["broker_auth_usable"] is True
    assert health["required_credentials_present"] is True
    assert health["credential_mode"] == "managed_environment"
    assert health["keyring_required"] is False
    assert health["keyring_available"] is None
    assert health["sources"] == {
        credentials.CLIENT_SECRET: "environment",
        credentials.REFRESH_TOKEN: "environment",
        credentials.ACCOUNT_NUMBER: "missing",
    }


def test_store_health_is_degraded_when_active_keyring_loses_optional_secret(
    monkeypatch,
):
    def _get_password(_service, username):
        if username.endswith(credentials.CLIENT_SECRET):
            return "keyring-client-secret"
        if username.endswith(credentials.REFRESH_TOKEN):
            return "keyring-refresh-token"
        raise keyring.errors.NoKeyringError

    monkeypatch.setattr(keyring, "get_password", _get_password)
    health = credentials.credential_store_health()

    assert health["status"] == "degraded"
    assert health["broker_auth_usable"] is True
    assert health["warning_code"] == "KEYRING_BACKEND_UNAVAILABLE"
    assert health["keyring_required"] is True


def test_store_health_is_unusable_when_required_secret_needs_keyring(
    monkeypatch,
):
    monkeypatch.setenv("TASTYTRADE_CLIENT_SECRET", "managed-client-secret")

    def _raise(*_args, **_kwargs):
        raise keyring.errors.NoKeyringError

    monkeypatch.setattr(keyring, "get_password", _raise)
    health = credentials.credential_store_health()

    assert health["status"] == "unusable"
    assert health["broker_auth_usable"] is False
    assert health["required_credentials_present"] is False
    assert health["error_code"] == "KEYRING_BACKEND_UNAVAILABLE"
    assert health["keyring_required"] is True
    assert health["missing_required"] == [credentials.REFRESH_TOKEN]
