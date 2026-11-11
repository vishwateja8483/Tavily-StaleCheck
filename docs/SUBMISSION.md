# StaleCheck hackathon submission

**Theme** Build a Stale-Answer Detector for AI Applications (Problem Statement 06).

**Project description** StaleCheck extracts time-sensitive claims from AI-generated answers through Maxim Bifrost. Users review the exact Tavily queries and allowed domains before the first search. StaleCheck then compares claims against retrieved source excerpts, checks quotations against the excerpts, and reports verdicts and freshness scores. Suggested edits are kept alongside the original for review.

## What to submit

1. Sign in to HackCulture. Open **Manage → Submissions → Problem Statements & Prototype Submission** and select the stale-answer detector theme. The public event page lists prototype submission through **November 15, 2026 at 11:59 p.m.** and judging on November 29. [Official event and FAQ](https://hackculture.io/hackathons/ai-global-hackathon)
2. Enter **StaleCheck** and add the public GitHub repository URL. Check it from a signed-out browser. Include the README, install instructions and bundled browser app.
3. Upload [the PowerPoint pitch deck](pitch/StaleCheck-PS06.pptx). It covers the problem, architecture, Bifrost and Tavily, and screenshots from the working prototype.
4. Optionally add a shareable demo video URL. Test the link while signed out. A deployed app is optional. Join the event Discord for organizer updates. [HackCulture FAQ](https://hackculture.io/hackathons/ai-global-hackathon) · [Official Discord](https://discord.gg/STHG8YQdd)

StaleCheck addresses one problem statement. Bifrost and Tavily each have a role in that solution. The event's instructions for selecting a Bifrost primary theme apply to entries that combine a separate Bifrost challenge with a Tavily challenge.

## Demo and evidence

| Requirement | Where to see it |
|---|---|
| Identify time-sensitive claims | The browser demo accepts the full Acme answer and proposes four literal queries. Live extraction runs through Bifrost. |
| Recheck against public sources | Tavily Search runs after the query and domain approval. A local Bifrost gateway can hold and relay the Tavily key. |
| Show freshness for each relevant claim | The report includes a score from the newest dated quotation or an explicit unscored value. |
| Separate sources from model interpretation | The report links the source and shows verbatim evidence separately from the model's reasoning and edit proposal. |
| Show a working prototype | Run the offline browser demo and download its report and manifest. Use the live guide for a credentialed run. |

Lead with [the saved live proof and replay commands](LIVE-PROOF.md). Use the [presentation guide](HACKATHON-DEMO.md) for the walkthrough and [live Copilot setup](COPILOT-BIFROST-SETUP.md) for a credentialed run.

## Live proof for the presentation

Use the two-claim Python release run to show the decision boundary. Copilot proposed two queries through Bifrost and the browser required separate approval before Tavily searched `python.org`. The older-version claim came back **Potentially changed**, with a **95/100** freshness score. The release-date claim came back **Unverifiable** because the retained quotation lacked the figures needed to support the release date. Python.org independently lists the release date as October 25, 2026. [Python 3.14.10 release record](https://www.python.org/downloads/release/python-31410/)

The run made three Copilot calls through Bifrost and two Tavily searches. All five saved artifacts verified. The [matching live verification receipt](evidence/python-release/verification.json) records the exact approval, verdicts, per-attempt accounting and source freshness. The pitch deck leads with the Python run and includes results from the five live cases. The GitHub Free quota case shows how conflicting excerpts can leave a stale claim unverifiable.

```bash
bash scripts/copilot-check/run.sh --verify \
  --answer-file docs/examples/python-release-answer.txt \
  --domain python.org --max-claims 2
```

For a live presentation, start the workspace and paste the example into the answer editor. Read out the domain during approval, then open the report and follow the source link for the changed claim. Point to the original and proposed edit side by side. The retrieved excerpts and model output may vary between runs.
