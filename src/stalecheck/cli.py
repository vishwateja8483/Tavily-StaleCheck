"""Offline verification, mock acquisition and an opt-in live stale-answer check.

Credentials are read only by `stale-check --live`, and only from environment variables the caller names.
"""
import argparse
from datetime import datetime, timezone
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import sys
from .contracts import MAX_FILE, ValidationError, decode, json_bytes, read_json, require, safe_path
from .report import write_run
from .verify_artifacts import verify_artifacts


def _env(name):
    require(isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) is not None, "invalid environment variable name")
    value = os.environ.get(name)
    require(bool(value), f"environment variable {name} is empty or unset")
    return value


def _stale_check(args):
    if args.transcript or args.demo or args.demo_v4:
        require(not args.live, "--live conflicts with offline replay; replay never calls a service")
        fixture = "stale-check-v4-demo.json" if args.demo_v4 else "stale-check-demo.json"
        recorded = decode(files("stalecheck").joinpath("fixtures/" + fixture).read_bytes()) if args.demo or args.demo_v4 else read_json(args.transcript)
        require(isinstance(recorded, dict) and recorded.get("schema_version") in ("3.0", "4.0"), "stale-check replays only schema 3.0/4.0 stale-answer transcripts")
        result = write_run(recorded, args.output)
    else:
        require(args.live and args.confirm_outbound, "live checking needs --live and --confirm-outbound: the full answer and selected claims/source excerpts go to Bifrost; approved queries go to Tavily")
        require(args.model and args.primary_domain, "live checking needs --model and at least one --primary-domain")
        from .gateway import BifrostRelayTransport, GatewayClient
        from .stale_check_v4 import collect_transcript, validate_inputs
        from .export import write_exclusive_json
        with Path(args.answer).open("rb") as stream:
            raw = stream.read(MAX_FILE + 1)
        require(len(raw) <= MAX_FILE, "answer file exceeds size bound")
        try:
            answer = raw.decode("utf-8-sig").strip()
        except UnicodeDecodeError:
            raise ValidationError("answer file must be UTF-8") from None
        today = datetime.now(timezone.utc).date().isoformat()
        verification_date = args.verification_date or today
        validate_inputs(answer, verification_date, args.answer_date, args.primary_domain, args.max_claims, args.max_attempts, args.max_seconds)
        # Validate model/origin/fallbacks without accessing credentials or making a request.
        GatewayClient(args.gateway_url, args.model, fallbacks=args.fallback, allow_network=True, timeout=args.gateway_timeout)
        relay = BifrostRelayTransport(args.gateway_url, args.search_relay) if args.search_relay else None
        require(relay is None or args.gateway_token_env, "--search-relay needs --gateway-token-env: the Bifrost virtual key authorises the relay")
        root = Path(args.output).absolute()
        safe_path(root.parent, root.name)
        require(root.parent.is_dir() and not root.exists(), "live output must be new and its parent must exist")
        approved = read_json(args.approval) if args.approval else None
        if approved is not None:
            require(isinstance(approved, dict) and set(approved) == {"queries", "primary_domains", "sha256", "approved"}
                    and approved["approved"] is True, "invalid supplied query approval")
        root.mkdir(exist_ok=False)  # Reserve before credentials or billable work; never overwrite.
        write_exclusive_json({"status": "incomplete", "format": "stale-check-session-1"}, root / "session.json")
        sequence = 0

        def checkpoint(value):
            nonlocal sequence
            sequence += 1
            write_exclusive_json(value, root / f"checkpoint-{sequence:03d}.json")

        def approve(request):
            print("Exact outbound Tavily query/domain request:\n" + json.dumps(request, ensure_ascii=True, indent=2), file=sys.stderr)
            if approved is not None:
                return approved == {**request, "approved": True}
            if not sys.stdin.isatty():
                return False
            print("Type APPROVE to attest these exact queries are public and send them; anything else declines: ", end="", file=sys.stderr, flush=True)
            try:
                return input() == "APPROVE"
            except EOFError:
                return False

        try:
            tavily = (f"Exact approved queries/domains also go to Bifrost, which relays them to Tavily (relay '{args.search_relay}')." if relay
                      else "Only exact approved queries/domains go to Tavily.")
            print("Outbound disclosure: the FULL answer goes to Bifrost for extraction; selected claims and source excerpts go there for assessment. " + tavily, file=sys.stderr)
            token = _env(args.gateway_token_env) if args.gateway_token_env else None
            if relay is None:
                key = _env(args.tavily_key_env)
                require(0 < len(key) <= 512 and all(33 <= ord(c) <= 126 for c in key), "invalid Tavily token")
            else:
                key = token  # The virtual key authorises the relay; the Tavily key never reaches this process.
            gateway = GatewayClient(args.gateway_url, args.model, fallbacks=args.fallback, token=token, allow_network=True, timeout=args.gateway_timeout)
            transcript = collect_transcript(answer, verification_date=verification_date, primary_domains=args.primary_domain, gateway=gateway,
                                            search_key=key, search_transport=relay, allow_network=True, answer_date=args.answer_date, max_claims=args.max_claims,
                                            max_attempts=args.max_attempts, max_seconds=args.max_seconds, approve=approve, checkpoint=checkpoint)
            result = write_run(transcript, root / "run")
            write_exclusive_json({"status": "recorded", "stop_reason": transcript["stop_reason"], "run": "run"}, root / "completion.json")
        except BaseException:
            write_exclusive_json({"status": "incomplete", "error": "collection interrupted or failed; checkpoints retained; no automatic retry"}, root / "failure.json")
            raise
    report = read_json(Path(result["output"]) / "report.json")
    return {**result, "summary": report["summary"], "counts": report["counts"], "usage": report["usage"]}


def _stale_report(args):
    from .stale_check import render_html
    from .verify_artifacts import load_verified_run
    snapshot = load_verified_run(args.directory)
    require(snapshot["input"]["schema_version"] in ("3.0", "4.0"), "stale-report renders only stale-answer runs")
    if snapshot["input"]["schema_version"] == "4.0":
        from .stale_check_v4 import render_html
    raw = render_html(snapshot["report"]).encode("utf-8")
    require(len(raw) <= MAX_FILE, "rendered report exceeds size bound")
    output = Path(args.output).absolute()
    safe_path(output.parent, output.name)
    with output.open("xb") as stream:
        stream.write(raw)
    return {"status": "rendered", "output": str(output), "bytes": len(raw), "run_id": snapshot["report"]["run_id"]}


def _serve_assessment(args):
    from .web_jobs import JobManager, SETTINGS
    from .web_service import AssessmentServer
    # Only documented names; demo does not inspect credentials.
    configuration = {key: os.environ.get(key) for key in SETTINGS} if args.live else None
    server = AssessmentServer.create(
        lambda: JobManager(args.output, live=args.live, configuration=configuration), port=args.port)
    try:
        # Fragment never enters an HTTP request. Keep this handoff out of published logs.
        print('Open this private local launch URL (keep this terminal open):', flush=True)
        print(server.launch_url, flush=True)
        print('Mode: ' + server.manager.mode + '; live compatibility is not established by configuration presence.', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {'status': 'closed', 'unfinished_work': 'never resumed; checkpoints retained'}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="stalecheck", description="Frozen temporal verification; outbound providers require stale-check --live or serve-assessment --live with explicit consent.")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser('serve-assessment', help='Launch authenticated loopback assessment UI; explicit demo by default')
    serve.add_argument('--output', required=True, help='New launch directory; parent must already exist')
    serve.add_argument('--port', type=int, default=4190, help='127.0.0.1 port only; 0 chooses a free port')
    serve.add_argument('--live', action='store_true', help='Opt into Bifrost/Tavily; reads only BIFROST_URL, BIFROST_MODEL, BIFROST_VIRTUAL_KEY and either TAVILY_API_KEY or BIFROST_SEARCH_RELAY (a Bifrost relay name that keeps the Tavily key in the gateway)')
    demo = commands.add_parser("demo", help="Run clearly synthetic frozen evidence")
    demo.add_argument("--output", required=True)
    verify = commands.add_parser("verify", help="Verify answer text or reviewed public lifecycle JSON")
    verify.add_argument("--input", required=True)
    verify.add_argument("--output", required=True)
    artifacts = commands.add_parser("verify-artifacts", help="Check artifact integrity and domain consistency offline")
    artifacts.add_argument("directory")
    commands.add_parser("doctor", help="Report offline capability; no network/credential checks")
    check = commands.add_parser("stale-check", help="Check an AI answer for stale claims: replay a recorded transcript offline, or run once live through Bifrost and Tavily")
    check.add_argument("--output", required=True)
    origin = check.add_mutually_exclusive_group(required=True)
    origin.add_argument("--transcript", help="Recorded transcript JSON; offline replay, no keys, no network")
    origin.add_argument("--demo", action="store_true", help="Replay the packaged synthetic transcript (scripted mock replies; no keys, no network)")
    origin.add_argument("--demo-v4", action="store_true", help="Replay the consent-gated v4 synthetic transcript; no network")
    origin.add_argument("--answer", help="UTF-8 file holding the answer to check; live, needs --live --confirm-outbound")
    check.add_argument("--approval", help="Exact query/domain approval JSON; mismatch or noninteractive absence declines research")
    check.add_argument("--max-attempts", type=int, default=25, help="Whole-journey outbound attempt ceiling (1-25)")
    check.add_argument("--max-seconds", type=float, default=180, help="Cooperative journey deadline, at most 180 seconds")
    check.add_argument("--live", action="store_true", help="Allow Bifrost and Tavily calls")
    check.add_argument("--confirm-outbound", action="store_true", help="Confirm full-answer upload to Bifrost, followed by selected claims/source excerpts; Tavily queries need separate exact approval")
    check.add_argument("--primary-domain", action="append", default=[], help="Repeatable; Tavily results are restricted to these reviewed domains")
    check.add_argument("--verification-date", help="YYYY-MM-DD; default today (UTC)")
    check.add_argument("--answer-date", help="YYYY-MM-DD when the answer was produced")
    check.add_argument("--max-claims", type=int, default=5, help="Upper bound on checked claims (1-8)")
    check.add_argument("--gateway-url", default="http://127.0.0.1:8080", help="Bifrost origin; plain HTTP only for loopback")
    check.add_argument("--model", help="Bifrost model as provider/model")
    check.add_argument("--fallback", action="append", default=[], help="Repeatable Bifrost fallback model (at most 3)")
    check.add_argument("--gateway-token-env", help="Name of the environment variable holding a Bifrost virtual key (sk-bf-...)")
    check.add_argument("--gateway-timeout", type=float, default=60, help="Seconds allowed for each model call (1-180, default 60). Raise it for a slow local model and "
                       "keep the gateway's own provider timeout just below it; the journey deadline still applies")
    check.add_argument("--tavily-key-env", default="TAVILY_API_KEY", help="Name of the environment variable holding the Tavily API key")
    check.add_argument("--search-relay", metavar="NAME", help="Send Tavily searches through this Bifrost relay (<gateway>/relay/NAME/search) instead of calling Tavily directly; "
                       "the virtual key from --gateway-token-env authorises it and the Tavily key never reaches this process")
    stale_report = commands.add_parser("stale-report", help="Render a verified stale-answer run as one static HTML file")
    stale_report.add_argument("directory")
    stale_report.add_argument("--output", required=True)
    mock = commands.add_parser("acquire-mock", help="Exercise Tavily acquisition with local HTTP fixtures; never network")
    mock.add_argument("--input", required=True, help="Reviewed public query/URL plan")
    mock.add_argument("--responses", required=True, help="Ordered local mock response array")
    review = commands.add_parser("review-evidence", help="Record an explicit supplied human attestation offline; not a verdict")
    review.add_argument("--acquisition", required=True)
    review.add_argument("--proposal", required=True)
    review.add_argument("--previous-review")
    review.add_argument("--output", required=True)
    review_check = commands.add_parser("verify-review", help="Check review consistency, not authorship or entailment")
    review_check.add_argument("--input", required=True)
    review_check.add_argument("--acquisition")
    review_check.add_argument("--previous-review")
    conversion = commands.add_parser("review-to-evidence", help="Convert explicitly supplied validated attestations to v2 evidence")
    conversion.add_argument("--input", required=True)
    conversion.add_argument("--output", required=True)
    export = commands.add_parser("export-viewer", help="Export own verified runs, maximum 5 MiB, never overwrite")
    export.add_argument("directories", nargs="+")
    export.add_argument("--output", required=True)
    for name in ("compare", "verify-comparison"):
        comparison = commands.add_parser(name, help="Compare independently verified frozen runs" if name == "compare" else "Recompute retained comparison")
        comparison.add_argument("--before", required=True)
        comparison.add_argument("--after", required=True)
        comparison.add_argument("--output" if name == "compare" else "--input", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'serve-assessment':
            result = _serve_assessment(args)
        elif args.command == "doctor":
            result = {"project_id": "stalecheck", "offline_ready": True, "python": sys.version.split()[0], "mode": "replay", "live_retrieval": "explicit opt-in; connectivity not checked by doctor", "model": "none", "credential_access": False, "network_access": False, "live_opt_in": "stale-check --live --confirm-outbound or serve-assessment --live; answer-processing consent and exact-query approval required; doctor does not read credentials or test providers"}
        elif args.command == "stale-check":
            result = _stale_check(args)
        elif args.command == "stale-report":
            result = _stale_report(args)
        elif args.command == "acquire-mock":
            from .acquisition import MockTransport, acquire
            transport = MockTransport(read_json(args.responses))
            result = acquire(read_json(args.input), transport=transport)
            if transport.fixtures:
                raise ValidationError("unused mock responses")
        elif args.command in {"review-evidence", "verify-review"}:
            from .review import make_review, validate_review
            previous = read_json(args.previous_review) if args.previous_review else None
            acquisition = read_json(args.acquisition) if args.acquisition else None
            if args.command == "review-evidence":
                document = make_review(acquisition, read_json(args.proposal), previous_review=previous)
                raw = json_bytes(document)
                require(len(raw) <= MAX_FILE, "review exceeds size bound")
                output = Path(args.output).absolute()
                safe_path(output.parent, output.name)
                # Review creation never overwrites acquisitions or earlier reviews.
                with output.open("xb") as stream:
                    stream.write(raw)
                result = {"status": "review_recorded", "output": str(output), "verification_performed": False,
                          "assurance": "Supplied human attestation recorded; no authentication or entailment verification."}
            else:
                validate_review(read_json(args.input), acquisition=acquisition, previous_review=previous)
                result = {"valid": True, "verification_performed": False,
                          "assurance": "Review consistency only; not authenticated authorship, temporal verdict, or source truth."}
        elif args.command == "review-to-evidence":
            from .reviewed_evidence import convert_reviews
            from .export import write_exclusive_json
            result = {"status": "converted", **write_exclusive_json(convert_reviews(read_json(args.input)), args.output)}
        elif args.command == "export-viewer":
            from .export import export_viewer
            result = export_viewer(args.directories, args.output)
        elif args.command in {"compare", "verify-comparison"}:
            from .comparison import make_comparison, verify_comparison
            from .export import write_exclusive_json
            result = ({"status": "compared", **write_exclusive_json(make_comparison(args.before, args.after), args.output)} if args.command == "compare" else verify_comparison(read_json(args.input), args.before, args.after))
        elif args.command == "verify-artifacts":
            result = verify_artifacts(args.directory)
        else:
            value = decode(files("stalecheck").joinpath("fixtures/demo.json").read_bytes()) if args.command == "demo" else read_json(args.input)
            result = write_run(value, args.output)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ValidationError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        error = {"status": "error", "error": str(exc)}
        if hasattr(exc, "partial_result"):
            error["partial_result"] = exc.partial_result
        print(json.dumps(error, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
