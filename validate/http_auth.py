#!/usr/bin/env python3
"""Shared auth handling for OpenAI-compatible validation clients."""

from __future__ import annotations

import os


def resolve_api_key(explicit: str | None = None) -> str | None:
    """Resolve an explicit key, then the same environment used by launchers."""
    return explicit or os.environ.get("VLLM_API_KEY") or os.environ.get("API_KEY")


def api_headers(
    explicit: str | None = None,
    *,
    content_type: bool = False,
) -> dict[str, str]:
    """Build request headers without logging or otherwise exposing the key."""
    headers: dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = "application/json"
    key = resolve_api_key(explicit)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers
