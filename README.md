# Rate Limit Indicator

[繁體中文說明](README.zh-TW.md)

A unified rate-limit indicator for Codex, Claude Code, Grok Build, and
Gemini/AGY.

The project keeps each provider's proven data collector and normalizes their
usage into one shared snapshot model. On GNOME, a single AppIndicator displays
the selected providers, their 5H and 7D usage, reset countdowns, and provider
details.

## Features

- One GNOME panel indicator for all enabled providers.
- Auto mode follows the most recently changed fresh 7D quota.
- Custom mode supports multiple providers and a configurable display order.
- Independent controls for panel visibility and dropdown visibility.
- Unified 5H and 7D naming and color thresholds.
- Absolute reset times with countdowns.
- Expandable Codex reset-credit expirations.
- One login command controlled by a private local configuration file.
- JSON and text CLI output for normalized usage.

## Providers

| Provider | Unified GNOME UI | macOS | Data source |
| --- | --- | --- | --- |
| [Codex](providers/codex/README.md) | Adapter | Swift menu bar | Local Codex rollout data; optional ChatGPT quota API |
| [Claude](providers/claude/README.md) | Adapter | — | Claude OAuth usage API |
| [Grok](providers/grok/README.md) | Adapter | — | Grok CLI billing API, with 7D and monthly windows |
| [Gemini](providers/gemini/README.md) | Adapter | — | AGY localhost quota API with last-known snapshot cache |

Gemini prefers the same `RetrieveUserQuotaSummary` endpoint used by AGY's
`/usage` screen. It exposes Gemini and Claude/GPT 5H and 7D pools. If AGY is not
running or its local endpoint is temporarily unavailable, the adapter keeps the
last AGY snapshot and marks it stale instead of switching to a different quota
format.

## Requirements

- Linux with a GNOME-compatible AppIndicator implementation.
- Python 3.
- `python3-gi` and AppIndicator3 Python bindings.
- `systemd --user`.
- `lsof` for AGY localhost port discovery.
- The corresponding provider CLIs or local usage data for the providers you
  enable.

## Installation

Install all providers and the unified GNOME indicator:

```bash
./install.sh all
```

Install or repair only the unified manager:

```bash
./install.sh manager
```

Install an individual provider:

```bash
./install.sh codex
./install.sh claude
./install.sh grok
./install.sh gemini
```

Install the Codex macOS menu-bar app:

```bash
./install.sh codex-macos
```

The installer creates one GNOME login command:

```text
~/.local/bin/rate-limit-indicators start
```

It disables the standalone GNOME indicator services while retaining the
provider polling timers needed by the unified UI.

## Configuration

Settings are stored outside the repository:

```text
~/.config/rate-limit-indicator/providers.env
```

The file is created with user-only permissions. A complete example:

```bash
# Enable or disable provider data sources.
CODEX=true
CLAUDE=true
GROK=true
GEMINI=true

# auto or custom
DISPLAY_MODE=custom

# Providers shown in the panel in Custom mode.
DISPLAY_PROVIDERS=codex,grok,gemini

# Provider sections shown in the dropdown.
DROPDOWN_PROVIDERS=codex,claude,grok,gemini

# Shared order for both the panel and dropdown.
PROVIDER_ORDER=codex,grok,claude,gemini
```

`DISPLAY_MODE=auto` selects the fresh provider whose 7D usage changed most
recently. The largest change wins, the newest update breaks a tie, and the
current selection remains stable when nothing changes.

`DISPLAY_MODE=custom` displays every provider in `DISPLAY_PROVIDERS`.
`PROVIDER_ORDER` preserves the complete order, including providers currently
hidden from either surface.

The same options are available from `Display settings…` in the dropdown menu.

Apply configuration changes immediately:

```bash
~/.local/bin/rate-limit-indicators apply
```

## Commands

```bash
~/.local/bin/rate-limit-indicators start
~/.local/bin/rate-limit-indicators stop
~/.local/bin/rate-limit-indicators status
~/.local/bin/rate-limit-indicators apply
```

Read normalized usage without opening the UI:

```bash
~/.local/bin/rate-limit-indicators usage
~/.local/bin/rate-limit-indicators usage --json
~/.local/bin/rate-limit-indicators usage --provider codex
```

The normalized runtime cache is written to:

```text
~/.cache/rate-limit-indicator/snapshots.json
```

## Security

Credentials, tokens, provider caches, and user configuration are never stored
in this repository. Collectors read credentials from their providers' existing
local locations, and generated configuration/cache files are written under the
user's home directory with restrictive permissions.

The AGY adapter connects only to fixed loopback addresses. TLS verification is
relaxed exclusively for AGY's self-signed localhost certificate.

Claude OAuth credentials are read from Claude Code's existing
`~/.claude/.credentials.json`. The indicator never stores or refreshes the
access token itself. Without valid OAuth credentials, Claude usage is reported
as unavailable.

## Testing

```bash
./scripts/test-all.sh
```

The suite covers all provider parsers, the unified adapters and UI behavior,
installer/manager integration, and shell syntax. The macOS smoke test is skipped
automatically when `swiftc` is unavailable.

## Repository layout

```text
.
├── providers/
│   ├── codex/
│   ├── claude/
│   ├── grok/
│   └── gemini/
├── unified-indicator/
│   ├── adapters.py
│   ├── agy_rate.py
│   ├── cli.py
│   ├── indicator.py
│   ├── models.py
│   └── tests/
├── docs/
│   └── architecture.md
├── scripts/
│   └── test-all.sh
├── manage.sh
└── install.sh
```

## Imported revisions

- Codex: `a4f5a0b64377cc9c278a29c15f29722013e606e8`
- Claude: `98b8b48c07b782cde134f67b5cf07c1a6f931c4f`
- Grok: `89fc8c0b20b6c2ef8c3d25b57542a3eabacedb46`

The original standalone repositories remain intact.
