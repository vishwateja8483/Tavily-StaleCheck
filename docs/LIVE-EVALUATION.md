# Live evaluation: 26 answers, one small model, real Bifrost and Tavily

Run on 2026-11-03 with `scripts/live-check/evaluate.py`. It is a method and a first signal, not an accuracy figure: 26 short answers, labels written by an AI agent, one 3B model. Read the limits at the end before quoting any number.

## What was run

Each answer went through the real pipeline, once:

| Part | What was used |
|---|---|
| Pipeline | `python -m stalecheck stale-check --live` (the evaluation ran under the package's former name FreshnessDiff; the code is the same), `--max-claims 1 --verification-date 2026-11-03 --gateway-timeout 150`; the harness typed `APPROVE` only after the CLI had printed the exact queries and domains |
| Gateway | Bifrost 2.4.0 in Docker with a virtual key (`scripts/live-check/config.json`, provider timeout raised from 55 s to 145 s) |
| Model | Qwen2.5-3B-Instruct, 4-bit GGUF, served by Ollama on four CPU cores of a laptop that other programs were also using (`ollama/qwen2.5:3b`); temperature 0 |
| Search | Tavily through the gateway's relay (`--search-relay tavily`), restricted to the reviewed domains of each item |
| Cost | 48 model calls (46,320 tokens reported by Bifrost, complete for every call), 22 Tavily searches, 22 credits, 21 minutes in total (median 48 s per answer, slowest 149 s of the 180 s journey limit) |
| Artifacts | 26 run directories, each verifiable offline; `files_containing_a_key` was 0. The per-item results are in [live-evaluation-2026-11-03.json](live-evaluation-2026-11-03.json) |

## Dataset

[`scripts/live-check/dataset.json`](../scripts/live-check/dataset.json): 26 single-claim answers, 12 labelled STALE and 14 CURRENT, about facts that change: Python (6), Node.js (4), Kubernetes (4), Go (4) and Ubuntu (4) versions and support windows, and Tavily's own credits, depths and price (4). Each item names the authoritative page its label comes from (`devguide.python.org`, `nodejs.org`, `kubernetes.io`, `go.dev`, `ubuntu.com`, `docs.tavily.com`) and the line of that page that decides it. STALE means that page now contradicts the claim; CURRENT means it confirms it. The answers are one-sentence synthetic statements, not outputs of a real assistant.

The labels were written by an AI coding agent from those pages on 2026-11-03 and have **not been reviewed by a human**. The set is small and skews to software versions and support windows, where a page states the answer plainly.

## Scoring

The host-verified verdict is the one in the report. The model-alone verdict is the model's own proposal for the same claim (`CHANGED` read as `POTENTIALLY_CHANGED`, `SUPPORTED` as `MODEL_SUPPORTED`), before the host checks its quotations. For a STALE answer `POTENTIALLY_CHANGED` is correct, `MODEL_SUPPORTED` is a missed stale claim; for a CURRENT answer `MODEL_SUPPORTED` is correct, `POTENTIALLY_CHANGED` is a false alarm; `UNVERIFIABLE` is an abstention. An answer without a usable claim or whose journey failed has no verdict. Intervals are 95% Wilson intervals.

## Results

**Coverage.** 22 of 26 answers gave a usable claim (95% interval 0.67-0.94) and all 22 reached a verdict. The other four were rejected as `invalid_reply`: the model ended its reply with `}}` or `}}}` instead of `}]}`, so it was not valid JSON. No journey was stopped by a timeout. The host dropped 21 quotations from the 22 assessments (19 were not verbatim in the retained excerpt, 2 were too short).

| Outcome | Host-verified | Model alone |
|---|---|---|
| correct | 6 | 9 |
| abstained (`UNVERIFIABLE`) | 12 | 0 |
| missed stale claim | 0 | 0 |
| false alarm | 4 | 11 |
| no verdict | 4 | 6 |

| Label | Answers | correct | abstained | false alarm | no verdict |
|---|---|---|---|---|---|
| STALE | 12 | 6 | 4 | 0 | 2 |
| CURRENT | 14 | 0 | 8 | 4 | 2 |

Among the 22 answers that reached a verdict, 6 were right with the guards (0.13-0.48) and 9 for the model alone (0.23-0.61). What the guards changed, per answer: 7 wrong alarms became abstentions, 6 right verdicts stayed, 3 right verdicts became abstentions (the model said `CHANGED` for a stale claim but none of its quotations was verbatim), 4 wrong alarms stayed. The pipeline flagged 10 answers as potentially changed; 6 of them were stale by label, and it found 6 of the 12 stale ones.

| Answer | Label | Host verdict | Reason | Model proposed | Outcome (host / model alone) |
|---|---|---|---|---|---|
| py-39-security | STALE | no claim | invalid_reply | - | no verdict / no verdict |
| py-313-newest | STALE | POTENTIALLY_CHANGED | MODEL_CONTRADICTION_WITH_VERBATIM_QUOTE | CHANGED | correct / correct |
| py-38-eol | CURRENT | UNVERIFIABLE | NO_VERBATIM_CONTRADICTING_QUOTE | CHANGED | abstained / false alarm |
| py-312-security | CURRENT | UNVERIFIABLE | NO_VERBATIM_CONTRADICTING_QUOTE | CHANGED | abstained / false alarm |
| py-310-supported | STALE | no claim | invalid_reply | - | no verdict / no verdict |
| py-311-2027 | CURRENT | UNVERIFIABLE | ASSESSMENT_INVALID_REPLY | - | abstained / no verdict |
| node-18-lts | STALE | POTENTIALLY_CHANGED | MODEL_CONTRADICTION_WITH_VERBATIM_QUOTE | CHANGED | correct / correct |
| node-20-latest-lts | STALE | POTENTIALLY_CHANGED | MODEL_CONTRADICTION_WITH_VERBATIM_QUOTE | CHANGED | correct / correct |
| node-22-lts | CURRENT | POTENTIALLY_CHANGED | MODEL_CONTRADICTION_WITH_VERBATIM_QUOTE | CHANGED | false alarm / false alarm |
| node-20-eol | CURRENT | POTENTIALLY_CHANGED | MODEL_CONTRADICTION_WITH_VERBATIM_QUOTE | CHANGED | false alarm / false alarm |
| k8s-134-supported | STALE | UNVERIFIABLE | NO_VERBATIM_CONTRADICTING_QUOTE | CHANGED | abstained / correct |
| k8s-136-supported | CURRENT | POTENTIALLY_CHANGED | MODEL_CONTRADICTION_WITH_VERBATIM_QUOTE | CHANGED | false alarm / false alarm |
| k8s-136-latest | STALE | UNVERIFIABLE | NO_VERBATIM_CONTRADICTING_QUOTE | CHANGED | abstained / correct |
| k8s-three-branches | CURRENT | UNVERIFIABLE | CONFLICTING_EVIDENCE | CHANGED | abstained / false alarm |
| go-125-supported | STALE | POTENTIALLY_CHANGED | MODEL_CONTRADICTION_WITH_VERBATIM_QUOTE | CHANGED | correct / correct |
| go-126-supported | CURRENT | UNVERIFIABLE | NO_VERBATIM_CONTRADICTING_QUOTE | CHANGED | abstained / false alarm |
| go-126-latest | STALE | POTENTIALLY_CHANGED | MODEL_CONTRADICTION_WITH_VERBATIM_QUOTE | CHANGED | correct / correct |
| go-policy | CURRENT | POTENTIALLY_CHANGED | MODEL_CONTRADICTION_WITH_VERBATIM_QUOTE | CHANGED | false alarm / false alarm |
| ubuntu-2404-latest-lts | STALE | POTENTIALLY_CHANGED | MODEL_CONTRADICTION_WITH_VERBATIM_QUOTE | CHANGED | correct / correct |
| ubuntu-2204-security | CURRENT | no claim | invalid_reply | - | no verdict / no verdict |
| ubuntu-2004-standard | STALE | UNVERIFIABLE | NO_VERBATIM_CONTRADICTING_QUOTE | CHANGED | abstained / correct |
| ubuntu-2404-standard | CURRENT | no claim | invalid_reply | - | no verdict / no verdict |
| tavily-free-credits | CURRENT | UNVERIFIABLE | CONFLICTING_EVIDENCE | CHANGED | abstained / false alarm |
| tavily-search-credits | CURRENT | UNVERIFIABLE | NO_VERBATIM_CONTRADICTING_QUOTE | CHANGED | abstained / false alarm |
| tavily-depths | STALE | UNVERIFIABLE | ASSESSMENT_INVALID_REPLY | - | abstained / no verdict |
| tavily-payg | CURRENT | UNVERIFIABLE | NO_VERBATIM_CONTRADICTING_QUOTE | CHANGED | abstained / false alarm |

## What this shows

- **The 3B model has no discrimination.** It proposed `CHANGED` for every claim it judged (20 of 20 valid assessment replies) and never `SUPPORTED`. Its 9 correct verdicts are all STALE answers: they reflect that 12 of the 26 labels are STALE, not that it can tell stale from current. It called 11 of the 14 CURRENT claims changed. A model with this bias cannot confirm a claim, and no CURRENT answer in this run was marked supported.
- **The guards removed most of the damage, not all.** Requiring a verbatim quotation turned 7 of the model's 11 false alarms into abstentions and let no wrong "supported" through (there were none to let through). The price was 3 right verdicts lost to abstention. The 4 false alarms that remained are real quotations that do not contradict the claim: for "Node.js 22 is still a supported long-term support release" the model labelled "Node.js 22 is in active LTS status and will be actively supported until April 2027" as contradicting. The host checks that a quotation exists in a retained excerpt, not that it contradicts; the reports and the documentation already say so, and this run puts a number on it for a weak model. Not every correct verdict is right for the right reason either: for `node-18-lts` the retained quotation is an old announcement that Node.js 18 would become LTS, which does not contradict the claim, while for `py-313-newest` one of three quotations ("Python 3.15.0 is the newest major release of the Python programming language") does.
- **Unverifiable is the honest answer far more often than for a strong model.** 12 of 26 answers ended as `UNVERIFIABLE`, because the quotations were missing or dropped, the evidence conflicted, or the model's reply broke the contract. That is the intended fail-closed behaviour, but with this model it means the report mostly asks for human review.
- **Search and the freshness score behaved as documented.** 13 of the 22 judged answers have a freshness score, from 0 (the newest quoted page is a year old or older) to 99; 7 kept no quotation and 2 quoted only undated pages, so they have none. The score describes the age of the quoted page, not whether the claim is current.

## Three fixes this evaluation caused

The first attempts showed that a strict reply contract made a small model look worse than it is. All three fixes keep the checks that decide a verdict (verbatim quotations, exact approval, bounded budgets) and change only how a model's reply is read or how long it may take; each has tests.

| Observation | Change | Effect on the same answers |
|---|---|---|
| The model copied the category list from the prompt (`pricing\|api\|version\|...`) as the category of a claim, so the whole proposal was rejected as malformed. | A category outside the documented list is shown as `other` and the claim is kept (`parse_extraction`). | Extraction-only probe over the 26 answers: usable claims 13, then 22 |
| The model cited a source by the hashed delimiter that the prompt wraps around its excerpt (`SRC-8a7b...` or the bare hash) instead of its id (`claim-1-src-1`), so the whole assessment was rejected: 7 of the 11 assessment replies of a partial run, a further one for an over-long observation. | The delimiter is an alias of exactly one retained source and resolves to it (`parse_assessment`); any other unknown id is still invalid. | The same 11 recorded replies re-parsed: 3 valid, then 10 |
| On the shared four cores one assessment call took from about 15 s to over 130 s, and 8 of the 10 journeys that reached it in the first attempt stopped at the 55 s gateway or 60 s client limit as `gateway_failed`. | `--gateway-timeout SECONDS` (default 60, up to 180) for the client, a matching provider timeout in the gateway (`CALL_TIMEOUT` in `run.sh`). | 0 of 26 journeys stopped by a timeout with 150 s |

These before-and-after counts come from partial runs and probes on the same machine and answers, not from a controlled experiment. Two attempts were stopped by hand once their cause was clear; together they used 22 more Tavily credits than the final run.

## Limits

- **26 answers.** Every interval is wide (for example 6 correct of 22 judged: 0.13-0.48). Differences of a few answers mean nothing.
- **Labels are AI-written and unreviewed**, by the same family of tools that wrote the pipeline, from the same pages the search is restricted to. A human review may change some labels.
- **Short synthetic answers with one claim each.** This does not measure extraction from a long answer, claim selection (`--max-claims 1`) or prompt-injection resistance.
- **One model, one run per answer, temperature 0.** No variance estimate. Results from a hosted model are expected to differ a lot; none was available, so none was measured. The harness takes any Bifrost model name.
- **Web results move.** Tavily's results and the pages they come from change, so a rerun later may differ, and labels are only valid as of 2026-11-03.
- **A verbatim quotation is not entailment.** Scores here are about verdicts, not about whether the quoted evidence is relevant.

## Reproduce

```bash
# once: Ollama with the model, as in scripts/live-check/README.md. A slow CPU needs a longer call timeout:
TAVILY_API_KEY=... CALL_TIMEOUT=150 KEEP_GATEWAY=1 MODES= scripts/live-check/run.sh   # starts only the gateway and prints where the virtual key is
export BIFROST_VIRTUAL_KEY="$(cat runs/live-check-*/virtual-key.txt)" PYTHONPATH=src
python3 scripts/live-check/evaluate.py --model ollama/qwen2.5:3b --search-relay tavily --gateway-timeout 150 --output runs/live-evaluation-01
```

About 22 Tavily credits and 20 minutes on a CPU-only laptop. The output directory holds one verifiable run per answer, `results.json` and `results.md`. With a hosted model, point a gateway at that provider ([BIFROST-GATEWAY.md](BIFROST-GATEWAY.md)), keep the default `--gateway-timeout` and pass `--model provider/model`; `--resume` re-derives the results of a finished directory from its retained reports without calling anything. The scoring and the dataset checks are unit-tested in `tests/test_live_evaluation.py`.
