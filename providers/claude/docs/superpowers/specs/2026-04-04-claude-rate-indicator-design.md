# Claude Rate Limit Indicator — Design Spec

**Date:** 2026-04-04
**Status:** Implemented (standalone project)

---

## Overview

A GNOME Shell system tray indicator that displays Claude Code API rate limit usage (5-hour and 7-day windows) in the Ubuntu top bar. The indicator reads from a local JSON file updated by the existing Claude Code statusline script.

---

## Architecture

### Data Flow

```
Claude API response headers
        ↓
~/.claude/statusline-command.sh  (receives rate limits via stdin JSON from Claude Code)
        ↓  side-writes on every statusline refresh
~/.claude/rate_limits_live.json
        ↓  polled every 60 seconds
indicator.py  (AppIndicator3 daemon)
        ↓
GNOME top bar — right side (system tray area)
```

### Data Source: `~/.claude/rate_limits_live.json`

Written by the modified `statusline-command.sh`. Format:

```json
{
  "utilization_5h": 5,
  "reset_5h": 1774270800,
  "utilization_7d": 1,
  "reset_7d": 1774846800,
  "updated_at": 1743750601
}
```

- `utilization_5h` / `utilization_7d`: integer percentage (0–100)
- `reset_5h` / `reset_7d`: Unix timestamp of window reset
- `updated_at`: Unix timestamp of last write

If the file is absent or stale (>10 min), the indicator shows a neutral waiting state.

---

## Components

### 1. Statusline Data Producer (`claude-statusline`)

The separate `claude-statusline` project writes `rate_limits_live.json` when rate-limit data is available. This indicator only consumes that file and does not modify the statusline installation.

### 2. Indicator Daemon (`indicator.py`)

- Python 3, uses `gi.repository.AppIndicator3` and `gi.repository.Gtk`
- Runs as a background process
- Polls `~/.claude/rate_limits_live.json` every 60 seconds via `GLib.timeout_add_seconds`
- **Icon label** (always visible in top bar): Claude logo plus `5%|1% ⟳3h42m`
- **Click menu:**
  - `⚡ 5H: 5%  ⟳ 3h42m (14:30)`
  - `📅 7D: 1%  ⟳ 6d2h (04/10 14:30)`
  - `Updated: 14:30:01`
  - separator
  - `Refresh` — triggers immediate re-read
  - `Quit` — exits the daemon

**Color logic** — the dynamically generated SVG renders the usage text in:
- < 70% → `indicator-green` (normal state)
- 70–89% → `indicator-yellow` (attention state, uses `set_status(ATTENTION)`)
- ≥ 90% → `indicator-red` (attention state)

The Claude logo is bundled as a transparent SVG asset.

### 3. Install Script (`install.sh`)

1. Check / install `gir1.2-ayatana-appindicator3-0.1` via `apt`
2. Copy `indicator.py` and the Claude logo to `~/.local/share/claude-rate-indicator`
3. Write the `~/.local/bin/claude-rate-indicator` launcher
4. Install and enable the `claude-rate-indicator.service` systemd user service
5. Write the GNOME autostart entry and optionally launch immediately

### 4. Autostart Entry (`claude-rate-indicator.desktop`)

Standard XDG autostart `.desktop` file that starts the systemd user service.

---

## File Layout

```
claude-rate-indicator/
├── indicator.py
├── install.sh
├── assets/
│   └── claude-logo.svg
├── icons/
│   ├── claude-rate-green.svg
│   ├── claude-rate-yellow.svg
│   └── claude-rate-red.svg
├── tests/
│   └── test_indicator_logo.py
└── docs/superpowers/specs/2026-04-04-claude-rate-indicator-design.md
```

Post-install:
```
~/.local/share/claude-rate-indicator/       ← installed program and logo
~/.local/bin/claude-rate-indicator          ← launcher
~/.config/systemd/user/claude-rate-indicator.service
~/.config/autostart/claude-rate-indicator.desktop
~/.claude/rate_limits_live.json             ← written by statusline patch
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `python3` | Runtime (pre-installed on Ubuntu) |
| `gir1.2-ayatana-appindicator3-0.1` | AppIndicator3 GTK bindings |
| `python3-gi` | GObject introspection for Python (pre-installed) |

---

## Error Handling

- Missing JSON file → show `--` in the label
- Malformed JSON → show the no-data state
- Missing `gir1.2-ayatana-appindicator3-0.1` → `install.sh` installs it automatically

---

## Out of Scope

- Notifications / alerts when approaching rate limit (can be added later)
- Historical usage charts
- Multi-account support
