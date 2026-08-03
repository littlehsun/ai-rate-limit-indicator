#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_SUPPORT="$HOME/Library/Application Support/RateLimitIndicator"
BACKEND_DIR="$APP_SUPPORT/backend"
COLLECTOR_DIR="$BACKEND_DIR/collectors"
ASSET_DIR="$APP_SUPPORT/assets"
APP_DIR="${RATE_LIMIT_INDICATOR_APP_DIR:-$HOME/Applications/Rate Limit Indicator.app}"
DEFAULT_CONFIG_FILE="$HOME/.config/rate-limit-indicator/providers.env"
CONFIG_FILE="${RATE_LIMIT_INDICATOR_CONFIG:-$DEFAULT_CONFIG_FILE}"
CODEX_HOME_OVERRIDE="${CODEX_HOME:-}"
CLAUDE_CONFIG_DIR_OVERRIDE="${CLAUDE_CONFIG_DIR:-}"
CLAUDE_OAUTH_CREDENTIALS_FILE_OVERRIDE="${CLAUDE_OAUTH_CREDENTIALS_FILE:-}"
GROK_HOME_OVERRIDE="${GROK_HOME:-}"
GROK_RATE_CACHE_OVERRIDE="${GROK_RATE_CACHE:-}"
GROK_RATE_BILLING_URL_OVERRIDE="${GROK_RATE_BILLING_URL:-}"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/RateLimitIndicator"
LEGACY_CODEX_PLIST="$LAUNCH_AGENTS/com.hsun.codex-rate-menubar.plist"
LEGACY_CODEX_ENV="$HOME/.config/codex-rate-indicator/wham.env"
LEGACY_LOGIN_MIGRATION_MARKER="$APP_SUPPORT/migrate-legacy-launch-at-login"

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
canonicalize_path() {
    python3 -c 'import os, sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))' "$1"
}
canonicalize_path_list() {
    python3 -c 'import os, sys; print(",".join(os.path.realpath(os.path.expanduser(item.strip())) for item in sys.argv[1].split(",") if item.strip()))' "$1"
}
normalize_config_value() {
    local value
    value="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
    if [[ ${#value} -ge 2 ]]; then
        case "$value" in
            \"*\"|\'*\') value="${value:1:${#value}-2}" ;;
        esac
    fi
    printf '%s' "$value"
}
PYTHON_BIN="$(command -v python3)"
if [[ "$PYTHON_BIN" != /* || ! -x "$PYTHON_BIN" ]]; then
    echo "python3 must resolve to an executable absolute path: $PYTHON_BIN" >&2
    exit 1
fi
APP_DIR="$(canonicalize_path "$APP_DIR")"
APP_EXECUTABLE="$APP_DIR/Contents/MacOS/RateLimitIndicatorMac"
STAGED_APP_EXECUTABLE="$APP_DIR/Contents/MacOS/.RateLimitIndicatorMac.new"
DEFAULT_CONFIG_FILE="$(canonicalize_path "$DEFAULT_CONFIG_FILE")"
CONFIG_FILE="$(canonicalize_path "$CONFIG_FILE")"
CONFIG_DIR="$(dirname "$CONFIG_FILE")"
if [[ -n "$CODEX_HOME_OVERRIDE" ]]; then
    CODEX_HOME_OVERRIDE="$(canonicalize_path "$CODEX_HOME_OVERRIDE")"
fi
if [[ -n "$CLAUDE_CONFIG_DIR_OVERRIDE" ]]; then
    CLAUDE_CONFIG_DIR_OVERRIDE="$(canonicalize_path_list "$CLAUDE_CONFIG_DIR_OVERRIDE")"
fi
if [[ -n "$CLAUDE_OAUTH_CREDENTIALS_FILE_OVERRIDE" ]]; then
    CLAUDE_OAUTH_CREDENTIALS_FILE_OVERRIDE="$(canonicalize_path "$CLAUDE_OAUTH_CREDENTIALS_FILE_OVERRIDE")"
fi
if [[ -n "$GROK_HOME_OVERRIDE" ]]; then
    GROK_HOME_OVERRIDE="$(canonicalize_path "$GROK_HOME_OVERRIDE")"
fi
if [[ -n "$GROK_RATE_CACHE_OVERRIDE" ]]; then
    GROK_RATE_CACHE_OVERRIDE="$(canonicalize_path "$GROK_RATE_CACHE_OVERRIDE")"
fi

echo "=== Unified Rate Limit Indicator for macOS ==="
legacy_login_was_enabled=false
if launchctl print "gui/$UID/com.hsun.codex-rate-menubar" >/dev/null 2>&1; then
    legacy_login_was_enabled=true
fi

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
legacy_codex_source=""
if [[ -f "$LEGACY_CODEX_ENV" ]]; then
    legacy_codex_source="$(
        sed -n -E 's/^[[:space:]]*CODEX_RATE_SOURCE[[:space:]]*=[[:space:]]*([^#[:space:]]+).*$/\1/p' \
            "$LEGACY_CODEX_ENV" | tail -n 1
    )"
fi
legacy_codex_source="$(normalize_config_value "$legacy_codex_source")"
case "$legacy_codex_source" in
    auto|wham) ;;
    *) legacy_codex_source=local ;;
esac
if [[ ! -e "$CONFIG_FILE" ]]; then
    {
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
    config_file_created=true
fi
if ! grep -Eq '^[[:space:]]*CODEX_RATE_SOURCE[[:space:]]*=' "$CONFIG_FILE"; then
    printf '\n# local (default), auto, or wham; auto/wham opt in to network polling.\n' >> "$CONFIG_FILE"
    printf 'CODEX_RATE_SOURCE=%s\n' "$legacy_codex_source" >> "$CONFIG_FILE"
fi
if [[ "$config_file_created" == true || "$CONFIG_FILE" == "$DEFAULT_CONFIG_FILE" ]]; then
    chmod 600 "$CONFIG_FILE"
fi

echo "[3/5] Building the native SwiftUI app..."
swift build --package-path "$SCRIPT_DIR" -c release --product RateLimitIndicatorMac
BIN_DIR="$(swift build --package-path "$SCRIPT_DIR" -c release --show-bin-path)"
mkdir -p "$APP_DIR/Contents/MacOS"
cp "$BIN_DIR/RateLimitIndicatorMac" "$STAGED_APP_EXECUTABLE"
chmod +x "$STAGED_APP_EXECUTABLE"
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
/usr/bin/plutil -insert RateLimitIndicatorPythonPath -string "$PYTHON_BIN" \
    "$APP_DIR/Contents/Info.plist"
if [[ -n "$CODEX_HOME_OVERRIDE" ]]; then
    /usr/bin/plutil -insert RateLimitIndicatorCodexHome -string "$CODEX_HOME_OVERRIDE" \
        "$APP_DIR/Contents/Info.plist"
fi
if [[ -n "$CLAUDE_CONFIG_DIR_OVERRIDE" ]]; then
    /usr/bin/plutil -insert RateLimitIndicatorClaudeConfigDir \
        -string "$CLAUDE_CONFIG_DIR_OVERRIDE" "$APP_DIR/Contents/Info.plist"
fi
if [[ -n "$CLAUDE_OAUTH_CREDENTIALS_FILE_OVERRIDE" ]]; then
    /usr/bin/plutil -insert RateLimitIndicatorClaudeOAuthCredentialsFile \
        -string "$CLAUDE_OAUTH_CREDENTIALS_FILE_OVERRIDE" "$APP_DIR/Contents/Info.plist"
fi
if [[ -n "$GROK_HOME_OVERRIDE" ]]; then
    /usr/bin/plutil -insert RateLimitIndicatorGrokHome -string "$GROK_HOME_OVERRIDE" \
        "$APP_DIR/Contents/Info.plist"
fi
if [[ -n "$GROK_RATE_CACHE_OVERRIDE" ]]; then
    /usr/bin/plutil -insert RateLimitIndicatorGrokRateCache \
        -string "$GROK_RATE_CACHE_OVERRIDE" "$APP_DIR/Contents/Info.plist"
fi

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
    /usr/bin/plutil -insert EnvironmentVariables.RATE_LIMIT_INDICATOR_PYTHON \
        -string "$PYTHON_BIN" "$plist"
    if [[ "$provider" == "grok" ]]; then
        if [[ -n "$GROK_HOME_OVERRIDE" ]]; then
            /usr/bin/plutil -insert EnvironmentVariables.GROK_HOME \
                -string "$GROK_HOME_OVERRIDE" "$plist"
        fi
        if [[ -n "$GROK_RATE_CACHE_OVERRIDE" ]]; then
            /usr/bin/plutil -insert EnvironmentVariables.GROK_RATE_CACHE \
                -string "$GROK_RATE_CACHE_OVERRIDE" "$plist"
        fi
        if [[ -n "$GROK_RATE_BILLING_URL_OVERRIDE" ]]; then
            /usr/bin/plutil -insert EnvironmentVariables.GROK_RATE_BILLING_URL \
                -string "$GROK_RATE_BILLING_URL_OVERRIDE" "$plist"
        fi
    fi
    launchctl bootout "gui/$UID" "$plist" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$plist"
done

echo "Preparing legacy Codex menu-bar migration..."
if [[ "$legacy_login_was_enabled" == true ]]; then
    touch "$LEGACY_LOGIN_MIGRATION_MARKER"
    chmod 600 "$LEGACY_LOGIN_MIGRATION_MARKER"
else
    launchctl bootout "gui/$UID/com.hsun.codex-rate-menubar" 2>/dev/null || true
    launchctl bootout "gui/$UID" "$LEGACY_CODEX_PLIST" 2>/dev/null || true
    rm -f "$LEGACY_CODEX_PLIST"
fi

app_was_running=false
app_process_ids() {
    local pid
    local command
    ps -U "$UID" -ww -o pid= 2>/dev/null | while read -r pid; do
        command="$(ps -ww -p "$pid" -o command= 2>/dev/null || true)"
        if [[ "$command" == "$APP_EXECUTABLE" ]]; then
            printf '%s\n' "$pid"
        fi
    done
}
app_is_running() {
    [[ -n "$(app_process_ids)" ]]
}
if app_is_running; then
    app_was_running=true
    osascript -e 'tell application id "com.hsun.rate-limit-indicator" to quit' \
        >/dev/null 2>&1 || true
    for _ in {1..20}; do
        app_is_running || break
        sleep 0.25
    done
    if app_is_running; then
        while read -r pid; do
            kill "$pid" 2>/dev/null || true
        done < <(app_process_ids)
    fi
    for _ in {1..20}; do
        app_is_running || break
        sleep 0.25
    done
    if app_is_running; then
        echo "Could not stop the running Rate Limit Indicator app." >&2
        exit 1
    fi
fi
mv -f "$STAGED_APP_EXECUTABLE" "$APP_EXECUTABLE"

echo "[5/5] Installed."
echo "App: $APP_DIR"
echo "Config: $CONFIG_FILE"
echo "Backend: $BACKEND_DIR"
echo
if [[ "$app_was_running" == true ]]; then
    open "$APP_DIR"
else
    read -r -p "Open Rate Limit Indicator now? (Y/n) " answer || answer=""
    if [[ ! "$answer" =~ ^[Nn]$ ]]; then
        open "$APP_DIR"
    fi
fi
