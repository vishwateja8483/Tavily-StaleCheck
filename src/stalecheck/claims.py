"""Explicit structured claims or one documented, deliberately narrow line grammar."""
import re
from .contracts import day, identifier, obj, require, text, unique

GRAMMAR = re.compile(r"^(?:As of (\d{4}-\d{2}-\d{2}), )?([A-Za-z0-9][A-Za-z0-9 ._/-]{0,99}) is supported in ([A-Za-z0-9][A-Za-z0-9 ._/-]{0,99})\.$")
HISTORIC = re.compile(r"\b(was|were|previously|formerly|historically|last year|last month|as of|until|before|after|since|from|during|in \d{4})\b", re.I)


def validate_claims(claims):
    require(isinstance(claims, list) and 1 <= len(claims) <= 5, "provide one to five claims; subdivide larger answers")
    unique(claims, "claim")
    for claim in claims:
        text(claim.get("text"), "claim text")
        for field in ("subject", "assertion", "scope"):
            text(claim.get(field), field, 200)
        require(claim["assertion"] == "is_supported" or claim.get("extraction_warning"), "only is_supported lifecycle assertions are supported")
        if claim.get("as_of") is not None:
            day(claim["as_of"], "claim as_of")
    return claims


def parse_answer(answer):
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    require(1 <= len(lines) <= 5, "provide one to five nonempty claim lines; subdivide larger answers")
    claims = []
    for n, line in enumerate(lines, 1):
        match = GRAMMAR.fullmatch(line)
        claim = {"id": f"claim-{n}", "text": line, "subject": "unknown", "assertion": "unsupported", "scope": "unknown"}
        if match:
            as_of, subject, scope = match.groups()
            claim.update(subject=subject, assertion="is_supported", scope=scope)
            if as_of:
                day(as_of, "historical date")
                claim["as_of"] = as_of
        else:
            claim["extraction_warning"] = "Unsupported line grammar; no partial claim extraction or inferred meaning."
        claims.append(claim)
    return validate_claims(claims)


def normalize_input(value):
    obj(value, "input")
    require(value.get("schema_version") == "1.0", "unsupported input schema version")
    require(value.get("format", "answer") in {"answer", "public_lifecycle_claims"}, "unsupported ingestion format")
    text(value.get("scope"), "request scope", 200)
    day(value.get("verification_date"), "verification_date")
    if value.get("answer_date") is not None:
        day(value["answer_date"], "answer_date")
        require(value["answer_date"] <= value["verification_date"], "answer_date cannot follow verification_date")
    if value.get("format") == "public_lifecycle_claims":
        supplied = value.get("claims")
        require(isinstance(supplied, list) and all(type(claim) is dict and "extraction_warning" not in claim
                for claim in supplied), "structured claims cannot supply extraction_warning")
        claims = validate_claims(supplied)
        # An explicit ingestion format for reviewed public annotations, not inferred prose.
        for claim in claims:
            require(claim.get("public") is True, "public_lifecycle_claims requires public=true per claim")
        answer = value.get("answer_text", "\n".join(c["text"] for c in claims))
        text(answer, "answer_text")
    else:
        answer = text(value.get("answer_text"), "answer_text")
        claims = parse_answer(answer)
        require("claims" not in value, "use public_lifecycle_claims format for structured annotations")
    require(isinstance(value.get("evidence"), list) and len(value["evidence"]) <= 100, "evidence must be an array of at most 100 sources")
    return {**value, "answer_text": answer, "claims": claims}


def interpretation_warning(claim):
    if claim.get("extraction_warning"):
        return claim["extraction_warning"]
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", claim["text"])
    if dates and (not claim.get("as_of") or any(d != claim["as_of"] for d in dates)):
        return "Text date disagrees with structured as_of, or is not explicitly annotated."
    if HISTORIC.search(claim["text"]) and not claim.get("as_of"):
        return "Historical or interval wording has no unambiguous point-in-time annotation."
    # A point annotation must not erase an interval assertion.
    if re.search(r"\b(until|before|after|since|from|during|last year|last month)\b", claim["text"], re.I):
        return "Interval or relative-date wording is outside supported point-in-time semantics."
    return None
