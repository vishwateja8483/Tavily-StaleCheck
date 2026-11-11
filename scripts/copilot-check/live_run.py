#!/usr/bin/env python3
"""Turnkey live run: GitHub Copilot through Bifrost, Tavily through a Bifrost relay, then the StaleCheck UI.

Standard library only. Provider credentials reach nothing but the local Bifrost container's environment: they are
never printed, never placed on a command line and never written inside this repository. With your consent they are kept
in ~/.config/stalecheck/credentials.env (owner-only). Models are chosen by running StaleCheck's own extraction and
assessment prompts through its own parsers, so a model that is selected is one this product can actually use.
"""
import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
IMAGE = "maximhq/bifrost:v2.5.1"
PROVIDER = "github-copilot"
COPILOT_KEY, TAVILY_KEY = "GITHUB_COPILOT_API_KEY", "TAVILY_API_KEY"
CREDENTIAL_NAMES = (COPILOT_KEY, TAVILY_KEY)
TAVILY_API = "https://api.tavily.com"
PLACEHOLDER = "placeholder-until-sign-in"
MAX_CANDIDATES = 4
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

PROBE_ANSWER = "AcmeDB Pro costs $20 per month. The AcmeDB API is at version 2."
PROBE_CLAIM = "AcmeDB Pro costs $20 per month."
PROBE_SOURCE = {"id": "probe-src-1", "url": "https://acmedb.example/pricing", "title": "AcmeDB pricing",
                "content": "AcmeDB pricing. The Pro plan costs $35 per month. Prices last updated 1 October 2026.",
                "published_at": "2026-10-01T00:00:00Z"}

NON_CHAT = re.compile(r"embed|rerank|whisper|tts|audio|realtime|image|moderation|codex|^o\d", re.I)
SMALL = re.compile(r"mini|nano|flash|haiku|lite", re.I)
FAMILIES = (r"gpt-4\.1", r"claude-sonnet", r"gpt-4o", r"gemini", r"gpt-5", r"claude", r"gpt")


class LiveRunError(Exception):
    """A failure whose message is safe to show."""

    def __init__(self, message):
        super().__init__(redact(str(message)))


class MissingCredential(LiveRunError):
    pass


_known_secrets = set()


def remember(*values):
    _known_secrets.update(value for value in values if isinstance(value, str) and len(value) >= 8)


def redact(text):
    for value in _known_secrets:
        text = text.replace(value, "[redacted]")
    return text


def say(message=""):
    print(message, flush=True)


def user_env_file():
    return Path.home() / ".config" / "stalecheck" / "credentials.env"


_ENV_LINE = re.compile(r"(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)")


def usable_secret(value):
    return isinstance(value, str) and 8 <= len(value) <= 512 and all(33 <= ord(char) <= 126 for char in value)


def parse_env_text(text):
    """Only the two credential names are read, and only values that look like a single token."""
    found = {}
    for line in text.splitlines():
        match = _ENV_LINE.fullmatch(line.strip())
        if match is None or match.group(1) not in CREDENTIAL_NAMES:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if usable_secret(value):
            found[match.group(1)] = value
    return found


def read_env_file(path):
    try:
        return parse_env_text(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}


def load_credentials(environ, files):
    """The environment beats the repository .env, which beats the saved user file."""
    values = {}
    for path in reversed(files):
        values.update(read_env_file(path))
    values.update({name: environ[name] for name in CREDENTIAL_NAMES if usable_secret(environ.get(name))})
    return values


def save_credentials(path, updates):
    path = Path(path)
    merged = {**read_env_file(path), **updates}
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("".join(f"{name}={merged[name]}\n" for name in CREDENTIAL_NAMES if name in merged))
    os.replace(temporary, path)


def http_json(method, url, *, body=None, headers=None, timeout=30):
    """One JSON request; loopback calls never use a proxy. Returns (status, parsed body or None)."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/json", **({"Content-Type": "application/json"} if data is not None else {}), **(headers or {})})
    host = urllib.parse.urlsplit(url).hostname
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if host in LOOPBACK else urllib.request.build_opener()
    try:
        with opener.open(request, timeout=timeout) as response:
            status, raw = response.status, response.read(2_000_000)
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read(200_000)
    except (urllib.error.URLError, OSError) as error:
        raise LiveRunError(f"cannot reach {urllib.parse.urlsplit(url).netloc}: {getattr(error, 'reason', error)}") from None
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = None
    return status, payload


def error_message(payload, status):
    detail = None
    if isinstance(payload, dict):
        detail = payload.get("error", payload.get("detail"))
        if isinstance(detail, dict):
            detail = detail.get("message", detail.get("error"))
    return f"HTTP {status}: {redact(detail if isinstance(detail, str) and detail else 'no error text')[:300]}"


def _seconds(value, default):
    return value if type(value) is int and 1 <= value <= 60 else default


def device_sign_in(base, setup_token, *, say=say, sleep=time.sleep, clock=time.monotonic, open_url=None):
    """GitHub's device flow, run by Bifrost. Returns (GitHub token, login or None)."""
    headers = {"X-Bifrost-Setup-Token": setup_token}
    status, started = http_json("POST", f"{base}/api/providers/{PROVIDER}/sign-in", body={}, headers=headers)
    if status != 200 or not isinstance(started, dict) or not all(
            isinstance(started.get(key), str) and started[key] for key in ("device_code", "user_code", "verification_uri")):
        raise LiveRunError("GitHub sign-in could not start: " + error_message(started, status))
    remember(started["device_code"])
    uri = started["verification_uri"]
    say("Sign in to GitHub so Copilot can be used through Bifrost:")
    say(f"  1. Open {uri}")
    say(f"  2. Enter this code: {started['user_code']}")
    parts = urllib.parse.urlsplit(uri)
    if open_url is not None and parts.scheme == "https" and parts.hostname == "github.com":
        open_url(uri)
    deadline = clock() + min(started.get("expires_in") if type(started.get("expires_in")) is int else 900, 900)
    interval = _seconds(started.get("interval"), 5)
    say("Waiting for approval...")
    while True:
        sleep(interval)
        if clock() > deadline:
            raise LiveRunError("The GitHub sign-in code expired before it was approved. Run this again for a new code.")
        status, result = http_json("POST", f"{base}/api/providers/{PROVIDER}/sign-in/poll",
                                   body={"device_code": started["device_code"]}, headers=headers)
        if status != 200 or not isinstance(result, dict):
            raise LiveRunError("GitHub sign-in failed: " + error_message(result, status))
        state = result.get("status")
        if state == "pending":
            interval = max(interval, _seconds(result.get("interval"), interval))
        elif state == "complete":
            if not usable_secret(result.get("token")):
                raise LiveRunError("GitHub sign-in finished without a usable token.")
            remember(result["token"])
            login = result.get("login")
            return result["token"], login if isinstance(login, str) and login else None
        else:
            raise LiveRunError("The GitHub sign-in was " + ("denied." if state == "denied" else "not completed (code expired)."))


def check_tavily(key, base=TAVILY_API):
    """A free usage lookup: validates the key without spending a search credit."""
    status, payload = http_json("GET", base + "/usage", headers={"Authorization": f"Bearer {key}"})
    if status in (401, 403):
        raise LiveRunError("Tavily rejected this API key. Copy a key from https://app.tavily.com/home and try again.")
    summary = {"verified": status == 200, "plan": None, "credits_used": None, "credit_limit": None}
    if status == 200 and isinstance(payload, dict):
        account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        plan = account.get("current_plan")
        summary["plan"] = plan if isinstance(plan, str) else None
        for field, name in (("credits_used", "plan_usage"), ("credit_limit", "plan_limit")):
            value = account.get(name)
            summary[field] = value if type(value) in (int, float) else None
    return summary


def normalize_model(name):
    if "/" not in name:
        name = f"{PROVIDER}/{name}"
    if not name.startswith(PROVIDER + "/"):
        raise LiveRunError(f"only {PROVIDER}/... models are configured here, not {name!r}")
    return name


def rank_models(ids):
    """Usable chat models, best first: full-size before small, preferred families, newest version, shortest name."""
    names = []
    for value in ids:
        if isinstance(value, str) and value:
            name = value if value.startswith(PROVIDER + "/") else f"{PROVIDER}/{value}"
            if name not in names and not NON_CHAT.search(name.split("/", 1)[1]):
                names.append(name)

    def key(name):
        bare = name.split("/", 1)[1]
        family = next((index for index, pattern in enumerate(FAMILIES) if re.search(pattern, bare)), len(FAMILIES))
        number = re.search(r"\d+(?:\.\d+)?", bare)
        return bool(SMALL.search(bare)), family, -(float(number.group()) if number else 0.0), len(bare), bare

    return sorted(names, key=key)


def discover_models(base, virtual_key):
    status, payload = http_json("GET", f"{base}/v1/models?provider={PROVIDER}", headers={"x-bf-vk": virtual_key}, timeout=60)
    if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise LiveRunError("Copilot model discovery failed (" + error_message(payload, status) + "). Check that this GitHub account "
                           "has an active Copilot plan (https://github.com/settings/copilot) and that its organization allows "
                           f"Copilot. If a saved sign-in was revoked, delete the {COPILOT_KEY} line from {user_env_file()} and run again.")
    return [row["id"] for row in payload["data"] if isinstance(row, dict) and isinstance(row.get("id"), str)]


def _product():
    try:
        import stalecheck  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(ROOT / "src"))
    from stalecheck import gateway, stale_check, stale_check_v4
    return gateway, stale_check, stale_check_v4


def provider_error(base, virtual_key, model):
    """Bifrost's own error text for a model that failed, from one tiny request."""
    status, payload = http_json("POST", f"{base}/v1/chat/completions", headers={"x-bf-vk": virtual_key}, timeout=60, body={
        "model": model, "messages": [{"role": "user", "content": "Reply with OK."}], "temperature": 0, "max_tokens": 16, "stream": False})
    return None if status == 200 else error_message(payload, status)


def probe_model(base, virtual_key, model):
    """Run StaleCheck's real extraction and assessment prompts and accept the model only if its parsers accept the replies."""
    gateway, legacy, current = _product()
    result = {"model": model, "ok": False, "stage": "extraction", "error": None, "seconds": None, "tokens": None}
    started, tokens = time.monotonic(), 0
    try:
        client = gateway.GatewayClient(base, model, token=virtual_key, allow_network=True)
        reply = client.chat(legacy.extraction_messages(PROBE_ANSWER, 3), max_tokens=1200)
        tokens += reply["usage"]["total_tokens"] or 0
        if not legacy.parse_extraction(PROBE_ANSWER, reply["text"], 3)[0]:
            raise ValueError("the extraction reply was not accepted by StaleCheck's parser")
        result["stage"] = "assessment"
        reply = client.chat(current.assessment_messages(PROBE_CLAIM, [PROBE_SOURCE], date.today().isoformat()), max_tokens=900)
        tokens += reply["usage"]["total_tokens"] or 0
        verdict, _, _, _, verified, _ = legacy.parse_assessment([PROBE_SOURCE], reply["text"])
        if verdict != "CHANGED" or not any(item["relation"] == "contradicts" for item in verified):
            raise ValueError("the model missed an obvious price change or its quote was not verbatim")
        result.update(ok=True, stage=None)
    except gateway.GatewayError as exc:
        result["error"] = provider_error(base, virtual_key, model) or str(exc)
    except ValueError as exc:
        result["error"] = str(exc)
    result.update(seconds=round(time.monotonic() - started, 1), tokens=tokens or None)
    return result


def select_model(base, virtual_key, candidates, *, say=say, probe=probe_model):
    results = []
    for model in candidates[:MAX_CANDIDATES]:
        result = probe(base, virtual_key, model)
        results.append(result)
        if result["ok"]:
            say(f"  ok    {model}  ({result['seconds']} s, {result['tokens']} tokens)")
            return model, results
        say(f"  fail  {model}  {result['stage']}: {redact(str(result['error']))[:240]}")
    return None, results


def check_relay(base, virtual_key, relay="tavily"):
    """An empty query is rejected after authentication and costs nothing: 401 means the key failed, 400 means it was accepted."""
    status, _ = http_json("POST", f"{base}/relay/{relay}/search", headers={"x-bf-vk": virtual_key}, body={"query": ""}, timeout=60)
    if status in (401, 403):
        raise LiveRunError(f"The Bifrost relay or the Tavily key was refused (HTTP {status}).")
    if status in (429, 432, 433):
        raise LiveRunError(f"Tavily reports that this key or plan is out of credits (HTTP {status}).")
    if status >= 500:
        raise LiveRunError(f"The relay could not reach Tavily (HTTP {status}).")
    return status


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Gateway:
    """One local Bifrost container at a time. Credentials travel only through its environment."""

    def __init__(self, image, work, port=None):
        self.image, self.work, self.fixed_port = image, Path(work), port
        self.name, self.url, self.started, self.boots = f"stalecheck-copilot-{os.getpid()}", None, False, 0

    def start(self, environment):
        self.boots += 1
        data = self.work / f"data-{self.boots}"
        data.mkdir()
        shutil.copy(HERE / "config.json", data / "config.json")
        os.chmod(self.work, 0o755)
        os.chmod(data, 0o777)
        os.chmod(data / "config.json", 0o666)
        port = self.fixed_port or free_port()
        command = ["docker", "run", "--rm", "-d", "--name", self.name, "-p", f"127.0.0.1:{port}:8080",
                   "-e", "APP_HOST=0.0.0.0", "-e", "APP_PORT=8080",
                   *[part for name in sorted(environment) for part in ("-e", name)], "-v", f"{data}:/app/data", self.image]
        result = subprocess.run(command, env={**os.environ, **environment}, capture_output=True, text=True)
        if result.returncode != 0:
            raise LiveRunError("docker could not start Bifrost: " + result.stderr.strip()[-400:])
        self.started, self.url = True, f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if http_json("GET", self.url + "/health", timeout=3)[0] == 200:
                    return
            except LiveRunError:
                pass
            time.sleep(1)
        logs = subprocess.run(["docker", "logs", "--tail", "15", self.name], capture_output=True, text=True)
        raise LiveRunError("Bifrost did not become healthy in time:\n" + (logs.stdout + logs.stderr)[-1500:])

    def stop(self):
        if self.started:
            subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
            self.started = False


def require_docker():
    if shutil.which("docker") is None:
        raise LiveRunError("Docker is required to run Bifrost locally. Install Docker Desktop, start it, and run this again.")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        raise LiveRunError("Docker is installed but not running. Start Docker Desktop and run this again.")


def ensure_image(image):
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0:
        return
    say(f"Pulling {image} (first run only)...")
    if subprocess.run(["docker", "pull", image]).returncode != 0:
        raise LiveRunError(f"could not pull {image}")


def keep_new_credentials(entered, mode, interactive):
    if not entered or mode == "no" or (mode == "ask" and not interactive):
        return
    path = user_env_file()
    if mode == "ask" and input(f"Keep the new credentials for next time in {path} (owner-only file)? [y/N] ").strip().lower() not in ("y", "yes"):
        return
    save_credentials(path, entered)
    say(f"Saved to {path}. Delete that file to forget them.")


def serve_ui(args, gateway, model, virtual_key, out):
    environment = {**os.environ, "BIFROST_URL": gateway.url, "BIFROST_MODEL": model, "BIFROST_VIRTUAL_KEY": virtual_key,
                   "BIFROST_SEARCH_RELAY": "tavily", "PYTHONUNBUFFERED": "1",
                   "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    for name in CREDENTIAL_NAMES:
        environment.pop(name, None)  # the product talks only to Bifrost with a virtual key
    command = [sys.executable, "-m", "stalecheck", "serve-assessment", "--live", "--output", str(out / "assessment"), "--port", "0"]
    say("\nStarting the StaleCheck UI. No search runs until you approve the exact query. Keep this terminal open;")
    say("the launch URL below is a private local credential.\n")
    process = subprocess.Popen(command, env=environment, cwd=ROOT, stdout=subprocess.PIPE, text=True)
    try:
        for line in process.stdout:
            print(line, end="", flush=True)
            if args.open_ui and line.startswith("http://127.0.0.1:"):
                webbrowser.open(line.strip())
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def run(args):
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    credentials = load_credentials(os.environ, [ROOT / ".env", user_env_file()])
    remember(*credentials.values())
    missing = [name for name in CREDENTIAL_NAMES if name not in credentials]
    if missing and not interactive:
        raise MissingCredential(
            f"Missing {', '.join(missing)}. Export them, put them in a git-ignored .env file, or run this in a terminal to be guided "
            f"through it. {COPILOT_KEY}: a fine-grained GitHub token with the Copilot Requests permission, or let this script sign "
            f"in with GitHub. {TAVILY_KEY}: https://app.tavily.com/home")
    require_docker()
    entered = {}
    if TAVILY_KEY not in credentials:
        key = getpass.getpass("Paste your Tavily API key (input hidden; https://app.tavily.com/home): ").strip()
        if not usable_secret(key):
            raise LiveRunError("That does not look like a Tavily API key.")
        entered[TAVILY_KEY] = credentials[TAVILY_KEY] = key
        remember(key)
    tavily = check_tavily(credentials[TAVILY_KEY])
    say("Tavily: key accepted" + (f" (plan {tavily['plan']})" if tavily["plan"] else "") if tavily["verified"]
        else "Tavily: key not verified; the first search will confirm it")

    virtual_key, setup_token = "sk-bf-" + secrets.token_hex(16), secrets.token_hex(16)
    remember(virtual_key, setup_token)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = Path(args.output).absolute() if args.output else ROOT / "runs" / f"copilot-live-{stamp}"
    if out.exists():
        raise LiveRunError(f"{out} already exists; choose a new --output directory")
    work = Path(tempfile.mkdtemp(prefix="stalecheck-copilot-"))
    gateway = Gateway(args.image, work, args.port)
    try:
        ensure_image(args.image)
        shared = {TAVILY_KEY: credentials[TAVILY_KEY], "BIFROST_VIRTUAL_KEY": virtual_key, "BIFROST_SETUP_TOKEN": setup_token}
        if COPILOT_KEY not in credentials:
            gateway.start({**shared, COPILOT_KEY: PLACEHOLDER})
            token, login = device_sign_in(gateway.url, setup_token, open_url=None if args.no_browser else webbrowser.open)
            gateway.stop()
            entered[COPILOT_KEY] = credentials[COPILOT_KEY] = token
            say("Signed in to GitHub" + (f" as {login}." if login else "."))
        keep_new_credentials(entered, args.save, interactive)
        say("Starting Bifrost...")
        gateway.start({**shared, COPILOT_KEY: credentials[COPILOT_KEY]})

        say("Choosing a Copilot model (each candidate must satisfy StaleCheck's real prompts and parsers):")
        candidates = [normalize_model(args.model)] if args.model else rank_models(discover_models(gateway.url, virtual_key))
        if not candidates:
            raise LiveRunError("No chat model is available for this Copilot seat. Check its plan and organization policy.")
        model, probes = select_model(gateway.url, virtual_key, candidates)
        if model is None:
            raise LiveRunError("No Copilot model passed StaleCheck's compatibility probe. Try another with --model github-copilot/<name>.")
        relay_status = check_relay(gateway.url, virtual_key)
        evidence = {"schema": "copilot-live-evidence-1", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "bifrost_image": args.image, "provider": PROVIDER, "model": model, "probes": probes,
                    "tavily": {**tavily, "relay": "tavily", "relay_auth_check_status": relay_status},
                    "notes": ["Copilot requests count against the seat's allowance; Bifrost may report their dollar cost as zero.",
                              "Probes used a synthetic answer and source; no real search was run by this script."]}
        out.mkdir(parents=True)
        (out / "live-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        say(f"\nLive path verified: Copilot model {model}; Tavily key accepted by the Bifrost relay.")
        say(f"Evidence (no secrets): {out / 'live-evidence.json'}")
        say("Copilot requests count against your seat's allowance; Bifrost may report their cost as $0.")
        if args.check_only:
            return 0
        return serve_ui(args, gateway, model, virtual_key, out)
    finally:
        gateway.stop()
        shutil.rmtree(work, ignore_errors=True)


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="run.sh", description="Run StaleCheck live with GitHub Copilot (through Bifrost) and Tavily.")
    parser.add_argument("--check-only", action="store_true", help="verify Copilot, Tavily and the relay, write live-evidence.json, then stop")
    parser.add_argument("--model", default=os.environ.get("LIVE_MODEL"), help="use this Copilot model (default: probe the best available)")
    parser.add_argument("--image", default=os.environ.get("BIFROST_IMAGE", IMAGE), help="Bifrost image (default: %(default)s)")
    parser.add_argument("--port", type=int, default=int(os.environ["GATEWAY_PORT"]) if os.environ.get("GATEWAY_PORT", "").isdigit() else None,
                        help="local Bifrost port (default: any free port)")
    parser.add_argument("--output", default=os.environ.get("OUT_DIR"), help="new run directory (default: runs/copilot-live-<time>)")
    parser.add_argument("--save", choices=("ask", "yes", "no"), default="ask", help="keep newly entered credentials in the owner-only user file")
    parser.add_argument("--no-browser", action="store_true", help="do not open GitHub's device sign-in page automatically")
    parser.add_argument("--open-ui", action="store_true", help="open the StaleCheck launch URL in your browser")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    try:
        return run(args)
    except MissingCredential as error:
        print(f"\n{error}", file=sys.stderr)
        return 2
    except LiveRunError as error:
        print(f"\nStopped: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
