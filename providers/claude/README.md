# Claude Rate Indicator

Claude Code 的獨立 Ubuntu / GNOME rate-limit 指示器。在頂部列顯示 Claude logo、5 小時與 7 天用量，以及 5 小時視窗的重置倒數。

```text
Claude logo  30%|72% ⟳2h15m
```

點擊圖示可查看各視窗用量、重置時間、資料更新時間，並可手動重新整理或結束程式。用量低於 70% 顯示綠色、70–89% 顯示黃色、90% 以上顯示紅色。

## 資料來源

指示器每 60 秒讀取：

```text
~/.claude/rate_limits_live.json
```

這個檔案由同層的 `claude-statusline` 專案產生：

```text
Claude Code → claude-statusline → rate_limits_live.json → claude-rate-indicator
```

## 安裝

```bash
bash install.sh
```

安裝程式會：

1. 檢查 AppIndicator3 相依套件。
2. 將程式與 Claude logo 安裝至 `~/.local/share/claude-rate-indicator/`。
3. 建立 `~/.local/bin/claude-rate-indicator` 啟動器。
4. 建立並啟用 `claude-rate-indicator.service` systemd user service。
5. 建立 GNOME autostart 項目。

## 管理服務

```bash
systemctl --user status claude-rate-indicator.service
systemctl --user restart claude-rate-indicator.service
systemctl --user stop claude-rate-indicator.service
```

查看日誌：

```bash
journalctl --user -u claude-rate-indicator.service
```

## 測試

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile indicator.py
bash -n install.sh
```

## 專案結構

```text
claude-rate-indicator/
├── indicator.py
├── install.sh
├── assets/
│   └── claude-logo.svg
├── icons/
│   ├── claude-rate-green.svg
│   ├── claude-rate-yellow.svg
│   └── claude-rate-red.svg
└── tests/
    └── test_indicator_logo.py
```
