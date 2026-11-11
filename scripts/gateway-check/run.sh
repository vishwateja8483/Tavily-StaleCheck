#!/usr/bin/env bash
# Run StaleCheck against a real Bifrost gateway container. The model and Tavily upstreams are local stand-ins
# (stub_upstreams.py), so no provider account or API key is needed. Needs Docker
# and Python 3.12+. See ../../docs/BIFROST-GATEWAY.md.
#
#   scripts/gateway-check/run.sh                      # uses maximhq/bifrost:v2.4.0
#   BIFROST_IMAGE=maximhq/bifrost:latest scripts/gateway-check/run.sh
#   KEEP_WORK=1 scripts/gateway-check/run.sh          # keep the run directory (stub log, reports) for inspection
#   GATEWAY_CONFIG=my-config.json scripts/gateway-check/run.sh   # run the same checks against another config
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${BIFROST_IMAGE:-maximhq/bifrost:v2.4.0}"
PORT="${GATEWAY_PORT:-8080}"
PYTHON_BIN="${PYTHON:-python3}"
SETUP_TOKEN="harness-setup-token"   # only protects this throwaway gateway's /api routes
NAME="bifrost-gateway-check-$$"
STUB_NAME="$NAME-stubs"
WORK="$(cd "$(mktemp -d)" && pwd -P)"
STUB_PID=""

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker rm -f "$STUB_NAME" >/dev/null 2>&1 || true
  if [ -n "$STUB_PID" ]; then kill "$STUB_PID" >/dev/null 2>&1 || true; fi
  if [ "${KEEP_WORK:-0}" = "1" ]; then echo "run directory kept: $WORK"; else rm -rf "$WORK"; fi
}
trap cleanup EXIT

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 2; }
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 2; }
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"

mkdir -p "$WORK/data"
cp "${GATEWAY_CONFIG:-$HERE/config.json}" "$WORK/data/config.json"
if [ "$(uname -s)" = Darwin ]; then
  # Share loopback with the stubs without changing Bifrost's provider URL policy.
  stub_image="${STUB_IMAGE:-python:3.12.15-slim-trixie}"
  docker image inspect "$stub_image" >/dev/null 2>&1 || docker pull "$stub_image"
  docker run --rm -d --name "$STUB_NAME" --user "$(id -u):$(id -g)" \
    -p "127.0.0.1:$PORT:$PORT" -p 127.0.0.1:9101:9101 -p 127.0.0.1:9102:9102 \
    -v "$HERE/stub_upstreams.py:/stub_upstreams.py:ro" -v "$WORK:/work" \
    "$stub_image" python -u /stub_upstreams.py --bind 0.0.0.0 --log /work/stubs.jsonl >/dev/null
  network=(--network "container:$STUB_NAME")
  listen=0.0.0.0
else
  network=(--network host)
  listen=127.0.0.1
  "$PYTHON_BIN" "$HERE/stub_upstreams.py" --log "$WORK/stubs.jsonl" >"$WORK/stubs.out" 2>&1 &
  STUB_PID=$!
fi
chmod -R a+rwX "$WORK/data"   # the gateway runs as an unprivileged user and writes its SQLite files here

for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:9101/_admin/mode" >/dev/null 2>&1 && break
  sleep 0.5
done

docker run -d --name "$NAME" "${network[@]}" \
  -e APP_HOST="$listen" -e APP_PORT="$PORT" -e BIFROST_SETUP_TOKEN="$SETUP_TOKEN" \
  -e GATEWAY_CHECK_LLM_KEY=stub-llm-key -e GATEWAY_CHECK_TAVILY_KEY=stub-tavily-secret \
  -e GATEWAY_CHECK_VK_DEMO=sk-bf-demo-key-0123456789 \
  -v "$WORK/data:/app/data" "$IMAGE" >/dev/null
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
if ! curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "the gateway did not become healthy; last log lines:" >&2
  docker logs --tail 30 "$NAME" >&2 || true
  exit 1
fi
echo "gateway: $(docker inspect --format '{{.Config.Image}}' "$NAME") on 127.0.0.1:$PORT"

status=0
"$PYTHON_BIN" "$HERE/check.py" --gateway "http://127.0.0.1:$PORT" --stub-log "$WORK/stubs.jsonl" \
  --setup-token "$SETUP_TOKEN" --work "$WORK" ${CHECK_JSON:+--json "$CHECK_JSON"} || status=$?
if [ "$status" -ne 0 ]; then
  echo "--- gateway log (tail) ---" >&2
  docker logs --tail 40 "$NAME" >&2 || true
  echo "--- stub upstream output ---" >&2
  if [ -n "$STUB_PID" ]; then
    tail -n 20 "$WORK/stubs.out" >&2 || true
  else
    docker logs --tail 20 "$STUB_NAME" >&2 || true
  fi
fi
exit "$status"
