# Hackathon demo: StaleCheck

## What this demo shows

StaleCheck checks time-sensitive claims in an AI answer. Bifrost extracts claims and assesses retrieved excerpts; Tavily supplies search results. The live flow asks for consent to process the answer, then asks the user to approve the exact queries and domains before any search. The report keeps evidence separate from model inference, includes verbatim excerpts and source links, and never applies suggested edits.

The demo below uses **mock providers and invented Acme data**. It demonstrates the workflow and safety boundaries, not live provider compatibility, factual accuracy, or real search results.

## Run the browser demo

From the repository root with the package installed:

```bash
mkdir -p runs
python -m stalecheck serve-assessment --output runs/hackathon-demo
```

Keep the process running and open the private `127.0.0.1` launch URL it prints. The launch URL is a short-lived local credential; do not share or post it. The default mode is offline demo mode and does not call Bifrost or Tavily.

Walkthrough:

1. Select **Load Acme example**.
2. Read the mock-mode label and the answer; give consent and select **Start mocked assessment**.
3. Review the four proposed queries and the exact domain. Select **Approve exact mocked searches** to complete the demo, or **Decline searches** to show that declining prevents research.
4. Open the verified report. Point out the quote/source, the per-claim verdict and freshness, the model-generated rewrite, and the explicit limitations. Download `report.json` or `manifest.json` to show the verifiable artifacts.
5. Explain that a completed job means processing finished; it does not mean every claim is true.

The assessment server persists demo artifacts in the launch directory. Use a new, empty output directory for another launch; the server does not resume a previous session after restart.

## Run the offline CLI demo

This is useful for a fast, reproducible terminal presentation:

```bash
mkdir -p runs
python -m stalecheck stale-check --demo-v4 --output runs/hackathon-cli-demo
python -m stalecheck verify-artifacts runs/hackathon-cli-demo
python -m stalecheck stale-report runs/hackathon-cli-demo --output runs/hackathon-cli-demo.html
```

The browser and CLI share a fixture that produces four selected claims: one model-supported, one potentially changed, and two unverifiable. Verification checks artifact consistency, not source authenticity or truth. This path is offline and does not need API keys.

## Live Bifrost and Tavily integration

The code has a live path, but the mock demo above does not exercise it. A live run requires a reachable Bifrost gateway, a configured model and virtual key, plus Tavily access directly or through a Bifrost search relay. Provider requests may incur charges.

For the browser app, set `BIFROST_URL`, `BIFROST_MODEL`, `BIFROST_VIRTUAL_KEY`, and either `TAVILY_API_KEY` or `BIFROST_SEARCH_RELAY`, then launch with `--live`. Read [the live setup in the README](../README.md#live-run-with-your-own-bifrost-and-tavily) and [the Bifrost/Tavily integration notes](BIFROST-GATEWAY.md) first. Use only credentials and systems you are authorized to use; never paste keys into the browser form or publish them.

Do not present the offline fixture as a live-provider result. If live services are unavailable during judging, say so and demonstrate the explicit mock flow instead.

## Hackathon track

StaleCheck addresses **Problem Statement 06 · Tavily**, the stale-answer detector challenge. The live path uses Bifrost for model requests and Tavily for approved searches.

## Three-minute presentation

- **0:00–0:25 — Problem:** confident AI answers can contain claims that age at different rates; checking every sentence manually is slow.
- **0:25–0:50 — Differentiator:** StaleCheck does not let a model silently browse or rewrite. It requires answer-processing consent and approval of the exact search request.
- **0:50–1:45 — Demo:** load the supplied answer, consent, inspect the proposed query, approve it, and open the report.
- **1:45–2:20 — Trust boundary:** show a verbatim quote, source, freshness metadata, and the model-generated suggestion. Explain why a quote match is not semantic proof and why unverifiable is a valid result.
- **2:20–3:00 — Engineering and honesty:** show artifact verification and bounded/no-retry behavior. State clearly that the browser walkthrough uses invented mock evidence; live Bifrost/Tavily integration is a separate opt-in path.

Do not claim benchmark accuracy from the current small evaluation: its labels were AI-written and not human-reviewed. See [the evaluation and its limitations](LIVE-EVALUATION.md).
