#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/rate-limit-indicator-tests.XXXXXX")"
trap 'rm -rf "$CACHE_DIR"' EXIT
export PYTHONPYCACHEPREFIX="$CACHE_DIR/pycache"

echo "=== Codex ==="
(
    cd "$ROOT_DIR/providers/codex"
    PYTHONPATH="$PWD/ubuntu-indicator" python3 -m unittest discover -s tests -v
    bash -n ubuntu-indicator/install.sh
    bash -n macos-menubar/install.sh
    if command -v swiftc >/dev/null 2>&1; then
        bash tests/test_macos_once.sh
    else
        echo "SKIP: Codex macOS smoke test (swiftc not installed)"
    fi
)

echo
echo "=== Claude ==="
(
    cd "$ROOT_DIR/providers/claude"
    python3 -m unittest discover -s tests -v
    python3 -m py_compile indicator.py
    bash -n install.sh
)

echo
echo "=== Grok ==="
(
    cd "$ROOT_DIR/providers/grok"
    PYTHONPATH="$PWD/ubuntu-indicator" python3 -m unittest discover -s tests -v
    bash -n ubuntu-indicator/install.sh
)

echo
echo "=== Gemini ==="
(
    cd "$ROOT_DIR/providers/gemini"
    PYTHONPATH="$PWD/ubuntu-indicator" python3 -m unittest discover -s tests -v
    bash -n ubuntu-indicator/install.sh
)

echo
echo "=== Unified indicator ==="
(
    cd "$ROOT_DIR/unified-indicator"
    PYTHONPATH="$PWD" python3 -m unittest discover -s tests -v
    bash -n install.sh
)

echo
echo "=== Integration ==="
python3 -m unittest discover -s "$ROOT_DIR/tests" -v
bash -n "$ROOT_DIR/install.sh"
bash -n "$ROOT_DIR/manage.sh"
"$ROOT_DIR/install.sh" --help
echo "All available checks passed."
