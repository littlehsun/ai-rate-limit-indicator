#!/usr/bin/env bash
set -euo pipefail

SOURCE_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else
    REAL_HOME="$HOME"
fi

CONFIG_DIR="$REAL_HOME/.config/rate-limit-indicator"
CONFIG_FILE="${RATE_LIMIT_INDICATOR_CONFIG:-$CONFIG_DIR/providers.env}"
LEGACY_CODEX_ENV="$REAL_HOME/.config/codex-rate-indicator/wham.env"
APP_DIR="$REAL_HOME/.local/share/rate-limit-indicator"
INSTALLED_SCRIPT="$APP_DIR/manage.sh"
BIN="$REAL_HOME/.local/bin/rate-limit-indicators"
AUTOSTART="$REAL_HOME/.config/autostart/rate-limit-indicators.desktop"
UNIFIED_SERVICE="rate-limit-indicator.service"
UNIFIED_CLI="$REAL_HOME/.local/share/rate-limit-indicator/unified/cli.py"

PROVIDER_KEYS=(CODEX CLAUDE GROK GEMINI)
PROVIDER_NAMES=(codex claude grok gemini)
PROVIDER_SERVICES=(
    codex-rate-indicator.service
    claude-rate-indicator.service
    grok-rate-indicator.service
    gemini-rate-indicator.service
)
PROVIDER_TIMERS=(
    codex-rate-wham-poll.timer
    ""
    grok-rate-poll.timer
    gemini-rate-poll.timer
)

usage() {
    echo "Usage: $0 {install|start|apply|stop|status|usage [--json] [--provider NAME]}"
}

read_enabled() {
    local key="$1"
    local value
    value="$(read_config_value "$key")"
    case "$value" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

read_config_value() {
    local key="$1"
    local file="${2:-$CONFIG_FILE}"
    local value
    [[ -f "$file" ]] || return 0
    value="$(
        sed -n -E "s/^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=[[:space:]]*([^#[:space:]]+).*$/\\2/p" \
            "$file" 2>/dev/null | tail -n 1 | tr '[:upper:]' '[:lower:]'
    )"
    if [[ ${#value} -ge 2 ]]; then
        case "$value" in
            \"*\"|\'*\') value="${value:1:${#value}-2}" ;;
        esac
    fi
    printf '%s' "$value"
}

has_config_assignment() {
    local key="$1"
    grep -Eq "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$CONFIG_FILE"
}

timer_enabled_for_provider() {
    local key="$1"
    if [[ "$key" != "CODEX" ]]; then
        return 0
    fi
    case "$(read_config_value CODEX_RATE_SOURCE)" in
        auto|wham) return 0 ;;
        *) return 1 ;;
    esac
}

import_graphical_environment() {
    systemctl --user import-environment \
        DISPLAY XAUTHORITY WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE \
        DBUS_SESSION_BUS_ADDRESS DESKTOP_SESSION GDMSESSION GNOME_SHELL_SESSION_MODE \
        2>/dev/null || true
}

apply_provider() {
    local key="$1"
    local name="$2"
    local service="$3"
    local timer="$4"

    if read_enabled "$key"; then
        echo "Enabling $name data source..."
        if [[ -n "$timer" ]]; then
            if timer_enabled_for_provider "$key"; then
                systemctl --user enable --now "$timer" \
                    || echo "Warning: could not enable $timer." >&2
            else
                systemctl --user disable --now "$timer" 2>/dev/null || true
            fi
        fi
    else
        echo "Disabling $name data source..."
        if [[ -n "$timer" ]]; then
            systemctl --user disable --now "$timer" 2>/dev/null || true
        fi
    fi
    systemctl --user stop "$service" 2>/dev/null || true
}

apply_config() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        echo "Missing config: $CONFIG_FILE" >&2
        echo "Run '$0 install' first." >&2
        exit 1
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        echo "systemctl is required to manage indicators." >&2
        exit 1
    fi

    import_graphical_environment
    systemctl --user daemon-reload 2>/dev/null || true
    for idx in "${!PROVIDER_KEYS[@]}"; do
        apply_provider \
            "${PROVIDER_KEYS[$idx]}" \
            "${PROVIDER_NAMES[$idx]}" \
            "${PROVIDER_SERVICES[$idx]}" \
            "${PROVIDER_TIMERS[$idx]}"
    done
    if any_provider_enabled; then
        echo "Starting unified indicator..."
        systemctl --user restart "$UNIFIED_SERVICE" \
            || echo "Warning: could not start $UNIFIED_SERVICE." >&2
    else
        echo "No providers enabled; stopping unified indicator."
        systemctl --user stop "$UNIFIED_SERVICE" 2>/dev/null || true
    fi
}

any_provider_enabled() {
    local key
    for key in "${PROVIDER_KEYS[@]}"; do
        if read_enabled "$key"; then
            return 0
        fi
    done
    return 1
}

disable_individual_autostarts() {
    local name
    local desktop
    for name in "${PROVIDER_NAMES[@]}"; do
        desktop="$REAL_HOME/.config/autostart/$name-rate-indicator.desktop"
        [[ -f "$desktop" ]] || continue
        if grep -q '^X-GNOME-Autostart-enabled=' "$desktop"; then
            sed -i.bak 's/^X-GNOME-Autostart-enabled=.*/X-GNOME-Autostart-enabled=false/' "$desktop"
            rm -f -- "$desktop.bak"
        else
            printf '\nX-GNOME-Autostart-enabled=false\n' >> "$desktop"
        fi
    done
}

install_manager() {
    mkdir -p "$CONFIG_DIR" "$APP_DIR" "$REAL_HOME/.local/bin" "$REAL_HOME/.config/autostart"
    chmod 700 "$CONFIG_DIR"
    legacy_codex_source="$(read_config_value CODEX_RATE_SOURCE "$LEGACY_CODEX_ENV")"
    case "$legacy_codex_source" in
        auto|wham) ;;
        *) legacy_codex_source=local ;;
    esac

    if [[ ! -e "$CONFIG_FILE" ]]; then
        {
            echo "# Select the indicators managed at GNOME login."
            echo "CODEX=true"
            echo "CODEX_RATE_SOURCE=$legacy_codex_source"
            echo "CLAUDE=true"
            echo "GROK=true"
            echo "GEMINI=true"
            echo "DISPLAY_MODE=auto"
            echo "DISPLAY_PROVIDERS=codex,claude,grok,gemini"
            echo "DROPDOWN_PROVIDERS=codex,claude,grok,gemini"
            echo "PROVIDER_ORDER=codex,claude,grok,gemini"
        } > "$CONFIG_FILE"
    fi
    if ! has_config_assignment CODEX_RATE_SOURCE; then
        printf '\n# local (default), auto, or wham; auto/wham opt in to network polling.\n' >> "$CONFIG_FILE"
        printf 'CODEX_RATE_SOURCE=%s\n' "$legacy_codex_source" >> "$CONFIG_FILE"
    fi
    if ! has_config_assignment DISPLAY_MODE; then
        legacy_display="$(read_config_value DISPLAY_PROVIDER)"
        case "$legacy_display" in
            codex|claude|grok|gemini) ;;
            *) legacy_display="" ;;
        esac
        if [[ -n "$legacy_display" ]]; then
            printf 'DISPLAY_MODE=custom\n' >> "$CONFIG_FILE"
        else
            printf 'DISPLAY_MODE=auto\n' >> "$CONFIG_FILE"
        fi
    fi
    if ! has_config_assignment DISPLAY_PROVIDERS; then
        if [[ -n "${legacy_display:-}" ]]; then
            printf 'DISPLAY_PROVIDERS=%s\n' "$legacy_display" >> "$CONFIG_FILE"
        else
            printf 'DISPLAY_PROVIDERS=codex,claude,grok,gemini\n' >> "$CONFIG_FILE"
        fi
    fi
    if ! has_config_assignment DROPDOWN_PROVIDERS; then
        printf 'DROPDOWN_PROVIDERS=codex,claude,grok,gemini\n' >> "$CONFIG_FILE"
    fi
    if ! has_config_assignment PROVIDER_ORDER; then
        printf 'PROVIDER_ORDER=codex,claude,grok,gemini\n' >> "$CONFIG_FILE"
    fi
    sed -E -i.bak '/^[[:space:]]*(export[[:space:]]+)?DISPLAY_PROVIDER[[:space:]]*=/d' \
        "$CONFIG_FILE"
    rm -f -- "$CONFIG_FILE.bak"
    chmod 600 "$CONFIG_FILE"

    if [[ "$SOURCE_SCRIPT" != "$INSTALLED_SCRIPT" ]]; then
        cp "$SOURCE_SCRIPT" "$INSTALLED_SCRIPT"
    fi
    chmod +x "$INSTALLED_SCRIPT"

    cat > "$BIN" <<EOF
#!/usr/bin/env bash
exec "$INSTALLED_SCRIPT" "\$@"
EOF
    chmod +x "$BIN"

    cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Rate Limit Indicators
Comment=Start selected AI rate-limit indicators
Exec=$BIN start
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

    disable_individual_autostarts
    for service in "${PROVIDER_SERVICES[@]}"; do
        systemctl --user disable "$service" 2>/dev/null || true
    done
    systemctl --user disable "$UNIFIED_SERVICE" 2>/dev/null || true

    echo "Manager: $BIN"
    echo "Config: $CONFIG_FILE"
    echo "Autostart: $AUTOSTART"
    apply_config
}

stop_all() {
    local service
    local timer
    for service in "${PROVIDER_SERVICES[@]}"; do
        systemctl --user stop "$service" 2>/dev/null || true
    done
    for timer in "${PROVIDER_TIMERS[@]}"; do
        [[ -n "$timer" ]] || continue
        systemctl --user stop "$timer" 2>/dev/null || true
    done
    systemctl --user stop "$UNIFIED_SERVICE" 2>/dev/null || true
}

show_status() {
    local idx
    local configured
    local active
    printf '%-8s %-10s %-10s\n' "PROVIDER" "CONFIG" "COLLECTOR"
    for idx in "${!PROVIDER_KEYS[@]}"; do
        configured=false
        read_enabled "${PROVIDER_KEYS[$idx]}" && configured=true
        if [[ -n "${PROVIDER_TIMERS[$idx]}" ]]; then
            active="$(systemctl --user is-active "${PROVIDER_TIMERS[$idx]}" 2>/dev/null || true)"
        else
            active="file"
        fi
        printf '%-8s %-10s %-10s\n' "${PROVIDER_NAMES[$idx]}" "$configured" "${active:-unknown}"
    done
    active="$(systemctl --user is-active "$UNIFIED_SERVICE" 2>/dev/null || true)"
    printf '\nUnified UI: %s\n' "${active:-unknown}"
}

case "${1:-}" in
    install) install_manager ;;
    start|apply) apply_config ;;
    stop) stop_all ;;
    status) show_status ;;
    usage)
        if [[ ! -f "$UNIFIED_CLI" ]]; then
            echo "Unified CLI is not installed. Run the repository installer first." >&2
            exit 1
        fi
        exec python3 "$UNIFIED_CLI" "${@:2}"
        ;;
    -h|--help) usage ;;
    *) usage >&2; exit 2 ;;
esac
