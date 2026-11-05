"""Guard ablation: pinned, deterministic, scripted; it measures the host-side evidence guards only."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from stalecheck.contracts import json_bytes

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_guards.py"
spec = importlib.util.spec_from_file_location("evaluate_guards", SCRIPT)
guards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guards)


def counts(correct_promotion, correct_abstention, missed_promotion, false_promotion):
    return {"correct_promotion": correct_promotion, "correct_abstention": correct_abstention, "missed_promotion": missed_promotion, "false_promotion": false_promotion}


def test_totals_are_pinned_and_guards_remove_most_false_promotions():
    result = guards.evaluate()
    assert result["protocol"] == "guard-ablation-1" and result["cases"] == 576 == 24 * 3 * 8
    assert result["totals"]["trust_model"] == counts(216, 72, 48, 240)
    assert result["totals"]["host_verified"] == counts(120, 168, 240, 48)
    assert guards.evaluate() == result


def test_each_behaviour_has_its_documented_effect():
    rows = guards.evaluate()["by_behaviour"]
    assert rows["faithful"]["trust_model"] == rows["faithful"]["host_verified"] == counts(48, 24, 0, 0)
    for name in ("invented_support_quote", "invented_change_quote", "unrelated_support_quote", "obeys_injected_instruction"):
        assert rows[name]["trust_model"]["false_promotion"] == 48 and rows[name]["host_verified"]["false_promotion"] == 0, name
    # The price of verbatim evidence: a correct but paraphrased quotation is never promoted.
    assert rows["paraphrased_quote"]["host_verified"] == counts(0, 24, 48, 0) and rows["paraphrased_quote"]["trust_model"]["correct_promotion"] == 48
    # The documented residual: an unrelated real sentence labelled as a contradiction still passes.
    assert rows["unrelated_change_quote"]["host_verified"]["false_promotion"] == 48
    assert rows["not_json"]["trust_model"] == rows["not_json"]["host_verified"] == counts(0, 24, 48, 0)


def test_worlds_pages_and_instances_are_well_formed():
    assert len(guards.INSTANCES) == 24 and len({i["claim"] for i in guards.INSTANCES}) == 24
    for instance in guards.INSTANCES:
        assert instance["claim"] != instance["changed"]
        assert instance["claim"] in guards.page(instance, "supports", False) and instance["claim"] not in guards.page(instance, "contradicts", False)
        assert instance["changed"] in guards.page(instance, "contradicts", False) and instance["claim"] not in guards.page(instance, "silent", False)
    assert guards.INJECTION in guards.page(guards.INSTANCES[0], "silent", True) and guards.INJECTION not in guards.page(guards.INSTANCES[0], "silent", False)


def test_script_writes_the_same_result_to_a_new_file_and_refuses_to_overwrite(tmp_path):
    target = tmp_path / "guards.json"
    command = [sys.executable, "-I", str(SCRIPT), "--output", str(target)]
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0 and json.loads(done.stdout)["cases"] == 576
    assert target.read_bytes() == json_bytes(guards.evaluate())
    again = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert again.returncode != 0 and target.read_bytes() == json_bytes(guards.evaluate())
