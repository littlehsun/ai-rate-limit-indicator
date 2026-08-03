#!/usr/bin/env bash
set -euo pipefail

APP_SUPPORT="${RATE_LIMIT_INDICATOR_APP_SUPPORT:-$HOME/Library/Application Support/RateLimitIndicator}"
CONFIG_FILE="${RATE_LIMIT_INDICATOR_CONFIG:-$HOME/.config/rate-limit-indicator/providers.env}"
PYTHON_BIN="${RATE_LIMIT_INDICATOR_PYTHON:-$(command -v python3 || true)}"
provider="${1:-}"

if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
    echo "python3 executable not found: ${PYTHON_BIN:-unset}" >&2
    exit 1
fi

is_enabled() {
    local key="$1"
    local value
    value="$(
        sed -n -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*([^#[:space:]]+).*$/\\1/p" \
            "$CONFIG_FILE" 2>/dev/null | tail -n 1
    )"
    value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
    case "$value" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

case "$provider" in
    codex)
        is_enabled CODEX || exit 0
        exec "$PYTHON_BIN" "$APP_SUPPORT/backend/collectors/wham.py" --once
        ;;
    grok)
        is_enabled GROK || exit 0
        exec "$PYTHON_BIN" "$APP_SUPPORT/backend/collectors/grok_rate.py" --once
        ;;
    *)
        echo "Usage: $0 {codex|grok}" >&2
        exit 2
        ;;
esac
