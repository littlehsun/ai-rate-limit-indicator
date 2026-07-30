# Gemini Rate Indicator

Gemini CLI 的 Ubuntu / GNOME quota indicator。頂部列顯示目前最受限制的 Pro、Flash quota，用量百分比會各自依照門檻變色，並顯示重置倒數。

```text
Gemini logo  32%|8%  ⟳3h20m
```

## Data source

Poller reuses the local Gemini CLI OAuth session:

```text
~/.gemini/oauth_creds.json
```

It calls the same Gemini Code Assist quota endpoint used by the official Gemini CLI and writes a credential-free cache:

```text
~/.cache/gemini-rate-indicator/quota.json
```

The cache contains quota percentages, model IDs, reset times, and an update timestamp. OAuth tokens are never copied into the cache or repository.

If the OAuth token has expired, the poller refreshes it using the existing Gemini CLI refresh token and atomically updates the original credential file. The public installed-app OAuth value needed for refresh stays in a private local file:

```text
~/.config/gemini-rate-indicator/oauth.env
```

The installer creates a mode-`600` template. No OAuth value is committed to this repository.

## Install

```bash
bash ubuntu-indicator/install.sh
```

Gemini CLI must have been authenticated with a Google account at least once. If the credential file is absent, install and sign in to the official Gemini CLI first.

## Commands

```bash
~/.local/bin/gemini-rate-poll
PYTHONPATH=ubuntu-indicator python3 ubuntu-indicator/gemini_rate.py --once
PYTHONPATH=ubuntu-indicator python3 ubuntu-indicator/gemini_rate.py --cache-only --json
```

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `GEMINI_HOME` | `~/.gemini` | Gemini CLI credential directory |
| `GEMINI_RATE_CACHE` | `~/.cache/gemini-rate-indicator/quota.json` | Cache path |
| `GEMINI_CLOUD_PROJECT` | auto-detected | Optional Code Assist project override |
| `GEMINI_CODE_ASSIST_ENDPOINT` | `https://cloudcode-pa.googleapis.com` | API endpoint override |
| `GEMINI_OAUTH_CLIENT_ID` | decoded from local ID token | Optional OAuth client ID override |
| `GEMINI_OAUTH_CLIENT_SECRET` | local config | Installed-app value required only for token refresh |

## Tests

```bash
PYTHONPATH="$PWD/ubuntu-indicator" python3 -m unittest discover -s tests -v
bash -n ubuntu-indicator/install.sh
```

## Upstream reference

Quota fields and endpoint behavior follow the official [Google Gemini CLI](https://github.com/google-gemini/gemini-cli), which exposes the same quota information through `/stats model`.
