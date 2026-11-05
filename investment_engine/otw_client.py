"""Read-only OTW HTTP client using verified API paths.

No write methods are defined. Strategy discovery is dynamic; no global strategy
ID environment variable is required.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


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

    def list_strategies(self) -> list[dict[str, Any]]:
        """Discover paper strategies from OTW instead of deploying per-strategy env."""
        data = self.get("/api/paper-trading/strategies")
        strategies = data.get("strategies")
        if not isinstance(strategies, list):
            raise OTWError("OTW strategies response must contain a list")
        seen: set[str] = set()
        for item in strategies:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise OTWError("OTW returned a malformed strategy")
            _validate_uuid(item["id"])
            if item["id"] in seen:
                raise OTWError("OTW returned duplicate strategy IDs")
            seen.add(item["id"])
        return strategies

    def active_strategies(self) -> list[dict[str, Any]]:
        return [s for s in self.list_strategies() if s.get("status") == "active"]

    def resolve_strategy_id(self, strategy_id: str | None = None) -> str:
        """Explicit ID wins; otherwise select only when exactly one active exists.

        Do not silently choose the first of multiple active portfolios.
        For batch preview, use list_strategies()/active_strategies() explicitly.
        """
        if strategy_id:
            _validate_uuid(strategy_id)
            return strategy_id
        active = self.active_strategies()
        if len(active) == 1:
            return active[0]["id"]
        if not active:
            raise OTWError("No active paper strategy found; inspect GET /v1/strategies")
        raise OTWError("Multiple active paper strategies; supply strategy_id or use --all-active")

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
