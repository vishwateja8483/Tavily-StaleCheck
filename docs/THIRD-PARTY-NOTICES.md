# StaleCheck third-party notices

## Scope and project licensing

This notice is based on **actual local dependency manifests and license files**. It does not certify comprehensive legal clearance of every transitive component. The project is licensed under the [MIT License](../LICENSE) (`pyproject.toml` declares `license = "MIT"` with `license-files = ["LICENSE"]`); third-party notices below describe additional components bundled into the packaged UI and do not relicense them.

Project paths refer to the repository root. UI paths identify the `web/` directory. The packaged assessment UI in `src/stalecheck/assessment_static/` is a built bundle of React and the application code; redistributing it must keep the notices below.

## Python package and external service boundary

The local `pyproject.toml` declares Python >=3.12, runtime dependencies `[]`, build backend `setuptools>=68`, and optional test dependency `pytest>=8,<10`. Standard-library HTTP/JSON facilities are used; no Tavily SDK or model SDK is a declared runtime dependency. Python itself and any separately distributed build/test environment retain their own licenses; their complete installed license sets were not inspected for this notice. Do not infer their exact versions or licenses from broad version constraints.

Tavily is an optional external service addressed by the separately opted-in Python adapter, not bundled provider software. Its service/content terms and source-material rights are separate from open-source software licenses. Mock responses and quoted captured text do not convey ownership, publisher authenticity or permission to redistribute arbitrary source content. There is no live compatibility or paid-use approval claim here.

The explicit synthetic lifecycle/reviewer fixtures are not official publisher statements or authenticated human review.

## Optional browser application

StaleCheck's Python package does not import UI libraries. The packaged assessment UI is built from `web/package.json` and `web/package-lock.json`. These local installed runtime packages and their LICENSE files were inspected:

| Package | Observed local version | Actual license source |
|---|---|---|
| React | 19.3.1 | `web/node_modules/react/LICENSE` — MIT |
| React DOM | 19.3.1 | `web/node_modules/react-dom/LICENSE` — MIT |
| Scheduler | 0.28.0 | `web/node_modules/scheduler/LICENSE` — MIT |

Observed local versions must not be represented as verified contents of every historical or future bundle. The web app also declares TypeScript, Vite, Vitest, React plugin/types, Testing Library and jsdom as development dependencies. Its lockfile is the exact graph; the runtime notices below are not an exhaustive inventory of all build/test/transitive packages or a conclusion about what an emitted bundle contains. Preserve additional applicable notices for all components actually redistributed, including any shipped development environment.

### MIT notice — React, React DOM and Scheduler

Their inspected license files contain the same text:

```text
MIT License

Copyright (c) Meta Platforms, Inc. and affiliates.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The packaged assessment UI bundles React and React DOM; the other packages above are build and test tooling and are not part of the built files.
