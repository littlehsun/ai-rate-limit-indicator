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
    value="$(config_value "$key")"
    case "$value" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

config_value() {
    local key="$1"
    local value
    value="$(
        sed -n -E "s/^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=[[:space:]]*([^#[:space:]]+).*$/\\2/p" \
            "$CONFIG_FILE" 2>/dev/null | tail -n 1 | tr '[:upper:]' '[:lower:]'
    )"
    if [[ ${#value} -ge 2 ]]; then
        case "$value" in
            \"*\"|\'*\') value="${value:1:${#value}-2}" ;;
        esac
    fi
    printf '%s' "$value"
}

strip_unquoted_comment() {
    local input="$1"
    local output=""
    local quote=""
    local character
    local previous=""
    local escaped=false
    local index
    for ((index = 0; index < ${#input}; index++)); do
        character="${input:index:1}"
        if [[ "$escaped" == true ]]; then
            output+="$character"
            escaped=false
            previous="$character"
            continue
        fi
        if [[ "$character" == "\\" && "$quote" != "'" ]]; then
            output+="$character"
            escaped=true
            previous="$character"
            continue
        fi
        if [[ -n "$quote" ]]; then
            output+="$character"
            if [[ "$character" == "$quote" ]]; then
                quote=""
            fi
            previous="$character"
            continue
        fi
        case "$character" in
            "'"|'"') quote="$character" ;;
            '#')
                if [[ -z "$output" || "$previous" == " " || "$previous" == $'\t' ]]; then
                    break
                fi
                ;;
        esac
        output+="$character"
        previous="$character"
    done
    printf '%s' "$output" | sed -E 's/[[:space:]]+$//'
}

expand_supported_path_variables() {
    local value="$1"
    case "$value" in
        '~') value="$HOME" ;;
        '~/'*) value="$HOME/${value:2}" ;;
    esac
    value="${value//\$\{HOME\}/$HOME}"
    value="${value//\$HOME/$HOME}"
    if [[ -n "${XDG_CACHE_HOME:-}" ]]; then
        value="${value//\$\{XDG_CACHE_HOME\}/$XDG_CACHE_HOME}"
        value="${value//\$XDG_CACHE_HOME/$XDG_CACHE_HOME}"
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
        key="$(printf '%s' "$key" | sed -E 's/^export[[:space:]]+//')"
        case "$key" in
            CHATGPT_ACCESS_TOKEN|CHATGPT_BEARER_TOKEN|CODEX_AUTH_FILE|XDG_CACHE_HOME|\
            CODEX_RATE_WHAM_CACHE|CHATGPT_WHAM_USAGE_URL|\
            CHATGPT_WHAM_RESET_CREDITS_URL|CHATGPT_WHAM_TIMEOUT) ;;
            *) continue ;;
        esac
        value="$(printf '%s' "$value" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
        value="$(strip_unquoted_comment "$value")"
        case "$value" in
            \"*\"|\'*\') value="${value:1:${#value}-2}" ;;
        esac
        case "$key" in
            CODEX_AUTH_FILE|XDG_CACHE_HOME|CODEX_RATE_WHAM_CACHE)
                value="$(expand_supported_path_variables "$value")"
                ;;
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
