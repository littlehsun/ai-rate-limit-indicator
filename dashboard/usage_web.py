#!/usr/bin/env python3
"""Serve the four-provider dashboard as a web page.

Two audiences share one page. Opened in a browser tab it is a full dashboard;
dropped into a widget frame it reflows to the compact layout on its own, so a
widget host that only knows how to show a URL gets something readable without a
second implementation.

The page never talks to `publish.py` itself. It reads `/api/usage.json` from
this server, which holds the snapshot behind a short TTL, so ten open tabs cost
the publisher one request rather than ten -- the same reason the widget reads a
published snapshot instead of the provider APIs.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from usage_monitor import (
    DANGER_PERCENT,
    LANGUAGES,
    PROVIDER_ORDER,
    load_themes,
    WARNING_PERCENT,
    MonitorError,
    Settings,
    Snapshot,
    SnapshotClient,
    Theme,
    default_config_path,
    load_settings,
    snapshot_as_json,
)

DEFAULT_PORT = 8478
DEFAULT_BIND = "127.0.0.1"

# How long a fetched snapshot is reused before the publisher is asked again.
# The desktop refreshes on its own schedule, so asking faster than this only
# spends requests to be told the same thing.
SNAPSHOT_TTL_SECONDS = 20.0

WEB_STRINGS = {
    "zh-TW": {
        "title": "AI 用量總覽",
        "snapshot": "快照",
        "offline": "無法連線發布端 · 顯示快取",
        "stale": "快照已過期",
        "unreported": "未回報",
        "resets": "重設",
        "expired": "已到期",
        "disabled": "未啟用任何 provider",
        "missing": "無資料",
        "error": "讀取失敗",
    },
    "en": {
        "title": "AI usage overview",
        "snapshot": "Snapshot",
        "offline": "Publisher unreachable · showing cache",
        "stale": "Snapshot is stale",
        "unreported": "not reported",
        "resets": "Resets",
        "expired": "expired",
        "disabled": "No provider is enabled",
        "missing": "no data",
        "error": "Fetch failed",
    },
}


class SnapshotCache:
    """Hold one snapshot for every tab and widget pointed at this server."""

    def __init__(self, client: SnapshotClient, ttl: float = SNAPSHOT_TTL_SECONDS):
        self._client = client
        self._ttl = ttl
        self._lock = threading.Lock()
        self._snapshot: Optional[Snapshot] = None
        self._fetched_at = 0.0

    def payload(self, providers: Sequence[str], *, now: Optional[float] = None) -> str:
        # The snapshot is what is shared; the serialisation is not. Caching the
        # rendered JSON instead would hand the first caller's provider list to
        # every widget that asked for a different one.
        return snapshot_as_json(self.snapshot(now=now), providers)

    def snapshot(self, *, now: Optional[float] = None) -> Snapshot:
        now = time.time() if now is None else now
        with self._lock:
            if self._snapshot is not None and now - self._fetched_at < self._ttl:
                return self._snapshot
            self._snapshot = self._client.fetch(now=now)
            self._fetched_at = now
            return self._snapshot


def resolve_providers(raw: Optional[str], configured: Sequence[str]) -> Tuple[str, ...]:
    """Let a URL narrow the provider list, so one server backs several widgets."""

    if not raw:
        return tuple(configured)
    wanted = {name.strip().lower() for name in raw.split(",") if name.strip()}
    chosen = tuple(name for name in PROVIDER_ORDER if name in wanted)
    return chosen or tuple(configured)


class DashboardHandler(BaseHTTPRequestHandler):
    cache: SnapshotCache
    settings: Settings
    # Loaded once at bind time rather than per request: themes.ini names a
    # dozen files, and re-reading them on every widget poll would turn a colour
    # scheme into disk traffic.
    themes: Dict[str, Theme]
    server_version = "RateLimitDashboard"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        providers = resolve_providers(
            (query.get("providers") or [None])[0], type(self).settings.providers
        )

        if parsed.path in ("/", "/index.html"):
            language = (query.get("lang") or [type(self).settings.language])[0]
            if language not in LANGUAGES:
                language = type(self).settings.language
            theme = (query.get("theme") or [type(self).settings.theme])[0]
            if theme not in type(self).themes:
                theme = type(self).settings.theme
            compact = (query.get("view") or [""])[0] == "compact"
            self._send(
                200,
                "text/html; charset=utf-8",
                render_page(
                    providers=providers,
                    language=language,
                    theme=theme,
                    themes=type(self).themes,
                    compact=compact,
                    interval=type(self).settings.interval,
                ).encode("utf-8"),
            )
            return

        if parsed.path == "/api/usage.json":
            try:
                payload = type(self).cache.payload(providers)
            except MonitorError as exc:
                self._send(
                    503,
                    "application/json; charset=utf-8",
                    json.dumps({"error": str(exc)}).encode("utf-8"),
                )
                return
            self._send(200, "application/json; charset=utf-8", payload.encode("utf-8"))
            return

        self.send_error(404, "not found")

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # A widget refreshing on its own schedule would otherwise write a line
        # into the journal forever.
        pass


def render_page(
    *,
    providers: Sequence[str],
    language: str,
    theme: str,
    compact: bool,
    interval: int,
    themes: Optional[Dict[str, Theme]] = None,
) -> str:
    text = WEB_STRINGS[language]
    available = themes if themes is not None else load_themes()
    palette = available[theme]
    config = {
        "providers": list(providers),
        "interval": max(10, interval),
        "warning": WARNING_PERCENT,
        "danger": DANGER_PERCENT,
        "text": text,
        "compact": compact,
        "colors": {
            "good": palette.good,
            "warning": palette.warning,
            "danger": palette.danger,
            "empty": palette.empty,
            "accent": palette.accent,
            "heading": palette.heading,
            "muted": palette.muted,
        },
    }
    page = _PAGE.replace("__TITLE__", html.escape(text["title"])).replace(
        "__CONFIG__", json.dumps(config, ensure_ascii=False)
    )
    for token, value in (
        ("__BG__", palette.background),
        ("__SURFACE__", palette.surface),
        ("__BORDER__", palette.border),
        ("__INK__", palette.text),
        ("__MUTED__", palette.muted),
        # `empty` is the theme's own unfilled-bar colour, which is what the
        # terminal draws its ░ in, so the two views show the same track.
        ("__TRACK__", palette.empty),
        ("__ACCENT__", palette.accent),
        ("__HEADING__", palette.heading),
    ):
        page = page.replace(token, value)
    return page


# The page is one file with no external request in it: a widget frame is often
# offline-ish and a CDN round trip is exactly the thing that leaves it blank.
_PAGE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>__TITLE__</title>
<!-- Inline, because a widget frame asking for /favicon.ico earns a 404 in the
     console on every single render otherwise. -->
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><text y='14' font-size='14'>%F0%9F%93%8A</text></svg>">
<style>
  /* Every colour is painted from the selected theme, so the page is dracula or
     gruvbox all the way down rather than one fixed slate palette wearing the
     theme's progress bars. There is deliberately no prefers-color-scheme
     override: these palettes are dark by definition, and a themed dashboard
     that turns white on a light system is no longer the theme that was asked
     for. The Codex monitor this follows has no light mode either. */
  :root {
    --bg: __BG__;
    --surface: __SURFACE__;
    --border: __BORDER__;
    --ink: __INK__;
    --ink-2: __MUTED__;
    --ink-3: __MUTED__;
    --track: __TRACK__;
    --accent: __ACCENT__;
    --heading: __HEADING__;
    --radius: 14px;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font: 15px/1.45 ui-sans-serif, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  main { max-width: 720px; margin: 0 auto; padding: 20px 16px 32px; }
  header { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }
  h1 { font-size: 17px; font-weight: 650; margin: 0; letter-spacing: .01em; }
  .meta { color: var(--ink-3); font-size: 12px; font-variant-numeric: tabular-nums; }
  .banner {
    margin: 10px 0 0; padding: 7px 11px; border-radius: 9px; font-size: 12.5px;
    background: color-mix(in srgb, var(--warn) 16%, transparent);
    color: var(--warn); border: 1px solid color-mix(in srgb, var(--warn) 34%, transparent);
  }
  .banner[hidden] { display: none; }
  .cards { display: grid; gap: 12px; margin-top: 14px; }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 13px 15px 14px;
  }
  .card-head { display: flex; align-items: center; gap: 8px; margin-bottom: 11px; }
  /* Provider names take the theme's heading colour and window labels its
     accent, which is exactly what the terminal view paints them, so the two
     read as the same program rather than two that share a data source. */
  .name { font-weight: 640; font-size: 14.5px; color: var(--heading); }
  .status { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--ink-3); }
  .status.fresh { color: var(--good); }
  .status.stale, .status.error { color: var(--warn); }
  .win + .win { margin-top: 10px; }
  .win-top { display: flex; align-items: baseline; gap: 8px; font-size: 12.5px; }
  .win-label { color: var(--accent); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pct { font-weight: 650; font-size: 14px; font-variant-numeric: tabular-nums; }
  .track { height: 6px; border-radius: 3px; background: var(--track); margin-top: 5px; overflow: hidden; }
  .fill { height: 100%; border-radius: 3px; }
  .reset { margin-top: 3px; font-size: 11.5px; color: var(--ink-3); font-variant-numeric: tabular-nums; }
  .extras { margin-top: 10px; font-size: 11.5px; color: var(--ink-3); }
  .extras div + div { margin-top: 2px; }
  .err { margin-top: 8px; font-size: 12px; color: var(--danger); }
  .empty { color: var(--ink-3); font-size: 12.5px; }

  /* Widget mode: the same markup, tightened. Driven by the frame's own width
     so it works whether the host passes ?view=compact or just a small box. */
  @media (max-width: 420px) {
    main { padding: 12px 11px 14px; }
    h1 { font-size: 14px; }
    .cards { gap: 8px; }
    .card { padding: 9px 11px 10px; border-radius: 11px; }
    .card-head { margin-bottom: 7px; }
    .reset, .extras { display: none; }
    .win + .win { margin-top: 7px; }
  }
  body.compact main { padding: 10px; max-width: none; }
  body.compact .reset, body.compact .extras, body.compact .meta { display: none; }
  body.compact .cards { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; align-items: start; }
  body.compact .card { padding: 9px 11px 10px; }
  body.compact h1 { font-size: 13px; margin-bottom: 2px; }
</style>
</head>
<body>
<main>
  <header>
    <h1>__TITLE__</h1>
    <span class="meta" id="meta"></span>
  </header>
  <div class="banner" id="banner" hidden></div>
  <div class="cards" id="cards"></div>
</main>
<script>
const CONFIG = __CONFIG__;
const T = CONFIG.text;

document.documentElement.style.setProperty("--good", CONFIG.colors.good);
document.documentElement.style.setProperty("--warn", CONFIG.colors.warning);
document.documentElement.style.setProperty("--danger", CONFIG.colors.danger);
if (CONFIG.compact) document.body.classList.add("compact");

function colorFor(percent) {
  if (percent >= CONFIG.danger) return CONFIG.colors.danger;
  if (percent >= CONFIG.warning) return CONFIG.colors.warning;
  return CONFIG.colors.good;
}

function relative(seconds) {
  if (seconds <= 0) return T.expired;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + "m";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + "h" + String(minutes % 60).padStart(2, "0") + "m";
  return Math.floor(hours / 24) + "d" + (hours % 24) + "h";
}

function clock(ms) {
  const d = new Date(ms);
  return d.getHours() + ":" + String(d.getMinutes()).padStart(2, "0");
}

function el(tag, className, textContent) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  // textContent, never innerHTML: every string below arrives from the
  // publisher and an error message is not markup.
  if (textContent !== undefined) node.textContent = textContent;
  return node;
}

function windowNode(win) {
  const wrap = el("div", "win");
  const top = el("div", "win-top");
  top.append(el("span", "win-label", win.label));
  const reported = win.used_percent !== null && win.used_percent !== undefined;
  const pct = el("span", "pct", reported ? win.used_percent + "%" : "--");
  if (reported) pct.style.color = colorFor(win.used_percent);
  else pct.style.color = "var(--ink-3)";
  top.append(pct);
  wrap.append(top);

  const track = el("div", "track");
  // A window nobody reported gets an empty track, and so does a genuine zero:
  // the 2% floor exists to keep a small number visible, not to invent one. The
  // label beside it is what separates "0%" from "--".
  if (reported && win.used_percent > 0) {
    const fill = el("div", "fill");
    fill.style.width = Math.max(2, Math.min(win.used_percent, 100)) + "%";
    fill.style.background = colorFor(win.used_percent);
    track.append(fill);
  }
  wrap.append(track);

  if (win.resets_at) {
    const left = win.resets_at * 1000 - Date.now();
    wrap.append(el("div", "reset", T.resets + " " + relative(Math.floor(left / 1000))));
  } else if (!reported) {
    wrap.append(el("div", "reset", T.unreported));
  }
  return wrap;
}

function cardNode(provider) {
  const card = el("div", "card");
  const head = el("div", "card-head");
  head.append(el("span", "name", provider.label));
  head.append(el("span", "status " + (provider.status || ""), provider.status || ""));
  card.append(head);
  // Compact is a widget frame, where a provider that reports four windows
  // would push every card after it out of the box. Two is what the small and
  // medium widgets show for the same reason; the full page still lists them
  // all. The server has already ordered them lead-first.
  const windows = CONFIG.compact ? provider.windows.slice(0, 2) : provider.windows;
  for (const win of windows) card.append(windowNode(win));
  if (provider.extras && provider.extras.length) {
    const extras = el("div", "extras");
    for (const extra of provider.extras) extras.append(el("div", null, extra));
    card.append(extras);
  }
  if (provider.error) card.append(el("div", "err", provider.error));
  return card;
}

function render(payload) {
  const cards = document.getElementById("cards");
  cards.replaceChildren();
  const byName = new Map((payload.providers || []).map((p) => [p.provider, p]));
  if (!CONFIG.providers.length) {
    cards.append(el("div", "empty", T.disabled));
  }
  for (const name of CONFIG.providers) {
    const provider = byName.get(name);
    if (!provider) {
      const card = el("div", "card");
      card.append(el("span", "name", name));
      card.append(el("div", "empty", T.missing));
      cards.append(card);
      continue;
    }
    cards.append(cardNode(provider));
  }

  let newest = null;
  for (const provider of payload.providers || []) {
    const parsed = Date.parse(provider.updated_at || "");
    if (!Number.isNaN(parsed) && (newest === null || parsed > newest)) newest = parsed;
  }
  document.getElementById("meta").textContent =
    newest === null ? "" : T.snapshot + " " + clock(newest);

  const banner = document.getElementById("banner");
  // The publisher can only report what it knew before it slept, so an
  // unreachable host and an old snapshot are different problems and say so.
  if (payload.reachable === false) {
    banner.textContent = T.offline;
    banner.hidden = false;
  } else if (newest !== null && Date.now() - newest > 15 * 60 * 1000) {
    banner.textContent = T.stale + " · " + relative(Math.floor((Date.now() - newest) / 1000));
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}

async function tick() {
  const url = "/api/usage.json?providers=" + encodeURIComponent(CONFIG.providers.join(","));
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    render(await response.json());
  } catch (error) {
    const banner = document.getElementById("banner");
    banner.textContent = T.error + " · " + error.message;
    banner.hidden = false;
  }
}

tick();
setInterval(tick, CONFIG.interval * 1000);
</script>
</body>
</html>
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the usage dashboard as a web page.")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--bind", default=os.environ.get("USAGE_DASHBOARD_BIND", DEFAULT_BIND))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("USAGE_DASHBOARD_PORT", DEFAULT_PORT))
    )
    parser.add_argument("--endpoint", default=None, help="publish.py URL to read")
    parser.add_argument("--providers", default=None)
    return parser


def serve(
    address: str,
    port: int,
    settings: Settings,
    *,
    client: Optional[SnapshotClient] = None,
) -> ThreadingHTTPServer:
    snapshot_client = client or SnapshotClient(
        settings.endpoint,
        timeout=settings.timeout,
        cache_path=settings.cache_file,
        # A browser is waiting, so one try and then the cache. The terminal
        # monitor can afford to sit through three.
        attempts=1,
    )
    handler = type(
        "BoundDashboardHandler",
        (DashboardHandler,),
        {
            "cache": SnapshotCache(snapshot_client),
            "settings": settings,
            "themes": load_themes(settings.themes_file),
        },
    )
    return ThreadingHTTPServer((address, port), handler)


def main() -> int:
    args = build_parser().parse_args()
    configured = load_settings(args.config or default_config_path())
    providers = resolve_providers(args.providers, configured.providers)
    settings = Settings(
        endpoint=args.endpoint or configured.endpoint,
        providers=providers,
        theme=configured.theme,
        language=configured.language,
        interval=configured.interval,
        color=configured.color,
        clear=configured.clear,
        timeout=configured.timeout,
        cache_file=configured.cache_file,
        themes_file=configured.themes_file,
    )

    try:
        httpd = serve(args.bind, args.port, settings)
    except OSError as exc:
        print(f"cannot serve on {args.bind}:{args.port}: {exc}", file=sys.stderr)
        return 1

    print(f"dashboard on http://{args.bind}:{args.port}/  (reading {settings.endpoint})", flush=True)
    print(f"  widget view: http://{args.bind}:{args.port}/?view=compact", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
