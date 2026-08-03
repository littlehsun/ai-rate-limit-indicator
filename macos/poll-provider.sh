#!/usr/bin/env bash
set -euo pipefail

APP_SUPPORT="${RATE_LIMIT_INDICATOR_APP_SUPPORT:-$HOME/Library/Application Support/RateLimitIndicator}"
CONFIG_FILE="${RATE_LIMIT_INDICATOR_CONFIG:-$HOME/.config/rate-limit-indicator/providers.env}"
PYTHON_BIN="${RATE_LIMIT_INDICATOR_PYTHON:-$(command -v python3 || true)}"
WHAM_ENV="${CODEX_RATE_WHAM_ENV:-$HOME/.config/codex-rate-indicator/wham.env}"
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

config_value() {
    local key="$1"
    local value
    value="$(
        sed -n -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*([^#[:space:]]+).*$/\\1/p" \
            "$CONFIG_FILE" 2>/dev/null | tail -n 1 | tr '[:upper:]' '[:lower:]'
    )"
    if [[ ${#value} -ge 2 ]]; then
        case "$value" in
            \"*\"|\'*\') value="${value:1:${#value}-2}" ;;
        esac
    fi
    printf '%s' "$value"
}

load_wham_environment() {
    [[ -f "$WHAM_ENV" ]] || return 0
    local line
    local key
    local value
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            ''|'#'*) continue ;;
        esac
        [[ "$line" == *=* ]] || continue
        key="${line%%=*}"
        value="${line#*=}"
        key="$(printf '%s' "$key" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
        case "$key" in
            CHATGPT_ACCESS_TOKEN|CHATGPT_BEARER_TOKEN|CODEX_AUTH_FILE|XDG_CACHE_HOME|\
            CODEX_RATE_WHAM_CACHE|CHATGPT_WHAM_USAGE_URL|\
            CHATGPT_WHAM_RESET_CREDITS_URL|CHATGPT_WHAM_TIMEOUT) ;;
            *) continue ;;
        esac
        value="$(printf '%s' "$value" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
        case "$value" in
            \"*\"|\'*\') value="${value:1:${#value}-2}" ;;
        esac
        export "$key=$value"
    done < "$WHAM_ENV"
}

case "$provider" in
    codex)
        is_enabled CODEX || exit 0
        case "$(config_value CODEX_RATE_SOURCE)" in
            auto|wham) ;;
            *) exit 0 ;;
        esac
        load_wham_environment
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
