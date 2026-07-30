# Grok Rate Indicator

GNOME status-bar tool that shows Grok Build usage the same way `/usage` does:
**weekly pool %** and **monthly credit budget**.

Modeled after the integrated [`codex` provider](../codex).

## Data sources

| View | Endpoint | Fields |
|------|----------|--------|
| Weekly | `GET https://cli-chat-proxy.grok.com/v1/billing?format=credits` | `creditUsagePercent`, `currentPeriod` (week) |
| Monthly | `GET https://cli-chat-proxy.grok.com/v1/billing` | `used`, `monthlyLimit` (USD **cents**) |

Auth: bearer token from `~/.grok/auth.json` (`grok login`). No browser cookies.

Cache:

```text
~/.cache/grok-rate-indicator/billing.json
```

## What the numbers mean

### Not request counts

`monthlyLimit: 15000` is **not** “15000 times”.

The CLI labels this as **`used of $ limit`**. Values are **USD cents**:

| Raw API | Dollars |
|---------|---------|
| `15000` | **$150.00** monthly included budget |
| `178`   | **$1.78** used this calendar month |

Each turn burns a variable amount of that budget depending on model, tokens,
tools, images, etc. One chat is not “1 count”.

### Weekly vs monthly

| Meter | What it is | Reset |
|-------|------------|--------|
| **Weekly** | Unified SuperGrok usage pool (Grok / Build / Chat share it). Shown as a **percentage**. | Rolling week window from API (`currentPeriod`) |
| **Monthly** | Dollar credit budget for the billing month. Shown as **$ used / $ limit** and %. | Calendar month from API (`billingPeriodStart`/`End`) |

You can be high on weekly while still low on monthly (or the reverse). Weekly is
usually what you hit first.

## Top-bar label

```text
weekly%|monthly%  ⟳reset
```

Example:

```text
4%|1%  ⟳4d12h
```

- Left % = weekly pool
- Right % = monthly dollar budget
- `⟳` = countdown to the **weekly** reset (nearer limit)
- Colors: green &lt;70%, yellow 70–89%, red ≥90% (each side colored independently; dot uses max)

Menu:

- Weekly: 4%  reset …
- Monthly: $1.78 / $150 (1%)  reset …
- Product rows (e.g. GrokBuild: 4%) when present
- On-demand / Updated

## Ubuntu / GNOME

```bash
bash ubuntu-indicator/install.sh
```

Or restart after code updates:

```bash
bash ubuntu-indicator/install.sh   # answer y
# or
~/.local/bin/grok-rate-poll
systemctl --user restart grok-rate-indicator.service
```

CLI:

```bash
~/.local/bin/grok-rate-poll
PYTHONPATH=ubuntu-indicator python3 ubuntu-indicator/grok_rate.py --once
PYTHONPATH=ubuntu-indicator python3 ubuntu-indicator/grok_rate.py --cache-only --json
```

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `GROK_HOME` | `~/.grok` | `auth.json` location |
| `GROK_RATE_BILLING_URL` | `https://cli-chat-proxy.grok.com/v1/billing` | Base billing URL |
| `GROK_RATE_CACHE` | `~/.cache/grok-rate-indicator/billing.json` | Cache path |

## Tests

```bash
PYTHONPATH="$PWD/ubuntu-indicator" python3 -m unittest discover -s tests -v
bash -n ubuntu-indicator/install.sh
```

## Notes

- Private CLI billing surface; fields may change.
- HTTP 401 → run `grok login` again.
- macOS menubar is out of scope for v1.
