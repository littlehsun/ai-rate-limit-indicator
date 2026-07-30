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
SERVICE_DIR="$REAL_HOME/.config/systemd/user"
SERVICE="$SERVICE_DIR/rate-limit-indicator.service"

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
cp "$SCRIPT_DIR/indicator.py" "$APP_DIR/indicator.py"
cp "$SCRIPT_DIR/assets/"* "$APP_DIR/assets/"
cp "$ROOT_DIR/providers/codex/ubuntu-indicator/codex_rate.py" "$COLLECTOR_DIR/codex_rate.py"
cp "$ROOT_DIR/providers/codex/ubuntu-indicator/wham.py" "$COLLECTOR_DIR/wham.py"
cp "$ROOT_DIR/providers/grok/ubuntu-indicator/grok_rate.py" "$COLLECTOR_DIR/grok_rate.py"
cp "$ROOT_DIR/providers/gemini/ubuntu-indicator/gemini_rate.py" "$COLLECTOR_DIR/gemini_rate.py"
chmod +x "$APP_DIR/cli.py" "$APP_DIR/indicator.py"

cat > "$BIN" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/indicator.py" "\$@"
EOF
chmod +x "$BIN"

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

systemctl --user daemon-reload 2>/dev/null || true
echo "Unified indicator: $BIN"
echo "Unified service: $SERVICE"
