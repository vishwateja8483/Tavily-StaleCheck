"""Opt-in client for a stock Bifrost gateway: its OpenAI-compatible chat endpoint and its relay for Tavily Search.

No environment or file credential lookup, redirects, environment proxies, tool calls or
client-side retries: Bifrost owns retries, budgets and failover. The token is an explicit
argument that is never retained or echoed. The shapes follow the Bifrost documentation
reviewed on 2026-11-01 and were exercised against a real Bifrost 2.4.0 container with local
stand-in upstreams (scripts/gateway-check); no live model or Tavily provider has been called.
"""
import http.client
import json
import math
import re
import time
from urllib.parse import urlsplit

from .acquisition import Response
from .contracts import ValidationError, decode, json_bytes, obj, require, sha

CHAT_PATH = "/v1/chat/completions"
MAX_REQUEST_BYTES = 131_072
MAX_RESPONSE_BYTES = 262_144
MAX_ERROR_BYTES = 8_192
MAX_REPLY_CHARS = 20_000
MAX_FALLBACKS = 3
MAX_TOKENS = 4_096
TIMEOUT = 60.0
MAX_TIMEOUT = 180.0
MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
LABEL = re.compile(r"^[A-Za-z0-9._:/@+-]{1,128}$")
RELAY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# Refusal types documented by Bifrost or seen from a real 2.4.0 gateway; any other type is reported without provider text.
REFUSALS = {"budget_exceeded", "rate_limited", "token_limited", "request_limited", "model_blocked",
            "provider_blocked", "access_blocked", "virtual_key_required", "access_not_found", "relay_blocked"}


class GatewayError(ValidationError):
    """Sanitized: only an HTTP status and a documented refusal type, never provider text."""

    def __init__(self, message, *, status=None, kind=None):
        super().__init__(message)
        self.status, self.kind = status, kind


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def gateway_origin(value):
    require(isinstance(value, str) and 0 < len(value) <= 200 and value.isascii() and value == value.strip(), "invalid gateway URL")
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError:
        raise ValidationError("invalid gateway port") from None
    host = (parts.hostname or "").lower()
    require(parts.scheme in ("http", "https") and host and parts.username is None and parts.password is None
            and not parts.query and not parts.fragment and parts.path in ("", "/"),
            "gateway URL must be an origin without credentials, path, query or fragment")
    remote_ok = parts.scheme == "https" and re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host) is not None and not host.split(".")[-1].isdigit()
    require(host in LOOPBACK or remote_ok, "plain HTTP is allowed only for a loopback gateway; remote gateways need an HTTPS hostname")
    return parts.scheme, host, port or (443 if parts.scheme == "https" else 80)


class StdlibGatewayTransport:
    """Direct connection to the configured origin; no redirects, proxies or credential reads."""
    is_mock = False

    def __call__(self, target, body, *, token, timeout, max_bytes):
        return self._post(target, CHAT_PATH, body, token=token, timeout=timeout, max_bytes=max_bytes)

    @staticmethod
    def _post(target, path, body, *, token, timeout, max_bytes):
        scheme, host, port = target
        connection = (http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection)(host, port, timeout=timeout)
        headers = {"Content-Type": "application/json", "Accept": "application/json", "Accept-Encoding": "identity"}
        if token:
            headers["x-bf-vk"] = token
        deadline = time.monotonic() + timeout
        try:
            connection.request("POST", path, body=body, headers=headers)
            response = connection.getresponse()
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise GatewayError("compressed gateway responses are unsupported")
            limit = max_bytes if response.status == 200 else MAX_ERROR_BYTES
            data = bytearray()
            while len(data) <= limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(16_384, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > limit:
                require(response.status != 200, "gateway response exceeds byte bound")
                del data[limit:]
            return Response(response.status, bytes(data))
        finally:
            connection.close()


class BifrostRelayTransport:
    """Tavily Search through a Bifrost relay (`/relay/NAME/search`), authorised by a Bifrost virtual key.

    Same call signature as acquisition.StdlibTransport, so it is a drop-in `search_transport` whose
    `api_key` is the virtual key. The Tavily key stays inside the gateway, the answer is Tavily's own
    JSON, and the spend lands on the virtual key's budgets. Like the direct transport, a non-200
    answer carries no body: a refusal (budget, missing relay grant) is reported by status alone.
    """
    is_mock = False

    def __init__(self, url, relay):
        self.target = gateway_origin(url)
        require(isinstance(relay, str) and RELAY.fullmatch(relay) is not None, "invalid relay name")
        self.prefix = "/relay/" + relay

    def __call__(self, path, body, *, api_key, timeout, max_bytes):
        require(path in ("/search", "/extract"), "unsupported acquisition endpoint")
        response = StdlibGatewayTransport._post(self.target, self.prefix + path, body, token=api_key, timeout=timeout, max_bytes=max_bytes)
        return response if response.status == 200 else Response(response.status, b"")


class MockGatewayTransport:
    """Ordered local fixtures, not a claim of gateway compatibility."""
    is_mock = True

    def __init__(self, fixtures):
        require(isinstance(fixtures, list) and len(fixtures) <= 32, "mock gateway responses must be a bounded array")
        self.fixtures, self.calls = list(fixtures), []

    def __call__(self, target, body, *, token, timeout, max_bytes):
        require(bool(self.fixtures), "mock gateway response queue exhausted")
        fixture = obj(self.fixtures.pop(0), "mock gateway response")
        require(set(fixture) == {"status", "body"} and type(fixture["status"]) is int, "invalid mock gateway response")
        self.calls.append({"body": decode(body), "token_supplied": token is not None})
        return Response(fixture["status"], json_bytes(fixture["body"]))


def _label(value):
    return value if isinstance(value, str) and LABEL.fullmatch(value) else None


def _count(value):
    return value if type(value) is int and 0 <= value <= 10**9 else None


def _refusal(raw):
    try:
        data = obj(decode(raw), "gateway error")
        kind = data.get("type")
        if kind is None and isinstance(data.get("error"), dict):
            kind = data["error"].get("type")
    except (ValueError, RecursionError):
        return None
    return kind if isinstance(kind, str) and kind in REFUSALS else None


def _served_by(data):
    extra = data.get("extra_fields")
    info = extra.get("routing_info") if isinstance(extra, dict) else None
    if isinstance(info, dict):
        provider, model = _label(info.get("provider")), _label(info.get("model"))
        fallback = info.get("is_fallback")
        # Bifrost omits the false value on a primary-served response (omitempty).
        if "is_fallback" not in info and (provider or model) and not any(k in info for k in ("primary_provider", "primary_model")):
            fallback = False
        return {"provider": provider, "model": model,
                "is_fallback": fallback if type(fallback) is bool else None}
    model = _label(data.get("model"))
    return None if model is None else {"provider": None, "model": model, "is_fallback": None}


class GatewayClient:
    def __init__(self, url, model, *, fallbacks=(), token=None, transport=None, allow_network=False, max_tokens=1024, timeout=TIMEOUT):
        self.target = gateway_origin(url)
        require(isinstance(model, str) and MODEL.fullmatch(model) is not None, "invalid model name")
        fallbacks = list(fallbacks)
        require(len(fallbacks) <= MAX_FALLBACKS and all(isinstance(item, str) and MODEL.fullmatch(item) for item in fallbacks)
                and len({model, *fallbacks}) == len(fallbacks) + 1, "invalid fallback models")
        require(type(max_tokens) is int and 1 <= max_tokens <= MAX_TOKENS, "invalid token ceiling")
        # A slow local model may need more than the default; the journey deadline still caps every call.
        require(type(timeout) in (int, float) and math.isfinite(timeout) and 1 <= timeout <= MAX_TIMEOUT, "invalid gateway timeout")
        self.transport = transport if transport is not None else StdlibGatewayTransport()
        require(isinstance(self.transport, MockGatewayTransport) or allow_network is True, "gateway access requires explicit network opt-in")
        require(token is None or (isinstance(token, str) and 0 < len(token) <= 512 and all(33 <= ord(c) <= 126 for c in token)), "invalid gateway token")
        self.model, self.fallbacks, self.token, self.max_tokens, self.timeout = model, fallbacks, token, max_tokens, float(timeout)

    def chat(self, messages, *, max_tokens=None, budget=None):
        """One bounded request; returns a reply record or raises a sanitized GatewayError."""
        require(isinstance(messages, list) and 1 <= len(messages) <= 4 and all(
            isinstance(m, dict) and set(m) == {"role", "content"} and m["role"] in ("system", "user")
            and isinstance(m["content"], str) and 0 < len(m["content"]) <= 40_000 for m in messages), "invalid gateway messages")
        ceiling = self.max_tokens if max_tokens is None else max_tokens
        require(type(ceiling) is int and 1 <= ceiling <= MAX_TOKENS, "invalid token ceiling")
        payload = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": ceiling, "stream": False}
        if self.fallbacks:
            payload["fallbacks"] = self.fallbacks
        body = canonical(payload)
        require(len(body) <= MAX_REQUEST_BYTES, "gateway request exceeds byte bound")
        started = time.monotonic()
        attempt, timeout = budget.begin("gateway", self.timeout) if budget is not None else (None, self.timeout)
        usage, routing, status = None, None, "failed"
        try:
            try:
                response = self.transport(self.target, body, token=self.token, timeout=timeout, max_bytes=MAX_RESPONSE_BYTES)
            except ValidationError as exc:
                if type(self.transport) is MockGatewayTransport:
                    raise  # Built-in mock transport emits only static contract messages.
                if type(self.transport) is StdlibGatewayTransport:
                    raise GatewayError(str(exc)) from None
                raise GatewayError("gateway transport failed; not retried; usage unknown") from None
            except Exception:
                raise GatewayError("gateway transport failed; not retried; usage unknown") from None
            require(isinstance(response, Response) and type(response.status) is int and isinstance(response.body, bytes), "invalid gateway transport response")
            limit = MAX_RESPONSE_BYTES if response.status == 200 else MAX_ERROR_BYTES
            if response.status == 200 and len(response.body) > limit:
                raise GatewayError("gateway response exceeds byte bound")
            raw = response.body[:limit]
            # Capture billing before validating the reply contract, even on refusal.
            try:
                data = obj(decode(raw), "gateway response")
            except (ValueError, TypeError, RecursionError, OverflowError):
                data = None
            if data is not None:
                reported = data.get("usage")
                if isinstance(reported, dict):
                    usage = {key: _count(reported.get(key)) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
                routing = _served_by(data)
                if routing and self.token:
                    routing = {key: None if isinstance(value, str) and self.token in value else value for key, value in routing.items()}
            if budget is not None:
                try:
                    budget.check()
                except ValidationError:
                    raise GatewayError("gateway journey time budget exhausted") from None
            if response.status != 200:
                kind = _refusal(raw)
                raise GatewayError(f"gateway HTTP {response.status}" + (f" ({kind})" if kind else ""), status=response.status, kind=kind)
            try:
                require(data is not None, "invalid gateway JSON")
                choices = data.get("choices")
                require(isinstance(choices, list) and choices, "gateway response has no choices")
                choice = obj(choices[0], "choice")
                content = obj(choice.get("message"), "message").get("content")
                require(isinstance(content, str) and 0 < len(content) <= MAX_REPLY_CHARS, "gateway reply text is missing or too large")
                require(choice.get("finish_reason") == "stop", "gateway reply was truncated or filtered")
                extra = data.get("extra_fields") if isinstance(data.get("extra_fields"), dict) else {}
                latency = extra.get("latency")
                # Bifrost's extra_fields.latency is a millisecond count, not seconds.
                latency = round(float(latency) / 1000, 3) if type(latency) in (int, float) and 0 <= latency < 86_400_000 else round(time.monotonic() - started, 3)
            except (ValueError, TypeError, RecursionError, OverflowError):
                raise GatewayError("invalid gateway response contract") from None
            if self.token and self.token in content:
                raise GatewayError("gateway reply reflected the credential; discarded")
            if budget is not None:
                try:
                    budget.check()
                except ValidationError:
                    raise GatewayError("gateway journey time budget exhausted") from None
            if usage is None:
                usage = {key: None for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
            status = "ok"
            return {"text": content, "finish_reason": "stop", "model_requested": self.model, "fallbacks_requested": list(self.fallbacks),
                    "usage": usage,
                    "served_by": routing, "latency_seconds": latency,
                    "request_sha256": sha(body), "response_sha256": sha(response.body)}
        finally:
            if budget is not None:
                budget.finish(attempt, usage=usage, status=status, routing=routing)
