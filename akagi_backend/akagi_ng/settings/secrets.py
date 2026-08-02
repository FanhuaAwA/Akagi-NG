from __future__ import annotations

import base64
import binascii
import ctypes
import json
import os
import tempfile
from pathlib import Path

import keyring as _keyring

from akagi_ng.core.paths import get_settings_dir

__all__ = ["delete_secret", "get_secret", "set_secret"]

_ENTROPY = b"Akagi-NG"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_KEYRING_SERVICE = "me.akagi.ng"


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


if os.name == "nt":
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = ctypes.c_int
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = ctypes.c_int
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
else:
    _crypt32 = None
    _kernel32 = None


def _protect(data: bytes) -> bytes:
    if _crypt32 is None or _kernel32 is None:
        raise OSError("Windows DPAPI is unavailable")

    input_buffer = ctypes.create_string_buffer(data)
    input_blob = _DataBlob(len(data), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy_buffer = ctypes.create_string_buffer(_ENTROPY)
    entropy_blob = _DataBlob(len(_ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()

    try:
        if not _crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        if output_blob.data:
            _kernel32.LocalFree(output_blob.data)


def _unprotect(data: bytes) -> bytes:
    if _crypt32 is None or _kernel32 is None:
        raise OSError("Windows DPAPI is unavailable")

    input_buffer = ctypes.create_string_buffer(data)
    input_blob = _DataBlob(len(data), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy_buffer = ctypes.create_string_buffer(_ENTROPY)
    entropy_blob = _DataBlob(len(_ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()

    try:
        if not _crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        if output_blob.data:
            _kernel32.LocalFree(output_blob.data)


def _read_storage(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Invalid secret storage") from None

    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in data.items()
    ):
        raise ValueError("Invalid secret storage")
    return data


def _write_storage(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(data, temporary_file, sort_keys=True, separators=(",", ":"))
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _keyring_get(name: str) -> str:
    try:
        value = _keyring.get_password(_KEYRING_SERVICE, name)
    except Exception:
        raise OSError("System credential storage is unavailable") from None
    if value is not None and not isinstance(value, str):
        raise OSError("System credential storage returned invalid data")
    return value or ""


def _keyring_set(name: str, value: str) -> None:
    try:
        _keyring.set_password(_KEYRING_SERVICE, name, value)
    except Exception:
        raise OSError("System credential storage is unavailable") from None


def _keyring_delete(name: str) -> None:
    try:
        if _keyring.get_password(_KEYRING_SERVICE, name) is not None:
            _keyring.delete_password(_KEYRING_SERVICE, name)
    except Exception:
        raise OSError("System credential storage is unavailable") from None


def get_secret(name: str, storage_path: Path | None = None) -> str:
    if _crypt32 is None or _kernel32 is None:
        return _keyring_get(name)

    path = storage_path if storage_path is not None else get_settings_dir() / "secrets.json"
    encoded = _read_storage(path).get(name)
    if encoded is None:
        return ""

    try:
        ciphertext = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Invalid encrypted secret") from None

    try:
        return _unprotect(ciphertext).decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Invalid encrypted secret") from None


def set_secret(name: str, value: str, storage_path: Path | None = None) -> None:
    if not value:
        delete_secret(name, storage_path)
        return

    if _crypt32 is None or _kernel32 is None:
        _keyring_set(name, value)
        return

    path = storage_path if storage_path is not None else get_settings_dir() / "secrets.json"
    data = _read_storage(path)
    data[name] = base64.b64encode(_protect(value.encode("utf-8"))).decode("ascii")
    _write_storage(path, data)


def delete_secret(name: str, storage_path: Path | None = None) -> None:
    if _crypt32 is None or _kernel32 is None:
        _keyring_delete(name)
        return

    path = storage_path if storage_path is not None else get_settings_dir() / "secrets.json"
    data = _read_storage(path)
    if name in data:
        del data[name]
        _write_storage(path, data)
