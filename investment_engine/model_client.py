"""Bounded chat-completions transport for server-configured compatible providers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from http.client import HTTPException
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .agent_runtime import ModelRequest, ModelResponse, RolePolicy


class ModelTransportError(RuntimeError):
    """Safe transport failure. Provider response bodies are never included."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def chat_completions_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(character.isspace() or ord(character) < 32 for character in base_url)):
        raise ValueError("model base URL must be HTTPS without credentials, query or fragment")
    return base_url.rstrip("/") + "/chat/completions"


@dataclass
class OpenAICompatibleClient:
    endpoint: str
    api_key: str = field(repr=False)
    policies: dict[str, RolePolicy] = field(repr=False)
    max_cost: Decimal
    max_input_tokens: int
    max_output_tokens: int
    reserved_cost: Decimal = field(default=Decimal(0), init=False)
    reserved_input_tokens: int = field(default=0, init=False)
    reserved_output_tokens: int = field(default=0, init=False)
    skipped_for_budget: int = field(default=0, init=False)
    _opener: object = field(default_factory=lambda: build_opener(_RejectRedirects()), repr=False)

    def complete(self, request: ModelRequest) -> ModelResponse:
        policy = self.policies.get(request.role)
        if policy is None or request.model != policy.model:
            raise ModelTransportError("model is outside the server role policy")

        messages = [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]
        body = json.dumps({
            "model": policy.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": policy.max_output_tokens,
            "stream": False,
        }, ensure_ascii=False).encode("utf-8")

        # A byte-per-token estimate plus a margin for the provider's chat wrapper.
        # Hold the reservation even after a timeout or a malformed response.
        input_tokens = len(body) + 256
        output_tokens = policy.max_output_tokens
        cost = (
            Decimal(policy.input_cost_per_million) * input_tokens
            + Decimal(policy.output_cost_per_million) * output_tokens
        ) / 1_000_000
        if (input_tokens > policy.max_input_tokens
                or self.reserved_input_tokens + input_tokens > self.max_input_tokens
                or self.reserved_output_tokens + output_tokens > self.max_output_tokens
                or self.reserved_cost + cost > self.max_cost):
            self.skipped_for_budget += 1
            raise ModelTransportError("model call exceeds the configured preflight budget")
        self.reserved_input_tokens += input_tokens
        self.reserved_output_tokens += output_tokens
        self.reserved_cost += cost

        outbound = Request(self.endpoint, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        # Chat-completion envelopes may include additional provider metadata.
        limit = 2 * policy.max_response_bytes + 65_536
        try:
            with self._opener.open(outbound, timeout=policy.timeout_seconds) as response:
                if response.status != 200:
                    raise ModelTransportError("model provider returned an unexpected HTTP status")
                payload = response.read(limit + 1)
        except TimeoutError:
            raise TimeoutError("model provider timed out") from None
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError("model provider timed out") from None
            raise ModelTransportError("model provider request failed") from None
        except (OSError, HTTPException):
            raise ModelTransportError("model provider transport failed") from None

        if len(payload) > limit:
            raise ModelTransportError("model provider response exceeded the size limit")
        try:
            data = json.loads(payload)
            choice, = data["choices"]
            content = choice["message"]["content"]
            model = data["model"]
            version = data.get("model_version") or model
            usage = data.get("usage") or {}
            finish_reason = choice["finish_reason"]
            if (model != policy.model or not isinstance(version, str)
                    or (policy.model_version is not None and version != policy.model_version)
                    or not isinstance(content, str) or not isinstance(usage, dict)
                    or finish_reason != "stop"):
                raise ValueError
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ModelTransportError("model provider returned an invalid completion") from None
        return ModelResponse(content, model, version, usage=usage, finish_reason=finish_reason)
