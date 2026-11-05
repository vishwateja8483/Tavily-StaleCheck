"""Strict v2 report derivation from retained supplied review attestations."""
from .claims import validate_claims
from .contracts import day, json_bytes, require, sha, text
from .review import _bounded, _keys
from .reviewed_evidence import validate_reviewed_evidence
from .temporal_v2 import evaluate_v2

V2_LIMITATIONS = (
    "Offline replay: no model, retrieval, paid API, network, or credential access.",
    "Supplied attestations and reviewer identities are not authenticated authorship, semantic entailment, source truth, or independently reviewed effectiveness.",
    "Only exact subject/assertion/scope point-in-time lifecycle annotations are assessed; ambiguous or unsupported interpretations abstain.",
    "Intervals are half-open; explicit unknown end or unknown start cannot establish active coverage. Publication/retrieval dates never become effective dates.",
    "Provider publication metadata may estimate publication or update. Search snippets and extracted text are not original publisher document bytes.",
    "Corrections retain immediate prior linkage only, not a complete authenticated review history; they do not erase or temporally supersede prior statements. Only explicitly selected reviews become evidence.",
    "Partial acquisitions remain partial; reviewing selected candidates does not establish retrieval completeness. Synthetic attestations demonstrate plumbing, not actual human review.",
)


def v2_config():
    return {"package_version": "0.2.0", "algorithm": "reviewed-lifecycle-intervals-v2", "max_claims": 5, "network": False, "model": None}


def normalize_v2(value):
    _bounded(value)
    _keys(value, {"schema_version", "format", "answer_text", "answer_date", "verification_date", "scope", "claims", "reviewed_evidence"}, "v2 input")
    require(value["schema_version"] == "2.0" and value["format"] == "reviewed_lifecycle_claims", "unsupported v2 input format/version")
    text(value["answer_text"], "answer_text")
    text(value["scope"], "scope", 200)
    day(value["verification_date"], "verification_date")
    if value["answer_date"] is not None:
        day(value["answer_date"], "answer_date")
        require(value["answer_date"] <= value["verification_date"], "answer_date cannot follow verification_date")
    validate_claims(value["claims"])
    for claim in value["claims"]:
        keys = {"id", "text", "subject", "assertion", "scope", "public"}
        _keys(claim, keys | ({"as_of"} if "as_of" in claim else set()), "v2 claim")
        require(claim["public"] is True and claim["assertion"] == "is_supported", "v2 requires explicit public lifecycle claims")
        if "as_of" in claim:
            day(claim["as_of"], "as_of")
    validate_reviewed_evidence(value["reviewed_evidence"])
    return value


def make_report_v2(value, run_id):
    request = normalize_v2(value)
    reviewed = request["reviewed_evidence"]
    evidence = reviewed["evidence"]
    claims = [evaluate_v2(claim, evidence, request["answer_date"], request["verification_date"], request["scope"]) for claim in request["claims"]]
    warnings = []
    for entry in reviewed["reviews"]:
        review = entry["review"]
        if review["acquisition"]["status"] != "acquired":
            warnings.append(f"{review['review_sha256']}: acquisition status {review['acquisition']['status']}")
        if review["proposal"].get("previous_review_sha256"):
            warnings.append(f"{review['review_sha256']}: correction immediate prior retained; no automatic supersession or complete history")
    for source in evidence:
        if source["published_at"] is None:
            warnings.append(f"{source['id']}: publication date missing")
        if any(s["effective_from"] is None or s["end_state"] == "unknown" for s in source["statements"]):
            warnings.append(f"{source['id']}: explicit effective-date coverage unknown")
    return {"schema_version": "2.0", "project_id": "stalecheck", "run_id": run_id, "mode": "replay", "title": "StaleCheck reviewed temporal verification v2", "summary": f"{len(claims)} claim(s), {sum(c['verdict'] == 'ABSTAIN' for c in claims)} abstention(s); reviewed evidence-conditioned replay.", "limitations": list(V2_LIMITATIONS), "claims": claims, "evidence": evidence, "reviewed_evidence": reviewed, "answer_text": request["answer_text"], "answer_date": request["answer_date"], "verification_date": request["verification_date"], "scope": request["scope"], "warnings": warnings, "input_sha256": sha(json_bytes(value)), "config_sha256": sha(json_bytes(v2_config())), "usage": {"model_calls": 0, "retrieval_calls": 0, "provider_usage": None}, "version": "0.2.0"}
