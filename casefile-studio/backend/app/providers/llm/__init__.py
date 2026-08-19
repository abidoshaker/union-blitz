"""LLM adapters used for scene grouping and image prompts.

Note what these are *not* asked to do: they never re-emit the narration text.
They are given numbered sentences and return groupings plus image prompts, so
the script that reaches TTS is assembled from the author's own sentences and
cannot be paraphrased. See services/segmentation.py.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

import httpx

from ...keystore import get_key


class LLMUnavailable(RuntimeError):
    pass


class LLMProvider:
    name = "base"
    label = "Base"
    is_local = False

    def available(self) -> tuple[bool, str]:
        return True, ""

    def complete_json(self, system: str, user: str, *, max_tokens: int = 4000) -> Any:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        usable, reason = self.available()
        return {"name": self.name, "label": self.label, "available": usable, "unavailable_reason": reason}


def _extract_json(text: str) -> Any:
    """Models sometimes wrap JSON in prose or a fence. Dig it out."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMUnavailable("The model did not return usable JSON.")


class ClaudeProvider(LLMProvider):
    name = "anthropic"
    label = "Anthropic Claude"

    def __init__(self, model: str = "claude-sonnet-5") -> None:
        self.model = model

    def available(self) -> tuple[bool, str]:
        return (True, "") if get_key("anthropic") else (False, "No Anthropic API key yet.")

    def complete_json(self, system: str, user: str, *, max_tokens: int = 4000) -> Any:
        key = get_key("anthropic")
        if not key:
            raise LLMUnavailable("No Anthropic API key.")
        with httpx.Client(timeout=180) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            if resp.status_code >= 400:
                raise LLMUnavailable(f"Anthropic: {resp.status_code} {resp.text[:200]}")
            blocks = resp.json().get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return _extract_json(text)


class OpenAIProvider(LLMProvider):
    name = "openai"
    label = "OpenAI"

    def __init__(self, model: str = "gpt-4.1-mini") -> None:
        self.model = model

    def available(self) -> tuple[bool, str]:
        return (True, "") if get_key("openai") else (False, "No OpenAI API key yet.")

    def complete_json(self, system: str, user: str, *, max_tokens: int = 4000) -> Any:
        key = get_key("openai")
        if not key:
            raise LLMUnavailable("No OpenAI API key.")
        with httpx.Client(timeout=180) as client:
            resp = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            if resp.status_code >= 400:
                raise LLMUnavailable(f"OpenAI: {resp.status_code} {resp.text[:200]}")
            content = resp.json()["choices"][0]["message"]["content"]
        return _extract_json(content)


class HeuristicProvider(LLMProvider):
    """No API key, no network. Groups by word count and builds prompts from
    the scene's own nouns. Always available, so segmentation never hard-fails."""

    name = "heuristic"
    label = "Built-in splitter (no key needed)"
    is_local = True

    def available(self) -> tuple[bool, str]:
        return True, ""

    def complete_json(self, system: str, user: str, *, max_tokens: int = 4000) -> Any:
        raise LLMUnavailable("The built-in splitter runs in segmentation.py, not through the LLM path.")


@lru_cache(maxsize=1)
def _registry() -> dict[str, LLMProvider]:
    return {p.name: p for p in (ClaudeProvider(), OpenAIProvider(), HeuristicProvider())}


def get_provider(name: str | None) -> LLMProvider:
    provider = _registry().get((name or "anthropic").lower())
    if provider is None:
        raise LLMUnavailable(f"Unknown LLM provider {name!r}")
    return provider


def best_available() -> LLMProvider:
    for name in ("anthropic", "openai"):
        provider = _registry()[name]
        if provider.available()[0]:
            return provider
    return _registry()["heuristic"]


def all_providers() -> list[LLMProvider]:
    return list(_registry().values())


def describe_all() -> list[dict]:
    return [p.describe() for p in all_providers()]
