"""Exercise StaleCheck against a real Bifrost gateway whose model and Tavily upstreams are local stand-ins.

run.sh starts the gateway container and stub_upstreams.py, then runs this file. Every check prints PASS or
FAIL and the exit status is 1 if any check fails. No real model or Tavily provider is called and no real
credential is read: every key below is a made-up value that exists only in config.json.
"""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from stalecheck.acquisition import acquire  # noqa: E402
from stalecheck.gateway import BifrostRelayTransport, GatewayClient, GatewayError  # noqa: E402
from stalecheck.stale_check import extraction_messages  # noqa: E402
from stalecheck.verify_artifacts import verify_artifacts  # noqa: E402

DEMO_KEY = "sk-bf-demo-key-0123456789"                 # virtual key "vk-demo" (run.sh gives the gateway the same value as GATEWAY_CHECK_VK_DEMO): both providers, the tavily relay, a budget
LLM_ONLY_KEY = "sk-bf-llm-only-key-0123456789"        # virtual key "vk-no-relay": stub-primary only, no relay grant
TAVILY_SECRET = "stub-tavily-secret"                   # credential the gateway holds for the relay; the app never sees it
PROVIDER_KEY = "stub-llm-key"                          # provider key the gateway holds for the two model stubs
ANSWER = "Acme Cloud's Starter plan costs $19 per month. The Acme SDK is at version 4.2."
PRIMARY, BACKUP = "stub-primary/demo-model", "stub-backup/demo-model"
CREDIT_PRICE, BUDGET = 0.008, 0.045                    # relay cost per Tavily credit and the budget cap in config.json
SEARCH = {"query": "acme cloud starter plan pricing", "topic": "general", "search_depth": "basic", "max_results": 5,
          "include_domains": ["docs.example.com"], "include_domains_mode": "restrict", "include_usage": True, "include_published_date": True}
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(("PASS  " if condition else "FAIL  ") + name + ("" if condition or not detail else "\n        " + str(detail)[:600]), flush=True)


def call(method, url, body=None, headers=None):
    request = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with OPENER.open(request, timeout=20) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


class Stubs:
    ports = {"primary": 9101, "backup": 9102}

    def __init__(self, log):
        self.log = Path(log)

    def entries(self, service=None, since=0):
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line]
        return [row for row in rows[since:] if service is None or row["service"].startswith(service)]

    def mark(self):
        return len(self.entries())

    def set_mode(self, which, mode):
        call("POST", f"http://127.0.0.1:{self.ports[which]}/_admin/mode", {"mode": mode})


def gateway_client(url, key, model=PRIMARY, fallbacks=()):
    return GatewayClient(url, model, fallbacks=list(fallbacks), token=key, allow_network=True)


def refusal(url, key, model=PRIMARY):
    try:
        gateway_client(url, key, model).chat(extraction_messages(ANSWER, 5))
    except GatewayError as error:
        return error.status, error.kind
    return None, None


def check_chat(url, stubs):
    messages = extraction_messages(ANSWER, 5)
    mark = stubs.mark()
    reply = gateway_client(url, DEMO_KEY, fallbacks=[BACKUP]).chat(messages)
    check("chat through the gateway is served by the primary provider", reply["served_by"] == {"provider": "stub-primary", "model": "demo-model", "is_fallback": False}, reply["served_by"])
    check("the reply carries integer token usage and the JSON the model wrote", all(type(v) is int for v in reply["usage"].values()) and len(json.loads(reply["text"])["claims"]) == 2, reply["usage"])
    stubs.set_mode("primary", "down")
    try:
        fallback = gateway_client(url, DEMO_KEY, fallbacks=[BACKUP]).chat(messages)
    finally:
        stubs.set_mode("primary", "up")
    check("a primary outage is served by the fallback and reported as a takeover", fallback["served_by"] == {"provider": "stub-backup", "model": "demo-model", "is_fallback": True}, fallback["served_by"])
    seen = stubs.entries("llm", mark)
    check("model upstreams receive the gateway-held provider key and never the virtual key",
          bool(seen) and all(e["authorization"] == "Bearer " + PROVIDER_KEY and not e["x_bf_vk_forwarded"] for e in seen), [e["authorization"] for e in seen])
    for name, key, model, expected in (("no virtual key", None, PRIMARY, (401, "virtual_key_required")), ("an unknown virtual key", "sk-bf-no-such-key", PRIMARY, (401, "access_not_found")),
                                       ("a virtual key without the provider", LLM_ONLY_KEY, BACKUP, (403, "provider_blocked"))):
        got = refusal(url, key, model)
        check(f"{name} is refused for chat as {expected[1]} (HTTP {expected[0]})", got == expected, got)


def check_relay(url, stubs):
    mark = stubs.mark()
    plan = {"schema_version": "1.0", "format": "reviewed_public_acquisition", "public_reviewed": True,
            "queries": [SEARCH["query"]], "urls": [], "primary_domains": ["docs.example.com"]}
    result = acquire(plan, allow_network=True, api_key=DEMO_KEY, transport=BifrostRelayTransport(url, "tavily"))
    candidate = result["candidates"][0]
    check("a search through the relay returns Tavily's own result", candidate["url"] == "https://docs.example.com/acme/pricing" and "$24 per month" in candidate["content"] and candidate["published_at"], candidate)
    check("the relayed answer carries Tavily's credit usage", result["requests"][0]["provider_usage"] == {"credits": 1}, result["requests"])
    seen = stubs.entries("tavily", mark)
    check("Tavily is reached once, with only the gateway-held secret and no virtual key",
          len(seen) == 1 and seen[0]["authorization"] == "Bearer " + TAVILY_SECRET and not seen[0]["x_bf_vk_forwarded"] and seen[0]["path"] == "/search", seen)
    for name, headers, expected in (("a virtual key without a relay grant", {"x-bf-vk": LLM_ONLY_KEY}, (403, "relay_blocked")), ("no virtual key", {}, (401, "virtual_key_required")),
                                    ("an unknown virtual key", {"x-bf-vk": "sk-bf-no-such-key"}, (401, "access_not_found"))):
        status, body = call("POST", url + "/relay/tavily/search", SEARCH, headers)
        check(f"the relay refuses {name} as {expected[1]} (HTTP {expected[0]})", (status, body.get("type")) == expected, (status, body))
    status, body = call("POST", url + "/relay/tavily/extract", {"urls": ["https://docs.example.com/"]}, {"x-bf-vk": DEMO_KEY})
    check("only the configured route is relayed: /extract is refused (HTTP 404)", status == 404 and "no route" in body["error"]["message"], (status, body))
    check("a refused call never reaches Tavily", len(stubs.entries("tavily", mark)) == 1)


def stalecheck(args, key, answer, out):
    env = {**os.environ, "PYTHONPATH": str(SRC), "FD_CHECK_VK": key}
    for name in ("TAVILY_API_KEY", "BIFROST_VIRTUAL_KEY", "BIFROST_URL", "BIFROST_MODEL"):
        env.pop(name, None)  # the journey must work with no Tavily key in the environment
    command = ["stale-check", "--answer", str(answer), "--live", "--confirm-outbound", "--primary-domain", "docs.example.com", "--verification-date", "2026-11-01",
               "--max-claims", "2", "--gateway-token-env", "FD_CHECK_VK", "--search-relay", "tavily", *args, "--output", str(out)]
    return subprocess.run([sys.executable, "-c", "import sys; from stalecheck.cli import main; sys.exit(main(sys.argv[1:]))", *command],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env, timeout=180)


def printed_request(stderr):
    marker = "Exact outbound Tavily query/domain request:\n"
    return json.JSONDecoder().raw_decode(stderr[stderr.index(marker) + len(marker):])[0]


def completion(out):
    return json.loads((out / "completion.json").read_text(encoding="utf-8"))


def stored_text(out):
    return "".join(path.read_text(encoding="utf-8", errors="replace") for path in out.rglob("*") if path.is_file())


def check_journeys(url, stubs, work):
    answer = work / "answer.txt"
    answer.write_text(ANSWER, encoding="utf-8")
    common = ["--gateway-url", url, "--model", PRIMARY]
    mark = stubs.mark()
    declined = stalecheck([*common, "--fallback", BACKUP], DEMO_KEY, answer, work / "declined")
    request = printed_request(declined.stderr)
    check("journey 1: without approval only the extraction call is made and no search is sent",
          declined.returncode == 0 and completion(work / "declined")["stop_reason"] == "approval_declined" and len(stubs.entries("llm", mark)) == 1 and not stubs.entries("tavily", mark),
          declined.stderr[-400:])
    check("journey 1: the exact queries are shown before anything is sent", request["queries"] == ["acme cloud starter plan pricing", "acme sdk latest release version"], request)
    approval = work / "approval.json"
    approval.write_text(json.dumps({**request, "approved": True}), encoding="utf-8")
    mark = stubs.mark()
    done = stalecheck([*common, "--fallback", BACKUP, "--approval", str(approval)], DEMO_KEY, answer, work / "approved")
    summary = json.loads(done.stdout) if done.returncode == 0 else {}
    usage = summary.get("usage", {})
    check("journey 2: the approved journey finishes with a verdict per claim (one changed, one supported)",
          summary.get("counts") == {"MODEL_SUPPORTED": 1, "POTENTIALLY_CHANGED": 1, "UNVERIFIABLE": 0}, done.stderr[-600:] or summary.get("counts"))
    check("journey 2: three gateway calls, two relayed searches, two credits, no fallback, routing known for every call",
          usage.get("gateway_calls") == 3 and usage.get("search_calls") == 2 and usage.get("credits", {}).get("known_subtotal") == {"credits": 2}
          and usage.get("routing") == {"observed_fallbacks": 0, "unknown_calls": 0} and usage.get("provenance") == {"gateway": "bifrost", "search": "tavily"}, usage)
    searches = stubs.entries("tavily", mark)
    check("journey 2: Tavily saw two searches, each with only the gateway-held secret",
          len(searches) == 2 and all(e["authorization"] == "Bearer " + TAVILY_SECRET and not e["x_bf_vk_forwarded"] for e in searches), searches)
    check("journey 2: the written run verifies offline", verify_artifacts(work / "approved" / "run")["valid"] is True)
    text = stored_text(work / "approved")
    check("journey 2: no key of any kind was stored", all(secret not in text for secret in (DEMO_KEY, TAVILY_SECRET, PROVIDER_KEY)))
    mark = stubs.mark()
    blocked = stalecheck([*common, "--approval", str(approval)], LLM_ONLY_KEY, answer, work / "blocked")
    check("journey 3: a key without the relay grant is stopped at the first search, which never reaches Tavily",
          blocked.returncode == 0 and completion(work / "blocked")["stop_reason"] == "search_failed" and not stubs.entries("tavily", mark), blocked.stderr[-400:])
    return approval


def check_budget(url, stubs, work, approval, spent):
    status, body = 200, {}
    while status == 200 and spent < 12:
        status, body = call("POST", url + "/relay/tavily/search", SEARCH, {"x-bf-vk": DEMO_KEY})
        spent += status == 200
    expected = math.ceil(BUDGET / CREDIT_PRICE)
    check(f"the virtual key's budget stops the relay: {expected} searches succeed, the next is refused as budget_exceeded (HTTP 402)",
          (status, body.get("type"), spent) == (402, "budget_exceeded", expected), (status, body, spent))
    mark = stubs.mark()
    answer = work / "answer.txt"
    stopped = stalecheck(["--gateway-url", url, "--model", PRIMARY, "--fallback", BACKUP, "--approval", str(approval)], DEMO_KEY, answer, work / "budget")
    check("journey 4: once the budget is spent the same key is refused for model calls too, so the journey stops before any upstream call",
          stopped.returncode == 0 and completion(work / "budget")["stop_reason"] == "gateway_failed"
          and not stubs.entries("llm", mark) and not stubs.entries("tavily", mark), stopped.stderr[-400:])
    return spent


def check_accounting(url, token, spent):
    headers = {"X-Bifrost-Setup-Token": token}
    cost = spent * CREDIT_PRICE
    stats, usage = {}, None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:  # the gateway writes logs and budget counters in batches
        _, stats = call("GET", url + "/api/relay-logs/stats", headers=headers)
        _, budgets = call("GET", url + "/api/governance/budgets", headers=headers)
        usage = budgets["budgets"][0]["current_usage"] if budgets.get("budgets") else None
        if stats.get("success_count") == spent and usage is not None and abs(usage - cost) < 1e-9:
            break
        time.sleep(1)
    check("Relay Logs count every successful search and bill one credit each", stats.get("success_count") == spent and stats.get("total_units") == spent and abs(stats.get("total_cost", 0) - cost) < 1e-9, stats)
    check("refused relay calls are logged as errors and cost nothing", stats.get("error_count", 0) >= 4 and abs(stats.get("total_cost", 0) - cost) < 1e-9, stats)
    check("the virtual key's budget counter equals the relay spend", usage is not None and abs(usage - cost) < 1e-9, usage)


def phase(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception as error:  # a crashed phase is a failed check; the remaining phases still run
        check(f"{function.__name__} ran to completion", False, f"{type(error).__name__}: {error}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--stub-log", required=True)
    parser.add_argument("--setup-token", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--json", help="write the PASS/FAIL list here")
    args = parser.parse_args()
    url, stubs, work = args.gateway.rstrip("/"), Stubs(args.stub_log), Path(args.work)
    phase(check_chat, url, stubs)
    phase(check_relay, url, stubs)
    approval = phase(check_journeys, url, stubs, work)
    spent = phase(check_budget, url, stubs, work, approval, spent=3)
    phase(check_accounting, url, args.setup_token, spent or 0)
    failed = [name for name, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed", flush=True)
    if args.json:
        Path(args.json).write_text(json.dumps([{"check": name, "passed": ok} for name, ok in RESULTS], indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
