# Live verification cases

StaleCheck ran these cases in Chrome on November 11, 2026. A signed-in GitHub Copilot CLI handled claim extraction and evidence assessment through Maxim Bifrost. After approval, Bifrost relayed the exact Tavily searches. Each recorded assessment used three model calls and two searches, with zero reported fallbacks. The five assessments used 15 model calls and 10 searches in total. You can open the saved reports without credentials.

| Claim from the answer | StaleCheck verdict | Evidence freshness | Independent source check |
|---|---|---|---|
| Python 3.14.8 is still the latest Python 3.14 maintenance release | Potentially changed | 95/100 | Python lists 3.14.8 as superseded by 3.14.10. [Python 3.14.8 release page](https://www.python.org/downloads/release/python-3148/) |
| Python 3.14.10 was released on October 25, 2026 | Unverifiable | Unscored | The publisher confirms the date. The quote retained during this run lacked the date, so StaleCheck withheld a verdict. [Python 3.14.10 release page](https://www.python.org/downloads/release/python-31410/) |
| Node.js 20 is still supported by the Node.js project | Potentially changed | 0/100 | Node.js lists v20 as end of life. The dated quotation retained by StaleCheck was more than a year old. [Node.js release schedule](https://nodejs.org/en/about/previous-releases) |
| Node.js 24 remains a supported LTS release | Model-supported | 95/100 | Node.js lists v24 as LTS. [Node.js release schedule](https://nodejs.org/en/about/previous-releases) |
| GitHub Free includes 3,000 Actions minutes per month for private repositories | Unverifiable | 29/100 | GitHub lists 2,000 minutes for Free. The model treated a 3,000-minute quote about GitHub Team as supporting the Free claim. The report now places both sides of that conflict next to each other. [GitHub plan allowances](https://docs.github.com/en/billing/reference/product-usage-included) |
| GitHub Pro includes 3,000 Actions minutes per month for private repositories | Model-supported | 50/100 | GitHub lists 3,000 minutes for Pro. [GitHub plan allowances](https://docs.github.com/en/billing/reference/product-usage-included) |
| GitHub.com still supports `actions/upload-artifact` v3 | Potentially changed | 0/100 | GitHub retired v3 for GitHub.com. Its notice separately addresses GitHub Enterprise Server. [GitHub retirement notice](https://github.blog/changelog/2024-04-16-deprecation-notice-v3-of-the-artifact-actions/) |
| GitHub.com retired v3 of the artifact actions on January 30, 2025 | Model-supported | 0/100 | GitHub's changelog confirms that date. [GitHub retirement notice](https://github.blog/changelog/2024-04-16-deprecation-notice-v3-of-the-artifact-actions/) |
| Thomas Bach is still president of the IOC | Potentially changed | Unscored | The IOC lists Bach's term as 2013–2025. [IOC president list](https://www.olympics.com/ioc/history/presidents) |
| Kirsty Coventry is the current president of the IOC | Model-supported | Unscored | The IOC names Coventry as current president. [IOC president list](https://www.olympics.com/ioc/history/presidents) |

StaleCheck selected all ten intended claims. Eight verdicts aligned with primary sources and two abstained. Four of five stale claims were flagged. The remaining stale claim concerned the GitHub Free quota, where the retrieved material conflicted. The other abstention concerned a correct Python release date. This small, hand-picked set exercises release versions, service allowances, an API retirement and a leadership change.

Evidence freshness uses the age of the newest dated quotation. The 2025 GitHub retirement notice scored zero for recency during this assessment and still supported the historical retirement date. Both IOC claims received verdicts from quotes without usable dates, so their freshness remained unscored.

- **Python** · [Approval screenshot](screenshots/python-live-approval.png) · [Download HTML report](evidence/python-release/report.html) · [Live receipt](evidence/python-release/verification.json) · [Browser summary](evidence/python-release/browser-summary.json)
- **Node.js** · [Download HTML report](evidence/nodejs-support/report.html) · [Live receipt](evidence/nodejs-support/verification.json) · [Browser summary](evidence/nodejs-support/browser-summary.json)
- **GitHub plan quota** · [Download HTML report](evidence/github-quota/report.html) · [Live receipt](evidence/github-quota/verification.json) · [Browser summary](evidence/github-quota/browser-summary.json)
- **GitHub artifact API** · [Download HTML report](evidence/github-artifacts/report.html) · [Live receipt](evidence/github-artifacts/verification.json) · [Browser summary](evidence/github-artifacts/browser-summary.json)
- **IOC leadership** · [Download HTML report](evidence/ioc-leadership/report.html) · [Live receipt](evidence/ioc-leadership/verification.json) · [Browser summary](evidence/ioc-leadership/browser-summary.json)

Save any HTML report and open it in your browser. Select a claim to see the source quote and the reason for its verdict.

## Verify offline

From a clone, install the package with Python 3.12 or newer. Then verify the original artifact hashes and the report derivation against the recorded input.

```bash
python -m pip install .
python -m stalecheck verify-artifacts docs/evidence/python-release/run
python -m stalecheck verify-artifacts docs/evidence/nodejs-support/run
python -m stalecheck verify-artifacts docs/evidence/github-quota/run
python -m stalecheck verify-artifacts docs/evidence/github-artifacts/run
python -m stalecheck verify-artifacts docs/evidence/ioc-leadership/run
python -m stalecheck stale-report docs/evidence/python-release/run --output python-review.html
```

Each report derives from five registered artifacts and a manifest. The verification checks internal consistency and recorded bytes. Unsigned hashes cannot authenticate the original sources or establish that a claim is true.

## Use the result from another application

A documentation or support tool can pass answer text to [the StaleCheck CLI](../README.md#live-run-with-your-own-bifrost-and-tavily) and consume `report.json`. Each claim has a verdict, reason code, freshness object, source references and model-generated proposal. The browser's **Download verified run (.zip)** exports the original answer with the other run files so the receiving application can verify the report offline. The [loopback API](ASSESSMENT-API.md) is available to local clients using a launch token.

## Related work

FActScore and SAFE evaluate factuality claim by claim using retrieved evidence. RARR finds sources and revises unsupported statements. StaleCheck applies claim-level review to an answer that may have aged, records the approved search, shows the dates and quotations behind each verdict, and leaves proposed edits for an editor. The small live check reports abstentions separately because a failed retrieval and a wrong verdict call for different fixes. [FActScore](https://aclanthology.org/2023.emnlp-main.741/) · [SAFE](https://deepmind.google/research/publications/85420/) · [RARR](https://aclanthology.org/2023.acl-long.910/)
