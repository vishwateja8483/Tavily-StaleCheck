#!/usr/bin/env bash
# Live check: StaleCheck against a real Bifrost gateway container, a real local language model served by Ollama
# and the real Tavily API. Needs Docker on Linux or WSL2 (the container uses host networking), Python 3.12+, an
# Ollama server with the model already pulled, and TAVILY_API_KEY in the environment. One run costs about 3 Tavily
# credits per search mode. See docs/BIFROST-GATEWAY.md.
#
#   TAVILY_API_KEY=... scripts/live-check/run.sh               # searches directly, then through the Bifrost relay
#   MODES=relay LIVE_MODEL=qwen2.5:1.5b scripts/live-check/run.sh
#   KEEP_GATEWAY=1 scripts/live-check/run.sh                   # leave the gateway running; prints the settings for the web app
#   OLLAMA_URL=http://127.0.0.1:11434 BIFROST_IMAGE=maximhq/bifrost:v2.4.0 GATEWAY_PORT=8080 are the defaults
#   CALL_TIMEOUT=150 scripts/live-check/run.sh               # slow CPU-only model: seconds per model call (default 60); the gateway's
#                                                            # provider timeout is set 5 s lower so that it answers before the client gives up
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
IMAGE="${BIFROST_IMAGE:-maximhq/bifrost:v2.4.0}"
PORT="${GATEWAY_PORT:-8080}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
LIVE_MODEL="${LIVE_MODEL:-qwen2.5:3b}"
MODES="${MODES-direct relay}"   # an empty MODES only starts the gateway (with KEEP_GATEWAY=1 it stays up)
MAX_CLAIMS="${MAX_CLAIMS:-3}"
CALL_TIMEOUT="${CALL_TIMEOUT:-60}"
[[ "$CALL_TIMEOUT" =~ ^[0-9]+$ ]] && [ "$CALL_TIMEOUT" -ge 10 ] && [ "$CALL_TIMEOUT" -le 180 ] || { echo "CALL_TIMEOUT must be a whole number of seconds from 10 to 180" >&2; exit 2; }
DOMAIN="${PRIMARY_DOMAIN:-python.org}"
OUT="${OUT_DIR:-$ROOT/runs/live-check-$(date +%Y%m%d-%H%M%S)}"
PYTHON_BIN="${PYTHON:-python3}"
NAME="bifrost-live-$$"
WORK="$(mktemp -d)"

cleanup() {
  # A gateway left running keeps its SQLite files in $WORK, so keep that directory with it.
  if [ "${KEEP_GATEWAY:-0}" = "1" ]; then return; fi
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

for tool in docker curl "$PYTHON_BIN"; do command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required" >&2; exit 2; }; done
: "${TAVILY_API_KEY:?set TAVILY_API_KEY; it is handed to the gateway container only (and to the direct-search run)}"
curl -fsS -m 10 "$OLLAMA_URL/api/tags" | "$PYTHON_BIN" -c '
import json, sys
wanted = sys.argv[1]
names = [m["name"] for m in json.load(sys.stdin).get("models", [])]
sys.exit(0 if wanted in names or wanted + ":latest" in names else "model " + wanted + " is not pulled in Ollama; have: " + ", ".join(names))' "$LIVE_MODEL"

export BIFROST_VIRTUAL_KEY="${BIFROST_VIRTUAL_KEY:-sk-bf-$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(16))')}"
SETUP_TOKEN="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(16))')"   # only protects the gateway's /api routes
mkdir -p "$WORK/data" "$OUT"
sed "s/\"default_request_timeout_in_seconds\": 55/\"default_request_timeout_in_seconds\": $((CALL_TIMEOUT - 5))/" "$HERE/config.json" >"$WORK/data/config.json"
grep -q "\"default_request_timeout_in_seconds\": $((CALL_TIMEOUT - 5))," "$WORK/data/config.json" || { echo "could not set the provider timeout in the gateway config" >&2; exit 2; }
chmod -R a+rwX "$WORK/data"
CA=()
# Behind a TLS-inspecting proxy the gateway must trust the host's CA bundle to reach api.tavily.com.
[ -f /etc/ssl/certs/ca-certificates.crt ] && CA=(-v /etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/ca-certificates.crt:ro)

docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"
docker run -d --name "$NAME" --network host "${CA[@]}" \
  -e APP_HOST=127.0.0.1 -e APP_PORT="$PORT" -e BIFROST_SETUP_TOKEN="$SETUP_TOKEN" \
  -e BIFROST_VIRTUAL_KEY -e TAVILY_API_KEY -e "OLLAMA_URL=$OLLAMA_URL" \
  -v "$WORK/data:/app/data" "$IMAGE" >/dev/null
for _ in $(seq 1 90); do curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break; sleep 1; done
if ! curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "the gateway did not become healthy; last log lines:" >&2
  docker logs --tail 30 "$NAME" >&2 || true
  exit 1
fi
echo "gateway: $(docker inspect --format '{{.Config.Image}}' "$NAME") on 127.0.0.1:$PORT, model ollama/$LIVE_MODEL, output $OUT"

# Load the model into memory first so that loading time does not eat into the journey's per-call timeout.
curl -fsS -m 300 "http://127.0.0.1:$PORT/v1/chat/completions" -H "x-bf-vk: $BIFROST_VIRTUAL_KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"ollama/$LIVE_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the word ready.\"}],\"max_tokens\":8}" \
  | "$PYTHON_BIN" -c 'import json, sys; r = json.load(sys.stdin); print("warm-up reply:", repr(r["choices"][0]["message"]["content"]), "usage:", r.get("usage"))'

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
status=0
for mode in $MODES; do
  extra=()
  [ "$mode" = "relay" ] && extra=(--search-relay tavily)
  "$PYTHON_BIN" "$HERE/live_journey.py" --gateway "http://127.0.0.1:$PORT" --model "ollama/$LIVE_MODEL" --answer "$HERE/answer.txt" \
    --primary-domain "$DOMAIN" --max-claims "$MAX_CLAIMS" --gateway-timeout "$CALL_TIMEOUT" --approve --output "$OUT/$mode" --json "$OUT/$mode-summary.json" "${extra[@]}" || status=$?
done

curl -fsS -m 15 "http://127.0.0.1:$PORT/api/relay-logs/stats" -H "X-Bifrost-Setup-Token: $SETUP_TOKEN" | head -c 600 | sed 's/^/relay log stats: /' || true
echo
if [ "${KEEP_GATEWAY:-0}" = "1" ]; then
  (umask 077; printf '%s\n' "$BIFROST_VIRTUAL_KEY" >"$OUT/virtual-key.txt"; printf '%s\n' "$SETUP_TOKEN" >"$OUT/setup-token.txt")
  echo "gateway left running as $NAME on 127.0.0.1:$PORT (data in $WORK). The virtual key is in $OUT/virtual-key.txt, the dashboard setup token in $OUT/setup-token.txt."
  echo "Settings for 'python -m stalecheck serve-assessment --live': BIFROST_URL=http://127.0.0.1:$PORT BIFROST_MODEL=ollama/$LIVE_MODEL BIFROST_VIRTUAL_KEY=<that key> and TAVILY_API_KEY=<your key> or BIFROST_SEARCH_RELAY=tavily"
  echo "Stop it with: docker rm -f $NAME && rm -rf $WORK"
fi
exit "$status"
