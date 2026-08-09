#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPINDICATOR_PACKAGE="gir1.2-appindicator3-0.1"

if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else
    REAL_HOME="$HOME"
fi

APP_DIR="$REAL_HOME/.local/share/grok-rate-indicator"
BIN="$REAL_HOME/.local/bin/grok-rate-indicator"
POLL_BIN="$REAL_HOME/.local/bin/grok-rate-poll"
STARTER="$REAL_HOME/.local/bin/grok-rate-indicator-start"
AUTOSTART="$REAL_HOME/.config/autostart/grok-rate-indicator.desktop"
APPLICATIONS_DIR="$REAL_HOME/.local/share/applications"
APP_LAUNCHER="$APPLICATIONS_DIR/grok-rate-indicator.desktop"
SERVICE_DIR="$REAL_HOME/.config/systemd/user"
SERVICE="$SERVICE_DIR/grok-rate-indicator.service"
POLL_SERVICE="$SERVICE_DIR/grok-rate-poll.service"
POLL_TIMER="$SERVICE_DIR/grok-rate-poll.timer"
CACHE_DIR="$REAL_HOME/.cache/grok-rate-indicator"

echo "=== Grok Rate Indicator for GNOME ==="

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
mkdir -p "$APP_DIR/assets" "$REAL_HOME/.local/bin" "$SERVICE_DIR" "$CACHE_DIR"
chmod 700 "$CACHE_DIR" 2>/dev/null || true
cp "$SCRIPT_DIR/indicator.py" "$APP_DIR/indicator.py"
cp "$SCRIPT_DIR/grok_rate.py" "$APP_DIR/grok_rate.py"
cp "$SCRIPT_DIR/assets/grok-logo.png" "$APP_DIR/assets/grok-logo.png"
chmod +x "$APP_DIR/indicator.py" "$APP_DIR/grok_rate.py"

cat > "$BIN" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/indicator.py" "\$@"
EOF
chmod +x "$BIN"

cat > "$POLL_BIN" <<EOF
#!/usr/bin/env bash
# GROK_AUTO_REFRESH lets the collector nudge the Grok CLI when the token has
# expired. It lives in the shared config so both platforms read one switch.
SHARED_CONFIG="\${RATE_LIMIT_INDICATOR_CONFIG:-\$HOME/.config/rate-limit-indicator/providers.env}"
if [[ -z "\${GROK_AUTO_REFRESH:-}" && -f "\$SHARED_CONFIG" ]]; then
    case "\$(sed -n -E 's/^[[:space:]]*(export[[:space:]]+)?GROK_AUTO_REFRESH[[:space:]]*=[[:space:]]*([^#[:space:]]+).*\$/\\2/p' "\$SHARED_CONFIG" | tail -n 1 | tr -d '"'\\''' | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on) export GROK_AUTO_REFRESH=1 ;;
    esac
fi
: "\${GROK_CLI:=$(command -v grok || true)}"
[[ -n "\$GROK_CLI" ]] && export GROK_CLI
exec python3 "$APP_DIR/grok_rate.py" --once "\$@"
EOF
chmod +x "$POLL_BIN"

cat > "$STARTER" <<EOF
#!/usr/bin/env bash
set -euo pipefail

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user import-environment DISPLAY XAUTHORITY WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS DESKTOP_SESSION GDMSESSION GNOME_SHELL_SESSION_MODE || true
    systemctl --user daemon-reload || true
    systemctl --user restart grok-rate-poll.timer || true
    exec systemctl --user restart grok-rate-indicator.service
fi

exec "$BIN"
EOF
chmod +x "$STARTER"

echo "[3/5] Setting user service and autostart..."
cat > "$SERVICE" <<EOF
[Unit]
Description=Grok Rate Indicator
After=graphical-session.target

[Service]
Type=simple
ExecStart=$BIN
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

cat > "$POLL_SERVICE" <<EOF
[Unit]
Description=Poll Grok Build billing usage cache
Documentation=https://cli-chat-proxy.grok.com/

[Service]
Type=oneshot
ExecStart=$POLL_BIN
EOF

# OnActiveSec schedules from each timer activation so user-manager restarts
# still fire. OnBootSec alone can leave NextElapseUSecMonotonic=infinity.
cat > "$POLL_TIMER" <<EOF
[Unit]
Description=Poll Grok Build billing usage every minute

[Timer]
OnActiveSec=30s
OnUnitActiveSec=1m
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

mkdir -p "$REAL_HOME/.config/autostart"
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Grok Rate Indicator
Comment=Grok Build credit usage indicator for GNOME
Exec=$STARTER
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

mkdir -p "$APPLICATIONS_DIR"
cat > "$APP_LAUNCHER" <<EOF
[Desktop Entry]
Type=Application
Name=Grok Rate Indicator
Comment=Show Grok Build monthly credit usage in the top bar
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
echo "Poller: $POLL_BIN"
echo "Starter: $STARTER"
echo "Service: $SERVICE"
echo "Poll timer: $POLL_TIMER"
echo "Autostart: $AUTOSTART"
echo "App launcher: $APP_LAUNCHER"

read -r -p "Start now? (y/N) " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user enable --now grok-rate-poll.timer
        "$STARTER"
        echo "Started Grok Rate Indicator through user service."
        # Seed cache immediately so the tray has data before the first timer fire.
        "$POLL_BIN" || echo "Warning: initial billing poll failed (check grok login)."
    else
        "$POLL_BIN" || true
        "$BIN" &
        echo "Started Grok Rate Indicator (PID $!)."
    fi
fi
