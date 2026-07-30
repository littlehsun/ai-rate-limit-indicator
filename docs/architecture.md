# Architecture notes

## Current integration

The first integrated release is deliberately a monorepo rather than a runtime rewrite:

- each provider retains its proven parser, polling, cache, and authentication behavior;
- the root installer provides one entry point;
- the root test runner exercises every provider;
- provider credentials and caches remain outside the repository;
- standalone repositories remain available while this layout is evaluated.

This gives the project a safe baseline before combining three independently working indicators into one process or one panel icon.

## Reference projects

### CodexBar

[CodexBar](https://github.com/steipete/CodexBar) separates provider metadata, fetch context, settings, and provider implementations behind a registry. Its shared UI consumes normalized provider usage rather than understanding every provider API.

Ideas worth adopting:

1. A provider contract that returns normalized usage windows.
2. Provider metadata for name, icon, color, capabilities, and default visibility.
3. A registry that discovers enabled providers.
4. One refresh coordinator with provider-specific error isolation.
5. Adaptive refresh intervals and stale/error state.

### codexbar-gnome

[codexbar-gnome](https://github.com/InledGroup/codexbar-gnome) keeps the GNOME Shell extension thin and obtains normalized JSON through the CodexBar CLI. Its CLI subprocess adapter avoids shell execution by parsing arguments and launching an argv array.

Ideas worth adopting:

1. A CLI JSON boundary between data collection and desktop UI.
2. A small usage-fetcher interface with provider adapters.
3. One GNOME surface that can switch between providers.
4. Cancellation and timeout handling for refreshes.
5. Explicit used-versus-remaining display mode.

## Suggested next architecture

```text
provider adapters
  ├── codex
  ├── claude
  └── grok
        │
        ▼
normalized UsageSnapshot JSON
        │
        ├── rate-limit-indicator CLI
        └── GNOME AppIndicator UI
```

A minimal normalized snapshot could contain:

```json
{
  "provider": "codex",
  "updated_at": "2026-07-30T12:00:00Z",
  "windows": [
    {
      "id": "weekly",
      "used_percent": 42,
      "resets_at": "2026-08-03T08:00:00Z"
    }
  ],
  "status": "fresh",
  "error": null
}
```

## Recommended sequence

1. Extract the three parsers into provider adapters without changing their outputs.
2. Add contract tests for the normalized snapshot.
3. Add a unified `rate-limit-indicator --json` CLI.
4. Replace three GNOME processes with one AppIndicator process and a provider switcher.
5. Add settings only after the unified runtime is stable.
