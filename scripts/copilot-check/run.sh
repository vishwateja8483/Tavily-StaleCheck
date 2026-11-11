#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
IMAGE="${BIFROST_IMAGE:-maximhq/bifrost:v2.5.1}"
PORT="${GATEWAY_PORT:-18081}"
MODEL="${LIVE_MODEL:-}"
PYTHON_BIN="${PYTHON:-$ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then PYTHON_BIN="${PYTHON:-python3}"; fi
OUT="${OUT_DIR:-$ROOT/runs/copilot-live-$(date +%Y%m%d-%H%M%S)-$$}"
NAME="stalecheck-copilot-$$"
WORK="$(mktemp -d)"

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -rf "$WORK"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for tool in docker curl "$PYTHON_BIN"; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required" >&2; exit 2; }
done
if [ -z "${GITHUB_COPILOT_API_KEY:-}" ]; then
  echo "Set GITHUB_COPILOT_API_KEY to a fine-grained GitHub token with Copilot Requests permission." >&2
  exit 2
fi
if [ -z "${TAVILY_API_KEY:-}" ]; then
  echo "Set TAVILY_API_KEY; it is passed only to the local Bifrost container." >&2
  exit 2
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export BIFROST_VIRTUAL_KEY="${BIFROST_VIRTUAL_KEY:-sk-bf-$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_hex(16))')}"

mkdir -p "$WORK/data" "$OUT"
cp "$HERE/config.json" "$WORK/data/config.json"
chmod 755 "$WORK"
chmod -R a+rwX "$WORK/data"

docker pull "$IMAGE"
docker run --rm -d --name "$NAME" -p "127.0.0.1:$PORT:8080" \
  -e APP_HOST=0.0.0.0 -e APP_PORT=8080 \
  -e GITHUB_COPILOT_API_KEY -e TAVILY_API_KEY -e BIFROST_VIRTUAL_KEY \
  -v "$WORK/data:/app/data" "$IMAGE" >/dev/null

for _ in $(seq 1 90); do
  curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null

if ! curl -fsS "http://127.0.0.1:$PORT/v1/models?provider=github-copilot" \
  -H "x-bf-vk: $BIFROST_VIRTUAL_KEY" -o "$WORK/models.json"; then
  "$PYTHON_BIN" -c '
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    error = data.get("error", {})
    print((error.get("message") if isinstance(error, dict) else error) or "Copilot model discovery failed.")
except (OSError, ValueError):
    print("Copilot model discovery failed; response was not valid JSON.")
' "$WORK/models.json" >&2
  exit 1
fi
if [ -z "$MODEL" ]; then
  MODEL="$("$PYTHON_BIN" -c '
import json, sys
rows = json.load(open(sys.argv[1])).get("data", [])
ids = [row.get("id", "") for row in rows if isinstance(row, dict)]
ids = [value if value.startswith("github-copilot/") else "github-copilot/" + value
       for value in ids if value and "embedding" not in value.lower()]
preferred = next((value for value in ids if value.endswith("/gpt-5.5")), "")
print(preferred or next(iter(ids), ""))
' "$WORK/models.json")"
fi
if [ -z "$MODEL" ]; then
  echo "No chat model is available for this Copilot seat. Check its plan and organization policy." >&2
  exit 1
fi
case "$MODEL" in github-copilot/*) ;; *) MODEL="github-copilot/$MODEL" ;; esac

"$PYTHON_BIN" -c '
import json, sys
json.dump({"model": sys.argv[1], "messages": [{"role": "user", "content": "Reply with exactly COPILOT_LIVE_OK."}], "max_tokens": 16},
          open(sys.argv[2], "w"))
' "$MODEL" "$WORK/request.json"
if ! curl -fsS --max-time 120 "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H "x-bf-vk: $BIFROST_VIRTUAL_KEY" -H 'Content-Type: application/json' \
  --data-binary "@$WORK/request.json" -o "$WORK/response.json"; then
  "$PYTHON_BIN" -c '
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    error = data.get("error", {})
    print((error.get("message") if isinstance(error, dict) else error) or "Copilot request failed.")
except (OSError, ValueError):
    print("Copilot request failed; response was not valid JSON.")
' "$WORK/response.json" >&2
  exit 1
fi
"$PYTHON_BIN" -c '
import json, sys
response = json.load(open(sys.argv[1]))
content = response["choices"][0]["message"]["content"]
if "COPILOT_LIVE_OK" not in content:
    raise SystemExit("Copilot returned an unexpected smoke-test response.")
print("Real Copilot smoke test passed through Bifrost.")
print("Model:", sys.argv[2])
print("Reply:", content)
print("Usage reported by Bifrost:", response.get("usage"))
' "$WORK/response.json" "$MODEL"

export BIFROST_URL="http://127.0.0.1:$PORT"
export BIFROST_MODEL="$MODEL"
export BIFROST_SEARCH_RELAY=tavily
echo "Starting the real StaleCheck browser assessment. No search runs until you approve the exact request."
echo "Copilot requests count against your GitHub Copilot allowance; Bifrost may report their dollar cost as zero."
echo "Tavily searches use your Tavily account. Keep this terminal open; the launch URL is a private local credential."
"$PYTHON_BIN" -m stalecheck serve-assessment --live --output "$OUT/assessment" --port 0
