"""Generate actual offline CLI artifacts from explicitly invented attestations.

Run with Python -I, passing an absent output directory. No installed package,
network, credentials, sibling source or dependencies are required. This is a
source acceptance driver, not an installed-wheel acceptance claim.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))
from stalecheck.contracts import json_bytes, safe_path
from stalecheck.review import acquisition_digest, candidate_digest

BOOTSTRAP = "import sys,runpy; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('stalecheck',run_name='__main__')"


def generate(output):
    output = Path(output).absolute()
    safe_path(output.parent, output.name)
    output.mkdir(parents=True, exist_ok=False)
    transcript = []
    def save(name, value):
        path = output / name
        with path.open("xb") as stream:
            stream.write(json_bytes(value))
        return str(path)
    def cli(*args):
        command = [sys.executable, "-I", "-O", "-c", BOOTSTRAP, str(SOURCE), *map(str, args)]
        completed = subprocess.run(command, cwd=SOURCE.parent, capture_output=True, text=True, encoding="utf-8")
        transcript.append({"arguments": list(map(str, args)), "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr})
        if completed.returncode:
            raise RuntimeError(completed.stderr)
        return json.loads(completed.stdout)
    url = "https://example.com/synthetic-lifecycle"
    content = "Invented lifecycle: Synthetic service is supported from 2025-01-01 until 2026-06-01. It is unsupported from 2026-06-01."
    plan = save("plan.json", {"schema_version": "1.0", "format": "reviewed_public_acquisition", "public_reviewed": True, "queries": [], "urls": [url], "primary_domains": []})
    responses = save("responses.json", [{"path": "/extract", "status": 200, "body": {"results": [{"url": url, "title": "Invented offline lifecycle fixture", "raw_content": content}], "failed_results": []}}])
    acquisition = cli("acquire-mock", "--input", plan, "--responses", responses)
    acquisition_path = save("acquisition.json", acquisition)
    candidate = acquisition["candidates"][0]
    base = {"acquisition_sha256": acquisition_digest(acquisition), "candidate_id": candidate["id"], "candidate_sha256": candidate_digest(candidate), "content_sha256": candidate["content_sha256"], "quote": content, "quote_start_byte": 0, "quote_end_byte": len(content.encode("utf-8")), "subject": "Synthetic service", "assertion": "is_supported", "scope": "synthetic", "value": True, "effective_from": "2025-01-01", "end_state": "dated", "effective_to": "2026-06-01", "reviewer": "Invented synthetic fixture reviewer (NOT actual human review)", "attested": True, "reviewed_at": "2026-11-01T12:00:00Z", "rationale": "Explicitly invented attestation and lifecycle dates for offline plumbing demonstration, not publisher truth or authenticated human review."}
    proposals = [base, {key: value for key, value in {**base, "value": False, "effective_from": "2026-06-01", "end_state": "open_ended"}.items() if key != "effective_to"}]
    records = []
    for number, proposal in enumerate(proposals, 1):
        proposal_path = save(f"proposal-{number}.json", proposal)
        review_path = output / f"review-{number}.json"
        cli("review-evidence", "--acquisition", acquisition_path, "--proposal", proposal_path, "--output", review_path)
        cli("verify-review", "--input", review_path, "--acquisition", acquisition_path)
        records.append({"review": json.loads(review_path.read_bytes()), "previous_review": None})
    conversion_path = save("conversion.json", {"schema_version": "2.0", "format": "review_conversion", "reviews": records})
    evidence_path = output / "reviewed-evidence.json"
    cli("review-to-evidence", "--input", conversion_path, "--output", evidence_path)
    request = {"schema_version": "2.0", "format": "reviewed_lifecycle_claims", "answer_text": "Synthetic service is supported in synthetic.", "answer_date": "2026-01-01", "verification_date": "2026-05-01", "scope": "synthetic", "claims": [{"id": "synthetic-claim", "text": "Synthetic service is supported in synthetic.", "subject": "Synthetic service", "assertion": "is_supported", "scope": "synthetic", "public": True}], "reviewed_evidence": json.loads(evidence_path.read_bytes())}
    for name, date in (("before", "2026-05-01"), ("after", "2026-11-01")):
        request["verification_date"] = date
        input_path = save(f"input-{name}.json", request)
        cli("verify", "--input", input_path, "--output", output / name)
        cli("verify-artifacts", output / name)
    comparison = output / "comparison.json"
    cli("compare", "--before", output / "before", "--after", output / "after", "--output", comparison)
    cli("verify-comparison", "--before", output / "before", "--after", output / "after", "--input", comparison)
    cli("export-viewer", output / "before", output / "after", "--output", output / "viewer-bundle.json")
    def portable(text):
        # Keep machine-specific locations out of the retained record (stdout is itself JSON, so paths are escaped twice).
        for real, label in ((str(output), "<output>"), (str(SOURCE), "<source>"), (sys.executable, "<python>")):
            doubled = real.replace("\\", "\\\\")
            text = text.replace(doubled, label).replace(real, label)
        return text
    transcript = [{**entry, "arguments": [portable(item) for item in entry["arguments"]],
                   "stdout": portable(entry["stdout"]), "stderr": portable(entry["stderr"])} for entry in transcript]
    save("cli-transcript.json", {"interpreter": "<python>", "python": sys.version, "source": "<source>", "isolated": True, "optimized": True, "commands": transcript, "limitations": ["Source-only CLI acceptance, not installed-wheel verification.", "Every attestation is invented synthetic fixture data, not actual human review."]})
    print(json.dumps({"output": str(output), "commands": len(transcript), "viewer_fixture": str(output / "viewer-bundle.json")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    generate(parser.parse_args().output)
