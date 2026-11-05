# v1, acquisition and input-format reference

Reference for the offline lifecycle-verification workflow (v1/v2), the mock and opt-in Tavily acquisition adapter and the structured input formats. The stale-answer journey that uses Bifrost and Tavily (v3/v4) is described in the [repository README](../README.md) and in [V4-CONTRACT.md](V4-CONTRACT.md). Commands below run from the repository root.

## Independent offline v2 workflow

See the [documentation index and tested standalone v2 quickstart](README.md) and [frozen domain contract](V2-CONTRACT.md). Explicit review-to-evidence conversion, unknown-end temporal explanations, independently verified run comparison and a product-local 5 MiB bundle export are now implemented. [Actual synthetic CLI output](../fixtures/v2-cli/cli-transcript.json) and the [exported bundle](../fixtures/v2-cli/viewer-bundle.json) are retained. Earlier v1/review-foundation limitations below describe those retained paths; v2 is opt-in by exact schema/format, never a fallback. Distribution metadata is now 0.4.0 for the stale-answer increment; retained v2 domain reports/config still use 0.2.0.

## Current v2 capability and evidence status

The [documentation index](README.md) links architecture, user, development, operations and third-party-notice guides. The implemented offline v2 source adds `review-to-evidence`, reviewed lifecycle assessment with explanations, exact artifact rederivation, `compare`, `verify-comparison`, and product-local `export-viewer` (a bounded JSON bundle for external tools). Conversion validates explicitly supplied attestations; it never invents human approval or infers lifecycle dates from acquired prose. V1 behavior below remains supported through strict version dispatch.

The source driver and retained `fixtures/v2-cli/cli-transcript.json` demonstrate invented review data and a supported-to-stale clock-only comparison. They are source evidence, not live acquisition or authenticated review. Distribution metadata is `0.4.0`; retained v2 domain config/report still uses `0.2.0`. See the v4 contract for the stale-answer journey's acceptance boundary.

Loading two runs does not verify a comparison: use `verify-comparison` with both original runs.

## Install and run

From this directory, after installing Python:

```sh
python -m pip install -e ".[test]"
python -m stalecheck doctor
python -m stalecheck demo --output demo-run
python -m stalecheck verify-artifacts demo-run
python -m stalecheck verify --input input.json --output verification-run
python -m pytest
```

`stalecheck` is also installed as a console command. Each output directory **must be new**. CLI emits JSON on stdout, errors as JSON on stderr with exit code 2. Successful commands exit 0. `doctor` reports offline capability only; it does not contact providers or inspect credentials. Installation may download setuptools/pytest if missing; the application never does.

Without installation, set `PYTHONPATH` to this package's absolute `src` directory and use `python -m stalecheck`. This also works with unrelated working directories.

## Separate Tavily acquisition (mock-tested, not live-validated)

Acquisition does **not** invoke the verifier, annotate lifecycle statements, generate queries from findings, or forward evidence from other tools. It accepts only this exact plan shape. The caller must review every literal query and extraction URL as public and non-sensitive; `public_reviewed` is an attestation, **not a secret detector**. Unknown fields (including `evidence`, `answer_text`, or raw finding objects) reject before transport. Do not put private findings into query strings or URL paths.

```json
{
  "schema_version": "1.0",
  "format": "reviewed_public_acquisition",
  "public_reviewed": true,
  "queries": ["Python support lifecycle"],
  "urls": ["https://docs.python.org/3/whatsnew/3.12.html"],
  "primary_domains": ["python.org"]
}
```

Search is restricted to reviewed primary domains, and the adapter independently checks returned hosts. Domain labels mean **caller preference**, not publisher authentication or truth. Explicit reviewed extract URLs may include secondary sources, but primary-domain candidates sort first. Search hits are **never automatically extracted**: review desired hits, then submit a separate URL-only plan (`queries: []`). No recency filter silently discards historical sources.

### Offline mock CLI

Save the plan above as `plan.json`. Save this **invented** HTTP response array as `responses.json` (these are test strings, not claims about Python):

```json
[
  {"path": "/search", "status": 200, "body": {
    "results": [{"url": "https://docs.python.org/3/whatsnew/3.12.html", "title": "Synthetic mock", "content": "Invented search snippet; not lifecycle evidence.", "score": 0.9}],
    "request_id": "mock-search", "usage": {"credits": 1}
  }},
  {"path": "/extract", "status": 200, "body": {
    "results": [],
    "failed_results": [{"url": "https://docs.python.org/3/whatsnew/3.12.html", "error": "Synthetic extraction failure"}]
  }}
]
```

```sh
python -m stalecheck acquire-mock --input plan.json --responses responses.json
```

This prints an `unreviewed_acquisition` JSON object, not a verification run; do not send it to `verify-artifacts` as a report. Exit 0 means the command processed the mock contract, **not** that acquisition was complete: inspect `status` (`acquired`, `partial`, `failed`), `failures`, and `review_required`. Invalid plans, oversized/malformed responses, transport/HTTP errors and exhausted budgets fail closed with exit 2. A fatal error raises `AcquisitionError` with a bounded `partial_result` containing normalized earlier candidates, attempted requests, known reported usage, and unknown usage (`null`) for lost/failed responses. The CLI retains exit 2 and emits this partial state on stderr inside its error JSON; stdout is empty. Partial state is not successful verification. It excludes credentials and raw provider error bodies; earlier/failed live attempts might still have incurred charges.

### Opt-in Python surface

`stalecheck.acquisition.acquire(plan, *, allow_network=False, api_key=None, transport=None)` accepts an injectable transport. `MockTransport(response_array)` needs no token or opt-in; every other transport is treated as potentially live. The default `StdlibTransport` only posts to `https://api.tavily.com/search` or `/extract`, with `Authorization: Bearer <explicit-token>`; it does not follow redirects, use environment proxies, or look up credentials in files/environment. A future authorized caller can pass a token directly from its own secret handling and `allow_network=True`. `stalecheck.gateway.BifrostRelayTransport(gateway_url, relay)` is a drop-in alternative transport that posts the same search to `<gateway>/relay/<relay>/search` with a Bifrost virtual key as `api_key`; it keeps the loopback-or-HTTPS origin rule of the gateway client and returns refusals by status alone. Standalone acquisition still has no live CLI; the separate v4 stale-check journey above does, reading only explicitly named environment variables rather than token command-line values. **Do not execute live requests without separate paid-call approval.** The unit tests use no live calls or credential reads; one authorized live smoke test is recorded under "Actual contract sources" below.

The narrow URL policy allows ASCII HTTPS DNS-host URLs on port 443 only, with no userinfo, query, fragment, percent escapes, IP literals, numeric TLD, control characters, or common local/reserved suffixes. A provider-chosen search hit with an otherwise safe HTTPS URL containing a query, fragment or percent escape is skipped with a URL-free `unsupported_result_url` failure rather than aborting the entire search; explicit extract URLs still reject before transport. It does not resolve DNS or prove a hostname/path is public. Tavily's remote fetch/redirect behavior and public accessibility remain upstream responsibilities plus caller review; the local adapter never fetches a returned source URL.

### Bounds and failure semantics

- At most **3 queries**, **300 characters/query**, **5 explicit URLs**, **10 primary domains**; duplicates reject. Search requires primary domains. No arbitrary provider options or attached private context are forwarded.
- Search explicitly uses `basic`, `general`, `max_results: 5`, restrictive domain filtering, no auto-parameters, generated answer, raw content, or images; it requests publication metadata and usage. Extract uses `basic`, `format: text`, and provider timeout **10 seconds**.
- At most **16,384 request bytes**, **512,000 response bytes/request**, **100,000 content characters/candidate**, and **1,000 title characters**. Oversized data rejects rather than silently truncating. Stdlib reads bounded chunks with identity encoding; compressed responses reject. JSON duplicate keys/nonfinite numbers reject.
- **15-second socket/attempt budget**, **60-second overall deadline**, at most **2 attempts/request**, therefore at most **8 attempts/plan**. Only explicit HTTP 429/502/503/504 retry, once after 0.25 seconds; no `Retry-After` sleeping, redirect following, retry of failed extract rows, or retry of ambiguous I/O failures. **DNS resolution is OS-managed and can outlast a socket timeout**; this is not a hard process-level wall-clock guarantee. Custom transports must honor supplied byte/time limits; the adapter rechecks bytes and overall deadline on return.
- HTTP 200 extract checks **both** `results` and `failed_results`. All failures remain `failed`, mixed success is `partial`, and omitted requested URLs become `missing_result`. Duplicate, unrequested, or malformed extract rows reject. Empty searches and out-of-domain hits become explicit incomplete outcomes. HTTP failures/transport exceptions are sanitized, never echoing tokens or provider error bodies.
- Per-request IDs and reported credits are retained when present; absent usage stays unknown. No dollar cost is estimated. Mock fixture credits are marked as mock metadata, **not measured charges**; actual retrieval calls remain zero in mock results. Search relevance is never treated as a truth/confidence score.

### Mandatory review boundary and clocks

Candidates retain content, its SHA-256, URL, retrieval timestamp, and separately the raw provider date plus a parsed date/timestamp when recognizable. Tavily's `published_date` is its **estimate of publication or last update**, not an authenticated publisher timestamp or an effective date. The retained `published_at`/`published_at_raw` field names do not strengthen that guarantee. Unknown/unparseable dates remain null; extract usually has no publication metadata. `effective_from` and `effective_to` stay null, `statements` stays empty, and `review_required` stays true. Retrieval freshness, publication dates, high relevance, primary-domain preference, and injected instructions inside scraped text cannot create a verdict or effective interval. Render all provider text as text, never instructions/HTML.

Candidates deliberately use provenance `tavily_acquired_unreviewed` (or `tavily_mock`), which the existing verifier **rejects**. After independent human review, a caller may construct a **separate** `user_supplied_frozen` evidence item with exact captured content/hash/quote, appropriately reviewed publication date, and `user_reviewed` statements with explicit subject/scope/value/effective intervals. For v1 this remains a separate manually prepared evidence boundary. The current v2 `review-to-evidence` converter instead validates explicitly supplied review records; it does not infer approvals or act as a model adjudicator. Exact quote membership still does not prove semantic entailment. The original offline verifier and its artifacts remain unchanged.

### Offline review records

The separate commands below retain **explicit caller-supplied human attestations**, not temporal verdicts:

```sh
python -m stalecheck review-evidence --acquisition acquisition.json --proposal proposal.json --output new-review.json
python -m stalecheck verify-review --input new-review.json --acquisition acquisition.json
```

The output file must not exist; its parent must exist. Review proposals require `candidate_id`, `acquisition_sha256`, `candidate_sha256`, `content_sha256`, `quote`, `quote_start_byte`, `quote_end_byte`, `subject`, `assertion` (`is_supported`), `scope`, boolean `value`, nullable `effective_from`, `end_state`, nonblank `reviewer`, `attested: true`, RFC3339 `reviewed_at`, and `rationale`. Offsets refer to exact UTF-8 bytes. `end_state` is `dated`, `open_ended`, or `unknown`; only `dated` includes `effective_to`. Unknown is never converted to open-ended applicability.

Use `stalecheck.review.acquisition_digest()` and `candidate_digest()` for canonical snapshot identities. The output embeds the original acquisition unchanged and binds both text and candidate metadata, including URL. `make_review()` and `validate_review()` provide the same offline Python boundary. No timestamp, identity, approval or lifecycle date is invented by these functions.

Corrections include `previous_review_sha256` in the proposal and require `--previous-review` on both commands. Only the immediate supplied previous record and its intrinsic consistency are checked—not a complete authenticated history. Corrections do not automatically supersede temporal facts. File creation is exclusive, not tamper-proof storage.

Review records remain separate from v1 verification inputs and cannot by themselves be bundled as verification runs. Historically this review-record milestone did not include conversion or v2 assessment; the current explicit v2 workflow now supplies both through `review-to-evidence` and `verify`. Authenticated reviewer identity, semantic entailment checks and source authentication remain unimplemented. Existing v1 null-end semantics and report derivation are preserved through explicit version dispatch; unknown input/configuration versions fail closed.

### Actual contract sources and unverified gate

Reviewed **2026-11-01**, using Postman API-engineering/discovery guidance, and these provider-owned references:

- [Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search): POST `/search`, Bearer auth, `query`, basic/general settings, domain restrictions, opt-out answer/raw content/auto-parameters, publication metadata (`published_date`, including RFC2822 examples), result `title/url/content/score`, `request_id`, optional usage credits. Provider max results is 20; this adapter limits to 5.
- [Tavily Extract API](https://docs.tavily.com/documentation/api-reference/endpoint/extract): POST `/extract`, Bearer auth, URL list (provider max 20; adapter max 5), `extract_depth`, `format`, timeout range 1–60 seconds, `results[].raw_content`, `failed_results[].url/error`, optional usage and request ID. HTTP 200 can represent partial or all-URL extraction failure.

These contracts were written from the documentation and exercised with mocks. On 2026-11-02, with the owner's authorization, a budgeted live smoke test then sent four `/search` calls (two direct, two through a Bifrost relay, one credit each) and one call with a wrong key: the request body, Bearer auth, response fields, RFC 2822 `published_date`, `usage.credits`, `include_domains` filtering and the 401 status all matched this adapter. `/extract` and Tavily's partial-failure behavior were not exercised. No Postman workspace publication, external mock service or authenticated discovery was used. Mock tests do not establish search effectiveness, upstream redirects/DNS policy, source truth, or production wall-clock performance.

## Input A: original answer text

```json
{
  "schema_version": "1.0",
  "format": "answer",
  "answer_text": "DemoRuntime 1 is supported in community.\nAs of 2024-06-01, DemoRuntime 1 is supported in community.",
  "answer_date": "2024-06-01",
  "verification_date": "2025-06-01",
  "scope": "community",
  "evidence": []
}
```

`format` defaults to `answer`. Preserve `answer_text` exactly; each nonempty line is one claim slot. Only these case-sensitive **whole-line** patterns are extracted:

- `SUBJECT is supported in SCOPE.`
- `As of YYYY-MM-DD, SUBJECT is supported in SCOPE.`

Subject/scope accept ASCII letters, numbers, spaces, periods, underscores, slashes, and hyphens (up to 100 characters). Unsupported prose produces an `ABSTAIN` record with original wording, not fabricated extraction. Multiple assertions on one line are not partially extracted. Relative dates and interval wording abstain. Split prose into one claim per line before submitting; the CLI cannot count semantic claims hidden inside unsupported prose. More than five nonempty lines rejects the entire request, never silently truncates. Empty answers reject.

## Input B: reviewed public lifecycle annotations

For CLI integration, use the **separate** structured ingestion format. No prose inference is performed. A human/caller must review that `subject`, `assertion`, `scope`, and `as_of` faithfully represent the supplied text.

```json
{
  "schema_version": "1.0",
  "format": "public_lifecycle_claims",
  "answer_date": "2024-06-01",
  "verification_date": "2025-06-01",
  "scope": "community",
  "claims": [
    {
      "id": "finding1-lifecycle",
      "text": "DemoRuntime 1 is supported in community.",
      "subject": "DemoRuntime 1",
      "assertion": "is_supported",
      "scope": "community",
      "public": true
    }
  ],
  "evidence": []
}
```

Optional `answer_text` is preserved; otherwise it is the newline-joined original claim texts. Optional claim `as_of` is an explicit historical date. Claim IDs are caller-controlled unique ASCII letters/numbers/underscore/hyphen, up to 100 characters. One to five claims only. `public: true` is a caller attestation, not a secret detector. Only `is_supported` is implemented; version/latest-version, security exploitability, remediation effectiveness, free-form predicates, and future predictions are out of scope. Unsupported structured predicates reject, while unparseable original answer lines abstain. Empty frozen evidence is valid and yields abstention. Do not submit secrets: original input is included in artifacts.

The tool conservatively abstains for dates in text that disagree with/miss `as_of`, historical words without point annotations, or interval/relative wording. It is not a general language understanding system: reviewed structured annotations can still misrepresent text, and the caller remains responsible for their meaning.

## Frozen evidence schema

Both inputs take the same `evidence` array (maximum 100 sources). Example source, with the digest replaced by the actual lowercase SHA-256 of the exact UTF-8 `content`:

```json
{
  "id": "ev-1",
  "title": "User-captured lifecycle document",
  "content": "DemoRuntime 1 support ends on 2025-01-01.",
  "url": "https://fixtures.example.invalid/lifecycle/1",
  "retrieved_at": "2025-06-01T00:00:00Z",
  "published_at": null,
  "provenance": "user_supplied_frozen",
  "content_sha256": "REPLACE_WITH_ACTUAL_SHA256",
  "statements": [
    {
      "id": "support-ended",
      "subject": "DemoRuntime 1",
      "assertion": "is_supported",
      "scope": "community",
      "value": false,
      "quote": "DemoRuntime 1 support ends on 2025-01-01.",
      "effective_from": "2025-01-01",
      "effective_to": null,
      "annotation_provenance": "user_reviewed"
    }
  ]
}
```

A complete runnable specimen with correct digests is `src/stalecheck/fixtures/demo.json`. Its provenance and annotation provenance are `synthetic_fixture`. Non-synthetic frozen evidence uses `user_supplied_frozen` and `user_reviewed`. A quote must occur **exactly** in captured content. The software checks quotation membership, not that natural-language content entails the structured annotation. Statement IDs are unique per evidence source and citations identify both source and statement. Sources with no statements or missing effective start remain incomplete and cannot independently support a verdict.

Evidence URLs are provenance labels only, never fetched: HTTPS hostname, no credentials, IP literals, local/internal suffixes, nonstandard ports, control characters or backslashes. No DNS/authenticity checks occur. Reserved `example.invalid` names deliberately identify synthetic fixtures. Render content and quotes as text, never HTML or instructions.

## Temporal semantics

Four clocks are preserved separately:

1. `answer_date`: date original present-tense answer was given; optional, never guessed.
2. `as_of` / statement `effective_from`, `effective_to`: claim target and applicability intervals.
3. `published_at`: publisher date, optional; no validity inference.
4. `retrieved_at`: timezone-aware capture timestamp; no validity inference.

Date-only fields are strict Gregorian `YYYY-MM-DD`; timestamps are RFC3339 with timezone. Each statement covers **[effective_from, effective_to)**: start inclusive, end exclusive. A null/omitted end means an **explicitly annotated open-ended interval**, not a fresh-fetch inference. Missing start means unknown. Dates have day-level precision; do not use them for intra-day transitions. Subject and scope are exact, case-sensitive; editions are never generalized. Request scope mismatch abstains. `answer_date` cannot be after `verification_date`; future claim dates abstain.

- `SUPPORTED_AS_OF`: unconflicted true evidence covers the exact target date.
- `STALE`: a present-tense claim has unconflicted true evidence at `answer_date` **and** false evidence covering verification date that became effective after the answer. Missing or conflicting prior applicability cannot be stale.
- `CONTRADICTED_AS_OF`: unconflicted false evidence at target date, without established stale transition.
- `ABSTAIN`: missing date coverage, conflicting evidence, unsupported extraction, ambiguous temporal wording, future date or wrong scope.

Explicit historical claims always target their historical date. A later lifecycle change does not make a valid historical statement stale. A historical contradiction remains contradiction. A current page can carry explicit historic applicability annotations, but a current retrieval alone does not establish historic validity or what was known at the time. No “latest source wins” rule.

Ordinal `freshness` is `current evidence-supported`, `historical-only`, `changed/contradicted`, or `unknown`. It is not a numeric score, factual probability, calibrated confidence, or a model/retrieval benchmark.

## Artifacts and verification

New runs contain:

- `manifest.json`: v1 shared envelope, mode `replay`, run ID, status, timestamp, limitations, registered SHA-256/byte lengths.
- `input.json`: frozen original input, canonical JSON serialization; `input_sha256` hashes this serialization, not the original input file's formatting.
- `config.json`: versioned deterministic policy configuration; its hash is in the report.
- `report.json`: common project/run fields, original answer/dates/scope, limitations, warnings, `claims`, `evidence`, explicit zero model/retrieval calls and unknown provider usage (`null`).
- `events.jsonl`: ordered start/verification/completion trace.
- `errors.jsonl`: empty for completed offline runs.

Every result claim includes `id`, `text`, `verdict`, `reason`, `evidence_ids`, `as_of`, original structured fields, freshness, and exact dated citations. Evidence retains `id`, `title`, `content`, `url`, capture/publication dates, content digest, provenance, and dated statements.

`verify-artifacts DIR` validates envelope fields, supported version/mode/config, bounded bytes, unique safe case-insensitive artifact paths, required files, hashes, unexpected files, sequence/run IDs, chronological events and completion. It rejects symlinks/reparse points, unsafe paths, duplicate JSON keys, duplicated domain IDs, dangling references and altered dates/quotes/verdicts. It compares the report against **pure deterministic replay of the captured input**; no source retrieval, model run, or external action occurs. No `assert` checks are used in runtime validation, including under `python -O`. This implementation accepts only completed replay artifacts generated by this supported version, not arbitrary future domain schemas or incomplete runs.

Artifacts are bounded to 2 MB each, 12 MB total, 100 registered files, and 300 filesystem entries. Output path components are conservative ASCII portable file names. Writers refuse overwrite and leave an `incomplete` marker if interrupted after directory creation; there is no automatic resume. A hard crash can leave malformed/incomplete output, which verification rejects. Concurrent modification of a directory during verification is unsupported. Hashes and an unsigned manifest detect inconsistency, **not source authenticity or coordinated forgery**. Package/config version hashes are not signed code attestation.

## Tests and limitations

Tests cover temporal transitions, historic support/contradiction, half-open boundaries, conflicting/missing evidence, ambiguous dates, narrow extraction, prompt-injection text, input bounds, provenance, exact quotations, unsafe references, artifact tampering, missing completion, offline execution, and optimized-Python rejection. OS symlink creation tests skip when Windows privilege is unavailable; runtime checks remain in place. These are implementation tests, not evaluation of extraction, retrieval quality, model autonomy, or factual accuracy against public software truth.
