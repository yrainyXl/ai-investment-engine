"""Small read-only HTTP client using verified OTW API paths.

This module deliberately has no POST/PUT/DELETE functions.
"""
from __future__ import annotations
import json
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class OTWError(RuntimeError):
    pass


class OTWReadClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        if not base_url:
            raise OTWError("OTW_BASE_URL is required for live mode")
        parts = urlsplit(base_url)
        if parts.scheme not in ("https", "http") or not parts.netloc:
            raise OTWError("OTW_BASE_URL must be an http(s) URL")
        if parts.scheme != "https" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise OTWError("Refusing to transmit OTW token over non-local HTTP")
        if not token:
            raise OTWError("OTW_API_TOKEN is required for live mode")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def get(self, path: str) -> dict[str, Any]:
        if not path.startswith("/api/") or "?" in path or "#" in path or ".." in path:
            raise OTWError("Invalid OTW API path")
        request = Request(
            self.base_url + path,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as resp:
                content = json.loads(resp.read().decode("utf-8"))
                if not isinstance(content, dict):
                    raise OTWError("Unexpected response: expected a JSON object")
                return content
        except (HTTPError, URLError, ValueError) as exc:
            raise OTWError(f"OTW GET failed for {path}: {exc}") from exc

    def strategy_context(self, strategy_id: str) -> dict[str, Any]:
        _validate_uuid(strategy_id)
        return self.get(f"/api/paper-trading/strategies/{strategy_id}/context")

    def asset_thesis(self, strategy_id: str, asset_id: str) -> dict[str, Any] | None:
        _validate_uuid(strategy_id)
        _validate_uuid(asset_id)
        try:
            return self.get(f"/api/paper-trading/strategies/{strategy_id}/documents/{asset_id}")
        except OTWError as exc:
            if "HTTP Error 404" in str(exc):
                return None
            raise


def _validate_uuid(value: str) -> None:
    import uuid
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value.lower():
            raise ValueError("not canonical")
    except (ValueError, TypeError, AttributeError) as exc:
        raise OTWError("Expected a canonical UUID strategy/asset ID") from exc
