"""Synthetic attestations only; these tests do not create human approvals."""
from copy import deepcopy
import hashlib
import json

import pytest

from stalecheck.acquisition import MockTransport, acquire
from stalecheck.contracts import MAX_FILE, ValidationError, json_bytes
from stalecheck.review import acquisition_digest, candidate_digest, make_review, validate_review


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


@pytest.fixture
def acquisition():
    plan = {
        "schema_version": "1.0", "format": "reviewed_public_acquisition",
        "public_reviewed": True, "queries": [], "urls": ["https://docs.python.org/review"],
        "primary_domains": ["docs.python.org"],
    }
    transport = MockTransport([{"path": "/extract", "status": 200, "body": {
        "results": [{"url": plan["urls"][0], "title": "Synthetic review fixture",
                     "raw_content": "Préface. Supported ✅. Supported ✅.",
                     "published_date": "2026-01-01"}], "failed_results": [],
    }}])
    return acquire(plan, transport=transport, now=lambda: "2026-01-02T00:00:00Z")


@pytest.fixture
def proposal(acquisition):
    candidate = acquisition["candidates"][0]
    quote = "Supported ✅."
    raw = candidate["content"].encode("utf-8")
    start = raw.rindex(quote.encode("utf-8"))
    return {
        "acquisition_sha256": acquisition_digest(acquisition),
        "candidate_id": candidate["id"], "candidate_sha256": candidate_digest(candidate),
        "content_sha256": candidate["content_sha256"],
        "quote": quote, "quote_start_byte": start, "quote_end_byte": start + len(quote.encode("utf-8")),
        "subject": "synthetic-runtime", "assertion": "is_supported", "scope": "fixture",
        "value": True, "effective_from": "2026-01-01", "end_state": "unknown",
        "reviewer": "synthetic-test-reviewer (not a real attestation)", "attested": True,
        "reviewed_at": "2026-01-03T00:00:00Z", "rationale": "Synthetic test dates; end is not specified.",
    }


def rehash(document):
    document["review_sha256"] = digest({k: v for k, v in document.items() if k != "review_sha256"})


def test_roundtrip_deterministic_self_contained_detached(acquisition, proposal):
    original = deepcopy(acquisition)
    document = make_review(acquisition, proposal)
    assert document == make_review(acquisition, proposal)
    assert document == validate_review(document, acquisition=acquisition)
    assert document["acquisition"] == original
    assert document["acquisition"]["review_required"] is True
    assert document["acquisition"]["candidates"][0]["provenance"] == "tavily_mock"
    assert document["extraction_method"] == "extracted_text"
    assert document["review_sha256"] == digest({k: v for k, v in document.items() if k != "review_sha256"})
    assert len(json_bytes(document)) <= MAX_FILE
    proposal["scope"] = "changed"
    acquisition["candidates"][0]["title"] = "changed"
    assert document["proposal"]["scope"] == "fixture"
    assert document["acquisition"] == original


def test_canonical_key_order_is_irrelevant(acquisition, proposal):
    reordered = dict(reversed(list(acquisition.items())))
    reordered["candidates"] = [dict(reversed(list(acquisition["candidates"][0].items())))]
    assert acquisition_digest(reordered) == acquisition_digest(acquisition)
    assert make_review(reordered, dict(reversed(list(proposal.items())))) == make_review(acquisition, proposal)


@pytest.mark.parametrize("end_state", ["unknown", "open_ended", "dated"])
def test_end_states_are_explicit(acquisition, proposal, end_state):
    proposal["end_state"] = end_state
    if end_state == "dated":
        proposal["effective_to"] = "2027-01-01"
    document = make_review(acquisition, proposal)
    assert validate_review(document)["proposal"]["end_state"] == end_state
    assert ("effective_to" in document["proposal"]) == (end_state == "dated")


@pytest.mark.parametrize("end_state", ["unknown", "open_ended"])
def test_unknown_start_allowed_without_inference(acquisition, proposal, end_state):
    proposal.update(effective_from=None, end_state=end_state)
    assert make_review(acquisition, proposal)["proposal"]["effective_from"] is None


@pytest.mark.parametrize("field,value", [
    ("attested", False), ("attested", 1), ("attested", None),
    ("reviewer", ""), ("reviewer", "  "), ("rationale", ""), ("rationale", "\t"),
    ("reviewed_at", "2026-01-03"), ("reviewed_at", "2026-02-30T00:00:00Z"),
    ("subject", " "), ("scope", ""), ("assertion", "supported"), ("value", 1),
    ("end_state", "inferred"), ("end_state", []), ("effective_from", "2026-02-30"),
    ("quote_start_byte", True), ("quote_end_byte", False), ("quote_start_byte", 1.0),
    ("quote_start_byte", -1), ("quote_end_byte", 999_999), ("quote", "invented"),
    ("candidate_id", "missing"), ("candidate_sha256", "z" * 64),
    ("content_sha256", "0" * 64), ("acquisition_sha256", "0" * 64),
])
def test_invalid_proposals_rejected(acquisition, proposal, field, value):
    proposal[field] = value
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal)


@pytest.mark.parametrize("field", ["attested", "reviewer", "reviewed_at", "rationale", "end_state", "acquisition_sha256"])
def test_missing_required_fields(acquisition, proposal, field):
    del proposal[field]
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal)


@pytest.mark.parametrize("start,end", [(None, "2027-01-01"), ("2026-01-01", "2026-01-01"),
                                       ("2026-01-01", "2025-01-01"), ("2026-01-01", None)])
def test_invalid_dated_intervals(acquisition, proposal, start, end):
    proposal.update(end_state="dated", effective_from=start, effective_to=end)
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal)


@pytest.mark.parametrize("end_state", ["unknown", "open_ended"])
def test_non_dated_end_must_not_carry_effective_to(acquisition, proposal, end_state):
    proposal.update(end_state=end_state, effective_to=None)
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal)


def test_dated_end_requires_explicit_effective_to(acquisition, proposal):
    proposal["end_state"] = "dated"
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal)


def test_repeated_quote_uses_exact_byte_occurrence(acquisition, proposal):
    second = make_review(acquisition, proposal)
    first_offset = acquisition["candidates"][0]["content"].encode("utf-8").index(proposal["quote"].encode("utf-8"))
    proposal["quote_start_byte"] = first_offset
    proposal["quote_end_byte"] = first_offset + len(proposal["quote"].encode("utf-8"))
    first = make_review(acquisition, proposal)
    assert first["review_sha256"] != second["review_sha256"]


def test_multibyte_boundary_and_character_offsets_rejected(acquisition, proposal):
    raw = acquisition["candidates"][0]["content"].encode("utf-8")
    proposal.update(quote="✅", quote_start_byte=raw.index("✅".encode("utf-8")) + 1,
                    quote_end_byte=raw.index("✅".encode("utf-8")) + 3)
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal)
    content = acquisition["candidates"][0]["content"]
    proposal.update(quote="Supported ✅.", quote_start_byte=content.rindex("Supported"),
                    quote_end_byte=len(content))
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal)


@pytest.mark.parametrize("field,value", [("title", "Changed metadata"), ("url", "https://docs.python.org/changed"),
                                         ("retrieved_at", "2026-01-04T00:00:00Z"), ("relevance_score", 0.5)])
def test_same_content_changed_metadata_not_same_snapshot(acquisition, proposal, field, value):
    before = candidate_digest(acquisition["candidates"][0])
    acquisition["candidates"][0][field] = value
    assert candidate_digest(acquisition["candidates"][0]) != before
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal)


@pytest.mark.parametrize("mutation", ["extra", "duplicate", "bad_hash", "wrong_hash", "reviewed", "wrong_mode", "extra_request", "usage_bool"])
def test_invalid_acquisition(acquisition, proposal, mutation):
    if mutation == "extra":
        acquisition["candidates"][0]["hidden"] = "ignored?"
    elif mutation == "duplicate":
        acquisition["candidates"].append(deepcopy(acquisition["candidates"][0]))
    elif mutation == "bad_hash":
        acquisition["candidates"][0]["content_sha256"] = "A" * 64
    elif mutation == "wrong_hash":
        acquisition["candidates"][0]["content_sha256"] = "0" * 64
    elif mutation == "reviewed":
        acquisition["candidates"][0]["statements"] = [{"value": True}]
    elif mutation == "wrong_mode":
        acquisition["mode"] = "live"
    elif mutation == "extra_request":
        acquisition["requests"][0]["auth"] = "not allowed"
    else:
        acquisition["usage"]["model_calls"] = False
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal)


@pytest.mark.parametrize("where", ["document", "proposal", "acquisition", "plan", "usage"])
def test_extra_fields_rejected(acquisition, proposal, where):
    document = make_review(acquisition, proposal)
    target = {"document": document, "proposal": document["proposal"], "acquisition": document["acquisition"],
              "plan": document["acquisition"]["plan"], "usage": document["acquisition"]["usage"]}[where]
    target["extra"] = True
    rehash(document)
    with pytest.raises(ValidationError):
        validate_review(document)


@pytest.mark.parametrize("field,value", [("extraction_method", "publisher_original_bytes"), ("candidate_id", "candidate-2"),
                                         ("candidate_sha256", "0" * 64), ("schema_version", "2.0"),
                                         ("format", "frozen_evidence")])
def test_coherently_rehashed_copied_fields_rejected(acquisition, proposal, field, value):
    document = make_review(acquisition, proposal)
    document[field] = value
    rehash(document)
    with pytest.raises(ValidationError):
        validate_review(document)


def test_tampered_proposal_and_external_acquisition(acquisition, proposal):
    document = make_review(acquisition, proposal)
    document["proposal"]["scope"] = "other"
    with pytest.raises(ValidationError):
        validate_review(document)
    # Anyone can create a coherent new claim: a digest is not authentication.
    rehash(document)
    assert validate_review(document) == document
    acquisition["candidates"][0]["title"] = "Other snapshot"
    with pytest.raises(ValidationError):
        validate_review(document, acquisition=acquisition)


def test_correction_explicit_immediate_previous_record(acquisition, proposal):
    prior = make_review(acquisition, proposal)
    proposal.update(previous_review_sha256=prior["review_sha256"], value=False,
                    reviewed_at="2026-01-04T00:00:00Z", rationale="Synthetic correction.")
    correction = make_review(acquisition, proposal, previous_review=prior)
    assert validate_review(correction, previous_review=prior) == correction
    assert prior["proposal"]["value"] is True
    with pytest.raises(ValidationError):
        validate_review(correction)
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal)
    with pytest.raises(ValidationError):
        validate_review(prior, previous_review=prior)
    # Only immediate previous record is required: no global-history claim.
    proposal.update(previous_review_sha256=correction["review_sha256"], reviewed_at="2026-01-05T00:00:00Z")
    next_record = make_review(acquisition, proposal, previous_review=correction)
    assert validate_review(next_record, previous_review=correction) == next_record


@pytest.mark.parametrize("mutation", ["hash", "record", "snapshot", "predates"])
def test_invalid_corrections(acquisition, proposal, mutation):
    prior = make_review(acquisition, proposal)
    proposal["previous_review_sha256"] = prior["review_sha256"]
    if mutation == "hash":
        proposal["previous_review_sha256"] = "0" * 64
    elif mutation == "record":
        prior["proposal"]["attested"] = False
        rehash(prior)
        proposal["previous_review_sha256"] = prior["review_sha256"]
    elif mutation == "snapshot":
        acquisition["candidates"][0]["title"] = "Different snapshot"
        proposal["candidate_sha256"] = candidate_digest(acquisition["candidates"][0])
        proposal["acquisition_sha256"] = acquisition_digest(acquisition)
    else:
        proposal["reviewed_at"] = "2026-01-02T00:00:00Z"
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal, previous_review=prior)


@pytest.mark.parametrize("bad", ["oversize", "depth", "surrogate", "cycle", "nonstring_key", "nan", "huge_int", "many_nodes", "nonjson"])
def test_bounded_inputs_raise_controlled_errors(acquisition, proposal, bad):
    if bad == "oversize":
        value = "x" * (MAX_FILE + 1)
    elif bad == "depth":
        value = None
        for _ in range(100):
            value = [value]
    elif bad == "surrogate":
        value = "\ud800"
    elif bad == "cycle":
        value = []
        value.append(value)
    elif bad == "nonstring_key":
        value = {1: "one"}
    elif bad == "nan":
        value = float("nan")
    elif bad == "huge_int":
        value = 1 << 100_000
    elif bad == "many_nodes":
        value = [0] * 100_000
    else:
        value = object()
    proposal["rationale"] = value
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal)
    for helper in (acquisition_digest, candidate_digest, validate_review):
        with pytest.raises(ValidationError):
            helper({"unexpected": value})


def test_final_output_size_bound_not_only_individual_inputs(acquisition, proposal):
    # Both inputs fit separately, but embedding the proposal exceeds MAX_FILE.
    candidates = []
    for i in range(19):
        candidate = deepcopy(acquisition["candidates"][0])
        candidate.update(id=f"candidate-{i + 1}", content="x" * 100_000)
        candidate["content_sha256"] = hashlib.sha256(candidate["content"].encode()).hexdigest()
        candidates.append(candidate)
    acquisition["candidates"] = candidates
    # Bring source JSON to just below the ceiling using bounded metadata.
    for candidate in candidates:
        candidate["title"] = "t" * 1000
    # Source is around 1.94 MB; non-ASCII content grows its encoded byte size.
    padding = MAX_FILE - len(json_bytes(acquisition)) - 1000
    candidates[0]["content"] = "é" * padding + "x" * (100_000 - padding)
    candidates[0]["content_sha256"] = hashlib.sha256(candidates[0]["content"].encode()).hexdigest()
    proposal.update(acquisition_sha256=acquisition_digest(acquisition), candidate_id=candidates[0]["id"],
                    candidate_sha256=candidate_digest(candidates[0]), content_sha256=candidates[0]["content_sha256"],
                    quote="é", quote_start_byte=0, quote_end_byte=2, rationale="r" * 4000)
    assert len(json_bytes(acquisition)) < MAX_FILE
    with pytest.raises(ValidationError):
        make_review(acquisition, proposal)


def test_retained_live_shape_without_live_calls(acquisition, proposal):
    # Change only the synthetic fixture's claimed mode; no network is used.
    acquisition["mode"] = "live"
    acquisition["candidates"][0]["provenance"] = "tavily_acquired_unreviewed"
    acquisition["requests"][0]["provider_usage"] = {"credits": 0}
    acquisition["usage"].update(retrieval_calls=1, mock_calls=0, provider_usage=[{"credits": 0}])
    proposal.update(acquisition_sha256=acquisition_digest(acquisition),
                    candidate_sha256=candidate_digest(acquisition["candidates"][0]))
    assert validate_review(make_review(acquisition, proposal))["acquisition"]["mode"] == "live"
    acquisition["usage"]["provider_usage"][0]["credits"] = False
    with pytest.raises(ValidationError):
        acquisition_digest(acquisition)


def test_partial_snapshot_can_retain_reviewed_candidate(acquisition, proposal):
    acquisition["status"] = "partial"
    acquisition["failures"] = [{"endpoint": "/extract", "reason": "acquisition_aborted"}]
    proposal["acquisition_sha256"] = acquisition_digest(acquisition)
    document = make_review(acquisition, proposal)
    assert validate_review(document)["acquisition"]["status"] == "partial"


def test_search_snippet_not_original_document(acquisition, proposal):
    acquisition["plan"].update(queries=["synthetic public query"], urls=[])
    acquisition["candidates"][0]["endpoint"] = "/search"
    acquisition["requests"][0]["endpoint"] = "/search"
    proposal.update(acquisition_sha256=acquisition_digest(acquisition),
                    candidate_sha256=candidate_digest(acquisition["candidates"][0]))
    assert make_review(acquisition, proposal)["extraction_method"] == "search_snippet"
