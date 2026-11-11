# Product

<!-- impeccable:product-schema 1 -->

> Provenance: written without an interview. The owner asked repeatedly for unattended, end-to-end work, so every fact below is inferred from the repository, `README.md`, `docs/USER-GUIDE.md`, the PS-06 problem statement and the HackCulture event page. Items marked *(inferred)* were not confirmed by the owner.

## Platform

web

## Users

- **People who reuse AI-written answers** *(inferred)*: developers, support and documentation writers, analysts. They hold an LLM answer that mentions prices, versions, support windows, laws, leaders or availability, and must decide before reusing it which sentences have aged.
- **Hackathon evaluators** *(inferred)*: judges of the Geek Room Global AI Hackathon 2026 (Problem Statement 06, Tavily track, with Bifrost integration required). They open the repository, read the README and slides, and may run the offline demo. Judging criteria: innovation, technical execution, problem and impact, use of AI, user experience, scalability and potential.

## Product Purpose

StaleCheck takes an LLM answer, finds its time-sensitive claims through the Maxim Bifrost gateway, re-checks them against live sources with Tavily Search, and reports for each claim a verdict (model-supported, potentially changed, unverifiable), a 0-100 freshness score, verbatim source quotations and links, and a suggested edit that is never applied automatically. Success is a reader who can see which claim to fix, and why, without trusting a model's say-so.

## Positioning

A verdict moves only on a quotation found verbatim in a retained Tavily excerpt; everything a model says stays labelled model-generated. Two consent steps (answer upload, then the exact search queries and domains) gate every disclosure and every search. "Unverifiable" is a first-class result, not a failure. A generic AI fact-checker or search chat cannot truthfully claim these guarantees.

## Operating Context

- Local, single-user, loopback-only web app (`stalecheck serve-assessment`). The launch URL carries a private fragment token that the page strips.
- Two modes: **demo** (mock providers, invented "Acme" evidence, no network) and **live** (Bifrost for every model call, Tavily for searches, optionally relayed through Bifrost). The page must always say which mode it is in.
- Journey: answer and consent → exact-query approval → verified report. Artifacts: `report.html`, `report.json`, `input.json`, `manifest.json`, checked by `stalecheck verify-artifacts`.
- The report is script-free HTML shown in a sandboxed iframe under a strict content-security policy. The app page forbids external requests, fonts, images and scripts, and stores nothing in the browser.
- Real live runs use GitHub Copilot as the model provider behind Bifrost and need a Copilot sign-in and a Tavily key that only the owner can supply.

## Capabilities and Constraints

- Claim categories: pricing, api, version, law, leadership, availability, other. Freshness states: current (≤90 days), recent (≤365 days), dated.
- Freshness score = recency of the newest quoted source relative to the verification date. It is an uncalibrated heuristic, not a probability; "evidence strength" is likewise uncalibrated. A claim with no dated quoted source is "not scored".
- Bounded runs: claim, attempt and time ceilings; no automatic retries; checkpoints kept on failure.
- Vocabulary to keep: *model-supported*, *potentially changed*, *unverifiable*, *model-generated*, *requires review*. Never "true", "false", "verified fact" or "confidence %".
- Python standard library only at runtime; the UI is React 19 + TypeScript built by Vite into `src/stalecheck/assessment_static/` and committed.
- Undecided: whether a hosted deployment is wanted (none exists); the final demo-video link.

## Brand Commitments

- Name: **StaleCheck**. Voice in existing copy: plain, exact, auditable, candid about limits ("Verdicts and proposed edits require human review").
- The owner asked that the interface stop looking "AI sloppy" and take inspiration broadly. Recorded as given; no aesthetic is prescribed here.
- Every submission claim about AI use and tools must be honest: the product's models run through Bifrost; the code was written with AI assistance.

## Evidence on Hand

- Offline demo fixture with invented Acme data (`fixtures/`, `--demo-v4`); 766+ passing tests.
- Historical live run on 2026-11-02 with a local open-weights model through Bifrost 2.4.0 and the real Tavily API (`docs/LIVE-EVALUATION.md`). Not the current provider.
- A real GitHub Copilot + Tavily run has not yet happened. No human-reviewed accuracy benchmark, user testimonials, customers or deployed instance exist; do not imply any.
- Problem statement: `problem_explanation_urf74r9ydml.pdf` (PS-06). Submission needs a public repo, a PPT, and optionally a demo video link; prototype deadline Nov 15, 2026.

## Product Principles

1. **Evidence before opinion.** Only verbatim quotations move a verdict; inference is always labelled.
2. **Consent before disclosure or spend.** Nothing is sent or searched until the person approves exactly what will be sent.
3. **Say "unverifiable" instead of guessing,** and print the limits inside the product.
4. **Bounded and replayable.** Ceilings up front, hashed artifacts after.
5. **Make both integrations legible.** Every run shows what Bifrost did (model calls, routing, tokens) and what Tavily did (queries, sources, dates, credits).

## Accessibility & Inclusion

- Keyboard-first: skip link, focus moves to the new step's heading, visible focus rings, no keyboard traps.
- No horizontal overflow at 390 px; respect reduced motion; WCAG AA contrast intent *(inferred from existing DOM-contract tests)*.
- Status and verdicts never rely on colour alone.
