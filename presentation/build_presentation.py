"""
Builds PathSense_SIH_Presentation.pptx on the OFFICIAL SIH 2026 idea-presentation template.

    python presentation/build_presentation.py  [path\\to\\SIH2026-IDEA-Presentation-Format.pptx]

The official format allows at most six slides (title + five), fixed headings, and fixed "idea details pointers";
the pointers are kept verbatim and answered with short points and original vector diagrams. No prototype
screenshots are used. Every measured number is read at build time from outputs/ (scenario_tests_last.json,
live_benchmark.json, *_stats.json) - nothing is typed in by hand.
"""

import json
import os
import re
import sys

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "outputs")
ICONS = os.path.join(ROOT, "presentation", "assets", "icons")
TEMPLATE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "Downloads", "SIH2026-IDEA-Presentation-Format.pptx")

# ------------------------------------------------------------------ design tokens (white template, SIH blue)
NAVY, INK, MUTED, LINE = "1F2A44", "243044", "5B6778", "D5DDE8"
TINT, TINT2 = "F3F7FC", "EAF2FB"
BLUE, TEAL, TEAL_T = "0070C0", "0F9E8E", "E3F4F1"
GO, SLOW, BRAKE = "16A34A", "D97706", "DC2626"
BODY = "Arial"
SERIF = "Times New Roman"


def rgb(h):
    return RGBColor.from_string(h)


def jload(p, d=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return d


def need(v, what):
    if v is None:
        sys.exit(f"missing measured value: {what}")
    return v


# ------------------------------------------------------------------ measured numbers
TESTS = jload(os.path.join(OUT, "scenario_tests_last.json"), {}) or {}
m = re.search(r"(\d+)\s*/\s*(\d+)", TESTS.get("summary", ""))
PASSED, TOTAL = (int(m.group(1)), int(m.group(2))) if m else (None, None)
need(PASSED, "outputs/scenario_tests_last.json (run the suite from app.py /api/system/run-tests)")
LIVE = jload(os.path.join(OUT, "live_benchmark.json"), {}) or {}
CLIPS = ["india_bangalore", "india_newbel", "india_cvraman", "ka_kadur", "blr_iisc", "delhi_cattle", "frederiksted_pier"]
STATS = {c: jload(os.path.join(OUT, f"{c}_stats.json"), {}) for c in CLIPS}
FPS = [s["processing_fps"] for s in STATS.values() if s.get("processing_fps") and s.get("input_resolution", [0])[0] >= 1280]
PLAN_MS = [s["stage_ms_per_frame"]["planner"] for s in STATS.values() if s.get("stage_ms_per_frame")]
N_CLIPS = sum(1 for s in STATS.values() if s)
need(FPS or None, "clip stats")

# ------------------------------------------------------------------ primitives
prs = Presentation(TEMPLATE)


def delete_slide(idx):
    sld = prs.slides._sldIdLst[idx]
    prs.part.drop_rel(sld.rId)
    prs.slides._sldIdLst.remove(sld)


def shape_by_name(s, name):
    return next(sh for sh in s.shapes if sh.name == name)


def remove(sh):
    sh._element.getparent().remove(sh._element)


def set_text_keep_format(sh, text):
    """replace a text frame's text but keep the first run's formatting"""
    tf = sh.text_frame
    p0 = tf.paragraphs[0]
    runs = p0.runs
    runs[0].text = text
    for r in runs[1:]:
        r._r.getparent().remove(r._r)
    for p in tf.paragraphs[1:]:
        p._p.getparent().remove(p._p)


def box(s, x, y, w, h, fill=TINT, line=None, radius=0.08, shape=MSO_SHAPE.ROUNDED_RECTANGLE, lw=0.75, alpha=None):
    sh = s.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid()
        sh.fill.fore_color.rgb = rgb(fill)
        if alpha is not None:
            clr = sh.fill._xPr.find(qn("a:solidFill")).find(qn("a:srgbClr"))
            etree.SubElement(clr, qn("a:alpha")).set("val", str(int(alpha * 100000)))
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = rgb(line)
        sh.line.width = Pt(lw)
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        sh.adjustments[0] = min(0.5, radius / max(0.01, min(w, h)))
    sh.shadow.inherit = False
    return sh


def text(s, x, y, w, h, paras, size=12, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=BODY,
         space=0, bullets=False, line_spacing=None):
    """paras: str | [para]; para: str | [(text, {opts})]."""
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for i, para in enumerate(paras if isinstance(paras, list) else [paras]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if space:
            p.space_after = Pt(space)
        if line_spacing:
            p.line_spacing = line_spacing
        if bullets:
            pPr = p._p.get_or_add_pPr()
            pPr.set("marL", str(int(Inches(0.17)))); pPr.set("indent", str(-int(Inches(0.17))))
            bc = etree.SubElement(pPr, qn("a:buClr")); etree.SubElement(bc, qn("a:srgbClr")).set("val", BLUE)
            etree.SubElement(pPr, qn("a:buFont")).set("typeface", "Arial")
            etree.SubElement(pPr, qn("a:buChar")).set("char", "•")
        for t, o in ([(para, {})] if isinstance(para, str) else para):
            r = p.add_run()
            r.text = t
            f = r.font
            f.name = o.get("font", font)
            f.size = Pt(o.get("size", size))
            f.bold = o.get("bold", bold)
            f.italic = o.get("italic", False)
            f.color.rgb = rgb(o.get("color", color))
    return tb


def pointer(s, x, y, w, label, size=13):
    """an official 'idea details pointer', kept verbatim, styled as a section label"""
    box(s, x, y + 0.04, 0.06, 0.24, fill=BLUE, radius=0.01, shape=MSO_SHAPE.RECTANGLE)
    text(s, x + 0.14, y, w - 0.14, 0.32, label, size=size, bold=True, color=NAVY)


def icon(s, name, x, y, size, color="b", circle=None, pad=0.2):
    if circle:
        box(s, x, y, size, size, fill=circle, shape=MSO_SHAPE.OVAL)
        p = size * pad
        s.shapes.add_picture(os.path.join(ICONS, f"{name}_{color}.png"), Inches(x + p), Inches(y + p), Inches(size - 2 * p), Inches(size - 2 * p))
    else:
        s.shapes.add_picture(os.path.join(ICONS, f"{name}_{color}.png"), Inches(x), Inches(y), Inches(size), Inches(size))


def arrow(s, x1, y1, x2, y2, color=MUTED, width=1.5, dash=False):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    if dash:
        etree.SubElement(ln, qn("a:prstDash")).set("val", "dash")
    tail = etree.SubElement(ln, qn("a:tailEnd"))
    tail.set("type", "triangle"); tail.set("w", "med"); tail.set("len", "med")
    return c


def line(s, x1, y1, x2, y2, color=LINE, width=1.0, dash=None):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    if dash:
        etree.SubElement(c.line._get_or_add_ln(), qn("a:prstDash")).set("val", dash)
    return c


def chip(s, x, y, label, color, w=None, size=10, h=0.28, fill="FFFFFF"):
    w = w or (0.2 + 0.075 * len(label) * size / 10)
    box(s, x, y, w, h, fill=fill, line=color, radius=h / 2, lw=1.1)
    text(s, x, y, w, h, label, size=size, bold=True, color=color, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    return w


def notes(s, t):
    s.notes_slide.notes_text_frame.text = t


slides = list(prs.slides)
if len(slides) == 7:          # slide 7 = "IMPORTANT INSTRUCTIONS" - the template says to delete it before upload
    delete_slide(6)
slides = list(prs.slides)
assert len(slides) == 6, len(slides)

# ================================================================== 1. TITLE PAGE (official fields)
s = slides[0]
tb = shape_by_name(s, "TextBox 9")
values = {"Problem Statement ID": " 26037",
          "Problem Statement Title": " Adaptive Path Planning and Collision Avoidance for Autonomous Vehicles on Unstructured Indian Roads",
          "Theme": " Smart Vehicles", "PS Category": " Software", "Team ID": "", "Team Name": ""}
for p in tb.text_frame.paragraphs:
    full = "".join(r.text for r in p.runs)
    key = next((k for k in values if full.strip().startswith(k)), None)
    if key is None or not p.runs:
        continue
    if key == "PS Category":                       # "PS Category- Software/Hardware" -> label + value "Software"
        for r in p.runs:
            r.text = r.text.replace("Software/Hardware", "").replace("Software/ Hardware", "")
        values[key] = " Software"
    if values[key]:
        r = p.add_run()
        r.text = values[key]
        r.font.bold = False
        r.font.size = p.runs[0].font.size
        r.font.name = p.runs[0].font.name
        r.font.color.rgb = rgb(INK)
# the template's 28 pt justified bullets overflow once the long PS title is filled in: same bullets, fitted size
for p in tb.text_frame.paragraphs:
    p.alignment = PP_ALIGN.LEFT
    p.line_spacing = 1.0
    p.space_before = Pt(0)
    p.space_after = Pt(14)
    for r in p.runs:
        r.font.size = Pt(18)
notes(s, "Team ID and Team Name are left as the official template fields - fill them in from the SIH portal.")

# ================================================================== 2. IDEA TITLE / PROPOSED SOLUTION
s = slides[1]
set_text_keep_format(shape_by_name(s, "Title 1"), "PATHSENSE")
remove(shape_by_name(s, "TextBox 8"))
text(s, 0.4, 1.28, 12.5, 0.38, [[("❖ ", {"color": BLUE}), ("Proposed Solution (Describe your Idea/Solution/Prototype)", {"color": NAVY})]],
     size=17, bold=True)
text(s, 0.4, 1.68, 12.5, 0.32, "Threat-aware perception and path planning for mixed Indian traffic — it reacts to conflicts with the ego path, not to mere proximity.",
     size=12.5, color=MUTED)

L = 0.4; LW = 6.75
blocks = [
    ("Detailed explanation of the proposed solution", [
        "Detects and tracks every road user from one front camera — cars, autos, two-wheelers, pedestrians, cattle",
        "Estimates distance, closing speed and time-to-collision; builds a bird’s-eye risk map",
        "Scores 17 candidate paths every frame → GO / SLOW DOWN / BRAKE / NO SAFE PATH, with the reason"]),
    ("How it addresses the problem", [
        "No lane markings needed: free-space corridor + candidate trajectories",
        "Irregular motion read from track history: cut-in, crossing, oncoming, wrong-way",
        "Left-hand-traffic aware: oncoming vehicles on their side and footpath pedestrians do not trigger braking"]),
    ("Innovation and uniqueness of the solution", [
        "Path-threat classes: IN PATH · ENTERING · APPROACHING · NEAR · CLEAR",
        "Explainable, replayable decisions + a safety regression suite",
        "Confirmation-first accident response (simulation) and phone-camera live mode"]),
]
y = 2.15
for label, pts in blocks:
    pointer(s, L, y, LW, label, size=12.5)
    text(s, L + 0.14, y + 0.36, LW - 0.2, 1.2, pts, size=11.5, color=INK, bullets=True, space=3)
    y += 1.55

# --- concept diagram: top-down road, left-hand traffic
PX, PY, PW, PH = 7.45, 2.1, 5.45, 4.62
box(s, PX, PY, PW, PH, fill="FFFFFF", line=LINE, radius=0.12)
text(s, PX + 0.2, PY + 0.12, PW - 0.4, 0.28, "Threat to the ego path — not proximity", size=11.5, bold=True, color=NAVY)
fpL, rd0, rd1, fpR = PX + 0.35, PX + 0.95, PX + PW - 0.95, PX + PW - 0.35
top, bot = PY + 0.5, PY + PH - 0.62
box(s, fpL, top, rd0 - fpL, bot - top, fill="EEF1F4", radius=0.02, shape=MSO_SHAPE.RECTANGLE)           # left footpath
box(s, rd1, top, fpR - rd1, bot - top, fill="EEF1F4", radius=0.02, shape=MSO_SHAPE.RECTANGLE)           # right footpath
box(s, rd0, top, rd1 - rd0, bot - top, fill="E6EAEF", radius=0.02, shape=MSO_SHAPE.RECTANGLE)           # carriageway
mid = (rd0 + rd1) / 2
line(s, mid, top + 0.05, mid, bot - 0.05, color="FFFFFF", width=2.0, dash="dash")
laneL = (rd0 + mid) / 2
# ego corridor
box(s, laneL - 0.5, top + 0.25, 1.0, bot - top - 1.0, fill=TEAL, alpha=0.18, radius=0.05)
text(s, laneL - 0.5, top + 0.3, 1.0, 0.25, "ego path", size=9, color=TEAL, bold=True, align=PP_ALIGN.CENTER)
box(s, laneL - 0.28, bot - 0.72, 0.56, 0.62, fill=TEAL, radius=0.08)
text(s, laneL - 0.28, bot - 0.72, 0.56, 0.62, "EGO", size=9, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
arrow(s, laneL, bot - 0.74, laneL, bot - 1.05, color=TEAL, width=2)
laneR = (mid + rd1) / 2
# oncoming car on its own side
box(s, laneR - 0.27, top + 0.35, 0.54, 0.62, fill="FFFFFF", line=NAVY, radius=0.08, lw=1.2)
icon(s, "car", laneR - 0.17, top + 0.46, 0.34, "n")
arrow(s, laneR, top + 1.0, laneR, top + 1.45, color=NAVY, width=1.75)
chip(s, laneR - 0.62, top + 1.52, "Own side · GO", GO, w=1.24, size=9)
# pedestrian walking on the left footpath
fx = (fpL + rd0) / 2
box(s, fx - 0.2, top + 2.05, 0.4, 0.4, fill="FFFFFF", line=NAVY, shape=MSO_SHAPE.OVAL, lw=1.2)
icon(s, "walk", fx - 0.13, top + 2.12, 0.26, "n")
arrow(s, fx, top + 2.03, fx, top + 1.7, color=NAVY, width=1.5)
chip(s, fpL - 0.02, top + 2.52, "Footpath · GO", GO, w=1.2, size=9)
# pedestrian crossing into the corridor
cx, cy = mid + 0.15, top + 0.55
box(s, cx - 0.2, cy, 0.4, 0.4, fill="FFFFFF", line=BRAKE, shape=MSO_SHAPE.OVAL, lw=1.5)
icon(s, "walk", cx - 0.13, cy + 0.07, 0.26, "r")
arrow(s, cx - 0.22, cy + 0.2, laneL + 0.1, cy + 0.2, color=BRAKE, width=2)
chip(s, laneL - 0.45, cy + 0.5, "Crossing in · BRAKE", BRAKE, w=1.45, size=9)
# two-wheeler drifting toward the path
mx, my = mid + 0.2, top + 2.15
box(s, mx - 0.2, my, 0.4, 0.4, fill="FFFFFF", line=SLOW, shape=MSO_SHAPE.OVAL, lw=1.5)
icon(s, "moto", mx - 0.14, my + 0.07, 0.28, "a")
arrow(s, mx - 0.18, my + 0.08, mx - 0.5, my - 0.22, color=SLOW, width=1.75)
chip(s, mx + 0.25, my + 0.06, "Drifting in · SLOW", SLOW, w=1.42, size=9)
text(s, PX + 0.2, PY + PH - 0.5, PW - 0.4, 0.4, "India drives on the left: oncoming traffic is expected on the right. Only a predicted conflict with the ego corridor changes the decision.",
     size=9.5, color=MUTED)
notes(s, "Diagram: original illustration of the path-threat model (decision.py path_threat).")

# ================================================================== 3. TECHNICAL APPROACH
s = slides[2]
remove(shape_by_name(s, "TextBox 8"))
pointer(s, 0.4, 1.3, 12.5, "Technologies to be used (e.g. programming languages, frameworks, hardware)")
groups = [("Languages", ["Python 3.10", "JavaScript"], BLUE),
          ("AI models (pretrained, open)", ["YOLOv8n + ByteTrack", "Depth-Anything-V2", "CLIP ViT-B/32"], BLUE),
          ("Frameworks", ["PyTorch (CUDA)", "OpenCV", "Flask web app"], BLUE),
          ("Hardware", ["1 front camera / phone", "Laptop GPU (RTX 3050)"], BLUE),
          ("Next (PS-recommended)", ["MATLAB / Simulink", "RoadRunner", "Automated Driving Toolbox"], SLOW)]
gx = 0.4
widths = [1.75, 2.75, 2.35, 2.45, 3.1]
for (title, items, col), gw in zip(groups, widths):
    text(s, gx, 1.72, gw, 0.24, title, size=10, bold=True, color=MUTED)
    yy = 1.98
    for it in items:
        w = gw - 0.12
        box(s, gx, yy, w, 0.3, fill=TINT if col == BLUE else "FFF7EB", line=LINE if col == BLUE else "F3D19C", radius=0.08)
        text(s, gx + 0.1, yy, w - 0.15, 0.3, it, size=10.5, color=INK, anchor=MSO_ANCHOR.MIDDLE)
        yy += 0.36
    gx += gw + 0.08

pointer(s, 0.4, 3.18, 12.5, "Methodology and process for implementation (Flow Charts/Images/ working prototype)")
flow = [("camera", "Camera", "dashcam · phone"), ("visibility", "Perceive", "detect · track · auto-rickshaw"),
        ("layers", "Measure", "road-plane depth · TTC · ego speed"), ("map", "Map", "bird’s-eye risk grid"),
        ("brain", "Predict", "motion cues · path threat"), ("route", "Plan", "17 candidate arcs"),
        ("shield", "Decide", "GO · SLOW · BRAKE")]
fw, fg, fx0, fy = 1.62, 0.2, 0.4, 3.62
for i, (ic, t1, t2) in enumerate(flow):
    x = fx0 + i * (fw + fg)
    hl = i >= 5
    box(s, x, fy, fw, 1.18, fill=TEAL_T if hl else "FFFFFF", line=TEAL if hl else LINE, radius=0.1, lw=1.0)
    icon(s, ic, x + 0.14, fy + 0.14, 0.34, "e" if hl else "b")
    text(s, x + 0.55, fy + 0.15, fw - 0.6, 0.3, t1, size=12, bold=True, color=NAVY)
    text(s, x + 0.14, fy + 0.58, fw - 0.24, 0.55, t2, size=9.5, color=MUTED)
    if i < len(flow) - 1:
        arrow(s, x + fw + 0.01, fy + 0.59, x + fw + fg - 0.01, fy + 0.59, color=BLUE, width=1.5)
# replanning loop
lx0, lx1, ly = fx0 + 0.8, fx0 + 6 * (fw + fg) + fw / 2, fy + 1.32
line(s, lx1, fy + 1.19, lx1, ly, color=TEAL, width=1.5)
line(s, lx1, ly, lx0, ly, color=TEAL, width=1.5)
arrow(s, lx0, ly, lx0, fy + 1.2, color=TEAL, width=1.5)
text(s, (lx0 + lx1) / 2 - 2.2, ly + 0.03, 4.4, 0.24, "re-planned every frame (closed decision loop)", size=9.5, color=TEAL, bold=True, align=PP_ALIGN.CENTER)
# working prototype - measured
live_txt = f"{LIVE['fps']} FPS · {LIVE['rtt_median_ms']} ms" if LIVE.get("fps") else "—"
proto = [(f"{PASSED}/{TOTAL}", "safety regression scenarios pass"),
         (f"{min(FPS):.1f}–{max(FPS):.1f}", "FPS offline, 720p, laptop GPU"),
         (live_txt, "live phone→PC mode (same-PC benchmark)"),
         (f"{min(PLAN_MS):.1f}–{max(PLAN_MS):.1f} ms" if round(min(PLAN_MS), 1) != round(max(PLAN_MS), 1) else f"{min(PLAN_MS):.1f} ms", "re-planning time per frame"),
         (f"{N_CLIPS}", "real drives validated (CC-licensed)")]
box(s, 0.4, 5.2, 12.5, 1.55, fill=TINT, radius=0.12)
text(s, 0.62, 5.3, 4, 0.26, "Working prototype — measured", size=11, bold=True, color=NAVY)
pw = 12.1 / len(proto)
for i, (big, small) in enumerate(proto):
    x = 0.62 + i * pw
    text(s, x, 5.62, pw - 0.15, 0.5, big, size=20, bold=True, color=BLUE)
    text(s, x, 6.13, pw - 0.2, 0.55, small, size=10, color=MUTED)
notes(s, "Numbers: outputs/scenario_tests_last.json, *_stats.json (processing_fps, stage_ms_per_frame.planner), "
         "outputs/live_benchmark.json. Offline FPS is not real time. MATLAB/Simulink/RoadRunner are planned, not implemented.")

# ================================================================== 4. FEASIBILITY AND VIABILITY
s = slides[3]
remove(shape_by_name(s, "TextBox 8"))
pointer(s, 0.4, 1.3, 12.5, "Analysis of the feasibility of the idea")
feas = [("check", "Technically proven", "End-to-end prototype runs today: video or phone camera in, explained decision out."),
        ("speed", "Low cost", "One camera and a commodity GPU; open pretrained models — no custom training needed."),
        ("layers", "Modular", "Each stage (detect, depth, plan, decide) is swappable — ready for sensor fusion or MATLAB.")]
for i, (ic, t1, t2) in enumerate(feas):
    x = 0.4 + i * 4.2
    box(s, x, 1.72, 4.0, 1.02, fill=TINT, radius=0.1)
    icon(s, ic, x + 0.18, 1.88, 0.5, "b", circle="FFFFFF", pad=0.18)
    text(s, x + 0.82, 1.84, 3.05, 0.28, t1, size=12, bold=True, color=NAVY)
    text(s, x + 0.82, 2.13, 3.05, 0.6, t2, size=10, color=MUTED)

pointer(s, 0.4, 2.95, 6.1, "Potential challenges and risks")
pointer(s, 6.75, 2.95, 6.1, "Strategies for overcoming these challenges")
risks = [("Monocular depth is an estimate, not a measurement", "Road-plane calibration today; radar/LiDAR fusion next"),
         ("Not yet real time on a laptop GPU", "Model export (ONNX/TensorRT), lower input size, embedded GPU"),
         ("Unusual road users (pushcarts, animals)", "Fine-tune on the Indian Driving Dataset (IDD)"),
         ("Over- or under-braking in dense traffic", f"Path-threat model + {TOTAL}-scenario regression suite on every change"),
         ("Validation limited to recorded video", "Closed-loop RoadRunner / Simulink scenes for all five PS scenarios"),
         ("False accident alarms", "Multi-cue confirmation + human confirmation; simulation-only today")]
ry = 3.33
for i, (r_, m_) in enumerate(risks):
    y = ry + i * 0.4
    if i % 2 == 0:
        box(s, 0.4, y, 12.5, 0.4, fill="F7F9FC", radius=0.02, shape=MSO_SHAPE.RECTANGLE)
    box(s, 0.52, y + 0.15, 0.1, 0.1, fill=SLOW, shape=MSO_SHAPE.OVAL)
    text(s, 0.75, y, 5.6, 0.4, r_, size=11, color=INK, anchor=MSO_ANCHOR.MIDDLE)
    arrow(s, 6.3, y + 0.2, 6.62, y + 0.2, color=BLUE, width=1.25)
    box(s, 6.8, y + 0.15, 0.1, 0.1, fill=GO, shape=MSO_SHAPE.OVAL)
    text(s, 7.03, y, 5.8, 0.4, m_, size=11, color=INK, anchor=MSO_ANCHOR.MIDDLE)
# roadmap
steps = [("Prototype", "done", GO), ("Simulation (RoadRunner / Simulink)", "next", BLUE), ("Sensor fusion", "planned", MUTED), ("Field pilot", "planned", MUTED)]
sy, sx0, sx1 = 6.2, 0.9, 12.4
line(s, sx0, sy, sx1, sy, color=LINE, width=2)
for i, (t1, t2, col) in enumerate(steps):
    x = sx0 + i * (sx1 - sx0) / (len(steps) - 1)
    box(s, x - 0.11, sy - 0.11, 0.22, 0.22, fill=col, shape=MSO_SHAPE.OVAL)
    al = PP_ALIGN.LEFT if i == 0 else (PP_ALIGN.RIGHT if i == len(steps) - 1 else PP_ALIGN.CENTER)
    bx = x - 0.12 if i == 0 else (x - 3.08 if i == len(steps) - 1 else x - 1.6)
    text(s, bx, sy + 0.16, 3.2, 0.45, [[(t1, {"bold": True, "color": NAVY}), (f"  {t2}", {"color": col})]], size=10.5, align=al)
notes(s, "Risks and strategies reflect the documented limitations in README (camera-only, monocular depth, not real time, open-loop validation).")

# ================================================================== 5. IMPACT AND BENEFITS
s = slides[4]
remove(shape_by_name(s, "TextBox 8"))
pointer(s, 0.4, 1.3, 8.1, "Potential impact on the target audience")
aud = [("walk", "Pedestrians & two-wheelers", "Conflicts with the path are flagged early; people beside the road are not."),
       ("car", "Drivers & fleets", "Taxis, autos, logistics: driver-assist warnings that explain why."),
       ("science", "ADAS / AV developers", "Indian-road decision logic and a reusable scenario test suite."),
       ("ambulance", "Emergency response", "Confirmation-first accident alerts with location and nearby hospitals.")]
for i, (ic, t1, t2) in enumerate(aud):
    col, row = i % 2, i // 2
    x, y = 0.4 + col * 4.1, 1.72 + row * 1.12
    box(s, x, y, 3.95, 1.0, fill=TINT, radius=0.1)
    icon(s, ic, x + 0.16, y + 0.2, 0.55, "b", circle="FFFFFF", pad=0.18)
    text(s, x + 0.85, y + 0.12, 3.0, 0.28, t1, size=12, bold=True, color=NAVY)
    text(s, x + 0.85, y + 0.41, 3.0, 0.55, t2, size=10, color=MUTED)
box(s, 8.75, 1.72, 4.15, 2.12, fill="FFFFFF", line=LINE, radius=0.12)
text(s, 8.97, 1.84, 3.8, 0.26, "Why it matters — India, 2022", size=10.5, bold=True, color=MUTED)
text(s, 8.97, 2.12, 3.8, 0.55, "4,61,312", size=30, bold=True, color=BLUE)
text(s, 8.97, 2.67, 3.8, 0.26, "road accidents", size=10.5, color=INK)
text(s, 8.97, 2.95, 3.8, 0.5, "1,68,491", size=26, bold=True, color=BRAKE)
text(s, 8.97, 3.43, 3.8, 0.3, "people killed  (MoRTH, Road Accidents in India 2022)", size=9.5, color=MUTED)

pointer(s, 0.4, 4.12, 12.5, "Benefits of the solution (social, economic, environmental, etc.)")
ben = [("shield", "Social", GO, ["Safer mixed traffic for vulnerable road users", "Decisions come with reasons — builds trust and aids audits"]),
       ("speed", "Economic", BLUE, ["Camera-first, open-source stack on commodity hardware", "Phone camera works for demos and training"]),
       ("loop", "Environmental", TEAL, ["Fewer unnecessary hard brakes → smoother driving (expected, not yet measured)", "Software upgrade path — no new hardware for pilots"])]
for i, (ic, t1, col, pts) in enumerate(ben):
    x = 0.4 + i * 4.2
    box(s, x, 4.52, 4.0, 2.2, fill="FFFFFF", line=LINE, radius=0.12)
    icon(s, ic, x + 0.2, 4.67, 0.44, {GO: "g", BLUE: "b", TEAL: "e"}[col])
    text(s, x + 0.78, 4.72, 3.0, 0.32, t1, size=13.5, bold=True, color=col)
    text(s, x + 0.25, 5.24, 3.6, 1.45, pts, size=12.5, color=INK, bullets=True, space=6)
notes(s, "Accident statistics: Ministry of Road Transport & Highways, 'Road Accidents in India 2022'. "
         "Environmental benefit is an expectation, not a measured result.")

# ================================================================== 6. RESEARCH AND REFERENCES
s = slides[5]
remove(shape_by_name(s, "TextBox 8"))
pointer(s, 0.4, 1.3, 12.5, "Details / Links of the reference and research work")
refs_l = [("Perception & models", [
    "Jocher et al., Ultralytics YOLOv8 — github.com/ultralytics/ultralytics",
    "Zhang et al., ByteTrack, ECCV 2022 — arXiv:2110.06864",
    "Yang et al., Depth Anything V2, NeurIPS 2024 — arXiv:2406.09414",
    "Radford et al., CLIP, ICML 2021 — arXiv:2103.00020"]),
    ("Planning & decision", [
        "Paden et al., Motion planning & control for self-driving urban vehicles, IEEE T-IV 2016 — arXiv:1604.07446",
        "Kinematic bicycle model for candidate-arc generation (17 arcs, up to 15 degrees steering)"])]
refs_r = [("Data, tools & context", [
    "SIH 2026 PS 26037 (MathWorks) — problem statement & expected solution",
    "Varma et al., Indian Driving Dataset (IDD), WACV 2019 — idd.insaan.iiit.ac.in",
    "MathWorks RoadRunner & Automated Driving Toolbox — mathworks.com (planned)",
    "MoRTH, Road Accidents in India 2022 — morth.nic.in",
    "OpenStreetMap Overpass API — nearby-hospital lookup"]),
    ("Validation footage (Wikimedia Commons)", [
        "Bengaluru & Karnataka drives — L. Shyamal (CC0); Delhi cattle — Fowler&fowler (CC BY-SA 3.0)",
        "Frederiksted pier (left-hand traffic) — John Edwards (CC BY 3.0), 45 s excerpt"])]
import math
for col, groups_ in enumerate([refs_l, refs_r]):
    x = 0.4 + col * 6.35
    y = 1.78
    for title, items in groups_:
        n_lines = sum(math.ceil(len(it) / 78) for it in items)      # ~78 characters per line at 11 pt in 5.75"
        hh = 0.62 + 0.27 * n_lines + 0.06 * len(items)
        box(s, x, y, 6.15, hh, fill=TINT if col == 0 else "FFFFFF", line=None if col == 0 else LINE, radius=0.1)
        text(s, x + 0.2, y + 0.13, 5.8, 0.28, title, size=12.5, bold=True, color=NAVY)
        text(s, x + 0.2, y + 0.5, 5.75, hh - 0.55, items, size=11, color=INK, bullets=True, space=4)
        y += hh + 0.22
text(s, 0.4, 6.52, 12.5, 0.3, "Prototype status: research prototype — not a certified autonomous-driving or safety system; emergency features run in simulation only.",
     size=9.5, color=MUTED)

dst = os.path.join(ROOT, "PathSense_SIH_Presentation.pptx")
prs.save(dst)
print("saved", dst, len(prs.slides), "slides")
