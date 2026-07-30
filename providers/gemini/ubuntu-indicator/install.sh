#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPINDICATOR_PACKAGE="gir1.2-appindicator3-0.1"

if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else
    REAL_HOME="$HOME"
fi

APP_DIR="$REAL_HOME/.local/share/gemini-rate-indicator"
BIN="$REAL_HOME/.local/bin/gemini-rate-indicator"
POLL_BIN="$REAL_HOME/.local/bin/gemini-rate-poll"
STARTER="$REAL_HOME/.local/bin/gemini-rate-indicator-start"
AUTOSTART="$REAL_HOME/.config/autostart/gemini-rate-indicator.desktop"
APPLICATIONS_DIR="$REAL_HOME/.local/share/applications"
APP_LAUNCHER="$APPLICATIONS_DIR/gemini-rate-indicator.desktop"
SERVICE_DIR="$REAL_HOME/.config/systemd/user"
SERVICE="$SERVICE_DIR/gemini-rate-indicator.service"
POLL_SERVICE="$SERVICE_DIR/gemini-rate-poll.service"
POLL_TIMER="$SERVICE_DIR/gemini-rate-poll.timer"
CACHE_DIR="$REAL_HOME/.cache/gemini-rate-indicator"
CONFIG_DIR="$REAL_HOME/.config/gemini-rate-indicator"
OAUTH_ENV="$CONFIG_DIR/oauth.env"

echo "=== Gemini Rate Indicator for GNOME ==="

echo "[1/5] Checking AppIndicator dependency..."
if ! python3 -c "import gi; gi.require_version('AppIndicator3','0.1'); from gi.repository import AppIndicator3" 2>/dev/null; then
    if command -v apt-get >/dev/null 2>&1; then
        echo "Installing $APPINDICATOR_PACKAGE..."
        sudo apt-get update
        sudo apt-get install -y "$APPINDICATOR_PACKAGE"
    else
        echo "Missing AppIndicator3 Python bindings."
        exit 1
    fi
fi

echo "[2/5] Installing files..."
mkdir -p "$APP_DIR/assets" "$REAL_HOME/.local/bin" "$SERVICE_DIR" "$CACHE_DIR" "$CONFIG_DIR"
chmod 700 "$CACHE_DIR" "$CONFIG_DIR"
cp "$SCRIPT_DIR/indicator.py" "$APP_DIR/indicator.py"
cp "$SCRIPT_DIR/gemini_rate.py" "$APP_DIR/gemini_rate.py"
cp "$SCRIPT_DIR/assets/gemini-logo.svg" "$APP_DIR/assets/gemini-logo.svg"
chmod +x "$APP_DIR/indicator.py" "$APP_DIR/gemini_rate.py"

cat > "$BIN" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/indicator.py" "\$@"
EOF
chmod +x "$BIN"

cat > "$POLL_BIN" <<EOF
#!/usr/bin/env bash
if [[ -f "$OAUTH_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$OAUTH_ENV"
    set +a
fi
exec python3 "$APP_DIR/gemini_rate.py" --once "\$@"
EOF
chmod +x "$POLL_BIN"

cat > "$STARTER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
systemctl --user import-environment DISPLAY XAUTHORITY WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS DESKTOP_SESSION GDMSESSION GNOME_SHELL_SESSION_MODE || true
systemctl --user daemon-reload || true
systemctl --user restart gemini-rate-poll.timer || true
exec systemctl --user restart gemini-rate-indicator.service
EOF
chmod +x "$STARTER"

echo "[3/5] Setting user service, timer, and autostart..."
if [[ ! -e "$OAUTH_ENV" ]]; then
    cat > "$OAUTH_ENV" <<EOF
# Optional local installed-app OAuth values used only when Gemini CLI's cached
# access token expires. Keep this file private and never commit it.
# GEMINI_OAUTH_CLIENT_ID=
# GEMINI_OAUTH_CLIENT_SECRET=
EOF
fi
chmod 600 "$OAUTH_ENV"

cat > "$SERVICE" <<EOF
[Unit]
Description=Gemini Rate Indicator
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
Description=Poll Gemini CLI quota cache
Documentation=https://github.com/google-gemini/gemini-cli

[Service]
Type=oneshot
EnvironmentFile=-$OAUTH_ENV
ExecStart=$POLL_BIN
EOF

cat > "$POLL_TIMER" <<EOF
[Unit]
Description=Poll Gemini CLI quota every minute

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
Name=Gemini Rate Indicator
Comment=Gemini CLI quota indicator for GNOME
Exec=$STARTER
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

mkdir -p "$APPLICATIONS_DIR"
cat > "$APP_LAUNCHER" <<EOF
[Desktop Entry]
Type=Application
Name=Gemini Rate Indicator
Comment=Show Gemini CLI quota in the top bar
Exec=$STARTER
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;
StartupNotify=false
EOF
chmod 644 "$APP_LAUNCHER"
systemctl --user daemon-reload 2>/dev/null || true

echo "[4/5] Checking Gemini CLI credentials..."
if [[ -f "$REAL_HOME/.gemini/oauth_creds.json" ]]; then
    echo "Gemini CLI OAuth credentials found."
else
    echo "Warning: $REAL_HOME/.gemini/oauth_creds.json was not found."
    echo "Sign in with the official Gemini CLI before polling quota."
fi

echo "[5/5] Installed."
echo "Binary: $BIN"
echo "Poller: $POLL_BIN"
echo "Service: $SERVICE"
echo "Poll timer: $POLL_TIMER"
echo "OAuth env: $OAUTH_ENV"

read -r -p "Start now? (y/N) " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    systemctl --user enable --now gemini-rate-poll.timer
    "$STARTER"
    "$POLL_BIN" || echo "Warning: initial Gemini quota poll failed."
fi
