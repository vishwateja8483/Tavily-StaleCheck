"""The live CLI path over a real loopback socket to a Bifrost-shaped stub; Tavily must never be reached."""
import json

import pytest

from stalecheck.acquisition import StdlibTransport
from stalecheck.cli import main
from stalecheck.verify_artifacts import verify_artifacts
from test_gateway import serve
from test_stale_check import ANSWER, PRICE, claims, proposal


@pytest.fixture
def tavily_forbidden(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("Tavily was contacted without approval")
    monkeypatch.setattr(StdlibTransport, "__call__", refuse)
    monkeypatch.setenv("TEST_BF_VK", "sk-bf-virtual-key-123")
    monkeypatch.setenv("TEST_TAVILY", "tvly-secret-456")


def test_live_cli_sends_extraction_to_the_gateway_then_declines_research_without_approval(tmp_path, capsys, tavily_forbidden):
    payload = json.dumps(claims(proposal(PRICE, "acme starter price"))["body"]).encode()
    answer = tmp_path / "answer.txt"
    answer.write_text(ANSWER, encoding="utf-8")
    with serve(lambda: (200, {"Content-Type": "application/json"}, payload)) as (port, seen):
        code = main(["stale-check", "--answer", str(answer), "--live", "--confirm-outbound", "--gateway-url", f"http://127.0.0.1:{port}",
                     "--model", "openai/gpt-4o-mini", "--fallback", "anthropic/claude-sonnet-4-5", "--gateway-token-env", "TEST_BF_VK",
                     "--tavily-key-env", "TEST_TAVILY", "--primary-domain", "example.com", "--verification-date", "2026-11-01", "--output", str(tmp_path / "live")])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    result = json.loads(captured.out)
    assert result["counts"] == {"MODEL_SUPPORTED": 0, "POTENTIALLY_CHANGED": 0, "UNVERIFIABLE": 1}
    assert result["usage"]["gateway_calls"] == 1 and result["usage"]["search_calls"] == 0 and result["usage"]["provenance"] == {"gateway": "bifrost", "search": "tavily"}
    assert len(seen) == 1 and seen[0]["path"] == "/v1/chat/completions" and seen[0]["headers"]["x-bf-vk"] == "sk-bf-virtual-key-123"
    sent = json.loads(seen[0]["body"])
    assert sent["model"] == "openai/gpt-4o-mini" and sent["fallbacks"] == ["anthropic/claude-sonnet-4-5"] and sent["stream"] is False and "tools" not in sent
    assert ANSWER in sent["messages"][1]["content"]
    # The exact queries were shown for approval, and nothing was approved without a terminal or an approval file.
    assert "acme starter price" in captured.err and "Outbound disclosure" in captured.err
    session = tmp_path / "live"
    assert json.loads((session / "completion.json").read_text(encoding="utf-8")) == {"status": "recorded", "stop_reason": "approval_declined", "run": "run"}
    assert verify_artifacts(session / "run")["valid"] is True and (session / "session.json").exists() and not (session / "failure.json").exists()
    blob = b"".join(path.read_bytes() for path in session.rglob("*") if path.is_file())
    assert b"sk-bf-virtual-key-123" not in blob and b"tvly-secret-456" not in blob
    report = json.loads((session / "run" / "report.json").read_text(encoding="utf-8"))
    assert report["stop_reason"] == "approval_declined" and report["approval"]["approved"] is False and report["claims"][0]["reason_code"] == "APPROVAL_DECLINED"
