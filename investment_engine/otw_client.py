"""Read-only OTW client supporting REST and the deployed MCP transport."""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class OTWError(RuntimeError):
    pass


class OTWReadClient:
    def __init__(self, base_url: str, token: str, timeout: float = 20.0, transport: str = "rest"):
        if not base_url:
            raise OTWError("OTW_BASE_URL is required for live mode")
        parts = urlsplit(base_url)
        if parts.scheme not in ("https", "http") or not parts.netloc:
            raise OTWError("OTW_BASE_URL must be an http(s) URL")
        if parts.scheme != "https" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise OTWError("Refusing to transmit OTW token over non-local HTTP")
        if not token:
            raise OTWError("OTW_API_TOKEN is required for live mode")
        if transport not in ("rest", "mcp"):
            raise OTWError("OTW_TRANSPORT must be 'rest' or 'mcp'")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.transport = transport

    def _request(self, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urlopen(request, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = f"HTTP {exc.code}"
            try:
                body = exc.read(512).decode("utf-8", errors="replace")
                if body:
                    detail += f": {body}"
            except OSError:
                pass
            raise OTWError(detail) from exc
        except (URLError, ValueError) as exc:
            raise OTWError(f"OTW request failed: {type(exc).__name__}") from exc
        if not isinstance(result, dict):
            raise OTWError("OTW returned a non-object response")
        return result

    def get(self, path: str) -> dict[str, Any]:
        if not path.startswith("/api/") or "?" in path or "#" in path or ".." in path:
            raise OTWError("Invalid OTW API path")
        if self.transport == "rest":
            result = self._request(self.base_url + path)
        else:
            # The deployed OTW MCP exposes only the read tool to this service token.
            rpc = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "otw_read", "arguments": {"path": path}},
            }
            envelope = self._request(self.base_url + "/api/mcp", rpc)
            if envelope.get("error"):
                error_value = envelope.get("error")
                message = str(error_value.get("message", "")) if isinstance(error_value, dict) else str(error_value)
                raise OTWError("HTTP 404: resource not found" if "404" in message else "OTW MCP returned a JSON-RPC error")
            result_block = envelope.get("result")
            if not isinstance(result_block, dict):
                raise OTWError("OTW MCP read tool failed")
            if result_block.get("isError") is True:
                blocks = result_block.get("content", [])
                error_text = str(blocks[0].get("text", "")) if blocks and isinstance(blocks[0], dict) else ""
                raise OTWError("HTTP 404: resource not found" if "404" in error_text else "OTW MCP read tool failed")
            content = result_block.get("content")
            if not isinstance(content, list) or not content or not isinstance(content[0], dict):
                raise OTWError("OTW MCP returned no text content")
            raw_text = content[0].get("text")
            if not isinstance(raw_text, str):
                raise OTWError("OTW MCP returned an invalid text content block")
            try:
                result = json.loads(raw_text)
            except ValueError as exc:
                raise OTWError("OTW MCP read result was not JSON") from exc
            if not isinstance(result, dict):
                raise OTWError("OTW MCP read result was not an object")
        return result

    def list_strategies(self) -> list[dict[str, Any]]:
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
            if "HTTP 404" in str(exc) or "HTTP Error 404" in str(exc):
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
