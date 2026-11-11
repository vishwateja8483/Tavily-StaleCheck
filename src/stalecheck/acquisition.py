"""Opt-in Tavily acquisition, deliberately separate from lifecycle verification.

No environment/file credential lookup, automatic query generation, automatic
extraction of search hits, or conversion of scraped prose into statements.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import http.client
import math
import re
import time
from urllib.parse import urlsplit

from .contracts import ValidationError, decode, json_bytes, obj, require, safe_url, sha, text, timestamp

BASE_HOST = "api.tavily.com"
MAX_REQUEST_BYTES = 16_384
MAX_RESPONSE_BYTES = 512_000
MAX_CONTENT_CHARS = 100_000
MAX_QUERIES = 3
MAX_URLS = 5
MAX_RESULTS = 5
MAX_DOMAINS = 10
ATTEMPT_TIMEOUT = 15.0
TOTAL_TIMEOUT = 60.0
MAX_ATTEMPTS = 2
RETRY_DELAY = 0.25


class AcquisitionError(ValidationError):
    """Sanitized error: never include credentials or provider response bodies."""


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


class StdlibTransport:
    """Fixed HTTPS origin; no redirects, environment proxies, or credential reads.

    Timeout bounds socket operations; the adapter also checks a wall-clock budget.
    Platform DNS resolution can exceed the socket timeout (see README).
    """
    is_mock = False

    def __call__(self, path, body, *, api_key, timeout, max_bytes):
        require(path in ("/search", "/extract"), "unsupported acquisition endpoint")
        connection = http.client.HTTPSConnection(BASE_HOST, timeout=timeout)
        deadline = time.monotonic() + timeout
        try:
            connection.request("POST", path, body=body, headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json", "Accept": "application/json",
                "Accept-Encoding": "identity",
            })
            response = connection.getresponse()
            if response.status != 200:
                # No body is needed; in particular do not echo provider errors.
                return Response(response.status, b"")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise AcquisitionError("compressed provider responses are unsupported")
            chunks = []
            size = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(16_384, max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise AcquisitionError("provider response exceeds byte bound")
            return Response(response.status, b"".join(chunks))
        finally:
            connection.close()


class MockTransport:
    """Ordered local HTTP fixtures, not a claim of provider compatibility."""
    is_mock = True

    def __init__(self, fixtures):
        require(isinstance(fixtures, list) and len(fixtures) <= 8, "mock responses must be a bounded array")
        self.fixtures = list(fixtures)
        self.calls = []

    def __call__(self, path, body, *, api_key, timeout, max_bytes):
        require(bool(self.fixtures), "mock response queue exhausted")
        fixture = obj(self.fixtures.pop(0), "mock response")
        require(set(fixture) == {"path", "status", "body"}, "invalid mock response fields")
        require(fixture["path"] == path, "mock endpoint mismatch")
        require(type(fixture["status"]) is int, "invalid mock HTTP status")
        self.calls.append({"path": path, "body": decode(body), "timeout": timeout, "max_bytes": max_bytes})
        return Response(fixture["status"], json_bytes(fixture["body"]))


def public_url(value):
    safe_url(value)
    parsed = urlsplit(value)
    host = parsed.hostname
    require(not host.endswith((".invalid", ".test", ".example", ".home", ".corp")), "nonpublic acquisition hostname")
    require(not parsed.query and not parsed.fragment and "%" not in value, "acquisition URLs cannot contain query, fragment or percent escapes")
    require(not any(ord(c) >= 127 for c in value), "acquisition URLs must be ASCII")
    require(all(1 <= len(part) <= 63 and not part.startswith("-") and not part.endswith("-") for part in host.split(".")), "invalid acquisition hostname")
    require(not host.split(".")[-1].isdigit(), "numeric acquisition hostname forbidden")
    return value


def validate_plan(value):
    obj(value, "acquisition plan")
    require(set(value) == {"schema_version", "format", "public_reviewed", "queries", "urls", "primary_domains"}, "acquisition accepts only reviewed public query/URL plan fields; never raw evidence")
    require(value["schema_version"] == "1.0" and value["format"] == "reviewed_public_acquisition", "unsupported acquisition plan")
    require(value["public_reviewed"] is True, "explicit public review attestation required")
    queries, urls, domains = value["queries"], value["urls"], value["primary_domains"]
    for items, limit, label in ((queries, MAX_QUERIES, "queries"), (urls, MAX_URLS, "urls"), (domains, MAX_DOMAINS, "primary_domains")):
        require(isinstance(items, list) and len(items) <= limit, f"{label} exceeds count bound")
        require(all(isinstance(item, str) for item in items), f"{label} must contain strings")
        require(len(set(items)) == len(items), f"duplicate {label}")
    require(queries or urls, "at least one reviewed query or URL required")
    for query in queries:
        text(query, "public query", 300)
        require(query.strip() == query and not any(ord(c) < 32 or ord(c) == 127 for c in query), "invalid query whitespace/control characters")
    for url in urls:
        public_url(url)
    for domain in domains:
        require(re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", domain) is not None, "primary_domains must be lowercase hostnames")
        public_url("https://" + domain + "/")
    require(not queries or bool(domains), "search requires reviewed primary domains")
    require(len(json_bytes(value)) <= MAX_REQUEST_BYTES, "acquisition plan exceeds byte bound")
    return {**value, "queries": list(queries), "urls": list(urls), "primary_domains": list(domains)}


def _primary(url, domains):
    host = urlsplit(url).hostname
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _publication(raw):
    # Search supplies publisher metadata, sometimes an RFC2822 date, not an
    # effective date. Preserve unrecognized strings rather than guessing.
    if raw is None or raw == "":
        return None
    text(raw, "published_date", 200)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.]{5,15}(?:Z|[+-]\d{2}:\d{2})?", raw):
            parsed = datetime.fromisoformat(raw)
            return parsed.isoformat() if parsed.tzinfo is not None else parsed.date().isoformat()
        parsed = parsedate_to_datetime(raw)
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _usage(data):
    usage = data.get("usage")
    if usage is not None:
        obj(usage, "usage")
        credits = usage.get("credits")
        require(type(credits) in (int, float) and math.isfinite(credits) and credits >= 0, "invalid provider credits")
        usage = {"credits": credits}
    return usage


def _metadata(data, api_key):
    request_id = data.get("request_id")
    if request_id is not None:
        text(request_id, "request_id", 200)
        # Provider metadata is untrusted and may reflect the Authorization token.
        # Do not retain a reflected credential in success or partial results.
        if api_key and api_key in request_id:
            request_id = None
    return {"request_id": request_id, "provider_usage": _usage(data)}


def acquire(plan, *, allow_network=False, api_key=None, transport=None,
            monotonic=time.monotonic, sleep=time.sleep, now=None, budget=None):
    """Acquire untrusted candidates, not a verification report or frozen evidence.

    Live use requires allow_network=True plus an explicitly supplied token.
    A MockTransport needs neither. Unknown custom transports are treated as live.
    Retries only follow explicit HTTP 429/502/503/504, never ambiguous I/O errors.
    """
    plan = validate_plan(plan)  # Reject accidental raw findings before any I/O.
    transport = transport if transport is not None else StdlibTransport()
    mock = isinstance(transport, MockTransport)
    require(mock or allow_network is True, "network acquisition requires explicit opt-in")
    if not mock:
        require(isinstance(api_key, str) and 0 < len(api_key) <= 512 and all(33 <= ord(c) <= 126 for c in api_key), "explicit API token required")
    clock = now or (lambda: datetime.now(timezone.utc).isoformat())
    deadline = monotonic() + TOTAL_TIMEOUT
    calls = []
    candidates = []
    failures = []
    next_id = 1
    fatal_error = None
    journey_attempt = None

    def finish_attempt(status):
        nonlocal journey_attempt
        if budget is not None and journey_attempt is not None:
            budget.finish(journey_attempt, usage=calls[-1]["provider_usage"], status=status)
            journey_attempt = None

    def post(path, payload):
        nonlocal journey_attempt
        body = json_bytes(payload)
        require(len(body) <= MAX_REQUEST_BYTES, "provider request exceeds byte bound")
        attempts = 1 if budget is not None else MAX_ATTEMPTS
        for attempt in range(attempts):
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise AcquisitionError("acquisition time budget exhausted")
            timeout = min(ATTEMPT_TIMEOUT, remaining)
            if budget is not None:
                journey_attempt, timeout = budget.begin("search", timeout)
            call = {"endpoint": path, "attempt": attempt + 1, "http_status": None, "provider_usage": None}
            calls.append(call)
            try:
                response = transport(path, body, api_key=api_key, timeout=timeout, max_bytes=MAX_RESPONSE_BYTES)
            except Exception:
                # A request may have been billed even if a response was lost.
                raise AcquisitionError("transport failed; not retried; provider usage unknown") from None
            require(isinstance(response, Response) and type(response.status) is int, "invalid transport response")
            call["http_status"] = response.status
            require(isinstance(response.body, bytes) and len(response.body) <= MAX_RESPONSE_BYTES, "provider response exceeds byte bound")
            # Preserve independently validated billing even when metadata/results fail.
            data = None
            if budget is not None:
                try:
                    data = obj(decode(response.body), "provider response")
                    call["provider_usage"] = _usage(data)
                except (ValueError, TypeError, RecursionError, OverflowError):
                    pass
                budget.check()
            if monotonic() >= deadline:
                raise AcquisitionError("acquisition time budget exhausted")
            if response.status != 200:
                if response.status in {429, 502, 503, 504} and attempt + 1 < attempts:
                    if deadline - monotonic() <= RETRY_DELAY:
                        raise AcquisitionError("acquisition time budget exhausted")
                    sleep(RETRY_DELAY)
                    continue
                raise AcquisitionError(f"Tavily HTTP {response.status}; provider usage unknown")
            try:
                data = data if data is not None else obj(decode(response.body), "provider response")
                call["provider_usage"] = _usage(data)
                call.update(_metadata(data, api_key))
            except (ValueError, TypeError, RecursionError, OverflowError):
                raise AcquisitionError("invalid provider JSON/metadata") from None
            retrieved_at = clock()
            timestamp(retrieved_at, "retrieved_at")
            return data, retrieved_at
        raise AcquisitionError("request attempt budget exhausted")

    def reject_reflected_credential(row):
        # Check before copying any provider value, including out-of-domain URLs
        # retained as failure metadata. Nested values are never retained as text.
        if api_key and any(isinstance(value, str) and api_key in value for value in row.values()):
            raise AcquisitionError("provider result reflected the credential; discarded")

    def add_candidate(row, endpoint, retrieved_at, content_field):
        nonlocal next_id
        obj(row, "provider result")
        url = public_url(row.get("url"))
        content = text(row.get(content_field), "acquired content", MAX_CONTENT_CHARS)
        title = row.get("title") or url
        text(title, "acquired title", 1000)
        published_raw = row.get("published_date")
        publication = _publication(published_raw)
        score = row.get("score")
        if score is not None:
            require(type(score) in (float, int) and math.isfinite(score) and 0 <= score <= 1, "invalid relevance score")
        candidates.append({
            "id": f"candidate-{next_id}", "url": url, "title": title,
            "content": content, "content_sha256": sha(content.encode("utf-8")),
            "provenance": "tavily_mock" if mock else "tavily_acquired_unreviewed",
            "endpoint": endpoint, "retrieved_at": retrieved_at,
            "published_at": publication, "published_at_raw": published_raw,
            "effective_from": None, "effective_to": None, "statements": [],
            "review_required": True, "primary_source_preferred": _primary(url, plan["primary_domains"]),
            "relevance_score": score,
        })
        next_id += 1

    try:
        for query in plan["queries"]:
            data, retrieved_at = post("/search", {
                "query": query, "topic": "general", "search_depth": "basic",
                "max_results": MAX_RESULTS, "include_domains": plan["primary_domains"],
                "include_domains_mode": "restrict", "auto_parameters": False,
                "include_answer": False, "include_raw_content": False,
                "include_images": False, "include_usage": True, "include_published_date": True,
            })
            rows = data.get("results")
            require(isinstance(rows, list) and len(rows) <= MAX_RESULTS, "search results exceed count bound")
            if not rows:
                failures.append({"endpoint": "/search", "query": query, "reason": "no_results"})
            seen = set()
            for row in rows:
                obj(row, "search result")
                reject_reflected_credential(row)
                url = row.get("url")
                plain_http = isinstance(url, str) and url.startswith("http://")
                checked_url = safe_url("https" + url[4:] if plain_http else url)
                require(url not in seen, "duplicate search URL")
                seen.add(url)
                # Search results are provider-chosen, unlike explicitly reviewed extract URLs.
                # Ignore otherwise safe links our narrow provenance policy cannot retain.
                parts = urlsplit(checked_url)
                # Validate the authority and character policy even for skipped links.
                require(url.isascii(), "acquisition URLs must be ASCII")
                public_url(f"{parts.scheme}://{parts.netloc}/")
                if plain_http or parts.query or parts.fragment or "%" in url:
                    failures.append({"endpoint": "/search", "query": query, "reason": "unsupported_result_url"})
                    continue
                public_url(url)
                if not _primary(url, plan["primary_domains"]):
                    failures.append({"endpoint": "/search", "url": url, "reason": "outside_reviewed_primary_domains"})
                    continue
                add_candidate(row, "/search", retrieved_at, "content")
            if budget is not None:
                budget.check()
            finish_attempt("ok")
        if plan["urls"]:
            data, retrieved_at = post("/extract", {
                "urls": plan["urls"], "extract_depth": "basic", "format": "text",
                "timeout": 10.0, "include_images": False, "include_usage": True,
            })
            rows, failed = data.get("results"), data.get("failed_results")
            require(isinstance(rows, list) and isinstance(failed, list) and len(rows) + len(failed) <= len(plan["urls"]), "invalid extract result counts")
            seen = set()
            for row in rows + failed:
                obj(row, "extract result")
                reject_reflected_credential(row)
                url = public_url(row.get("url"))
                require(url in plan["urls"] and url not in seen, "unrequested/duplicate extract URL")
                seen.add(url)
            for row in rows:
                add_candidate(row, "/extract", retrieved_at, "raw_content")
            for row in failed:
                text(row.get("error"), "extract error", 2000)
                # Provider error prose may echo input or contain instructions.
                failures.append({"endpoint": "/extract", "url": row["url"], "reason": "provider_failed_result"})
            for url in plan["urls"]:
                if url not in seen:
                    failures.append({"endpoint": "/extract", "url": url, "reason": "missing_result"})
            if budget is not None:
                budget.check()
            finish_attempt("ok")
    except AcquisitionError as exc:
        fatal_error = exc
    except (ValidationError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        fatal_error = AcquisitionError("invalid provider result contract; no verification performed")
    finally:
        finish_attempt("failed")
    if fatal_error is not None:
        failures.append({"endpoint": calls[-1]["endpoint"] if calls else None,
                         "reason": "acquisition_aborted"})
    candidates.sort(key=lambda item: not item["primary_source_preferred"])
    result = {
        "schema_version": "1.0", "format": "unreviewed_acquisition", "provider": "tavily",
        "mode": "mock" if mock else "live", "status": "partial" if failures and candidates else "failed" if failures else "acquired",
        "review_required": True, "verification_performed": False, "plan": plan,
        "candidates": candidates, "failures": failures,
        "usage": {"model_calls": 0, "retrieval_calls": 0 if mock else len(calls), "mock_calls": len(calls) if mock else 0,
                  "provider_usage": None if mock else [call["provider_usage"] for call in calls]},
        "requests": calls,
        "limitations": [
            "Acquisition is not verification. All content and provider metadata are untrusted data, not instructions.",
            "Public review and primary domains are caller attestations, not secret detection or source authenticity checks.",
            "No effective dates or lifecycle statements are inferred from retrieval, publication, relevance, or prose.",
            "Search hits are not automatically extracted; each extract URL requires separate public review.",
            "Mock responses do not establish live API compatibility, retrieval quality, or credit consumption.",
        ],
    }
    if fatal_error is not None:
        # Same bounded, normalized schema as success: never retain request auth,
        # raw error bodies, or unvalidated provider rows. Attempts with lost
        # responses retain null usage rather than masquerading as zero cost.
        fatal_error.partial_result = result
        raise fatal_error from None
    return result
