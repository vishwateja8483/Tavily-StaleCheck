# Real Copilot + Tavily setup

This is the real-provider path for StaleCheck: GitHub Copilot supplies model responses through Bifrost, and Tavily supplies live search results through Bifrost's relay. The application still requires consent to process the answer and separate approval of the exact queries before Tavily is called.

## Requirements

- Docker Desktop running.
- A GitHub account with an active Copilot plan or assigned Copilot seat.
- A fine-grained GitHub personal access token with the **Copilot Requests** account permission, or a token created by Bifrost's **Sign in with GitHub** flow.
- A Tavily API key.
- Python 3.12+ and the StaleCheck package installed.

For an organization-assigned Copilot seat, the organization's Copilot policy must permit Copilot CLI usage. GitHub Copilot usage is billed against the seat's Copilot allowance; Bifrost currently may report `$0` for those requests, so do not treat its cost display as a billing estimate. Bifrost's Copilot provider also relies on Copilot token exchange behavior that GitHub does not publish as a stable API contract.

## Configure credentials without committing them

The runner reads secrets only from the current shell environment. Do not put them in the browser, source files, command-line arguments, or chat. Run these commands in Bash (on macOS, start `bash` first if your interactive shell is zsh):

```bash
read -rs -p "GitHub Copilot token: " GITHUB_COPILOT_API_KEY; echo
export GITHUB_COPILOT_API_KEY
read -rs -p "Tavily API key: " TAVILY_API_KEY; echo
export TAVILY_API_KEY
```

The GitHub CLI token returned by `gh auth token` is not guaranteed to be accepted by Copilot's API. The tested token in this environment was rejected, so use a token with the required permission or Bifrost's GitHub sign-in flow instead.

## Run the live browser demo

From the repository root:

```bash
bash scripts/copilot-check/run.sh
```

The script pulls Bifrost v2.5.1, starts it bound to loopback, discovers a model available to the authenticated Copilot seat, and sends one small real model request as a smoke test. It then starts StaleCheck in live mode and prints a private launch URL. Keep the terminal open, open that URL, enter an answer, and follow the two consent steps. The first request uses Copilot; no Tavily search occurs until you approve the exact query/domain request. A real assessment can use paid Copilot allowance and Tavily credits.

Choose another model available on your account with `LIVE_MODEL=github-copilot/<model>`. To use another local port, set `GATEWAY_PORT`; to change the Bifrost image, set `BIFROST_IMAGE`. The script removes its temporary Bifrost container and credentials when the assessment server exits.

## Current validation status

The StaleCheck offline and mocked-provider suites pass, and the real browser mock flow was exercised. In this environment, Bifrost v2.5.1 was able to start, but the GitHub CLI's existing token was rejected by the Copilot provider with HTTP 403. The Copilot/Tavily end-to-end path is therefore **not yet verified here**. A valid Copilot Requests token (or Bifrost GitHub sign-in) and a Tavily key are required before the live runner can be completed.

The older `scripts/live-check/` evaluation documents a historical run using a local Ollama model. It is retained as historical evidence, not used by this Copilot setup.
