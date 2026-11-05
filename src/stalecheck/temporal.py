"""Deterministic interval reasoning, not language-model or truth verification."""
from .claims import interpretation_warning


def _active(statement, target):
    return statement.get("effective_from") is not None and statement["effective_from"] <= target and (statement.get("effective_to") is None or target < statement["effective_to"])


def evaluate(claim, evidence, answer_date, verification_date, scope):
    target = claim.get("as_of") or verification_date
    result = {**claim, "as_of": target, "verdict": "ABSTAIN", "reason": "", "evidence_ids": [], "citations": [], "freshness": "unknown"}
    warning = interpretation_warning(claim)
    if warning or target > verification_date or claim["scope"] != scope:
        result["reason"] = warning or ("Claim date is after verification date." if target > verification_date else "Claim scope differs from request scope; no cross-edition inference.")
        return result
    matching = [(source, statement) for source in evidence for statement in source["statements"] if all(statement[field] == claim[field] for field in ("subject", "assertion", "scope"))]
    active = [(source, statement) for source, statement in matching if _active(statement, target)]
    values = {s["value"] for _, s in active}
    used = active
    if not values:
        result["reason"] = "No matching evidence with explicit effective coverage at the claim date; publication and retrieval dates do not establish validity."
    elif len(values) > 1:
        result["reason"] = "Conflicting matching evidence covers the same date and scope."
    elif values == {True}:
        result.update(verdict="SUPPORTED_AS_OF", reason="Matching annotated evidence explicitly covers the claim date and exact scope.", freshness="historical-only" if claim.get("as_of") else "current evidence-supported")
    else:
        result.update(verdict="CONTRADICTED_AS_OF", reason="Matching annotated evidence contradicts the claim at the claim date; prior applicability has not been established.", freshness="changed/contradicted")
        if not claim.get("as_of") and answer_date:
            prior = [(source, statement) for source, statement in matching if _active(statement, answer_date)]
            # A stale transition needs unconflicted true-at-answer evidence AND a later false interval.
            changed = [(source, statement) for source, statement in active if answer_date < statement["effective_from"] <= target]
            if {s["value"] for _, s in prior} == {True} and changed:
                used = active + prior
                result.update(verdict="STALE", reason="Evidence establishes applicability at answer_date and a later effective invalidating change before verification_date.")
        elif claim.get("as_of"):
            result["reason"] = "Historical claim contradicted at its historical date, not made stale by today's state."
    seen = set()
    for source, statement in used:
        key = (source["id"], statement["id"])
        if key in seen:
            continue
        seen.add(key)
        if source["id"] not in result["evidence_ids"]:
            result["evidence_ids"].append(source["id"])
        result["citations"].append({"evidence_id": source["id"], "statement_id": statement["id"], "quote": statement["quote"], "effective_from": statement.get("effective_from"), "effective_to": statement.get("effective_to")})
    return result
