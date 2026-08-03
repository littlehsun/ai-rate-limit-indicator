#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPINDICATOR_PACKAGE="gir1.2-appindicator3-0.1"

if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else
    REAL_HOME="$HOME"
fi

APP_DIR="$REAL_HOME/.local/share/codex-rate-indicator"
BIN="$REAL_HOME/.local/bin/codex-rate-indicator"
WHAM_BIN="$REAL_HOME/.local/bin/codex-rate-wham-poll"
STARTER="$REAL_HOME/.local/bin/codex-rate-indicator-start"
AUTOSTART="$REAL_HOME/.config/autostart/codex-rate-indicator.desktop"
APPLICATIONS_DIR="$REAL_HOME/.local/share/applications"
APP_LAUNCHER="$APPLICATIONS_DIR/codex-rate-indicator.desktop"
SERVICE_DIR="$REAL_HOME/.config/systemd/user"
SERVICE="$SERVICE_DIR/codex-rate-indicator.service"
WHAM_SERVICE="$SERVICE_DIR/codex-rate-wham-poll.service"
WHAM_TIMER="$SERVICE_DIR/codex-rate-wham-poll.timer"
CONFIG_DIR="$REAL_HOME/.config/codex-rate-indicator"
WHAM_ENV="$CONFIG_DIR/wham.env"

echo "=== Codex Rate Indicator for GNOME ==="

echo "[1/5] Checking AppIndicator dependency..."
if ! python3 -c "import gi; gi.require_version('AppIndicator3','0.1'); from gi.repository import AppIndicator3" 2>/dev/null; then
    if command -v apt-get >/dev/null 2>&1; then
        echo "Installing $APPINDICATOR_PACKAGE..."
        sudo apt-get update
        sudo apt-get install -y "$APPINDICATOR_PACKAGE"
    else
        echo "Missing AppIndicator3 Python bindings."
        echo "Install your distro's AppIndicator3 typelib package, then rerun this script."
        exit 1
    fi
else
    echo "AppIndicator3 is available."
fi

echo "[2/5] Installing files..."
mkdir -p "$APP_DIR/assets" "$REAL_HOME/.local/bin" "$SERVICE_DIR"
cp "$SCRIPT_DIR/indicator.py" "$APP_DIR/indicator.py"
cp "$SCRIPT_DIR/codex_rate.py" "$APP_DIR/codex_rate.py"
cp "$SCRIPT_DIR/wham.py" "$APP_DIR/wham.py"
cp "$SCRIPT_DIR/assets/codex-logo.png" "$APP_DIR/assets/codex-logo.png"
chmod +x "$APP_DIR/indicator.py" "$APP_DIR/codex_rate.py" "$APP_DIR/wham.py"

cat > "$BIN" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/indicator.py" "\$@"
EOF
chmod +x "$BIN"

cat > "$WHAM_BIN" <<EOF
#!/usr/bin/env bash
if [[ -f "$WHAM_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$WHAM_ENV"
    set +a
fi
exec python3 "$APP_DIR/wham.py" --once "\$@"
EOF
chmod +x "$WHAM_BIN"

cat > "$STARTER" <<EOF
#!/usr/bin/env bash
set -euo pipefail

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user import-environment DISPLAY XAUTHORITY WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS DESKTOP_SESSION GDMSESSION GNOME_SHELL_SESSION_MODE || true
    systemctl --user daemon-reload || true
    exec systemctl --user restart codex-rate-indicator.service
fi

exec "$BIN"
EOF
chmod +x "$STARTER"

echo "[3/5] Setting user service and autostart..."
cat > "$SERVICE" <<EOF
[Unit]
Description=Codex Rate Indicator
After=graphical-session.target

[Service]
Type=simple
ExecStart=$BIN
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-$WHAM_ENV

[Install]
WantedBy=default.target
EOF

cat > "$WHAM_SERVICE" <<EOF
[Unit]
Description=Poll ChatGPT wham quota cache
Documentation=https://chatgpt.com/

[Service]
Type=oneshot
EnvironmentFile=$WHAM_ENV
ExecStart=$WHAM_BIN
EOF

cat > "$WHAM_TIMER" <<EOF
[Unit]
Description=Poll ChatGPT wham quota cache every minute

[Timer]
OnActiveSec=30s
OnUnitActiveSec=1m
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
if [[ ! -e "$WHAM_ENV" ]]; then
    cat > "$WHAM_ENV" <<EOF
# Opt in to ChatGPT wham quota polling.
# This is an undocumented ChatGPT backend. By default the poller uses
# ~/.codex/auth.json from your logged-in Codex session.
# CHATGPT_ACCESS_TOKEN=optional-token-override
CODEX_RATE_SOURCE=local
CODEX_RATE_SHOW_5H=false
EOF
    chmod 600 "$WHAM_ENV"
fi
if ! grep -Eq '^[[:space:]]*CODEX_RATE_SOURCE[[:space:]]*=' "$WHAM_ENV"; then
    printf '\nCODEX_RATE_SOURCE=local\n' >> "$WHAM_ENV"
fi
if ! grep -Eq '^[[:space:]]*CODEX_RATE_SHOW_5H[[:space:]]*=' "$WHAM_ENV"; then
    printf 'CODEX_RATE_SHOW_5H=false\n' >> "$WHAM_ENV"
fi
chmod 600 "$WHAM_ENV"

mkdir -p "$REAL_HOME/.config/autostart"
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Codex Rate Indicator
Comment=Codex rate limit indicator for GNOME
Exec=$STARTER
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

mkdir -p "$APPLICATIONS_DIR"
cat > "$APP_LAUNCHER" <<EOF
[Desktop Entry]
Type=Application
Name=Codex Rate Indicator
Comment=Show Codex rate-limit usage in the top bar
Exec=$STARTER
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;
StartupNotify=false
EOF
chmod 644 "$APP_LAUNCHER"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR" >/dev/null 2>&1 || true
fi
systemctl --user daemon-reload 2>/dev/null || true

echo "[4/5] Checking panel host..."
if command -v gdbus >/dev/null 2>&1; then
    host_status="$(gdbus call --session --dest org.kde.StatusNotifierWatcher --object-path /StatusNotifierWatcher --method org.freedesktop.DBus.Properties.Get org.kde.StatusNotifierWatcher IsStatusNotifierHostRegistered 2>/dev/null || true)"
    if [[ "$host_status" == *"<true>"* ]]; then
        echo "StatusNotifier host is available."
    else
        echo "Warning: no StatusNotifier host was detected. Enable Ubuntu AppIndicators or a compatible tray extension."
    fi
else
    echo "Skipping StatusNotifier host check because gdbus is not installed."
fi

echo "[5/5] Installed."
echo "Binary: $BIN"
echo "Wham poller: $WHAM_BIN"
echo "Starter: $STARTER"
echo "Service: $SERVICE"
echo "Wham timer: $WHAM_TIMER"
echo "Wham env: $WHAM_ENV"
echo "Autostart: $AUTOSTART"
echo "App launcher: $APP_LAUNCHER"

read -r -p "Start now? (y/N) " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    if command -v systemctl >/dev/null 2>&1; then
        "$STARTER"
        echo "Started Codex Rate Indicator through user service."
    else
        "$BIN" &
        echo "Started Codex Rate Indicator (PID $!)."
    fi
fi
