"""Auth helpers.

Currently supports optional API key authentication via header `X-Api-Key`.
"""

from __future__ import annotations

from fastapi import Header, HTTPException

from .settings import Settings


def require_api_key(settings: Settings, x_api_key: str | None = Header(default=None)) -> None:
    """Optional API key auth.

    If API_KEY is set in env, requests must provide header: X-Api-Key.
    """

    if not settings.api_key:
        return
    if not x_api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid api key")
