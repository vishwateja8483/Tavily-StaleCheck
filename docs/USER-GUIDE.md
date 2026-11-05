# StaleCheck user guide

## What this product does

StaleCheck performs evidence-conditioned lifecycle assessment, not web truth checking. V2 supports explicitly supplied review attestations, deterministic evidence conversion, temporal explanations, frozen artifact verification/comparison, and bundle export. Those commands are offline; `stale-check --live` is a separate opt-in path that reads specified credential environment variables and sends approved outbound requests.

Paths here are relative to the repository root, which contains `pyproject.toml`.

The v2 producer is implemented in source. Older overview statements that conversion is unavailable refer to a prior milestone. Current package metadata is `0.4.0`; the v2 domain config/report uses `0.2.0`.

## Independent installed release

Use Python 3.12+ and an owner-approved current wheel whose source revision and checksum have been checked through the release's process. From a clean directory outside an extracted archive:

```sh
python -m venv /absolute/path/stalecheck-env
/absolute/path/stalecheck-env/bin/python -m pip install --no-index --no-deps /absolute/path/release/packages/stalecheck-0.4.0-py3-none-any.whl
/absolute/path/stalecheck-env/bin/python -I -m stalecheck --help
/absolute/path/stalecheck-env/bin/python -I -m stalecheck doctor
/absolute/path/stalecheck-env/bin/python -I -m stalecheck demo --output /absolute/path/work/fd-demo
/absolute/path/stalecheck-env/bin/python -I -O -m stalecheck verify-artifacts /absolute/path/work/fd-demo
```

Substitute the actual approved wheel filename if release metadata changes. No dependency acquisition is needed for this standard-library runtime; `--no-index --no-deps` prevents it. This documentation did not perform installation or certify any wheel. Windows uses the environment's `Scripts/python.exe`, for example `& 'C:\work\stalecheck-env\Scripts\python.exe' -I -m stalecheck doctor` in PowerShell.

Below, `python` means the selected environment interpreter. The installed `stalecheck` console command accepts the same arguments. Run outputs and output files must be new; create a clean work directory and choose distinct names for repeat attempts.

## Source-only path and actual fixture evidence

For any CLI command without installing, replace `python -I -m stalecheck` with:

```sh
python -I -O -c 'import sys; sys.path.insert(0, "/absolute/path/stalecheck/src"); from stalecheck.cli import main; raise SystemExit(main())' doctor
```

Windows Python requires a path such as `C:/work/stalecheck/src` inside the string. `-I` ignores `PYTHONPATH`; merely setting that variable does not make `-I -m` load an uninstalled package.

The existing source driver provides a complete, explicitly synthetic end-to-end fixture recipe:

```sh
python -I /absolute/path/stalecheck/scripts/generate_v2_fixture.py --output /absolute/path/work/fd-v2-new
```

This is a **source-only driver**, not the installed product CLI. It locates its own product `src`, invokes the actual CLI in isolated optimized subprocesses, and supplies **invented** review identities/attestations. It is not a human review UI. It writes plan/responses, acquisition, proposals/reviews, conversion/evidence, before/after inputs and runs, comparison, viewer bundle, and transcript. The output directory must not exist. This documentation inspected but did not execute the driver.

Retained `fixtures/v2-cli/cli-transcript.json` records the source-only commands and results. Its reference case uses answer date `2026-01-01`, support from `2025-01-01` up to but excluding `2026-06-01`, and explicit open-ended contradiction beginning `2026-06-01`. The before run targets `2026-05-01` and supports the claim; the after run targets `2026-11-01` and marks it stale. The retained comparison records `causes: ["clock"]`, `time_only: true`. These are invented lifecycle data, not observed publisher facts or authenticated human review.

## Step-by-step CLI workflow

The commands below use the **retained source fixtures**, absolute project paths, and fresh output names in the current clean work directory. They work with a current installed package or the explicit source prefix above. Fixtures under `fixtures/` are source companion files, not guaranteed installed wheel resources.

### 1. Inspect unreviewed mock acquisition

```sh
python -I -m stalecheck acquire-mock --input /absolute/path/stalecheck/fixtures/v2-cli/plan.json --responses /absolute/path/stalecheck/fixtures/v2-cli/responses.json
```

There is **no `--output` argument**: stdout is the full acquisition JSON. Capture it as exact UTF-8 if needed. Acquisition is not a run and cannot go to `verify-artifacts` or directly become lifecycle evidence. Inspect `status` (`acquired`, `partial`, `failed`), `failures`, `review_required`, and usage; exit 0 does not guarantee complete acquisition.

A new invocation creates a new retrieval timestamp and therefore new acquisition/candidate hashes. **Do not reuse the retained proposals against that fresh acquisition.** The following review commands deliberately use the retained matching `acquisition.json`. The source driver demonstrates consistent regeneration with new hashes; real review must explicitly supply its own attestation and dates.

### 2. Record and check supplied attestations

```sh
python -I -m stalecheck review-evidence --acquisition /absolute/path/stalecheck/fixtures/v2-cli/acquisition.json --proposal /absolute/path/stalecheck/fixtures/v2-cli/proposal-1.json --output fd-review-1.json
python -I -m stalecheck verify-review --input fd-review-1.json --acquisition /absolute/path/stalecheck/fixtures/v2-cli/acquisition.json
python -I -m stalecheck review-evidence --acquisition /absolute/path/stalecheck/fixtures/v2-cli/acquisition.json --proposal /absolute/path/stalecheck/fixtures/v2-cli/proposal-2.json --output fd-review-2.json
python -I -m stalecheck verify-review --input fd-review-2.json --acquisition /absolute/path/stalecheck/fixtures/v2-cli/acquisition.json
```

Proposals require acquisition/candidate/content SHA-256, candidate ID, exact quote and UTF-8 byte start/end, subject, assertion `is_supported`, scope, boolean value, nullable `effective_from`, `end_state`, reviewer identity claim, `attested: true`, timezone-aware `reviewed_at`, and rationale. Only `dated` includes `effective_to`; supplying that field for open/unknown is invalid. Use `stalecheck.review.acquisition_digest()` and `candidate_digest()` when preparing canonical snapshot identities; do not hash arbitrary pretty-printed JSON and expect the same value.

A correction also includes `previous_review_sha256` and supplies `--previous-review FILE` to **both** review creation and verification. Only that immediate record is checked. A correction is not automatic deletion or supersession of earlier temporal facts.

### 3. Convert selected review records

```sh
python -I -m stalecheck review-to-evidence --input /absolute/path/stalecheck/fixtures/v2-cli/conversion.json --output fd-reviewed-evidence.json
```

`conversion.json` embeds the actual full review objects; it is not a list of file paths. Its shape is `schema_version: "2.0"`, `format: "review_conversion"`, `reviews: [{review, previous_review}]`, with null previous review for originals. One to 100 distinct supplied reviews are required. Repeated embedded acquisitions can exceed file/structure bounds before the count limit.

The retained conversion uses the same review contents as the matching review-copy commands above; it does not dynamically read those newly named output files. When building your own workflow, explicitly assemble the conversion from the selected validated records. Conversion never generates approval, fills unknown dates, or silently selects the latest correction.

### 4. Assess explicit v2 claims and verify frozen artifacts

```sh
python -I -m stalecheck verify --input /absolute/path/stalecheck/fixtures/v2-cli/input-before.json --output fd-before
python -I -m stalecheck verify --input /absolute/path/stalecheck/fixtures/v2-cli/input-after.json --output fd-after
python -I -O -m stalecheck verify-artifacts fd-before
python -I -O -m stalecheck verify-artifacts fd-after
```

These inputs already embed the full converted evidence, not the path to `fd-reviewed-evidence.json`. For a new input provide exactly schema `2.0`, format `reviewed_lifecycle_claims`, answer_text, nullable answer_date, verification_date, scope, claims, and reviewed_evidence. Each of one to five explicit public claims has id/text/subject/assertion/scope/public and optional date-only `as_of`. There is no automatic claim extraction from acquired content. `public: true` is an attestation, not a secret detector; input and reviewed acquisitions are retained in artifacts.

The original v1 `verify` formats remain available: `answer` extracts only whole-line `SUBJECT is supported in SCOPE.` or `As of YYYY-MM-DD, SUBJECT is supported in SCOPE.` patterns, while `public_lifecycle_claims` accepts explicit annotations. Unsupported answer prose abstains; unsupported structured predicates reject. V2 accepts only the explicit reviewed format and never falls back to v1.

### 5. Compare and verify the comparison

```sh
python -I -m stalecheck compare --before fd-before --after fd-after --output fd-comparison.json
python -I -m stalecheck verify-comparison --before fd-before --after fd-after --input fd-comparison.json
```

Both runs are independently verified. Matching uses subject/assertion/scope/original as-of, not display IDs. Reusing an ID cannot hide a semantic change; duplicate semantic identities are rejected. Read matched/added/removed rows, before/after verdicts, snapshot-level `causes`, and `time_only`. Multiple change categories can apply. Clock-only attribution requires every other frozen input/config unchanged apart from display claim IDs; it is not real-world causal proof. Keep both full runs to reverify the comparison.

### 6. Export for inspection

```sh
python -I -m stalecheck export-viewer fd-before fd-after --output fd-viewer.json
```

Select 1–20 unique own runs. The final envelope is at most 5 MiB (5,242,880 bytes), checked before opening a new file; no truncation. Store exports outside frozen runs to avoid adding an unregistered file. Manifest/viewer envelope stays `1.0`; report carries v2. Comparison remains a separate file, not a viewer field. The viewer imports it through its own **Import comparison** control: it validates the structure, renders the declared attribution and per-claim changes, and, when both runs are loaded, cross-checks declared digests (input, config, report) and verdicts. It cannot check `manifest_sha256`, recompute the attribution, or establish clock-only change, so loading two runs does not verify their comparison. Use the comparison CLI and both original runs. The viewer omits frozen input bytes and is not a replacement for full artifact verification.

## End states and explanations

| Evidence/end state | Coverage |
|---|---|
| `dated` | Start inclusive, end exclusive; requires known start and later end |
| `open_ended` | Known start onward, with no supplied end field in the proposal |
| `unknown` | No active coverage, even if a start is known |
| Missing start | No active coverage |

Publication/retrieval/review clocks never substitute for effective dates. Explicit historical as-of stays historical. `STALE` requires support at answer date and a later active invalidation at a present-tense target, without conflicting coverage. Otherwise negative coverage is `CONTRADICTED_AS_OF`. Positive unconflicted coverage is `SUPPORTED_AS_OF`; missing/conflicting/ineligible coverage is `ABSTAIN`.

For each report claim read `verdict`, `reason`, `freshness`, `citations`, and `explanation.reason_code`. The explanation preserves original as-of separately from target; `prior` describes answer date and `current` describes target date, including historical targets. Support/contradiction/excluded references identify exact statements and review hashes. Exclusion reasons explain unknown dates, ended/not-yet-effective intervals, and field mismatches. Mechanically supported `current.state` cannot override top-level ambiguity, scope, or future-target abstention.

Successful CLI commands return 0 with JSON on stdout; validation/I/O failures return 2 with error JSON on stderr. Argparse usage failures also return 2 but need not be JSON. A completed assessment may contain only abstentions; completion means processing succeeded, not that a claim was proved. Fatal acquisition can carry sanitized `partial_result` on stderr and no successful stdout result.

Syntax and semantics here were checked against the CLI, source driver, contract, and retained transcript. No new tests, builds, installation, CLI smoke, network call, or live compatibility validation was performed for this guide.
