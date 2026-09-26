// Renders the original concept scenes in presentation/hero/scene.html to PNG with headless Edge/Chrome
// (Chrome DevTools Protocol, Node 22).        node presentation/hero/render_hero.mjs [name,name,...]
// Output: presentation/assets/scenes/<name>.png.  Set BROWSER to a chrome/msedge executable if Edge is elsewhere.
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, rmSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { tmpdir } from "node:os";

// name -> query string and size. Every image is an original render; none is a prototype screenshot.
const SHOTS = {
  road:       { qs: "scene=road&view=iso&corridor=0", w: 2400, h: 1500 },     // unstructured road, mixed traffic
  perception: { qs: "scene=road&view=chase&boxes=1&corridor=0", w: 2400, h: 1500 },
  planning:   { qs: "scene=road&view=top&fan=1", w: 2400, h: 1500 },
  market:     { qs: "scene=market&view=iso", w: 2400, h: 1500 },
  village:    { qs: "scene=village&view=iso", w: 2400, h: 1500 },
  corridor:   { qs: "scene=road&view=chase", w: 2400, h: 1500 },
  before:     { qs: "scene=road&view=chase&naive=1", w: 2400, h: 1500 },     // lane-following baseline (concept)
};

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "..", "assets", "scenes");
mkdirSync(OUT, { recursive: true });
const BROWSER = process.env.BROWSER || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 9341;
const prof = join(tmpdir(), "pathsense_hero_profile");
const proc = spawn(BROWSER, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${prof}`, "--hide-scrollbars",
  "--enable-webgl", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let targets;
for (let i = 0; i < 60; i++) {
  try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json(); if (targets.length) break; } catch { /* starting */ }
  await sleep(250);
}
const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
await send("Page.enable"); await send("Runtime.enable");

const names = process.argv[2] ? process.argv[2].split(",") : Object.keys(SHOTS);
for (const name of names) {
  const { qs, w, h } = SHOTS[name];
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${pathToFileURL(join(HERE, "scene.html")).href}?${qs}&w=${w}&h=${h}` });
  let ok = false;
  for (let i = 0; i < 240 && !ok; i++) {
    await sleep(500);
    ok = (await send("Runtime.evaluate", { expression: "window.__ready===true", returnByValue: true })).result?.result?.value;
  }
  if (!ok) { console.error("scene did not render:", name); continue; }
  await sleep(500);
  const r = await send("Page.captureScreenshot", { format: "png" });
  const f = join(OUT, `${name}.png`);
  writeFileSync(f, Buffer.from(r.result.data, "base64"));
  console.log("wrote", f);
}
ws.close(); proc.kill();
await sleep(500);
try { rmSync(prof, { recursive: true, force: true }); } catch { /* profile still locked */ }
