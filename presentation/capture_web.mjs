// Headless Edge + Chrome DevTools Protocol screenshots of the PathSense web app (for the SIH deck).
// usage: node shoot.mjs <outDir>
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const OUT = process.argv[2];
mkdirSync(OUT, { recursive: true });
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 9333;
const prof = join(OUT, "_profile");
const edge = spawn(EDGE, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${prof}`, "--hide-scrollbars",
  "--autoplay-policy=no-user-gesture-required", "--mute-audio", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let targets;
for (let i = 0; i < 50; i++) {
  try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json(); if (targets.length) break; } catch { /* starting */ }
  await sleep(200);
}
const page = targets.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const evalJs = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;

async function shot(name, { url, w = 1440, h = 900, mobile = false, theme = "dark", js = null, wait = 2500 }) {
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 2, mobile });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
  await send("Page.navigate", { url: url.replace("/#", `/?shot=${name}#`) });
  await sleep(wait);
  await evalJs(`try{localStorage.setItem('ps-theme','${theme}')}catch(e){}; applyTheme('${theme}'); true`);
  if (js) { await evalJs(`(async()=>{${js}})()`); await sleep(1500); }
  const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  writeFileSync(join(OUT, name + ".png"), Buffer.from(r.result.data, "base64"));
  console.log("shot", name);
}

await send("Page.enable"); await send("Runtime.enable");
const B = "http://127.0.0.1:8000/";
const pick = (sub, t) => `const d=state.demos.find(x=>x.name.includes('${sub}')&&x.browser_playable)||state.demos.find(x=>x.browser_playable); await selectDemo(d.name); video.currentTime=${t}; await new Promise(r=>video.addEventListener('seeked',r,{once:true})); await new Promise(r=>setTimeout(r,800)); updatePanel();`;
const cfg = JSON.parse(process.env.SHOTS || "{}");
await shot("web_demo", { url: B + "#demo", js: pick(cfg.demo || "india_bangalore", cfg.demo_t || 20) });
await shot("web_demo_light", { url: B + "#demo", theme: "light", js: pick(cfg.demo || "india_bangalore", cfg.demo_t || 20) });
await shot("web_events", { url: B + "#events", js: pick(cfg.events || "india_bangalore", 1) + "showView('events'); state.evFilter='brake'; renderEvents(); const e=state.events.events.find(x=>x.type==='decision'&&x.state==='BRAKE'); if(e) await selectEvent(e.id);" });
await shot("web_emergency", { url: B + "#emergency", js: "await triggerEmergency(0.9, 'demo trigger (button) — not a real detection'); await new Promise(r=>setTimeout(r,6000));", wait: 2500 });
await shot("web_system", { url: B + "#system" });
await shot("web_mobile", { url: B + "#demo", w: 390, h: 844, mobile: true, js: pick(cfg.demo || "india_bangalore", cfg.demo_t || 20) + "scrollTo(0,0);" });
ws.close(); edge.kill();
