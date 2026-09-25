"""
Builds PathSense_SIH_Presentation.pptx (SIH 2026, PS 26037) with python-pptx.

Every number on the slides is read at build time from the pipeline outputs (outputs/*_stats.json, *_frames.json,
scenario_tests_last.json, live_benchmark.json) - nothing is typed in by hand. Screenshots are real frames of the
rendered HUD videos and real captures of the web app (presentation/assets/shots, made by capture_web.mjs).

    python presentation/build_presentation.py            # -> PathSense_SIH_Presentation.pptx (repo root)

Design language = the web app / HUD: dark surfaces, teal accent, decision colours GO/SLOW/BRAKE/NO SAFE PATH.
"""

import json
import os
import statistics
import sys

import cv2
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt
from lxml import etree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "outputs")
ASSETS = os.path.join(ROOT, "presentation", "assets")
ICONS = os.path.join(ASSETS, "icons")
SHOTS = os.path.join(ASSETS, "shots")
FRAMES = os.path.join(ASSETS, "frames")

# ------------------------------------------------------------------ design tokens (= web/static/app.css)
BG, SURF, SURF2, LINE = "0B0E11", "12161B", "181D23", "262D36"
TEXT, MUTED, FAINT = "F3F5F7", "A3AFBB", "6F7C89"
ACCENT, GO, SLOW, BRAKE, NSP, STEER, ORANGE = "2DD4BF", "22C55E", "F59E0B", "EF4444", "B91C1C", "38BDF8", "F97316"
HEAD, BODY, MONO = "Calibri", "Calibri", "Consolas"
W, H = 13.333, 7.5


def rgb(h):
    return RGBColor.from_string(h)


# ------------------------------------------------------------------ data
def jload(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


CLIPS = [  # name, short title, scenario, licence credit
    ("ka_kadur", "Kadur–Chikmagalur", "Rural highway / village-type road", "CC0 · L. Shyamal"),
    ("india_bangalore", "Nandidurga Rd, Bengaluru", "Dense mixed traffic, autos, queue", "CC0 · L. Shyamal"),
    ("india_newbel", "New BEL Rd, Bengaluru", "Narrow road, pedestrians, oncoming", "CC0 · L. Shyamal"),
    ("india_cvraman", "C V Raman Rd, Bengaluru", "Arterial road, cut-ins", "CC0 · L. Shyamal"),
    ("blr_iisc", "IISc campus, Bengaluru", "Pedestrians on the carriageway", "CC0 · L. Shyamal"),
    ("delhi_cattle", "Lutyens Delhi", "Cattle crossing", "CC BY-SA 3.0 · Fowler&fowler"),
]


def clip_stats(name):
    s = jload(os.path.join(OUT, f"{name}_stats.json"), {})
    fr = (jload(os.path.join(OUT, f"{name}_frames.json"), {}) or {}).get("frames", [])
    fps = s.get("input_fps") or 30.0
    lab = s.get("decision_label_pct", {})
    steer = [f["steer"] for f in fr]
    rate = [abs(b - a) * fps for a, b in zip(steer, steer[1:])]
    return {
        "frames": s.get("frames_processed"), "dur": round(s["frames_processed"] / fps, 1) if s else None,
        "fps": s.get("processing_fps"), "planner_ms": (s.get("stage_ms_per_frame") or {}).get("planner"),
        "brake": round(lab.get("BRAKE", 0) + lab.get("NO SAFE PATH - BRAKE", 0), 1) if lab else None,
        "slow": lab.get("SLOW DOWN", 0.0) if lab else None, "go": round(sum(v for k, v in lab.items() if k.startswith(("GO", "STEER"))), 1) if lab else None,
        "episodes": s.get("brake_episodes"), "beh": s.get("behavior_counts", {}),
        "autos": (s.get("auto_rickshaw") or {}).get("confirmed_tracks"),
        "trans_min": s.get("decision_transitions_per_min"),
        "steer_rate_med": round(statistics.median(rate), 1) if rate else None,
        "steer_rate_p95": round(sorted(rate)[int(0.95 * (len(rate) - 1))], 1) if rate else None,
        "acc": s.get("accident") or {}, "codec": s.get("video_codec"), "gpu": s.get("gpu_name"),
        "stage": s.get("stage_ms_per_frame") or {},
    }


STATS = {n: clip_stats(n) for n, *_ in CLIPS}
ACC_VAN = clip_stats("acc_van_rear_end")
ACC_HW = clip_stats("acc_highway_multi")
TESTS = jload(os.path.join(OUT, "scenario_tests_last.json"), {}) or {}
LIVE = jload(os.path.join(OUT, "live_benchmark.json"), {}) or {}


def tests_pass():
    import re
    m = re.search(r"(\d+)\s*/\s*(\d+)", TESTS.get("summary", ""))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def need(v, what):
    if v is None:
        sys.exit(f"missing measured value: {what} - run the pipeline / tests first")
    return v


# ------------------------------------------------------------------ primitives
prs = Presentation()
prs.slide_width, prs.slide_height = Inches(W), Inches(H)
BLANK = prs.slide_layouts[6]
N_SLIDES = 17


def slide_bg(s, color=BG):
    f = s.background.fill
    f.solid()
    f.fore_color.rgb = rgb(color)


def box(s, x, y, w, h, fill=SURF, line=LINE, radius=0.08, shape=MSO_SHAPE.ROUNDED_RECTANGLE, lw=0.75):
    sh = s.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid()
        sh.fill.fore_color.rgb = rgb(fill)
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = rgb(line)
        sh.line.width = Pt(lw)
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        sh.adjustments[0] = min(0.5, radius / max(0.01, min(w, h)))
    sh.shadow.inherit = False
    sh.text_frame.text = ""
    return sh


def text(s, x, y, w, h, runs, size=14, color=TEXT, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
         font=BODY, spacing=None, line_spacing=None):
    """runs: str | list of paragraphs; a paragraph is str or list of (text, {opts}) runs."""
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    paras = runs if isinstance(runs, list) else [runs]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if spacing:
            p.space_after = Pt(spacing)
        if line_spacing:
            p.line_spacing = line_spacing
        for rt, o in ([(para, {})] if isinstance(para, str) else para):
            r = p.add_run()
            r.text = rt
            f = r.font
            f.name = o.get("font", font)
            f.size = Pt(o.get("size", size))
            f.bold = o.get("bold", bold)
            f.italic = o.get("italic", False)
            f.color.rgb = rgb(o.get("color", color))
    return tb


def icon(s, name, x, y, size, color="t", circle=None, pad=0.22):
    if circle:
        box(s, x, y, size, size, fill=circle, line=None, shape=MSO_SHAPE.OVAL)
        p = size * pad
        s.shapes.add_picture(os.path.join(ICONS, f"{name}_{color}.png"), Inches(x + p), Inches(y + p), Inches(size - 2 * p), Inches(size - 2 * p))
    else:
        s.shapes.add_picture(os.path.join(ICONS, f"{name}_{color}.png"), Inches(x), Inches(y), Inches(size), Inches(size))


def picture(s, path, x, y, w=None, h=None, border=LINE):
    kw = {}
    if w:
        kw["width"] = Inches(w)
    if h:
        kw["height"] = Inches(h)
    pic = s.shapes.add_picture(path, Inches(x), Inches(y), **kw)
    if border:
        pic.line.color.rgb = rgb(border)
        pic.line.width = Pt(0.75)
    return pic


def arrow(s, x1, y1, x2, y2, color=FAINT, width=1.5):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    tail = etree.SubElement(ln, qn("a:tailEnd"))
    tail.set("type", "triangle"); tail.set("w", "med"); tail.set("len", "med")
    return c


def chip(s, x, y, label, color, w=None, size=11, fill=None, text_color=None, h=0.32):
    w = w or (0.16 + 0.085 * len(label) * size / 11)
    b = box(s, x, y, w, h, fill=fill or SURF2, line=color, radius=h / 2, lw=1.0)
    text(s, x, y, w, h, label, size=size, color=text_color or color, bold=True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    return w


def header(s, n, kicker, title, sub=None):
    slide_bg(s)
    # wordmark (same mark as the web app)
    icon(s, "route", 0.55, 0.36, 0.30, "t")
    text(s, 0.92, 0.33, 2.5, 0.36, [[("PathSense", {"bold": True, "size": 15})]])
    text(s, W - 4.55, 0.36, 4.0, 0.3, "SMART INDIA HACKATHON 2026  ·  PS 26037", size=10.5, color=MUTED, bold=True,
         align=PP_ALIGN.RIGHT)
    text(s, 0.55, 0.92, 9, 0.3, kicker.upper(), size=11.5, color=ACCENT, bold=True)
    text(s, 0.55, 1.2, W - 1.1, 0.7, title, size=30, bold=True, font=HEAD)
    if sub:
        text(s, 0.55, 1.83, W - 1.1, 0.4, sub, size=14, color=MUTED)
    # footer
    text(s, 0.55, H - 0.42, 8, 0.25, "PathSense — research prototype, not a certified autonomous-driving or safety system",
         size=9, color=FAINT)
    text(s, W - 1.55, H - 0.42, 1.0, 0.25, f"{n:02d} / {N_SLIDES}", size=9, color=FAINT, align=PP_ALIGN.RIGHT)


def tag(s, x, y, kind):
    """Measured / Prototype capability / Future work label."""
    c = {"MEASURED": GO, "CAPABILITY": STEER, "FUTURE": SLOW, "SIMULATION": BRAKE}[kind]
    lbl = {"MEASURED": "MEASURED", "CAPABILITY": "PROTOTYPE CAPABILITY", "FUTURE": "FUTURE WORK", "SIMULATION": "SIMULATION ONLY"}[kind]
    return chip(s, x, y, lbl, c, size=9.5, h=0.27)


def new_slide():
    return prs.slides.add_slide(BLANK)


def notes(s, t):
    s.notes_slide.notes_text_frame.text = t


# ------------------------------------------------------------------ frame grabs from the rendered HUD videos
def fit_aspect(img, aspect):
    """Centre-crop to width/height = aspect (so a picture fills its slot without running off the slide)."""
    hh, ww = img.shape[:2]
    if ww / hh > aspect:
        nw = int(hh * aspect)
        x0 = (ww - nw) // 2
        return img[:, x0:x0 + nw]
    nh = int(ww / aspect)
    y0 = (hh - nh) // 2
    return img[y0:y0 + nh]


def grab(name, t_s, out_name, crop=None, width=1600, aspect=None):
    os.makedirs(FRAMES, exist_ok=True)
    dst = os.path.join(FRAMES, out_name + ".jpg")
    cap = cv2.VideoCapture(os.path.join(OUT, f"{name}_pathsense.mp4"))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t_s * fps))
    ok, fr = cap.read()
    cap.release()
    if not ok:
        sys.exit(f"cannot read {name} at {t_s}s")
    if crop:
        hh, ww = fr.shape[:2]
        x0, y0, x1, y1 = crop
        fr = fr[int(y0 * hh):int(y1 * hh), int(x0 * ww):int(x1 * ww)]
    if aspect:
        fr = fit_aspect(fr, aspect)
    if fr.shape[1] > width:
        fr = cv2.resize(fr, (width, int(fr.shape[0] * width / fr.shape[1])), interpolation=cv2.INTER_AREA)
    cv2.imwrite(dst, fr, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return dst


def shot(name, crop=None, width=1800):
    """Web app capture (PNG) -> JPEG, optional relative crop."""
    src = next((p for p in (os.path.join(SHOTS, name + e) for e in (".png", ".jpg")) if os.path.exists(p)),
               os.path.join(SHOTS, name + ".png"))
    img = cv2.imread(src)
    if img is None:
        sys.exit(f"missing web capture {src} - run presentation/capture_web.mjs")
    if crop:
        hh, ww = img.shape[:2]
        x0, y0, x1, y1 = crop
        img = img[int(y0 * hh):int(y1 * hh), int(x0 * ww):int(x1 * ww)]
    if img.shape[1] > width:
        img = cv2.resize(img, (width, int(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA)
    dst = os.path.join(FRAMES, "web_" + name + ("_c" if crop else "") + ".jpg")
    os.makedirs(FRAMES, exist_ok=True)
    cv2.imwrite(dst, img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return dst


GRABS = json.load(open(os.path.join(ROOT, "presentation", "grabs.json"), encoding="utf-8"))


def G(key):
    g = GRABS[key]
    return grab(g["clip"], g["t"], key, crop=g.get("crop"), width=g.get("width", 1600), aspect=g.get("aspect"))

# ================================================================== slides
passed, total = tests_pass()
need(passed, "scenario_tests_last.json (run: python scenario_tests.py via the web System tab or app API)")
all_fps = [STATS[n]["fps"] for n in STATS if STATS[n]["fps"]]
gpu = next((STATS[n]["gpu"] for n in STATS if STATS[n]["gpu"]), "GPU")
gpu_short = (gpu or "").replace("NVIDIA GeForce ", "")

# 1 ---------------------------------------------------------------- title
s = new_slide(); slide_bg(s)
hero = G("hero")
s.shapes.add_picture(hero, Inches(6.1), Inches(0), width=Inches(W - 6.1), height=Inches(H))
icon(s, "route", 0.7, 0.75, 0.55, "t")
text(s, 1.38, 0.74, 4, 0.6, [[("PathSense", {"bold": True, "size": 26})]])
text(s, 0.7, 1.55, 5.2, 0.35, "SMART INDIA HACKATHON 2026", size=13, color=ACCENT, bold=True)
text(s, 0.7, 2.0, 5.0, 1.9, "Adaptive Path Planning & Collision Avoidance on Unstructured Indian Roads", size=30,
     bold=True, font=HEAD, line_spacing=0.95)
rows = [("Problem Statement ID", "26037"), ("Organisation", "MathWorks"), ("Theme", "Smart Vehicles"),
        ("Category", "Software"), ("Team", "Team name & ID — add before submission")]
for i, (k, v) in enumerate(rows):
    y = 4.35 + i * 0.42
    text(s, 0.7, y, 2.1, 0.35, k, size=12, color=MUTED)
    text(s, 2.75, y, 3.0, 0.35, v, size=12.5, bold=(i < 4), color=TEXT if i < 4 else FAINT)
text(s, 0.7, H - 0.55, 5.2, 0.3, "Research prototype — not a certified autonomous-driving or safety system.", size=9.5, color=FAINT)
notes(s, "Title. PathSense is a camera-based research prototype for PS 26037. Hero image: a real frame of the rendered "
         "HUD on CC-licensed Indian road footage.")

# 2 ---------------------------------------------------------------- problem
s = new_slide(); header(s, 2, "The problem", "Indian roads break the assumptions most planners are built on")
# irregular road scene diagram (top view)
box(s, 0.55, 2.35, 7.35, 4.45, fill=SURF, line=LINE, radius=0.18)
road = s.shapes.build_freeform(Inches(1.35), Inches(6.8))
road.add_line_segments([(Inches(2.2), Inches(2.35)), (Inches(6.1), Inches(2.35)), (Inches(7.2), Inches(6.8))], close=True)
r = road.convert_to_shape(); r.fill.solid(); r.fill.fore_color.rgb = rgb("1D232A"); r.line.fill.background()
for cx, cy, sz in [(3.2, 5.6, 0.34), (5.6, 3.7, 0.26)]:  # potholes
    box(s, cx, cy, sz * 1.6, sz, fill="0F1317", line="2A323B", shape=MSO_SHAPE.OVAL)
agents = [("car", 3.9, 4.55, "Car", TEXT), ("auto", 2.35, 3.5, "Auto-rickshaw", SLOW), ("moto", 5.2, 4.95, "Two-wheeler", STEER),
          ("walk", 1.35, 5.4, "Pedestrian", ORANGE), ("bus", 4.55, 2.62, "Bus", TEXT), ("truck", 6.25, 5.7, "Truck", TEXT),
          ("cow", 3.25, 2.75, "Cattle", BRAKE), ("cart", 6.55, 3.45, "Pushcart", MUTED)]
for ic, x, y, lbl, col in agents:
    box(s, x, y, 0.62, 0.62, fill=SURF2, line=col, shape=MSO_SHAPE.OVAL, lw=1.25)
    icon(s, ic, x + 0.11, y + 0.11, 0.4, {TEXT: "w", SLOW: "a", STEER: "s", ORANGE: "a", BRAKE: "r", MUTED: "m"}[col])
    text(s, x - 0.45, y + 0.65, 1.52, 0.25, lbl, size=10, color=MUTED, align=PP_ALIGN.CENTER)
box(s, 3.85, 6.05, 0.7, 0.55, fill=ACCENT, line=None, radius=0.1)   # ego
text(s, 3.85, 6.05, 0.7, 0.55, "EGO", size=10, bold=True, color=BG, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
arrow(s, 2.85, 3.95, 3.7, 4.75, color=SLOW, width=1.75)     # auto cutting across
arrow(s, 1.95, 5.6, 3.1, 5.3, color=ORANGE, width=1.75)     # pedestrian crossing
arrow(s, 3.85, 3.25, 4.5, 3.9, color=BRAKE, width=1.75)     # cattle wandering
facts = [("rule", "No lane discipline", "Missing markings, unclear road edges, informal merging"),
         ("merge", "Irregular motion", "Cut-ins, wrong-way riders, sudden pedestrian and animal crossings"),
         ("layers", "Unusual road users", "Auto-rickshaws, pushcarts and cattle are not in standard detector classes"),
         ("pothole", "Unstructured surface", "Potholes, unpaved shoulders, obstacles on the carriageway")]
for i, (ic, t1, t2) in enumerate(facts):
    y = 2.4 + i * 1.12
    icon(s, ic, 8.3, y + 0.05, 0.55, "t", circle=SURF2)
    text(s, 9.05, y, 3.8, 0.35, t1, size=16, bold=True)
    text(s, 9.05, y + 0.36, 3.8, 0.7, t2, size=12.5, color=MUTED)
notes(s, "From the PS background: mixed traffic, no lane discipline, irregular movement, unclear edges and potholes.")

# 3 ---------------------------------------------------------------- why existing approaches struggle
s = new_slide(); header(s, 3, "Why existing approaches struggle", "Structured-road stacks assume what India does not guarantee")
cols = [("Typical structured-road stack", MUTED, ["Lane-marking based path", "Traffic assumed to follow lanes",
                                                 "Fixed object classes (car / truck / person)", "Obstacle = anything close"]),
        ("PathSense", ACCENT, ["Free-space corridor + 17 candidate arcs, no lanes needed",
                               "Per-track motion: cut-in, crossing, oncoming, side-pass",
                               "Auto-rickshaw refinement, animals and riders handled",
                               "Threat = conflict with the ego path, not proximity"])]
for ci, (title, col, items) in enumerate(cols):
    x = 0.55 + ci * 6.2
    box(s, x, 2.4, 5.95, 4.35, fill=SURF if ci == 0 else "0F1B1A", line=LINE if ci == 0 else ACCENT, radius=0.18)
    text(s, x + 0.35, 2.62, 5.3, 0.45, title, size=18, bold=True, color=col)
    for i, it in enumerate(items):
        y = 3.3 + i * 0.83
        icon(s, "block" if ci == 0 else "check", x + 0.35, y + 0.02, 0.36, "m" if ci == 0 else "t")
        text(s, x + 0.9, y, 4.8, 0.7, it, size=14.5, color=TEXT if ci else MUTED)
arrow(s, 6.52, 4.55, 6.73, 4.55, color=ACCENT, width=2.5)
notes(s, "Left: generic assumptions. Right: what the prototype implements (each item is in the repository).")

# 4 ---------------------------------------------------------------- proposed solution
s = new_slide(); header(s, 4, "Proposed solution", "PathSense: see, predict, plan, decide — every frame",
                        "A camera-first perception → planning → decision pipeline tuned for Indian mixed traffic")
picture(s, shot("web_demo", crop=(0.0, 0.0, 1.0, 0.8)), 0.55, 2.45, w=7.6)
pillars = [("visibility", "Perceive", "Detect & track road users, estimate distance and time-to-collision"),
           ("route", "Plan", "Bird's-eye costmap, 17 candidate trajectories, safest steering"),
           ("shield", "Decide", "GO / SLOW DOWN / BRAKE / NO SAFE PATH with the reason and the threat"),
           ("crash", "Respond", "Accident detection with a confirmation-first emergency workflow (simulated)")]
for i, (ic, t1, t2) in enumerate(pillars):
    y = 2.45 + i * 1.08
    icon(s, ic, 8.5, y, 0.58, "t", circle=SURF2)
    text(s, 9.3, y - 0.02, 3.5, 0.35, t1, size=16, bold=True)
    text(s, 9.3, y + 0.33, 3.5, 0.7, t2, size=12, color=MUTED)
text(s, 0.55, 6.38, 7.6, 0.3, "Web app — Demo view: processed drive, live decision panel, clickable decision timeline", size=10, color=FAINT)
notes(s, "Screenshot is the real web app (python app.py) playing a processed drive.")

# 5 ---------------------------------------------------------------- closed loop
s = new_slide(); header(s, 5, "How it works", "A decision loop, re-evaluated on every frame")
import math
cx, cy, R = 4.15, 4.5, 1.75
loop = [("visibility", "Perception", "detect · track · depth"), ("timeline", "Prediction", "motion cues · TTC"),
        ("route", "Planning", "costmap · 17 arcs"), ("shield", "Decision", "risk state · hysteresis"),
        ("loop", "Replanning", "next frame, new data")]
pos = []
for i, (ic, t1, t2) in enumerate(loop):
    a = -math.pi / 2 + i * 2 * math.pi / len(loop)
    x, y = cx + R * math.cos(a), cy + R * math.sin(a)
    pos.append((x, y))
for i in range(len(loop)):
    (x1, y1), (x2, y2) = pos[i], pos[(i + 1) % len(loop)]
    dx, dy = x2 - x1, y2 - y1
    d = math.hypot(dx, dy)
    k = 0.52 / d
    arrow(s, x1 + dx * k, y1 + dy * k, x2 - dx * k, y2 - dy * k, color=ACCENT, width=2)
for (ic, t1, t2), (x, y) in zip(loop, pos):
    box(s, x - 0.45, y - 0.45, 0.9, 0.9, fill=SURF2, line=ACCENT, shape=MSO_SHAPE.OVAL, lw=1.5)
    icon(s, ic, x - 0.25, y - 0.25, 0.5, "t")
    lx = x + 0.55 if x >= cx - 0.1 else x - 2.45
    text(s, lx, y - 0.32, 1.9, 0.3, t1, size=14, bold=True, align=PP_ALIGN.LEFT if x >= cx - 0.1 else PP_ALIGN.RIGHT)
    text(s, lx, y - 0.02, 1.9, 0.3, t2, size=11, color=MUTED, align=PP_ALIGN.LEFT if x >= cx - 0.1 else PP_ALIGN.RIGHT)
stage = STATS["india_bangalore"]["stage"]
box(s, 7.75, 2.45, 5.0, 4.3, fill=SURF, line=LINE, radius=0.18)
text(s, 8.05, 2.65, 4.4, 0.35, "Per-frame budget (measured)", size=16, bold=True)
tag(s, 8.05, 3.07, "MEASURED")
parts = [("Detection + tracking", stage.get("detect_track")), ("Depth", stage.get("depth")), ("Ego-motion", stage.get("ego_motion")),
         ("Costmap + planner", round((stage.get("costmap") or 0) + (stage.get("planner") or 0), 1)),
         ("Decision + accident", round((stage.get("decision") or 0) + (stage.get("accident") or 0), 1))]
mx = max(v or 0 for _, v in parts) or 1
for i, (k, v) in enumerate(parts):
    y = 3.55 + i * 0.58
    text(s, 8.05, y, 2.2, 0.3, k, size=12, color=MUTED)
    bw = 1.6 * (v or 0) / mx
    box(s, 10.25, y + 0.04, max(0.04, bw), 0.24, fill=ACCENT if i in (3, 4) else "3B4652", line=None, radius=0.05)
    text(s, 10.3 + bw + 0.05, y, 1.0, 0.3, f"{v} ms", size=12, bold=True)
text(s, 8.05, 6.35, 4.5, 0.35, f"india_bangalore, {gpu_short}, 720p. Replanning itself is a few ms; "
                               "depth dominates.", size=10, color=FAINT)
notes(s, "Stage timings from outputs/india_bangalore_stats.json. Planner+costmap time is the replanning latency.")

# 6 ---------------------------------------------------------------- architecture
s = new_slide(); header(s, 6, "System architecture", "One pipeline, reused by batch video, web upload and live camera")
chain = [("camera", "Camera / video", "dashcam · upload · phone"), ("visibility", "YOLOv8n + ByteTrack", "detect_track.py"),
         ("layers", "Depth (road-plane)", "depth.py"), ("speed", "Ego-motion", "ego_motion.py"),
         ("map", "BEV costmap", "costmap.py"), ("brain", "Behaviour + threat", "behavior.py · decision.py"),
         ("route", "Candidate trajectories", "planner.py · 17 arcs"), ("shield", "Risk decision", "decision.py"),
         ("flag", "Path recommendation", "HUD · web · events")]
for i, (ic, t1, t2) in enumerate(chain):
    row, col = divmod(i, 3)
    col = col if row % 2 == 0 else 2 - col          # snake layout
    x, y = 0.55 + col * 2.95, 2.45 + row * 1.45
    hl = i in (6, 7)
    box(s, x, y, 2.55, 1.05, fill="0F1B1A" if hl else SURF, line=ACCENT if hl else LINE, radius=0.14)
    icon(s, ic, x + 0.2, y + 0.27, 0.5, "t")
    text(s, x + 0.85, y + 0.2, 1.65, 0.35, t1, size=12.5, bold=True)
    text(s, x + 0.85, y + 0.55, 1.65, 0.35, t2, size=9.5, color=MUTED, font=MONO)
    if i < len(chain) - 1:
        nrow, ncol = divmod(i + 1, 3)
        ncol = ncol if nrow % 2 == 0 else 2 - ncol
        if nrow == row:
            if ncol > col:
                arrow(s, x + 2.57, y + 0.52, x + 2.93, y + 0.52, color=ACCENT)
            else:
                arrow(s, x - 0.02, y + 0.52, x - 0.38, y + 0.52, color=ACCENT)
        else:
            arrow(s, x + 1.27, y + 1.07, x + 1.27, y + 1.43, color=ACCENT)
outs = [("web", "Web app", "app.py · Flask, one command"), ("event", "Event log + replay", "events.py · timeline"),
        ("crash", "Accident monitor", "accident.py · simulated response"), ("mobile", "Live camera", "live.py · phone over HTTPS")]
text(s, 9.55, 2.4, 3.3, 0.3, "OUTPUTS", size=11, color=ACCENT, bold=True)
for i, (ic, t1, t2) in enumerate(outs):
    y = 2.8 + i * 0.98
    icon(s, ic, 9.55, y + 0.05, 0.5, "t", circle=SURF2)
    text(s, 10.2, y, 2.7, 0.3, t1, size=13, bold=True)
    text(s, 10.2, y + 0.33, 2.7, 0.3, t2, size=10, color=MUTED)
notes(s, "Snake order follows the processing order. File names are the actual modules in the repository.")

# 7 ---------------------------------------------------------------- perception
s = new_slide(); header(s, 7, "Perception + tracking", "Know who is on the road — including the ones COCO does not know")
picture(s, G("perception"), 0.55, 2.4, w=7.4)
autos = sum((STATS[n]["autos"] or 0) for n in STATS)
items = [("visibility", "YOLOv8n + ByteTrack", "Persistent track IDs; degenerate boxes filtered"),
         ("moto", "Rider merge", "A person on a two-wheeler is one road user, not a pedestrian"),
         ("auto", "Auto-rickshaw refinement", "CLIP zero-shot re-check of truck/bus tracks → “auto (est.)”"),
         ("cow", "Animals & vulnerable users", "Cattle, dogs and pedestrians get larger safety margins")]
for i, (ic, t1, t2) in enumerate(items):
    y = 2.4 + i * 0.95
    icon(s, ic, 8.35, y, 0.5, "t", circle=SURF2)
    text(s, 9.05, y - 0.03, 3.8, 0.3, t1, size=14, bold=True)
    text(s, 9.05, y + 0.3, 3.8, 0.55, t2, size=11.5, color=MUTED)
box(s, 8.35, 6.2, 4.45, 0.62, fill=SURF, line=LINE, radius=0.1)
text(s, 8.55, 6.2, 4.1, 0.62, [[(f"{autos}", {"bold": True, "size": 20, "color": SLOW}),
                               ("  auto-rickshaw tracks confirmed across the 6 Indian clips", {"size": 11.5, "color": MUTED})]],
     anchor=MSO_ANCHOR.MIDDLE)
notes(s, "Auto-rickshaw count = sum of confirmed_tracks in the six *_stats.json files (current threshold 0.75).")

# 8 ---------------------------------------------------------------- depth + ego + bev
s = new_slide(); header(s, 8, "Depth + ego-motion + bird's-eye view", "From one camera to a top-down map of risk")
steps = [("layers", "Relative depth", "Depth-Anything-V2-Small"), ("rule", "Road-plane calibration", "flat-road fit per frame; horizon & camera height self-calibrated"),
         ("speed", "Ego-motion", "optical flow on the road → speed estimate"), ("map", "BEV costmap", "0.5 m cells, collision radius, 2 m emergency gap")]
for i, (ic, t1, t2) in enumerate(steps):
    x = 0.55 + i * 3.1
    box(s, x, 2.4, 2.8, 1.55, fill=SURF, line=LINE, radius=0.14)
    icon(s, ic, x + 0.2, 2.58, 0.45, "t")
    text(s, x + 0.2, 3.1, 2.45, 0.3, t1, size=13.5, bold=True)
    text(s, x + 0.2, 3.42, 2.45, 0.5, t2, size=10, color=MUTED)
    if i < 3:
        arrow(s, x + 2.82, 3.17, x + 3.08, 3.17, color=ACCENT)
bev = G("bev")
picture(s, bev, 0.55, 4.2, h=2.65)
full = G("bev_full")
picture(s, full, 2.95, 4.2, w=6.65, h=2.65)
box(s, 9.8, 4.2, 3.0, 2.65, fill=SURF, line=LINE, radius=0.14)
text(s, 10.0, 4.35, 2.6, 0.3, "Honest about depth", size=13.5, bold=True, color=SLOW)
text(s, 10.0, 4.72, 2.65, 2.0, "Monocular distances are calibrated estimates, not metric sensor ranges. Checked "
                               "against a car-width prior; no LiDAR ground truth.", size=11, color=MUTED)
notes(s, "BEV crop is the picture-in-picture of the rendered HUD. Depth is road-plane calibrated, not metric.")

# 9 ---------------------------------------------------------------- path planning
s = new_slide(); header(s, 9, "Adaptive path planning", "17 candidate trajectories, re-scored every frame")
# arc fan diagram
box(s, 0.55, 2.4, 6.0, 4.4, fill=SURF, line=LINE, radius=0.18)
ox, oy = 3.55, 6.5
for k in range(17):
    ang = math.radians(-15 + k * 30 / 16)
    L = 3.7
    blocked = k in (8, 9, 10, 11)
    best = k == 5
    col = BRAKE if blocked else (ACCENT if best else "3B4652")
    ln = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(ox), Inches(oy), Inches(ox + L * math.sin(ang) * 1.6), Inches(oy - L * math.cos(ang)))
    ln.line.color.rgb = rgb(col)
    ln.line.width = Pt(3.5 if best else 1.5)
box(s, ox - 0.3, oy - 0.1, 0.6, 0.38, fill=ACCENT, line=None, radius=0.08)
text(s, ox - 0.3, oy - 0.1, 0.6, 0.38, "EGO", size=9.5, bold=True, color=BG, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
box(s, 3.55, 3.55, 0.5, 0.5, fill=SURF2, line=SLOW, shape=MSO_SHAPE.OVAL, lw=1.5)
icon(s, "auto", 3.63, 3.63, 0.34, "a")
text(s, 0.8, 2.55, 5.5, 0.3, [[("■ ", {"color": ACCENT}), ("chosen arc   ", {"color": MUTED}), ("■ ", {"color": BRAKE}),
                              ("blocked   ", {"color": MUTED}), ("■ ", {"color": "3B4652"}), ("free", {"color": MUTED})]], size=11)
pts = [("Cost per arc", "Collision cost along the arc + curvature + deviation from the previous choice (smoothness)"),
       ("Hard vs soft cost", "Only CRITICAL occupancy is lethal; nearby objects add soft cost"),
       ("Bicycle kinematics", "Arcs span ±15° steering for a 2.7 m wheelbase"),
       ("All blocked?", "NO SAFE PATH - BRAKE — only while every arc stays blocked (0.3 s debounce)")]
for i, (t1, t2) in enumerate(pts):
    y = 2.45 + i * 0.9
    text(s, 6.95, y, 5.9, 0.3, t1, size=14.5, bold=True)
    text(s, 6.95, y + 0.33, 5.9, 0.5, t2, size=11.5, color=MUTED)
sm = [STATS[n]["steer_rate_med"] for n in STATS if STATS[n]["steer_rate_med"] is not None]
p95 = [STATS[n]["steer_rate_p95"] for n in STATS if STATS[n]["steer_rate_p95"] is not None]
pl = [STATS[n]["planner_ms"] for n in STATS if STATS[n]["planner_ms"]]
box(s, 6.95, 6.05, 5.85, 0.78, fill=SURF, line=LINE, radius=0.1)
tag(s, 7.1, 6.14, "MEASURED")
rng_ = f"{min(pl):.1f}" if round(min(pl), 1) == round(max(pl), 1) else f"{min(pl):.1f}–{max(pl):.1f}"
text(s, 7.1, 6.46, 5.6, 0.32, f"Replanning {rng_} ms/frame · recommended steering holds steady (median rate "
                              f"{statistics.median(sm):.0f}°/s), p95 ≤ {max(p95):.0f}°/s · 6 Indian clips", size=10.5)
notes(s, "Smoothness = |Δ recommended steering| per second from *_frames.json; replanning = planner stage time.")

# 10 --------------------------------------------------------------- decision engine
s = new_slide(); header(s, 10, "Risk & decision engine", "Threat = conflict with the ego path, not “object is close”")
cls = [("IN PATH", BRAKE, "inside the corridor, closing"), ("ENTERING", ORANGE, "lateral motion into the corridor"),
       ("APPROACHING", SLOW, "drifting toward the path"), ("NEAR", STEER, "close, but parallel / moving away"),
       ("CLEAR", GO, "no conflict")]
text(s, 0.55, 2.35, 5, 0.3, "PATH-CONFLICT CLASS PER OBJECT", size=11, color=MUTED, bold=True)
for i, (k, c, d) in enumerate(cls):
    y = 2.75 + i * 0.72
    chip(s, 0.55, y, k, c, w=1.75, size=11.5)
    text(s, 2.45, y + 0.02, 3.3, 0.3, d, size=12, color=MUTED)
states = [("GO", GO, "path clear"), ("SLOW DOWN", SLOW, "risk developing"), ("BRAKE", BRAKE, "conflict imminent"),
          ("NO SAFE PATH", NSP, "every arc blocked")]
text(s, 6.3, 2.35, 5, 0.3, "DECISION (with hysteresis)", size=11, color=MUTED, bold=True)
for i, (k, c, d) in enumerate(states):
    x = 6.3 + (i % 2) * 3.3
    y = 2.75 + (i // 2) * 1.3
    box(s, x, y, 3.05, 1.1, fill=SURF, line=c, radius=0.14, lw=1.5)
    box(s, x + 0.25, y + 0.24, 0.2, 0.2, fill=c, line=None, shape=MSO_SHAPE.OVAL)
    text(s, x + 0.58, y + 0.13, 2.4, 0.4, k, size=17, bold=True, color=c if c != NSP else BRAKE)
    text(s, x + 0.58, y + 0.56, 2.4, 0.4, d, size=11.5, color=MUTED)
box(s, 6.3, 5.4, 6.35, 1.4, fill="0F1B1A", line=ACCENT, radius=0.14)
text(s, 6.55, 5.52, 5.9, 0.3, "India drives on the left", size=14, bold=True, color=ACCENT)
text(s, 6.55, 5.88, 5.95, 0.9, "An oncoming vehicle on its own side → GO / monitor. Drifting toward us → SLOW DOWN. "
                               "Entering the corridor → BRAKE. Wrong-way vehicle on our side → flagged.", size=11.5, color=TEXT)
notes(s, "decision.py path_threat(): clearance, drift-corrected lateral velocity, predicted clearance within min(TTC, 3 s).")

# 11 --------------------------------------------------------------- scenarios
s = new_slide(); header(s, 11, "Indian-road scenarios", "The five PS scenarios on real Indian footage")
scen = [("Unmarked village road", "ka_kadur", "Rural highway, curves", "PARTIAL"),
        ("Urban intersection, no signals", "india_bangalore", "Dense mixed traffic & queue (no turning manoeuvre)", "PARTIAL"),
        ("Highway merge, slow vehicles", "india_cvraman", "Arterial cut-ins + synthetic cut-in tests", "PARTIAL"),
        ("Dense market, mixed traffic", "blr_iisc", "Pedestrians on the carriageway, two-wheelers", "PARTIAL"),
        ("Sudden cattle crossing", "delhi_cattle", "Cattle crossing at a traffic island", "COVERED")]
for i, (ps, clip, what, cov) in enumerate(scen):
    x = 0.55 + i * 2.5
    box(s, x, 2.4, 2.3, 4.4, fill=SURF, line=LINE, radius=0.14)
    img = G("scen_" + clip)
    s.shapes.add_picture(img, Inches(x + 0.1), Inches(2.5), width=Inches(2.1))
    text(s, x + 0.15, 3.85, 2.05, 0.6, ps, size=12.5, bold=True)
    text(s, x + 0.15, 4.45, 2.05, 0.8, what, size=10.5, color=MUTED)
    st = STATS[clip]
    text(s, x + 0.15, 5.3, 2.05, 0.3, [[(f"{st['dur']} s", {"bold": True}), (f"  ·  BRAKE {st['brake']}%", {"color": MUTED})]], size=11)
    chip(s, x + 0.15, 5.75, "REAL CLIP · " + ("COVERED" if cov == "COVERED" else "PROXY"), GO if cov == "COVERED" else SLOW, w=2.0, size=9.5, h=0.28)
    text(s, x + 0.15, 6.2, 2.05, 0.5, next(c[3] for c in CLIPS if c[0] == clip), size=8.5, color=FAINT)
notes(s, "PROXY = the real clip exercises the scenario only partly (e.g. no turning manoeuvre). "
         "RoadRunner scenes for all five are future work.")

# 12 --------------------------------------------------------------- website / live
s = new_slide(); header(s, 12, "Web app & live demonstration", "One command: python app.py")
picture(s, shot("web_events"), 0.55, 2.4, w=5.6)
picture(s, shot("web_mobile"), 6.35, 2.4, h=4.4)
feats = [("upload", "Upload → process → play", "GPU pipeline with live progress"),
         ("event", "Clickable event timeline", "“why did it brake here?” + replay"),
         ("mobile", "Phone camera → PC", "HTTPS on the LAN, results overlaid"),
         ("tune", "Dark / light, responsive", "desktop · tablet · phone")]
for i, (ic, t1, t2) in enumerate(feats):
    y = 2.4 + i * 0.86
    icon(s, ic, 8.6, y, 0.46, "t", circle=SURF2)
    text(s, 9.22, y - 0.03, 3.6, 0.3, t1, size=13, bold=True)
    text(s, 9.22, y + 0.28, 3.6, 0.3, t2, size=10.5, color=MUTED)
if LIVE:
    box(s, 8.6, 5.95, 4.2, 0.85, fill=SURF, line=LINE, radius=0.1)
    tag(s, 8.75, 6.03, "MEASURED")
    text(s, 8.75, 6.36, 4.0, 0.35, f"Live mode {LIVE['fps']} FPS · round trip {LIVE['rtt_median_ms']} ms "
                                   f"· {LIVE['width']}×{LIVE['height']}, server on the same PC", size=10.5)
text(s, 0.55, 6.6, 5.6, 0.3, "Events view: every BRAKE with its reason and the responsible object", size=10, color=FAINT)
notes(s, "Live numbers from outputs/live_benchmark.json (live_benchmark.py posting real frames to the running server).")

# 13 --------------------------------------------------------------- accident / emergency
s = new_slide(); header(s, 13, "Accident detection & emergency response", "Detect conservatively. Confirm with a human. Never call automatically.")
flow = [("crash", "Accident", "multi-cue + persistence"), ("check", "Confirmation", "impact / jolt / stop + stillness"),
        ("location", "Location", "browser GPS or labelled demo"), ("phone", "Emergency contact", "simulated SMS / e-mail"),
        ("hospital", "Nearby hospital", "OpenStreetMap lookup"), ("ambulance", "Ambulance request", "SIMULATION — user confirms")]
for i, (ic, t1, t2) in enumerate(flow):
    y = 2.35 + i * 0.74
    box(s, 0.55, y, 4.3, 0.62, fill=SURF, line=BRAKE if i in (0, 5) else LINE, radius=0.1)
    icon(s, ic, 0.7, y + 0.12, 0.38, "r" if i in (0, 5) else "t")
    text(s, 1.25, y + 0.05, 1.9, 0.3, t1, size=12.5, bold=True)
    text(s, 1.25, y + 0.33, 3.5, 0.3, t2, size=9.5, color=MUTED)
    if i < 5:
        arrow(s, 2.7, y + 0.63, 2.7, y + 0.73, color=FAINT, width=1.25)
picture(s, shot("web_emergency", crop=(0.3, 0.1, 0.7, 0.93)), 5.1, 2.35, h=4.45)
acc = ACC_VAN["acc"]
conf = acc.get("confirmed") or []
box(s, 8.85, 2.35, 3.95, 2.1, fill=SURF, line=LINE, radius=0.12)
tag(s, 9.0, 2.47, "MEASURED")
text(s, 9.0, 2.85, 3.7, 0.3, "Real public-domain dashcam rear-end", size=12.5, bold=True)
if conf:
    text(s, 9.0, 3.2, 3.7, 1.2, [[(f"CONFIRMED at {conf[0]['time_s']} s", {"bold": True, "color": BRAKE}),
                                 (f"  ·  confidence {int(round(100 * conf[0]['confidence']))}%", {"color": MUTED})],
                                ", ".join(conf[0]["cues"])], size=11.5, color=MUTED)
else:
    text(s, 9.0, 3.2, 3.7, 1.2, [[("NOT CONFIRMED", {"bold": True, "color": SLOW}),
                                 ("  — the cutting-in car filled the frame, motion-blurred; the detector lost it in "
                                  "the last second (conf < 0.2). Near-field sensing needed.", {"color": MUTED})]], size=10.5)
fp = [n for n in STATS if (STATS[n]["acc"].get("confirmed") or [])]
box(s, 8.85, 4.6, 3.95, 1.1, fill=SURF, line=LINE, radius=0.12)
text(s, 9.0, 4.72, 3.7, 0.95, [[(f"{6 - len(fp)} / 6", {"bold": True, "size": 20, "color": GO if not fp else SLOW})],
                               "Indian drives without a false accident confirmation"], size=11, color=MUTED)
box(s, 8.85, 5.85, 3.95, 0.95, fill="1F0F10", line=BRAKE, radius=0.12)
text(s, 9.0, 5.93, 3.7, 0.85, "Nothing is sent or called. Real services need an authorised provider, "
                              "credentials outside Git and explicit user confirmation.", size=10, color=TEXT)
notes(s, "Accident clip: Wikimedia Commons 'The van in front suddenly changed lanes…' (public domain), first 30 s. "
         "Emergency panel screenshot is the web app in DEMO / SIMULATION mode.")

# 14 --------------------------------------------------------------- validation
s = new_slide(); header(s, 14, "Validation & results", "What we measured — and on what")
big = [(f"{passed}/{total}", "regression scenarios pass", "synthetic decision scenarios", GO),
       (f"{len(STATS)}", "real Indian drives", f"{round(sum(STATS[n]['dur'] or 0 for n in STATS) / 60, 1)} min, CC-licensed", ACCENT),
       (f"{min(all_fps):.1f}–{max(all_fps):.1f}", "FPS offline (720p)", f"{gpu_short} · not real time", STEER),
       (f"{sum(len(v) for v in [STATS[n]['beh'] for n in STATS] if v) and sum(sum(STATS[n]['beh'].values()) for n in STATS)}",
        "behaviour cues logged", "cut-in · crossing · oncoming · side-pass", SLOW)]
for i, (n_, l1, l2, c) in enumerate(big):
    x = 0.55 + i * 3.1
    box(s, x, 2.35, 2.85, 1.55, fill=SURF, line=LINE, radius=0.14)
    text(s, x + 0.2, 2.42, 2.5, 0.7, n_, size=32, bold=True, color=c)
    text(s, x + 0.2, 3.1, 2.5, 0.3, l1, size=12.5, bold=True)
    text(s, x + 0.2, 3.42, 2.55, 0.3, l2, size=10, color=MUTED)
tag(s, 0.55, 4.08, "MEASURED")
hdr = ["Clip", "Scenario", "GO %", "SLOW %", "BRAKE %", "BRAKE episodes", "Autos", "FPS"]
rows = [[n, next(c[2] for c in CLIPS if c[0] == n), STATS[n]["go"], STATS[n]["slow"], STATS[n]["brake"], STATS[n]["episodes"],
         STATS[n]["autos"], STATS[n]["fps"]] for n, *_ in CLIPS]
gf = s.shapes.add_table(len(rows) + 1, len(hdr), Inches(0.55), Inches(4.45), Inches(12.25), Inches(2.4))
tbl = gf.table
tbl._tbl.tblPr.find(qn("a:tableStyleId")).text = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"  # "No Style, No Grid"
widths = [1.9, 3.85, 1.0, 1.1, 1.1, 1.5, 0.85, 0.95]
for j, wd in enumerate(widths):
    tbl.columns[j].width = Inches(wd)
for i in range(len(rows) + 1):
    tbl.rows[i].height = Inches(0.34)
    for j in range(len(hdr)):
        cell = tbl.cell(i, j)
        v = hdr[j] if i == 0 else rows[i - 1][j]
        cell.text = "—" if v is None else str(v)
        cell.fill.solid()
        cell.fill.fore_color.rgb = rgb(SURF2 if i == 0 else (SURF if i % 2 else BG))
        cell.margin_left = cell.margin_right = Inches(0.08)
        cell.margin_top = cell.margin_bottom = Inches(0.02)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = cell.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT if j < 2 else PP_ALIGN.RIGHT
        for r_ in p.runs:
            r_.font.size = Pt(11 if i else 10.5)
            r_.font.bold = i == 0
            r_.font.name = MONO if (j == 0 and i) else BODY
            r_.font.color.rgb = rgb(MUTED if i == 0 else TEXT)
notes(s, "All values from outputs/*_stats.json of the current code. BRAKE % = BRAKE + NO SAFE PATH. "
         "Open-loop: decisions are recommendations on recorded video, the vehicle is not controlled.")

# 15 --------------------------------------------------------------- innovation
s = new_slide(); header(s, 15, "What is different", "Built for Indian traffic, explainable by design")
inn = [("merge", "Directional path-threat model", "Left-hand traffic aware: oncoming on its side is not a threat; drifting or entering is."),
       ("auto", "Indian road-user handling", "Auto-rickshaw refinement, rider merge, cattle and pedestrian margins."),
       ("event", "Explainable decisions", "Every BRAKE has a reason, a responsible object and a replayable clip."),
       ("check", "Safety regression suite", f"{total} scenarios incl. cut-ins, crossings, wrong-way and accident false alarms."),
       ("crash", "Confirmation-first emergency flow", "Conservative detection; nothing is sent without a human."),
       ("mobile", "Phone-to-PC live mode", "Any phone camera; heavy models stay on the PC GPU.")]
for i, (ic, t1, t2) in enumerate(inn):
    col, row = i % 3, i // 3
    x, y = 0.55 + col * 4.15, 2.4 + row * 2.2
    box(s, x, y, 3.9, 2.0, fill=SURF, line=LINE, radius=0.16)
    icon(s, ic, x + 0.3, y + 0.28, 0.6, "t", circle=SURF2)
    text(s, x + 0.3, y + 1.0, 3.4, 0.35, t1, size=14.5, bold=True)
    text(s, x + 0.3, y + 1.38, 3.4, 0.6, t2, size=11, color=MUTED)
notes(s, "Each item exists in the repository today.")

# 16 --------------------------------------------------------------- limitations + future
s = new_slide(); header(s, 16, "Limitations & future scope", "Where the prototype stands — stated plainly")
colsx = [("MEASURED", "Today (measured)", [f"{passed}/{total} synthetic scenarios pass",
                                          f"{len(STATS)} real Indian clips, offline {min(all_fps):.1f}–{max(all_fps):.1f} FPS",
                                          "Live mode" + (f" {LIVE['fps']} FPS (same-PC benchmark)" if LIVE else ""),
                                          "Accident confirmed on a real rear-end clip" if conf else "Accident flow: synthetic tests + labelled demo"]),
         ("CAPABILITY", "Prototype limits", ["Camera only — misses frame-filling, blurred vehicles at contact range", "Monocular depth: estimates, not metric",
                                             "Open-loop on recorded video — no vehicle control", "Daytime clips; no night or rain",
                                             "Not real time on a laptop GPU"]),
         ("FUTURE", "Next steps", ["MATLAB / Simulink port, RoadRunner village & intersection scenes",
                                   "Closed-loop tests with a bicycle model; completion-rate metrics",
                                   "Camera + radar/LiDAR fusion (Automated Driving Toolbox)",
                                   "IDD fine-tuning for autos, pushcarts, animals",
                                   "Authorised emergency-provider integration"])]
for i, (kind, t1, items) in enumerate(colsx):
    x = 0.55 + i * 4.15
    box(s, x, 2.4, 3.9, 4.4, fill=SURF, line=LINE, radius=0.16)
    tag(s, x + 0.3, 2.6, kind)
    text(s, x + 0.3, 3.02, 3.4, 0.35, t1, size=16, bold=True)
    for k, it in enumerate(items):
        y = 3.55 + k * 0.62
        box(s, x + 0.32, y + 0.1, 0.1, 0.1, fill={"MEASURED": GO, "CAPABILITY": STEER, "FUTURE": SLOW}[kind], line=None, shape=MSO_SHAPE.OVAL)
        text(s, x + 0.55, y, 3.2, 0.6, it, size=11.5, color=TEXT)
notes(s, "The PS encourages MATLAB/Simulink and RoadRunner; this prototype is Python. The port is listed as future work.")

# 17 --------------------------------------------------------------- conclusion
s = new_slide(); slide_bg(s)
pic = s.shapes.add_picture(G("closing"), Inches(0), Inches(0), width=Inches(W))
scrim = box(s, 0, 0, W, H, fill=BG, line=None, shape=MSO_SHAPE.RECTANGLE)
fill = scrim.fill._xPr.find(qn("a:solidFill"))
clr = fill.find(qn("a:srgbClr"))
etree.SubElement(clr, qn("a:alpha")).set("val", "92000")
icon(s, "route", 0.8, 1.2, 0.6, "t")
text(s, 1.55, 1.2, 6, 0.6, [[("PathSense", {"bold": True, "size": 28})]])
text(s, 0.8, 2.2, 9.5, 1.6, "Perceive the chaos. Predict the conflict. Plan the safest path.", size=36, bold=True, font=HEAD)
text(s, 0.8, 4.0, 9.0, 0.9, "A working, explainable research prototype for Indian mixed traffic — "
                            "with honest numbers and a clear path to a MATLAB / RoadRunner closed-loop evaluation.", size=16, color=MUTED)
box(s, 0.8, 5.3, 5.2, 0.6, fill=SURF, line=LINE, radius=0.1)
text(s, 1.0, 5.3, 5.0, 0.6, [[("$ ", {"color": ACCENT, "font": MONO}), ("python app.py", {"font": MONO, "bold": True})]], size=15,
     anchor=MSO_ANCHOR.MIDDLE)
text(s, 0.8, 6.1, 9, 0.35, "Thank you  ·  SIH 2026  ·  PS 26037", size=14, color=ACCENT, bold=True)
text(s, 0.8, H - 0.5, 11, 0.3, "Research prototype — not a certified autonomous-driving or safety system. "
                               "Emergency features run in simulation only.", size=9.5, color=FAINT)
notes(s, "Close with a live demo of the web app.")

assert len(prs.slides) == N_SLIDES, len(prs.slides)
dst = os.path.join(ROOT, "PathSense_SIH_Presentation.pptx")
prs.save(dst)
print("saved", dst, len(prs.slides), "slides")
