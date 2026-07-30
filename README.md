# Rate Limit Indicator

Codex、Claude Code 與 Grok Build 的 rate-limit indicator monorepo。

目前這個整合版保留三個 provider 已驗證的資料來源與背景服務，並提供共用安裝、測試入口。三個 GNOME indicator 仍會各自顯示，避免第一階段整併改變既有行為。

## Providers

| Provider | Linux / GNOME | macOS | Data source |
| --- | --- | --- | --- |
| [Codex](providers/codex/README.md) | AppIndicator3 | Swift menu bar | Local Codex rollout files; optional ChatGPT quota API |
| [Claude](providers/claude/README.md) | AppIndicator3 | — | `~/.claude/rate_limits_live.json` |
| [Grok](providers/grok/README.md) | AppIndicator3 | — | Grok CLI billing API |

## Install

Install all GNOME indicators:

```bash
./install.sh all
```

Or install one provider:

```bash
./install.sh codex
./install.sh claude
./install.sh grok
```

Install the Codex macOS menu-bar app:

```bash
./install.sh codex-macos
```

Each provider keeps its own user service, cache, configuration, and authentication files. The repository does not contain or copy local credentials.

## Test

```bash
./scripts/test-all.sh
```

The macOS smoke test is skipped automatically when `swiftc` is unavailable.

## Repository layout

```text
.
├── providers/
│   ├── codex/
│   ├── claude/
│   └── grok/
├── docs/
│   └── architecture.md
├── scripts/
│   └── test-all.sh
└── install.sh
```

## Imported revisions

- Codex: `a4f5a0b64377cc9c278a29c15f29722013e606e8`
- Claude: `98b8b48c07b782cde134f67b5cf07c1a6f931c4f`
- Grok: `89fc8c0b20b6c2ef8c3d25b57542a3eabacedb46`

The original standalone repositories remain intact. Future development can move to this repository after the integrated layout is accepted.
