"""Build deterministic reports and write new, self-contained run directories."""
from datetime import datetime, timezone
from pathlib import Path
import json
import uuid
from .claims import normalize_input
from .contracts import json_bytes, require, safe_path, sha
from .evidence import validate_evidence
from .temporal import evaluate

from .versions import V1_VERSION, V1_LIMITATIONS, v1_config, report_builder

VERSION = V1_VERSION
CONFIG = v1_config()
LIMITATIONS = list(V1_LIMITATIONS)


def make_report(value, run_id):
    return report_builder(value)(value, run_id)


def make_report_v1(value, run_id):
    request = normalize_input(value)
    evidence = validate_evidence(request["evidence"])
    claims = [evaluate(c, evidence, request.get("answer_date"), request["verification_date"], request["scope"]) for c in request["claims"]]
    warnings = []
    for source in evidence:
        if source.get("published_at") is None:
            warnings.append(f"{source['id']}: publication date missing")
        if not source["statements"] or any(s.get("effective_from") is None for s in source["statements"]):
            warnings.append(f"{source['id']}: effective-date coverage incomplete")
    return {"project_id": "stalecheck", "run_id": run_id, "mode": "replay", "title": "StaleCheck frozen temporal verification", "summary": f"{len(claims)} claim(s), {sum(c['verdict'] == 'ABSTAIN' for c in claims)} abstention(s); evidence-conditioned deterministic replay.", "limitations": list(V1_LIMITATIONS), "claims": claims, "evidence": evidence, "answer_text": request["answer_text"], "answer_date": request.get("answer_date"), "verification_date": request["verification_date"], "scope": request["scope"], "warnings": warnings, "input_sha256": sha(json_bytes(value)), "config_sha256": sha(json_bytes(v1_config())), "usage": {"model_calls": 0, "retrieval_calls": 0, "provider_usage": None}, "version": V1_VERSION}


def write_run(value, output):
    run_id = "fd-" + uuid.uuid4().hex
    report = make_report(value, run_id)  # Validate before creating output.
    config = v1_config()
    if value["schema_version"] == "2.0":
        from .report_v2 import v2_config
        config = v2_config()
    elif value["schema_version"] == "3.0":
        from .stale_check import v3_config
        config = v3_config()
    elif value["schema_version"] == "4.0":
        from .stale_check_v4 import v4_config
        config = v4_config()
    root = Path(output).absolute()
    safe_path(root.parent, root.name)
    require(not root.exists(), "output already exists; completed artifacts are never overwritten")
    root.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc).isoformat()
    events = [{"sequence": 1, "type": "run_started", "run_id": run_id, "timestamp": now, "data": {"mode": "replay"}}, {"sequence": 2, "type": "claims_verified", "run_id": run_id, "timestamp": now, "data": {"count": len(report["claims"])}}, {"sequence": 3, "type": "run_completed", "run_id": run_id, "timestamp": now, "data": {}}]
    payloads = {"input.json": json_bytes(value), "config.json": json_bytes(config), "report.json": json_bytes(report), "events.jsonl": b"".join((json.dumps(e, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8") for e in events), "errors.jsonl": b""}
    manifest = {"schema_version": "1.0", "project_id": "stalecheck", "run_id": run_id, "mode": "replay", "status": "incomplete", "created_at": now, "artifacts": [], "limitations": list(report["limitations"])}
    # A crash leaves explicit incomplete state (never silently resumed).
    (root / "manifest.json").write_bytes(json_bytes(manifest))
    for name, data in payloads.items():
        require(len(data) <= 2_000_000, "generated artifact exceeds size bound")
        (root / name).write_bytes(data)
        manifest["artifacts"].append({"path": name, "sha256": sha(data), "bytes": len(data)})
    manifest["status"] = "completed"
    (root / "manifest.json").write_bytes(json_bytes(manifest))
    return {"run_id": run_id, "output": str(root), "status": "completed", "claims": len(report["claims"])}
