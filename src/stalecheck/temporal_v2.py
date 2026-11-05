"""V2 explanations with explicit unknown-end semantics; v1 stays untouched."""
from .claims import interpretation_warning


def reference(source, statement):
    return {"evidence_id": source["id"], "statement_id": statement["id"], "review_sha256": statement["review_sha256"]}


def assessment(claim, evidence, date):
    result = {"date": date, "state": "unavailable" if date is None else "no_coverage", "support": [], "contradiction": [], "excluded": []}
    if date is None:
        return result
    for source in evidence:
        for statement in source["statements"]:
            reason = None
            for field in ("subject", "assertion", "scope"):
                if statement[field] != claim[field]:
                    reason = field.upper() + "_MISMATCH"
                    break
            if reason is None:
                if statement["effective_from"] is None:
                    reason = "UNKNOWN_START"
                elif statement["end_state"] == "unknown":
                    reason = "UNKNOWN_END"
                elif date < statement["effective_from"]:
                    reason = "NOT_YET_EFFECTIVE"
                elif statement["end_state"] == "dated" and date >= statement["effective_to"]:
                    reason = "ENDED"
            ref = reference(source, statement)
            if reason:
                result["excluded"].append({**ref, "reason_code": reason})
            else:
                result["support" if statement["value"] else "contradiction"].append(ref)
    if result["support"] and result["contradiction"]:
        result["state"] = "conflict"
    elif result["support"]:
        result["state"] = "supported"
    elif result["contradiction"]:
        result["state"] = "contradicted"
    return result


def evaluate_v2(claim, evidence, answer_date, verification_date, scope):
    original = claim.get("as_of")
    target = original or verification_date
    prior = assessment(claim, evidence, answer_date)
    current = assessment(claim, evidence, target)
    result = {**claim, "as_of": target, "verdict": "ABSTAIN", "reason": "", "evidence_ids": [], "citations": [], "freshness": "unknown"}
    warning = interpretation_warning(claim)
    code = "NO_COVERAGE"
    reason = "No matching evidence with known explicit effective coverage; acquisition dates do not establish applicability."
    eligible = not warning and target <= verification_date and claim["scope"] == scope
    if warning:
        code, reason = "AMBIGUOUS_CLAIM", warning
    elif target > verification_date:
        code, reason = "FUTURE_TARGET", "Claim target is after verification_date."
    elif claim["scope"] != scope:
        code, reason = "REQUEST_SCOPE_MISMATCH", "Claim scope differs from request scope; no cross-scope inference."
    elif current["state"] == "conflict":
        code, reason = "CONFLICT", "Both support and contradiction cover the target; conflict requires abstention."
    elif current["state"] == "supported":
        code, reason = "SUPPORTED", "Unconflicted reviewed support explicitly covers the target."
        result.update(verdict="SUPPORTED_AS_OF", freshness="historical-only" if original else "current evidence-supported")
    elif current["state"] == "contradicted":
        code, reason = "CONTRADICTED", "Unconflicted reviewed contradiction covers the target; stale transition is not established."
        result.update(verdict="CONTRADICTED_AS_OF", freshness="changed/contradicted")
        active_false = {ref["statement_id"] for ref in current["contradiction"]}
        changed = answer_date is not None and any(statement["id"] in active_false and answer_date < statement["effective_from"] <= target for source in evidence for statement in source["statements"])
        if original is None and prior["state"] == "supported" and changed:
            code, reason = "STALE_TRANSITION", "Unconflicted support at answer_date and later effective invalidation establish a stale transition."
            result["verdict"] = "STALE"
    used = current["support"] + current["contradiction"] if eligible else []
    if result["verdict"] == "STALE":
        used += prior["support"]
    wanted = {ref["statement_id"] for ref in used}
    for source in evidence:
        for statement in source["statements"]:
            if statement["id"] in wanted:
                result["evidence_ids"].append(source["id"])
                result["citations"].append({**reference(source, statement), **{key: statement[key] for key in ("quote", "effective_from", "effective_to", "end_state")}})
    result["reason"] = reason
    result["explanation"] = {"schema_version": "2.0", "reason_code": code, "original_as_of": original, "target_date": target, "answer_date": answer_date, "verification_date": verification_date, "prior": prior, "current": current}
    return result
