"""Product-local bounded export of exact verified snapshots."""
from datetime import datetime, timezone
from pathlib import Path
from .contracts import MAX_FILE, json_bytes, require, safe_path
from .verify_artifacts import load_verified_run

MAX_VIEWER_BYTES = 5 * 1024 * 1024


def write_exclusive_json(value, output, *, maximum=MAX_FILE):
    raw = json_bytes(value)
    require(len(raw) <= maximum, f"output exceeds {maximum}-byte bound; nothing written")
    path = Path(output).absolute()
    safe_path(path.parent, path.name)
    with path.open("xb") as stream:
        stream.write(raw)
    return {"output": str(path), "bytes": len(raw)}


def export_viewer(directories, output):
    require(isinstance(directories, list) and 1 <= len(directories) <= 20, "export requires one to 20 runs")
    runs, seen = [], set()
    for directory in directories:
        snapshot = load_verified_run(directory)
        require(snapshot["input"]["schema_version"] not in ("3.0", "4.0"), "stale-answer runs are not exportable to the viewer; render them with stale-report")
        identity = snapshot["report"]["run_id"]
        require(identity not in seen, "duplicate run identity")
        seen.add(identity)
        runs.append({key: snapshot[key] for key in ("manifest", "report", "events")})
        # Stop accumulating runs before serialization can grow without bound.
        require(sum(len(json_bytes(run)) for run in runs) <= MAX_VIEWER_BYTES, "viewer export exceeds 5 MiB; nothing written")
    envelope = {"schema_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat(), "runs": runs, "annotations": [], "limitations": ["StaleCheck-only offline export, checked by its own artifact verifier before export.", "Unsigned local consistency is not source authenticity, factual truth, semantic entailment, or authenticated reviewer authorship.", "Viewer bundle omits frozen input bytes; retain original run directories for independent artifact verification. No evidence is truncated."]}
    return {"status": "exported", "runs": len(runs), **write_exclusive_json(envelope, output, maximum=MAX_VIEWER_BYTES)}
