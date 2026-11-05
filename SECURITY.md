# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub's **Report a vulnerability** form (Security tab) rather than a public issue. Include the affected command or file, what you observed, and the smallest input that reproduces it. Expect an acknowledgement, not a service-level guarantee: this is a research prototype.

## What this repository does and does not do

- Default commands are offline replays. They make no network calls and need no credentials.
- Outbound requests happen only on the explicit live paths, `stalecheck stale-check --live --confirm-outbound` and `stalecheck serve-assessment --live`. They send the full answer to your Bifrost gateway after upload consent, and only the approved queries to Tavily (directly, or through your gateway's relay).
- The live path has been run against a real Bifrost 2.4.0 container with a local open-weights model and the real Tavily API ([docs/LIVE-EVALUATION.md](docs/LIVE-EVALUATION.md)). No hosted model provider has been called.
- Credentials are referenced by **environment variable name** (`--gateway-token-env`, `--tavily-key-env`), never by value, and are not written to artifacts or logs.
- The local assessment server binds to loopback only and requires a one-time launch token that is held in memory; see [docs/ASSESSMENT-API.md](docs/ASSESSMENT-API.md).
- Model replies and retrieved source text are untrusted data. They are displayed as text and never executed.
- Artifact hashes establish consistency, not authenticity. Nothing here is signed.

## Do not commit secrets

`.env` and `.env.*` are git-ignored; `.env.example` lists variable names only. `scripts/audit-repo-readiness.py` is a read-only, heuristic scan for key material and local paths; it is not a guarantee. If you find a credential in the history, revoke it first, then rewrite the history.
