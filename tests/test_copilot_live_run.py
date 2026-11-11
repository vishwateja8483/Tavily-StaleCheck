"""The guided Copilot live runner: credentials, sign-in, model probing and exit codes. No Docker, Copilot or Tavily is contacted."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import threading

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "copilot-check" / "live_run.py"
SIGN_IN = {"device_code": "dev-code-12345", "user_code": "ABCD-1234", "verification_uri": "https://github.com/login/device", "expires_in": 899, "interval": 5}
EXTRACTION = {"claims": [{"quote": "AcmeDB Pro costs $20 per month.", "category": "pricing", "time_sensitivity": "high",
                          "reason": "Prices change.", "query": "AcmeDB Pro price"}]}
CHANGED = {"verdict": "CHANGED", "observed": "The Pro plan costs $35 per month.", "rationale": "The source gives a different price.",
           "evidence": [{"source_id": "probe-src-1", "relation": "contradicts", "quote": "The Pro plan costs $35 per month."}],
           "rewrite": "AcmeDB Pro costs $35 per month."}
SUPPORTED = {"verdict": "SUPPORTED", "observed": "Unchanged.", "rationale": "Looks right.", "evidence": []}


@pytest.fixture(scope="module")
def live():
    spec = importlib.util.spec_from_file_location("copilot_live_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def serve(script):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            seen.append((self.command, self.path, body, {k.lower(): v for k, v in self.headers.items()}))
            status, payload = script(self.command, self.path, body)
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


def completion(reply):
    return 200, {"choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(reply)}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def test_env_text_yields_only_credential_names_with_token_shaped_values(live):
    text = "# note\nexport GITHUB_COPILOT_API_KEY='ghu_abcdefgh'\nTAVILY_API_KEY = \"tvly-abcdefgh\"\nOTHER_API_KEY=zzzzzzzzzz\nPATH=/usr/bin\n"
    assert live.parse_env_text(text) == {"GITHUB_COPILOT_API_KEY": "ghu_abcdefgh", "TAVILY_API_KEY": "tvly-abcdefgh"}
    assert live.parse_env_text("TAVILY_API_KEY=short\nGITHUB_COPILOT_API_KEY=has space inside\n") == {}


def test_environment_beats_repo_env_beats_saved_file(live, tmp_path):
    repo, saved = tmp_path / ".env", tmp_path / "saved.env"
    saved.write_text("GITHUB_COPILOT_API_KEY=saved-copilot\nTAVILY_API_KEY=saved-tavily\n")
    repo.write_text("TAVILY_API_KEY=repo-tavily\n")
    environ = {"GITHUB_COPILOT_API_KEY": "env-copilot", "PATH": "/usr/bin"}
    assert live.load_credentials(environ, [repo, saved]) == {"GITHUB_COPILOT_API_KEY": "env-copilot", "TAVILY_API_KEY": "repo-tavily"}
    assert live.load_credentials({}, [repo, tmp_path / "absent.env"]) == {"TAVILY_API_KEY": "repo-tavily"}


def test_saved_credentials_merge_and_stay_owner_only(live, tmp_path):
    path = tmp_path / "config" / "credentials.env"
    live.save_credentials(path, {"TAVILY_API_KEY": "tvly-one-1234"})
    live.save_credentials(path, {"GITHUB_COPILOT_API_KEY": "ghu_two_1234"})
    assert live.read_env_file(path) == {"TAVILY_API_KEY": "tvly-one-1234", "GITHUB_COPILOT_API_KEY": "ghu_two_1234"}
    assert not path.with_name("credentials.env.tmp").exists()
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700


def test_rank_models_prefers_full_size_chat_models_and_drops_the_rest(live):
    ids = ["text-embedding-3-small", "gpt-4o", "o3-mini", "claude-sonnet-4", "gpt-4.1-mini", "github-copilot/gpt-4.1", "claude-sonnet-4.5", "gpt-4o", "gpt-5-codex"]
    assert live.rank_models(ids) == ["github-copilot/" + name for name in ("gpt-4.1", "claude-sonnet-4.5", "claude-sonnet-4", "gpt-4o", "gpt-4.1-mini")]


def test_device_sign_in_waits_through_pending_then_returns_the_token(live):
    polls = []

    def bifrost(method, path, body):
        if path.endswith("/sign-in"):
            return 200, SIGN_IN
        polls.append(body)
        return (200, {"status": "pending"}) if len(polls) < 3 else (200, {"status": "complete", "token": "ghu_signed_in_1", "login": "octo"})

    said, opened = [], []
    with serve(bifrost) as (base, seen):
        result = live.device_sign_in(base, "setup-token-1", say=said.append, sleep=lambda seconds: None, open_url=opened.append)
    assert result == ("ghu_signed_in_1", "octo")
    assert polls == [{"device_code": "dev-code-12345"}] * 3
    assert seen[0][3]["x-bifrost-setup-token"] == "setup-token-1"
    assert opened == ["https://github.com/login/device"] and any("ABCD-1234" in line for line in said)
    assert "dev-code-12345" not in " ".join(said)


def test_device_sign_in_opens_only_githubs_own_page(live):
    def bifrost(method, path, body):
        if path.endswith("/sign-in"):
            return 200, {**SIGN_IN, "verification_uri": "https://login.example/device"}
        return 200, {"status": "complete", "token": "ghu_signed_in_1"}

    opened = []
    with serve(bifrost) as (base, _):
        assert live.device_sign_in(base, "setup-token-1", say=lambda line: None, sleep=lambda seconds: None, open_url=opened.append)[1] is None
    assert opened == []


@pytest.mark.parametrize("state,message", [("denied", "denied"), ("expired", "not completed")])
def test_device_sign_in_reports_a_refused_or_expired_code(live, state, message):
    def bifrost(method, path, body):
        return (200, SIGN_IN) if path.endswith("/sign-in") else (200, {"status": state})

    with serve(bifrost) as (base, _), pytest.raises(live.LiveRunError, match=message):
        live.device_sign_in(base, "setup-token-1", say=lambda line: None, sleep=lambda seconds: None)


def test_device_sign_in_stops_polling_after_the_local_deadline(live):
    with serve(lambda method, path, body: (200, SIGN_IN)) as (base, seen):
        with pytest.raises(live.LiveRunError, match="expired"):
            live.device_sign_in(base, "setup-token-1", say=lambda line: None, sleep=lambda seconds: None, clock=iter([0, 1000]).__next__)
    assert [item[1] for item in seen] == ["/api/providers/github-copilot/sign-in"]


def test_tavily_key_is_checked_with_the_free_usage_endpoint(live):
    def tavily(method, path, body):
        return 200, {"account": {"current_plan": "Researcher", "plan_usage": 3, "plan_limit": 1000}}

    with serve(tavily) as (base, seen):
        assert live.check_tavily("tvly-key-1234", base=base) == {"verified": True, "plan": "Researcher", "credits_used": 3, "credit_limit": 1000}
    assert seen[0][:2] == ("GET", "/usage") and seen[0][3]["authorization"] == "Bearer tvly-key-1234"
    with serve(lambda method, path, body: (401, {"detail": {"error": "Unauthorized"}})) as (base, _), pytest.raises(live.LiveRunError, match="rejected"):
        live.check_tavily("tvly-key-1234", base=base)


def test_select_model_skips_failing_models_and_says_why(live):
    def bifrost(method, path, body):
        model = body["model"]
        if model.endswith("/broken"):
            return 400, {"error": {"message": "the model is not supported for this plan"}}
        if "find factual claims" in body["messages"][0]["content"]:
            return completion(EXTRACTION)
        return completion(SUPPORTED if model.endswith("/lazy") else CHANGED)

    names = ["github-copilot/broken", "github-copilot/lazy", "github-copilot/steady"]
    with serve(bifrost) as (base, seen):
        model, results = live.select_model(base, "sk-bf-test0000", names, say=lambda line: None)
    assert model == "github-copilot/steady"
    assert [result["ok"] for result in results] == [False, False, True]
    assert "not supported for this plan" in results[0]["error"] and results[0]["stage"] == "extraction"
    assert "missed an obvious price change" in results[1]["error"] and results[1]["stage"] == "assessment"
    assert results[2]["tokens"] == 30
    assert {entry[3]["x-bf-vk"] for entry in seen} == {"sk-bf-test0000"}


@pytest.mark.parametrize("status,outcome", [(400, 400), (401, "refused"), (432, "out of credits"), (502, "could not reach Tavily")])
def test_relay_check_separates_an_accepted_key_from_a_refused_one(live, status, outcome):
    with serve(lambda method, path, body: (status, {"error": "x"})) as (base, seen):
        if isinstance(outcome, int):
            assert live.check_relay(base, "sk-bf-test0000") == outcome
        else:
            with pytest.raises(live.LiveRunError, match=outcome):
                live.check_relay(base, "sk-bf-test0000")
    assert seen[0][:3] == ("POST", "/relay/tavily/search", {"query": ""})


def test_errors_never_show_remembered_secrets(live):
    live.remember("ghu_remembered_1")
    assert "ghu_remembered_1" not in str(live.LiveRunError("sign-in failed for ghu_remembered_1"))


def test_main_exits_2_without_prompting_when_credentials_are_missing(live, monkeypatch, tmp_path, capsys):
    for name in live.CREDENTIAL_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr(live, "user_env_file", lambda: tmp_path / "absent.env")
    monkeypatch.setattr(live.sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(live.signal, "signal", lambda *args: None)
    assert live.main(["--check-only"]) == 2
    assert "TAVILY_API_KEY" in capsys.readouterr().err
