# Rate Limit Indicator

[English](README.md)

這是一個整合 Codex、Claude Code、Grok Build 與 Gemini/AGY 用量的
rate-limit indicator。

專案保留各 provider 原本已驗證的資料來源與 collector，將結果轉換成統一
snapshot，再由單一 GNOME AppIndicator 顯示 5H、7D、reset 倒數與詳細資訊。

## 主要功能

- 一個 GNOME indicator 顯示所有啟用的 provider。
- Auto 模式會依最近有變化的 7D 用量自動選擇 provider。
- Custom 模式可多選 provider，並自訂顯示順序。
- Indicator 與下拉選單可以分別決定是否顯示。
- 統一使用 5H、7D 命名與顏色規則。
- 顯示 reset 絕對時間與倒數。
- Codex reset credits 可展開查看每筆到期時間。
- 登入時只需執行一個指令，啟用項目由本機設定檔控制。
- 提供文字與 JSON 格式的統一 usage CLI。

## 資料來源

| Provider | 資料來源 |
| --- | --- |
| Codex | 本機 Codex rollout 資料；可選用 ChatGPT quota API |
| Claude | Claude OAuth usage API |
| Grok | Grok CLI billing API，顯示 7D 與 Monthly |
| Gemini | AGY localhost quota API；暫時無法使用時保留最後一份 AGY snapshot |

Gemini 會優先讀取 AGY `/usage` 同一個 `RetrieveUserQuotaSummary`
localhost endpoint，顯示 Gemini 與 Claude/GPT 各自的 5H、7D 用量。

## 安裝

安裝全部 provider 與統一 indicator：

```bash
./install.sh all
```

只安裝或修復統一 manager：

```bash
./install.sh manager
```

安裝單一 provider：

```bash
./install.sh codex
./install.sh claude
./install.sh grok
./install.sh gemini
```

安裝後，登入時會執行：

```text
~/.local/bin/rate-limit-indicators start
```

## 設定

設定檔位於 repository 外：

```text
~/.config/rate-limit-indicator/providers.env
```

完整範例：

```bash
CODEX=true
CLAUDE=true
GROK=true
GEMINI=true

# auto 或 custom
DISPLAY_MODE=custom

# Custom 模式顯示在 panel 的 provider
DISPLAY_PROVIDERS=codex,grok,gemini

# 顯示在下拉選單的 provider
DROPDOWN_PROVIDERS=codex,claude,grok,gemini

# Panel 與下拉選單共用的順序
PROVIDER_ORDER=codex,grok,claude,gemini
```

`DISPLAY_MODE=auto` 會選擇最近有更新且 7D 數字變化最大的 provider；
沒有新變化時會維持目前選擇。

`DISPLAY_MODE=custom` 會同時顯示 `DISPLAY_PROVIDERS` 中勾選的 provider，
並依 `PROVIDER_ORDER` 排序。這些設定也可以從下拉選單的
`Display settings…` 修改。

套用設定：

```bash
~/.local/bin/rate-limit-indicators apply
```

## 常用指令

```bash
~/.local/bin/rate-limit-indicators start
~/.local/bin/rate-limit-indicators stop
~/.local/bin/rate-limit-indicators status
~/.local/bin/rate-limit-indicators usage
~/.local/bin/rate-limit-indicators usage --json
~/.local/bin/rate-limit-indicators usage --provider gemini
```

## 安全性

Repository 不保存 credential、token、provider cache 或使用者設定。
Collector 只會讀取各 CLI 原本的本機認證位置；產生的設定與 cache 會放在
使用者家目錄，並使用限制權限。

AGY adapter 只會連線至 loopback address。因為 AGY 使用 localhost
self-signed certificate，TLS 驗證只會針對固定的本機 endpoint 放寬。

Claude OAuth credential 會直接讀取 Claude Code 現有的
`~/.claude/.credentials.json`；indicator 不會自行保存或更新 access token。
若沒有有效的 OAuth credential，Claude usage 會顯示為 unavailable。

## 測試

```bash
./scripts/test-all.sh
```

完整架構與設計說明請參考 [docs/architecture.md](docs/architecture.md)。
