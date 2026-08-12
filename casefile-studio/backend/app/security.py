"""Encrypted API-key storage.

The Fernet key lives in the OS keyring (Windows Credential Manager) when one is
available, and falls back to a 0600 key file. Keys are never logged and never
returned to the browser in full.
"""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_SERVICE = "casefile-studio"
_USER = "master-key"
_cached: Fernet | None = None


def _keyfile() -> Path:
    return settings.data_dir / ".master.key"


def _load_from_keyring() -> str | None:
    try:
        import keyring
    except Exception:
        return None
    try:
        return keyring.get_password(_SERVICE, _USER)
    except Exception:
        return None


def _store_in_keyring(key: str) -> bool:
    try:
        import keyring

        keyring.set_password(_SERVICE, _USER, key)
        return True
    except Exception:
        return False


def _load_from_file() -> str | None:
    path = _keyfile()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return None


def _store_in_file(key: str) -> None:
    path = _keyfile()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # Windows without POSIX perms; the file is inside the user profile


def _fernet() -> Fernet:
    global _cached
    if _cached is not None:
        return _cached

    key = _load_from_keyring() or _load_from_file()
    if not key:
        key = Fernet.generate_key().decode()
        if not _store_in_keyring(key):
            _store_in_file(key)
    _cached = Fernet(key.encode())
    return _cached


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return None


def redact(value: str | None) -> str:
    """What the UI is allowed to see: last 4 characters only."""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * max(4, len(value) - 4) + value[-4:]


def fingerprint(value: str) -> str:
    import hashlib

    return base64.b16encode(hashlib.sha256(value.encode()).digest()[:4]).decode().lower()
