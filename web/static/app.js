/* PathSense web client. All decisions come from the Python pipeline (app.py); this file only displays them. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const api = async (url, opts) => {
  const r = await fetch(url, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || r.statusText), { status: r.status, body: j });
  return j;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, unit = "", nd = 1) => (v === null || v === undefined ? "—" : `${Number(v).toFixed(nd)}<small>${unit}</small>`);
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** decision label -> style key (matches overlay.py colours; text is always shown too, never colour alone) */
function styleKey(label) {
  if (!label) return "GO";
  if (label.startsWith("NO SAFE")) return "NSP";
  if (label.startsWith("BRAKE")) return "BRAKE";
  if (label.startsWith("SLOW")) return "SLOW";
  if (label.startsWith("STEER")) return "STEER";
  return "GO";
}
const cssVar = (k) => getComputedStyle(document.documentElement).getPropertyValue(k).trim();
const colorOf = (label) => cssVar({ GO: "--go", SLOW: "--slow", BRAKE: "--brake", NSP: "--nsp", STEER: "--steer" }[styleKey(label)]);

/* ------------------------------------------------------------------ theme */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  $("#themeBtn use").setAttribute("href", t === "dark" ? "#i-sun" : "#i-moon");
  $("#themeBtn").setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
  $('meta[name="theme-color"]').content = t === "dark" ? "#0b0e11" : "#f5f7fa";
  try { localStorage.setItem("ps-theme", t); } catch (e) { /* storage unavailable */ }
  if (state.demo) drawTimeline();
}
$("#themeBtn").onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");

/* ------------------------------------------------------------------ tabs */
function showView(v) {
  $$(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.view === v)));
  $$(".view").forEach((s) => s.classList.toggle("active", s.id === `view-${v}`));
  if (v === "system") loadSystem();
  if (v === "emergency") loadContact();
  if (v === "events") renderEvents();
  history.replaceState(null, "", `#${v}`);
}
$$(".tab").forEach((t) => (t.onclick = () => showView(t.dataset.view)));
const VIEWS = ["demo", "live", "events", "emergency", "system"];
window.addEventListener("hashchange", () => { const v = location.hash.slice(1); if (VIEWS.includes(v)) showView(v); });

/* ------------------------------------------------------------------ demo player */
const state = { demos: [], demo: null, events: null, frames: null, fps: 25, evFilter: "all", evSel: null };
const video = $("#video");

async function loadDemos(select) {
  state.demos = await api("/api/demos");
  const lib = $("#library");
  if (!state.demos.length) {
    lib.innerHTML = `<div class="callout">No processed videos in <span class="mono">outputs/</span> yet. Upload one to get started.</div>`;
    return;
  }
  lib.innerHTML = state.demos.map((d) => {
    const pct = d.decision_pct || {};
    const bar = Object.entries(pct).filter(([, v]) => v > 0)
      .map(([k, v]) => `<span style="width:${v}%;background:${colorOf(k)}" title="${esc(k)} ${v}%"></span>`).join("");
    return `<button class="demo-card" data-name="${esc(d.name)}">
      <div class="t">${esc(d.title)}${d.uploaded ? ' <span class="chip" style="font-size:11px">upload</span>' : ""}</div>
      <div class="m">${d.duration_s ? `<span>${d.duration_s}s</span>` : ""}${d.frames ? `<span>${d.frames} frames</span>` : ""}
        ${d.brake_pct !== null && d.brake_pct !== undefined ? `<span>BRAKE ${d.brake_pct}%</span>` : ""}
        ${d.accidents ? `<span style="color:var(--brake)">accident ×${d.accidents}</span>` : ""}</div>
      <div class="bar">${bar}</div>
      ${d.browser_playable ? "" : '<div class="warn">Older encoding (mp4v) — may not play in the browser; re-render to fix.</div>'}
    </button>`;
  }).join("");
  $$(".demo-card", lib).forEach((b) => (b.onclick = () => selectDemo(b.dataset.name, true)));
  const want = select || (state.demo && state.demo.name) || (state.demos.find((d) => d.browser_playable) || state.demos[0]).name;
  selectDemo(want, false);
}

async function selectDemo(name, play) {
  const d = state.demos.find((x) => x.name === name);
  if (!d) return;
  state.demo = d; state.events = null; state.frames = null; state.evSel = null;
  $$(".demo-card").forEach((b) => b.setAttribute("aria-current", String(b.dataset.name === name)));
  $("#playerEmpty").hidden = true;
  video.src = d.video_url;
  if (play) video.play().catch(() => {});
  $("#evTitle").textContent = `Events — ${d.title}`;
  const enc = encodeURIComponent(name).replace(/%2F/g, "/");
  const [ev, fr] = await Promise.all([
    d.has_events ? api(`/api/demos/${enc}/events`).catch(() => null) : null,
    d.has_frames ? api(`/api/demos/${enc}/frames`).catch(() => null) : null,
  ]);
  if (state.demo !== d) return;
  state.events = ev; state.frames = fr;
  state.fps = (fr && fr.fps) || (ev && ev.fps) || d.fps || 25;
  drawTimeline(); renderEvents(); updatePanel();
}

function drawTimeline() {
  const tl = $("#timeline");
  const ev = state.events;
  if (!ev) { tl.innerHTML = ""; return; }
  const total = ev.frames || 1;
  const segs = (ev.segments || []).map((s) =>
    `<div class="seg" style="left:${(100 * s.start_frame) / total}%;width:${(100 * (s.end_frame - s.start_frame + 1)) / total}%;background:${colorOf(s.decision)}" title="${esc(s.decision)} @ ${s.start_s}s"></div>`).join("");
  const accs = (ev.events || []).filter((e) => e.type === "accident" && e.accident_state === "CONFIRMED")
    .map((e) => `<div class="acc" style="left:${(100 * e.frame) / total}%" title="Accident (confirmed) @ ${e.time_s}s"></div>`).join("");
  tl.innerHTML = segs + accs + '<div class="marker" id="tlMarker"></div>';
}
$("#timeline").onclick = (e) => {
  if (!state.events || !video.duration) return;
  const r = e.currentTarget.getBoundingClientRect();
  video.currentTime = ((e.clientX - r.left) / r.width) * video.duration;
};

function frameAt(t) {
  const fr = state.frames && state.frames.frames;
  if (!fr || !fr.length) return null;
  const i = Math.min(fr.length - 1, Math.max(0, Math.round(t * state.fps)));
  return fr[i];
}
/** fall back to the last decision event before t when per-frame telemetry is missing (older outputs) */
function eventAt(t) {
  const evs = state.events && state.events.events;
  if (!evs) return null;
  let cur = null;
  for (const e of evs) { if (e.type !== "decision") continue; if (e.time_s <= t) cur = e; else break; }
  return cur;
}

function setPanel(p) {
  const k = styleKey(p.label);
  $("#dLabel").className = `decision-label st-${k}`;
  $("#dLabelText").textContent = p.label || "—";
  $("#dTitle").textContent = p.title || "";
  const path = p.path || "—";
  $("#dPath").className = `chip bg-${path}`;
  $("#dPath").textContent = `PATH ${path}${p.threats ? ` · ${p.threats} threat${p.threats > 1 ? "s" : ""}` : ""}`;
  $("#dSpeed").innerHTML = fmt(p.speed, " km/h", 0);
  $("#dSteer").innerHTML = p.steer === null || p.steer === undefined ? "—"
    : `${Math.abs(p.steer) < 1 ? "0" : Math.abs(p.steer).toFixed(0)}°<small>${Math.abs(p.steer) < 1 ? "straight" : p.steer < 0 ? "left" : "right"}</small>`;
  const key = p.key;
  $("#dThreat").innerHTML = key && key.cls ? `${esc(key.cls)}<small>#${key.id ?? "?"}${key.beh ? " · " + esc(key.beh) : ""}</small>` : "none";
  $("#dDist").innerHTML = key ? fmt(key.dist, " m") : "—";
  $("#dTTC").innerHTML = key && key.ttc !== null && key.ttc !== undefined ? fmt(key.ttc, " s") : "—";
  $("#dObjs").innerHTML = p.objects === undefined || p.objects === null ? "—" : String(p.objects);
  $("#dReason").textContent = p.reason || "";
  $("#dAcc").innerHTML = p.acc ? `<span class="chip" style="color:var(--brake)">ACCIDENT ${esc(p.acc)} · simulation workflow</span>` : "";
}

function updatePanel() {
  const t = video.currentTime || 0;
  const f = frameAt(t);
  if (f) {
    const e = eventAt(t);
    setPanel({ ...f, reason: e ? e.reason : "" });
  } else {
    const e = eventAt(t);
    if (e) setPanel({ label: e.decision, title: e.title, path: e.path_status, threats: e.threats, speed: e.ego_speed_kmh,
      steer: e.steering_deg, reason: e.reason, key: e.object && { id: e.object.track_id, cls: e.object.class_name,
        dist: e.object.distance_m, ttc: e.object.ttc_s, beh: e.object.behavior } });
  }
  const m = $("#tlMarker");
  if (m && video.duration) m.style.left = `${(100 * t) / video.duration}%`;
  $("#tlTime").textContent = video.duration ? `${mmss(t)} / ${mmss(video.duration)}` : "";
}
video.addEventListener("timeupdate", updatePanel);
video.addEventListener("seeked", updatePanel);
video.addEventListener("error", () => {
  $("#dTitle").textContent = "This video cannot be decoded by the browser (older mp4v encoding). Re-render it with the current pipeline.";
});
(function tick() { if (!video.paused) updatePanel(); requestAnimationFrame(tick); })();

/* ------------------------------------------------------------------ events view */
$$("#evFilters .filter").forEach((b) => (b.onclick = () => {
  state.evFilter = b.dataset.f;
  $$("#evFilters .filter").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  renderEvents();
}));

function evSummary(e) {
  if (e.type === "decision") return { what: e.decision, why: e.reason || e.title, label: e.decision };
  if (e.type === "behavior") {
    const o = e.object || {};
    return { what: e.behavior, why: `${o.class_name || "object"} #${o.track_id} at ${o.distance_m ?? "?"} m`, label: "STEER" };
  }
  const confirmed = e.accident_state === "CONFIRMED";
  return { what: confirmed ? "ACCIDENT DETECTED (confirmed)" : "Collision cue — unconfirmed",
    why: `confidence ${Math.round(100 * (e.confidence || 0))}% · ${(e.cues || []).join(", ")}${confirmed ? " · simulated emergency workflow" : ""}`,
    label: confirmed ? "BRAKE" : "SLOW" };
}

function renderEvents() {
  const ul = $("#eventList");
  const evs = (state.events && state.events.events) || [];
  const f = state.evFilter;
  const list = evs.filter((e) => f === "all" || (f === "brake" ? e.type === "decision" && e.state === "BRAKE" : e.type === f));
  if (!state.demo) { ul.innerHTML = '<li class="callout">Select a processed drive on the Demo tab.</li>'; return; }
  if (!list.length) { ul.innerHTML = '<li class="callout">No events for this filter.</li>'; return; }
  ul.innerHTML = list.map((e) => {
    const s = evSummary(e);
    return `<li class="event" data-id="${e.id}" tabindex="0" aria-current="${state.evSel === e.id}">
      <span class="time">${mmss(e.time_s)}.${String(Math.round((e.time_s % 1) * 10)).slice(0, 1)}</span>
      <div><div class="what st-${styleKey(s.label)}">${esc(s.what)}</div><div class="why">${esc(s.why)}</div></div>
      <span class="chip">#${e.id} · ${esc(e.type)}</span></li>`;
  }).join("");
  $$(".event", ul).forEach((li) => {
    li.onclick = () => selectEvent(+li.dataset.id);
    li.onkeydown = (k) => { if (k.key === "Enter") selectEvent(+li.dataset.id); };
  });
}

async function selectEvent(id) {
  state.evSel = id;
  $$(".event").forEach((li) => li.setAttribute("aria-current", String(+li.dataset.id === id)));
  const enc = encodeURIComponent(state.demo.name).replace(/%2F/g, "/");
  try {
    const r = await api(`/api/demos/${enc}/events/${id}/card`);
    $("#evCard").textContent = r.card;
  } catch (e) { $("#evCard").textContent = e.message; }
  $("#evPlay").disabled = false;
}
$("#evPlay").onclick = () => {
  const e = state.events.events.find((x) => x.id === state.evSel);
  if (!e) return;
  showView("demo");
  video.currentTime = Math.max(0, e.time_s - 2);
  video.play().catch(() => {});
  window.scrollTo({ top: 0, behavior: "smooth" });
};

/* ------------------------------------------------------------------ upload */
const upModal = $("#uploadModal");
let currentJob = null, uploadXhr = null;
$$('[data-action="upload"]').forEach((b) => (b.onclick = () => openUpload()));
$$("[data-close]").forEach((b) => (b.onclick = () => b.closest(".modal").classList.remove("open")));
function openUpload() {
  if (!currentJob) { $("#upProgress").hidden = true; $("#drop").hidden = false; }
  upModal.classList.add("open");
}
const drop = $("#drop");
$("#fileInput").onchange = (e) => e.target.files[0] && startUpload(e.target.files[0]);
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && startUpload(e.dataTransfer.files[0]));

function setStage(active, done = []) {
  $$("#upStages .stage").forEach((s) => {
    s.classList.toggle("on", s.dataset.s === active);
    s.classList.toggle("done", done.includes(s.dataset.s));
  });
}
function setProgress(pct, stage, detail) {
  $("#upBar").style.width = `${pct}%`;
  $("#upPct").textContent = `${Math.round(pct)}%`;
  if (stage) $("#upStage").textContent = stage;
  if (detail !== undefined) $("#upDetail").textContent = detail;
}

function startUpload(file) {
  $("#drop").hidden = true; $("#upProgress").hidden = false;
  setStage("upload"); setProgress(0, `Uploading ${file.name}`, `${(file.size / 1e6).toFixed(1)} MB`);
  const fd = new FormData(); fd.append("video", file);
  const xhr = (uploadXhr = new XMLHttpRequest());
  xhr.open("POST", "/api/upload");
  xhr.upload.onprogress = (e) => e.lengthComputable && setProgress((100 * e.loaded) / e.total, `Uploading ${file.name}`);
  xhr.onload = () => {
    uploadXhr = null;
    let j = {}; try { j = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
    if (xhr.status !== 200) { setProgress(0, "Upload failed", j.error || xhr.statusText); return; }
    currentJob = j.job_id; pollJob();
  };
  xhr.onerror = () => { uploadXhr = null; setProgress(0, "Upload failed", "network error"); };
  xhr.send(fd);
}

async function pollJob() {
  if (!currentJob) return;
  let j;
  try { j = await api(`/api/jobs/${currentJob}`); } catch (e) { setProgress(0, "Lost job", e.message); currentJob = null; return; }
  if (j.status === "error") { setProgress(0, "Processing failed", j.error); currentJob = null; return; }
  if (j.status === "done") {
    setStage(null, ["upload", "init", "process", "render"]);
    setProgress(100, "Done", `${j.summary.frames_processed} frames · ${j.summary.processing_fps} FPS processing`);
    currentJob = null;
    await loadDemos(j.result);
    selectDemo(j.result, true);
    setTimeout(() => upModal.classList.remove("open"), 900);
    return;
  }
  if (j.stage === "queued" || j.stage === "initializing") {
    setStage("init", ["upload"]); setProgress(0, "Loading models", "YOLOv8 · Depth-Anything-V2 · CLIP");
  } else {
    const p = j.progress ?? 0;
    const rendering = p >= 99.5;
    setStage(rendering ? "render" : "process", rendering ? ["upload", "init", "process"] : ["upload", "init"]);
    setProgress(p, rendering ? "Writing video, events & timeline" : "Processing frames",
      `frame ${j.frame}${j.total ? " / " + j.total : ""} · ${j.fps} FPS · current decision: ${j.label || "—"}`);
  }
  setTimeout(pollJob, 700);
}
$("#upCancel").onclick = async () => {
  if (uploadXhr) { uploadXhr.abort(); uploadXhr = null; }
  if (currentJob) { await api(`/api/jobs/${currentJob}/cancel`, { method: "POST" }).catch(() => {}); }
  setProgress(0, "Cancelling…", "The partial result is kept only if frames were written.");
};

/* ------------------------------------------------------------------ live camera */
const live = { stream: null, session: null, running: false, facing: "environment", times: [], last: null };
const lv = $("#liveVideo"), lc = $("#liveCanvas"), grab = document.createElement("canvas");

function liveSupportNote() {
  const secure = window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia;
  $("#liveHelp").style.display = secure && location.hostname === "localhost" ? "none" : "";
  if (!secure) $("#liveStatus").textContent = "Camera unavailable: this page is not a secure context (use HTTPS or localhost).";
}

async function liveStart() {
  if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) {
    $("#liveStatus").textContent = "Camera API unavailable here — open the HTTPS address (see the note).";
    return;
  }
  try {
    $("#liveStatus").textContent = "Requesting camera…";
    live.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: live.facing, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
  } catch (e) { $("#liveStatus").textContent = `Camera error: ${e.name} — ${e.message}`; return; }
  lv.srcObject = live.stream; await lv.play().catch(() => {});
  $("#liveStatus").textContent = "Loading models on the PC (first start can take ~20 s)…";
  try { live.session = (await api("/api/live/start", { method: "POST" })).session; }
  catch (e) { $("#liveStatus").textContent = `Server error: ${e.message}`; liveStop(); return; }
  $("#liveEmpty").hidden = true; $("#liveStop").disabled = false;
  ["#livePill", "#liveMeta", "#liveBottom"].forEach((s) => ($(s).hidden = false));
  live.running = true; live.times = [];
  $("#liveStatus").innerHTML = '<span class="rec">LIVE</span> processing on the PC';
  liveLoop();
}

function liveStop() {
  live.running = false;
  if (live.stream) live.stream.getTracks().forEach((t) => t.stop());
  live.stream = null; lv.srcObject = null;
  if (live.session) api("/api/live/stop", { method: "POST" }).catch(() => {});
  live.session = null;
  $("#liveEmpty").hidden = false; $("#liveStop").disabled = true;
  ["#livePill", "#liveMeta", "#liveBottom"].forEach((s) => ($(s).hidden = true));
  lc.getContext("2d").clearRect(0, 0, lc.width, lc.height);
  $("#liveStatus").textContent = "Stopped";
}

async function liveLoop() {
  while (live.running) {
    if (!lv.videoWidth) { await new Promise((r) => setTimeout(r, 100)); continue; }
    const W = 640, H = Math.round((lv.videoHeight / lv.videoWidth) * W);
    grab.width = W; grab.height = H;
    grab.getContext("2d").drawImage(lv, 0, 0, W, H);
    const blob = await new Promise((r) => grab.toBlob(r, "image/jpeg", 0.75));
    const t0 = performance.now();
    let res;
    try {
      const r = await fetch(`/api/live/frame?session=${live.session}`, { method: "POST", body: blob, headers: { "Content-Type": "image/jpeg" } });
      res = await r.json();
      if (r.status === 503) { $("#liveStatus").textContent = res.error; await new Promise((z) => setTimeout(z, 1000)); continue; }
      if (!r.ok) throw new Error(res.error || r.statusText);
    } catch (e) { $("#liveStatus").textContent = `Error: ${e.message}`; await new Promise((z) => setTimeout(z, 800)); continue; }
    const rtt = performance.now() - t0;
    const now = performance.now();
    live.times.push(now); while (live.times.length && now - live.times[0] > 3000) live.times.shift();
    const fps = live.times.length > 1 ? ((live.times.length - 1) * 1000) / (now - live.times[0]) : null;
    drawLive(res, rtt, fps);
  }
}

function drawLive(res, rtt, fps) {
  const d = res.decision, k = styleKey(d.label);
  const pill = $("#livePill");
  pill.className = `live-pill ${k}`; pill.textContent = d.label;
  $("#liveMeta").innerHTML = `LIVE ${fps ? fps.toFixed(1) : "—"} FPS<br>latency ${rtt.toFixed(0)} ms<br>PATH ${esc(d.path || "—")}`;
  const key = d.key;
  $("#liveTitle").textContent = `${d.title || ""}${key && key.ttc ? ` · TTC ${key.ttc}s` : ""}${key && key.dist ? ` · ${key.dist} m` : ""}`;
  $("#liveReason").textContent = `${d.reason || ""} · steer ${res.steering_deg}° · ${res.speed_kmh} km/h (est.)`;
  $("#lFps").innerHTML = fps ? `${fps.toFixed(1)}<small>fps</small>` : "—";
  $("#lRtt").innerHTML = `${rtt.toFixed(0)}<small>ms</small>`;
  $("#lProc").innerHTML = `${res.perf.processing_ms.toFixed(0)}<small>ms</small>`;
  $("#lRes").innerHTML = `${res.width}<small>×${res.height}</small>`;
  // boxes over the video (object-fit: contain -> compute letterbox)
  const rect = lc.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  lc.width = rect.width * dpr; lc.height = rect.height * dpr;
  const g = lc.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, rect.width, rect.height);
  const s = Math.min(rect.width / res.width, rect.height / res.height), vw = res.width * s, vh = res.height * s;
  const ox = (rect.width - vw) / 2, oy = (rect.height - vh) / 2;
  g.font = "600 12px system-ui, sans-serif"; g.lineWidth = 2;
  for (const o of res.objects) {
    const [x1, y1, x2, y2] = o.box;
    const col = o.hazard === "CRITICAL" ? cssVar("--brake") : o.hazard === "WARNING" ? cssVar("--slow") : o.in_path ? cssVar("--steer") : "#e8edf2";
    g.strokeStyle = col; g.lineWidth = o.key ? 3 : 1.5;
    g.strokeRect(ox + x1 * vw, oy + y1 * vh, (x2 - x1) * vw, (y2 - y1) * vh);
    const txt = `${o.cls}${o.dist !== null ? " " + o.dist + "m" : ""}${o.ttc ? " TTC " + o.ttc + "s" : ""}${o.beh ? " " + o.beh : ""}`;
    const tw = g.measureText(txt).width + 8;
    g.fillStyle = "rgba(8,10,12,.78)"; g.fillRect(ox + x1 * vw, oy + y1 * vh - 18, tw, 18);
    g.fillStyle = col; g.fillText(txt, ox + x1 * vw + 4, oy + y1 * vh - 5);
  }
  if (res.accident && res.accident.state !== "NORMAL") {
    const conf = res.accident.state === "CONFIRMED";
    g.fillStyle = conf ? cssVar("--brake") : cssVar("--slow"); g.fillRect(ox + 10, oy + vh - 120, 360, 30);
    g.fillStyle = conf ? "#fff" : "#1a1206"; g.font = "800 14px system-ui, sans-serif";
    g.fillText(conf ? `ACCIDENT DETECTED · ${Math.round(100 * res.accident.confidence)}% · SIMULATION`
      : `POSSIBLE COLLISION · confirming (${Math.round(100 * res.accident.confidence)}%)`, ox + 18, oy + vh - 100);
    if (res.accident.state === "CONFIRMED" && !live.alerted) { live.alerted = true; triggerEmergency(res.accident.confidence, "live camera"); }
  }
  drawBev(res);
}

function drawBev(res) {
  const c = $("#bevCanvas"), g = c.getContext("2d"), W = c.width, H = c.height;
  const range = 30, half = 10, sx = W / (2 * half), sy = H / range;
  const X = (x) => W / 2 + x * sx, Y = (y) => H - y * sy;
  g.fillStyle = cssVar("--surface-2"); g.fillRect(0, 0, W, H);
  g.strokeStyle = cssVar("--line"); g.lineWidth = 1;
  for (let y = 5; y < range; y += 5) { g.beginPath(); g.moveTo(0, Y(y)); g.lineTo(W, Y(y)); g.stroke(); g.fillStyle = cssVar("--faint"); g.font = "11px system-ui"; g.fillText(`${y} m`, 4, Y(y) - 3); }
  g.fillStyle = "rgba(45,212,191,.12)"; g.fillRect(X(-1.1), 0, 2.2 * sx, H);
  const path = res.bev.path || [];
  if (path.length > 1) {
    g.strokeStyle = cssVar("--accent"); g.lineWidth = 4; g.lineCap = "round"; g.beginPath();
    path.forEach(([x, y], i) => (i ? g.lineTo(X(x), Y(y)) : g.moveTo(X(x), Y(y)))); g.stroke();
  }
  for (const o of res.objects) {
    if (o.dist === null || o.x === null) continue;
    g.fillStyle = o.hazard === "CRITICAL" ? cssVar("--brake") : o.hazard === "WARNING" ? cssVar("--slow") : cssVar("--muted");
    g.beginPath(); g.arc(X(o.x), Y(o.dist), o.key ? 7 : 5, 0, 7); g.fill();
  }
  g.fillStyle = cssVar("--text"); g.beginPath(); g.moveTo(X(0), Y(0) - 16); g.lineTo(X(-0.8), H); g.lineTo(X(0.8), H); g.fill();
}
$("#liveStart").onclick = liveStart;
$("#liveStop").onclick = liveStop;
$("#liveFlip").onclick = () => { live.facing = live.facing === "environment" ? "user" : "environment"; if (live.running) { liveStop(); liveStart(); } };

/* ------------------------------------------------------------------ emergency */
async function loadContact() {
  const c = await api("/api/emergency/contact").catch(() => ({}));
  $$("#contactForm input").forEach((i) => (i.value = c[i.name] || ""));
}
$("#cSave").onclick = async () => {
  const data = Object.fromEntries($$("#contactForm input").map((i) => [i.name, i.value.trim()]));
  await api("/api/emergency/contact", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  $("#cMsg").textContent = "Saved locally.";
};
$("#cDelete").onclick = async () => {
  await api("/api/emergency/contact", { method: "DELETE" });
  $$("#contactForm input").forEach((i) => (i.value = ""));
  $("#cMsg").textContent = "Deleted.";
};

const geo = { lat: null, lon: null };
function getBrowserLocation() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition((p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude, acc: p.coords.accuracy }),
      () => resolve(null), { enableHighAccuracy: true, timeout: 8000 });
  });
}
function hospitalItems(h) {
  if (h.error && !(h.results || []).length) return `<li class="muted">Hospital lookup unavailable: ${esc(h.error)}</li>`;
  if (!(h.results || []).length) return '<li class="muted">No hospitals found within 6 km (OpenStreetMap).</li>';
  return h.results.map((r) => `<li><strong>${esc(r.name)}</strong> <span class="muted">· ${esc(r.kind)} · ${r.distance_km} km</span>
    ${r.phone ? `<div class="muted mono" style="font-size:12.5px;margin-top:3px">${esc(r.phone)} (from OpenStreetMap — not dialled)</div>` : ""}
    <div style="margin-top:4px;font-size:12.5px"><a target="_blank" rel="noopener" href="https://www.openstreetmap.org/?mlat=${r.lat}&mlon=${r.lon}#map=17/${r.lat}/${r.lon}">Open map</a></div></li>`).join("");
}
$("#geoBtn").onclick = async () => {
  $("#geoMsg").textContent = "Requesting location permission…";
  const p = await getBrowserLocation();
  if (!p) {
    $("#geoMsg").textContent = "Location unavailable (permission denied, or not HTTPS). The DEMO LOCATION will be used if set.";
    return;
  }
  Object.assign(geo, p);
  $("#geoMsg").textContent = `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)} (±${Math.round(p.acc)} m, browser geolocation)`;
  $("#hospList").innerHTML = '<li class="muted">Searching OpenStreetMap…</li>';
  $("#hospList").innerHTML = hospitalItems(await api(`/api/emergency/hospitals?lat=${p.lat}&lon=${p.lon}`).catch((e) => ({ error: e.message })));
};

async function triggerEmergency(confidence = 0.9, source = "demo trigger (button)") {
  const m = $("#emModal"), body = $("#emBody");
  body.innerHTML = '<p class="muted">Preparing simulated workflow…</p>';
  m.classList.add("open");
  const r = await api("/api/emergency/trigger", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat: geo.lat, lon: geo.lon, confidence, source }) }).catch((e) => ({ error: e.message }));
  if (r.error) { body.innerHTML = `<p>${esc(r.error)}</p>`; return; }
  const c = r.contact || {}, loc = r.location, h = (r.hospitals.results || [])[0];
  const notif = r.notification.results.map((n) => n.status === "SIMULATED"
    ? `<div><span class="ok">✓ SIMULATED ${esc(n.channel.toUpperCase())} SENT</span> <span class="muted">to ${esc(n.recipient)} — not actually sent</span></div>`
    : `<div class="muted">${esc(n.status)} — add a contact in Emergency settings</div>`).join("");
  body.innerHTML = `
    <div class="em-row"><div class="k">Confidence</div><div>${Math.round(100 * r.event.confidence)}% · ${esc(r.event.source)}</div></div>
    <div class="em-row"><div class="k">Time</div><div class="mono">${esc(r.event.time)}</div></div>
    <div class="em-row"><div class="k">Location</div><div>${loc.lat !== null ? `${loc.lat.toFixed(5)}, ${loc.lon.toFixed(5)} <span class="chip">${esc(loc.source)}</span>` : '<span style="color:var(--slow)">Unavailable</span> — allow location or set a DEMO LOCATION'}</div></div>
    <div class="em-row"><div class="k">Emergency contact</div><div>${c.contact_name ? esc(c.contact_name) : '<span class="muted">not configured</span>'}${notif}</div></div>
    <div class="em-row"><div class="k">Nearest hospital</div><div>${h ? `<strong>${esc(h.name)}</strong> · ${h.distance_km} km` : `<span class="muted">${esc(r.hospitals.error || "none found")}</span>`}</div></div>
    <div class="em-row"><div class="k">Ambulance</div><div><strong style="color:var(--brake)">AMBULANCE REQUEST — SIMULATION</strong><div class="muted" style="font-size:13px">Ready for confirmation. No call has been placed.</div></div></div>
    <details style="margin-top:12px"><summary class="muted" style="cursor:pointer">Message that would be sent</summary><p class="mono" style="font-size:12.5px">${esc(r.message)}</p></details>
    <div class="row" style="margin-top:18px">
      <button class="btn" id="emReview">Review event</button>
      <button class="btn btn-danger" id="emConfirm">Confirm emergency action</button>
      <button class="btn btn-ghost" id="emDismiss">Dismiss (false alarm)</button>
    </div>
    <div id="emResult" style="margin-top:14px"></div>`;
  $("#emDismiss").onclick = () => { m.classList.remove("open"); live.alerted = false; };
  $("#emReview").onclick = () => {
    m.classList.remove("open");
    const acc = state.events && state.events.events.find((e) => e.type === "accident" && e.accident_state === "CONFIRMED");
    if (acc) { showView("events"); state.evFilter = "accident"; renderEvents(); selectEvent(acc.id); }
  };
  $("#emConfirm").onclick = async () => {
    const x = await api("/api/emergency/confirm", { method: "POST" });
    $("#emResult").innerHTML = `<div class="callout warn"><strong>Confirmed — ${esc(x.mode)}.</strong> ${esc(x.note)}
      <div style="margin-top:8px"><a class="btn btn-danger" href="tel:112">Call 112 yourself</a></div></div>`;
  };
}
$("#emTrigger").onclick = () => triggerEmergency(0.9, "demo trigger (button) — not a real detection");

/* ------------------------------------------------------------------ system */
async function loadSystem() {
  const s = await api("/api/system").catch(() => null);
  if (!s) return;
  const t = s.tests;
  const pass = t && t.summary ? t.summary.match(/(\d+)\s*\/\s*(\d+)/) : null;
  $("#sysStats").innerHTML = [
    [pass ? `${pass[1]}/${pass[2]}` : "—", "regression scenarios passing"],
    [s.gpu ? s.gpu.replace("NVIDIA GeForce ", "") : "CPU", s.cuda ? `CUDA · ${s.vram_gb} GB` : "no CUDA"],
    [String(state.demos.length), "processed drives available"],
    ["17", "candidate arcs (±15°)"],
  ].map(([n, l]) => `<div class="card card-pad stat"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div></div>`).join("");
  $("#sysInfo").innerHTML = [
    `Python ${esc(s.python)} · PyTorch ${esc(s.torch || "?")}`,
    ...s.models.map(esc),
    `Served over ${s.https ? "HTTPS" : "HTTP"} · LAN: ${s.lan_ips.map((ip) => esc(ip)).join(", ") || "—"}`,
  ].map((x) => `<li>${x}</li>`).join("");
  if (t) showTests(t);
}
function showTests(t) {
  $("#testSummary").innerHTML = `<span class="${t.ok ? "st-GO" : "st-BRAKE"}">${esc(t.summary || (t.ok ? "passed" : "failed"))}</span> <span class="muted" style="font-weight:500">· ${esc(t.time)} · ${t.seconds}s</span>`;
  $("#testList").textContent = t.results.join("\n");
}
$("#runTests").onclick = async () => {
  const b = $("#runTests"); b.disabled = true; b.textContent = "Running…";
  try { showTests(await api("/api/system/run-tests", { method: "POST" })); loadSystem(); } catch (e) { $("#testSummary").textContent = e.message; }
  b.disabled = false; b.textContent = "Run tests";
};

/* ------------------------------------------------------------------ boot */
(function boot() {
  let t = "dark";
  try { t = localStorage.getItem("ps-theme") || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"); } catch (e) { /* default */ }
  applyTheme(t);
  liveSupportNote();
  api("/api/system").then((s) => {
    const port = location.port || (location.protocol === "https:" ? 443 : 80);
    $("#liveUrls").innerHTML = s.lan_ips.map((ip) => `<div class="mono">https://${esc(ip)}:${location.protocol === "https:" ? port : 8443}</div>`).join("");
  }).catch(() => {});
  const v = location.hash.slice(1);
  loadDemos().catch(() => {}).finally(() => { if (VIEWS.includes(v)) showView(v); });
})();
