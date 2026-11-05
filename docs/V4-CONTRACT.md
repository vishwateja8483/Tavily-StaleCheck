# Consent-gated stale-answer assessment (schema 4.0)

## Product and architecture

StaleCheck is a standalone, standard-library-only package. V1/v2 lifecycle verification and v3 stale-answer replay remain exact-version paths. V4 uses a new deterministic algorithm/config identity; it never silently upgrades retained transcripts.

```text
UTF-8 answer + dates + preferred domains
    -> validate input, origin, limits, new output destination
    -> reserve session directory (before credentials or calls)
    -> Bifrost claim extraction (full answer disclosed)
    -> exact generated queries + domains + SHA-256
    -> explicit approval, or skip all research
    -> Tavily Search (one attempt per claim, preferred domains enforced locally)
    -> bounded source excerpts -> Bifrost model assessment
    -> quotation membership / shape / figure guards
    -> immutable session checkpoints + replayable run
    -> offline artifact verifier -> script-free HTML
```

Implementation: `stale_check_v4.py` orchestrates and derives reports; `journey.py` tracks attempts; `gateway.py` and `acquisition.py` enforce adapter bounds; `cli.py` owns explicit environment reads and output reservation; `versions.py` dispatches exact contracts. `report.py` and `verify_artifacts.py` write/rederive runs; `stale-report` renders verified snapshots only.

## Browser execution boundary

`serve-assessment --output NEW_DIRECTORY` adds a separate loopback-only browser application. `web_service.py` authenticates every API read, mutation and download; `web_jobs.py` owns bounded launch-lifetime idempotency, one active worker and exact approval. The same `collect_transcript()` invocation waits for approval; polling never reruns extraction. The original 180-second maximum deadline includes human review. Cancellation/expiry are service lifecycle states, not new v4 reason codes; failure checkpoints are never advertised as complete runs. Already-sent calls cannot be recalled. The handler has a 5-second socket timeout and 10-second absolute connection lifetime; neither imposes a hard provider deadline.

The dedicated packaged React screen fetches verified HTML into a script-free sandbox. It does not weaken the legacy bundle parser's v3/v4 rejection. Default mode is explicitly mocked, using injected transports through the same lifecycle; `--live` requires `BIFROST_URL`, `BIFROST_MODEL`, `BIFROST_VIRTUAL_KEY` and either `TAVILY_API_KEY` or `BIFROST_SEARCH_RELAY` (searches through a Bifrost relay), explicit upload consent and exact-query approval. No fallback or automatic retry. See [ASSESSMENT-API.md](ASSESSMENT-API.md) for the wire contract, security boundary and launch steps in the package README.

## Run offline

From an installed standalone StaleCheck package, in a directory whose outputs do not exist:

```sh
python -m stalecheck stale-check --demo-v4 --output demo-v4
python -m stalecheck verify-artifacts demo-v4
python -m stalecheck stale-report demo-v4 --output demo-v4.html
```

Open the HTML locally. There are no scripts, external assets or remote rendering calls. All text and links are escaped. The packaged Acme scenario and its approval, usage and routing are scripted, not live acceptance or model performance measurements.

## Opt-in live run

Configure your own reviewed Maxim Bifrost deployment and model first. Have the relevant environment variables supplied securely by your shell/secret manager; do not place values in commands, repository files or screenshots.

```sh
python -m stalecheck stale-check --answer public-answer.txt \
  --live --confirm-outbound \
  --gateway-url http://127.0.0.1:8080 --model provider/model \
  --gateway-token-env BIFROST_VIRTUAL_KEY --tavily-key-env TAVILY_API_KEY \
  --primary-domain python.org --max-claims 3 \
  --max-attempts 7 --max-seconds 120 --output live-session
```

The full answer goes to Bifrost for extraction; selected claims and retained source excerpts also go there. The exact generated queries and domains are printed before Tavily. In an interactive terminal, type `APPROVE` only after reviewing them as public/non-sensitive. Decline, EOF or absent noninteractive approval sends **no Tavily request**. Extraction has already happened and may be billed.

**Search through a Bifrost relay.** `--search-relay NAME` sends each approved Tavily search to `<gateway-url>/relay/NAME/search` instead of `api.tavily.com`, authorised by the virtual key from `--gateway-token-env` (header `x-bf-vk`, the same key as the model calls). The Tavily key stays in the gateway, `--tavily-key-env` is not read, and the approved queries then reach Tavily by way of Bifrost, which the printed disclosure says. The relay returns Tavily's own JSON, so the response contract, usage accounting and report are unchanged, and `provenance.search` stays `tavily`. A refusal by the gateway (no relay grant, spent budget, unknown route) arrives as a non-200 status and stops the journey as `search_failed`, without any gateway text in the artifacts. Gateway configuration, observed responses and the real-gateway check are in [BIFROST-GATEWAY.md](BIFROST-GATEWAY.md). `scripts/live-check/run.sh` runs this live command against a real gateway, a small local model and the real Tavily API, directly and relayed; its results and the failure modes it met (a gateway timeout, a rejected reply) are in the same document.

For automation, `--approval approval.json` takes exactly `queries`, `primary_domains`, `sha256`, `approved: true`. The digest is SHA-256 of UTF-8 canonical JSON of the first two fields (sorted keys, compact separators, no ASCII escaping). The whole object must match the generated request. A mismatch declines research rather than broadening approval. Approval is an unsigned supplied attestation, not authenticated authorship. Do not preapprove unknown model output.

Live output differs intentionally from offline replay:

```text
live-session/
  session.json                 # initial incomplete marker, retained
  checkpoint-001.json ...       # exclusive bounded snapshots
  completion.json              # only when collection/report writing returns
  failure.json                 # if interrupted/failed; never provider exception text
  run/                         # normal independently verifiable artifact bundle
```

Use `verify-artifacts live-session/run` and `stale-report live-session/run --output live.html`. A completed artifact bundle means consistent retained assessment, **not** all research succeeded: inspect `stop_reason`, per-claim reasons and usage completeness. Session checkpoints are recovery evidence, not independently authenticated execution or a resumable job. Interrupted output is not overwritten; start a new session after reviewing possible billed attempts. A filesystem failure can leave partial output.

## Contracts and limits

- At most 8,000 answer characters, 8 selected claims, 3 retained sources/claim, 8,000 characters/source, 400 characters/quotation. Extraction and assessment output ceilings: 1,200 and 900 tokens.
- An extraction proposal holds exactly `quote`, `category`, `time_sensitivity`, `reason` and `query`: the quote must be an exact span of the answer, the query public and URL-free, the time-sensitivity one of `high`, `medium`, `low`. The `category` is only a display label, so a value outside the documented list is shown as `other` and the claim is kept; small models often copy the whole list from the prompt, and a 3B model lost about half of its claims to that before this was relaxed (see [LIVE-EVALUATION.md](LIVE-EVALUATION.md)). Any other defect rejects that proposal and records the reason.
- Each model call may take 60 seconds by default. `--gateway-timeout SECONDS` (1-180) raises that for a slow local model, and the journey deadline still caps every call. Set the gateway's own provider timeout a few seconds lower, so that Bifrost answers (HTTP 504) before the client gives up; `scripts/live-check/run.sh` does this with `CALL_TIMEOUT`.
- At most 25 outbound attempts and 180 cooperative seconds, both lowerable. The shared ledger counts actual adapter transport entry, not just successful replies. No client retry inside a v4 journey. Any gateway or fatal search failure stops subsequent spending.
- Deadline checks bracket calls; remaining time is passed to socket adapters. This is **not a hard process deadline**: OS DNS, response headers, detached sockets and uncooperative custom transports can overrun. No monetary cap is inferred from request or token limits.
- Bifrost client uses native `/v1/chat/completions`, explicit `x-bf-vk`, `stream: false`, no tools, direct origin-only HTTP loopback/HTTPS remote, no redirects or ambient proxy discovery. Requested fallbacks are explicit gateway configuration, not client rerouting after failure. Bifrost's reported latency is milliseconds and is converted to `latency_seconds`; its primary-served routing omits `is_fallback` and is recorded as false when provider/model identity is present. Unknown observed routing stays unknown.
- Tavily uses fixed HTTPS `/search` with Bearer auth (with `--search-relay`: the gateway's `/relay/NAME/search` with the virtual key), numeric `max_results`, boolean `auto_parameters`/`include_usage`, basic/general search, reviewed domain filtering; the adapter rejects returned hosts outside preference. Search hits whose otherwise safe HTTPS URLs contain query, fragment or percent escapes are skipped with a URL-free failure (the remaining valid hits are still assessed). Any other malformed hit (unsafe or non-HTTPS URL, empty content, out-of-range score) fails the whole search closed and stops later spending; blank publication dates are treated as absent. It does not locally fetch links or auto-extract search hits.
- Provider response sizes and metadata are bounded; unknown usage remains unknown. Validated tokens/credits from failed responses are retained where available. Mock subtotals are labelled non-spending.
- No auth/budget enforcement is claimed merely because client headers exist. Review Bifrost deployment virtual-key enforcement, model/provider restrictions, budgets, content logging/exporters and extra-header forwarding for its pinned release before sending data.

## Meaning of the results

- `MODEL_SUPPORTED`: a model-labelled support quotation occurs in a recorded source and passes lexical figure guards; this is not independently verified entailment.
- `POTENTIALLY_CHANGED`: a model-labelled contradiction quotation occurs in a source. It does not prove the answer was ever correct. `temporal_change_established` stays false.
- `UNVERIFIABLE`: missing, conflicting, malformed, unquoted or skipped evidence/assessment.

Publication/update is a provider estimate; effective dates remain unknown. Future dates do not score as useful date metadata. Verbatim quotation, preferred-domain match, distinct URLs and date availability are exposed as an **uncalibrated heuristic**, never high-confidence probabilities or independent agreement. All observations, relations, rationales and edits remain model-generated. Edits are never automatically applied, including overlapping spans or sentences with additional unsupported assertions.

**Freshness score per claim.** `claims[].freshness` has `state`, `newest_source_date`, `age_days`, `score`, `maximum` (100) and `calibrated` (always `false`). It looks only at the sources that carry a retained verbatim quotation and measures the age of the newest provider-estimated date against `verification_date`: `current` is at most 90 days, `recent` at most 365 days, `dated` older; `undated` means no quoted source has a usable date (dates after the verification date are ignored) and `none` means no quotation was retained. `score` is the integer 100 for a source dated on the verification date, falling linearly to 0 at 365 days, and `null` when undated or none. It describes how recent the evidence is. It does not show that the claim is current, changed or true, and the HTML report says so next to the model-labelled category; an at-a-glance table at the top of the report lists claim, category, verdict and freshness.

## Submission requirement / status / evidence

| Requirement | Implemented | Remaining acceptance |
|---|---|---|
| Answer -> claims -> cited assessment | v4 CLI and static HTML | A first small evaluation is in [LIVE-EVALUATION.md](LIVE-EVALUATION.md); a human-reviewed one and a hosted model remain |
| Maxim Bifrost extraction/assessment | Native HTTP client, explicit virtual key, mock contract tests; run end to end against a real Bifrost 2.4.0 container with stand-in upstreams (`scripts/gateway-check`: chat, fallback takeover, refusals, budget) and with a small local model, Qwen2.5-3B through Ollama (`scripts/live-check`, 2026-11-02: 4 model calls, 6.3 k tokens, no fallback) | A hosted model's responses; accuracy beyond the small evaluation |
| Tavily research | Exact approval, fixed Search adapter, optional Bifrost relay (`--search-relay`), mock contract tests, relay run against the real gateway; Search request/response, domain filtering and credit usage confirmed against the real Tavily API on 2026-11-02 (four calls), then three searches per journey in full live journeys, direct and through the relay (`scripts/live-check`) | Search-quality evaluation and invoice-level billing |
| Failure recovery and accounting | Terminal stops, checkpoints, shared ledger, unknown subtotals; a real gateway timeout (504 after 55 s) stopped a journey as `gateway_failed` with a verifiable partial report | No automatic retries |
| Supported/changed/unverifiable UI | Qualified model labels and full-answer/edit comparison | Human-reviewed evaluation; the AI-labelled one is in [LIVE-EVALUATION.md](LIVE-EVALUATION.md) |
| Freshness score per claim | `claims[].freshness` (state, age, 0-100 score) from provider-estimated dates, shown in the HTML report with the claim category | Live date coverage: Tavily `published_date` is a beta estimate and may be null |
| Reproducibility | V1/v2/v3 compatibility and v4 deterministic replay/tampering tests | Final refreshed standalone distribution acceptance |
| Challenge eligibility | Challenge 05 selected from supplied brief | Organizer confirmation: supplied brief marked it provisional |

Test files: `tests/test_stale_check.py` (v3 golden compatibility), `tests/test_stale_check_v4.py`, `tests/test_journey.py`, `tests/test_gateway.py`, `tests/test_acquisition.py`. Run the full suite with `python -m pytest -q` from the repository root.


## Provider references reviewed 2026-11-01

- https://docs.getbifrost.ai/quickstart/gateway/setting-up
- https://docs.getbifrost.ai/features/governance/virtual-keys
- https://docs.tavily.com/documentation/api-reference/endpoint/search

Documentation review and passing mock tests are not live service acceptance, deployment security validation, measured billing or model accuracy.
