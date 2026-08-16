// Runs usage-widget.js against stubbed Scriptable globals and the real
// snapshot, so the row selection and labels can be checked without an iPhone.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const SOURCE = "/Users/hsun/Hsun/rate-limit-indicator/mobile/usage-widget.js";
const SNAPSHOT = path.join(os.homedir(), ".cache/rate-limit-indicator/snapshots.json");

class Stack {
  constructor(sink) { this.sink = sink; this.kind = "stack"; }
  layoutHorizontally() {}
  layoutVertically() {}
  centerAlignContent() {}
  addSpacer() {}
  addStack() { const s = new Stack(this.sink); this.sink.push({ type: "stack", node: s }); return s; }
  addText(t) { const n = { type: "text", text: t }; this.sink.push(n); return n; }
  addImage(i) { this.sink.push({ type: "text", text: `[${(i&&i.sym)||"image"}]` }); return {}; }
}

class ListWidget {
  constructor() { this.nodes = []; }
  setPadding() {}
  addSpacer() {}
  addStack() { const s = new Stack(this.nodes); this.nodes.push({ type: "stack", node: s }); return s; }
  addText(t) { const n = { type: "text", text: t }; this.nodes.push(n); return n; }
  async presentMedium() {}
}

function flatten(nodes, out = []) {
  for (const n of nodes) {
    if (n.type === "text") out.push(n.text);
  }
  return out;
}

globalThis.Color = function (hex, alpha) { return { hex, alpha }; };
globalThis.Color.dynamic = (a, b) => ({ light: a, dark: b });
globalThis.Font = new Proxy({}, { get: () => (n) => ({ n }) });
globalThis.Size = function (w, h) { return { w, h }; };
globalThis.ListWidget = ListWidget;
globalThis.args = { widgetParameter: null };
globalThis.__sym = null;
globalThis.SFSymbol = { named: (n) => { globalThis.__sym = n; return { applyFont() {}, image: { sym: n } }; } };
globalThis.Rect = function (x, y, w, h) { return { x, y, w, h }; };
globalThis.Point = function (x, y) { return { x, y }; };
globalThis.Path = class { addLines() {} };
globalThis.DrawContext = class {
  setLineWidth() {} setStrokeColor() {} strokeEllipse() {}
  addPath() {} strokePath() {} getImage() { return { image: true }; }
};

const payload = JSON.parse(fs.readFileSync(SNAPSHOT, "utf8"));
globalThis.Request = class {
  constructor(url) { this.url = url; }
  async loadJSON() {
    if (globalThis.__FAIL_FETCH) throw new Error("unreachable");
    return payload;
  }
};
globalThis.FileManager = {
  local: () => ({
    cacheDirectory: () => "/tmp",
    joinPath: (a, b) => path.join(a, b),
    fileExists: () => Boolean(globalThis.__CACHED),
    writeString: () => {},
    readString: () => JSON.stringify({ fetchedAt: globalThis.__CACHE_AGE, payload }),
  }),
};

let captured = null;
globalThis.Script = { setWidget: (w) => { captured = w; }, complete: () => {} };

const source = fs.readFileSync(SOURCE, "utf8");

async function render(family, { fail = false, cached = false, cacheAge = Date.now() } = {}) {
  globalThis.__FAIL_FETCH = fail;
  globalThis.__CACHED = cached;
  globalThis.__CACHE_AGE = cacheAge;
  globalThis.config = { widgetFamily: family, runsInWidget: true };
  captured = null;
  const module = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
  await import(`${module}#${family}-${fail}-${cached}-${cacheAge}`);
  return flatten(captured.nodes);
}

const rows = (lines) => lines.slice(1);

console.log("=== SMALL（正常）===");
for (const line of await render("small")) console.log("  " + line);

console.log("\n=== MEDIUM（正常）===");
for (const line of await render("medium")) console.log("  " + line);

console.log("\n=== SMALL（連不上，有 cache，3 小時前）===");
for (const line of await render("small", { fail: true, cached: true, cacheAge: Date.now() - 3 * 3600 * 1000 })) {
  console.log("  " + line);
}

console.log("\n=== SMALL（連不上，完全沒 cache）===");
for (const line of await render("small", { fail: true, cached: false })) console.log("  " + line);

console.log("\n=== LARGE（正常）===");
for (const line of await render("large")) console.log("  " + line);

for (const fam of ["accessoryCircular", "accessoryRectangular"]) {
  console.log(`\n=== ${fam}（正常）===`);
  for (const line of await render(fam)) console.log("  " + line);
  console.log(`=== ${fam}（連不上）===`);
  for (const line of await render(fam, { fail: true, cached: true, cacheAge: Date.now() - 3*3600*1000 })) console.log("  " + line);
}

console.log("\n=== 行數檢查 ===");
// A row starts with its label; percentages and reset times trail it.
const countRows = (lines) => lines.filter((t) => !/%$/.test(t) && !/^\d+[hdm]/.test(t)).length;
const small = countRows(rows(await render("small")));
console.log(`  small 行數: ${small}  ${small <= 4 ? "✓ 放得下" : "✗ 會超出高度"}`);
const medium = countRows(rows(await render("medium")));
const perCol = Math.ceil(medium / 2);
console.log(`  medium 行數: ${medium}，分兩欄各 ${perCol} 行  ${perCol <= 4 ? "✓ 放得下" : "✗ 會超出高度"}`);
