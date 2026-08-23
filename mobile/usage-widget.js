// Rate Limit Indicator — iOS home screen widget for Scriptable.
//
// Reads the snapshot published by `publish.py` on the Tailscale network. The
// publishing machine sleeps, so every state below has to survive that: a stale
// snapshot and an unreachable host are normal, not exceptional.
//
// Setup: set ENDPOINT to your publisher, run once in Scriptable to check it,
// then add a Scriptable widget to the home screen and pick this script.

// Where publish.py is serving. Put your own address here, or leave it and set
// the URL as the widget parameter instead — this file lives in a public repo,
// so it must not carry anyone's actual host.
const DEFAULT_ENDPOINT = "http://127.0.0.1:8477/usage.json";
const ENDPOINT = (args.widgetParameter || "").trim() || DEFAULT_ENDPOINT;

// Rows for the small widget, in order. Four rows is what fits, so a provider
// that needs two windows either spends two of them or sets `combine`.
//
//   (omitted)                  the provider's first window
//   windows: [a, b]            one row each
//   windows: [a, b], combine   one row: `a` draws the bar, `b` rides along as
//                              a second number
// What each provider shows in the small and medium widgets. `bar` is the
// window the progress bar measures and the large number reports; `also` are
// smaller numbers printed to its left, in this order.
//
// Every bar is a weekly window. Codex and Grok only report one, so leading with
// 7D everywhere is what makes four bars of the same width comparable — a 5H bar
// beside a 7D bar measures different things at identical length. The session
// windows still matter, so they ride along as numbers rather than disappearing.
//
// Providers absent here show their first backend window and nothing else.
const PROVIDER_ORDER = ["codex", "claude", "grok", "gemini"];

const PROVIDER_WINDOWS = {
  claude: { bar: "7d", also: ["5h"] },
  // Antigravity reports a Gemini group and a Claude/GPT one. Carrying both here
  // put four unlabelled percentages in a cell 150pt wide, which was unreadable
  // at a glance — the thing these sizes exist for. Only Gemini, then; the large
  // widget still lists every Claude/GPT window with its name attached.
  gemini: { bar: "7d", also: ["5h"] },
};

// Antigravity drops a window once its quota is spent: the bucket comes back
// marked `disabled` and the adapter skips it, so a Gemini account at 100% for
// the week stops reporting its five-hour window at all. Leaving the number out
// would shorten the row and move everything beside it, so the slot stays and
// says it has nothing rather than reporting a figure nobody sent.
function absentWindow(id) {
  const cadence = id.split("-").pop().toUpperCase();
  return { id, label: cadence, used_percent: null, resets_at: null };
}

function pickWindows(provider) {
  const wanted = PROVIDER_WINDOWS[provider.provider];
  const find = (id) => provider.windows.find((w) => w.id === id);
  if (!wanted) return { bar: provider.windows[0], also: [] };
  const bar = find(wanted.bar) || provider.windows[0];
  const also = (wanted.also || []).map((id) => find(id) || absentWindow(id));
  return { bar, also: bar ? also : [] };
}

function percentLabel(window) {
  return window.used_percent === null ? "--" : `${window.used_percent}%`;
}

// Tapping play in Scriptable reports no widget family, so pick one. Large
// shows every window, which is what you want when checking a change; set it to
// "small" or "medium" to preview those instead. Widgets ignore this entirely.
const PREVIEW_FAMILY = "large";

// The circular Lock Screen widget tracks whichever provider is closest to
// running out. Set this to "codex", "claude", "grok" or "gemini" to pin it.
const CIRCULAR_PROVIDER = "worst";

// Matches UsageColor in the macOS app so both surfaces agree on "bad".
const RED = new Color("#FF5454");
const AMBER = new Color("#FFB82E");
const GREEN = new Color("#00B04F");

// How soon to ask iOS to run this again. See the note at refreshAfterDate:
// iOS decides the real cadence, so treat this as the floor, not the interval.
const REFRESH_AFTER_MS = 5 * 60 * 1000;

// A snapshot older than this is called out even if the publisher claims fresh,
// because the publisher can only report what it knew before it went to sleep.
// Three missed refreshes is late enough to mean something actually went wrong.
const STALE_AFTER_MS = 15 * 60 * 1000;

// A publisher on a laptop can be mid-wake when the widget asks, so one refused
// connection is not proof it is down. Three tries, ten seconds apart.
const FETCH_ATTEMPTS = 3;
const RETRY_DELAY_MS = 10 * 1000;
const FETCH_TIMEOUT_S = 8;
const CACHE_NAME = "rate-limit-usage.json";

function colorFor(percent) {
  if (percent >= 90) return RED;
  if (percent >= 70) return AMBER;
  return GREEN;
}

const INK = Color.dynamic(new Color("#1C1C1E"), new Color("#F2F2F7"));
const INK_2 = Color.dynamic(new Color("#6C6C70"), new Color("#9A9AA0"));
const INK_3 = Color.dynamic(new Color("#A0A0A6"), new Color("#6E6E76"));
const TRACK = Color.dynamic(new Color("#00000018"), new Color("#FFFFFF20"));

const cachePath = () => {
  const fm = FileManager.local();
  return fm.joinPath(fm.cacheDirectory(), CACHE_NAME);
};

function sleep(milliseconds) {
  return new Promise((resolve) => Timer.schedule(milliseconds, false, resolve));
}

async function fetchOnce() {
  const request = new Request(ENDPOINT);
  request.timeoutInterval = FETCH_TIMEOUT_S;
  const payload = await request.loadJSON();
  if (!payload || !Array.isArray(payload.providers)) throw new Error("bad payload");
  return payload;
}

async function loadPayload() {
  const fm = FileManager.local();
  const path = cachePath();
  // Retrying is for a tap, where waiting costs nothing but time. A widget
  // render that retries spends its whole budget failing and gets killed, which
  // leaves the stale tile in place and never draws the offline mark — the
  // opposite of what the retry was for. One attempt there, then the cache.
  const attempts = config.runsInWidget ? 1 : FETCH_ATTEMPTS;
  try {
    let payload = null;
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      try {
        payload = await fetchOnce();
        break;
      } catch (error) {
        if (attempt === attempts) throw error;
        await sleep(RETRY_DELAY_MS);
      }
    }
    fm.writeString(path, JSON.stringify({ fetchedAt: Date.now(), payload }));
    return { payload, fetchedAt: Date.now(), reachable: true };
  } catch (error) {
    // Losing the tailnet must not blank the widget: the last numbers are still
    // the best answer available, as long as their age is visible.
    if (!fm.fileExists(path)) return { payload: null, fetchedAt: null, reachable: false };
    try {
      const cached = JSON.parse(fm.readString(path));
      return { payload: cached.payload, fetchedAt: cached.fetchedAt, reachable: false };
    } catch (_) {
      return { payload: null, fetchedAt: null, reachable: false };
    }
  }
}

function ageLabel(milliseconds) {
  const minutes = Math.max(0, Math.round(milliseconds / 60000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h${String(minutes % 60).padStart(2, "0")}m`;
  return `${Math.floor(hours / 24)}d${hours % 24}h`;
}

function snapshotTime(payload) {
  // The moment the desktop last refreshed, not the moment we fetched it. A
  // frozen widget cannot tell how long it has been frozen, but a wall clock
  // printed into the tile stays true however long it sits there.
  let newest = null;
  for (const provider of payload.providers) {
    const parsed = Date.parse(provider.updated_at || "");
    if (!Number.isNaN(parsed) && (newest === null || parsed > newest)) newest = parsed;
  }
  return newest === null ? null : new Date(newest);
}

function clockLabel(date) {
  return `${date.getHours()}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function resetLabel(resetsAt) {
  if (!resetsAt) return "";
  const remaining = resetsAt * 1000 - Date.now();
  if (remaining <= 0) return "now";
  return ageLabel(remaining);
}

function addBar(container, percent, width) {
  const track = container.addStack();
  track.size = new Size(width, 4);
  track.layoutHorizontally();
  track.cornerRadius = 2;
  track.backgroundColor = TRACK;
  const fill = track.addStack();
  fill.size = new Size(Math.max(2, (width * Math.min(percent, 100)) / 100), 4);
  fill.cornerRadius = 2;
  fill.backgroundColor = colorFor(percent);
  // A stack centres its children, which left a 4% bar floating in the middle
  // of its own track. The trailing spacer is what pins the fill to the left.
  track.addSpacer();
}

function addWindowRow(container, provider, window, options) {
  const row = container.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();

  const name = row.addText(options.label);
  name.font = Font.mediumSystemFont(11);
  name.textColor = options.dim ? INK_3 : INK_2;
  name.lineLimit = 1;
  // "Claude/GPT 5H" does not fit a medium column at full size, and truncating
  // it leaves two rows that only the reset time tells apart.
  name.minimumScaleFactor = 0.7;

  row.addSpacer();

  if (options.showReset) {
    const reset = row.addText(resetLabel(window.resets_at));
    reset.font = Font.systemFont(9);
    reset.textColor = INK_3;
    row.addSpacer(6);
  }

  // Session windows first, then the weekly one the bar measures. Reading left
  // to right walks from the soonest reset to the longest, and the number the
  // bar belongs to sits closest to it.
  for (const extra of options.extras || []) {
    const tail = row.addText(percentLabel(extra));
    tail.font = Font.systemFont(10);
    tail.textColor =
      options.dim || extra.used_percent === null ? INK_3 : colorFor(extra.used_percent);
    row.addSpacer(4);
  }

  const value = row.addText(`${options.stale ? "~" : ""}${window.used_percent}%`);
  value.font = Font.boldSystemFont(12);
  value.textColor = options.dim ? INK_3 : INK;

  const barRow = container.addStack();
  barRow.layoutHorizontally();
  addBar(barRow, window.used_percent, options.barWidth);
  container.addSpacer(options.gap);
}

// Providers whose own name is not what the CLI is called.
const CELL_LABELS = { gemini: "Antigravity" };
// Large lists every window, where "Claude/GPT 5H" sitting next to
// "Claude 5H" needs saying which one belongs to Antigravity.
const SHORT_LABELS = { gemini: "AGY" };
// A circular Lock Screen widget has room for a number and about three
// characters, so "Antigravity" cannot simply be truncated to "Antigr".
const CODES = { codex: "CDX", claude: "CLD", grok: "GRK", gemini: "AGY" };

function providerCode(provider) {
  return CODES[provider.provider] || provider.label.slice(0, 3).toUpperCase();
}

function cellLabel(provider) {
  return CELL_LABELS[provider.provider] || provider.label;
}

const MISSING_WINDOW = { used_percent: 0, resets_at: null, label: "" };

function addBlock(container, cell, options) {
  const box = container.addStack();
  box.layoutVertically();
  box.size = new Size(options.width, options.height);

  const top = box.addStack();
  top.layoutHorizontally();
  top.centerAlignContent();
  const name = top.addText(cell.label);
  name.font = Font.semiboldSystemFont(11);
  name.textColor = options.dim ? INK_3 : INK_2;
  name.lineLimit = 1;
  name.minimumScaleFactor = 0.7;
  top.addSpacer();

  for (const extra of cell.extras || []) {
    const tail = top.addText(percentLabel(extra));
    tail.font = Font.semiboldSystemFont(10);
    tail.textColor =
      options.dim || extra.used_percent === null ? INK_3 : colorFor(extra.used_percent);
    top.addSpacer(4);
  }

  const value = top.addText(`${options.stale ? "~" : ""}${cell.window.used_percent}%`);
  value.font = Font.boldSystemFont(16);
  value.textColor = options.dim ? INK_3 : colorFor(cell.window.used_percent);

  box.addSpacer(5);
  addBar(box, cell.window.used_percent, options.width);
  box.addSpacer(4);

  const detail = box.addText(cell.detail);
  detail.font = Font.systemFont(9);
  detail.textColor = INK_3;
  detail.lineLimit = 1;
  detail.minimumScaleFactor = 0.7;
}

function qualify(provider, window) {
  // A bare cadence like "5H" needs the provider's name to mean anything.
  // Anything else already names itself: Gemini sends "Gemini 5H" and
  // "Claude/GPT 5H", which would otherwise read "Gemini Gemini 5H" and
  // "Gemini Claude/GPT 5H".
  if (!window.label) return provider.label;
  if (/^\d+[HD]$/i.test(window.label)) return `${provider.label} ${window.label}`;
  return window.label;
}

function tightest(windows) {
  // Most used wins; a tie goes to whichever resets first, and a tie there
  // keeps the order the backend sent. The backend lists Antigravity's Gemini
  // group before Claude/GPT, so an all-zero account shows Gemini.
  return windows.reduce((best, window) => {
    if (window.used_percent !== best.used_percent) {
      return window.used_percent > best.used_percent ? window : best;
    }
    const a = window.resets_at || Infinity;
    const b = best.resets_at || Infinity;
    return a < b ? window : best;
  }, windows[0]);
}

function largeCells(payload) {
  // Large is 2.24x the height of medium, which is exactly enough for one block
  // per window. Nothing has to be chosen or hidden here.
  const cells = [];
  for (const provider of payload.providers) {
    const short = SHORT_LABELS[provider.provider];
    if (provider.windows.length === 0) {
      cells.push({
        provider,
        label: cellLabel(provider),
        window: MISSING_WINDOW,
        detail: "no data",
      });
      continue;
    }
    for (const window of provider.windows) {
      const named = qualify(provider, window);
      cells.push({
        provider,
        label: short ? `${short} ${named}` : named,
        window,
        detail: `reset ${resetLabel(window.resets_at)}`,
      });
    }
  }
  return cells;
}

function orderedProviders(payload) {
  const found = PROVIDER_ORDER.map((id) =>
    payload.providers.find((p) => p.provider === id),
  ).filter(Boolean);
  // Anything the backend adds later still shows up, just after the known four.
  const extra = payload.providers.filter((p) => !PROVIDER_ORDER.includes(p.provider));
  return found.concat(extra);
}

function resetDetail(bar, also) {
  // The session window resets first and is the one you check before starting
  // something, so it leads here the same way it leads the numbers.
  const session = also[0];
  if (!session || session.used_percent === null) {
    return `${bar.label} · ${resetLabel(bar.resets_at)}`;
  }
  return `${session.label} ${resetLabel(session.resets_at)} · ${bar.label} ${resetLabel(bar.resets_at)}`;
}

function mediumCells(payload) {
  return orderedProviders(payload).map((provider) => {
    const label = cellLabel(provider);
    const { bar, also } = pickWindows(provider);
    if (!bar) {
      return { provider, label, window: MISSING_WINDOW, extras: [], detail: "no data" };
    }
    return { provider, label, window: bar, extras: also, detail: resetDetail(bar, also) };
  });
}

function smallRows(payload) {
  return orderedProviders(payload).map((provider) => {
    const { bar, also } = pickWindows(provider);
    return {
      provider,
      label: cellLabel(provider),
      window: bar || MISSING_WINDOW,
      // 132pt of row holds a name and two numbers, so a provider configured
      // with more of them loses the surplus here rather than overflowing.
      extras: also.slice(0, 1),
    };
  });
}

// Lock Screen and StandBy widgets are rendered monochrome by iOS, so the
// severity colours are gone and the fill proportion has to carry the meaning.
function accessoryGauge(percent, size) {
  const ctx = new DrawContext();
  ctx.size = new Size(size, size);
  ctx.opaque = false;
  ctx.respectScreenScale = true;

  const stroke = 6;
  const radius = (size - stroke) / 2;
  const centre = size / 2;

  ctx.setLineWidth(stroke);
  ctx.setStrokeColor(new Color("#FFFFFF", 0.28));
  ctx.strokeEllipse(new Rect(stroke / 2, stroke / 2, size - stroke, size - stroke));

  const swept = (Math.min(Math.max(percent, 0), 100) / 100) * 2 * Math.PI;
  if (swept > 0) {
    const start = -Math.PI / 2;
    const steps = Math.max(2, Math.round(swept / 0.06));
    const points = [];
    for (let i = 0; i <= steps; i += 1) {
      const angle = start + (swept * i) / steps;
      points.push(new Point(centre + radius * Math.cos(angle), centre + radius * Math.sin(angle)));
    }
    const path = new Path();
    path.addLines(points);
    ctx.addPath(path);
    ctx.setStrokeColor(new Color("#FFFFFF"));
    ctx.setLineWidth(stroke);
    ctx.strokePath();
  }
  return ctx.getImage();
}

function accessoryRows(payload) {
  return orderedProviders(payload)
    .map((provider) => {
      const { bar } = pickWindows(provider);
      return bar ? { provider, label: cellLabel(provider), window: bar } : null;
    })
    .filter(Boolean);
}

function buildAccessory(state, family) {
  const widget = new ListWidget();
  widget.setPadding(0, 0, 0, 0);
  widget.refreshAfterDate = new Date(Date.now() + REFRESH_AFTER_MS);

  if (!state.payload) {
    const message = widget.addText("no data");
    message.font = Font.systemFont(12);
    return widget;
  }

  const rows = accessoryRows(state.payload);
  // A Lock Screen glance has no room to explain itself, but showing an hours
  // old number as if it were current is worse than one extra character.
  const age = state.fetchedAt ? Date.now() - state.fetchedAt : null;
  const mark = !state.reachable || (age !== null && age > STALE_AFTER_MS) ? "~" : "";

  if (family === "accessoryCircular") {
    // One number is all a circle holds. By default it holds whichever is
    // closest to running out; pin CIRCULAR_PROVIDER to always watch one.
    const pinned = rows.find((row) => row.provider.provider === CIRCULAR_PROVIDER);
    const worst =
      pinned ||
      rows.reduce((a, b) => (b.window.used_percent > a.window.used_percent ? b : a), rows[0]);
    const size = 58;
    const box = widget.addStack();
    box.size = new Size(size, size);
    box.backgroundImage = accessoryGauge(worst.window.used_percent, size);
    box.layoutVertically();
    box.addSpacer();
    const line = box.addStack();
    line.layoutHorizontally();
    line.addSpacer();
    const value = line.addText(`${mark}${worst.window.used_percent}`);
    value.font = Font.boldSystemFont(15);
    value.minimumScaleFactor = 0.7;
    line.addSpacer();
    const tag = box.addStack();
    tag.layoutHorizontally();
    tag.addSpacer();
    const name = tag.addText(providerCode(worst.provider));
    name.font = Font.mediumSystemFont(9);
    tag.addSpacer();
    box.addSpacer();
    return widget;
  }

  // accessoryRectangular: every provider, one compact line each.
  widget.setPadding(1, 2, 1, 2);
  for (const [index, row] of rows.entries()) {
    if (index > 0) widget.addSpacer(2);
    const line = widget.addStack();
    line.layoutHorizontally();
    line.centerAlignContent();
    // No minimumScaleFactor here. Letting long names shrink meant "Antigravity"
    // rendered smaller than "Grok", so the four rows disagreed about type size.
    // 172pt fits every provider name at 11pt, so truncation never comes up.
    const name = line.addText(row.label);
    name.font = Font.systemFont(11);
    name.lineLimit = 1;
    line.addSpacer();
    const value = line.addText(`${mark}${row.window.used_percent}%`);
    value.font = Font.semiboldSystemFont(11);
  }
  return widget;
}

function addOfflineMark(container) {
  const mark = container.addText("\u2715");
  mark.font = Font.boldSystemFont(11);
  mark.textColor = RED;
}

function buildWidget(state, family) {
  const widget = new ListWidget();
  widget.setPadding(12, 13, 12, 13);
  widget.backgroundColor = Color.dynamic(new Color("#FFFFFF"), new Color("#1C1C1E"));
  // iOS treats this as a hint, not a schedule: widgets share a daily refresh
  // budget, and asking every 5 minutes is far more than it will grant. Asking
  // anyway means it refreshes as soon as the budget allows instead of waiting
  // out a longer interval we made up.
  widget.refreshAfterDate = new Date(Date.now() + REFRESH_AFTER_MS);
  widget.url = ENDPOINT;

  if (!state.payload) {
    const title = widget.addText("Usage");
    title.font = Font.boldSystemFont(13);
    title.textColor = INK;
    widget.addSpacer(6);
    const message = widget.addText("Publisher unreachable, and no snapshot cached yet.");
    message.font = Font.systemFont(11);
    message.textColor = INK_3;
    return widget;
  }

  const large = family === "large";
  const medium = family === "medium" || large;
  const barWidth = medium ? 146 : 132;
  const age = state.fetchedAt ? Date.now() - state.fetchedAt : null;
  const offline = !state.reachable;
  const oldData = age !== null && age > STALE_AFTER_MS;

  const header = widget.addStack();
  header.layoutHorizontally();
  header.centerAlignContent();
  const title = header.addText("Usage");
  title.font = Font.boldSystemFont(12);
  title.textColor = INK;
  header.addSpacer();
  const stamp = snapshotTime(state.payload);
  if (offline) {
    // The clock alone cannot say why it stopped moving, so mark the reason:
    // these numbers are the last ones fetched, not the current ones.
    addOfflineMark(header);
    header.addSpacer(3);
  }
  if (stamp || offline) {
    const badge = header.addText(stamp ? clockLabel(stamp) : "offline");
    badge.font = Font.systemFont(9);
    badge.textColor = offline || oldData ? AMBER : INK_3;
  }
  widget.addSpacer(8);

  const firstError = (state.payload.providers.find((p) => p.error) || {});

  if (medium) {
    // Medium is twice as wide as small but exactly as tall, so the extra room
    // buys a second column of blocks, not more rows. Two by two, one provider
    // per block, each with room for a real number and its reset.
    const cells = large ? largeCells(state.payload) : mediumCells(state.payload);
    const cellWidth = (312 - 12) / 2;
    for (let start = 0; start < cells.length; start += 2) {
      if (start > 0) widget.addSpacer(large ? 12 : 9);
      const band = widget.addStack();
      band.layoutHorizontally();
      for (const [index, cell] of cells.slice(start, start + 2).entries()) {
        if (index > 0) band.addSpacer(12);
        addBlock(band, cell, {
          width: cellWidth,
          height: large ? 62 : 50,
          stale: cell.provider.status !== "fresh" || (oldData && !offline),
          dim: offline,
        });
      }
    }
    // Without this the blocks float in the middle of a large widget.
    if (large) widget.addSpacer();
  } else {
    const rows = smallRows(state.payload);
    const gap = rows.length > 3 ? 4 : 6;
    for (const row of rows) {
      addWindowRow(widget, row.provider, row.window, {
        label: row.label,
        extras: row.extras,
        stale: row.provider.status !== "fresh" || (oldData && !offline),
        dim: offline,
        showReset: false,
        barWidth,
        gap,
      });
    }
  }

  if (firstError.error && medium) {
    widget.addSpacer(2);
    const note = widget.addText(`${firstError.label}: ${firstError.error}`);
    note.font = Font.systemFont(9);
    note.textColor = AMBER;
    note.lineLimit = 2;
  }

  return widget;
}

const state = await loadPayload();
const family = config.widgetFamily || PREVIEW_FAMILY;
const widget = family.startsWith("accessory")
  ? buildAccessory(state, family)
  : buildWidget(state, family);

if (config.runsInWidget) {
  Script.setWidget(widget);
} else if (family === "small") {
  await widget.presentSmall();
} else if (family === "large") {
  await widget.presentLarge();
} else if (family === "accessoryCircular") {
  await widget.presentAccessoryCircular();
} else if (family === "accessoryRectangular") {
  await widget.presentAccessoryRectangular();
} else {
  await widget.presentMedium();
}
Script.complete();
