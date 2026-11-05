#!/usr/bin/env python3
"""Run one live StaleCheck journey through a Bifrost gateway and print a summary that holds no secrets.

The CLI asks for the literal word APPROVE before any Tavily call. With --approve this script types it for
the operator, but only after the CLI has printed the exact queries and domains it is about to send.
Needs a POSIX pseudo-terminal (Linux, macOS or WSL).
"""
import argparse
import json
import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path

PROMPT = b"Type APPROVE"


def run_in_terminal(arguments, *, approve, timeout):
    """Run the CLI on a pseudo-terminal so its interactive approval prompt works; return (exit code, output)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(sys.executable, [sys.executable, "-m", "stalecheck", *arguments])
    chunks, answered, deadline = [], False, time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not select.select([fd], [], [], 1.0)[0]:
            continue
        try:
            data = os.read(fd, 4096)
        except OSError:  # EIO once the child has closed the terminal
            break
        if not data:
            break
        chunks.append(data)
        if approve and not answered and PROMPT in b"".join(chunks):
            os.write(fd, b"APPROVE\n")
            answered = True
    else:
        os.kill(pid, 15)
    return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]), b"".join(chunks).decode("utf-8", "replace")


def module(*arguments):
    done = subprocess.run([sys.executable, "-m", "stalecheck", *arguments], capture_output=True, text=True, encoding="utf-8")
    return done.returncode, (done.stdout + done.stderr).strip()


def leaked(directory, secrets):
    """Count files under directory that contain any of the secret values (the values are never printed)."""
    values = [s.encode() for s in secrets if s]
    return sum(any(v in path.read_bytes() for v in values) for path in Path(directory).rglob("*") if path.is_file())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--model", required=True, help="Bifrost model as provider/model, for example ollama/qwen2.5:3b")
    parser.add_argument("--answer", required=True)
    parser.add_argument("--primary-domain", action="append", required=True)
    parser.add_argument("--max-claims", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=180)
    parser.add_argument("--gateway-timeout", type=float, help="seconds allowed for each model call (the CLI default is 60); raise it for a slow local model")
    parser.add_argument("--search-relay", metavar="NAME", help="send Tavily searches through this Bifrost relay instead of calling Tavily directly")
    parser.add_argument("--vk-env", default="BIFROST_VIRTUAL_KEY")
    parser.add_argument("--tavily-env", default="TAVILY_API_KEY")
    parser.add_argument("--output", required=True, help="new directory; its parent must exist")
    parser.add_argument("--approve", action="store_true", help="type APPROVE once the exact queries have been printed")
    parser.add_argument("--json", metavar="FILE", help="also write the summary as JSON")
    args = parser.parse_args()

    cli = ["stale-check", "--answer", args.answer, "--live", "--confirm-outbound", "--gateway-url", args.gateway, "--model", args.model,
           "--gateway-token-env", args.vk_env, "--max-claims", str(args.max_claims), "--max-seconds", str(args.max_seconds),
           "--output", str(Path(args.output) / "session")]
    cli += ["--search-relay", args.search_relay] if args.search_relay else ["--tavily-key-env", args.tavily_env]
    if args.gateway_timeout:
        cli += ["--gateway-timeout", str(args.gateway_timeout)]
    for domain in args.primary_domain:
        cli += ["--primary-domain", domain]
    Path(args.output).mkdir(parents=True, exist_ok=False)

    started = time.monotonic()
    code, output = run_in_terminal(cli, approve=args.approve, timeout=args.max_seconds + 120)
    elapsed = time.monotonic() - started
    print(f"--- CLI output (exit {code}, {elapsed:.0f}s) ---")
    print(output.strip())

    run = Path(args.output) / "session" / "run"
    summary = {"exit_code": code, "seconds": round(elapsed, 1), "model": args.model,
               "search": f"relay:{args.search_relay}" if args.search_relay else "direct", "recorded": (run / "report.json").is_file()}
    if summary["recorded"]:
        report = json.loads((run / "report.json").read_text("utf-8"))
        summary["verify_artifacts"] = module("verify-artifacts", str(run))[0] == 0
        html = Path(args.output) / "report.html"
        summary["report_html"] = str(html) if module("stale-report", str(run), "--output", str(html))[0] == 0 else None
        summary.update(stop_reason=report["stop_reason"], counts=report["counts"], usage=report["usage"], rejected=report["rejected_proposals"],
                       claims=[{"id": c["id"], "text": c["text"], "verdict": c["verdict"], "reason_code": c["reason_code"], "quotes": len(c["evidence"]),
                                "freshness": c["freshness"]["score"], "rewrite": c["inference"]["suggested_rewrite"]} for c in report["claims"]])
    summary["files_containing_a_key"] = leaked(args.output, [os.environ.get(args.vk_env), os.environ.get(args.tavily_env)])
    print("--- summary ---")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if summary["recorded"] and summary.get("verify_artifacts") and summary["files_containing_a_key"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
