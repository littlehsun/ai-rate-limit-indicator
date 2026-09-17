#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else
    REAL_HOME="$HOME"
fi

APP_DIR="$REAL_HOME/.local/share/rate-limit-indicator/unified"
COLLECTOR_DIR="$APP_DIR/collectors"
BIN="$REAL_HOME/.local/bin/rate-limit-indicator"
CLI_BIN="$REAL_HOME/.local/bin/rate-limit-usage"
SERVICE_DIR="$REAL_HOME/.config/systemd/user"
SERVICE="$SERVICE_DIR/rate-limit-indicator.service"
PUBLISH_SERVICE="$SERVICE_DIR/rate-limit-publish.service"

if ! python3 -c "import gi; gi.require_version('AppIndicator3','0.1'); from gi.repository import AppIndicator3" 2>/dev/null; then
    echo "Missing AppIndicator3 Python bindings." >&2
    exit 1
fi

mkdir -p "$APP_DIR/assets" "$COLLECTOR_DIR" "$REAL_HOME/.local/bin" "$SERVICE_DIR"
cp "$SCRIPT_DIR/models.py" "$APP_DIR/models.py"
cp "$SCRIPT_DIR/adapters.py" "$APP_DIR/adapters.py"
cp "$SCRIPT_DIR/agy_rate.py" "$APP_DIR/agy_rate.py"
cp "$SCRIPT_DIR/claude_oauth.py" "$APP_DIR/claude_oauth.py"
cp "$SCRIPT_DIR/cli.py" "$APP_DIR/cli.py"
cp "$SCRIPT_DIR/publish.py" "$APP_DIR/publish.py"
cp "$SCRIPT_DIR/indicator.py" "$APP_DIR/indicator.py"
cp "$SCRIPT_DIR/float_widget.py" "$APP_DIR/float_widget.py"
cp "$SCRIPT_DIR/assets/"* "$APP_DIR/assets/"

# The floating desktop widget and the data layer it reads. It lands beside the
# tray so that float_widget.py finds it without knowing where this repository
# was checked out, and it brings its palettes with it: a theme named in
# config.ini that was left behind in the repository is an error on startup.
cp "$ROOT_DIR/dashboard/usage_float.py" "$APP_DIR/usage_float.py"
cp "$ROOT_DIR/dashboard/usage_monitor.py" "$APP_DIR/usage_monitor.py"
mkdir -p "$APP_DIR/themes"
cp "$ROOT_DIR/dashboard/themes/"*.ini "$APP_DIR/themes/"
# config.ini carries the user's theme, language and provider choices, so an
# upgrade must not overwrite the one they edited.
if [[ ! -f "$APP_DIR/config.ini" ]]; then
    cp "$ROOT_DIR/dashboard/config.ini" "$APP_DIR/config.ini"
fi
cp "$ROOT_DIR/providers/codex/ubuntu-indicator/codex_rate.py" "$COLLECTOR_DIR/codex_rate.py"
cp "$ROOT_DIR/providers/codex/ubuntu-indicator/wham.py" "$COLLECTOR_DIR/wham.py"
cp "$ROOT_DIR/providers/grok/ubuntu-indicator/grok_rate.py" "$COLLECTOR_DIR/grok_rate.py"
cp "$ROOT_DIR/providers/gemini/ubuntu-indicator/gemini_rate.py" "$COLLECTOR_DIR/gemini_rate.py"
chmod +x "$APP_DIR/cli.py" "$APP_DIR/publish.py" "$APP_DIR/indicator.py" \
    "$APP_DIR/usage_float.py"

cat > "$BIN" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/indicator.py" "\$@"
EOF
chmod +x "$BIN"

# The same backend the tray reads, reachable from a terminal. Only the path
# differs between platforms; the command and its flags do not.
cat > "$CLI_BIN" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/cli.py" "\$@"
EOF
chmod +x "$CLI_BIN"

cat > "$SERVICE" <<EOF
[Unit]
Description=Unified AI Rate Limit Indicator
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

# Restart=on-failure covers the boot ordering: publish.py exits non-zero when
# no Tailscale address exists yet, and systemd simply tries again.
cat > "$PUBLISH_SERVICE" <<EOF
[Unit]
Description=Serve AI rate limit usage on the Tailscale network
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/env python3 $APP_DIR/publish.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

# A launcher for the widget: this is what the app grid lists, what the dock
# pins, and -- through StartupWMClass matching the widget's program name --
# what a minimised widget is clicked back out of the dock with.
DESKTOP_DIR="$REAL_HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/rate-limit-float.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AI Usage Float
Comment=Floating AI usage widget
Exec=/usr/bin/env python3 $APP_DIR/usage_float.py
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;
StartupWMClass=rate-limit-float
EOF
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

systemctl --user daemon-reload 2>/dev/null || true
echo "Unified indicator: $BIN"
echo "Usage CLI: $CLI_BIN"
echo "Unified service: $SERVICE"
echo "Publisher service: $PUBLISH_SERVICE (set MOBILE_PUBLISH=true to enable)"
echo "Desktop widget: $DESKTOP_DIR/rate-limit-float.desktop (tray menu: Desktop widget)"
