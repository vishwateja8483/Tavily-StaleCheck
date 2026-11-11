#!/usr/bin/env bash
# Run Bifrost and Ollama in one Docker network namespace. Only Bifrost is published.
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd "$HERE/../.." && pwd -P)"
PYTHON_BIN="${PYTHON:-$ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then PYTHON_BIN="${PYTHON:-python3}"; fi
MODE="${1:---live}"
case "$MODE" in --live|--check-model) ;; *) echo "Usage: $0 [--live | --check-model]" >&2; exit 2;; esac
MODEL="${LIVE_MODEL:-qwen2.5:3b}"
IMAGE="${BIFROST_IMAGE:-maximhq/bifrost:v2.4.0}"
OLLAMA_IMAGE="${OLLAMA_IMAGE:-ollama/ollama:latest}"
PORT="${GATEWAY_PORT:-18081}"
OUT="${OUT_DIR:-$ROOT/runs/local-live-$(date +%Y%m%d-%H%M%S)-$$}"
OLLAMA_NAME="stalecheck-ollama-$$"
GATEWAY_NAME="stalecheck-bifrost-$$"

for tool in docker curl "$PYTHON_BIN"; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required." >&2; exit 2; }
done
docker info --format '{{.ServerVersion}}' >/dev/null || { echo "Start Docker Desktop or Docker Engine first." >&2; exit 2; }
if [ "$MODE" = --live ] && [ -z "${TAVILY_API_KEY:-}" ]; then
  if [ -t 0 ]; then
    read -rs -p "Tavily API key: " TAVILY_API_KEY
    echo
  fi
  : "${TAVILY_API_KEY:?Set TAVILY_API_KEY or enter it at the private terminal prompt.}"
fi

WORK="$(cd "$(mktemp -d)" && pwd -P)"
cleanup() {
  docker rm -f "$GATEWAY_NAME" "$OLLAMA_NAME" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$WORK/data"
cp "$HERE/config.json" "$WORK/data/config.json"
chmod 755 "$WORK"
chmod -R a+rwX "$WORK/data"
export BIFROST_VIRTUAL_KEY="sk-bf-$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(16))')"
export BIFROST_SETUP_TOKEN="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(16))')"
# The relay configuration expects a credential value. Model-only mode never calls the relay.
if [ "$MODE" = --check-model ]; then TAVILY_API_KEY=model-check-only; fi
export TAVILY_API_KEY

docker image inspect "$OLLAMA_IMAGE" >/dev/null 2>&1 || docker pull "$OLLAMA_IMAGE"
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"
docker run --rm -d --name "$OLLAMA_NAME" -p "127.0.0.1:$PORT:8080" \
  -e OLLAMA_HOST=127.0.0.1 -e OLLAMA_CONTEXT_LENGTH=8192 -e OLLAMA_KEEP_ALIVE=30m \
  -v stalecheck-ollama-models:/root/.ollama "$OLLAMA_IMAGE" >/dev/null

for _ in $(seq 1 30); do
  docker exec "$OLLAMA_NAME" ollama list >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$OLLAMA_NAME" ollama list >/dev/null
docker exec "$OLLAMA_NAME" ollama show "$MODEL" >/dev/null 2>&1 || docker exec "$OLLAMA_NAME" ollama pull "$MODEL"

docker run --rm -d --name "$GATEWAY_NAME" --network "container:$OLLAMA_NAME" \
  -e APP_HOST=0.0.0.0 -e APP_PORT=8080 -e BIFROST_SETUP_TOKEN -e BIFROST_VIRTUAL_KEY -e TAVILY_API_KEY \
  -e OLLAMA_URL=http://127.0.0.1:11434 -v "$WORK/data:/app/data" "$IMAGE" >/dev/null
unset TAVILY_API_KEY BIFROST_SETUP_TOKEN
for _ in $(seq 1 45); do
  curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null

export PYTHONPATH="$ROOT/src"
export BIFROST_URL="http://127.0.0.1:$PORT" BIFROST_MODEL="ollama/$MODEL" BIFROST_SEARCH_RELAY=tavily
"$PYTHON_BIN" - <<'PY'
import os
from stalecheck.gateway import GatewayClient, GatewayError

gateway = GatewayClient(os.environ['BIFROST_URL'], os.environ['BIFROST_MODEL'],
                        token=os.environ['BIFROST_VIRTUAL_KEY'], allow_network=True, timeout=60)
try:
    reply = gateway.chat([{'role': 'user', 'content': 'Reply with exactly one word: ready.'}], max_tokens=32)
except GatewayError as error:
    raise SystemExit(f'Bifrost model probe failed: HTTP {error.status}, {error.kind}') from None
if reply['text'].strip().lower().rstrip('.') != 'ready':
    raise SystemExit('The local model returned an unexpected probe response.')
print(f"Bifrost routed a real model response from {os.environ['BIFROST_MODEL']}.")
PY
if [ "$MODE" = --check-model ]; then exit 0; fi
mkdir -p "$(dirname "$OUT")"
"$PYTHON_BIN" -m stalecheck serve-assessment --live --output "$OUT" --port 0
