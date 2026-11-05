"""Live smoke test of StaleCheck's Tavily adapter: reviewed public Search queries, one credit each.

The key comes from the environment variable you name and is never printed or stored. With --relay the
searches go through a Bifrost relay instead, authorised by a Bifrost virtual key (see
docs/BIFROST-GATEWAY.md).

    export TAVILY_API_KEY=...
    python scripts/tavily-live-smoke.py
    python scripts/tavily-live-smoke.py --relay tavily --gateway-url http://127.0.0.1:8080 --token-env BIFROST_VIRTUAL_KEY
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from stalecheck.acquisition import AcquisitionError, acquire  # noqa: E402
from stalecheck.gateway import BifrostRelayTransport  # noqa: E402

QUERIES = [("Tavily Search API pricing and credits", ["docs.tavily.com", "tavily.com"]),
           ("Python latest stable release version", ["python.org", "docs.python.org"])]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key-env", default="TAVILY_API_KEY", help="environment variable holding the Tavily key (direct mode)")
    parser.add_argument("--relay", help="Bifrost relay name: send the searches through the gateway instead of to Tavily")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8080")
    parser.add_argument("--token-env", default="BIFROST_VIRTUAL_KEY", help="environment variable holding the Bifrost virtual key (relay mode)")
    parser.add_argument("--searches", type=int, choices=(1, 2), default=2, help="how many of the two queries to send")
    args = parser.parse_args()
    name = args.token_env if args.relay else args.key_env
    secret = os.environ.get(name)
    if not secret:
        sys.exit(f"environment variable {name} is empty or unset")
    transport = BifrostRelayTransport(args.gateway_url, args.relay) if args.relay else None
    summary = []
    for query, domains in QUERIES[:args.searches]:
        plan = {"schema_version": "1.0", "format": "reviewed_public_acquisition", "public_reviewed": True, "queries": [query], "urls": [], "primary_domains": domains}
        try:
            result = acquire(plan, allow_network=True, api_key=secret, transport=transport)
        except AcquisitionError as error:
            summary.append({"query": query, "error": str(error)})
            continue
        summary.append({"query": query, "status": result["status"], "http_status": [call["http_status"] for call in result["requests"]],
                        "credits": [call["provider_usage"] for call in result["requests"]], "failures": result["failures"],
                        "results": [{"url": row["url"], "published_at": row["published_at"]} for row in result["candidates"]]})
    text = json.dumps(summary, indent=2)
    if secret in text:
        sys.exit("refusing to print: the key appears in the output")
    print(text)
    return 1 if any("error" in item for item in summary) else 0


if __name__ == "__main__":
    sys.exit(main())
