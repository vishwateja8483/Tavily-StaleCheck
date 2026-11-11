# Hackathon demo: StaleCheck

## Show the saved live result first

Open the [Python live case](LIVE-PROOF.md) and show the query approval screenshot. Then download and open its [HTML report](evidence/python-release/report.html). It was rendered from a recorded Chrome run with Copilot through Bifrost and Tavily Search. You can open and verify the saved result without keys.

The browser demo below uses scripted providers and invented Acme data. Use it if someone wants to try the full interaction without credentials.

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

1. **0:00–0:25.** Show the original claim that Python 3.14.8 was still the latest release. Explain how a confident answer can become stale.
2. **0:25–1:00.** Show the real approval screen. Bifrost routed claim extraction to Copilot. Tavily searched only after the two queries and `python.org` were approved.
3. **1:00–2:00.** In the report, open both claims. Show the newer release, the source quote, and the proposed edit. Then show why the release date remained unverifiable in the retrieved material even though the publisher independently confirms it.
4. **2:00–2:35.** Show the run ZIP or run `verify-artifacts` on the saved bundle. Explain that it checks integrity and reproduces the report from the recorded input.
5. **2:35–3:00.** Describe how a docs or support team could check an answer before publishing it. Mention that the live spot checks cover a small number of claims.

If the judges want to interact, use the live Copilot setup or explicitly switch to the mock demo. [Live setup](COPILOT-BIFROST-SETUP.md)
