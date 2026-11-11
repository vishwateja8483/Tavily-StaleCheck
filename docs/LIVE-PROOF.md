# Live verification cases

StaleCheck ran these cases in Chrome on November 11, 2026. A signed-in GitHub Copilot CLI handled claim extraction and evidence assessment through Maxim Bifrost. After approval, Bifrost relayed the exact Tavily searches. Each recorded assessment used three model calls and two searches, with zero reported fallbacks. The saved reports render without credentials.

| Claim from the answer | StaleCheck verdict | Evidence freshness | Independent source check |
|---|---|---|---|
| Python 3.14.8 is still the latest Python 3.14 maintenance release | Potentially changed | 95/100 | Python lists 3.14.8 as superseded by 3.14.10. [Python 3.14.8 release page](https://www.python.org/downloads/release/python-3148/) |
| Python 3.14.10 was released on October 25, 2026 | Unverifiable | Unscored | The publisher confirms the date. The quote retained during this run lacked the date, so StaleCheck withheld a verdict. [Python 3.14.10 release page](https://www.python.org/downloads/release/python-31410/) |
| Node.js 20 is still supported by the Node.js project | Potentially changed | 0/100 | Node.js lists v20 as end of life. The dated quotation retained by StaleCheck was more than a year old. [Node.js release schedule](https://nodejs.org/en/about/previous-releases) |
| Node.js 24 remains a supported LTS release | Model-supported | 95/100 | Node.js lists v24 as LTS. [Node.js release schedule](https://nodejs.org/en/about/previous-releases) |

The checks illustrate a stale claim, a supported claim and a case where retrieval did not capture enough evidence to support a true statement. Four claims are too few to estimate accuracy. The freshness score uses the age of the newest dated quotation. A current source can still contain weak evidence, and an old source can still describe a true fact.

- **Python** · [Approval screenshot](screenshots/python-live-approval.png) · [Download HTML report](evidence/python-release/report.html) · [Live receipt](evidence/python-release/verification.json) · [Browser summary](evidence/python-release/browser-summary.json)
- **Node.js** · [Download HTML report](evidence/nodejs-support/report.html) · [Live receipt](evidence/nodejs-support/verification.json) · [Browser summary](evidence/nodejs-support/browser-summary.json)

Save either HTML report and open it in your browser. Select a claim to see the source quote and the reason for its verdict.

## Verify offline

From a clone, install the package with Python 3.12 or newer. Then verify the original artifact hashes and the report derivation against the recorded input.

```bash
python -m pip install .
python -m stalecheck verify-artifacts docs/evidence/python-release/run
python -m stalecheck verify-artifacts docs/evidence/nodejs-support/run
python -m stalecheck stale-report docs/evidence/python-release/run --output python-review.html
```

Each report derives from five registered artifacts and a manifest. The verification checks internal consistency and recorded bytes. Unsigned hashes cannot authenticate the original sources or establish that a claim is true.

## Use the result from another application

A documentation or support tool can pass answer text to [the StaleCheck CLI](../README.md#live-run-with-your-own-bifrost-and-tavily) and consume `report.json`. Each claim has a verdict, reason code, freshness object, source references and model-generated proposal. The browser's **Download verified run (.zip)** exports the original answer with the other run files so the receiving application can verify the report offline. The [loopback API](ASSESSMENT-API.md) is available to local clients using a launch token.
