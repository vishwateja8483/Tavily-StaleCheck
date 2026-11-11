"""Offline integrity and domain consistency verification, never source authentication."""
from pathlib import Path
from .contracts import DIGEST, MAX_FILE, MAX_TOTAL, decode, identifier, json_bytes, obj, require, safe_path, sha, string_list, timestamp
from .versions import report_builder

REQUIRED = {"input.json", "config.json", "report.json", "events.jsonl", "errors.jsonl"}


def _read(path):
    require(path.is_file(), f"missing/nonregular artifact: {path.name}")
    require(path.stat().st_size <= MAX_FILE, f"artifact too large: {path.name}")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE + 1)
    require(len(data) <= MAX_FILE, "artifact exceeded read bound")
    return data


def verify_artifacts(directory):
    return load_verified_run(directory)["verification"]


def load_verified_run(directory, *, include_bytes=False):
    """Return the exact byte snapshot verified, never a later unchecked reread."""
    root = Path(directory).absolute()
    require(root.is_dir(), "run directory missing")
    manifest_raw = _read(safe_path(root, "manifest.json"))
    manifest = obj(decode(manifest_raw), "manifest")
    expected_keys = {"schema_version", "project_id", "run_id", "mode", "status", "created_at", "artifacts", "limitations"}
    require(set(manifest) == expected_keys, "manifest fields do not match schema")
    require(manifest["schema_version"] == "1.0", "unsupported manifest schema")
    require(manifest["project_id"] == "stalecheck", "wrong project_id")
    run_id = identifier(manifest["run_id"], "run_id")
    require(manifest["mode"] == "replay", "unsupported execution mode")
    require(manifest["status"] in {"completed", "incomplete", "blocked", "failed", "cancelled"}, "invalid status")
    require(manifest["status"] == "completed", f"run not completed: {manifest['status']}")
    created = timestamp(manifest["created_at"], "created_at")
    string_list(manifest["limitations"], "limitations")
    entries = manifest["artifacts"]
    require(isinstance(entries, list) and 1 <= len(entries) <= 100, "invalid artifact list")
    files, folded, total = {}, set(), 0
    for entry in entries:
        obj(entry, "artifact")
        require(set(entry) == {"path", "sha256", "bytes"}, "invalid artifact fields")
        path = safe_path(root, entry["path"])
        require(entry["path"].casefold() not in folded and entry["path"].casefold() != "manifest.json", "duplicate/reserved artifact path")
        folded.add(entry["path"].casefold())
        require(type(entry["bytes"]) is int and 0 <= entry["bytes"] <= MAX_FILE, "invalid byte length")
        require(isinstance(entry["sha256"], str) and DIGEST.fullmatch(entry["sha256"]), "invalid digest")
        total += entry["bytes"]
        require(total <= MAX_TOTAL, "run exceeds total size bound")
        data = _read(path)
        require(len(data) == entry["bytes"] and sha(data) == entry["sha256"], f"artifact digest/size mismatch: {entry['path']}")
        files[entry["path"]] = data
    require(REQUIRED == set(files), "unexpected or missing registered artifact")
    # Runs are flat: reject every directory, including otherwise invisible empty ones.
    actual = set()
    visited = 0
    for path in root.iterdir():
        visited += 1
        require(visited <= 300, "too many run directory entries")
        relative = path.relative_to(root).as_posix()
        safe_path(root, relative)
        require(not path.is_dir(), "unexpected run subdirectory")
        require(path.is_file(), "nonregular run entry")
        actual.add(relative)
    require(actual == set(files) | {"manifest.json"}, "unregistered or missing run files")
    request = decode(files["input.json"])
    config = decode(files["config.json"])
    make_report = report_builder(request, config)
    report = obj(decode(files["report.json"]), "report")
    # Pure deterministic interval validation also checks IDs, quotations, dates, links,
    # provenance, prior validity, and every report reference against the frozen input.
    expected = make_report(request, run_id)
    require(report == expected, "report differs from validated frozen input (IDs, references, dates, quotations or verdicts)")
    # Python equality treats True == 1 and 0 == False; all schemas also need type-exact comparison.
    import json
    require(json.dumps(report, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False), "report types differ from derivation")
    require(manifest["limitations"] == report["limitations"], "manifest/report limitations mismatch")
    lines = files["events.jsonl"].splitlines()
    require(2 <= len(lines) <= 1000, "invalid event count")
    events = [obj(decode(line), "event") for line in lines]
    previous = created
    for sequence, event in enumerate(events, 1):
        require(set(event) == {"sequence", "type", "run_id", "timestamp", "data"}, "invalid event fields")
        require(type(event["sequence"]) is int and event["sequence"] == sequence, "event sequence gap/duplicate")
        require(event["run_id"] == run_id, "event run_id mismatch")
        require(isinstance(event["type"], str), "invalid event type")
        obj(event["data"], "event data")
        when = timestamp(event["timestamp"], "event timestamp")
        require(when >= previous, "events precede creation or go backward")
        previous = when
    require(len(events) == 3 and [event["type"] for event in events] == ["run_started", "claims_verified", "run_completed"], "unexpected claims_verified replay event sequence")
    require(events[0]["data"] == {"mode": "replay"} and events[-1]["data"] == {}, "replay start/completion data mismatch")
    verified_data = events[1]["data"]
    require(set(verified_data) == {"count"}, "invalid claims_verified data fields")
    require(type(verified_data["count"]) is int and verified_data["count"] == len(expected["claims"]), "claims_verified count differs from derived report")
    require(not files["errors.jsonl"].strip(), "completed replay has unexpected recorded errors")
    verification = {"valid": True, "project_id": "stalecheck", "run_id": run_id, "status": "completed", "artifacts": len(files), "assurance": "Internal consistency only; unsigned hashes cannot authenticate sources or establish truth."}
    snapshot = {"verification": verification, "manifest": manifest, "report": report, "events": events, "input": request, "config": config,
                "digests": {"manifest": sha(manifest_raw), **{name[:-5]: sha(files[name]) for name in ("input.json", "config.json", "report.json")}}}
    if include_bytes:
        snapshot["artifact_bytes"] = {**files, "manifest.json": manifest_raw}
    return snapshot
