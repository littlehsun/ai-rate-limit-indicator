// Rate Limit Indicator — iOS home screen widget for Scriptable.
//
// Reads the snapshot published by `publish.py` on the Tailscale network. The
// publishing machine sleeps, so every state below has to survive that: a stale
// snapshot and an unreachable host are normal, not exceptional.
//
// Setup: set ENDPOINT to your publisher, run once in Scriptable to check it,
// then add a Scriptable widget to the home screen and pick this script.

const ENDPOINT = "http://127.0.0.1:8477/usage.json";

// Rows for the small widget, in order. Four rows is what fits, so a provider
// that needs two windows either spends two of them or sets `combine`.
//
//   (omitted)                  the provider's first window
//   windows: [a, b]            one row each
//   windows: [a, b], combine   one row: `a` draws the bar, `b` rides along as
//                              a second number
const SMALL_ROWS = [
  { provider: "codex" },
  { provider: "claude", windows: ["5h", "7d"], combine: true },
  { provider: "grok" },
  { provider: "gemini" },
];

// Matches UsageColor in the macOS app so both surfaces agree on "bad".
const RED = new Color("#FF5454");
const AMBER = new Color("#FFB82E");
const GREEN = new Color("#00B04F");

// A snapshot older than this is called out even if the publisher claims fresh,
// because the publisher can only report what it knew before it went to sleep.
const STALE_AFTER_MS = 20 * 60 * 1000;
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

async function loadPayload() {
  const fm = FileManager.local();
  const path = cachePath();
  try {
    const request = new Request(ENDPOINT);
    request.timeoutInterval = 10;
    const payload = await request.loadJSON();
    if (!payload || !Array.isArray(payload.providers)) throw new Error("bad payload");
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

  const value = row.addText(`${options.stale ? "~" : ""}${window.used_percent}%`);
  value.font = Font.boldSystemFont(12);
  value.textColor = options.dim ? INK_3 : INK;

  for (const extra of options.extras || []) {
    row.addSpacer(4);
    // Only one window can own the bar, so the others are numbers only. They
    // stay lighter so the row still reads as one provider, not two.
    const tail = row.addText(`${extra.used_percent}%`);
    tail.font = Font.systemFont(10);
    tail.textColor = options.dim ? INK_3 : colorFor(extra.used_percent);
  }

  const barRow = container.addStack();
  barRow.layoutHorizontally();
  addBar(barRow, window.used_percent, options.barWidth);
  container.addSpacer(options.gap);
}

// Providers whose own name is not what the CLI is called.
const CELL_LABELS = { gemini: "Antigravity" };

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

  const value = top.addText(`${options.stale ? "~" : ""}${cell.window.used_percent}%`);
  value.font = Font.boldSystemFont(16);
  value.textColor = options.dim ? INK_3 : colorFor(cell.window.used_percent);

  if (cell.secondary) {
    top.addSpacer(4);
    const tail = top.addText(`${cell.secondary.used_percent}%`);
    tail.font = Font.semiboldSystemFont(10);
    tail.textColor = options.dim ? INK_3 : colorFor(cell.secondary.used_percent);
  }

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

function mediumCells(payload) {
  return payload.providers.map((provider) => {
    const label = cellLabel(provider);
    const windows = provider.windows;
    if (windows.length === 0) {
      return { provider, label, window: MISSING_WINDOW, detail: "no data" };
    }
    if (windows.length === 1) {
      return {
        provider,
        label,
        window: windows[0],
        detail: `${windows[0].label} · ${resetLabel(windows[0].resets_at)}`,
      };
    }
    if (windows.length === 2) {
      // A provider with exactly a session and a weekly window shows both, the
      // way Claude does: the bar tracks the first, the second rides along.
      return {
        provider,
        label,
        window: windows[0],
        secondary: windows[1],
        detail: windows
          .map((w) => `${w.label} ${resetLabel(w.resets_at)}`)
          .join(" · "),
      };
    }
    // Antigravity sends four: Gemini and Claude/GPT, each with a session and a
    // weekly window. One cell cannot hold four, so it holds whichever is
    // closest to running out, and names which one that is.
    const window = tightest(windows);
    return {
      provider,
      label,
      window,
      detail: `${window.label} · ${resetLabel(window.resets_at)}`,
    };
  });
}

function smallRows(payload) {
  const rows = [];
  for (const wanted of SMALL_ROWS) {
    const provider = payload.providers.find((p) => p.provider === wanted.provider);
    if (!provider) continue;
    const windows = wanted.windows
      ? wanted.windows.map((id) => provider.windows.find((w) => w.id === id)).filter(Boolean)
      : provider.windows.slice(0, 1);
    if (windows.length === 0) {
      rows.push({ provider, window: MISSING_WINDOW, label: cellLabel(provider) });
      continue;
    }
    if (wanted.combine) {
      // The bar can only track one window, so the rest ride along as numbers.
      rows.push({
        provider,
        window: windows[0],
        label: cellLabel(provider),
        extras: windows.slice(1),
      });
      continue;
    }
    for (const window of windows) {
      // One row can carry the provider's name alone; two rows for the same
      // provider have to say which window each one is.
      rows.push({
        provider,
        window,
        label: windows.length > 1 ? qualify(provider, window) : cellLabel(provider),
      });
    }
  }
  return rows;
}

function buildWidget(state, family) {
  const widget = new ListWidget();
  widget.setPadding(12, 13, 12, 13);
  widget.backgroundColor = Color.dynamic(new Color("#FFFFFF"), new Color("#1C1C1E"));
  widget.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
  widget.url = ENDPOINT;

  if (!state.payload) {
    const title = widget.addText("Rate Limits");
    title.font = Font.boldSystemFont(13);
    title.textColor = INK;
    widget.addSpacer(6);
    const message = widget.addText("Publisher unreachable, and no snapshot cached yet.");
    message.font = Font.systemFont(11);
    message.textColor = INK_3;
    return widget;
  }

  const medium = family === "medium" || family === "large";
  const barWidth = medium ? 146 : 132;
  const age = state.fetchedAt ? Date.now() - state.fetchedAt : null;
  const offline = !state.reachable;
  const oldData = age !== null && age > STALE_AFTER_MS;

  const header = widget.addStack();
  header.layoutHorizontally();
  header.centerAlignContent();
  const title = header.addText("Rate Limits");
  title.font = Font.boldSystemFont(12);
  title.textColor = INK;
  header.addSpacer();
  if (offline || oldData) {
    const badge = header.addText(age === null ? "offline" : ageLabel(age));
    badge.font = Font.systemFont(9);
    badge.textColor = offline ? AMBER : INK_3;
  }
  widget.addSpacer(8);

  const firstError = (state.payload.providers.find((p) => p.error) || {});

  if (medium) {
    // Medium is twice as wide as small but exactly as tall, so the extra room
    // buys a second column of blocks, not more rows. Two by two, one provider
    // per block, each with room for a real number and its reset.
    const cells = mediumCells(state.payload);
    const cellWidth = (312 - 12) / 2;
    for (let start = 0; start < cells.length; start += 2) {
      if (start > 0) widget.addSpacer(9);
      const band = widget.addStack();
      band.layoutHorizontally();
      for (const [index, cell] of cells.slice(start, start + 2).entries()) {
        if (index > 0) band.addSpacer(12);
        addBlock(band, cell, {
          width: cellWidth,
          height: 50,
          stale: cell.provider.status !== "fresh" || oldData,
          dim: offline,
        });
      }
    }
  } else {
    const rows = smallRows(state.payload);
    const gap = rows.length > 3 ? 4 : 6;
    for (const row of rows) {
      addWindowRow(widget, row.provider, row.window, {
        label: row.label,
        extras: row.extras,
        stale: row.provider.status !== "fresh" || oldData,
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
const widget = buildWidget(state, config.widgetFamily || "medium");

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  await widget.presentMedium();
}
Script.complete();
