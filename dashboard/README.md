# Usage dashboard

A four-provider monitor in the style of `codex-usage-monitor`, reading its
numbers the way the iOS widget does.

Its tests run in `scripts/test-all.sh`. There is no installer and no service
unit yet, so it is started by hand.

```
dashboard/
├── usage_monitor.py   terminal monitor + the shared data layer
├── usage_web.py       the same data as a web page
├── usage_float.py     the same data as a floating desktop widget
├── config.ini         endpoint, theme, language, per-provider switches
├── themes/            palettes, loaded through themes.ini
└── tests/
```

## Where the numbers come from

From the snapshot `publish.py` serves, never from the provider APIs:

```
provider APIs → desktop indicator → snapshots.json → publish.py :8477
                                                          ↓
                          ┌───────────────┬───────────────┼───────────────┐
                      iOS widget    usage_monitor     usage_web      usage_float
```

That is the whole point of reading it this way. No credential is needed here, no
provider quota is spent, and one desktop refresh feeds every screen watching it.
It is also the contract the Scriptable widget already works to, so anything that
breaks this breaks that too.

The costs are the same ones the widget carries. The publishing machine sleeps,
so a stale snapshot and an unreachable host are normal states rather than
exceptional ones. Both are handled the same way: the last numbers stay on screen
with their age attached, and the two conditions are reported separately because
they mean different things — an unreachable publisher is a network problem, an
old snapshot is a desktop that stopped refreshing.

## Terminal

```bash
python3 usage_monitor.py                      # loop on the configured interval
python3 usage_monitor.py --once               # render once and exit
python3 usage_monitor.py --json               # machine-readable
python3 usage_monitor.py --providers codex,claude
python3 usage_monitor.py --theme nord --language en
python3 usage_monitor.py --endpoint http://100.x.y.z:8477/usage.json
```

`100.x.y.z` stands in for your publisher's own Tailscale address — `tailscale ip
-4` on the machine running `publish.py` prints it. Like `mobile/usage-widget.js`,
this file is public, so it carries no real host. Note that a machine shared
across tailnets has a different IPv4 in each one, so the address a phone should
use is the one that tailnet sees, not the one the publisher prints for itself.

Ten themes, `zh-TW` and `en`. See **Themes** below.

## Web

```bash
python3 usage_web.py                          # http://127.0.0.1:8478/
python3 usage_web.py --bind 0.0.0.0 --port 8478
```

| URL | |
|---|---|
| `/` | full dashboard, for a browser tab |
| `/?view=compact` | widget layout: 2×2 grid, two windows per card, no reset rows |
| `/?providers=codex,claude` | narrow the list without touching `config.ini` |
| `/?lang=en` `/?theme=nord` | override per URL |
| `/api/usage.json` | the snapshot the page polls |

The page reflows to the compact layout on its own below 420px, so a widget host
that only knows how to show a URL gets something readable without `?view=compact`.
It is one self-contained file with no external request in it — a widget frame is
often offline-ish, and a CDN round trip is exactly what leaves it blank.

Every tab and widget pointed at one server shares a single snapshot behind a 20s
TTL, so ten viewers cost the publisher one request rather than ten.

## Floating desktop widget

A frameless always-on-top window that sits on the desktop, on every workspace,
wherever it was dragged. It is the same numbers the terminal shows, in the place
you actually look while a long agent run is spending them.

```bash
python3 usage_float.py                        # where config.ini says
python3 usage_float.py --compact              # one bar per provider
python3 usage_float.py --theme nord --language en --scale 1.25
python3 usage_float.py --providers claude,codex --opacity 0.7
python3 usage_float.py --x 40 --y 40          # place it, once
python3 usage_float.py --reset-position       # forget where it was
```

| Gesture | |
|---|---|
| Left-drag anywhere | move it; the position is remembered |
| Right-click | compact, opacity, text size, theme, language, autostart, quit |
| Scroll | opacity, in 5% steps |

Full mode gives every window a bar, a percentage and a reset countdown, with a
provider's extras and errors underneath. Compact mode keeps each provider's lead
window only — always the weekly one, so four bars of the same width are
measuring the same thing.

Settings live in `[float]` in `config.ini`; `endpoint`, `theme`, `language`,
`interval` and the provider switches are the shared ones above. What you change
from the menu — position, compact, opacity, scale, on top — is remembered in
`~/.local/state/rate-limit-indicator/float.json` rather than written back into
`config.ini`, so the file you hand-edit stays yours.

To have it come back at login:

```bash
python3 usage_float.py --autostart install    # or remove, or status
```

Linux and GTK 3 only (`python3-gi`, `gir1.2-gtk-3.0` — the same bindings the
GNOME indicator already needs). macOS has the native menu-bar app instead. On
Wayland the widget works, but the compositor owns window placement: it will not
be restored to a saved position, and `--x`/`--y` do nothing.

## Themes

Palettes are files, not code. `themes/themes.ini` is the only entry point: it
names the files rather than holding the colours, so adding a palette means
dropping an `.ini` beside it and listing it — nobody edits a theme somebody else
wrote, and nobody merges a file everybody touches.

```
dashboard/themes/
├── themes.ini            the index; every palette is loaded through this
├── dracula.ini           default
├── nord.ini              tokyo-night.ini      catppuccin-mocha.ini
├── gruvbox.ini           solarized-dark.ini   one-dark.ini
├── monochrome.ini        solarized-light.ini  catppuccin-latte.ini
└── custom.ini            yours; ships empty
```

`python3 usage_monitor.py --list-themes` prints what loaded, marking the current
one. `--theme NAME`, `--themes /path/to/themes.ini`, or `?theme=NAME` on the web
view.

Eleven keys, all required. There is no derivation and nothing is optional,
because the ten shipped files are the reference: copy the closest one and change
the values. `custom.ini` carries the full list with a note on what each key
paints. An entry whose keys are all still blank is treated as an untouched
template and skipped, which is why `custom.ini` can stay listed in `themes.ini`
until you fill it in. A partly filled one is an error naming the keys you missed.

A later entry in `themes.ini` redefining an earlier name wins, so a shipped
theme is overridden by adding a file, never by editing the original.

Two of the ten are light — `solarized-light` and `catppuccin-latte`. They work
because the web view takes its background from the palette rather than from
`prefers-color-scheme`: the theme decides light or dark, not the system. The
terminal view ignores `background`, `surface`, `border` and `text` entirely,
since a terminal supplies its own background.

The iOS widget does not read these files. It runs in Scriptable on a phone with
no access to this machine, so its dracula palette is written into
`mobile/usage-widget.js` directly and has to be changed there.

## Per-provider switches

```ini
[providers]
codex = true
claude = true
grok = true
gemini = false
```

Order on screen is always codex, claude, grok, gemini regardless of the order
here. `--providers` and `?providers=` narrow it further at runtime.

## Two deliberate departures from `codex-usage-monitor`

**Colour thresholds are 70/90, not 60/85.** This sits next to the iOS widget and
the macOS menu bar on the same desk, and one percentage must not be amber in one
surface and green in another. `UsageColor` in the Swift app draws the same two
lines.

**A window nobody reported shows `--` and an empty track, not `0%`.** Antigravity
drops a window once its quota is spent, so a nought there would read as "all of
it still available" — the opposite of true. A genuine `0%` also gets an empty
track; the label beside it is what tells the two apart.

## Tests

```bash
cd dashboard && PYTHONPATH="$PWD" python3 -m unittest discover -s tests
```

75 tests, no network and no display: the fetch layer takes an injected opener,
and everything above `usage_float.py`'s GTK layer is plain data.

## Known gaps

- No installer and no service unit. Run it by hand, or let `usage_float.py
  --autostart install` bring the widget back at login.
- The widget's theme and language menus last for the session only: they are
  shared settings, and a widget quietly rewriting `config.ini` would change the
  terminal view too.
- `usage_web.py` binds loopback by default. `--bind` to a tailnet address is
  untested and `publish.py` has a deliberate refuse-to-wildcard guard that this
  does not copy.
- The web page polls on a fixed interval and does not back off when the
  publisher is down.
