from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Protocol


class CredentialStore(Protocol):
    @property
    def available(self) -> bool: ...

    def load(self) -> str: ...

    def save(self, secret: str) -> None: ...

    def clear(self) -> None: ...


class UnavailableCredentialStore:
    """Fail-closed store for platforms without an implemented secure vault."""

    @property
    def available(self) -> bool:
        return False

    def load(self) -> str:
        return ""

    def save(self, secret: str) -> None:
        del secret
        raise RuntimeError("Secure API key storage is unavailable on this platform")

    def clear(self) -> None:
        return


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    # Keep the buffer alive for the duration of the native call.
    buffer = ctypes.create_string_buffer(value)
    return (_DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))),
            buffer)


class WindowsDpapiCredentialStore:
    """Current-user encrypted credential file backed by Windows DPAPI.

    The ciphertext can only be decrypted by the same Windows user. WeavePath
    never writes the plaintext key to JSON, SQLite, logs, or API responses.
    """

    _ENTROPY = b"WeavePath/model-provider-api-key/v1"
    _MAGIC = b"WEAVEPATH-DPAPI-V1\0"
    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @property
    def available(self) -> bool:
        return os.name == "nt"

    def _protect(self, value: bytes) -> bytes:
        if not self.available:
            raise RuntimeError("Windows DPAPI is unavailable")
        source, source_buffer = _blob(value)
        entropy, entropy_buffer = _blob(self._ENTROPY)
        result = _DataBlob()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source), "WeavePath", ctypes.byref(entropy), None, None,
            self._CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result),
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(result.pbData)

    def _unprotect(self, value: bytes) -> bytes:
        if not self.available:
            raise RuntimeError("Windows DPAPI is unavailable")
        source, source_buffer = _blob(value)
        entropy, entropy_buffer = _blob(self._ENTROPY)
        result = _DataBlob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, ctypes.byref(entropy), None, None,
            self._CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result),
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(result.pbData)

    def load(self) -> str:
        if not self.available or not self.path.exists():
            return ""
        value = self.path.read_bytes()
        if not value.startswith(self._MAGIC):
            raise ValueError("Stored API key has an unsupported format")
        return self._unprotect(value[len(self._MAGIC):]).decode("utf-8")

    def save(self, secret: str) -> None:
        if not secret:
            raise ValueError("API key must not be blank")
        encrypted = self._MAGIC + self._protect(secret.encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_bytes(encrypted)
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        temp.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def default_credential_store(settings_path: str | Path) -> CredentialStore:
    path = Path(settings_path).with_name("model-api-key.dpapi")
    if os.name == "nt":
        return WindowsDpapiCredentialStore(path)
    return UnavailableCredentialStore()
