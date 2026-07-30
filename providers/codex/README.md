# Codex Rate Indicator

Small status-bar tools that show Codex rate-limit usage from local Codex rollout files.

Default data source:

```text
~/.codex/sessions/**/rollout-*.jsonl
```

The tools read the newest `payload.rate_limits` snapshot from `event_msg` rows. No auth file is read and no network request is made.

Optional ChatGPT/Codex Desktop quota polling:

```text
https://chatgpt.com/backend-api/wham/usage
https://chatgpt.com/backend-api/wham/rate-limit-reset-credits
```

These are undocumented ChatGPT backend endpoints, so this project treats them
as opt-in only. It does not extract browser cookies. The poller uses the
logged-in Codex token from `~/.codex/auth.json` by default, or an explicit
bearer token override from a local env file:

```bash
mkdir -p ~/.config/codex-rate-indicator
chmod 700 ~/.config/codex-rate-indicator
$EDITOR ~/.config/codex-rate-indicator/wham.env
chmod 600 ~/.config/codex-rate-indicator/wham.env
```

Example `wham.env`:

```bash
CODEX_RATE_SOURCE=auto
CODEX_RATE_SHOW_5H=false
# Optional override:
# CHATGPT_ACCESS_TOKEN=replace-with-token
```

`CODEX_RATE_SOURCE` values:

```text
local  read only local rollout files, no network
auto   prefer the cached wham snapshot, fall back to local rollout files
wham   read only the cached wham snapshot
```

`CODEX_RATE_SHOW_5H=false` hides the 5h value and uses the 7d reset countdown.
Set it to `true` to restore the combined 5h and 7d display.

## What It Shows

Default:

```text
weekly%
```

With `CODEX_RATE_SHOW_5H=true`:

```text
5h%|weekly%
```

When present, the 5-hour window uses `window_minutes = 300`; the weekly window
uses `window_minutes = 10080`.

## Ubuntu / GNOME

Install:

```bash
bash ubuntu-indicator/install.sh
```

The installer creates a user systemd service and a GNOME autostart entry:

```text
~/.config/systemd/user/codex-rate-indicator.service
~/.config/autostart/codex-rate-indicator.desktop
~/.local/share/applications/codex-rate-indicator.desktop
```

The applications launcher appears in the Ubuntu app grid as `Codex Rate
Indicator`. Opening it restarts the user service, which is useful if the
indicator crashes or disappears from the top bar.

Run manually:

```bash
~/.local/bin/codex-rate-indicator
```

The GNOME indicator polls `~/.codex/sessions` every 60 seconds.

If wham polling is configured, enable the timer. It polls the API every minute
and schedules an initial poll whenever the timer starts:

```bash
systemctl --user enable --now codex-rate-wham-poll.timer
systemctl --user restart codex-rate-indicator.service
```

Run one wham poll manually:

```bash
~/.local/bin/codex-rate-wham-poll
```

## macOS

Install:

```bash
bash macos-menubar/install.sh
```

Run one-shot CLI output:

```bash
~/.local/bin/codex-rate-menubar --once
```

Run the menubar app manually:

```bash
~/.local/bin/codex-rate-menubar
```

The installer creates:

```text
~/Library/LaunchAgents/com.hsun.codex-rate-menubar.plist
```

## Custom Codex Home

Both tools honor:

```bash
CODEX_HOME=/path/to/.codex
```

The macOS binary also supports:

```bash
codex-rate-menubar --once --codex-home /path/to/.codex
```

## Tests

```bash
PYTHONPATH="$PWD/ubuntu-indicator" python3 -m unittest discover -s tests
bash tests/test_macos_once.sh
```
