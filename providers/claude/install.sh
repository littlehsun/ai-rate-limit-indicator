#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 取得真實使用者的 home（相容 sudo 執行）
if [[ -n "$SUDO_USER" ]]; then
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_HOME="$HOME"
fi

APP_DIR="$REAL_HOME/.local/share/claude-rate-indicator"
BIN="$REAL_HOME/.local/bin/claude-rate-indicator"
AUTOSTART="$REAL_HOME/.config/autostart/claude-rate-indicator.desktop"
SERVICE_DIR="$REAL_HOME/.config/systemd/user"
SERVICE="$SERVICE_DIR/claude-rate-indicator.service"

echo "=== Claude Rate Limit Indicator 安裝 ==="

# 1. 相依套件
echo "[1/4] 檢查相依套件..."
if ! python3 -c "import gi; gi.require_version('AppIndicator3','0.1'); from gi.repository import AppIndicator3" 2>/dev/null; then
    echo "  安裝 gir1.2-ayatana-appindicator3-0.1..."
    sudo apt-get install -y gir1.2-ayatana-appindicator3-0.1
else
    echo "  AppIndicator3 已存在"
fi

# 2. 複製執行檔
echo "[2/4] 安裝執行檔..."
mkdir -p "$APP_DIR/assets" "$REAL_HOME/.local/bin"
cp "$SCRIPT_DIR/indicator.py" "$APP_DIR/indicator.py"
cp "$SCRIPT_DIR/assets/claude-logo.svg" "$APP_DIR/assets/claude-logo.svg"
cat > "$BIN" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/indicator.py" "\$@"
EOF
chmod +x "$BIN"

# 3. 安裝圖示
echo "[3/4] 安裝圖示..."
ICON_DEST="$REAL_HOME/.local/share/icons/claude-rate-indicator"
mkdir -p "$ICON_DEST"
cp "$SCRIPT_DIR/icons/"*.svg "$ICON_DEST/"

# 4. Autostart
echo "[4/4] 設定開機自動啟動..."
mkdir -p "$REAL_HOME/.config/autostart" "$SERVICE_DIR"
cat > "$SERVICE" <<EOF
[Unit]
Description=Claude Rate Limit Indicator
After=graphical-session.target

[Service]
Type=simple
ExecStart=$BIN
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Claude Rate Indicator
Comment=Claude Code rate limit indicator for GNOME
Exec=systemctl --user start claude-rate-indicator.service
Icon=$ICON_DEST/claude-rate-green.svg
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
systemctl --user daemon-reload 2>/dev/null || true

echo ""
echo "安裝完成！"
echo ""
echo "啟動指示器："
echo "  $BIN &"
echo ""
echo "（下次登入會自動啟動）"
echo ""

# 詢問是否立即啟動
read -r -p "現在啟動？ (y/N) " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    if systemctl --user enable claude-rate-indicator.service \
        && systemctl --user restart claude-rate-indicator.service; then
        echo "指示器已透過 systemd user service 啟動"
    else
        LOG_DIR="$REAL_HOME/.cache/claude-rate-indicator"
        mkdir -p "$LOG_DIR"
        nohup "$BIN" >"$LOG_DIR/indicator.log" 2>&1 &
        echo "指示器已啟動（PID $!）"
    fi
fi
