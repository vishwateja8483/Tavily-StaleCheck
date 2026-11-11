"""Keep judge-facing receipts paired with the verified live run they describe."""
import json
from pathlib import Path

import pytest

from stalecheck.stale_check_v4 import render_html
from stalecheck.verify_artifacts import load_verified_run


EVIDENCE = Path(__file__).resolve().parents[1] / "docs" / "evidence"
LIVE_CASES = ("python-release", "nodejs-support", "github-quota", "github-artifacts", "ioc-leadership")


@pytest.mark.parametrize("case", LIVE_CASES)
def test_recorded_browser_case_replays_offline(case):
    directory = EVIDENCE / case
    snapshot = load_verified_run(directory / "run")
    report = snapshot["report"]
    receipt = json.loads((directory / "verification.json").read_text(encoding="utf-8"))
    browser = json.loads((directory / "browser-summary.json").read_text(encoding="utf-8"))

    assert receipt["artifact_verification"] == snapshot["verification"]
    assert receipt["approval"] == report["approval"]
    assert receipt["usage"] == report["usage"]
    assert receipt["attempts"] == snapshot["input"]["attempts"]
    assert receipt["verdicts"] == report["counts"]
    assert [json.loads(query) for query in browser["queries"]] == receipt["approval"]["queries"]
    assert browser["state"] == receipt["assessment"] == "completed"


def test_conflicting_quota_report_groups_quotes_by_model_label():
    snapshot = load_verified_run(EVIDENCE / "github-quota" / "run")
    assert snapshot["report"]["claims"][0]["reason_code"] == "CONFLICTING_EVIDENCE"
    html = render_html(snapshot["report"])
    comparison = html.split('<div class="evidence-comparison">', 1)[1].split("<details>", 1)[0]
    support, contradiction = comparison.split("<h4>Labelled as contradicting</h4>")
    assert "<h4>Labelled as supporting</h4>" in support
    assert support.count("Model-labelled relation: supports") == 1
    assert contradiction.count("Model-labelled relation: contradicts") == 2
