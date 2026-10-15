#!/usr/bin/env bash
# harness-kit one-line installer for macOS / Linux / Git Bash.
#
#   curl -fsSL https://raw.githubusercontent.com/duonghuyhieu/harness/main/install.sh | bash
#       -> installs the harness + .claude/ into the CURRENT directory (run it from your project folder)
#   curl -fsSL .../install.sh | bash -s -- /path/to/project [harness install options]
#   ./install.sh /path/to/project [options]          (from a clone)
#
# Piped, the kit is downloaded to ~/.harness-kit (re-running updates it). Needs Python >= 3.10. No sudo.
set -euo pipefail
REPO="duonghuyhieu/harness"

PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && \
     [ "$("$cand" -c 'import sys; print(sys.version_info >= (3, 10))' 2>/dev/null)" = "True" ]; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "harness-kit needs Python >= 3.10 (python3 or python on PATH): https://www.python.org/downloads/" >&2
  exit 1
fi

# 1. Locate the kit: next to this script (a clone), otherwise download it to ~/.harness-kit
SELF="${BASH_SOURCE[0]:-}"
if [ -n "$SELF" ] && [ -f "$(cd "$(dirname "$SELF")" && pwd)/harness.py" ]; then
  KIT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
else
  KIT_DIR="$HOME/.harness-kit"
  TMP="$(mktemp -d)"
  echo "Downloading harness-kit from github.com/$REPO ..."
  URL="https://github.com/$REPO/archive/refs/heads/main.tar.gz"
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$URL" -o "$TMP/kit.tar.gz"
  else wget -qO "$TMP/kit.tar.gz" "$URL"; fi
  tar -xzf "$TMP/kit.tar.gz" -C "$TMP"
  rm -rf "${KIT_DIR:?}"
  mv "$TMP"/harness-* "$KIT_DIR"
  rm -rf "${TMP:?}"
fi

# 2. Install into the target project (never overwrites your files)
TARGET="${1:-$PWD}"
[ $# -gt 0 ] && shift
exec "$PY" "$KIT_DIR/harness.py" install "$TARGET" "$@"
