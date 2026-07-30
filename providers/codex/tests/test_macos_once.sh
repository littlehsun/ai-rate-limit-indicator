#!/usr/bin/env bash
set -euo pipefail

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

mkdir -p "$ROOT/sessions/2026/05/05"
cat > "$ROOT/sessions/2026/05/05/rollout-test.jsonl" <<'JSONL'
{"timestamp":"2026-05-05T00:01:00Z","type":"event_msg","payload":{"type":"token_count","rate_limits":{"primary":{"used_percent":2,"window_minutes":300,"resets_at":1777929435},"secondary":{"used_percent":14,"window_minutes":10080,"resets_at":1778480096},"plan_type":"prolite"}}}
{"timestamp":"2026-05-05T00:02:00Z","type":"event_msg","payload":{"type":"token_count","rate_limits":{"primary":{"used_percent":3,"window_minutes":300,"resets_at":1777929435},"secondary":{"used_percent":15,"window_minutes":10080,"resets_at":1778480096},"plan_type":"prolite"}}}
JSONL

swiftc macos-menubar/CodexRateMenubar.swift -o "$ROOT/codex-rate-menubar"

output="$("$ROOT/codex-rate-menubar" --once --codex-home "$ROOT" --now 1777911600)"

test "$output" = "3%|15%"
