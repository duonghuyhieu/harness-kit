#!/usr/bin/env bash
# Standard boot path: set up environment -> verify baseline -> print the app start command.
# The real commands live in .harness/config.json (single source of truth).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
PY=""
for cand in python3 python py; do  # skip the Microsoft Store python3 stub on Windows
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import sys; sys.exit(sys.version_info < (3, 9))" >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then echo "ERROR: Python >= 3.9 is required to run scripts/harness.py"; exit 1; fi

echo "==> Working directory: $PWD"
echo "==> Installing dependencies"
"$PY" scripts/harness.py setup

echo "==> Verifying baseline"
"$PY" scripts/harness.py check

echo "==> App start command:"
"$PY" scripts/harness.py setup --print-dev

if [ "${RUN_START_COMMAND:-0}" = "1" ]; then
  exec "$PY" scripts/harness.py setup --run-dev
fi
echo "Set RUN_START_COMMAND=1 if you want init.sh to start the app as well."
