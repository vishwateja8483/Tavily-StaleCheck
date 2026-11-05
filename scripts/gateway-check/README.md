# Gateway check: StaleCheck against a real Bifrost

`run.sh` starts the real Bifrost gateway (`maximhq/bifrost:v2.4.0`, pinned) in Docker, puts local stand-ins behind it, and drives StaleCheck through it: chat, fallback takeover, refusals, the Tavily relay, a virtual-key budget and Relay Logs. It exercises our code against the real gateway's routing, governance and accounting. It does **not** call any real model or Tavily provider and needs no API key or account.

```bash
scripts/gateway-check/run.sh            # Docker on Linux or WSL2, Python 3.12+, curl
KEEP_WORK=1 scripts/gateway-check/run.sh     # keep the stub log and the written reports
GATEWAY_CONFIG=my-config.json scripts/gateway-check/run.sh   # same checks against another gateway config
```

The container uses host networking, so Docker Desktop on Windows or macOS is not enough; use Linux or WSL2 with Docker Engine. The gateway listens on `127.0.0.1:8080` only (set `GATEWAY_PORT` to change it). It also needs ports 9101, 9102, 9201 and 9301 on loopback for the stand-ins.

| File | Role |
|---|---|
| `run.sh` | Starts the stand-ins and the container, waits for `/health`, runs `check.py`, always removes the container |
| `check.py` | The checks (28 at the time of writing); prints `PASS`/`FAIL`, exits 1 on any failure |
| `config.json` | The gateway configuration under test: two model providers, the `tavily` relay, a $0.045 budget, two virtual keys |
| `stub_upstreams.py` | Loopback stand-ins: two OpenAI-compatible models (one can be switched off), a Tavily Search endpoint that only accepts the gateway-held secret, and an empty model catalogue |

Notes on what the stand-ins are:

- The model is rule-based: it answers StaleCheck's two prompts with valid JSON. Nothing here measures the accuracy of a language model.
- The Tavily stand-in has the documented response shape (`results`, `published_date` in RFC 2822 form, `usage.credits`). Whether live Tavily responses match it is established only by the Tavily reference, not by this check.
- Bifrost 2.4.0 refuses to start when it cannot download its model price list. `config.json` therefore points `framework.pricing` at the local catalogue stand-in, which also keeps the check independent of the outside network. Model calls are priced at zero; only relay spend is accounted.
- Every key in these files (`sk-bf-demo-key-...`, `stub-tavily-secret`, `harness-setup-token`) is a made-up value for this throwaway gateway. Real secrets belong in environment variables referenced as `env.NAME`; this config already does that for the two upstream secrets.
- The setup token only protects the throwaway gateway's `/api` routes, which the check reads for Relay Logs and budget counters.

Full description, the observed gateway behaviour and the limits: [docs/BIFROST-GATEWAY.md](../../docs/BIFROST-GATEWAY.md).
