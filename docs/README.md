# StaleCheck documentation index

StaleCheck is an independent, offline, evidence-conditioned temporal assessment product. The implemented v2 workflow converts **explicitly supplied review attestations** into versioned evidence, explains temporal coverage and uncertainty, verifies frozen artifacts, compares two verified runs, and exports a bounded JSON bundle (the read-only artifact viewer that consumed these bundles is not part of this repository). It does not infer approvals from acquired prose or authenticate a reviewer.

## Read first

- [V4 stale-answer journey, exact consent, accounting and submission status](V4-CONTRACT.md) — Bifrost + Tavily CLI/static report; legacy v1/v2/v3 replay preserved. Live results are in [LIVE-EVALUATION.md](LIVE-EVALUATION.md) and [BIFROST-GATEWAY.md](BIFROST-GATEWAY.md).

- [Architecture and trust boundaries](ARCHITECTURE.md)
- [User guide and standalone commands](USER-GUIDE.md)
- [Development and extension gates](DEVELOPMENT.md)
- [Operations and troubleshooting](OPERATIONS.md)
- [Third-party notices and licensing scope](THIRD-PARTY-NOTICES.md)

- [Frozen v2 domain and bundle interchange contract](V2-CONTRACT.md)
- [v1, acquisition and input-format reference](V1-REFERENCE.md) — retained v1 behavior, the acquisition adapter and the structured input formats.
- [Actual synthetic CLI fixture output](../fixtures/v2-cli/cli-transcript.json)
- [Actual exported bundle](../fixtures/v2-cli/viewer-bundle.json)
- [Actual before run manifest](../fixtures/v2-cli/before/manifest.json), [after run manifest](../fixtures/v2-cli/after/manifest.json), and [verified comparison](../fixtures/v2-cli/comparison.json)

## Quickstart: reproduce the source-only offline milestone

Prerequisite: Python 3.12 or newer. No runtime dependencies, network, credentials or paid provider calls are needed. Execute from the repository root, with a **new, absent** output directory. These commands use the same Python command syntax in PowerShell and POSIX shells.

```text
python -I scripts/generate_v2_fixture.py --output demo-v2
```

The driver invokes 13 actual CLI subprocesses using `-I -O`, explicitly bootstrapping only this product's `src` directory. This is source-isolated acceptance, **not installed-wheel acceptance**. It writes an unreviewed mock acquisition, two explicit invented proposals/reviews, converted evidence, two complete run directories, comparison, viewer bundle, and CLI transcript. Existing outputs are never overwritten. Delete only a known owned demo directory or choose a new name to rerun.

The retained reference output is `fixtures/v2-cli/`. Its before run supports the claim at 2026-05-01; its after run reports STALE at 2026-11-01, from the same reviewed evidence and answer date. The comparison records only a verification-clock change. Its dates, lifecycle claims, and reviewer identity are deliberately invented, not actual human review or official source truths.

For an already installed StaleCheck package, use `python -I -O -m stalecheck ...` or the `stalecheck` console script. A source checkout alone is not importable under `-I -m`; use the source driver above rather than claiming an isolated installed package exists. Fresh installed-wheel verification is a separate release acceptance gate.

## Explicit CLI workflow and file roles

All commands return JSON on stdout and exit 0 on success. Validation/I/O failures return an error JSON on stderr and exit 2; argparse usage errors also exit 2. No v2 validation error falls back to v1.

1. **Acquire only mocked, unreviewed content.** The acquisition command returns the complete acquisition object on stdout.
   ```text
   stalecheck acquire-mock --input fixtures/v2-cli/plan.json --responses fixtures/v2-cli/responses.json
   ```
   The fixture driver captures that exact JSON as `acquisition.json`. A new invocation has a new retrieval timestamp and acquisition hash, so do not reuse old proposal hashes against newly captured data.
2. **Supply a deliberate proposal and attestation**, including acquisition/candidate/content hashes, exact UTF-8 quote offsets, assertion/scope/value, explicit lifecycle end state, reviewer identity claim, attested=true, timestamp and rationale. The tool never invents these fields. Proposal helpers `acquisition_digest` and `candidate_digest` are in `stalecheck.review`; the source driver demonstrates synthetic values, not a human approval flow.
   ```text
   stalecheck review-evidence --acquisition fixtures/v2-cli/acquisition.json --proposal fixtures/v2-cli/proposal-1.json --output review-copy.json
   stalecheck verify-review --input review-copy.json --acquisition fixtures/v2-cli/acquisition.json
   ```
   Corrections additionally require `--previous-review FILE` and the matching previous_review_sha256 in the proposal.
3. **Convert explicitly selected validated reviews**. Conversion input has `schema_version:2.0`, `format:review_conversion`, and `reviews:[{review,previous_review}]`; previous_review is null for an original attestation. Converted evidence retains the complete original acquisitions and selected review records.
   ```text
   stalecheck review-to-evidence --input fixtures/v2-cli/conversion.json --output converted-copy.json
   ```
   There is no auto-generated review, implied factual approval, or silent correction supersession. Selecting both an original and an opposing correction may create a conflict; selecting a correction alone makes only that selected review evidentiary, while retaining its immediate prior record for provenance.
4. **Assess a strict v2 input** containing public structured claims and the converted `reviewed_evidence` document.
   ```text
   stalecheck verify --input fixtures/v2-cli/input-before.json --output run-before
   stalecheck verify --input fixtures/v2-cli/input-after.json --output run-after
   stalecheck verify-artifacts run-before
   stalecheck verify-artifacts run-after
   ```
   Each run contains manifest.json, input.json, config.json, report.json, events.jsonl and errors.jsonl. The verifier checks size/path/identity/hash consistency, validates the supplied reviews again, and rederives the entire domain report including every explanation/reference. Completed runs reject unregistered files, tampering and mixed/unknown versions.
5. **Compare independently verified frozen runs** and verify the comparison separately.
   ```text
   stalecheck compare --before run-before --after run-after --output comparison-copy.json
   stalecheck verify-comparison --before run-before --after run-after --input comparison-copy.json
   ```
   Comparison binds exact manifest/input/config/report digests; matches subject/assertion/scope/original as_of, not display IDs; rejects ambiguous duplicate semantic identities; distinguishes content, extraction, statement, policy, clock and provenance changes. Multiple categories may apply. `time_only:true` requires all other inputs/config fixed apart from claim IDs. These are retained-input differences, not causal proof. Comparison is not inserted into an invented viewer envelope field.
6. **Export verified runs as an interchange bundle**.
   ```text
   stalecheck export-viewer run-before run-after --output viewer-copy.json
   ```
   Export uses the exact snapshots read and checked by the local verifier, not unchecked rereads. The unchanged 1.0 envelope contains generated_at, runs[{manifest,report,events}], annotations:[], limitations. Reports carry v2 domain schema/version. Maximum output is **5 MiB (5,242,880 bytes)**, checked before exclusive output creation, with no truncation. Existing filenames, unsafe paths, symlinks/reparse points, wrong-product runs and duplicate run identities are rejected. There is no larger export mode.

## How to read v2 verdicts and explanations

- `SUPPORTED_AS_OF`: unconflicted positive reviewed coverage at the target.
- `CONTRADICTED_AS_OF`: unconflicted negative coverage at the target without an established stale transition; historical contradictions remain historical contradictions.
- `STALE`: a present-tense claim with unconflicted support at answer_date and a later effective invalidation covering verification_date.
- `ABSTAIN`: no known coverage, conflicting evidence, unsupported/ambiguous interpretation, request-scope mismatch or future target.

The original explicit as_of is retained separately from the evaluated target. Prior/current assessments list support, contradiction and excluded statement references with deterministic reason codes. These are mechanical evidence-coverage diagnostics: an ineligible ambiguous/out-of-scope/future claim can have mechanically covering evidence but must still abstain, as indicated by the top-level explanation reason code.

Intervals are `[effective_from,effective_to)`. V2 end states are `dated`, `open_ended`, `unknown`. Missing start or unknown end never establishes active coverage; an unknown end is **not** an open interval. V1 null end retains its exact old open-ended meaning. Publication/retrieval clocks never substitute for lifecycle effective dates. V2 inputs are bounded to five explicit public claims, 100 supplied reviews, and the existing 2,000,000-byte per-file/structural limits; repeated embedded acquisitions can hit the byte/structure limit before the review count ceiling.

## Architecture and independent boundary

- `review.py`: existing strict acquisition/proposal validation, exact quote bytes, canonical digests and immediate correction linkage.
- `reviewed_evidence.py`: explicit conversion and type-exact deterministic evidence derivation.
- `report_v2.py`, `temporal_v2.py`: strict v2 input/config, coverage, abstention, stale transition and deterministic explanation generation.
- `versions.py`: exact allowlisted dispatch; `report.py` writes standard self-contained runs while retaining v1 derivation.
- `verify_artifacts.py`: bounded registered-file verification and exact verified snapshot reuse.
- `comparison.py`: independently verified run comparison and comparison rederivation.
- `export.py`: safe exclusive bounded JSON output and unchanged viewer envelope.
- `cli.py`: command-line entry point.
- `scripts/generate_v2_fixture.py`: synthetic source acceptance driver, not product runtime or a human review UI.

Current distribution metadata is 0.4.0; the v2 **domain** config/report version remains 0.2.0, with schema 2.0.

## Trust boundaries, limitations and recovery

Attested=true records a caller-supplied attestation, not authenticated authorship or semantic entailment. Exact quotes/hashes prove consistency, not truth, source authenticity, authority, or complete review history. Anyone able to replace every unsigned artifact coherently can create another internally consistent run. Corrections retain only the immediate prior record; older ancestors are not resolved and no append-only or signed log is claimed.

Provider publication metadata may be a publication-or-update estimate. Search content is a snippet and extract content is extracted text; neither is original publisher document bytes. Partial acquisitions stay partial, selected reviews do not imply complete coverage, and no source URL is fetched by assessment/verification/export. Live Tavily acquisition is separately opt-in and live compatibility remains unverified; the provided workflow exercises MockTransport only.

A failed run write may leave an explicit incomplete manifest. Do not treat it as verified, resume it silently, overwrite it, or edit files to manufacture completion. Keep it for inspection or delete only that known owned directory, then rerun with a new output name. Oversize viewer export leaves no output and never truncates evidence; export fewer complete runs or preserve the original run artifacts outside the viewer. Do not place additional notes inside a completed run directory: unregistered files intentionally invalidate it.

## Developer checks and observed acceptance

The observations below are historical source-milestone results. Later release
coordination reports refreshed `wheels02` installed checks with aggregate
**1,151 passed and 5 skipped** across the acceptance effort, not a
StaleCheck-only count. That report does not establish independent extraction
acceptance of the source-snapshot release archives, which remains outstanding.
These documentation edits performed no tests or installation.

Run from the product directory with an existing pytest installation (no dependency refresh was performed):

```text
python -m pytest tests -q
```

Observed 2026-11-01 with available Python 3.12.10: **324 passed, 1 skipped** in the final full product suite. The skip is the existing symlink test because the OS account lacks symlink creation privilege. All **9 retained root cross-product compatibility cases passed** separately, without editing root files; the StaleCheck v1 golden was also copied byte-for-byte into product-local `fixtures/v1-retained/` for standalone acceptance. The first product run had 318 passed, 1 skipped and one failure because `-I -m stalecheck` could not find an uninstalled package; the source-isolation test was corrected to explicitly bootstrap only product source, not to claim an installed package. It was not a verdict/semantic failure. The retained fixture transcript records the exact available interpreter and all 13 actual `-I -O` CLI calls. The existing `.venv/Scripts/python.exe` launcher was reported unavailable by the coordinator; no environment repair or install was attempted.

The broader release's independently installed wheel, viewer import/browser behavior, refreshed release packaging, presentations, live acquisition, authenticated review and effectiveness evaluation are **not** certified by this source-only milestone. No release build, install, new dependency, network call or credential access was performed here.
