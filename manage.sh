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
APP_DIR="$REAL_HOME/.local/share/rate-limit-indicator"
INSTALLED_SCRIPT="$APP_DIR/manage.sh"
BIN="$REAL_HOME/.local/bin/rate-limit-indicators"
AUTOSTART="$REAL_HOME/.config/autostart/rate-limit-indicators.desktop"

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
    echo "Usage: $0 {install|start|apply|stop|status}"
}

read_enabled() {
    local key="$1"
    local value
    value="$(
        sed -n -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*([^#[:space:]]+).*$/\\1/p" \
            "$CONFIG_FILE" 2>/dev/null | tail -n 1
    )"
    case "${value,,}" in
        1|true|yes|on) return 0 ;;
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
        echo "Starting $name indicator..."
        systemctl --user restart "$service" \
            || echo "Warning: could not start $service (is the provider installed?)." >&2
        if [[ -n "$timer" ]]; then
            systemctl --user enable --now "$timer" \
                || echo "Warning: could not enable $timer." >&2
        fi
    else
        echo "Stopping $name indicator..."
        systemctl --user stop "$service" 2>/dev/null || true
        if [[ -n "$timer" ]]; then
            systemctl --user disable --now "$timer" 2>/dev/null || true
        fi
    fi
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
}

disable_individual_autostarts() {
    local name
    local desktop
    for name in "${PROVIDER_NAMES[@]}"; do
        desktop="$REAL_HOME/.config/autostart/$name-rate-indicator.desktop"
        [[ -f "$desktop" ]] || continue
        if grep -q '^X-GNOME-Autostart-enabled=' "$desktop"; then
            sed -i 's/^X-GNOME-Autostart-enabled=.*/X-GNOME-Autostart-enabled=false/' "$desktop"
        else
            printf '\nX-GNOME-Autostart-enabled=false\n' >> "$desktop"
        fi
    done
}

install_manager() {
    mkdir -p "$CONFIG_DIR" "$APP_DIR" "$REAL_HOME/.local/bin" "$REAL_HOME/.config/autostart"
    chmod 700 "$CONFIG_DIR"

    if [[ ! -e "$CONFIG_FILE" ]]; then
        {
            echo "# Select the indicators managed at GNOME login."
            echo "CODEX=true"
            echo "CLAUDE=true"
            echo "GROK=true"
            echo "GEMINI=true"
        } > "$CONFIG_FILE"
    fi
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
}

show_status() {
    local idx
    local configured
    local active
    printf '%-8s %-10s %-10s\n' "PROVIDER" "CONFIG" "SERVICE"
    for idx in "${!PROVIDER_KEYS[@]}"; do
        configured=false
        read_enabled "${PROVIDER_KEYS[$idx]}" && configured=true
        active="$(systemctl --user is-active "${PROVIDER_SERVICES[$idx]}" 2>/dev/null || true)"
        printf '%-8s %-10s %-10s\n' "${PROVIDER_NAMES[$idx]}" "$configured" "${active:-unknown}"
    done
}

case "${1:-}" in
    install) install_manager ;;
    start|apply) apply_config ;;
    stop) stop_all ;;
    status) show_status ;;
    -h|--help) usage ;;
    *) usage >&2; exit 2 ;;
esac
