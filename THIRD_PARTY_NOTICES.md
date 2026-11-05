# Third-party notices

## unslop (CC BY 4.0)

The stylesheet passes of the assessment UI follow the method of **unslop** by Damir Sokolovsky ([@heydamir](https://github.com/heydamir/unslop)), licensed under [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/). Source inspected: commit `a6f121eee5f29c2f74976b75456feae8b66afb3a`.

Changes made here: the project's real DOM and stylesheet were the baseline. Any demo video rendered with the upstream `story.mjs` must keep the credit "unslop by @heydamir", which is part of that renderer's output. Recorders and videos are not stored in this repository.

## Services named, not bundled

Maxim Bifrost (LLM gateway) and Tavily (search) are external services used by `stalecheck stale-check --live` and `stalecheck serve-assessment --live`. No client libraries are bundled and no credentials are included; their own terms apply. The live path was run against a real Bifrost 2.4.0 container, a local open-weights model (Qwen2.5-3B-Instruct through Ollama) and the real Tavily API; see [docs/LIVE-EVALUATION.md](docs/LIVE-EVALUATION.md) and [docs/BIFROST-GATEWAY.md](docs/BIFROST-GATEWAY.md).

## Web UI dependencies

The Python package has no runtime dependencies. The assessment UI is built with React (MIT), Vite and Vitest (MIT), TypeScript (Apache-2.0), jsdom and Testing Library (MIT); the exact versions are pinned in `web/package-lock.json`. Each keeps its own license, and component-level notices are in [docs/THIRD-PARTY-NOTICES.md](docs/THIRD-PARTY-NOTICES.md). The stylesheet names the typeface Inter but ships no font files.
