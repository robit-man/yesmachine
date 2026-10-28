#!/usr/bin/env bash
set -euo pipefail
YESMACHINE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$YESMACHINE_DIR"
YESMACHINE_PYTHON=python3
if [[ -x "$YESMACHINE_DIR/.venv/bin/python" ]]; then
  YESMACHINE_PYTHON="$YESMACHINE_DIR/.venv/bin/python"
fi
exec "$YESMACHINE_PYTHON" -m yesmachine.app "$@"
