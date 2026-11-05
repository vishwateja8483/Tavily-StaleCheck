#!/usr/bin/env python3
"""Score the live stale-answer pipeline on the labelled claims in dataset.json.

One real journey per item (real Bifrost gateway, real model, real Tavily). Each item is a one-sentence answer holding
one claim, so the pipeline runs with --max-claims 1. The host-verified verdict and the model's own proposed verdict are
compared with the label. POSIX only (the CLI's approval prompt needs a pseudo-terminal). Same environment as run.sh:
a running gateway, BIFROST_VIRTUAL_KEY, TAVILY_API_KEY (unless --search-relay) and PYTHONPATH=src.

  python3 scripts/live-check/evaluate.py --model ollama/qwen2.5:3b --output runs/live-evaluation-01
"""
import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = ("STALE", "CURRENT")
CATEGORIES = ("pricing", "api", "version", "law", "leadership", "availability", "other")
OUTCOMES = ("correct", "abstained", "missed_stale", "false_alarm", "no_verdict")
RAW_TO_HOST = {"CHANGED": "POTENTIALLY_CHANGED", "SUPPORTED": "MODEL_SUPPORTED", "UNVERIFIABLE": "UNVERIFIABLE"}
# Reason codes that mean the pipeline did not get to a judgement (infrastructure or consent), not that it abstained.
FAILURE_CODES = {"GATEWAY_FAILED", "SEARCH_FAILED", "JOURNEY_LIMIT", "PREFLIGHT_FAILED", "APPROVAL_DECLINED", "INTERRUPTED_OR_FAILED"}


def load_dataset(path=HERE / "dataset.json"):
    data = json.loads(Path(path).read_text("utf-8"))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data.get("as_of"))):
        raise ValueError("dataset needs an as_of date")
    seen = set()
    for item in data["items"]:
        if set(item) != {"id", "category", "label", "domains", "answer", "evidence_url", "evidence"}:
            raise ValueError(f"unexpected fields in {item.get('id')}")
        if not re.fullmatch(r"[a-z0-9-]+", item["id"]) or item["id"] in seen:
            raise ValueError(f"bad or repeated id {item['id']}")
        seen.add(item["id"])
        if item["label"] not in LABELS or item["category"] not in CATEGORIES:
            raise ValueError(f"bad label or category in {item['id']}")
        if not (isinstance(item["answer"], str) and 0 < len(item["answer"]) <= 400 and item["answer"] == item["answer"].strip()):
            raise ValueError(f"bad answer in {item['id']}")
        if not item["domains"] or not all(re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", d) for d in item["domains"]):
            raise ValueError(f"bad domains in {item['id']}")
        host = re.fullmatch(r"https://([a-z0-9.-]+)/\S*", item["evidence_url"])
        if not host or not any(host.group(1) == d or host.group(1).endswith("." + d) for d in item["domains"]):
            raise ValueError(f"evidence_url of {item['id']} is not on its reviewed domains")
        if not item["evidence"].strip():
            raise ValueError(f"missing evidence in {item['id']}")
    return data


def outcome(label, verdict):
    """Judge a host-style verdict against the label; None means no judgement was reached."""
    if verdict is None:
        return "no_verdict"
    if verdict == "UNVERIFIABLE":
        return "abstained"
    if label == "STALE":
        return "correct" if verdict == "POTENTIALLY_CHANGED" else "missed_stale"
    return "correct" if verdict == "MODEL_SUPPORTED" else "false_alarm"


def raw_outcome(label, proposed):
    return outcome(label, RAW_TO_HOST.get(proposed))


def wilson(successes, total, z=1.96):
    if total == 0:
        return None
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(max(0.0, centre - margin), 3), round(min(1.0, centre + margin), 3)]


def coverage(results):
    """How far each answer got: a usable claim, then a verdict, and why the others stopped."""
    statuses, reasons, dropped = {}, {}, {}
    for r in results:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
        for reason in r.get("rejected", []):
            reasons[reason] = reasons.get(reason, 0) + 1
        for reason in r.get("dropped_quotes", []):
            dropped[reason] = dropped.get(reason, 0) + 1
    extracted = statuses.get("ok", 0) + statuses.get("stopped", 0)
    return {"answers": len(results), "claim_extracted": extracted, "claim_extracted_wilson95": wilson(extracted, len(results)),
            "judged": statuses.get("ok", 0), "statuses": dict(sorted(statuses.items())), "rejection_reasons": dict(sorted(reasons.items())),
            "dropped_quote_reasons": dict(sorted(dropped.items()))}


def among_judged(results):
    """Accuracy over the answers that reached a verdict, so extraction failures do not count against the verdict stage."""
    judged = [r for r in results if r["verdict"] is not None]
    right = {"host_verified": sum(outcome(r["label"], r["verdict"]) == "correct" for r in judged),
             "model_only": sum(raw_outcome(r["label"], r["proposed"]) == "correct" for r in judged)}
    return {"items": len(judged), **{key: {"correct": value, "of": len(judged), "wilson95": wilson(value, len(judged))} for key, value in right.items()}}


def score(results):
    """Counts of outcomes for the guarded pipeline and for the model's raw verdicts, with the transitions between them."""
    total = len(results)
    host, raw = dict.fromkeys(OUTCOMES, 0), dict.fromkeys(OUTCOMES, 0)
    by_label = {label: {"items": 0, **dict.fromkeys(OUTCOMES, 0)} for label in LABELS}
    transitions = {}
    for r in results:
        h, m = outcome(r["label"], r["verdict"]), raw_outcome(r["label"], r["proposed"])
        host[h] += 1
        raw[m] += 1
        by_label[r["label"]]["items"] += 1
        by_label[r["label"]][h] += 1
        key = f"{m} -> {h}"
        transitions[key] = transitions.get(key, 0) + 1
    return {
        "items": total,
        "host_verified": host, "model_only": raw, "by_label": by_label, "transitions": dict(sorted(transitions.items())),
        "accuracy": {"host_verified": {"correct": host["correct"], "of": total, "wilson95": wilson(host["correct"], total)},
                     "model_only": {"correct": raw["correct"], "of": total, "wilson95": wilson(raw["correct"], total)}},
        "wrong_verdicts": {"host_verified": host["missed_stale"] + host["false_alarm"], "model_only": raw["missed_stale"] + raw["false_alarm"]},
        "among_judged": among_judged(results), "coverage": coverage(results),
    }


def read_result(item, item_dir, exit_code, seconds):
    result = {"id": item["id"], "label": item["label"], "category": item["category"], "exit_code": exit_code, "seconds": round(seconds, 1),
              "status": "no_run", "verdict": None, "proposed": None}
    path = item_dir / "session" / "run" / "report.json"
    if not path.is_file():
        return result
    report = json.loads(path.read_text("utf-8"))
    usage = report["usage"]
    rejected = report["rejected_proposals"]
    result.update(stop_reason=report["stop_reason"], rejected=[r["reason"] for r in rejected if r["stage"] == "extraction"],
                  dropped_quotes=[r["reason"] for r in rejected if r["stage"] == "assessment"],
                  usage={"gateway_calls": usage["gateway_calls"], "search_calls": usage["search_calls"],
                         "tokens": usage["tokens"]["known_subtotal"]["total_tokens"], "tokens_complete": usage["tokens"]["complete"],
                         "credits": usage["credits"]["known_subtotal"]["credits"]})
    if not report["claims"]:
        result["status"] = "no_claim"
        return result
    claim = report["claims"][0]
    failed = claim["reason_code"] in FAILURE_CODES
    result.update(status="stopped" if failed else "ok", claim=claim["text"], query=claim["query"], reason_code=claim["reason_code"],
                  verdict=None if failed else claim["verdict"], proposed=claim["proposed_verdict"], quotes=len(claim["evidence"]),
                  freshness=claim["freshness"]["score"])
    return result


def markdown(dataset, results, summary, meta):
    cov = summary["coverage"]
    lines = [f"# Live evaluation, {meta['date']}", "",
             f"{summary['items']} one-claim answers, labelled as of {dataset['as_of']}. Model `{meta['model']}` through Bifrost, searches {meta['search']}.", "",
             f"Coverage: {cov['claim_extracted']} of {cov['answers']} answers gave a usable claim and {cov['judged']} reached a verdict "
             f"(statuses {cov['statuses']}; extraction proposals rejected: {cov['rejection_reasons'] or 'none'}; "
             f"quotations dropped because they are not verbatim or too short: {cov['dropped_quote_reasons'] or 'none'}).", "",
             "| id | label | host verdict | why | model proposed | outcome (host / model only) |", "|---|---|---|---|---|---|"]
    for r in results:
        why = r.get("reason_code") or ",".join(r.get("rejected", [])) or "-"
        lines.append(f"| {r['id']} | {r['label']} | {r['verdict'] or r.get('status')} | {why} | {r['proposed'] or '-'} | "
                     f"{outcome(r['label'], r['verdict'])} / {raw_outcome(r['label'], r['proposed'])} |")
    h, m, a = summary["host_verified"], summary["model_only"], summary["accuracy"]
    lines += ["", "| | host-verified | model alone |", "|---|---|---|"]
    for key in OUTCOMES:
        lines.append(f"| {key} | {h[key]} | {m[key]} |")
    j = summary["among_judged"]
    lines += ["", f"Correct: {a['host_verified']['correct']}/{summary['items']} host-verified (95% Wilson interval {a['host_verified']['wilson95']}), "
                  f"{a['model_only']['correct']}/{summary['items']} for the model alone ({a['model_only']['wilson95']}) over all answers.",
              f"Among the {j['items']} that reached a verdict: {j['host_verified']['correct']} host-verified correct ({j['host_verified']['wilson95']}), "
              f"{j['model_only']['correct']} for the model alone ({j['model_only']['wilson95']})."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default=str(HERE / "dataset.json"))
    parser.add_argument("--output", required=True, help="new directory (an existing one with --resume)")
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--model", required=True, help="Bifrost model as provider/model")
    parser.add_argument("--search-relay", metavar="NAME")
    parser.add_argument("--vk-env", default="BIFROST_VIRTUAL_KEY")
    parser.add_argument("--tavily-env", default="TAVILY_API_KEY")
    parser.add_argument("--max-seconds", type=float, default=180)
    parser.add_argument("--gateway-timeout", type=float, help="seconds allowed for each model call (the CLI default is 60); raise it for a slow local model")
    parser.add_argument("--only", help="comma-separated item ids")
    parser.add_argument("--resume", action="store_true", help="keep the finished items of an existing output directory (only their results are re-derived from the retained reports)")
    args = parser.parse_args()

    import live_journey  # POSIX only, so imported late

    dataset = load_dataset(args.dataset)
    wanted = set(args.only.split(",")) if args.only else None
    items = [item for item in dataset["items"] if wanted is None or item["id"] in wanted]
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=args.resume)
    results = []
    for number, item in enumerate(items, 1):
        item_dir = out / item["id"]
        finished = item_dir / "result.json"
        if args.resume and finished.is_file():
            old = json.loads(finished.read_text("utf-8"))  # the retained report is read again, so the current code scores every item
            result = read_result(item, item_dir, old["exit_code"], old["seconds"])
            finished.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            results.append(result)
            continue
        item_dir.mkdir(parents=True)
        answer = item_dir / "answer.txt"
        answer.write_text(item["answer"] + "\n", encoding="utf-8")
        cli = ["stale-check", "--answer", str(answer), "--live", "--confirm-outbound", "--gateway-url", args.gateway, "--model", args.model,
               "--gateway-token-env", args.vk_env, "--max-claims", "1", "--max-seconds", str(args.max_seconds),
               "--verification-date", dataset["as_of"], "--output", str(item_dir / "session")]
        cli += ["--search-relay", args.search_relay] if args.search_relay else ["--tavily-key-env", args.tavily_env]
        if args.gateway_timeout:
            cli += ["--gateway-timeout", str(args.gateway_timeout)]
        for domain in item["domains"]:
            cli += ["--primary-domain", domain]
        started = time.monotonic()
        code, text = live_journey.run_in_terminal(cli, approve=True, timeout=args.max_seconds + 120)
        result = read_result(item, item_dir, code, time.monotonic() - started)
        (item_dir / "cli-output.txt").write_text(text, encoding="utf-8")
        finished.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        results.append(result)
        print(f"[{number}/{len(items)}] {item['id']}: label {item['label']}, host {result['verdict']}, model {result['proposed']}, "
              f"{result['status']}, {result['seconds']}s", file=sys.stderr, flush=True)

    summary = score(results)
    meta = {"date": time.strftime("%Y-%m-%d"), "model": args.model, "search": f"through the Bifrost relay '{args.search_relay}'" if args.search_relay else "direct",
            "gateway": args.gateway, "gateway_timeout_seconds": args.gateway_timeout or 60,
            "files_containing_a_key": live_journey.leaked(out, [os.environ.get(args.vk_env), os.environ.get(args.tavily_env)])}
    (out / "results.json").write_text(json.dumps({"meta": meta, "dataset_as_of": dataset["as_of"], "summary": summary, "items": results}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "results.md").write_text(markdown(dataset, results, summary, meta), encoding="utf-8")
    print(markdown(dataset, results, summary, meta))
    return 0 if meta["files_containing_a_key"] == 0 else 1


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    sys.exit(main())
