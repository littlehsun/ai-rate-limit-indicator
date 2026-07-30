#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin/codex-rate-menubar"
PLIST="$HOME/Library/LaunchAgents/com.hsun.codex-rate-menubar.plist"

echo "=== Codex Rate Menubar for macOS ==="

if ! command -v swiftc >/dev/null 2>&1; then
    echo "swiftc not found. Install Xcode Command Line Tools first:"
    echo "  xcode-select --install"
    exit 1
fi

echo "[1/3] Compiling native menubar binary..."
mkdir -p "$HOME/.local/bin"
swiftc "$SCRIPT_DIR/CodexRateMenubar.swift" -o "$BIN" -framework AppKit
chmod +x "$BIN"

echo "[2/3] Writing LaunchAgent..."
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.hsun.codex-rate-menubar</string>
  <key>ProgramArguments</key>
  <array>
    <string>$BIN</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>/tmp/codex-rate-menubar.out.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/codex-rate-menubar.err.log</string>
</dict>
</plist>
EOF

echo "[3/3] Installed."
echo "Binary: $BIN"
echo "LaunchAgent: $PLIST"
echo "CLI check:"
echo "  $BIN --once"

read -r -p "Start now and enable LaunchAgent? (y/N) " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    launchctl bootout "gui/$UID" "$PLIST" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$PLIST"
    launchctl kickstart -k "gui/$UID/com.hsun.codex-rate-menubar"
    echo "Started Codex Rate Menubar."
fi
