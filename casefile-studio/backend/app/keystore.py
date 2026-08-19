"""Reading decrypted API keys. The only place that touches Secret rows."""

from __future__ import annotations

import os

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from .db import session_scope
from .models import Secret
from .security import decrypt, encrypt

# Environment fallbacks, so power users can use .env instead of the UI.
ENV_KEYS = {
    "fish": "FISH_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "pexels": "PEXELS_API_KEY",
    "pixabay": "PIXABAY_API_KEY",
    "unsplash": "UNSPLASH_ACCESS_KEY",
    "fal": "FAL_KEY",
    "nara": "NARA_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
}


def get_key(provider: str) -> str | None:
    # Providers call this from available(), which the UI hits on every settings
    # load - including before the database has been created. A missing table
    # must read as "no key", not as an exception out of a status check.
    try:
        with session_scope() as s:
            row = s.get(Secret, provider)
            if row and row.value_encrypted:
                value = decrypt(row.value_encrypted)
                if value:
                    return value
    except SQLAlchemyError:
        pass
    env_name = ENV_KEYS.get(provider)
    if env_name:
        return os.environ.get(env_name) or None
    return None


def set_key(provider: str, value: str) -> None:
    with session_scope() as s:
        row = s.get(Secret, provider)
        if row is None:
            row = Secret(provider=provider)
        row.value_encrypted = encrypt(value) if value else ""
        s.add(row)


def delete_key(provider: str) -> None:
    with session_scope() as s:
        row = s.get(Secret, provider)
        if row:
            s.delete(row)


def configured_providers() -> set[str]:
    stored: set[str] = set()
    try:
        with session_scope() as s:
            rows = s.exec(select(Secret)).all()
            stored = {r.provider for r in rows if r.value_encrypted}
    except SQLAlchemyError:
        pass
    stored |= {p for p, env in ENV_KEYS.items() if os.environ.get(env)}
    return stored
