// Renders presentation/hero/scene.html to PNG with headless Edge/Chrome (Chrome DevTools Protocol, Node 22).
//   node presentation/hero/render_hero.mjs            -> presentation/assets/hero_{hero,iso}.png
// Set BROWSER to a chrome/msedge executable if Edge is not at its default Windows path.
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, rmSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { tmpdir } from "node:os";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "..", "assets");
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

const views = (process.argv[2] || "hero,iso").split(",");
for (const view of views) {
  const [w, h] = view === "hero" ? [2800, 2240] : [3200, 1800];
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${pathToFileURL(join(HERE, "scene.html")).href}?view=${view}&w=${w}&h=${h}` });
  let ok = false;
  for (let i = 0; i < 240 && !ok; i++) {
    await sleep(500);
    ok = (await send("Runtime.evaluate", { expression: "window.__ready===true", returnByValue: true })).result?.result?.value;
  }
  if (!ok) { console.error("scene did not render:", view); continue; }
  await sleep(500);
  const r = await send("Page.captureScreenshot", { format: "png" });
  const f = join(OUT, `hero_${view}.png`);
  writeFileSync(f, Buffer.from(r.result.data, "base64"));
  const anchors = (await send("Runtime.evaluate", { expression: "window.__anchors", returnByValue: true })).result?.result?.value;
  writeFileSync(join(OUT, `hero_${view}_anchors.json`), JSON.stringify({ width: w, height: h, anchors }, null, 1));
  console.log("wrote", f);
}
ws.close(); proc.kill();
await sleep(500);
try { rmSync(prof, { recursive: true, force: true }); } catch { /* profile still locked */ }
