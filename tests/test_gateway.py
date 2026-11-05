"""Bifrost gateway client: URL policy, request shape, sanitized refusals and bounded transport."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading

import pytest

from stalecheck.acquisition import Response
from stalecheck.contracts import ValidationError, json_bytes
from stalecheck.gateway import (MAX_RESPONSE_BYTES, GatewayClient, GatewayError, MockGatewayTransport, StdlibGatewayTransport, gateway_origin)
from stalecheck.journey import JourneyBudget

MESSAGES = [{"role": "system", "content": "Reply with JSON."}, {"role": "user", "content": "Hello"}]


def completion(content="{\"ok\": true}", *, usage=None, extra=None, finish="stop", model="gpt-4o-mini"):
    body = {"id": "scripted", "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}]}
    if usage is not None:
        body["usage"] = usage
    if extra is not None:
        body["extra_fields"] = extra
    return {"status": 200, "body": body}


def client(fixtures, **kwargs):
    transport = MockGatewayTransport(fixtures)
    return GatewayClient("http://127.0.0.1:8080", "openai/gpt-4o-mini", transport=transport, **kwargs), transport


@pytest.mark.parametrize("value", ["http://127.0.0.1:8080", "http://localhost:8080/", "http://[::1]:8080", "https://gateway.example.com", "https://gateway.example.com:8443"])
def test_gateway_origin_accepts_loopback_http_and_https_hostnames(value):
    scheme, host, port = gateway_origin(value)
    assert scheme in ("http", "https") and host and 1 <= port <= 65535


@pytest.mark.parametrize("value", [
    "", " http://127.0.0.1:8080", "http://gateway.example.com", "http://10.0.0.5:8080", "https://10.0.0.5", "https://localhost.", "ftp://127.0.0.1",
    "http://user:pass@127.0.0.1:8080", "http://127.0.0.1:8080/v1", "http://127.0.0.1:8080?x=1", "http://127.0.0.1:8080#frag", "http://127.0.0.1:99999",
    "http://127.0.0.1:8080\n", "https://gatewäy.example.com", "https://singlelabel", "x" * 201,
])
def test_gateway_origin_rejects_unsafe_origins(value):
    with pytest.raises(ValidationError):
        gateway_origin(value)


def test_request_uses_openai_shape_model_fallbacks_and_virtual_key_flag():
    gateway, transport = client([completion()], fallbacks=["anthropic/claude-sonnet-4-5"], token="sk-bf-test-key", max_tokens=300)
    gateway.chat(MESSAGES)
    sent = transport.calls[0]
    assert sent["token_supplied"] is True
    assert sent["body"] == {"model": "openai/gpt-4o-mini", "messages": MESSAGES, "temperature": 0, "max_tokens": 300, "stream": False,
                            "fallbacks": ["anthropic/claude-sonnet-4-5"]}
    assert "sk-bf-test-key" not in json.dumps(sent["body"])


def test_request_without_fallbacks_or_token_omits_them():
    gateway, transport = client([completion()])
    gateway.chat(MESSAGES, max_tokens=50)
    assert "fallbacks" not in transport.calls[0]["body"] and transport.calls[0]["token_supplied"] is False and transport.calls[0]["body"]["max_tokens"] == 50


def serving(seen):
    def transport(target, body, *, token, timeout, max_bytes):
        seen.append(timeout)
        return Response(200, json_bytes(completion()["body"]))
    return transport


@pytest.mark.parametrize("configured,expected", [(None, 60.0), (150, 150.0), (1, 1.0), (180, 180.0)])
def test_call_timeout_defaults_to_sixty_seconds_and_can_be_raised_for_slow_models(configured, expected):
    seen = []
    extra = {} if configured is None else {"timeout": configured}
    GatewayClient("http://127.0.0.1:8080", "openai/gpt-4o-mini", transport=serving(seen), allow_network=True, **extra).chat(MESSAGES)
    assert seen == [expected]


def test_the_journey_deadline_still_caps_a_raised_call_timeout():
    seen = []
    gateway = GatewayClient("http://127.0.0.1:8080", "openai/gpt-4o-mini", transport=serving(seen), allow_network=True, timeout=150)
    gateway.chat(MESSAGES, budget=JourneyBudget(max_seconds=20))
    assert 0 < seen[0] <= 20


@pytest.mark.parametrize("value", [0, 0.5, -1, 181, float("inf"), float("nan"), True, "60", None])
def test_invalid_call_timeouts_are_rejected(value):
    with pytest.raises(ValidationError, match="invalid gateway timeout"):
        GatewayClient("http://127.0.0.1:8080", "openai/gpt-4o-mini", transport=MockGatewayTransport([]), timeout=value)


def test_reply_records_usage_routing_takeover_latency_and_digests():
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    routing = {"provider": "anthropic", "model": "claude-sonnet-4-5", "is_fallback": True, "primary_provider": "openai", "primary_model": "gpt-4o-mini"}
    gateway, _ = client([completion(usage=usage, extra={"latency": 2500, "routing_info": routing})])
    reply = gateway.chat(MESSAGES)
    assert reply["text"] == "{\"ok\": true}" and reply["usage"] == usage and reply["latency_seconds"] == 2.5
    assert reply["served_by"] == {"provider": "anthropic", "model": "claude-sonnet-4-5", "is_fallback": True}
    assert len(reply["request_sha256"]) == len(reply["response_sha256"]) == 64 and reply["finish_reason"] == "stop"


def test_primary_routing_omits_false_fallback_field_and_latency_is_milliseconds():
    gateway, _ = client([completion(extra={"latency": 1420, "routing_info": {"provider": "openai", "model": "gpt-4o-mini"}})])
    reply = gateway.chat(MESSAGES)
    assert reply["served_by"] == {"provider": "openai", "model": "gpt-4o-mini", "is_fallback": False}
    assert reply["latency_seconds"] == 1.42
    # An empty routing object is not evidence of a primary-served reply.
    unknown = client([completion(extra={"routing_info": {}})])[0].chat(MESSAGES)
    assert unknown["served_by"] == {"provider": None, "model": None, "is_fallback": None}


def test_missing_usage_and_routing_are_unknown_not_zero():
    reply = client([completion()])[0].chat(MESSAGES)
    assert reply["usage"] == {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    assert reply["served_by"] == {"provider": None, "model": "gpt-4o-mini", "is_fallback": None}


def test_hostile_usage_and_routing_values_are_dropped():
    usage = {"prompt_tokens": -1, "completion_tokens": True, "total_tokens": "7"}
    reply = client([completion(usage=usage, extra={"latency": "fast", "routing_info": {"provider": "bad provider!", "model": 3, "is_fallback": "yes"}})])[0].chat(MESSAGES)
    assert set(reply["usage"].values()) == {None}
    assert reply["served_by"] == {"provider": None, "model": None, "is_fallback": None}


@pytest.mark.parametrize("kind", ["budget_exceeded", "rate_limited", "token_limited", "request_limited", "model_blocked", "provider_blocked", "access_blocked", "virtual_key_required",
                                  "access_not_found", "relay_blocked"])
def test_documented_refusals_are_sanitized_and_typed(kind):
    secret = "SECRET-PROVIDER-TEXT sk-live-123"
    for body in ({"error": {"type": kind, "message": secret}}, {"type": kind, "message": secret}):
        gateway, _ = client([{"status": 402, "body": body}])
        with pytest.raises(GatewayError) as caught:
            gateway.chat(MESSAGES)
        assert caught.value.kind == kind and caught.value.status == 402 and secret not in str(caught.value) and kind in str(caught.value)


def test_unknown_error_types_are_reported_without_provider_text():
    gateway, _ = client([{"status": 500, "body": {"error": {"type": "boom", "message": "SECRET"}}}])
    with pytest.raises(GatewayError) as caught:
        gateway.chat(MESSAGES)
    assert caught.value.kind is None and caught.value.status == 500 and "SECRET" not in str(caught.value) and "boom" not in str(caught.value)


@pytest.mark.parametrize("fixture", [
    completion(finish="length"), completion(finish="content_filter"), completion(content=""), completion(content=["x"]),
    {"status": 200, "body": {"choices": []}}, {"status": 200, "body": {"choices": [{"message": "text", "finish_reason": "stop"}]}},
    {"status": 200, "body": ["not", "an", "object"]}, {"status": 200, "body": {"choices": [{"message": {"content": "x" * 20_001}, "finish_reason": "stop"}]}},
])
def test_invalid_or_truncated_replies_fail_closed(fixture):
    with pytest.raises(GatewayError):
        client([fixture])[0].chat(MESSAGES)


def test_reply_reflecting_the_credential_is_discarded():
    gateway, _ = client([completion("echo sk-bf-test-key")], token="sk-bf-test-key")
    with pytest.raises(GatewayError, match="reflected the credential"):
        gateway.chat(MESSAGES)


def test_network_transport_needs_explicit_opt_in():
    with pytest.raises(ValidationError, match="explicit network opt-in"):
        GatewayClient("http://127.0.0.1:8080", "openai/gpt-4o-mini")
    assert GatewayClient("http://127.0.0.1:8080", "openai/gpt-4o-mini", allow_network=True).transport.is_mock is False
    with pytest.raises(ValidationError, match="explicit network opt-in"):
        GatewayClient("http://127.0.0.1:8080", "openai/gpt-4o-mini", allow_network="yes")


@pytest.mark.parametrize("kwargs", [
    {"model": ""}, {"model": "bad model"}, {"model": "../x"}, {"fallbacks": ["a/b", "c/d", "e/f", "g/h"]}, {"fallbacks": ["openai/gpt-4o-mini"]},
    {"fallbacks": ["a/b", "a/b"]}, {"fallbacks": ["bad one"]}, {"max_tokens": 0}, {"max_tokens": 4097}, {"max_tokens": True},
    {"token": ""}, {"token": "has space"}, {"token": "x" * 513}, {"token": "tab\there"},
])
def test_client_parameters_are_bounded(kwargs):
    arguments = {"model": "openai/gpt-4o-mini", **kwargs}
    model = arguments.pop("model")
    with pytest.raises(ValidationError):
        GatewayClient("http://127.0.0.1:8080", model, transport=MockGatewayTransport([]), **arguments)


@pytest.mark.parametrize("messages", [
    [], "hi", [{"role": "assistant", "content": "x"}], [{"role": "user"}], [{"role": "user", "content": ""}], [{"role": "user", "content": "x", "name": "y"}],
    [{"role": "user", "content": "x" * 40_001}], [{"role": "user", "content": "x"}] * 5,
])
def test_messages_are_validated_before_any_call(messages):
    gateway, transport = client([completion()])
    with pytest.raises(ValidationError):
        gateway.chat(messages)
    assert transport.calls == []


def test_oversized_request_is_rejected_before_any_call():
    gateway, transport = client([completion()])
    with pytest.raises(ValidationError, match="request exceeds byte bound"):
        gateway.chat([{"role": "system", "content": "\u4e2d" * 40_000}, {"role": "user", "content": "\u4e2d" * 40_000}])
    assert transport.calls == []


def test_transport_failures_are_sanitized_and_not_retried():
    class Boom:
        calls = 0

        def __call__(self, *args, **kwargs):
            Boom.calls += 1
            raise OSError("connection reset: sk-bf-secret")
    gateway = GatewayClient("http://127.0.0.1:8080", "openai/gpt-4o-mini", transport=Boom(), allow_network=True, token="sk-bf-secret")
    with pytest.raises(GatewayError, match="usage unknown") as caught:
        gateway.chat(MESSAGES)
    assert Boom.calls == 1 and "sk-bf-secret" not in str(caught.value)


def test_mock_transport_is_bounded_and_ordered():
    with pytest.raises(ValidationError):
        MockGatewayTransport([completion()] * 33)
    gateway, _ = client([])
    with pytest.raises(ValidationError, match="queue exhausted"):
        gateway.chat(MESSAGES)


@contextmanager
def serve(script):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": self.rfile.read(int(self.headers.get("Content-Length", 0)))})
            status, headers, payload = script()
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    class Quiet(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # The client may hang up first when it enforces a byte bound.

    server = Quiet(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def live_client(port, **kwargs):
    return GatewayClient(f"http://127.0.0.1:{port}", "openai/gpt-4o-mini", transport=StdlibGatewayTransport(), allow_network=True, **kwargs)


def test_real_transport_posts_to_the_chat_path_with_only_the_virtual_key_header():
    payload = json.dumps(completion("{\"ok\": 1}")["body"]).encode()
    with serve(lambda: (200, {"Content-Type": "application/json"}, payload)) as (port, seen):
        reply = live_client(port, token="sk-bf-local", fallbacks=["anthropic/claude-sonnet-4-5"]).chat(MESSAGES)
    assert reply["text"] == "{\"ok\": 1}"
    request = seen[0]
    assert request["path"] == "/v1/chat/completions" and request["headers"]["x-bf-vk"] == "sk-bf-local" and "authorization" not in request["headers"]
    assert request["headers"]["accept-encoding"] == "identity" and json.loads(request["body"])["fallbacks"] == ["anthropic/claude-sonnet-4-5"]


def test_real_transport_omits_the_key_header_when_no_token_is_given():
    payload = json.dumps(completion()["body"]).encode()
    with serve(lambda: (200, {}, payload)) as (port, seen):
        live_client(port).chat(MESSAGES)
    assert "x-bf-vk" not in seen[0]["headers"]


def test_real_transport_does_not_follow_redirects():
    with serve(lambda: (302, {"Location": "http://127.0.0.1:1/elsewhere"}, b"")) as (port, seen):
        with pytest.raises(GatewayError, match="gateway HTTP 302"):
            live_client(port).chat(MESSAGES)
    assert len(seen) == 1


def test_real_transport_bounds_success_bodies():
    with serve(lambda: (200, {}, b"x" * (MAX_RESPONSE_BYTES + 5))) as (port, _):
        with pytest.raises(GatewayError, match="exceeds byte bound"):
            live_client(port).chat(MESSAGES)


def test_real_transport_rejects_compressed_bodies():
    with serve(lambda: (200, {"Content-Encoding": "gzip"}, b"\x1f\x8b")) as (port, _):
        with pytest.raises(GatewayError, match="compressed"):
            live_client(port).chat(MESSAGES)


def test_real_transport_truncates_error_bodies_and_keeps_the_refusal_type():
    body = json.dumps({"error": {"type": "budget_exceeded", "message": "SECRET " + "x" * 50_000}}).encode()
    with serve(lambda: (402, {}, body)) as (port, _):
        with pytest.raises(GatewayError) as caught:
            live_client(port).chat(MESSAGES)
    assert caught.value.status == 402 and "SECRET" not in str(caught.value)
    # The 8 KiB error cap cuts the JSON mid-message, and a cut body is never trusted for a refusal type.
    assert caught.value.kind is None


def test_real_transport_connection_failure_is_sanitized():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(GatewayError, match="usage unknown"):
        live_client(port).chat(MESSAGES)
