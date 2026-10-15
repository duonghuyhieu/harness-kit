#!/usr/bin/env bash
# Install harness-kit into a project (macOS / Linux / Git Bash).
#   ./install.sh [project-dir] [harness install options...]
#   ./install.sh --global              # skills + agents into ~/.claude for every project
# Needs Python >= 3.10. Runs the kit's own installer; nothing is downloaded and nothing needs sudo.
set -euo pipefail
KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && \
     [ "$("$cand" -c 'import sys; print(sys.version_info >= (3, 10))' 2>/dev/null)" = "True" ]; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "harness-kit needs Python >= 3.10 (python3 or python on PATH)." >&2
  exit 1
fi

if [ "${1:-}" = "--global" ]; then
  shift
  exec "$PY" "$KIT_DIR/harness.py" install --global "$@"
fi
TARGET="${1:-.}"
[ $# -gt 0 ] && shift
exec "$PY" "$KIT_DIR/harness.py" install "$TARGET" "$@"
