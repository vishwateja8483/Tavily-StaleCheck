"""V2 acceptance uses invented synthetic attestations, never live acquisition."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from stalecheck.acquisition import MockTransport, acquire
from stalecheck.cli import main
from stalecheck.comparison import make_comparison, verify_comparison
from stalecheck.contracts import ValidationError, json_bytes, sha
from stalecheck.export import export_viewer, write_exclusive_json
from stalecheck.report import make_report, write_run
from stalecheck.review import acquisition_digest, candidate_digest, make_review
from stalecheck.reviewed_evidence import convert_reviews, validate_reviewed_evidence
from stalecheck.verify_artifacts import verify_artifacts


def inputs():
    content = "Synthetic lifecycle: support starts 2025-01-01 and ends 2026-06-01. Unsupported from 2026-06-01."
    plan = {"schema_version": "1.0", "format": "reviewed_public_acquisition", "public_reviewed": True, "queries": [], "urls": ["https://example.com/synthetic-lifecycle"], "primary_domains": []}
    responses = [{"path": "/extract", "status": 200, "body": {"results": [{"url": plan["urls"][0], "title": "Invented synthetic lifecycle", "raw_content": content}], "failed_results": []}}]
    acquisition = acquire(plan, transport=MockTransport(responses), now=lambda: "2026-11-01T00:00:00Z")
    candidate = acquisition["candidates"][0]
    proposal = {"acquisition_sha256": acquisition_digest(acquisition), "candidate_id": candidate["id"], "candidate_sha256": candidate_digest(candidate), "content_sha256": candidate["content_sha256"], "quote": content, "quote_start_byte": 0, "quote_end_byte": len(content.encode()), "subject": "Synthetic service", "assertion": "is_supported", "scope": "synthetic", "value": True, "effective_from": "2025-01-01", "end_state": "dated", "effective_to": "2026-06-01", "reviewer": "Invented synthetic reviewer, not actual human review", "attested": True, "reviewed_at": "2026-11-01T01:00:00Z", "rationale": "Invented dates for deterministic offline controls, not publisher truth."}
    return plan, responses, acquisition, proposal


def request_with(proposals=None, acquisition=None):
    _, _, original, proposal = inputs()
    acquisition = acquisition or original
    if proposals is None:
        second = {**proposal, "value": False, "effective_from": "2026-06-01", "end_state": "open_ended"}
        second.pop("effective_to")
        proposals = [proposal, second]
    records = [{"review": make_review(acquisition, p), "previous_review": None} for p in proposals]
    evidence = convert_reviews({"schema_version": "2.0", "format": "review_conversion", "reviews": records})
    return {"schema_version": "2.0", "format": "reviewed_lifecycle_claims", "answer_text": "Synthetic service is supported in synthetic.", "answer_date": "2026-01-01", "verification_date": "2026-11-01", "scope": "synthetic", "claims": [{"id": "claim-1", "text": "Synthetic service is supported in synthetic.", "subject": "Synthetic service", "assertion": "is_supported", "scope": "synthetic", "public": True}], "reviewed_evidence": evidence}


def claim(value):
    return make_report(value, "test-run")["claims"][0]


def rewrite(directory, name, value):
    raw = json_bytes(value)
    (directory / name).write_bytes(raw)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    for entry in manifest["artifacts"]:
        if entry["path"] == name:
            entry.update(bytes=len(raw), sha256=sha(raw))
    (directory / "manifest.json").write_bytes(json_bytes(manifest))


@pytest.mark.parametrize("version", ["1.0", "2.0"])
@pytest.mark.parametrize("mutation", ["zero", "boolean", "float", "missing_count", "extra_data", "missing_event", "duplicate_event"])
def test_rehashed_claims_verified_correspondence(tmp_path, version, mutation):
    value = request_with() if version == "2.0" else {
        "schema_version": "1.0", "answer_text": "Synthetic service is supported in synthetic.",
        "scope": "synthetic", "verification_date": "2026-11-01", "evidence": [],
    }
    run = tmp_path / "run"
    write_run(value, run)
    assert verify_artifacts(run)["valid"]
    events = [json.loads(line) for line in (run / "events.jsonl").read_bytes().splitlines()]
    assert events[1]["data"]["count"] == 1
    if mutation == "zero": events[1]["data"]["count"] = 0
    elif mutation == "boolean": events[1]["data"]["count"] = True
    elif mutation == "float": events[1]["data"]["count"] = 1.0
    elif mutation == "missing_count": events[1]["data"] = {}
    elif mutation == "extra_data": events[1]["data"]["extra"] = "not derived"
    elif mutation == "missing_event": events.pop(1)
    else: events.insert(2, deepcopy(events[1]))
    for sequence, event in enumerate(events, 1):
        event["sequence"] = sequence
    raw = b"".join(json.dumps(event).encode() + b"\n" for event in events)
    (run / "events.jsonl").write_bytes(raw)
    manifest = json.loads((run / "manifest.json").read_bytes())
    for entry in manifest["artifacts"]:
        if entry["path"] == "events.jsonl":
            entry.update(bytes=len(raw), sha256=sha(raw))
    (run / "manifest.json").write_bytes(json_bytes(manifest))
    with pytest.raises(ValidationError, match="claims_verified"):
        verify_artifacts(run)
    output = tmp_path / "viewer.json"
    with pytest.raises(ValidationError, match="claims_verified"):
        export_viewer([run], output)
    assert not output.exists()


def test_stale_explanation_and_review_preservation():
    value = request_with()
    result = claim(value)
    assert result["verdict"] == "STALE"
    explanation = result["explanation"]
    assert explanation["reason_code"] == "STALE_TRANSITION"
    assert explanation["original_as_of"] is None
    assert explanation["prior"]["state"] == "supported"
    assert explanation["current"]["state"] == "contradicted"
    assert len(result["citations"]) == 2
    assert explanation["current"]["excluded"][0]["reason_code"] == "ENDED"
    reviewed = value["reviewed_evidence"]
    assert validate_reviewed_evidence(reviewed) == reviewed
    assert reviewed["reviews"][0]["review"]["acquisition"]["review_required"] is True
    assert reviewed["evidence"][0]["content_sha256"] == reviewed["reviews"][0]["review"]["content_sha256"]


@pytest.mark.parametrize("end_state,expected", [("unknown", "ABSTAIN"), ("open_ended", "SUPPORTED_AS_OF"), ("dated", "CONTRADICTED_AS_OF")])
def test_explicit_end_states(end_state, expected):
    _, _, _, proposal = inputs()
    proposal["end_state"] = end_state
    if end_state != "dated":
        proposal.pop("effective_to")
    value = request_with([proposal])
    result = claim(value)
    assert result["verdict"] == ("ABSTAIN" if end_state == "dated" else expected)
    if end_state == "unknown":
        assert result["explanation"]["current"]["excluded"][0]["reason_code"] == "UNKNOWN_END"
        assert result["citations"] == []


@pytest.mark.parametrize("case", ["unknown_start", "future_start", "conflict", "prior_conflict", "no_prior", "historical", "future_claim", "scope", "ambiguous"])
def test_temporal_negative_controls(case):
    _, _, _, p = inputs()
    false = {**p, "value": False, "effective_from": "2026-06-01", "end_state": "open_ended"}
    false.pop("effective_to")
    if case in ("unknown_start", "future_start"):
        p.pop("effective_to")
        p.update(end_state="open_ended", effective_from=None if case == "unknown_start" else "2027-01-01")
        value = request_with([p])
    elif case == "conflict":
        p.pop("effective_to")
        p["end_state"] = "open_ended"
        value = request_with([p, false])
    elif case == "prior_conflict":
        prior_false = {**p, "value": False}
        value = request_with([p, prior_false, false])
    else:
        value = request_with()
    if case == "no_prior":
        value["answer_date"] = None
    elif case == "historical":
        value["claims"][0]["as_of"] = "2026-11-01"
    elif case == "future_claim":
        value["claims"][0]["as_of"] = "2027-01-01"
    elif case == "scope":
        value["scope"] = "another"
    elif case == "ambiguous":
        value["claims"][0]["text"] = "Synthetic service was supported."
    result = claim(value)
    assert result["verdict"] == ("CONTRADICTED_AS_OF" if case in ("prior_conflict", "no_prior", "historical") else "ABSTAIN")


def test_corrections_explicitly_selected_not_automatic_supersession():
    _, _, acquisition, p = inputs()
    p.pop("effective_to")
    p["end_state"] = "open_ended"
    prior = make_review(acquisition, p)
    correction = make_review(acquisition, {**p, "value": False, "previous_review_sha256": prior["review_sha256"]}, previous_review=prior)
    records = [{"review": correction, "previous_review": prior}]
    converted = convert_reviews({"schema_version": "2.0", "format": "review_conversion", "reviews": records})
    value = request_with()
    value["reviewed_evidence"] = converted
    assert claim(value)["verdict"] == "CONTRADICTED_AS_OF"
    records.append({"review": prior, "previous_review": None})
    value["reviewed_evidence"] = convert_reviews({"schema_version": "2.0", "format": "review_conversion", "reviews": records})
    assert claim(value)["verdict"] == "ABSTAIN"
    records[0]["previous_review"] = None
    with pytest.raises(ValidationError):
        convert_reviews({"schema_version": "2.0", "format": "review_conversion", "reviews": records})


def test_partial_acquisition_remains_disclosed():
    _, _, acquisition, p = inputs()
    acquisition.update(status="partial", failures=[{"endpoint": "/extract", "reason": "acquisition_aborted"}])
    p["acquisition_sha256"] = acquisition_digest(acquisition)
    value = request_with([p], acquisition)
    report = make_report(value, "test")
    assert any("acquisition status partial" in warning for warning in report["warnings"])
    assert report["reviewed_evidence"]["reviews"][0]["review"]["acquisition"]["status"] == "partial"


@pytest.mark.parametrize("mutation", ["version", "extra", "attestation", "end", "quote", "hash", "duplicate", "mixed"])
def test_conversion_tamper_rejected(mutation):
    value = request_with()["reviewed_evidence"]
    if mutation == "version": value["schema_version"] = "1.0"
    elif mutation == "extra": value["extra"] = True
    elif mutation == "attestation": value["reviews"][0]["review"]["proposal"]["attested"] = False
    elif mutation == "end": value["evidence"][0]["statements"][0]["end_state"] = "open_ended"
    elif mutation == "quote": value["evidence"][0]["statements"][0]["quote_start_byte"] = 1
    elif mutation == "hash": value["evidence"][0]["candidate_sha256"] = "0" * 64
    elif mutation == "duplicate": value["reviews"].append(deepcopy(value["reviews"][0]))
    else: value["reviews"][0]["review"]["schema_version"] = "2.0"
    with pytest.raises(ValidationError): validate_reviewed_evidence(value)


@pytest.mark.parametrize("mutation", ["explanation", "config", "input", "incomplete", "identity"])
def test_artifact_rehashed_tampering(tmp_path, mutation):
    run = tmp_path / "run"
    write_run(request_with(), run)
    assert verify_artifacts(run)["valid"]
    if mutation == "explanation":
        report = json.loads((run / "report.json").read_bytes())
        report["claims"][0]["explanation"]["reason_code"] = "SUPPORTED"
        rewrite(run, "report.json", report)
    elif mutation == "config":
        config = json.loads((run / "config.json").read_bytes())
        config["max_claims"] = 5.0
        rewrite(run, "config.json", config)
    elif mutation == "input":
        value = request_with(); value["schema_version"] = "1.0"
        rewrite(run, "input.json", value)
    else:
        manifest = json.loads((run / "manifest.json").read_bytes())
        manifest["status" if mutation == "incomplete" else "run_id"] = "incomplete" if mutation == "incomplete" else "different"
        (run / "manifest.json").write_bytes(json_bytes(manifest))
    with pytest.raises(ValidationError): verify_artifacts(run)
    output = tmp_path / "viewer.json"
    with pytest.raises(ValidationError): export_viewer([run], output)
    assert not output.exists()


def test_v1_unparsed_lines_have_distinct_comparison_identities(tmp_path):
    value = {'schema_version': '1.0', 'answer_text': 'Foo is great.\nBar is great.',
             'scope': 'community', 'verification_date': '2026-11-01', 'evidence': []}
    before, after = tmp_path / 'before', tmp_path / 'after'
    write_run(value, before)
    comparison = make_comparison(before, before)
    assert len(comparison['claims']) == 2
    assert all(row['status'] == 'matched' for row in comparison['claims'])
    value['answer_text'] = 'Baz is great.'
    write_run(value, after)
    changed = make_comparison(before, after)
    assert [row['status'] for row in changed['claims']].count('matched') == 0


def test_comparison_time_only_and_semantic_identity(tmp_path):
    value = request_with()
    before, after = tmp_path / "before", tmp_path / "after"
    value["verification_date"] = "2026-05-01"
    write_run(value, before)
    value["verification_date"] = "2026-11-01"
    value["claims"][0]["id"] = "new-display-id"
    write_run(value, after)
    comparison = make_comparison(before, after)
    assert comparison["causes"] == ["clock"]
    assert comparison["time_only"] is True
    assert comparison["claims"][0]["verdict_changed"] is True
    assert verify_comparison(comparison, before, after)["valid"]
    comparison["time_only"] = False
    with pytest.raises(ValidationError): verify_comparison(comparison, before, after)


def test_comparison_reused_id_does_not_match_other_semantics(tmp_path):
    value = request_with()
    before, after = tmp_path / "before", tmp_path / "after"
    write_run(value, before)
    value["claims"][0]["subject"] = "Different service"
    write_run(value, after)
    result = make_comparison(before, after)
    assert {row["status"] for row in result["claims"]} == {"added", "removed"}
    assert not result["time_only"]


def test_comparison_duplicate_semantics_rejected(tmp_path):
    value = request_with()
    value["claims"].append({**value["claims"][0], "id": "other-id"})
    run = tmp_path / "run"
    write_run(value, run)
    with pytest.raises(ValidationError, match="ambiguous"):
        make_comparison(run, run)


def test_multiple_comparison_causes(tmp_path):
    value = request_with()
    before, after = tmp_path / "before", tmp_path / "after"
    write_run(value, before)
    _, _, acquisition, proposal = inputs()
    candidate = acquisition["candidates"][0]
    candidate["content"] += " More synthetic context."
    candidate["content_sha256"] = sha(candidate["content"].encode())
    proposal.update(acquisition_sha256=acquisition_digest(acquisition), candidate_sha256=candidate_digest(candidate), content_sha256=candidate["content_sha256"], effective_to="2026-07-01")
    value = request_with([proposal], acquisition)
    value["verification_date"] = "2026-12-01"
    write_run(value, after)
    result = make_comparison(before, after)
    assert {"content", "statement", "clock", "provenance"} <= set(result["causes"])
    assert not result["time_only"]


def test_export_envelope_exclusive_and_bounds(tmp_path, monkeypatch):
    run = tmp_path / "run"
    write_run(request_with(), run)
    output = tmp_path / "viewer.json"
    export_viewer([run], output)
    raw = output.read_bytes()
    envelope = json.loads(raw)
    assert set(envelope) == {"schema_version", "generated_at", "runs", "annotations", "limitations"}
    assert envelope["schema_version"] == "1.0"
    assert envelope["runs"][0]["report"]["schema_version"] == "2.0"
    with pytest.raises(FileExistsError): export_viewer([run], output)
    assert output.read_bytes() == raw
    with pytest.raises(ValidationError): export_viewer([run, run], tmp_path / "duplicate.json")
    monkeypatch.setattr("stalecheck.export.MAX_VIEWER_BYTES", 100)
    small = tmp_path / "small.json"
    with pytest.raises(ValidationError): export_viewer([run], small)
    assert not small.exists()
    with pytest.raises(ValidationError): write_exclusive_json({"large": "x" * 100}, small, maximum=10)
    assert not small.exists()
    with pytest.raises(ValidationError): write_exclusive_json({}, tmp_path / "CON.json")


def test_optimized_source_isolated_verification(tmp_path):
    run = tmp_path / "run"
    write_run(request_with(), run)
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    # Source acceptance, not installed-wheel acceptance: explicitly name only
    # this product's source after -I removes ambient cwd/PYTHONPATH/user-site.
    source = str(Path(__file__).parents[1] / "src")
    bootstrap = "import sys,runpy; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('stalecheck',run_name='__main__')"
    completed = subprocess.run([sys.executable, "-I", "-O", "-c", bootstrap, source, "verify-artifacts", str(run)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["valid"] is True


def test_product_local_v1_retained_bytes_and_config():
    directory = Path(__file__).parents[1] / "fixtures/v1-retained"
    raw = (directory / "manifest.json").read_bytes()
    assert sha(raw) == "36f4ac1d2b4cccba592ae198efbdb6836a0c791bfde2d9453b1fc62ec1dd7680"
    assert verify_artifacts(directory)["valid"] is True
    assert (directory / "manifest.json").read_bytes() == raw


@pytest.mark.parametrize("where", ["evidence", "report", "comparison"])
def test_numeric_boolean_substitution_is_not_equivalent(tmp_path, where):
    value = request_with()
    if where == "evidence":
        value["reviewed_evidence"]["evidence"][0]["statements"][0]["value"] = 1
        with pytest.raises(ValidationError): make_report(value, "test")
        return
    run = tmp_path / "run"
    write_run(value, run)
    if where == "report":
        report = json.loads((run / "report.json").read_bytes())
        report["claims"][0]["public"] = 1
        rewrite(run, "report.json", report)
        with pytest.raises(ValidationError): verify_artifacts(run)
    else:
        comparison = make_comparison(run, run)
        comparison["time_only"] = 0
        with pytest.raises(ValidationError): verify_comparison(comparison, run, run)


def test_v2_claim_and_input_unknown_versions_fields_rejected():
    for field, changed in (("schema_version", "2.1"), ("format", "answer"), ("extra", True)):
        value = request_with()
        value[field] = changed
        with pytest.raises(ValidationError): make_report(value, "test")
    value = request_with()
    value["claims"][0]["as_of"] = None
    with pytest.raises(ValidationError): make_report(value, "test")


def test_actual_cli_conversion_comparison_export(tmp_path, capsys):
    value = request_with()
    conversion = {key: value["reviewed_evidence"][key] for key in ("schema_version", "reviews")}
    conversion["format"] = "review_conversion"
    source, converted = tmp_path / "conversion.json", tmp_path / "converted.json"
    source.write_bytes(json_bytes(conversion))
    assert main(["review-to-evidence", "--input", str(source), "--output", str(converted)]) == 0
    capsys.readouterr()
    value["reviewed_evidence"] = json.loads(converted.read_bytes())
    runs = [tmp_path / "before", tmp_path / "after"]
    for date, run in zip(("2026-05-01", "2026-11-01"), runs):
        value["verification_date"] = date
        input_path = tmp_path / (run.name + ".json")
        input_path.write_bytes(json_bytes(value))
        assert main(["verify", "--input", str(input_path), "--output", str(run)]) == 0
        capsys.readouterr()
    comparison = tmp_path / "comparison.json"
    args = ["--before", str(runs[0]), "--after", str(runs[1])]
    assert main(["compare", *args, "--output", str(comparison)]) == 0
    assert main(["verify-comparison", *args, "--input", str(comparison)]) == 0
    assert main(["export-viewer", *map(str, runs), "--output", str(tmp_path / "viewer.json")]) == 0
    assert json.loads(comparison.read_bytes())["time_only"] is True
