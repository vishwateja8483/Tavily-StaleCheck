"""Local stand-ins for the two services behind a Bifrost gateway: an OpenAI-compatible model and Tavily Search.

Only the gateway check uses this. The model is rule-based, not a language model: it answers the two StaleCheck
prompts with valid JSON so that the real gateway, relay, fallback and budget paths carry realistic traffic.
Every request is appended to a JSON-lines log so the check can assert what each upstream actually received.
"""
import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TAVILY_SECRET = "stub-tavily-secret"
PRICE_PAGE = "Pricing updated 2026-10-20. The Starter plan costs $24 per month."
SDK_PAGE = "Release notes. The Acme SDK is at version 4.2 today."
FIGURE = re.compile(r"\d+(?:[.,]\d+)*")


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def extraction(user):
    answer = re.search(r"<<<(ANSWER-[0-9a-f]+)\n(.*?)\n\1>>>", user, re.S).group(2)
    claims = []
    for sentence in sentences(answer):
        quote = sentence.rstrip(".")
        if "$" in quote:
            claims.append({"quote": quote, "category": "pricing", "time_sensitivity": "high",
                           "reason": "Subscription prices change often.", "query": "acme cloud starter plan pricing"})
        elif "version" in quote:
            claims.append({"quote": quote, "category": "version", "time_sensitivity": "medium",
                           "reason": "Release numbers move on.", "query": "acme sdk latest release version"})
    return {"claims": claims}


def assessment(user):
    claim = re.search(r"Claim: (.*?)\n\nSources:\n", user, re.S).group(1)
    blocks = re.findall(r"\[([^\]]+)\] \S+ \(published: [^)]*\)\n<<<(SRC-[0-9a-f]+)\n(.*?)\n\2>>>", user, re.S)
    if not blocks:
        return {"verdict": "UNVERIFIABLE", "observed": "No source excerpt was provided.", "evidence": [], "rationale": "Nothing to compare.", "rewrite": ""}
    claim_figures = set(FIGURE.findall(claim))
    for source_id, _, content in blocks:
        for sentence in sentences(content):
            figures = set(FIGURE.findall(sentence))
            if "$" in sentence and figures and not claim_figures <= figures and "$" in claim:
                return {"verdict": "CHANGED", "observed": sentence[:280], "rationale": "The excerpt gives a different price than the claim.",
                        "evidence": [{"source_id": source_id, "relation": "contradicts", "quote": sentence}],
                        "rewrite": re.sub(r"\$\d+(?:[.,]\d+)*", "$" + sorted(figures, key=len)[-1], claim) + "."}
            if figures and claim_figures and claim_figures <= figures:
                return {"verdict": "SUPPORTED", "observed": sentence[:280], "rationale": "The excerpt repeats the claim's figures.",
                        "evidence": [{"source_id": source_id, "relation": "supports", "quote": sentence}], "rewrite": ""}
    return {"verdict": "UNVERIFIABLE", "observed": "The excerpts do not settle the claim.", "evidence": [], "rationale": "No matching figures.", "rewrite": ""}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def record(self, body, **extra):
        entry = {"service": self.server.service, "method": self.command, "path": self.path, "body": body,
                 "authorization": self.headers.get("Authorization"), "x_bf_vk_forwarded": "x-bf-vk" in {k.lower() for k in self.headers.keys()},
                 "header_names": sorted({k.lower() for k in self.headers.keys()}), **extra}
        with self.server.lock, open(self.server.log, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")

    def do_GET(self):
        if self.server.service == "catalog":
            self.record(None)
            return self.reply(200, {})
        if self.path == "/_admin/mode":
            return self.reply(200, {"mode": self.server.mode})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        if self.path == "/_admin/mode":
            self.server.mode = body["mode"]
            return self.reply(200, {"mode": self.server.mode})
        if self.server.service == "tavily":
            return self.tavily(body)
        return self.llm(body)

    def tavily(self, body):
        authorized = self.headers.get("Authorization") == "Bearer " + TAVILY_SECRET
        self.record(body, authorized=authorized)
        if self.path != "/search":
            return self.reply(404, {"detail": {"error": "not found"}})
        if not authorized:
            return self.reply(401, {"detail": {"error": "Unauthorized: missing or invalid API key."}})
        query = str(body.get("query", "")).lower()
        page = PRICE_PAGE if "pricing" in query else SDK_PAGE if "sdk" in query else None
        domain = (body.get("include_domains") or ["docs.example.com"])[0]
        results = [] if page is None else [{
            "url": f"https://{domain}/acme/" + ("pricing" if page is PRICE_PAGE else "sdk"), "title": "Acme documentation", "content": page,
            "score": 0.81, "raw_content": None, "published_date": "Tue, 20 Oct 2026 17:00:00 GMT" if page is PRICE_PAGE else "Tue, 15 Sep 2026 09:00:00 GMT",
            "favicon": None, "images": [], "id": "a3f9c2-04"}]
        self.reply(200, {"query": body.get("query"), "answer": None, "images": [], "results": results,
                         "auto_parameters": {"topic": "general", "search_depth": "basic"}, "response_time": "0.21",
                         "usage": {"credits": 1}, "request_id": "123e4567-e89b-12d3-a456-426614174111"})

    def llm(self, body):
        self.record({"model": body.get("model"), "message_roles": [m.get("role") for m in body.get("messages", [])]})
        if self.server.mode == "down":
            return self.reply(503, {"error": {"message": "stub upstream is down", "type": "server_error"}})
        messages = body["messages"]
        system, user = messages[0]["content"], messages[-1]["content"]
        content = extraction(user) if system.startswith("You find factual claims") else assessment(user)
        text = json.dumps(content)
        self.reply(200, {"id": "chatcmpl-stub", "object": "chat.completion", "created": 1790000000, "model": body.get("model"),
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": max(1, len(system + user) // 4), "completion_tokens": max(1, len(text) // 4),
                                   "total_tokens": max(1, len(system + user) // 4) + max(1, len(text) // 4)}})


def serve(service, port, log, mode="up"):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.service, server.log, server.mode, server.lock = service, log, mode, threading.Lock()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True)
    parser.add_argument("--primary-port", type=int, default=9101)
    parser.add_argument("--backup-port", type=int, default=9102)
    parser.add_argument("--tavily-port", type=int, default=9201)
    parser.add_argument("--catalog-port", type=int, default=9301)
    args = parser.parse_args()
    servers = [serve("llm-primary", args.primary_port, args.log), serve("llm-backup", args.backup_port, args.log),
               serve("tavily", args.tavily_port, args.log), serve("catalog", args.catalog_port, args.log)]
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    print("stub upstreams listening", [s.server_address[1] for s in servers], flush=True)
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
