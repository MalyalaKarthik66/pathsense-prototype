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
  $$("[data-theme-toggle]").forEach((b) => {
    b.querySelector("use").setAttribute("href", t === "dark" ? "#i-sun" : "#i-moon");
    b.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
  });
  $('meta[name="theme-color"]').content = t === "dark" ? "#0b0e11" : "#f5f7fa";
  try { localStorage.setItem("ps-theme", t); } catch (e) { /* storage unavailable */ }
  if (state.demo) drawTimeline();
}
$$("[data-theme-toggle]").forEach((b) => (b.onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark")));

/* ------------------------------------------------------------------ routing: #home (landing) | #demo #live #emergency (app) */
const VIEWS = ["demo", "live", "emergency"];
function showView(v) {
  if (!VIEWS.includes(v)) v = "demo";
  $("#home").hidden = true;
  $("#app").hidden = false;
  hero.stop();
  $$(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.view === v)));
  $$(".view").forEach((s) => s.classList.toggle("active", s.id === `view-${v}`));
  if (v === "emergency") loadContact();
  if (location.hash !== `#${v}`) history.pushState(null, "", `#${v}`);
  document.title = `PathSense · ${v[0].toUpperCase()}${v.slice(1)}`;
}
function showHome() {
  $("#app").hidden = true;
  $("#home").hidden = false;
  document.title = "PathSense";
  if (location.hash && location.hash !== "#home") history.pushState(null, "", "#home");
  hero.start();
}
function route() {
  const h = location.hash.slice(1);
  if (VIEWS.includes(h)) { showView(h); window.scrollTo(0, 0); } else showHome();
}
$$(".tab").forEach((t) => (t.onclick = () => showView(t.dataset.view)));
window.addEventListener("popstate", route);
window.addEventListener("hashchange", route);
// in-page anchors on the landing page scroll smoothly instead of changing the route
$$("[data-scroll]").forEach((a) => (a.onclick = (e) => {
  e.preventDefault();
  $(a.getAttribute("href")).scrollIntoView({ behavior: "smooth", block: "start" });
}));

/* ------------------------------------------------------------------ demo player */
const state = { demos: [], demo: null, events: null, frames: null, fps: 25, evFilter: "all", evSel: null };
const video = $("#video");

function demoCard(d) {
  const pct = d.decision_pct || {};
  const bar = Object.entries(pct).filter(([, v]) => v > 0)
    .map(([k, v]) => `<span style="width:${v}%;background:${colorOf(k)}" title="${esc(k)} ${v}%"></span>`).join("");
  const meta = `${d.duration_s ? `<span>${mmss(d.duration_s)}</span>` : ""}
      ${d.brake_pct !== null && d.brake_pct !== undefined ? `<span>BRAKE ${d.brake_pct}%</span>` : ""}
      ${d.accidents ? `<span style="color:var(--brake)">accident ×${d.accidents}</span>` : ""}`;
  return `<div class="demo-card${d.builtin ? " scenario" : ""}" data-name="${esc(d.name)}" role="group" aria-label="${esc(d.title)}">
    ${d.poster_url ? `<button class="thumb" data-play tabindex="-1" aria-hidden="true"><img src="${esc(d.poster_url)}" alt="" loading="lazy" decoding="async"><span class="thumb-play"><svg><use href="#i-play"/></svg></span></button>` : ""}
    <div class="body">
      <div class="t">${esc(d.title)}${d.uploaded ? ' <span class="chip" style="font-size:11px">upload</span>' : ""}</div>
      ${d.place ? `<div class="place">${esc(d.place)}</div>` : ""}
      ${d.desc ? `<p class="desc">${esc(d.desc)}</p>` : ""}
      <div class="m">${meta}</div>
      <div class="bar">${bar}</div>
      ${d.browser_playable ? "" : '<div class="warn">Older encoding (mp4v) — may not play in the browser; re-render to fix.</div>'}
      <button class="btn btn-sm demo-play" data-play><svg><use href="#i-play"/></svg><span>Play</span></button>
    </div>
  </div>`;
}

async function loadDemos(select) {
  state.demos = await api("/api/demos");
  const builtin = state.demos.filter((d) => d.builtin), local = state.demos.filter((d) => !d.builtin);
  $("#library").innerHTML = builtin.length ? builtin.map(demoCard).join("")
    : `<div class="callout">The built-in demos are missing from <span class="mono">demos/</span> (run <span class="mono">python build_demos.py</span>).</div>`;
  $("#libraryLocal").innerHTML = local.map(demoCard).join("");
  $("#localWrap").hidden = !local.length;
  $$(".demo-card").forEach((c) => $$("[data-play]", c).forEach((b) => (b.onclick = () => {
    if (state.demo && state.demo.name === c.dataset.name) replayFromStart(); else selectDemo(c.dataset.name, true);
  })));
  if (!state.demos.length) return;
  const want = select || (state.demo && state.demo.name) || (state.demos.find((d) => d.browser_playable) || state.demos[0]).name;
  selectDemo(want, false);
}

function replayFromStart() {
  video.currentTime = 0;
  video.play().catch(() => {});
  $("#player").scrollIntoView({ behavior: "smooth", block: "center" });
}

async function selectDemo(name, play) {
  const d = state.demos.find((x) => x.name === name);
  if (!d) return;
  state.demo = d; state.events = null; state.frames = null; state.evSel = null;
  $("#momentCard").innerHTML = '<p class="muted" style="margin:0">Select a moment to replay it.</p>';
  $$(".demo-card").forEach((b) => {
    const cur = b.dataset.name === name;
    b.setAttribute("aria-current", String(cur));
    const lbl = $(".demo-play span", b); if (lbl) lbl.textContent = "Play";
  });
  $("#playerEmpty").hidden = true; $("#replayBtn").hidden = true;
  if (d.poster_url) video.poster = d.poster_url; else video.removeAttribute("poster");
  video.src = d.video_url;
  if (play) { video.play().catch(() => {}); $("#player").scrollIntoView({ behavior: "smooth", block: "center" }); }
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
video.addEventListener("ended", () => { $("#replayBtn").hidden = false; });
video.addEventListener("play", () => {
  $("#replayBtn").hidden = true;
  const card = state.demo && $$(".demo-card").find((c) => c.dataset.name === state.demo.name);
  const lbl = card && $(".demo-play span", card); if (lbl) lbl.textContent = "Replay";   // once played, the card replays
});
$("#replayBtn").onclick = replayFromStart;
video.addEventListener("error", () => {
  $("#dTitle").textContent = "This video cannot be decoded by the browser (older mp4v encoding). Re-render it with the current pipeline.";
});
(function tick() { if (!video.paused) updatePanel(); requestAnimationFrame(tick); })();

/* ------------------------------------------------------------------ replay: key moments of the selected drive */
state.evFilter = "key";
$$("#evFilters .filter").forEach((b) => (b.onclick = () => {
  state.evFilter = b.dataset.f;
  $$("#evFilters .filter").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  renderEvents();
}));

const KEY_BEHAVIOURS = new Set(["CUT-IN", "CROSSING", "ONCOMING", "SLOW LEAD"]);
const niceBehaviour = (b) => ({ "CUT-IN": "Cut-in", CROSSING: "Crossing", ONCOMING: "Oncoming", "SLOW LEAD": "Slow vehicle ahead", "SIDE PASS": "Passing alongside" }[b] || b);
const niceClass = (c) => (c === "person" ? "pedestrian" : c || "object");

function evSummary(e) {
  if (e.type === "decision") return { what: e.decision, why: e.title || e.reason, label: e.decision };
  if (e.type === "behavior") {
    const o = e.object || {};
    return { what: niceBehaviour(e.behavior), why: `${niceClass(o.class_name)} at ${o.distance_m ?? "?"} m`, label: "STEER" };
  }
  const confirmed = e.accident_state === "CONFIRMED";
  return { what: confirmed ? "Accident detected" : "Possible collision (unconfirmed)",
    why: `confidence ${Math.round(100 * (e.confidence || 0))}%${confirmed ? " · simulated emergency workflow" : ""}`,
    label: confirmed ? "BRAKE" : "SLOW" };
}

function momentFilter(e) {
  const f = state.evFilter;
  if (f === "all") return true;
  if (f === "brake") return e.type === "decision" && e.state === "BRAKE";
  if (f === "behavior") return e.type === "behavior";
  // key moments: every BRAKE / SLOW DOWN onset, meaningful road-user behaviour, accident alerts
  if (e.type === "decision") return e.state !== "GO";
  if (e.type === "behavior") return KEY_BEHAVIOURS.has(e.behavior);
  return true;
}

function renderEvents() {
  const ul = $("#eventList");
  if (!state.demo) { ul.innerHTML = '<li class="muted" style="padding:12px 14px">Select a drive below.</li>'; return; }
  if (!state.events) { ul.innerHTML = '<li class="muted" style="padding:12px 14px">No replay data for this drive.</li>'; return; }
  const list = state.events.events.filter(momentFilter);
  if (!list.length) { ul.innerHTML = '<li class="muted" style="padding:12px 14px">Nothing in this category for this drive.</li>'; return; }
  ul.innerHTML = list.map((e) => {
    const sm = evSummary(e);
    const dur = e.duration_s ? `${e.duration_s.toFixed(1)} s` : "";
    return `<li class="moment" data-id="${e.id}" tabindex="0" role="button" aria-current="${state.evSel === e.id}">
      <span class="time">${mmss(e.time_s)}.${String(Math.floor((e.time_s % 1) * 10))}</span>
      <span class="mdot st-${styleKey(sm.label)}" aria-hidden="true"></span>
      <div><div class="what">${esc(sm.what)}</div><div class="why">${esc(sm.why)}</div></div>
      <span class="dur">${dur}</span></li>`;
  }).join("");
  $$(".moment", ul).forEach((li) => {
    li.onclick = () => selectEvent(+li.dataset.id);
    li.onkeydown = (k) => { if (k.key === "Enter" || k.key === " ") { k.preventDefault(); selectEvent(+li.dataset.id); } };
  });
}

function selectEvent(id, play = false) {
  const e = state.events && state.events.events.find((x) => x.id === id);
  if (!e) return;
  state.evSel = id;
  $$(".moment").forEach((li) => li.setAttribute("aria-current", String(+li.dataset.id === id)));
  const sm = evSummary(e);
  const o = e.object || {};
  const row = (k, v) => (v === null || v === undefined || v === "" ? "" : `<dt>${k}</dt><dd>${v}</dd>`);
  const rows = [
    row("Object", o.class_name ? `${esc(niceClass(o.class_name))} #${o.track_id}` : null),
    row("Distance", o.distance_m !== undefined && o.distance_m !== null ? `${o.distance_m} m` : null),
    row("TTC", o.ttc_s !== undefined && o.ttc_s !== null ? `${o.ttc_s} s` : null),
    row("Behaviour", e.type === "behavior" ? esc(niceBehaviour(e.behavior)) : (o.behavior ? esc(niceBehaviour(o.behavior)) : null)),
    row("Path", e.path_status ? `${esc(e.path_status)}${e.n_arcs ? ` · ${e.n_arcs - (e.blocked_arcs || 0)}/${e.n_arcs} paths free` : ""}` : null),
    row("Steering", e.steering_deg !== undefined && e.steering_deg !== null ? `${e.steering_deg}°` : null),
    row("Ego speed", e.ego_speed_kmh !== undefined && e.ego_speed_kmh !== null ? `${e.ego_speed_kmh} km/h (est.)` : null),
    row("Held for", e.duration_s ? `${e.duration_s} s` : null),
  ].join("");
  $("#momentCard").innerHTML = `
    <div class="row" style="justify-content:space-between"><span class="eyebrow">At ${mmss(e.time_s)}</span>
      ${e.type === "accident" ? '<span class="chip">simulation</span>' : ""}</div>
    <div class="mc-label st-${styleKey(sm.label)}">${esc(sm.what)}</div>
    <div class="mc-title">${esc(e.type === "decision" ? e.title : sm.why)}</div>
    ${e.type === "decision" && e.reason ? `<div class="muted" style="font-size:13px;margin-top:6px">${esc(e.reason)}</div>` : ""}
    <dl class="mc-rows">${rows}</dl>
    <div class="row" style="margin-top:18px"><button class="btn btn-primary" id="evPlay"><svg><use href="#i-play"/></svg>Replay</button></div>`;
  $("#evPlay").onclick = () => replayAt(e.time_s);
  video.pause();
  video.currentTime = Math.max(0, e.time_s - 0.2);
  if (play) replayAt(e.time_s);
}
function replayAt(t) {
  video.currentTime = Math.max(0, t - 2);
  video.play().catch(() => {});
  $("#player").scrollIntoView({ behavior: "smooth", block: "center" });
}

/* ------------------------------------------------------------------ upload
   The Upload button calls fileInput.click() synchronously and does nothing else, so the native picker opens at once.
   The input's accept list is file extensions only: on Windows, Chromium expands MIME wildcards such as "video/*"
   into every matching extension registered on the PC before it can show the dialog, which delayed it by seconds.
   Validation, the server capability check and the upload itself run only after a file has been chosen. */
const upModal = $("#uploadModal");
const fileInput = $("#fileInput");
let currentJob = null, uploadXhr = null, caps = null;
$$('[data-action="upload"]').forEach((b) => (b.onclick = () => fileInput.click()));
$$("[data-close]").forEach((b) => (b.onclick = () => b.closest(".modal").classList.remove("open")));
fileInput.onchange = () => { const f = fileInput.files[0]; fileInput.value = ""; if (f) startUpload(f); };
const drop = $("#drop");
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
/** show a failure with the real HTTP status and the server's message, and offer another file */
function uploadFailed(title, status, message) {
  uploadXhr = null; currentJob = null;
  $("#upProgress").hidden = true; $("#drop").hidden = false;
  const e = $("#upError");
  e.hidden = false;
  e.innerHTML = `<strong>${esc(title)}${status ? ` (HTTP ${status})` : ""}</strong><div style="margin-top:6px">${esc(message || "no details from the server")}</div>`;
  upModal.classList.add("open");
}
function showModalProgress() {
  $("#upError").hidden = true; $("#drop").hidden = true; $("#upProgress").hidden = false;
  upModal.classList.add("open");
}
async function getCaps() {
  if (caps) return caps;
  try { caps = await api("/api/capabilities"); } catch (e) { caps = null; }
  return caps;
}

async function startUpload(file) {
  if (currentJob || uploadXhr) { upModal.classList.add("open"); return; }
  const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
  const c = await getCaps();
  const allowed = (c && c.allowed_ext) || [".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"];
  if (!allowed.includes(ext)) return uploadFailed("Unsupported file", null, `${file.name}: use ${allowed.join(", ")}`);
  if (c && c.max_upload_mb && file.size > c.max_upload_mb * 1048576)
    return uploadFailed("File too large", null, `${file.name} is ${(file.size / 1048576).toFixed(0)} MB; the limit is ${c.max_upload_mb} MB.`);
  if (c && !c.inference) return uploadFailed("Processing unavailable on this server", 503, c.reason);
  showModalProgress();
  setStage("upload"); setProgress(0, `Uploading ${file.name}`, `${(file.size / 1e6).toFixed(1)} MB`);
  const fd = new FormData(); fd.append("video", file);
  const xhr = (uploadXhr = new XMLHttpRequest());
  xhr.open("POST", "/api/upload");
  xhr.upload.onprogress = (e) => e.lengthComputable && setProgress((100 * e.loaded) / e.total, `Uploading ${file.name}`);
  xhr.onload = () => {
    uploadXhr = null;
    let j = {}; try { j = JSON.parse(xhr.responseText); } catch (e) { /* not JSON (e.g. a proxy error page) */ }
    if (xhr.status !== 200 || !j.job_id) {
      const text = j.error || (xhr.responseText || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 200) || xhr.statusText;
      return uploadFailed("Upload failed", xhr.status, text);
    }
    currentJob = j.job_id; pollJob();
  };
  xhr.onerror = () => uploadFailed("Upload failed", null, "The connection closed before the server answered (network error, or the server restarted during the upload).");
  xhr.onabort = () => { uploadXhr = null; };
  xhr.send(fd);
}

async function pollJob() {
  if (!currentJob) return;
  let j;
  try { j = await api(`/api/jobs/${currentJob}`); }
  catch (e) {
    return uploadFailed("Lost the processing job", e.status, e.status === 404
      ? "The server no longer knows this job: it restarted during processing (for example out of memory), so the video was not processed."
      : e.message);
  }
  if (j.status === "error") return uploadFailed("Processing failed", null, j.error);
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
  if (uploadXhr) {
    uploadXhr.abort(); uploadXhr = null;
    $("#upProgress").hidden = true; $("#drop").hidden = false;
    return;
  }
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
  $("#liveStatus").textContent = "Loading models on the server (the first start takes a while)…";
  try { live.session = (await api("/api/live/start", { method: "POST" })).session; }
  catch (e) { liveStop(); $("#liveStatus").textContent = `Live mode unavailable (HTTP ${e.status || "?"}): ${e.message}`; return; }
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
    showView("demo");
    if (acc) { state.evFilter = "all"; renderEvents(); selectEvent(acc.id); }
    $("#replay").scrollIntoView({ behavior: "smooth" });
  };
  $("#emConfirm").onclick = async () => {
    const x = await api("/api/emergency/confirm", { method: "POST" });
    $("#emResult").innerHTML = `<div class="callout warn"><strong>Confirmed — ${esc(x.mode)}.</strong> ${esc(x.note)}
      <div style="margin-top:8px"><a class="btn btn-danger" href="tel:112">Call 112 yourself</a></div></div>`;
  };
}
$("#emTrigger").onclick = () => triggerEmergency(0.9, "demo trigger (button) — not a real detection");


/* ------------------------------------------------------------------ hero visual
   An abstract, generated scene (no prototype imagery): a road in perspective, road users on both sides, and the
   ego path as a light ribbon. Traffic on its own side and pedestrians on the footpath leave the path alone; when a
   pedestrian steps into the corridor the ribbon shortens and turns amber - the product idea in one picture. */
const hero = (() => {
  const c = document.getElementById("heroCanvas");
  if (!c) return { start() {}, stop() {} };
  const g = c.getContext("2d");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const EGO = 7.5;                         // m/s
  let W = 0, H = 0, raf = 0, running = false, last = 0, agents = [], nextCross = 3, clock = 0, brake = 0;
  const rnd = (a, b) => a + Math.random() * (b - a);

  function resize() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    W = c.clientWidth; H = c.clientHeight;
    c.width = Math.round(W * dpr); c.height = Math.round(H * dpr);
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  // ground-plane projection (camera 1.5 m high); vanishing point right of centre on wide screens
  function P(x, z) {
    const vx = W > 860 ? W * 0.7 : W * 0.5, hy = H * (W > 860 ? 0.33 : 0.24);
    const f = Math.min(W * 0.5, H * 0.9), zz = z + 1.6;
    return [vx + (x * f) / zz, hy + (2.4 * f) / zz, f / zz];
  }
  function spawn(kind, z) {
    if (kind === "onc") return { kind, x: rnd(3.0, 3.5), z: z ?? rnd(42, 55), v: -rnd(2, 4), w: 1.7, l: 4 };
    if (kind === "lead") return { kind, x: rnd(-0.15, 0.15), z: z ?? rnd(30, 40), v: rnd(6.5, 7.2), w: 1.5, l: 2.6 };
    if (kind === "pedL") return { kind, x: rnd(-3.6, -3.0), z: z ?? rnd(35, 50), v: rnd(0.8, 1.4), w: 0.5, l: 0.5 };
    if (kind === "pedR") return { kind, x: rnd(5.6, 6.2), z: z ?? rnd(35, 50), v: -rnd(0.8, 1.4), w: 0.5, l: 0.5 };
    return null;
  }
  function seed() {
    agents = [spawn("onc", 18), spawn("onc", 40), spawn("lead", 28), spawn("pedL", 9), spawn("pedL", 24), spawn("pedL", 40),
              spawn("pedR", 14), spawn("pedR", 33)];
  }
  function step(dt) {
    clock += dt;
    for (const a of agents) {
      a.z += (a.v - EGO * (1 - 0.8 * brake)) * dt;
      if (a.vx) a.x += a.vx * dt;
    }
    agents = agents.filter((a) => a.z > 0.8 && a.z < 60 && a.x < 9);
    const count = (k) => agents.filter((a) => a.kind === k).length;
    if (count("onc") < 2) agents.push(spawn("onc"));
    if (count("pedL") < 3) agents.push(spawn("pedL"));
    if (count("pedR") < 2) agents.push(spawn("pedR"));
    if (count("lead") < 1 && Math.random() < dt * 0.2) agents.push(spawn("lead"));
    nextCross -= dt;
    if (nextCross <= 0 && !agents.some((a) => a.kind === "cross")) {   // someone steps off the footpath ahead
      agents.push({ kind: "cross", x: -3.3, z: 17, v: 0, vx: 1.2, w: 0.5, l: 0.5 });
      nextCross = rnd(7, 10);
    }
    // conflict = a road user inside the ego corridor ahead (not "something is near")
    const conflict = agents.some((a) => Math.abs(a.x) < 1.35 && a.z < 26 && (a.kind === "cross" || a.z < 12));
    brake += ((conflict ? 1 : 0) - brake) * Math.min(1, dt * 3);
  }
  function col(v, a) {
    const s = getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    if (a === undefined) return s;
    const n = parseInt(s.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  function draw() {
    g.clearRect(0, 0, W, H);
    const text = col("--text"), accent = col("--accent"), slow = col("--slow");
    // road surface + edges (no lane paint: unmarked road)
    const edgeL = -2.1, edgeR = 5.0, zFar = 60;
    const pts = [P(edgeL, 0.2), P(edgeL, zFar), P(edgeR, zFar), P(edgeR, 0.2)];
    const grd = g.createLinearGradient(0, pts[1][1], 0, H);
    grd.addColorStop(0, col("--text", 0)); grd.addColorStop(1, col("--text", 0.05));
    g.fillStyle = grd; g.beginPath(); pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.fill();
    for (const [ex, a] of [[edgeL, 0.35], [edgeR, 0.25], [-4.2, 0.12], [7.2, 0.1]]) {
      const [x1, y1] = P(ex, 0.2), [x2, y2] = P(ex, zFar);
      const lg = g.createLinearGradient(x1, y1, x2, y2);
      lg.addColorStop(0, col("--text", a)); lg.addColorStop(1, col("--text", 0));
      g.strokeStyle = lg; g.lineWidth = 1; g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
    }
    // perception rings on the ground, slowly expanding
    for (let k = 0; k < 3; k++) {
      const r = ((clock * 5 + k * 12) % 36) + 2;
      const a = 0.16 * (1 - r / 38);
      g.strokeStyle = col("--accent", a); g.lineWidth = 1; g.beginPath();
      for (let i = 0; i <= 48; i++) {
        const th = Math.PI * (i / 48), [x, y] = P(Math.cos(th) * r, Math.sin(th) * r);
        i ? g.lineTo(x, y) : g.moveTo(x, y);
      }
      g.stroke();
    }
    // ego path ribbon: shortens and turns amber while a road user is in the corridor
    const reach = 26 - 18 * brake;
    const pathCol = (a) => (brake > 0.5 ? col("--slow", a) : col("--accent", a));
    const segs = 40;
    for (let i = 0; i < segs; i++) {
      const z0 = 0.4 + (reach * i) / segs, z1 = 0.4 + (reach * (i + 1)) / segs;
      const sway = (z) => 0.18 * Math.sin(clock * 0.5 + z * 0.05) * (z / 30);
      const [a0, b0] = P(-0.9 + sway(z0), z0), [a1, b1] = P(0.9 + sway(z0), z0), [a2, b2] = P(0.9 + sway(z1), z1), [a3, b3] = P(-0.9 + sway(z1), z1);
      g.fillStyle = pathCol(0.16 * (1 - i / segs)); g.beginPath(); g.moveTo(a0, b0); g.lineTo(a1, b1); g.lineTo(a2, b2); g.lineTo(a3, b3); g.fill();
    }
    g.strokeStyle = pathCol(0.9); g.lineWidth = 2; g.beginPath();
    for (let i = 0; i <= segs; i++) {
      const z = 0.4 + (reach * i) / segs, [x, y] = P(0.18 * Math.sin(clock * 0.5 + z * 0.05) * (z / 30), z);
      i ? g.lineTo(x, y) : g.moveTo(x, y);
    }
    g.stroke();
    // road users, far to near
    for (const a of [...agents].sort((p, q) => q.z - p.z)) {
      const [x, y, s] = P(a.x, a.z);
      const inPath = Math.abs(a.x) < 1.35 && a.z < 26 && (a.kind === "cross" || a.z < 12);
      const fade = Math.min(1, (60 - a.z) / 18) * Math.max(0, Math.min(1, (a.z - 3) / 7));  // fade in far, out near
      if (fade <= 0.01) continue;
      if (a.kind === "onc" || a.kind === "lead") {
        const w = a.w * s, h = 1.35 * s;
        g.fillStyle = col("--text", 0.08 * fade); g.strokeStyle = col("--text", 0.55 * fade); g.lineWidth = 1.2;
        g.beginPath(); g.roundRect ? g.roundRect(x - w / 2, y - h, w, h, Math.min(6, w * 0.12)) : g.rect(x - w / 2, y - h, w, h); g.fill(); g.stroke();
      } else {
        const h = 1.7 * s, r = Math.max(1.5, 0.13 * s);
        g.strokeStyle = inPath ? col("--slow", fade) : col("--text", 0.7 * fade); g.lineWidth = Math.max(1.2, 0.09 * s); g.lineCap = "round";
        g.beginPath(); g.moveTo(x, y); g.lineTo(x, y - h * 0.78); g.stroke();
        g.fillStyle = g.strokeStyle; g.beginPath(); g.arc(x, y - h * 0.9, r, 0, 7); g.fill();
      }
      // perception brackets
      const bw = Math.max(10, (a.w + 0.5) * s), bh = Math.max(14, (a.kind === "onc" || a.kind === "lead" ? 1.6 : 2.0) * s), k = Math.min(10, bw * 0.25);
      g.strokeStyle = inPath ? col("--slow", 0.95 * fade) : col("--accent", 0.45 * fade); g.lineWidth = 1;
      const x0 = x - bw / 2, y0 = y - bh, x1 = x + bw / 2, y1 = y + 2;
      g.beginPath();
      g.moveTo(x0, y0 + k); g.lineTo(x0, y0); g.lineTo(x0 + k, y0); g.moveTo(x1 - k, y0); g.lineTo(x1, y0); g.lineTo(x1, y0 + k);
      g.moveTo(x0, y1 - k); g.lineTo(x0, y1); g.lineTo(x0 + k, y1); g.moveTo(x1 - k, y1); g.lineTo(x1, y1); g.lineTo(x1, y1 - k);
      g.stroke();
    }
    // decision tag near the far end of the path
    const [tx, ty] = P(0, reach + 1.5);
    g.font = "600 12px Inter, 'Segoe UI', system-ui, sans-serif"; g.textAlign = "center";
    g.fillStyle = brake > 0.5 ? slow : accent;
    g.fillText(brake > 0.5 ? "YIELD" : "PATH CLEAR", tx, ty - 8);
    void text;
  }
  function frame(t) {
    if (!running) return;
    const dt = Math.min(0.05, (t - last) / 1000 || 0.016); last = t;
    step(dt); draw();
    raf = requestAnimationFrame(frame);
  }
  window.addEventListener("resize", () => { if (!$("#home").hidden) { resize(); if (reduce) draw(); } });
  document.addEventListener("visibilitychange", () => { if (document.hidden) hero.stop(); else if (!$("#home").hidden) hero.start(); });
  return {
    start() {
      resize();
      if (!agents.length) { seed(); for (let i = 0; i < 60; i++) step(0.05); }
      if (reduce) { draw(); return; }
      if (running) return;
      running = true; last = performance.now(); raf = requestAnimationFrame(frame);
    },
    stop() { running = false; cancelAnimationFrame(raf); },
  };
})();

/* ------------------------------------------------------------------ boot */
(function boot() {
  let t = "dark";
  try { t = localStorage.getItem("ps-theme") || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"); } catch (e) { /* default */ }
  applyTheme(t);
  liveSupportNote();
  getCaps().then((s) => {
    if (!s) return;
    if (!s.inference) { $("#upHint").hidden = false; $("#upHint").textContent = "This server plays the built-in demos; processing a new video needs a machine that can run the pipeline."; }
    if (s.host !== "local") $("#liveHelp").style.display = "none";   // LAN/HTTPS setup notes apply to a local PC only
    else {
      const port = location.port || (location.protocol === "https:" ? 443 : 80);
      $("#liveUrls").innerHTML = s.lan_ips.map((ip) => `<div class="mono">https://${esc(ip)}:${location.protocol === "https:" ? port : 8443}</div>`).join("");
    }
  });
  loadDemos().catch(() => {});
  route();
  // quiet reveal-on-scroll for the landing sections
  const io = "IntersectionObserver" in window ? new IntersectionObserver((es) => es.forEach((en) => {
    if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
  }), { threshold: 0.15 }) : null;
  $$(".home-section, .home-statement").forEach((el) => { if (io) { el.classList.add("reveal"); io.observe(el); } });
})();
