"""Bounded offline human-attestation records; not evidence or temporal assessment.

Digests identify canonical JSON snapshots, not authorship or source authenticity.
The source content is acquired text (never original publisher document bytes).
This module does no I/O and cannot enforce filesystem append-only storage.
"""
import json
import math
from urllib.parse import urlsplit

from .acquisition import MAX_CONTENT_CHARS, MAX_QUERIES, MAX_RESULTS, MAX_URLS, _publication, public_url, validate_plan
from .contracts import DIGEST, MAX_FILE, ValidationError, day, decode, identifier, json_bytes, obj, require, sha, text, timestamp

SCHEMA_VERSION = "1.0"
FORMAT = "human_attested_review"
MAX_DEPTH = 16
MAX_NODES = 50_000
_CANDIDATE_KEYS = {
    "id", "url", "title", "content", "content_sha256", "provenance", "endpoint",
    "retrieved_at", "published_at", "published_at_raw", "effective_from",
    "effective_to", "statements", "review_required", "primary_source_preferred",
    "relevance_score",
}
_ACQUISITION_KEYS = {
    "schema_version", "format", "provider", "mode", "status", "review_required",
    "verification_performed", "plan", "candidates", "failures", "usage", "requests",
    "limitations",
}
_PROPOSAL_KEYS = {
    "acquisition_sha256", "candidate_id", "candidate_sha256", "content_sha256",
    "quote", "quote_start_byte", "quote_end_byte", "subject", "assertion", "scope",
    "value", "effective_from", "end_state", "reviewer", "attested", "reviewed_at",
    "rationale",
}
_DOCUMENT_KEYS = {
    "schema_version", "format", "acquisition", "acquisition_sha256", "candidate_id",
    "candidate_sha256", "content_sha256", "extraction_method", "proposal", "review_sha256",
}


def _bounded(value):
    """Reject deep/cyclic, huge and non-JSON Python inputs before serialization."""
    pending = [(value, 0)]
    nodes = size = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        require(nodes <= MAX_NODES and depth <= MAX_DEPTH, "review input exceeds structural bound")
        kind = type(item)
        if kind is dict:
            require(len(item) <= MAX_NODES - nodes, "review object exceeds count bound")
            require(all(type(key) is str for key in item), "JSON keys must be strings")
            pending.extend((child, depth + 1) for pair in item.items() for child in pair)
            size += 2 + len(item) * 2
        elif kind is list:
            require(len(item) <= MAX_NODES - nodes, "review array exceeds count bound")
            pending.extend((child, depth + 1) for child in item)
            size += 2 + len(item)
        elif kind is str:
            require(len(item) <= MAX_FILE, "review text exceeds size bound")
            try:
                size += len(item.encode("utf-8")) + 2
            except UnicodeEncodeError:
                raise ValidationError("review text must be valid UTF-8") from None
        elif kind is int:
            require(item.bit_length() <= 64, "review integer exceeds bound")
            size += 20
        elif kind is float:
            require(math.isfinite(item), "review numbers must be finite")
            size += 24
        else:
            require(kind is bool or item is None, "review input must be JSON data")
            size += 5
        require(size <= MAX_FILE and len(pending) + nodes <= MAX_NODES, "review input exceeds size/count bound")
    try:
        raw = json_bytes(value)
    except (ValueError, TypeError, RecursionError, OverflowError):
        require(False, "invalid review JSON")
    require(len(raw) <= MAX_FILE, "review JSON exceeds file size bound")
    return value


def _digest(value):
    _bounded(value)
    # Sorted keys, compact separators, unescaped Unicode, no terminal newline.
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _hash(value, label):
    require(isinstance(value, str) and DIGEST.fullmatch(value) is not None, f"invalid {label}")
    return value


def _keys(value, expected, label):
    obj(value, label)
    require(set(value) == expected, f"invalid {label} fields")


def _nonblank(value, label, maximum):
    text(value, label, maximum)
    require(bool(value.strip()), f"{label} must not be blank")


def _candidate(value):
    _keys(value, _CANDIDATE_KEYS, "candidate")
    identifier(value["id"], "candidate id")
    public_url(value["url"])
    text(value["title"], "candidate title", 1000)
    text(value["content"], "candidate content", MAX_CONTENT_CHARS)
    _hash(value["content_sha256"], "candidate content_sha256")
    require(value["content_sha256"] == sha(value["content"].encode("utf-8")), "candidate content digest mismatch")
    require(value["provenance"] in ("tavily_mock", "tavily_acquired_unreviewed"), "invalid acquired provenance")
    require(value["endpoint"] in ("/search", "/extract"), "invalid acquisition endpoint")
    timestamp(value["retrieved_at"], "retrieved_at")
    require(value["published_at"] == _publication(value["published_at_raw"]), "candidate publication metadata mismatch")
    require(value["effective_from"] is None and value["effective_to"] is None and value["statements"] == [], "acquisition must remain unreviewed")
    require(value["review_required"] is True, "acquisition review_required must remain true")
    require(type(value["primary_source_preferred"]) is bool, "invalid primary source preference")
    score = value["relevance_score"]
    require(score is None or (type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1), "invalid relevance score")
    return value


def candidate_digest(candidate):
    """Hash the entire validated candidate, including URL and all metadata."""
    return _digest(_candidate(_bounded(candidate)))


def _provider_usage(value):
    if value is not None:
        _keys(value, {"credits"}, "provider usage")
        credits = value["credits"]
        require(type(credits) in (int, float) and math.isfinite(credits) and credits >= 0, "invalid provider credits")


def _acquisition(value):
    _keys(value, _ACQUISITION_KEYS, "acquisition")
    require(value["schema_version"] == "1.0" and value["format"] == "unreviewed_acquisition" and value["provider"] == "tavily", "unsupported acquisition snapshot")
    require(value["mode"] in ("mock", "live"), "invalid acquisition mode")
    require(value["review_required"] is True and value["verification_performed"] is False, "acquisition cannot claim verification")
    plan = validate_plan(value["plan"])
    candidates = value["candidates"]
    require(isinstance(candidates, list) and len(candidates) <= MAX_QUERIES * MAX_RESULTS + MAX_URLS, "invalid candidate count")
    seen = set()
    for candidate in candidates:
        _candidate(candidate)
        require(candidate["id"] not in seen, "duplicate ambiguous candidate id")
        seen.add(candidate["id"])
        expected = "tavily_mock" if value["mode"] == "mock" else "tavily_acquired_unreviewed"
        require(candidate["provenance"] == expected, "acquisition provenance/mode mismatch")
        host = urlsplit(candidate["url"]).hostname
        primary = any(host == domain or host.endswith("." + domain) for domain in plan["primary_domains"])
        require(candidate["primary_source_preferred"] is primary, "primary preference mismatch")
        if candidate["endpoint"] == "/extract":
            require(candidate["url"] in plan["urls"], "unrequested extracted URL")
        else:
            require(bool(plan["queries"]) and primary, "unrequested search candidate")
    failures = value["failures"]
    require(isinstance(failures, list) and len(failures) <= 32, "invalid failures")
    for failure in failures:
        obj(failure, "failure")
        reason = failure.get("reason")
        if reason in ("no_results", "unsupported_result_url"):
            _keys(failure, {"endpoint", "query", "reason"}, "failure")
            require(failure["endpoint"] == "/search" and failure["query"] in plan["queries"], "invalid search failure")
        elif reason in ("outside_reviewed_primary_domains", "provider_failed_result", "missing_result"):
            _keys(failure, {"endpoint", "url", "reason"}, "failure")
            public_url(failure["url"])
            endpoint = "/search" if reason == "outside_reviewed_primary_domains" else "/extract"
            require(failure["endpoint"] == endpoint, "invalid failure endpoint")
        else:
            _keys(failure, {"endpoint", "reason"}, "failure")
            require(reason == "acquisition_aborted" and failure["endpoint"] in (None, "/search", "/extract"), "invalid acquisition failure")
    expected_status = "partial" if failures and candidates else "failed" if failures else "acquired"
    require(value["status"] == expected_status, "acquisition status mismatch")
    requests = value["requests"]
    require(isinstance(requests, list) and len(requests) <= 8, "invalid acquisition requests")
    for request in requests:
        obj(request, "request")
        fields = {"endpoint", "attempt", "http_status", "provider_usage"}
        _keys(request, fields | ({"request_id"} if "request_id" in request else set()), "request")
        require(request["endpoint"] in ("/search", "/extract") and type(request["attempt"]) is int and request["attempt"] in (1, 2), "invalid acquisition request")
        status = request["http_status"]
        require(status is None or (type(status) is int and 100 <= status <= 599), "invalid request HTTP status")
        if request.get("request_id") is not None:
            text(request["request_id"], "request_id", 200)
        _provider_usage(request["provider_usage"])
    usage = value["usage"]
    _keys(usage, {"model_calls", "retrieval_calls", "mock_calls", "provider_usage"}, "usage")
    for key in ("model_calls", "retrieval_calls", "mock_calls"):
        require(type(usage[key]) is int and 0 <= usage[key] <= 8, "invalid acquisition call count")
    mock = value["mode"] == "mock"
    require(usage["model_calls"] == 0 and usage["mock_calls"] == (len(requests) if mock else 0) and usage["retrieval_calls"] == (0 if mock else len(requests)), "acquisition usage mismatch")
    if not mock:
        require(isinstance(usage["provider_usage"], list) and len(usage["provider_usage"]) == len(requests), "invalid retained provider usage")
        for retained in usage["provider_usage"]:
            _provider_usage(retained)
    require(usage["provider_usage"] == (None if mock else [row["provider_usage"] for row in requests]), "acquisition provider usage mismatch")
    limitations = value["limitations"]
    require(isinstance(limitations, list) and 1 <= len(limitations) <= 20, "invalid acquisition limitations")
    for limitation in limitations:
        text(limitation, "acquisition limitation", 2000)
    return value


def acquisition_digest(acquisition):
    """Snapshot identity: acquisition has no standalone acquisition ID."""
    return _digest(_acquisition(_bounded(acquisition)))


def _proposal(proposal, acquisition):
    obj(proposal, "review proposal")
    fields = _PROPOSAL_KEYS | ({"effective_to"} if proposal.get("end_state") == "dated" else set())
    if "previous_review_sha256" in proposal:
        fields |= {"previous_review_sha256"}
        _hash(proposal["previous_review_sha256"], "previous_review_sha256")
    _keys(proposal, fields, "review proposal")
    for key in ("acquisition_sha256", "candidate_sha256", "content_sha256"):
        _hash(proposal[key], key)
    require(proposal["acquisition_sha256"] == _digest(acquisition), "review acquisition digest mismatch")
    identifier(proposal["candidate_id"], "candidate id")
    candidates = [row for row in acquisition["candidates"] if row["id"] == proposal["candidate_id"]]
    require(len(candidates) == 1, "review requires exactly one matching candidate")
    candidate = candidates[0]
    require(proposal["candidate_sha256"] == _digest(candidate), "review candidate snapshot digest mismatch")
    require(proposal["content_sha256"] == candidate["content_sha256"], "review original content digest mismatch")
    quote = text(proposal["quote"], "quote", 20_000)
    start, end = proposal["quote_start_byte"], proposal["quote_end_byte"]
    raw = candidate["content"].encode("utf-8")
    require(type(start) is int and type(end) is int and 0 <= start < end <= len(raw), "invalid UTF-8 quote offsets")
    # Equality to valid UTF-8 quote bytes also proves both codepoint boundaries.
    require(raw[start:end] == quote.encode("utf-8"), "quote bytes/UTF-8 boundaries mismatch")
    for key in ("subject", "scope"):
        _nonblank(proposal[key], key, 200)
    require(proposal["assertion"] == "is_supported" and type(proposal["value"]) is bool, "invalid reviewed assertion/value")
    if proposal["effective_from"] is not None:
        day(proposal["effective_from"], "effective_from")
    require(proposal["end_state"] in ("dated", "open_ended", "unknown"), "invalid reviewed end state")
    if proposal["end_state"] == "dated":
        day(proposal["effective_to"], "effective_to")
        require(proposal["effective_from"] is not None and proposal["effective_from"] < proposal["effective_to"], "invalid half-open reviewed interval")
    _nonblank(proposal["reviewer"], "reviewer identity claim", 200)
    require(proposal["attested"] is True, "explicit human review attestation required")
    timestamp(proposal["reviewed_at"], "reviewed_at")
    _nonblank(proposal["rationale"], "review/date rationale", 4000)
    return candidate


def _body(acquisition, proposal):
    candidate = _proposal(proposal, acquisition)
    return {
        "schema_version": SCHEMA_VERSION, "format": FORMAT, "acquisition": acquisition,
        "acquisition_sha256": proposal["acquisition_sha256"],
        "candidate_id": candidate["id"], "candidate_sha256": proposal["candidate_sha256"],
        "content_sha256": candidate["content_sha256"],
        "extraction_method": "search_snippet" if candidate["endpoint"] == "/search" else "extracted_text",
        "proposal": proposal,
    }


def _validate_document(document):
    _bounded(document)
    _keys(document, _DOCUMENT_KEYS, "review document")
    require(document["schema_version"] == SCHEMA_VERSION and document["format"] == FORMAT, "unsupported review document")
    _acquisition(document["acquisition"])
    expected = _body(document["acquisition"], document["proposal"])
    _hash(document["review_sha256"], "review_sha256")
    expected["review_sha256"] = _digest(expected)
    require(document == expected, "review document derivation/digest mismatch")
    return document


def _previous(document, previous_review):
    prior_digest = document["proposal"].get("previous_review_sha256")
    require((prior_digest is None) == (previous_review is None), "correction requires explicitly linked supplied prior review")
    if previous_review is None:
        return
    # Validate the immediate prior record internally. Its older ancestors are not
    # resolved here; the hash link is not a claim of a complete/authenticated log.
    _validate_document(previous_review)
    require(prior_digest == previous_review["review_sha256"], "prior review digest mismatch")
    for key in ("acquisition_sha256", "candidate_id", "candidate_sha256", "content_sha256"):
        require(document[key] == previous_review[key], "correction must retain acquisition/candidate identity")
    require(timestamp(document["proposal"]["reviewed_at"], "reviewed_at") >= timestamp(previous_review["proposal"]["reviewed_at"], "prior reviewed_at"), "correction predates prior review")


def make_review(acquisition, proposal, *, previous_review=None):
    """Build a detached deterministic record from explicit caller attestation.

    Proposal fields are exact: the four linkage fields, UTF-8 quote and offsets,
    subject/assertion/scope/value, effective_from, end_state, reviewer, attested,
    reviewed_at and rationale. Only dated ends carry effective_to. Corrections
    also carry previous_review_sha256 and require the supplied immediate record.
    No clock, reviewer, quote, lifecycle dates or approvals are generated.
    """
    _acquisition(_bounded(acquisition))
    _bounded(proposal)
    document = _body(acquisition, proposal)
    document["review_sha256"] = _digest(document)
    _bounded(document)
    _previous(document, previous_review)
    return decode(json_bytes(document))


def validate_review(document, *, acquisition=None, previous_review=None):
    """Recompute all fields, hashes and immediate correction linkage offline.

    An optional external acquisition must match the embedded snapshot exactly.
    A correction always requires its explicitly supplied immediate prior record;
    older ancestry is not resolved. A correction never deletes or temporally
    supersedes prior statements. Unknown ends do not mean reviewed open ends.
    Validity is structural consistency, not authenticity, entailment or approval.
    """
    _validate_document(document)
    if acquisition is not None:
        require(acquisition_digest(acquisition) == document["acquisition_sha256"], "supplied acquisition snapshot mismatch")
    _previous(document, previous_review)
    return decode(json_bytes(document))
