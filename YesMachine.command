#!/usr/bin/env bash
set -euo pipefail
YESMACHINE_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
exec "$YESMACHINE_DIR/run.sh" "$@"
