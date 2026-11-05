"""The live evaluation's dataset and scoring; no provider is called."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live-check" / "evaluate.py"


@pytest.fixture(scope="module")
def evaluate():
    spec = importlib.util.spec_from_file_location("live_evaluate", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dataset_is_valid_and_has_both_labels(evaluate):
    data = evaluate.load_dataset()
    labels = [item["label"] for item in data["items"]]
    assert len(labels) >= 20 and labels.count("STALE") >= 8 and labels.count("CURRENT") >= 8
    assert all(item["answer"].endswith((".", "!")) and "\n" not in item["answer"] for item in data["items"])


@pytest.mark.parametrize("change,message", [
    ({"label": "OLD"}, "bad label"),
    ({"domains": ["Example.COM"]}, "bad domains"),
    ({"evidence_url": "https://other.example.net/page"}, "not on its reviewed domains"),
    ({"answer": " padded "}, "bad answer"),
    ({"extra": 1}, "unexpected fields"),
])
def test_dataset_rejects_malformed_items(evaluate, tmp_path, change, message):
    item = {"id": "one", "category": "version", "label": "STALE", "domains": ["example.com"], "answer": "X 1 is current.",
            "evidence_url": "https://docs.example.com/page", "evidence": "X 2 | current"}
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps({"as_of": "2026-11-03", "items": [{**item, **change}]}), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        evaluate.load_dataset(path)


def test_repeated_ids_are_rejected(evaluate, tmp_path):
    item = {"id": "one", "category": "version", "label": "STALE", "domains": ["example.com"], "answer": "X 1 is current.",
            "evidence_url": "https://example.com/page", "evidence": "X 2"}
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps({"as_of": "2026-11-03", "items": [item, item]}), encoding="utf-8")
    with pytest.raises(ValueError, match="repeated id"):
        evaluate.load_dataset(path)


@pytest.mark.parametrize("label,verdict,expected", [
    ("STALE", "POTENTIALLY_CHANGED", "correct"), ("STALE", "MODEL_SUPPORTED", "missed_stale"), ("STALE", "UNVERIFIABLE", "abstained"),
    ("CURRENT", "MODEL_SUPPORTED", "correct"), ("CURRENT", "POTENTIALLY_CHANGED", "false_alarm"), ("CURRENT", "UNVERIFIABLE", "abstained"),
    ("STALE", None, "no_verdict"), ("CURRENT", None, "no_verdict")])
def test_outcome_table(evaluate, label, verdict, expected):
    assert evaluate.outcome(label, verdict) == expected


def test_the_models_own_verdicts_map_to_the_hosts_labels(evaluate):
    assert evaluate.raw_outcome("STALE", "CHANGED") == "correct"
    assert evaluate.raw_outcome("CURRENT", "CHANGED") == "false_alarm"
    assert evaluate.raw_outcome("CURRENT", "SUPPORTED") == "correct"
    assert evaluate.raw_outcome("STALE", "SUPPORTED") == "missed_stale"
    assert evaluate.raw_outcome("STALE", None) == "no_verdict"


def test_score_counts_outcomes_and_what_the_guards_changed(evaluate):
    results = [
        {"label": "CURRENT", "verdict": "UNVERIFIABLE", "proposed": "CHANGED", "status": "ok"},       # the guard demoted a wrong alarm
        {"label": "STALE", "verdict": "POTENTIALLY_CHANGED", "proposed": "CHANGED", "status": "ok"},   # both right
        {"label": "STALE", "verdict": "MODEL_SUPPORTED", "proposed": "SUPPORTED", "status": "ok"},     # both missed it
        {"label": "CURRENT", "verdict": "MODEL_SUPPORTED", "proposed": "SUPPORTED", "status": "ok"},   # both right
        {"label": "CURRENT", "verdict": None, "proposed": None, "status": "stopped"},                  # the journey failed
    ]
    summary = evaluate.score(results)
    assert summary["items"] == 5
    assert summary["host_verified"] == {"correct": 2, "abstained": 1, "missed_stale": 1, "false_alarm": 0, "no_verdict": 1}
    assert summary["model_only"] == {"correct": 2, "abstained": 0, "missed_stale": 1, "false_alarm": 1, "no_verdict": 1}
    assert summary["transitions"]["false_alarm -> abstained"] == 1
    assert summary["wrong_verdicts"] == {"host_verified": 1, "model_only": 2}
    assert summary["by_label"]["CURRENT"]["items"] == 3 and summary["by_label"]["STALE"]["missed_stale"] == 1
    assert summary["accuracy"]["host_verified"]["correct"] == 2 and summary["accuracy"]["host_verified"]["of"] == 5


def test_coverage_separates_extraction_failures_from_verdict_quality(evaluate):
    results = [
        {"label": "STALE", "verdict": "POTENTIALLY_CHANGED", "proposed": "CHANGED", "status": "ok", "rejected": []},
        {"label": "CURRENT", "verdict": "UNVERIFIABLE", "proposed": "CHANGED", "status": "ok", "rejected": ["over_claim_limit"], "dropped_quotes": ["quote_not_in_source"] * 2},
        {"label": "CURRENT", "verdict": None, "proposed": None, "status": "stopped", "rejected": []},
        {"label": "STALE", "verdict": None, "proposed": None, "status": "no_claim", "rejected": ["invalid_reply"]},
        {"label": "STALE", "verdict": None, "proposed": None, "status": "no_run"},
    ]
    summary = evaluate.score(results)
    assert summary["coverage"] == {"answers": 5, "claim_extracted": 3, "claim_extracted_wilson95": evaluate.wilson(3, 5), "judged": 2,
                                   "statuses": {"no_claim": 1, "no_run": 1, "ok": 2, "stopped": 1},
                                   "rejection_reasons": {"invalid_reply": 1, "over_claim_limit": 1}, "dropped_quote_reasons": {"quote_not_in_source": 2}}
    assert summary["among_judged"] == {"items": 2, "host_verified": {"correct": 1, "of": 2, "wilson95": evaluate.wilson(1, 2)},
                                       "model_only": {"correct": 1, "of": 2, "wilson95": evaluate.wilson(1, 2)}}
    assert summary["accuracy"]["host_verified"]["of"] == 5


def test_wilson_interval_is_a_proper_interval(evaluate):
    assert evaluate.wilson(0, 0) is None
    low, high = evaluate.wilson(13, 26)
    assert 0.3 < low < 0.5 < high < 0.7
    assert evaluate.wilson(26, 26)[1] == 1.0 and evaluate.wilson(0, 26)[0] == 0.0


def write_report(path, claim_fields, *, stop_reason=None, rejected=(), dropped=()):
    run = path / "session" / "run"
    run.mkdir(parents=True)
    claims = [] if claim_fields is None else [{"text": "X 1 is current.", "query": "x release", "reason_code": "MODEL_CONTRADICTION_WITH_VERBATIM_QUOTE",
                                               "verdict": "POTENTIALLY_CHANGED", "proposed_verdict": "CHANGED", "evidence": [{}, {}],
                                               "freshness": {"score": 93}, **claim_fields}]
    usage = {"gateway_calls": 2, "search_calls": 1, "tokens": {"known_subtotal": {"total_tokens": 900}, "complete": True}, "credits": {"known_subtotal": {"credits": 1}}}
    (run / "report.json").write_text(json.dumps({"claims": claims, "stop_reason": stop_reason, "usage": usage,
                                                 "rejected_proposals": [{"stage": "extraction", "reference": "p", "reason": r} for r in rejected]
                                                                       + [{"stage": "assessment", "reference": "claim-1", "reason": r} for r in dropped]}), encoding="utf-8")


def test_read_result_reports_a_judged_claim(evaluate, tmp_path):
    write_report(tmp_path, {}, dropped=["quote_not_in_source"])
    result = evaluate.read_result({"id": "a", "label": "STALE", "category": "version"}, tmp_path, 0, 41.26)
    assert result["rejected"] == [] and result["dropped_quotes"] == ["quote_not_in_source"]
    assert (result["status"], result["verdict"], result["proposed"], result["quotes"], result["freshness"]) == ("ok", "POTENTIALLY_CHANGED", "CHANGED", 2, 93)
    assert result["usage"] == {"gateway_calls": 2, "search_calls": 1, "tokens": 900, "tokens_complete": True, "credits": 1} and result["seconds"] == 41.3


def test_read_result_separates_pipeline_failures_from_abstentions(evaluate, tmp_path):
    failed, abstained = tmp_path / "failed", tmp_path / "abstained"
    write_report(failed, {"reason_code": "GATEWAY_FAILED", "verdict": "UNVERIFIABLE", "proposed_verdict": None}, stop_reason="gateway_failed")
    write_report(abstained, {"reason_code": "NO_SOURCES", "verdict": "UNVERIFIABLE", "proposed_verdict": None})
    item = {"id": "a", "label": "CURRENT", "category": "version"}
    assert evaluate.read_result(item, failed, 0, 1)["verdict"] is None and evaluate.read_result(item, failed, 0, 1)["status"] == "stopped"
    assert evaluate.read_result(item, abstained, 0, 1)["verdict"] == "UNVERIFIABLE"


def test_read_result_handles_a_run_without_claims_or_report(evaluate, tmp_path):
    empty, missing = tmp_path / "empty", tmp_path / "missing"
    write_report(empty, None, rejected=["invalid_reply"])
    item = {"id": "a", "label": "STALE", "category": "version"}
    assert evaluate.read_result(item, empty, 0, 1)["status"] == "no_claim" and evaluate.read_result(item, empty, 0, 1)["rejected"] == ["invalid_reply"]
    missing.mkdir()
    assert evaluate.read_result(item, missing, 1, 1)["status"] == "no_run"


def test_markdown_lists_every_item_and_the_totals(evaluate):
    data = evaluate.load_dataset()
    results = [{"id": "a", "label": "STALE", "verdict": "POTENTIALLY_CHANGED", "proposed": "CHANGED", "status": "ok"},
               {"id": "b", "label": "CURRENT", "verdict": None, "proposed": None, "status": "no_run"}]
    text = evaluate.markdown(data, results, evaluate.score(results), {"date": "2026-11-03", "model": "ollama/x", "search": "direct"})
    assert "| a | STALE | POTENTIALLY_CHANGED | - | CHANGED | correct / correct |" in text and "| b | CURRENT | no_run | - | - | no_verdict / no_verdict |" in text
    assert "Correct: 1/2 host-verified" in text and "Coverage: 1 of 2 answers gave a usable claim and 1 reached a verdict" in text
    assert "Among the 1 that reached a verdict: 1 host-verified correct" in text
