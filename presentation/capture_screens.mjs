// Captures REAL screenshots of the running PathSense web app for the deck's Prototype slide.
//   1) start the app:  .venv/Scripts/python.exe app.py      (http://127.0.0.1:8000)
//   2) node presentation/capture_screens.mjs [theme=light|dark]   -> presentation/assets/screens/*.png
// Uses headless Edge/Chrome through the DevTools protocol (Node 22). Nothing is drawn or edited: each PNG is the
// browser viewport exactly as the app renders it (16:9 viewports, so the slide needs no cropping).
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, rmSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";

const THEME = process.argv[2] || "light";
const BASE = process.env.PATHSENSE_URL || "http://127.0.0.1:8000/";
const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "assets", "screens");
mkdirSync(OUT, { recursive: true });
const BROWSER = process.env.BROWSER || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 9343;
const prof = join(tmpdir(), "pathsense_screens_profile");
const proc = spawn(BROWSER, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${prof}`, "--hide-scrollbars",
  "--autoplay-policy=no-user-gesture-required", "--mute-audio", "about:blank"], { stdio: "ignore" });
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
const evalJs = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
await send("Page.enable"); await send("Runtime.enable");

async function shot(name, hash, js = null, wait = 3000, w = 1440, h = 810) {
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 2, mobile: false });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: THEME }] });
  await send("Page.navigate", { url: BASE + hash });
  await sleep(wait);
  await evalJs(`try{localStorage.setItem('ps-theme','${THEME}')}catch(e){}; typeof applyTheme==='function' && applyTheme('${THEME}'); true`);
  if (js) { await evalJs(`(async()=>{${js}})()`); await sleep(1500); }
  const r = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(OUT, `${name}.png`), Buffer.from(r.result.data, "base64"));
  console.log("shot", name);
}

// open a processed drive in the Demo view and seek to a moment
const DEMO = "india_bangalore";
const pick = (t) => `const d=state.demos.find(x=>x.name.includes('${DEMO}')&&x.browser_playable)||state.demos.find(x=>x.browser_playable);
  await selectDemo(d.name); video.pause(); video.currentTime=${t};
  await new Promise(r=>video.addEventListener('seeked',r,{once:true})); await new Promise(r=>setTimeout(r,900)); updatePanel();`;
await shot("1_home", "#home", null, 3500);
await shot("2_demo", "#demo", pick(59.4) + "scrollTo(0,0);");
await shot("3_replay", "#demo", pick(1) + `document.querySelectorAll('.moment')[2]?.click(); await new Promise(r=>setTimeout(r,600));
  scrollTo(0, document.querySelector('#replay').getBoundingClientRect().top + scrollY - 190);`, 3000, 1280, 720);
await shot("4_emergency", "#emergency");
ws.close(); proc.kill();
await sleep(500);
try { rmSync(prof, { recursive: true, force: true }); } catch { /* profile still locked */ }
