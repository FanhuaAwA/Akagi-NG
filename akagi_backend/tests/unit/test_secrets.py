import base64
import hmac
import json
import os
from unittest.mock import MagicMock

import pytest

from akagi_ng.settings import secrets

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI is required")

_FIRST_SECRET = "unit-test-secret-first"
_SECOND_SECRET = "unit-test-secret-second"


def _assert_secret(actual: str, expected: str) -> None:
    if not hmac.compare_digest(actual, expected):
        pytest.fail("secret mismatch", pytrace=False)


@windows_only
def test_round_trip_uses_default_storage_and_hides_plaintext(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "get_settings_dir", lambda: tmp_path)

    secrets.set_secret("api_key", _FIRST_SECRET)

    storage_path = tmp_path / "secrets.json"
    _assert_secret(secrets.get_secret("api_key"), _FIRST_SECRET)
    contents = storage_path.read_text(encoding="utf-8")
    if _FIRST_SECRET in contents:
        pytest.fail("secret was stored in plaintext", pytrace=False)
    stored = json.loads(contents)
    assert set(stored) == {"api_key"}
    base64.b64decode(stored["api_key"], validate=True)


@windows_only
def test_update_delete_and_empty_value(tmp_path):
    storage_path = tmp_path / "nested" / "secrets.json"
    secrets.set_secret("api_key", _FIRST_SECRET, storage_path)
    secrets.set_secret("api_key", _SECOND_SECRET, storage_path)
    _assert_secret(secrets.get_secret("api_key", storage_path), _SECOND_SECRET)

    secrets.delete_secret("api_key", storage_path)
    _assert_secret(secrets.get_secret("api_key", storage_path), "")

    secrets.set_secret("api_key", _FIRST_SECRET, storage_path)
    secrets.set_secret("api_key", "", storage_path)
    _assert_secret(secrets.get_secret("api_key", storage_path), "")


@windows_only
def test_corrupted_ciphertext_raises_windows_error(tmp_path):
    storage_path = tmp_path / "secrets.json"
    secrets.set_secret("api_key", _FIRST_SECRET, storage_path)
    stored = json.loads(storage_path.read_text(encoding="utf-8"))
    ciphertext = bytearray(base64.b64decode(stored["api_key"], validate=True))
    ciphertext[-1] ^= 1
    stored["api_key"] = base64.b64encode(ciphertext).decode("ascii")
    storage_path.write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(OSError):
        secrets.get_secret("api_key", storage_path)


def test_non_windows_uses_system_keyring(monkeypatch):
    stored = {}
    backend = MagicMock()
    backend.get_password.side_effect = lambda _service, name: stored.get(name)
    backend.set_password.side_effect = lambda _service, name, value: stored.__setitem__(name, value)
    backend.delete_password.side_effect = lambda _service, name: stored.pop(name)
    monkeypatch.setattr(secrets, "_crypt32", None)
    monkeypatch.setattr(secrets, "_kernel32", None)
    monkeypatch.setattr(secrets, "_keyring", backend)

    secrets.set_secret("api_key", _FIRST_SECRET)
    _assert_secret(secrets.get_secret("api_key"), _FIRST_SECRET)
    secrets.delete_secret("api_key")
    _assert_secret(secrets.get_secret("api_key"), "")


def test_non_windows_keyring_failure_is_safe(monkeypatch):
    backend = MagicMock()
    backend.get_password.side_effect = RuntimeError("backend details")
    monkeypatch.setattr(secrets, "_crypt32", None)
    monkeypatch.setattr(secrets, "_kernel32", None)
    monkeypatch.setattr(secrets, "_keyring", backend)

    with pytest.raises(OSError, match="System credential storage is unavailable") as caught:
        secrets.get_secret("api_key")

    assert "backend details" not in str(caught.value)
