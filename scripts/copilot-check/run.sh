#!/usr/bin/env bash
# Guided live run: GitHub Copilot through Bifrost, Tavily through a Bifrost relay. See docs/COPILOT-BIFROST-SETUP.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || true)"
fi
[[ -n "$PYTHON_BIN" ]] || { echo "Python 3.12 or newer is required." >&2; exit 1; }

if [[ "$(uname -s)" == Darwin && -z "${SSL_CERT_FILE:-}" && -r /etc/ssl/cert.pem ]] &&
   "$PYTHON_BIN" -c 'import ssl, sys; sys.exit(bool(ssl.get_default_verify_paths().cafile))'; then
  export SSL_CERT_FILE=/etc/ssl/cert.pem
fi

exec "$PYTHON_BIN" "$HERE/desktop_run.py" "$@"
