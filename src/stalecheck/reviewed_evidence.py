"""Explicit review-to-evidence conversion; no inferred approvals or dates."""
from .contracts import decode, json_bytes, require
from .review import _bounded, _digest, _keys, validate_review


def _derive(records):
    require(isinstance(records, list) and 1 <= len(records) <= 100, "provide one to 100 supplied reviews")
    evidence, seen = [], set()
    for entry in records:
        _keys(entry, {"review", "previous_review"}, "review entry")
        review = validate_review(entry["review"], previous_review=entry["previous_review"])
        digest = review["review_sha256"]
        require(digest not in seen, "duplicate review digest")
        seen.add(digest)
        proposal = review["proposal"]
        candidate = next(row for row in review["acquisition"]["candidates"] if row["id"] == review["candidate_id"])
        statement = {key: proposal[key] for key in ("subject", "assertion", "scope", "value", "quote", "effective_from", "end_state", "quote_start_byte", "quote_end_byte")}
        statement.update(id="statement-" + digest, effective_to=proposal.get("effective_to"), annotation_provenance="user_reviewed", review_sha256=digest)
        source = {key: candidate[key] for key in ("title", "url", "content", "content_sha256", "retrieved_at", "published_at")}
        source.update(id="review-" + digest, provenance="human_attested_acquisition", statements=[statement])
        source.update({key: review[key] for key in ("review_sha256", "candidate_sha256", "acquisition_sha256", "extraction_method")})
        evidence.append(source)
    return evidence


def convert_reviews(value):
    """Require validated explicit attestations, retaining acquisitions verbatim."""
    _bounded(value)
    _keys(value, {"schema_version", "format", "reviews"}, "review conversion")
    require(value["schema_version"] == "2.0" and value["format"] == "review_conversion", "unsupported review conversion version/format")
    document = {"schema_version": "2.0", "format": "reviewed_evidence", "reviews": value["reviews"], "evidence": _derive(value["reviews"])}
    _bounded(document)
    return decode(json_bytes(document))


def validate_reviewed_evidence(value):
    _bounded(value)
    _keys(value, {"schema_version", "format", "reviews", "evidence"}, "reviewed evidence")
    require(value["schema_version"] == "2.0" and value["format"] == "reviewed_evidence", "unsupported reviewed evidence version/format")
    require(_digest(value["evidence"]) == _digest(_derive(value["reviews"])), "reviewed evidence differs from supplied review derivation")
    return value
