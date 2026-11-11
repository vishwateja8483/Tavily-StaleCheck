"""Use the signed-in Copilot CLI as a text-only Bifrost provider."""
import argparse
from contextlib import contextmanager
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

from live_run import (ROOT, HERE, TAVILY_KEY, LiveRunError, check_tavily, free_port,
                      http_json, load_credentials, remember, serve_ui, user_env_file)

DEFAULT_MODEL = "claude-sonnet-5.5"
PROVIDER = "copilot-cli"
BIFROST_VERSION = "v2.5.1"
VERIFY_ANSWER = "A basic search with the Tavily Search API costs 2 API credits per request."
VERIFY_DOMAIN = "docs.tavily.com"
sys.path.insert(0, str(ROOT / "src"))
from stalecheck.contracts import ValidationError, json_bytes
from stalecheck.stale_check_v4 import validate_inputs


def cli_completion(messages, model, ceiling, cwd):
    prompt = "\n\n".join(message["content"] for message in messages)
    prompt += f"\n\nReturn only the requested response, within {ceiling} output tokens."
    command = ["copilot", "--silent", "--stream", "off", "--output-format", "json",
               "--disable-builtin-mcps", "--disable-mcp-server", "computer-use",
               "--no-custom-instructions", "--no-ask-user", "--available-tools", "__stalecheck_text_only__",
               "--sync-session=off", "--model", model]
    runtime_names = {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "XDG_CONFIG_HOME",
                     "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"}
    environment = {name: os.environ[name] for name in runtime_names if name in os.environ}
    try:
        result = subprocess.run(command, input=prompt, cwd=cwd, text=True,
                                capture_output=True, timeout=45, env=environment)
    except subprocess.TimeoutExpired:
        raise LiveRunError("Copilot timed out") from None
    if result.returncode:
        raise LiveRunError("Copilot failed. Check 'copilot' sign-in and model access.")
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    replies = [event["data"] for event in events if event.get("type") == "assistant.message"]
    if not replies or any(reply.get("model") != model for reply in replies):
        raise LiveRunError("Copilot returned no usable response from the requested model.")
    text = replies[-1].get("content")
    if not isinstance(text, str) or not text.strip():
        raise LiveRunError("Copilot returned an empty response.")
    if any(reply.get("toolRequests") for reply in replies) or any(event.get("type") == "tool.execution_start" for event in events):
        raise LiveRunError("Copilot attempted tool use on a text-only request.")
    metrics = next((event["data"]["providerModelMetrics"] for event in reversed(events)
                    if isinstance(event.get("data", {}).get("providerModelMetrics"), list)), [])
    usage = next((row["metrics"]["usage"] for row in metrics if row.get("modelId") == model), {})
    tokens = {}
    if all(type(usage.get(key)) is int and usage[key] >= 0 for key in ("inputTokens", "outputTokens")):
        tokens = {"prompt_tokens": usage["inputTokens"], "completion_tokens": usage["outputTokens"],
                  "total_tokens": usage["inputTokens"] + usage["outputTokens"]}
        if tokens["completion_tokens"] > ceiling:
            raise LiveRunError("Copilot exceeded the requested response length.")
    return text, tokens


class BridgeHandler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def log_message(self, *_args):
        pass

    def send_json(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def allowed(self):
        supplied = self.headers.get("Authorization", "").encode("latin-1")
        expected = f"Bearer {self.server.secret}".encode("ascii")
        if hmac.compare_digest(supplied, expected):
            return True
        self.send_json(401, {"error": {"message": "Invalid bridge credential"}})
        return False

    def do_GET(self):
        if self.allowed():
            if self.path == "/v1/models":
                self.send_json(200, {"object": "list", "data": [
                    {"id": self.server.model, "object": "model", "created": 0, "owned_by": "github-copilot"}]})
            else:
                self.send_json(404, {"error": {"message": "Unknown route"}})

    def do_POST(self):
        if not self.allowed():
            return
        if self.path != "/v1/chat/completions":
            return self.send_json(404, {"error": {"message": "Unknown route"}})
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 96_000:
                raise ValueError
            request = json.loads(self.rfile.read(size))
            messages = request["messages"]
            ceiling = request.get("max_completion_tokens", request.get("max_tokens", 1200))
            if (request.get("model") != self.server.model or request.get("stream", False) is not False
                    or request.get("tools") or request.get("functions") or request.get("n", 1) != 1
                    or type(ceiling) is not int or not 1 <= ceiling <= 1600
                    or not isinstance(messages, list) or not 1 <= len(messages) <= 4
                    or any(not isinstance(item, dict) or item.get("role") not in {"system", "user"}
                           or not isinstance(item.get("content"), str) for item in messages)):
                raise ValueError
        except (KeyError, ValueError, TypeError, OSError):
            return self.send_json(400, {"error": {"message": "Expected a bounded non-streaming text request"}})
        if not self.server.model_slot.acquire(blocking=False):
            return self.send_json(429, {"error": {"message": "A Copilot request is already in progress"}})
        try:
            text, usage = cli_completion(messages, self.server.model, ceiling, self.server.cwd)
        except (LiveRunError, ValueError, OSError, KeyError, TypeError) as error:
            message = str(error) if isinstance(error, LiveRunError) else "Copilot response could not be read."
            return self.send_json(502, {"error": {"message": message}})
        finally:
            self.server.model_slot.release()
        self.send_json(200, {"id": "chatcmpl-" + secrets.token_hex(8), "object": "chat.completion",
                            "created": int(time.time()), "model": self.server.model,
                            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                         "finish_reason": "stop"}], **({"usage": usage} if usage else {})})


@contextmanager
def bridge(work, model, secret):
    with ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler) as server:
        server.model, server.secret, server.cwd = model, secret, work / "copilot"
        server.model_slot = threading.BoundedSemaphore(1)
        server.cwd.mkdir()
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            worker.join()


def gateway_config(bridge_url, model):
    config = json.loads((HERE / "config.json").read_text())
    config["providers"] = {PROVIDER: {
        "custom_provider_config": {"base_provider_type": "openai"},
        "network_config": {"base_url": bridge_url, "default_request_timeout_in_seconds": 50, "max_retries": 0},
        "keys": [{"name": PROVIDER, "value": "env.STALECHECK_COPILOT_BRIDGE_KEY", "models": [model], "weight": 1}],
    }}
    config["governance"]["virtual_keys"][0]["provider_configs"][0].update(provider=PROVIDER, allowed_models=[model])
    return config


@contextmanager
def gateway(work, bridge_url, model, virtual_key, bridge_key, tavily):
    path = work / "bifrost"
    path.mkdir()
    (path / "config.json").write_text(json.dumps(gateway_config(bridge_url, model)))
    url = f"http://127.0.0.1:{free_port()}"
    env = {**os.environ, TAVILY_KEY: tavily, "BIFROST_VIRTUAL_KEY": virtual_key,
           "STALECHECK_COPILOT_BRIDGE_KEY": bridge_key, "BIFROST_SETUP_TOKEN": secrets.token_hex(16)}
    installed = Path.home() / ".cache" / "stalecheck" / f"bifrost-{BIFROST_VERSION}" / "bifrost-http"
    binary = os.environ.get("BIFROST_BINARY") or (str(installed) if os.access(installed, os.X_OK) else shutil.which("bifrost-http"))
    if not binary and not shutil.which("npx"):
        raise LiveRunError("Install Bifrost or set BIFROST_BINARY to its executable.")
    command = [binary] if binary else ["npx", "-y", "@maximhq/bifrost", "--transport-version", BIFROST_VERSION]
    command += ["-host", "127.0.0.1", "-port", url.rsplit(":", 1)[-1], "-app-dir", str(path)]
    with (work / "gateway.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise LiveRunError("Bifrost exited. Check the npx installation and version.")
                try:
                    if http_json("GET", url + "/health", timeout=2)[0] == 200:
                        break
                except LiveRunError:
                    pass
                time.sleep(1)
            else:
                raise LiveRunError("Bifrost did not become healthy.")
            yield url
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def verification_input(args):
    answer = args.answer_file.read_text(encoding="utf-8").strip() if args.answer_file else VERIFY_ANSWER
    domains = args.domain or [VERIFY_DOMAIN]
    attempts = 1 + 2 * args.max_claims
    value = {"answer_text": answer, "verification_date": date.today().isoformat(), "answer_date": None,
             "primary_domains": domains, "max_claims": args.max_claims, "max_attempts": attempts,
             "max_seconds": 180, "consent": True}
    validate_inputs(answer, value["verification_date"], None, domains, args.max_claims, attempts, 180)
    return value


def save_verification(out, state, run):
    from stalecheck.verify_artifacts import load_verified_run

    snapshot = load_verified_run(run)
    report, transcript = snapshot["report"], snapshot["input"]
    artifacts = snapshot["verification"]
    receipt = {
        "assessment": state, "artifact_verification": artifacts,
        "approval": transcript["approval"], "verdicts": report["counts"], "usage": report["usage"],
        "attempts": transcript["attempts"],
        "claims": [{"text": claim["text"], "verdict": claim["verdict"], "reason_code": claim["reason_code"],
                    "freshness": claim["freshness"], "retained_quotes": len(claim["evidence"])}
                   for claim in report["claims"]],
    }
    (out / "verification.json").write_bytes(json_bytes(receipt))
    if state != "completed" or not transcript["approval"]["approved"] or not report["evidence"] or not artifacts["valid"]:
        raise LiveRunError(f"Assessment did not complete with verified source evidence. See {out / 'verification.json'}.")
    return receipt


def verify(out, url, model, key, value):
    from stalecheck.web_jobs import JobManager, TERMINAL

    manager = JobManager(out / "assessment", live=True, configuration={
        "BIFROST_URL": url, "BIFROST_MODEL": model, "BIFROST_VIRTUAL_KEY": key, "BIFROST_SEARCH_RELAY": "tavily",
    })
    try:
        job, _ = manager.create(secrets.token_hex(16), value)
        record = manager.get(job["id"])
        approved = False
        deadline = time.monotonic() + 185
        while time.monotonic() < deadline:
            status = record.snapshot()
            if status["state"] in TERMINAL:
                break
            if status["state"] == "awaiting_approval" and not approved:
                print("Approving the live test search:", json.dumps(status["approval"]["queries"]), flush=True)
                manager.approve(job["id"], status["approval"], True)
                approved = True
            time.sleep(.25)
        if "report.html" not in status["artifacts"]:
            raise LiveRunError(f"Assessment ended in state {status['state']}. Checkpoints retained in {record.directory}.")
        (out / "report.html").write_bytes(manager.artifact(job["id"], "report.html"))
        receipt = save_verification(out, status["state"], record.directory / "run")
        print(json.dumps(receipt, indent=2))
    finally:
        manager.close()
        if manager.worker:
            manager.worker.join(5)


def verify_browser(out, url, model, key, driver, value):
    answer = out / "answer.txt"
    answer.write_text(value["answer_text"] + "\n", encoding="utf-8")
    env = {**os.environ, "BIFROST_URL": url, "BIFROST_MODEL": model, "BIFROST_VIRTUAL_KEY": key,
           "BIFROST_SEARCH_RELAY": "tavily"}
    env.pop(TAVILY_KEY, None)
    destination = out / "browser"
    command = ["node", str(ROOT / "scripts/check-assessment-live.mjs"), str(driver), sys.executable,
               str(destination), str(answer), "\n".join(value["primary_domains"]), str(value["max_claims"])]
    if subprocess.run(command, env=env, cwd=ROOT).returncode:
        raise LiveRunError(f"Browser assessment failed. See the retained run in {destination}.")
    summary = json.loads((destination / "summary.json").read_text())
    run, = (destination / "sessions").glob("*/run")
    save_verification(out, summary["state"], run)
    if summary["pageErrors"] or summary["unexpectedRequests"]:
        raise LiveRunError(f"The browser reported an error or an unexpected network request. See {destination / 'summary.json'}.")
    print("Live browser assessment and artifacts verified:", destination, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("LIVE_MODEL", DEFAULT_MODEL))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--open-ui", action="store_true", help="open the live assessment in the default browser")
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--verify", action="store_true", help="run and approve one real public search, save its report, then exit")
    checks.add_argument("--verify-browser", type=Path, metavar="PLAYWRIGHT_DRIVER",
                        help="run and approve one real public search in the browser, then verify the artifacts")
    parser.add_argument("--answer-file", type=Path, help="UTF-8 answer for a verification run")
    parser.add_argument("--domain", action="append", help="reviewed domain for a verification run, repeatable")
    parser.add_argument("--max-claims", type=int, choices=range(1, 9), help="claim limit for a verification run")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9._-]{1,100}", args.model):
        parser.error("model must be a Copilot CLI model ID")
    if (args.answer_file or args.domain or args.max_claims) and not (args.verify or args.verify_browser):
        parser.error("verification inputs require --verify or --verify-browser")
    value = None
    if args.verify or args.verify_browser:
        args.max_claims = args.max_claims or 1
        value = verification_input(args)
    if not shutil.which("copilot"):
        raise LiveRunError("Install and sign in to GitHub Copilot CLI.")
    key = load_credentials(os.environ, [ROOT / ".env", user_env_file()]).get(TAVILY_KEY)
    if not key:
        raise LiveRunError("Set TAVILY_API_KEY in the ignored .env file.")
    virtual_key, bridge_key = "sk-bf-" + secrets.token_hex(16), secrets.token_hex(24)
    remember(key, virtual_key, bridge_key)
    check_tavily(key)
    out = (args.output or ROOT / "runs" / f"copilot-desktop-{int(time.time())}").resolve()
    out.mkdir(parents=True, exist_ok=False)
    model = f"{PROVIDER}/{args.model}"
    with tempfile.TemporaryDirectory(prefix="stalecheck-copilot-") as work:
        directory = Path(work).resolve()
        with bridge(directory, args.model, bridge_key) as bridge_url, gateway(directory, bridge_url, args.model, virtual_key, bridge_key, key) as url:
            if args.verify:
                verify(out, url, model, virtual_key, value)
                return 0
            if args.verify_browser:
                verify_browser(out, url, model, virtual_key, args.verify_browser.resolve(), value)
                return 0
            return serve_ui(args, SimpleNamespace(url=url), model, virtual_key, out)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        sys.exit(main())
    except (LiveRunError, OSError, ValidationError) as error:
        sys.exit(str(error))
    except KeyboardInterrupt:
        sys.exit(130)
