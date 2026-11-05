# Live check: a real Bifrost gateway, a real model and the real Tavily API

`run.sh` runs the stale-answer journey for real. It starts a Bifrost 2.4.0 gateway in Docker with a virtual key that it generates itself, sends every model call to a small language model that [Ollama](https://ollama.com) serves on this machine, and checks the sample answer twice: once with StaleCheck calling Tavily directly and once with the searches relayed by Bifrost. No account, hosted-model key or token has to be obtained for the gateway; its admin setup token is random per run and only the script uses it. The only paid service is Tavily, about three credits per search mode. `evaluate.py` runs the same stack over 26 labelled answers (see Evaluation below).

Needs Docker on Linux or WSL2 (the gateway uses host networking), Python 3.12+, `curl`, a Tavily key in `TAVILY_API_KEY`, and an Ollama server on `127.0.0.1:11434` that has the model.

```bash
# once: a loopback-only Ollama and a model. The context length matters: the default silently cuts long prompts.
docker run -d --name ollama --network host -v ollama:/root/.ollama \
  -e OLLAMA_CONTEXT_LENGTH=8192 -e OLLAMA_KEEP_ALIVE=60m ollama/ollama
docker exec ollama ollama pull qwen2.5:3b

TAVILY_API_KEY=... scripts/live-check/run.sh
```

| Variable | Default | |
|---|---|---|
| `LIVE_MODEL` | `qwen2.5:3b` | Ollama model; the Bifrost model name is `ollama/<LIVE_MODEL>` |
| `MODES` | `direct relay` | `direct` calls Tavily from StaleCheck, `relay` goes through the gateway's `tavily` relay; `MODES=` (empty) only starts the gateway, which with `KEEP_GATEWAY=1` is the way to bring the stack back after a restart (run `docker start ollama` first) |
| `MAX_CLAIMS` | `3` | claims checked per journey |
| `CALL_TIMEOUT` | `60` | seconds allowed for each model call (10-180); the gateway's provider timeout is set 5 s lower so that it answers before the client gives up |
| `PRIMARY_DOMAIN` | `python.org` | the reviewed domain for the sample answer in `answer.txt` |
| `KEEP_GATEWAY` | unset | `1` leaves the gateway running and writes `virtual-key.txt` and `setup-token.txt` (mode 600) to the output directory |
| `OUT_DIR` | `runs/live-check-<time>` | per mode: `session/` (checkpoints and the verified `run/`), `report.html`, `<mode>-summary.json` |
| `OLLAMA_URL`, `BIFROST_IMAGE`, `GATEWAY_PORT` | `http://127.0.0.1:11434`, `maximhq/bifrost:v2.4.0`, `8080` | |

The script types `APPROVE` for the operator, but only after StaleCheck has printed the exact queries and domains. The summary lists each claim's verdict, the token and credit usage, the observed routing and `files_containing_a_key` (it must be 0). With `KEEP_GATEWAY=1` the browser app can use the same stack:

```bash
export BIFROST_URL=http://127.0.0.1:8080 BIFROST_MODEL=ollama/qwen2.5:3b BIFROST_VIRTUAL_KEY="$(cat runs/live-check-*/virtual-key.txt)"
export BIFROST_SEARCH_RELAY=tavily        # or TAVILY_API_KEY=... to search directly
python -m stalecheck serve-assessment --live --output runs/assessment-live
node scripts/check-assessment-live.mjs <playwright index.mjs> python new-output-dir scripts/live-check/answer.txt python.org   # optional browser check
```

## Notes

- **Behind a TLS-inspecting proxy** the script mounts the host's CA bundle into the gateway so that it can reach `api.tavily.com`. Ollama's registry may reset its parallel blob downloads (`EOF`); then fetch the model with one curl stream from Hugging Face and import it:

  ```bash
  curl -L -C - -o qwen2.5-3b-instruct-q4_k_m.gguf https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
  printf 'FROM /models/qwen2.5-3b-instruct-q4_k_m.gguf\nPARAMETER num_ctx 8192\nPARAMETER temperature 0\n' > Modelfile
  # run the Ollama container with -v "$PWD:/models:ro", then:
  docker exec ollama ollama create qwen2.5:3b -f /models/Modelfile
  ```

- **Speed.** A model call is limited to 60 s and a whole journey to 180 s. `qwen2.5:3b` on four CPU cores finishes three claims in about 80 s on an idle machine. If the machine is busy, Bifrost can answer 504 after its 55 s provider timeout; StaleCheck then stops with `gateway_failed` and keeps a verifiable partial report. Lower `MAX_CLAIMS`, use a faster machine or a hosted model, or raise the per-call limit with `CALL_TIMEOUT` (for example `CALL_TIMEOUT=150`; the journey limit of 180 s still applies). The browser app keeps the 60 s default per call.
- **Small models and the reply contract.** `qwen2.5:1.5b` answers the extraction prompt with a bare JSON array and quotes that are not in the answer; StaleCheck rejects the reply and searches nothing. This is the intended fail-closed behaviour, not a bug of the model setup. Two other habits of `qwen2.5:3b` are tolerated rather than rejected: copying the category list as a claim's category (shown as `other`) and citing a source by its hashed prompt delimiter instead of its id (resolved to that source).
- **A hosted provider** needs no change to StaleCheck: replace the `ollama` block of `config.json` with your provider (see `docs/BIFROST-GATEWAY.md`), start the gateway yourself and run `python scripts/live-check/live_journey.py --gateway URL --model provider/model --answer scripts/live-check/answer.txt --primary-domain python.org --approve --output runs/my-live` with `BIFROST_VIRTUAL_KEY` (and `TAVILY_API_KEY`, or `--search-relay tavily`) in the environment and `PYTHONPATH=src`.
- The sample answer and its verdicts are only a smoke test of the pipeline. They are not an evaluation of any model.

## Evaluation

`dataset.json` holds 26 one-sentence answers about facts that change (versions and support windows of Python, Node.js, Kubernetes, Go and Ubuntu, and Tavily's pricing), each with a STALE or CURRENT label, the authoritative page it comes from and the line of that page that decides it. The labels were written by an AI agent and have not been reviewed by a human. `evaluate.py` runs one real journey per answer against the gateway that `run.sh` left running and scores the verdicts with and without the host's quotation checks:

```bash
export BIFROST_VIRTUAL_KEY="$(cat runs/live-check-*/virtual-key.txt)" PYTHONPATH=src
python3 scripts/live-check/evaluate.py --model ollama/qwen2.5:3b --search-relay tavily --gateway-timeout 150 --output runs/live-evaluation-01
```

It costs about one Tavily credit per answer that yields a claim (the gateway must have been started with `CALL_TIMEOUT=150` for this model on a slow CPU). `--only ID,ID` runs a subset; `--resume` re-derives the results of a finished directory from its retained reports. The method, the results of 2026-11-03 and the limits are in [docs/LIVE-EVALUATION.md](../../docs/LIVE-EVALUATION.md); the scoring and the dataset checks are unit-tested in `tests/test_live_evaluation.py`.
