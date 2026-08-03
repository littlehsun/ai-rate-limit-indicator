#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_SUPPORT="$HOME/Library/Application Support/RateLimitIndicator"
BACKEND_DIR="$APP_SUPPORT/backend"
COLLECTOR_DIR="$BACKEND_DIR/collectors"
ASSET_DIR="$APP_SUPPORT/assets"
APP_DIR="${RATE_LIMIT_INDICATOR_APP_DIR:-$HOME/Applications/Rate Limit Indicator.app}"
APP_EXECUTABLE="$APP_DIR/Contents/MacOS/RateLimitIndicatorMac"
DEFAULT_CONFIG_FILE="$HOME/.config/rate-limit-indicator/providers.env"
CONFIG_FILE="${RATE_LIMIT_INDICATOR_CONFIG:-$DEFAULT_CONFIG_FILE}"
CONFIG_DIR="$(dirname "$CONFIG_FILE")"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/RateLimitIndicator"
LEGACY_CODEX_PLIST="$LAUNCH_AGENTS/com.hsun.codex-rate-menubar.plist"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "The macOS installer must run on macOS." >&2
    exit 1
fi
if ! command -v swift >/dev/null 2>&1; then
    echo "Swift is required. Install Xcode Command Line Tools first:" >&2
    echo "  xcode-select --install" >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required for the shared provider backend." >&2
    exit 1
fi

echo "=== Unified Rate Limit Indicator for macOS ==="
echo "Removing the legacy Codex menu-bar LaunchAgent..."
launchctl bootout "gui/$UID/com.hsun.codex-rate-menubar" 2>/dev/null || true
launchctl bootout "gui/$UID" "$LEGACY_CODEX_PLIST" 2>/dev/null || true
rm -f "$LEGACY_CODEX_PLIST"

echo "[1/5] Installing the shared provider backend..."
mkdir -p "$BACKEND_DIR" "$COLLECTOR_DIR" "$ASSET_DIR"
cp "$ROOT_DIR/unified-indicator/models.py" "$BACKEND_DIR/models.py"
cp "$ROOT_DIR/unified-indicator/adapters.py" "$BACKEND_DIR/adapters.py"
cp "$ROOT_DIR/unified-indicator/agy_rate.py" "$BACKEND_DIR/agy_rate.py"
cp "$ROOT_DIR/unified-indicator/claude_oauth.py" "$BACKEND_DIR/claude_oauth.py"
cp "$ROOT_DIR/unified-indicator/cli.py" "$BACKEND_DIR/cli.py"
cp "$ROOT_DIR/providers/codex/ubuntu-indicator/codex_rate.py" "$COLLECTOR_DIR/codex_rate.py"
cp "$ROOT_DIR/providers/codex/ubuntu-indicator/wham.py" "$COLLECTOR_DIR/wham.py"
cp "$ROOT_DIR/providers/grok/ubuntu-indicator/grok_rate.py" "$COLLECTOR_DIR/grok_rate.py"
cp "$ROOT_DIR/providers/gemini/ubuntu-indicator/gemini_rate.py" "$COLLECTOR_DIR/gemini_rate.py"
cp "$SCRIPT_DIR/poll-provider.sh" "$APP_SUPPORT/poll-provider.sh"
chmod +x "$BACKEND_DIR/cli.py" "$APP_SUPPORT/poll-provider.sh"

cp "$ROOT_DIR/unified-indicator/assets/codex-logo.png" "$ASSET_DIR/codex-logo.png"
cp "$ROOT_DIR/unified-indicator/assets/claude-logo.svg" "$ASSET_DIR/claude-logo.svg"
cp "$ROOT_DIR/unified-indicator/assets/grok-logo.png" "$ASSET_DIR/grok-logo.png"
cp "$ROOT_DIR/unified-indicator/assets/gemini-logo.svg" "$ASSET_DIR/gemini-logo.svg"

echo "[2/5] Creating the shared display configuration..."
config_dir_created=false
if [[ ! -d "$CONFIG_DIR" ]]; then
    mkdir -p "$CONFIG_DIR"
    config_dir_created=true
fi
if [[ "$config_dir_created" == true && "$CONFIG_FILE" == "$DEFAULT_CONFIG_FILE" ]]; then
    chmod 700 "$CONFIG_DIR"
fi
config_file_created=false
if [[ ! -e "$CONFIG_FILE" ]]; then
    {
        echo "CODEX=true"
        echo "CLAUDE=true"
        echo "GROK=true"
        echo "GEMINI=true"
        echo "DISPLAY_MODE=auto"
        echo "DISPLAY_PROVIDERS=codex,claude,grok,gemini"
        echo "DROPDOWN_PROVIDERS=codex,claude,grok,gemini"
        echo "PROVIDER_ORDER=codex,claude,grok,gemini"
    } > "$CONFIG_FILE"
    config_file_created=true
fi
if [[ "$config_file_created" == true || "$CONFIG_FILE" == "$DEFAULT_CONFIG_FILE" ]]; then
    chmod 600 "$CONFIG_FILE"
fi

echo "[3/5] Building the native SwiftUI app..."
swift build --package-path "$SCRIPT_DIR" -c release --product RateLimitIndicatorMac
BIN_DIR="$(swift build --package-path "$SCRIPT_DIR" -c release --show-bin-path)"
mkdir -p "$APP_DIR/Contents/MacOS"
cp "$BIN_DIR/RateLimitIndicatorMac" "$APP_EXECUTABLE"
chmod +x "$APP_EXECUTABLE"
cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>RateLimitIndicatorMac</string>
  <key>CFBundleIdentifier</key>
  <string>com.hsun.rate-limit-indicator</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>Rate Limit Indicator</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>LSUIElement</key>
  <true/>
</dict>
</plist>
PLIST
/usr/bin/plutil -insert RateLimitIndicatorConfigPath -string "$CONFIG_FILE" \
    "$APP_DIR/Contents/Info.plist"

echo "[4/5] Installing Codex and Grok polling LaunchAgents..."
mkdir -p "$LAUNCH_AGENTS" "$LOG_DIR"
for provider in codex grok; do
    label="com.hsun.rate-limit-indicator.$provider-poll"
    plist="$LAUNCH_AGENTS/$label.plist"
    cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$APP_SUPPORT/poll-provider.sh</string>
    <string>$provider</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>60</integer>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/$provider-poll.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/$provider-poll.err.log</string>
</dict>
</plist>
PLIST
    /usr/bin/plutil -insert EnvironmentVariables -dictionary "$plist"
    /usr/bin/plutil -insert EnvironmentVariables.RATE_LIMIT_INDICATOR_CONFIG \
        -string "$CONFIG_FILE" "$plist"
    launchctl bootout "gui/$UID" "$plist" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$plist"
done

echo "[5/5] Installed."
echo "App: $APP_DIR"
echo "Config: $CONFIG_FILE"
echo "Backend: $BACKEND_DIR"
echo
read -r -p "Open Rate Limit Indicator now? (Y/n) " answer || answer=""
if [[ ! "$answer" =~ ^[Nn]$ ]]; then
    open "$APP_DIR"
fi
