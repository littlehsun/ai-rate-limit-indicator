# Rate Limit Indicator

[繁體中文說明](README.zh-TW.md)

A unified rate-limit indicator for Codex, Claude Code, Grok Build, and
Gemini/AGY.

The project keeps each provider's proven data collector and normalizes their
usage into one shared snapshot model. The GNOME AppIndicator and native macOS
SwiftUI app consume that same backend and display the selected providers, their
5H and 7D usage, reset countdowns, and provider details.

## Features

- One GNOME panel indicator for all enabled providers.
- One native macOS menu-bar app using the same normalized backend.
- Auto mode follows the most recently changed fresh 7D quota.
- Custom mode supports multiple providers and a configurable display order.
- Independent controls for panel visibility and dropdown visibility.
- Unified 5H and 7D naming and color thresholds.
- Absolute reset times with countdowns.
- Expandable Codex reset-credit expirations.
- One login command controlled by a private local configuration file.
- JSON and text CLI output for normalized usage.

## Providers

| Provider | Unified GNOME UI | Unified macOS UI | Data source |
| --- | --- | --- | --- |
| [Codex](providers/codex/README.md) | Adapter | SwiftUI | Local Codex rollout data; optional ChatGPT quota API |
| [Claude](providers/claude/README.md) | Adapter | SwiftUI | Claude OAuth usage API |
| [Grok](providers/grok/README.md) | Adapter | SwiftUI | Grok CLI billing API, with the current 7D quota and product usage |
| [Gemini](providers/gemini/README.md) | Adapter | SwiftUI | AGY localhost quota API with last-known snapshot cache |

Gemini prefers the same `RetrieveUserQuotaSummary` endpoint used by AGY's
`/usage` screen. It exposes Gemini and Claude/GPT 5H and 7D pools. If AGY is not
running or its local endpoint is temporarily unavailable, the adapter keeps the
last AGY snapshot and marks it stale instead of switching to a different quota
format.

## Requirements

- Linux: GNOME-compatible AppIndicator, Python 3, AppIndicator3 Python
  bindings, and `systemd --user`.
- macOS: macOS 14 or newer, Python 3, and Xcode Command Line Tools.
- `lsof` for AGY localhost port discovery on either platform.
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

Install the unified native macOS menu-bar app:

```bash
./install.sh macos
```

`./install.sh codex-macos` remains an alias for compatibility.

The Linux installer creates one GNOME login command:

```text
~/.local/bin/rate-limit-indicators start
```

It disables the standalone GNOME indicator services while retaining the
provider polling timers needed by the unified UI.

The macOS installer builds `Rate Limit Indicator.app`, installs the same Python
backend under `~/Library/Application Support/RateLimitIndicator`, and uses
LaunchAgents for the existing Codex and Grok pollers. The settings window uses
macOS `SMAppService` for the optional Launch at Login toggle.

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

# Codex source: local makes no network requests. auto/wham explicitly opt in
# to polling undocumented ChatGPT quota endpoints with the existing Codex token.
CODEX_RATE_SOURCE=local

# auto or custom
DISPLAY_MODE=custom

# Providers shown in the panel in Custom mode.
DISPLAY_PROVIDERS=codex,grok,gemini

# Provider sections shown in the dropdown.
DROPDOWN_PROVIDERS=codex,claude,grok,gemini

# Shared order for both the panel and dropdown.
PROVIDER_ORDER=codex,grok,claude,gemini

# true runs `grok models` once the Grok token has expired, so the CLI
# refreshes its own credential. Grok tokens last about six hours, so without
# this an idle machine shows stale Grok numbers every morning.
GROK_AUTO_REFRESH=false

# true runs `agy models` when Antigravity is not listening. Antigravity only
# serves quota while it runs, so this starts it for the few seconds a read
# takes and stops it again.
AGY_AUTO_START=false
```

`DISPLAY_MODE=auto` selects the fresh provider whose 7D usage changed most
recently. The largest change wins, the newest update breaks a tie, and the
current selection remains stable when nothing changes.

`DISPLAY_MODE=custom` displays every provider in `DISPLAY_PROVIDERS`.
`PROVIDER_ORDER` preserves the complete order, including providers currently
hidden from either surface.

The same options are available from `Display settings…` in both platform UIs.

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

## Phone widget

An iPhone widget reads the same normalized snapshot the desktop UI writes. The
phone never holds a provider credential and never talks to Anthropic, OpenAI,
xAI, or Google: it fetches one JSON file from a machine you already run this on.

### What is required

- **Tailscale** on both the publishing machine and the phone, on the same
  tailnet. The publisher binds to the Tailscale interface only, so nothing on
  any other network can reach it.
- **[Scriptable](https://scriptable.app)** on the phone. It is free and needs
  no Apple Developer account, so the widget never expires.

### 1. Publish the snapshot

```bash
python3 ~/.local/share/rate-limit-indicator/unified/publish.py   # Linux
python3 ~/Library/Application\ Support/RateLimitIndicator/backend/publish.py   # macOS
```

It prints the address it bound to, and refuses to start when no Tailscale
address exists rather than falling back to a wildcard bind. It serves exactly
one route, `/usage.json`, and reads the cache per request.

The snapshot itself is whatever the desktop UI last wrote, so **the tray or
menu bar has to be running** for the numbers to move. Nothing here polls the
providers: a phone driving `load_snapshots()` would spend the Claude usage
budget on top of what the desktop already spends.

There is **no service unit yet**. The publisher stops when you close the
terminal and does not come back after a reboot.

### 2. Install the widget

Copy `mobile/usage-widget.js` into Scriptable. With iCloud sync on, that is:

```bash
cp mobile/usage-widget.js \
  ~/Library/Mobile\ Documents/iCloud~dk~simonbs~Scriptable/Documents/"Rate Limits.js"
```

Otherwise paste the file into a new script in the app.

### 3. Point it at the publisher

The script ships with a loopback default, because this repository is public and
must not carry anyone's address. Set your own either way:

- **Widget parameter** (preferred, nothing to edit): long-press the widget →
  Edit Widget → Parameter → `http://<your-tailscale-ip>:8477/usage.json`
- **In the script**: change `DEFAULT_ENDPOINT` at the top

Run the script once in Scriptable before adding it to a screen. Errors surface
there rather than as a silently blank tile.

### Sizes

| Size | Shows |
| --- | --- |
| Small | One line per provider; Claude carries 5H and 7D on one line |
| Medium | One block per provider, with percentage and reset |
| Large | One block per window, so Antigravity's four appear in full |
| Lock Screen circular | Ring gauge for whichever window is closest to running out |
| Lock Screen rectangular | Every provider, one line each |

Lock Screen sizes are rendered monochrome by iOS, so severity colour is
unavailable there and fill proportion carries it instead.

Settings live at the top of `usage-widget.js`: `SMALL_ROWS` chooses the small
widget's rows, `CIRCULAR_PROVIDER` pins the circular gauge to one provider
instead of following the worst, and `PREVIEW_FAMILY` picks which size the app
renders when you tap play.

### What it cannot do

iOS decides when a widget re-runs, and the daily refresh budget is far below
the five minutes the script asks for; expect tens of minutes. The header prints
the snapshot's own wall clock rather than an age, because a widget that has not
re-run cannot tell how long it has been sitting there. Tapping the widget with
`When Interacting: Run Script` re-reads immediately.

If the publisher is unreachable the widget keeps the last snapshot it fetched
and marks it, rather than blanking.

Gemini updates roughly an order of magnitude less often than the others, by
design. Antigravity only serves quota while it runs, and `AGY_AUTO_START`
starts it only once the cached snapshot has gone stale at ten minutes, so
spawning a process cannot be worth numbers already in hand. Expect Gemini's
figures to trail the rest by up to that long; it is not stuck.

Antigravity also drops a window once its quota is spent — the bucket comes back
marked `disabled` and is skipped — so a provider's window count can change
between refreshes. Every widget size handles that.

### Before serving on a shared tailnet

Anything on the tailnet that can route to the publishing machine can read the
snapshot. Check whether the tailnet has shared nodes or users beyond your own
before treating it as private; `tailscale status` lists owners.

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
installer/manager integration, and shell syntax. The unified SwiftUI app is
built by the suite when it runs on macOS with Swift available.

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
├── macos/
│   ├── Package.swift
│   ├── Sources/
│   └── install.sh
├── scripts/
│   └── test-all.sh
├── manage.sh
└── install.sh
```
