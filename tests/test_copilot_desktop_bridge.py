"""Verify the signed-in Copilot bridge without contacting GitHub."""
import importlib
import json
from pathlib import Path
import subprocess
import threading
import urllib.error
import urllib.request

import pytest


@pytest.fixture
def desktop(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts" / "copilot-check"))
    return importlib.import_module("desktop_run")


def test_cli_completion_uses_stdin_and_requires_the_requested_model(desktop, tmp_path, monkeypatch):
    model = "copilot-test"
    events = [
        {"type": "session.model_change", "data": {"newModel": model}},
        {"type": "assistant.message", "data": {"content": "response", "model": model}},
        {"type": "session.usage_info", "data": {"providerModelMetrics": [
            {"modelId": model, "metrics": {"usage": {"inputTokens": 10, "outputTokens": 3}}},
        ]}},
    ]
    monkeypatch.setenv("TAVILY_API_KEY", "test-secret-value")

    def invoke(command, **kwargs):
        assert "--model" in command and command[-1] == model
        assert "private answer" in kwargs["input"] and "private answer" not in command
        assert "TAVILY_API_KEY" not in kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "\n".join(map(json.dumps, events)))

    monkeypatch.setattr(desktop.subprocess, "run", invoke)
    messages = [{"role": "user", "content": "private answer"}]
    assert desktop.cli_completion(messages, model, 20, tmp_path) == (
        "response", {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    )
    events[1]["data"]["model"] = "unexpected-model"
    with pytest.raises(desktop.LiveRunError, match="requested model"):
        desktop.cli_completion(messages, model, 20, tmp_path)


def test_bridge_rejects_unapproved_routes_and_tool_requests(desktop, tmp_path):
    with desktop.bridge(tmp_path, "copilot-test", "test-bridge-secret") as base:
        url = base + "/v1/chat/completions"
        request = {"model": "copilot-test", "messages": [{"role": "user", "content": "text"}], "tools": [{}]}
        data = json.dumps(request).encode()
        for path, headers, status in [
            (url, {}, 401),
            (url, {"Authorization": "Bearer é"}, 401),
            (base + "/other", {"Authorization": "Bearer test-bridge-secret"}, 404),
            (url, {"Authorization": "Bearer test-bridge-secret"}, 400),
        ]:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(path, data=data, headers=headers), timeout=2)
            assert error.value.code == status


def test_bridge_bounds_concurrent_model_requests(desktop, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def complete(*_args):
        entered.set()
        assert release.wait(3)
        return "response", {}

    monkeypatch.setattr(desktop, "cli_completion", complete)
    with desktop.bridge(tmp_path, "copilot-test", "test-bridge-secret") as base:
        request = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps({"model": "copilot-test", "messages": [{"role": "user", "content": "text"}]}).encode(),
            headers={"Authorization": "Bearer test-bridge-secret"},
        )
        first = []

        def send_first():
            with urllib.request.urlopen(request, timeout=3) as response:
                first.append(response.status)

        worker = threading.Thread(target=send_first)
        worker.start()
        try:
            assert entered.wait(2)
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=2)
            assert error.value.code == 429
        finally:
            release.set()
            worker.join(3)
        assert first == [200]
