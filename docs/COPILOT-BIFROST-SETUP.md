# Live assessment with GitHub Copilot

StaleCheck uses a signed-in GitHub Copilot CLI for inference. Maxim Bifrost is the API gateway for both model calls and Tavily Search. The launcher issues fresh keys for the two loopback connections. [Bifrost gateway](https://github.com/maximhq/bifrost) · [GitHub Copilot CLI](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)

## Start the workspace

1. Install Python 3.12 or newer and sign in to GitHub Copilot CLI. A Copilot seat must have access to the model. The launcher defaults to `claude-sonnet-5.5`. Pass `--model` to select a different Copilot model explicitly.
2. Set `TAVILY_API_KEY` in your shell or git-ignored project `.env`. [Tavily dashboard](https://app.tavily.com/home)
3. Install Bifrost's native HTTP gateway or have Node.js and `npx` available for the official launcher. If you install the binary yourself, set `BIFROST_BINARY` to its path. [Official Bifrost setup](https://docs.getbifrost.ai/quickstart/gateway/setting-up)
4. Run the launcher from the repository root.

```bash
bash scripts/copilot-check/run.sh --open-ui
```

Open the private URL printed by the launcher. Paste an answer, set the source domains, and consent to answer processing. Review the exact Tavily query and domain list before approving the search.

The bridge accepts authenticated text-only requests from Bifrost and sends the prompt on stdin to the Copilot CLI in an empty working directory. Each assessment uses the selected model. The run has a wall-clock deadline and an attempt limit. The Copilot CLI uses its own account quota and can count additional prompt tokens from its built-in instructions.

## Verify a real run

```bash
# Uses the sample Tavily pricing claim and approves a single Tavily search.
bash scripts/copilot-check/run.sh --verify
```

The command writes the report, run artifacts and `verification.json` under `runs/`. For a different case, pass `--answer-file PATH --domain DOMAIN --max-claims N`. Repeat `--domain` for multiple approved domains. The verification command explicitly approves the generated public searches for the supplied case. Use `--verify-browser /absolute/path/to/playwright/index.mjs` for the same test through the browser. Set `PLAYWRIGHT_CHANNEL=chrome` to use an installed Chrome. The real test uses your Copilot allowance and Tavily credits.

## Tavily MCP in Codex

Codex can connect to Tavily for development research using OAuth. StaleCheck's assessment service uses Tavily Search through Bifrost and applies its exact-query and domain approval. [Tavily MCP setup](https://docs.tavily.com/documentation/mcp)

```bash
codex mcp add tavily --url https://mcp.tavily.com/mcp/
codex mcp login tavily
```
