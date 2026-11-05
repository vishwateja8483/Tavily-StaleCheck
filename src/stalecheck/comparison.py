"""Comparison of two independently verified frozen snapshots, not causal proof."""
from copy import deepcopy
from .claims import normalize_input
from .contracts import json_bytes, require, sha
from .review import _digest
from .verify_artifacts import load_verified_run

LIMITATIONS = [
    "Each run is independently checked for local integrity and deterministic consistency, not source truth or signed authenticity.",
    "Change categories describe retained input differences, not proof of real-world causality; multiple categories may apply.",
    "Full semantic claim identity excludes display IDs; duplicate semantic identities are rejected as ambiguous.",
    "Time-only requires all other frozen input and policy values unchanged apart from display claim IDs. Review history completeness is not established.",
]


def _claims(run):
    return run["input"]["claims"] if run["input"]["schema_version"] == "2.0" else normalize_input(run["input"])["claims"]


def _identity(claim):
    identity = {**{key: claim[key] for key in ("subject", "assertion", "scope")}, "as_of": claim.get("as_of")}
    if claim.get("extraction_warning"):
        identity["text"] = claim["text"]
    return identity


def _index(run):
    result = {}
    reports = {row["id"]: row for row in run["report"]["claims"]}
    for claim in _claims(run):
        identity = _identity(claim)
        key = json_bytes(identity)
        require(key not in result, "ambiguous duplicate semantic claim identity")
        result[key] = (identity, claim, reports[claim["id"]])
    return result


def _sorted(rows):
    return sorted(rows, key=json_bytes)


def _projections(run):
    report, request = run["report"], run["input"]
    evidence = report["evidence"]
    return {
        "content": _sorted([{key: source.get(key) for key in ("url", "content", "content_sha256")} for source in evidence]),
        "extraction": {"answer_text": report["answer_text"], "claims": [{key: value for key, value in claim.items() if key != "id"} for claim in _claims(run)], "sources": _sorted([{"url": source["url"], "extraction_method": source.get("extraction_method"), "quotes": [{key: s.get(key) for key in ("quote", "quote_start_byte", "quote_end_byte")} for s in source["statements"]]} for source in evidence])},
        "statement": _sorted([{key: s.get(key) for key in ("subject", "assertion", "scope", "value", "effective_from", "effective_to", "end_state")} for source in evidence for s in source["statements"]]),
        "policy": {"config": run["config"], "schema_version": request["schema_version"], "format": request.get("format", "answer"), "scope": request["scope"]},
        "clock": {"answer_date": report["answer_date"], "verification_date": report["verification_date"]},
        "provenance": _sorted([{key: value for key, value in source.items() if key not in {"id", "content", "content_sha256", "statements", "extraction_method"}} for source in evidence]),
    }


def _without_clocks(run):
    value = deepcopy(run["input"])
    value.pop("answer_date", None)
    value.pop("verification_date", None)
    if "claims" in value:
        for claim in value["claims"]:
            claim.pop("id", None)
    return {"input": value, "config": run["config"]}


def make_comparison(before_directory, after_directory):
    before = load_verified_run(before_directory)
    after = load_verified_run(after_directory)
    return compare_verified(before, after)


def compare_verified(before, after):
    """Internal helper; callers must provide snapshots from load_verified_run."""
    left, right = _index(before), _index(after)
    bp, ap = _projections(before), _projections(after)
    causes = [key for key in bp if bp[key] != ap[key]]
    # Retain a catch-all for input differences not represented by projections.
    # In particular, reviewer rationale/prior review changes must not disappear.
    if _without_clocks(before) != _without_clocks(after) and not any(key != "clock" for key in causes):
        causes.append("provenance")
    time_only = causes == ["clock"] and _without_clocks(before) == _without_clocks(after)
    rows = []
    for key in sorted(left.keys() | right.keys()):
        b, a = left.get(key), right.get(key)
        rows.append({"identity": (b or a)[0], "status": "matched" if b and a else "removed" if b else "added", "before_claim_id": b[1]["id"] if b else None, "after_claim_id": a[1]["id"] if a else None, "before_verdict": b[2]["verdict"] if b else None, "after_verdict": a[2]["verdict"] if a else None, "verdict_changed": bool(b and a and b[2]["verdict"] != a[2]["verdict"])})
    def ref(run):
        return {"run_id": run["report"]["run_id"], **{key + "_sha256": value for key, value in run["digests"].items()}}
    return {"schema_version": "2.0", "format": "frozen_run_comparison", "before": ref(before), "after": ref(after), "causes": causes, "time_only": time_only, "claims": rows, "limitations": list(LIMITATIONS)}


def verify_comparison(document, before_directory, after_directory):
    require(_digest(document) == _digest(make_comparison(before_directory, after_directory)), "comparison differs from independently verified runs")
    return {"valid": True, "assurance": "Recomputed frozen-input difference attribution, not causal proof or authenticity."}
