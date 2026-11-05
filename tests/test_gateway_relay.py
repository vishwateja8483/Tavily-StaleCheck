"""Tavily Search through a Bifrost relay: transport contract, status-only refusals and the live CLI journey over a loopback socket."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from stalecheck.acquisition import Response, acquire
from stalecheck.cli import main
from stalecheck.contracts import ValidationError, json_bytes
from stalecheck.gateway import BifrostRelayTransport
from stalecheck.stale_check_v4 import approval_request
from stalecheck.verify_artifacts import verify_artifacts
from test_gateway import serve
from test_stale_check import ANSWER, DOCS, PRICE, PRICE_PAGE, SDK, SDK_PAGE, VERSION, claims, hits, proposal, verdict

KEY = "sk-bf-virtual-key-123"
PLAN = {"schema_version": "1.0", "format": "reviewed_public_acquisition", "public_reviewed": True,
        "queries": ["acme starter price"], "urls": [], "primary_domains": ["example.com"]}


def tavily_reply(*rows, credits=1):
    return json.dumps(hits(*rows, credits=credits)["body"]).encode()


def replies(*fixtures):
    return [(fixture["status"], fixture["body"]) for fixture in fixtures]


@contextmanager
def bifrost_stand_in(chat, relay):
    """One loopback server for the gateway's two paths, chat completions and the Tavily relay; replies are consumed in order."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                         "body": self.rfile.read(int(self.headers.get("Content-Length", 0)))})
            status, body = (relay if self.path.startswith("/relay/") else chat).pop(0)
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_relay_transport_posts_to_the_relay_path_with_only_the_virtual_key_header():
    payload = tavily_reply((DOCS, PRICE_PAGE, "2026-10-20"))
    with serve(lambda: (200, {"Content-Type": "application/json"}, payload)) as (port, seen):
        transport = BifrostRelayTransport(f"http://127.0.0.1:{port}", "tavily")
        response = transport("/search", b'{"query": "x"}', api_key=KEY, timeout=5, max_bytes=100_000)
    assert response == Response(200, payload) and transport.is_mock is False
    request = seen[0]
    assert request["path"] == "/relay/tavily/search" and request["body"] == b'{"query": "x"}'
    assert request["headers"]["x-bf-vk"] == KEY and "authorization" not in request["headers"] and request["headers"]["accept-encoding"] == "identity"


@pytest.mark.parametrize("status,kind", [(401, "virtual_key_required"), (402, "budget_exceeded"), (403, "relay_blocked"), (404, None)])
def test_relay_refusals_are_returned_by_status_alone(status, kind):
    body = json.dumps({"type": kind, "error": {"message": "SECRET-GATEWAY-TEXT"}}).encode()
    with serve(lambda: (status, {}, body)) as (port, _):
        response = BifrostRelayTransport(f"http://127.0.0.1:{port}", "tavily")("/search", b"{}", api_key=KEY, timeout=5, max_bytes=1_000)
    assert response == Response(status, b"")


@pytest.mark.parametrize("name", ["", ".", "..", "../x", "a/b", "a b", "-lead", "a" * 65, "tavily\n", None, 7])
def test_relay_names_are_restricted_to_one_safe_path_segment(name):
    with pytest.raises(ValidationError, match="invalid relay name"):
        BifrostRelayTransport("http://127.0.0.1:8080", name)


def test_relay_transport_needs_a_safe_gateway_origin_and_a_known_endpoint():
    with pytest.raises(ValidationError, match="plain HTTP is allowed only for a loopback gateway"):
        BifrostRelayTransport("http://gateway.example.com", "tavily")
    with pytest.raises(ValidationError, match="unsupported acquisition endpoint"):
        BifrostRelayTransport("http://127.0.0.1:8080", "tavily")("/crawl", b"{}", api_key=KEY, timeout=1, max_bytes=10)


def test_acquisition_through_the_relay_uses_the_virtual_key_as_the_only_credential():
    payload = tavily_reply((DOCS, PRICE_PAGE, "2026-10-20"), credits=1)
    with serve(lambda: (200, {}, payload)) as (port, seen):
        result = acquire(PLAN, allow_network=True, api_key=KEY, transport=BifrostRelayTransport(f"http://127.0.0.1:{port}", "tavily"))
    assert [row["url"] for row in result["candidates"]] == [DOCS] and result["requests"][0]["provider_usage"] == {"credits": 1}
    assert len(seen) == 1 and seen[0]["path"] == "/relay/tavily/search" and seen[0]["headers"]["x-bf-vk"] == KEY
    sent = json.loads(seen[0]["body"])
    assert sent["query"] == "acme starter price" and sent["include_domains"] == ["example.com"] and sent["include_usage"] is True


def live(tmp_path, port, *extra, answer=ANSWER, claims_limit=2):
    source = tmp_path / "answer.txt"
    source.write_text(answer, encoding="utf-8")
    return ["stale-check", "--answer", str(source), "--live", "--confirm-outbound", "--gateway-url", f"http://127.0.0.1:{port}", "--model", "openai/gpt-4o-mini",
            "--primary-domain", "example.com", "--verification-date", "2026-11-01", "--max-claims", str(claims_limit), *extra, "--output", str(tmp_path / "run")]


@pytest.mark.parametrize("extra,message", [
    (["--search-relay", "tavily"], "--search-relay needs --gateway-token-env"),
    (["--search-relay", "bad name", "--gateway-token-env", "TEST_BF_VK"], "invalid relay name")])
def test_search_relay_is_validated_before_any_output_or_network_use(tmp_path, capsys, monkeypatch, extra, message):
    monkeypatch.setenv("TEST_BF_VK", KEY)
    assert main(live(tmp_path, 1, *extra)) == 2
    errors = [line for line in capsys.readouterr().err.splitlines() if line.startswith('{"status": "error"')]
    assert message in json.loads(errors[-1])["error"] and not (tmp_path / "run").exists()


def test_live_cli_journey_sends_search_through_the_relay_with_the_virtual_key_only(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TEST_BF_VK", KEY)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    chat = replies(claims(proposal(PRICE, "acme starter price"), proposal(VERSION, "acme sdk version", category="version")),
                   verdict("CHANGED", ("claim-1-src-1", "contradicts", "The Starter plan costs $24 per month")),
                   verdict("SUPPORTED", ("claim-2-src-1", "supports", "The Acme SDK is at version 4.2")))
    relay = replies(hits((DOCS, PRICE_PAGE, "2026-10-20"), credits=1), hits((SDK, SDK_PAGE, "2026-09-15"), credits=1))
    approval = tmp_path / "approval.json"
    approval.write_bytes(json_bytes({**approval_request([{"query": "acme starter price"}, {"query": "acme sdk version"}], ["example.com"]), "approved": True}))
    with bifrost_stand_in(chat, relay) as (port, seen):
        code = main(live(tmp_path, port, "--gateway-token-env", "TEST_BF_VK", "--search-relay", "tavily", "--approval", str(approval)))
    captured = capsys.readouterr()
    assert code == 0, captured.err
    result = json.loads(captured.out)
    assert result["counts"] == {"MODEL_SUPPORTED": 1, "POTENTIALLY_CHANGED": 1, "UNVERIFIABLE": 0}
    assert [request["path"] for request in seen] == ["/v1/chat/completions", "/relay/tavily/search", "/v1/chat/completions", "/relay/tavily/search", "/v1/chat/completions"]
    assert all(request["headers"]["x-bf-vk"] == KEY and "authorization" not in request["headers"] for request in seen)
    usage = result["usage"]
    assert usage["gateway_calls"] == 3 and usage["search_calls"] == 2 and usage["credits"]["known_subtotal"]["credits"] == 2
    assert usage["provenance"] == {"gateway": "bifrost", "search": "tavily"}
    assert "relays them to Tavily (relay 'tavily')" in captured.err and "Only exact approved queries" not in captured.err
    assert verify_artifacts(tmp_path / "run" / "run")["valid"] is True
    assert KEY.encode() not in b"".join(path.read_bytes() for path in (tmp_path / "run").rglob("*") if path.is_file())


@pytest.mark.parametrize("status,kind", [(402, "budget_exceeded"), (403, "relay_blocked"), (401, "access_not_found")])
def test_a_relay_refusal_stops_the_journey_as_a_failed_search(tmp_path, capsys, monkeypatch, status, kind):
    monkeypatch.setenv("TEST_BF_VK", KEY)
    chat = replies(claims(proposal(PRICE, "acme starter price")))
    relay = [(status, {"type": kind, "error": {"message": "SECRET-GATEWAY-TEXT"}})]
    approval = tmp_path / "approval.json"
    approval.write_bytes(json_bytes({**approval_request([{"query": "acme starter price"}], ["example.com"]), "approved": True}))
    with bifrost_stand_in(chat, relay) as (port, seen):
        code = main(live(tmp_path, port, "--gateway-token-env", "TEST_BF_VK", "--search-relay", "tavily", "--approval", str(approval), claims_limit=1))
    captured = capsys.readouterr()
    assert code == 0, captured.err
    report = json.loads((tmp_path / "run" / "run" / "report.json").read_text(encoding="utf-8"))
    assert report["stop_reason"] == "search_failed" and [request["path"] for request in seen] == ["/v1/chat/completions", "/relay/tavily/search"]
    assert verify_artifacts(tmp_path / "run" / "run")["valid"] is True
    assert b"SECRET-GATEWAY-TEXT" not in b"".join(path.read_bytes() for path in (tmp_path / "run").rglob("*") if path.is_file())
