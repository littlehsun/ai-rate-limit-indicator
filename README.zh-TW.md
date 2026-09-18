# Rate Limit Indicator

[English](README.md)

這是一個整合 Codex、Claude Code、Grok Build 與 Gemini/AGY 用量的
rate-limit indicator。

專案保留各 provider 原本已驗證的資料來源與 collector，將結果轉換成統一
snapshot，再由 GNOME AppIndicator 或原生 macOS SwiftUI app 顯示 5H、7D、
reset 倒數與詳細資訊。兩個平台共用相同 backend 邏輯。

## 主要功能

- 一個 GNOME indicator 顯示所有啟用的 provider。
- 一個使用相同 backend 的原生 macOS menu-bar app。
- Linux 桌面懸浮視窗，可從 indicator 選單開關，見
  [dashboard/README.md](dashboard/README.md)。
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
| Grok | Grok CLI billing API，顯示目前的 7D 額度與各產品用量 |
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

在 macOS 14 以上安裝統一 menu-bar app：

```bash
./install.sh macos
```

macOS 版只負責 SwiftUI 呈現；Codex、Claude、Grok 與 Gemini/AGY 都直接使用
現有的 normalized Python backend。Codex 與 Grok collector 會透過
LaunchAgent 排程，設定頁可使用 `Launch at login` 控制 app 登入啟動。

Linux 安裝後，登入時會執行：

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

# Codex 資料來源：local 不會發出網路請求；auto/wham 代表明確同意使用
# 現有 Codex token 輪詢未公開的 ChatGPT quota endpoints。
CODEX_RATE_SOURCE=local

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
self-signed certificate，TLS 驗證只會針對固定的本機 endpoint 放寬；每個 port
都會分別以 HTTPS 與純 HTTP 各試一次，因為 AGY 並非每個 port 都講同一種協定。

AGY 現在會拒絕沒有帶 CSRF token 的本機請求。該 token 每次執行重新產生、不寫入
磁碟，也不接受外部指定，因此 adapter 從 AGY 自己啟動的行程環境變數借用 ——
AGY 會把 `ANTIGRAVITY_CSRF_TOKEN` 與 `ANTIGRAVITY_LS_ADDRESS` 傳給它產生的
所有子行程。讀取其他行程的環境變數是相當強的行為，所以 adapter 只讀屬於本使用者
的行程、只找這兩個變數，而且不會記錄讀到的內容。也可以用 `AGY_CSRF_TOKEN`
自行提供。這只在 Linux 有效：它依賴 /proc，沒有 /proc 就視為沒有 token 而非
錯誤，macOS 會沿用既有 cache。完全找不到 token 時，Gemini 會顯示為缺少 token，
而不是 endpoint 掛掉。

token 會隨著鑄造它的那次 AGY 執行一起失效，但被交付 token 的行程可能多活好幾天
—— 從 AGY 開出來的 shell 會一直帶著一把再也不會被接受的 token。因此程式會收集
機器上所有的 token，較新的行程優先，並把「address 指向 AGY 目前真的在聽的 port」
那一把排到最前面。全部都被拒絕時，訊息會說這些 token 來自先前的執行，而不是丟出
HTTP 401。

可用的 token 會保留在記憶體中，直到它所屬的那個 Antigravity 行程結束為止。帶著
token 的行程會消失 —— AGY 開的 shell、它啟動的 ssh —— 沒有這層保留的話，最後一個
消失時 Gemini 就會斷掉，即使 Antigravity 還開在你面前。只存在記憶體：寫進磁碟會讓
token 活得比鑄造它的那次執行還久；Antigravity 重啟後會換新的，因為綁定的 pid 已經
不在了。

以上是 Antigravity 開著的情況。關閉時，`AGY_AUTO_START` 會改為請 CLI 印出它自己的
用量畫面，而不是打 endpoint：`agy -p /usage --output-format json`。這是 slash
command，由 CLI 內部處理 —— 回應裡 `total_tokens: 0`、不佔用任何 turn —— 所以
不需要 token、不需要監聽中的 server，也不花模型額度。代價是一個行程和將近十秒，
因此保留它取代的那個機制的兩道閘門：開關，以及冷卻時間（避免一個無法登入的 CLI
每次輪詢都換來一個行程）。啟動 Antigravity 本身已經沒有用，也不再嘗試：我們自己
啟動的 run 鑄的 token 不交給任何人，也不接受外部指定。

Claude OAuth credential 會直接讀取 Claude Code 現有的
`~/.claude/.credentials.json`；indicator 不會自行保存或更新 access token。
若沒有有效的 OAuth credential，Claude usage 會顯示為 unavailable。

## 測試

```bash
./scripts/test-all.sh
```

測試涵蓋各 provider parser、共用 adapter、GNOME UI 行為、macOS UI
整合邊界、installer 與 shell syntax；SwiftUI app 會在有 Swift 的 macOS
環境中實際編譯。
