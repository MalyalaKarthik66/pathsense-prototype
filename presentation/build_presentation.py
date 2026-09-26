"""
Builds PathSense_SIH_Presentation.pptx (8 slides) in the visual language of the team's reference deck.

    python presentation/build_presentation.py  [path\\to\\finalforsih.pptx]

Structure follows the SIH 2026 idea-presentation format as realised in the reference deck:
  1 Title page · 2 Proposed solution · 3 Technical approach · 4 Prototype · 5 Feasibility and viability ·
  6 Proof of concept, impact & benefits · 7 Business model canvas · 8 Research and references.

The reference deck is used for its DESIGN only: header, footer, logo placement, notebook cards, colour boxes,
typography. Every piece of content is replaced with PathSense content, and every reference-specific picture is
replaced with original material: 3D concept renders (presentation/hero/render_hero.mjs), editable diagrams, icons
(presentation/make_icons.js) and technology logos (presentation/make_logos.js).
No prototype measurements and no prototype screenshots are used anywhere.
"""

import os
import sys
from copy import deepcopy

from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "presentation", "assets")
ICONS, LOGOS, SCENES = (os.path.join(ASSETS, d) for d in ("icons", "logos", "scenes"))
CROPS = os.path.join(ASSETS, "deck")
os.makedirs(CROPS, exist_ok=True)
REFERENCE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "Downloads", "finalforsih.pptx")

TEAM_NAME, TEAM_ID = "Ctrl Alt Elite", "161638"
PS_ID, THEME = "SIH26037", "Smart Vehicles"
PS_TITLE = "Adaptive Path Planning and Collision Avoidance for Autonomous Vehicles on Unstructured Indian Roads"
REPO = "https://github.com/MalyalaKarthik66/pathsense-prototype"

# reference-deck palette
NAVY, INK, SUB, LINE = "1B2A41", "1A1A1A", "333F55", "C9D3E0"
BLUE, GREEN, PURPLE, ORANGE, RED, TEAL, GREY = "2F6DB5", "2E8B57", "6A4C9C", "E07B39", "C0392B", "0F9E8E", "6B7280"
LBLUE, LGREEN, LYELLOW, LPURPLE, LORANGE, LTEAL = "CFE3F6", "D5EDDF", "FBEFC5", "E3DAF2", "F9DCC6", "D3EFEA"
BEIGE = "F6EDD9"

prs = Presentation(REFERENCE)
SL = list(prs.slides)
assert len(SL) == 8, f"expected the 8-slide reference deck, got {len(SL)}"


# ------------------------------------------------------------------ helpers: edit reference text in place
def shp(s, name):
    return next(x for x in s.shapes if x.name == name)


def remove(sh):
    sh._element.getparent().remove(sh._element)


def _pick(runs, kind):
    for r in runs:
        rp = r.find(qn("a:rPr"))
        if rp is None:
            continue
        b, i, u = rp.get("b") == "1", rp.get("i") == "1", rp.get("u") not in (None, "none")
        if (kind == "b" and b and not u) or (kind == "i" and i) or (kind == "l" and u) or (kind == "n" and not b and not i and not u):
            return r
    return None


def fill(sh, paras):
    """Replace a text frame's paragraphs, cloning the reference paragraph/run formatting.
    paras: [para]; para = str | [(text, kind[, {sz, color, url}])] | (template_index, runs).
    kind: 'b' bold, 'n' normal, 'i' italic, 'l' link."""
    tx = sh.text_frame._txBody
    old = tx.findall(qn("a:p"))
    tmpl = [p for p in old if p.findall(qn("a:r"))] or old
    every = [r for p in tmpl for r in p.findall(qn("a:r"))]
    for p in old:
        tx.remove(p)
    for para in paras:
        ti, runs = (para if isinstance(para, tuple) else (0, para))
        runs = [(runs, "n")] if isinstance(runs, str) else runs
        tp = tmpl[min(ti, len(tmpl) - 1)]
        np_ = etree.SubElement(tx, qn("a:p"))
        if tp.find(qn("a:pPr")) is not None:
            np_.append(deepcopy(tp.find(qn("a:pPr"))))
        own = tp.findall(qn("a:r"))
        for run in runs:
            text, kind, opt = run[0], run[1], (run[2] if len(run) > 2 else {})
            src = _pick(own, kind)
            if src is None:
                src = _pick(every, kind)
            if src is None:
                src = (own or every)[0]
            rPr = deepcopy(src.find(qn("a:rPr")))
            for h in rPr.findall(qn("a:hlinkClick")):
                rPr.remove(h)
            rPr.attrib.pop("err", None)
            rPr.set("b", "1" if kind == "b" else "0")
            if kind != "i":
                rPr.attrib.pop("i", None)
            if kind != "l":
                rPr.attrib.pop("u", None)
            if "sz" in opt:
                rPr.set("sz", str(int(opt["sz"] * 100)))
            if "color" in opt:
                sf = rPr.find(qn("a:solidFill"))
                if sf is None:
                    sf = etree.Element(qn("a:solidFill")); rPr.insert(0, sf)
                for c in list(sf):
                    sf.remove(c)
                etree.SubElement(sf, qn("a:srgbClr")).set("val", opt["color"])
            if kind == "l" and opt.get("url"):
                rid = sh.part.relate_to(opt["url"], RT.HYPERLINK, is_external=True)
                etree.SubElement(rPr, qn("a:hlinkClick")).set(qn("r:id"), rid)
            r = etree.SubElement(np_, qn("a:r"))
            r.append(rPr)
            etree.SubElement(r, qn("a:t")).text = text


def place(sh, x=None, y=None, w=None, h=None):
    for k, v in (("left", x), ("top", y), ("width", w), ("height", h)):
        if v is not None:
            setattr(sh, k, Inches(v))


# ------------------------------------------------------------------ helpers: new editable elements in the reference style
def rgb(h):
    return RGBColor.from_string(h)


def rect(s, x, y, w, h, fill=None, line=None, lw=0.75, radius=None, dash=None, shape=None):
    kind = shape or (MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE)
    sh = s.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid(); sh.fill.fore_color.rgb = rgb(fill)
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = rgb(line); sh.line.width = Pt(lw)
        if dash:
            etree.SubElement(sh.line._get_or_add_ln(), qn("a:prstDash")).set("val", dash)
    if radius and kind == MSO_SHAPE.ROUNDED_RECTANGLE:
        sh.adjustments[0] = min(0.5, radius / max(0.01, min(w, h)))
    sh.shadow.inherit = False
    return sh


def text(s, x, y, w, h, paras, size=9, color=NAVY, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font="Arial",
         space=0, bullets=False, underline=False, italic=False):
    """paras: str | [para]; para: str | [(text, {bold,size,color,italic,url})]"""
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
        if bullets:
            pPr = p._p.get_or_add_pPr()
            pPr.set("marL", str(int(Inches(0.12)))); pPr.set("indent", str(-int(Inches(0.12))))
            etree.SubElement(pPr, qn("a:buFont")).set("typeface", "Arial")
            etree.SubElement(pPr, qn("a:buChar")).set("char", "•")
        for t, o in ([(para, {})] if isinstance(para, str) else para):
            r = p.add_run()
            r.text = t
            f = r.font
            f.name = font
            f.size = Pt(o.get("size", size))
            f.bold = o.get("bold", bold)
            f.italic = o.get("italic", italic)
            f.underline = o.get("underline", underline)
            f.color.rgb = rgb(o.get("color", color))
            if o.get("url"):
                r.hyperlink.address = o["url"]
    return tb


def icon(s, name, x, y, size, color="b", circle=None, pad=0.2):
    f = os.path.join(ICONS, f"{name}_{color}.png")
    if circle:
        rect(s, x, y, size, size, fill=circle, shape=MSO_SHAPE.OVAL)
        p = size * pad
        return s.shapes.add_picture(f, Inches(x + p), Inches(y + p), Inches(size - 2 * p), Inches(size - 2 * p))
    return s.shapes.add_picture(f, Inches(x), Inches(y), Inches(size), Inches(size))


def logo(s, name, x, y, size):
    return s.shapes.add_picture(os.path.join(LOGOS, f"{name}.png"), Inches(x), Inches(y), Inches(size), Inches(size))


def cover(name, src, w, h, fx=0.5, fy=0.5, zoom=1.0):
    """crop an original render to the box's aspect ratio (cover), around a focus point, and save a slide-ready JPEG"""
    im = Image.open(os.path.join(SCENES, f"{src}.png")).convert("RGB")
    W, H = im.size
    ar = w / h
    cw = W / zoom
    ch = cw / ar
    if ch > H / zoom:
        ch = H / zoom; cw = ch * ar
    x0 = min(max(fx * W - cw / 2, 0), W - cw)
    y0 = min(max(fy * H - ch / 2, 0), H - ch)
    out = os.path.join(CROPS, f"{name}.jpg")
    px = max(400, int(w * 240))
    im.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize((px, int(px / ar)), Image.LANCZOS).save(out, quality=88, optimize=True)
    return out


def picture(s, name, src, x, y, w, h, fx=0.5, fy=0.5, zoom=1.0, border=LINE):
    pic = s.shapes.add_picture(cover(name, src, w, h, fx, fy, zoom), Inches(x), Inches(y), Inches(w), Inches(h))
    if border:
        pic.line.color.rgb = rgb(border); pic.line.width = Pt(0.75)
    return pic


def arrow(s, x1, y1, x2, y2, color=SUB, width=1.25, kind=MSO_CONNECTOR.STRAIGHT, head=True):
    c = s.shapes.add_connector(kind, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    if head:
        t = etree.SubElement(c.line._get_or_add_ln(), qn("a:tailEnd"))
        t.set("type", "triangle"); t.set("w", "med"); t.set("len", "med")
    return c


def page_number(s, n):
    for sh in s.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip().isdigit() and (sh.top or 0) > Inches(6.8):
            fill(sh, [[(str(n), "b")]])
            return sh


def clear_notes(s):
    if s.has_notes_slide and s.notes_slide.notes_text_frame is not None:
        s.notes_slide.notes_text_frame.text = ""


for s in SL:
    clear_notes(s)

# ================================================================== 1. TITLE PAGE
s = SL[0]
tb = shp(s, "TextBox 9")
rows = [f"Problem Statement ID – {PS_ID}", f"Problem Statement Title – {PS_TITLE}", f"Theme – {THEME}",
        "PS Category – Software", f"Team ID – {TEAM_ID}", f"Team Name – {TEAM_NAME}", "Solution – PathSense"]
fill(tb, [[(r, "b")] for r in rows])
for p in tb.text_frame.paragraphs:
    pPr = p._p.get_or_add_pPr()
    pPr.set("algn", "l")
    sa = pPr.find(qn("a:spcAft"))
    if sa is not None:
        sa.find(qn("a:spcPts")).set("val", "900")
    for r in p.runs:
        r.font.size = Pt(20)
place(tb, y=1.75)

# ================================================================== 2. PROPOSED SOLUTION
s = SL[1]
page_number(s, 2)
fill(shp(s, "Text 5"), [[("PATHSENSE", "b")]])
fill(shp(s, "Text 6"), [[("Adaptive Path Planning & Collision Avoidance for Unstructured Indian Roads", "b")]])
fill(shp(s, "Text 10"), [
    [("PathSense", "b"), (" is an adaptive autonomous-driving intelligence system for Indian roads where lane markings may be absent and traffic behaviour is irregular.", "n")],
    [("Core idea: ", "b"), ("perceive every road user, understand its motion, reason about the vehicle’s real drivable corridor and keep re-planning a collision-free trajectory.", "n")],
])
fill(shp(s, "Text 14"), [[(a + ": ", "b"), (b, "n")] for a, b in [
    ("Unmarked Roads", "Lane markings are faded, partial or absent, so lane-following assumptions break."),
    ("Mixed Traffic", "Cars, buses, trucks, two-wheelers, auto-rickshaws, cyclists and pedestrians share one space."),
    ("Informal Merges", "Vehicles cut in from either side without signalling or lane discipline."),
    ("Sudden Direction Changes", "Two-wheelers and autos weave, U-turn or stop abruptly."),
    ("Wrong-Way Movement", "Riders and vehicles travel against the flow on the vehicle’s own side."),
    ("Pedestrian / Cyclist Interaction", "People walk along and across the carriageway without crossings."),
    ("Animal Crossings", "Cattle and dogs stand on, or wander into, the road."),
    ("Temporary Obstacles", "Work zones, parked vehicles, handcarts and debris block the usual path."),
]])
cards = [("Lane-Independent Path Planning", "Plans around the actual drivable corridor instead of depending only on painted lane markings."),
         ("Indian Mixed-Traffic Awareness", "Designed for cars, buses, trucks, motorcycles, auto-rickshaws, pedestrians, bicycles and animals."),
         ("Behaviour-Aware Collision Avoidance", "Considers object movement and potential path conflict rather than distance alone."),
         ("Adaptive Replanning", "Continuously evaluates candidate trajectories as the road scene changes."),
         ("Unstructured-Road Operation", "Targets roads where conventional lane-based assumptions are unreliable."),
         ("Safety-First Decision Layer", "Escalates from normal travel to caution, steering, braking and no-safe-path according to path threat.")]
sets = [(16, 17, 18), (19, 20, 21), (22, 23, 24), (25, 26, 27), (28, 29, 30), (31, 32, 33), (34, 35, 36), (79, 80, 81)]
y0, rh, gap = 2.10, 0.555, 0.052
for i, (a, b, c) in enumerate(sets):
    box_, num, body = shp(s, f"Shape {a}"), shp(s, f"Text {b}"), shp(s, f"Text {c}")
    if i >= len(cards):
        for x in (box_, num, body):
            remove(x)
        continue
    y = y0 + i * (rh + gap)
    for x in (box_, num, body):
        place(x, y=y, h=rh)
    fill(num, [[(f"{i + 1:02d}", "b")]])
    fill(body, [(0, [(cards[i][0], "b")]), (1, [(cards[i][1], "n")])])
# "How It Works" panel (replaces the reference infographic): three original concept renders
remove(shp(s, "Image 4"))
HX, HW = 9.99, 2.72
text(s, HX, 1.64, HW, 0.24, "How It Works", size=12, bold=True, color=INK, align=PP_ALIGN.CENTER)
text(s, HX, 1.87, HW, 0.15, "From the camera view to a safe, explainable path", size=6.5, color=SUB, align=PP_ALIGN.CENTER)
steps = [("1", "Perceive & Track", "Every road user, every frame", BLUE, "hw_perceive", "perception", 0.5, 0.52, 1.7),
         ("2", "Predict & Plan", "Candidate paths, conflicts rejected", TEAL, "hw_plan", "planning", 0.5, 0.55, 1.25),
         ("3", "Decide", "GO · SLOW · STEER · BRAKE", ORANGE, "hw_decide", "corridor", 0.48, 0.55, 1.35)]
yy = 2.08
for n, t1, t2, col, nm, src, fx, fy, z in steps:
    rect(s, HX, yy, HW, 0.21, fill=col, radius=0.04)
    rect(s, HX + 0.04, yy + 0.025, 0.16, 0.16, fill="FFFFFF", shape=MSO_SHAPE.OVAL)
    text(s, HX + 0.04, yy + 0.025, 0.16, 0.16, n, size=7, bold=True, color=col, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, HX + 0.25, yy, 1.3, 0.21, t1, size=7.5, bold=True, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
    text(s, HX + 1.35, yy, HW - 1.4, 0.21, t2, size=6, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.RIGHT)
    picture(s, nm, src, HX, yy + 0.23, HW, 0.86, fx, fy, z)
    yy += 1.15
rect(s, HX, yy + 0.02, HW, 0.24, fill=NAVY, radius=0.05)
text(s, HX, yy + 0.02, HW, 0.24, "SEE · PREDICT · PLAN — SAFE PATHS ON EVERY ROAD", size=6.5, bold=True, color="FFFFFF",
     align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
flow = [("Perception", "Detects road users & obstacles"), ("Tracking", "Keeps identities & motion over time"),
        ("Motion Understanding", "Short-term movement & interaction risk"), ("Ego-Path Understanding", "The vehicle’s real drivable corridor"),
        ("Adaptive Planning", "Generates & scores collision-free paths"), ("Decision", "GO / SLOW / STEER / BRAKE / NO SAFE PATH")]
for (t1, t2), n in zip(flow, (62, 65, 68, 71, 74, 77)):
    fill(shp(s, f"TextBox {n}"), [(0, [(t1, "b")]), (1, [(t2, "n")])])

# ================================================================== 3. TECHNICAL APPROACH
s = SL[2]
page_number(s, 3)
fill(shp(s, "Text 6"), [[("PathSense  |  Adaptive Perception-to-Planning Pipeline", "b")]])
remove(shp(s, "Picture 16"))
# -- inputs column
IX, IWc = 0.52, 1.95
rect(s, IX, 1.8, IWc, 0.26, fill=LBLUE)
text(s, IX + 0.08, 1.8, IWc, 0.26, "Inputs / Road Scene", size=9.5, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
ins = [("Front camera video", "Monocular dashcam or phone", "road", 0.5, 0.45, 1.6),
       ("Mixed traffic", "Cars, 2W, autos, people, animals", "market", 0.55, 0.45, 1.5),
       ("Road context", "Unmarked roads, shoulders, junctions", "village", 0.5, 0.45, 1.3),
       ("Ego motion", "From video; CAN / IMU in pilot", None, 0, 0, 0)]
for i, (t1, t2, src, fx, fy, z) in enumerate(ins):
    y = 2.1 + i * 0.66
    rect(s, IX, y, IWc, 0.62, fill="FFFFFF", line=LINE)
    if src:
        picture(s, f"in_{i}", src, IX + 0.04, y + 0.04, 0.72, 0.54, fx, fy, z, border=None)
    else:
        icon(s, "speed", IX + 0.16, y + 0.07, 0.48, "w", circle=BLUE, pad=0.2)
    text(s, IX + 0.82, y + 0.09, 1.1, 0.2, t1, size=8, bold=True, color=NAVY)
    text(s, IX + 0.82, y + 0.29, 1.1, 0.3, t2, size=6.5, color=SUB)
    arrow(s, IX + IWc, y + 0.31, 2.7, y + 0.31, color=GREY, width=0.9)
# -- outputs column
OX = 7.36
rect(s, OX, 1.8, IWc, 0.26, fill=LGREEN)
text(s, OX + 0.08, 1.8, IWc, 0.26, "Outputs / Decisions", size=9.5, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
outs = [("Safe trajectory", "Selected collision-free path", "planning", 0.5, 0.55, 1.4),
        ("Driving decision", "GO · SLOW · STEER · BRAKE · NO SAFE PATH", None, "shield", 0, 0),
        ("Threat alerts", "Which road user, why, how soon", "perception", 0.52, 0.5, 1.8),
        ("Decision log", "Explainable, replayable reasons", None, "timeline", 0, 0)]
for i, (t1, t2, src, fx, fy, z) in enumerate(outs):
    y = 2.1 + i * 0.66
    rect(s, OX, y, IWc, 0.62, fill="FFFFFF", line=LINE)
    if src:
        picture(s, f"out_{i}", src, OX + 0.04, y + 0.04, 0.72, 0.54, fx, fy, z, border=None)
    else:
        icon(s, fx, OX + 0.16, y + 0.07, 0.48, "w", circle=GREEN if i == 1 else PURPLE, pad=0.2)
    text(s, OX + 0.82, y + 0.09, 1.1, 0.2, t1, size=8, bold=True, color=NAVY)
    text(s, OX + 0.82, y + 0.29, 1.1, 0.3, t2, size=6.5, color=SUB)
    arrow(s, 7.14, 4.39, OX, y + 0.31, color=GREY, width=0.9)
# -- runtime block (top centre)
rect(s, 2.66, 1.8, 4.54, 0.5, fill="F7FAFE", line="8FB5DE", dash="dash")
text(s, 2.66, 1.82, 4.54, 0.18, "Runtime (GPU-accelerated inference, open pretrained models)", size=7.5, bold=True, color=NAVY, align=PP_ALIGN.CENTER)
for j, (lg, lab) in enumerate([("python", "Python"), ("pytorch", "PyTorch"), ("nvidia", "CUDA"), ("opencv", "OpenCV")]):
    x = 2.95 + j * 1.08
    logo(s, lg, x, 2.03, 0.2)
    text(s, x + 0.24, 2.03, 0.8, 0.2, lab, size=7.5, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
# -- 9-stage pipeline (snake)
nodes = [("camera", "Camera input", "front view, every frame", BLUE), ("visibility", "Object detection", "vehicles, people, animals", BLUE),
         ("timeline", "Multi-object tracking", "IDs & history", BLUE), ("layers", "Depth & scene", "distance, free space", PURPLE),
         ("brain", "Motion / threat", "who is moving where", PURPLE), ("route", "Ego-path & corridor", "real drivable corridor", PURPLE),
         ("loop", "Candidate trajectories", "kinematic path set", TEAL), ("warning", "Risk-aware selection", "reject conflicts", TEAL),
         ("shield", "Steering / decision", "path + GO…BRAKE", GREEN)]
cols, rows_y, nw, nh = [2.72, 4.22, 5.72], [2.44, 3.26, 4.08], 1.42, 0.62
order = [(0, 0), (0, 1), (0, 2), (1, 2), (1, 1), (1, 0), (2, 0), (2, 1), (2, 2)]
pos = []
for (ic, t1, t2, col), (r, c) in zip(nodes, order):
    x, y = cols[c], rows_y[r]
    pos.append((x, y))
    rect(s, x, y, nw, nh, fill="FFFFFF", line=col, lw=1.1, radius=0.06)
    icon(s, ic, x + 0.07, y + 0.1, 0.34, "w", circle=col, pad=0.2)
    text(s, x + 0.46, y + 0.07, nw - 0.5, 0.3, t1, size=7.5, bold=True, color=NAVY)
    text(s, x + 0.46, y + 0.37, nw - 0.5, 0.22, t2, size=6.3, color=SUB)
for i in range(len(pos) - 1):
    (x1, y1), (x2, y2) = pos[i], pos[i + 1]
    if y1 == y2:
        if x2 > x1:
            arrow(s, x1 + nw, y1 + nh / 2, x2, y2 + nh / 2, color=BLUE, width=1.25)
        else:
            arrow(s, x1, y1 + nh / 2, x2 + nw, y2 + nh / 2, color=BLUE, width=1.25)
    else:
        arrow(s, x1 + nw / 2, y1 + nh, x2 + nw / 2, y2, color=BLUE, width=1.25)
# -- safety layer bar
rect(s, 3.05, 4.84, 3.8, 0.36, fill="FFFFFF", line=NAVY, lw=1.0)
icon(s, "shield", 3.12, 4.88, 0.28, "n")
text(s, 3.45, 4.84, 3.35, 0.2, "Safety-First Decision Layer", size=8.5, bold=True, color=NAVY)
text(s, 3.45, 5.02, 3.35, 0.16, "path-threat reasoning  |  persistence & hysteresis  |  reasons", size=6.3, color=SUB)
arrow(s, 4.95, 4.72, 4.95, 4.84, color=NAVY, width=1.0)
# -- operating environments (bottom row)
envs = [("Urban dense traffic", "Crowds, vendors, turning buses", "market", 0.5, 0.45, 1.25, ORANGE, "bus"),
        ("Village roads", "Narrow, unmarked, cattle, tractors", "village", 0.62, 0.72, 1.6, GREEN, "cow"),
        ("Highways & transitions", "Merges, trucks, higher speeds", "village", 0.35, 0.22, 1.7, BLUE, "truck"),
        ("Unmarked city roads", "Work zones, wrong-way riders", "road", 0.5, 0.5, 1.4, PURPLE, "route")]
line_y = 5.3
rect(s, 1.6, line_y - 0.005, 6.65, 0.01, fill=GREY)
arrow(s, 4.95, 5.2, 4.95, line_y, color=GREY, width=0.9, head=False)
for i, (t1, t2, src, fx, fy, z, col, ic) in enumerate(envs):
    x = 0.55 + i * 2.2
    arrow(s, x + 1.06, line_y, x + 1.06, 5.4, color=GREY, width=0.9)
    rect(s, x, 5.4, 2.12, 1.26, fill="F8FAFD", line=LINE)
    icon(s, ic, x + 0.07, 5.45, 0.2, "n")
    text(s, x + 0.32, 5.44, 1.8, 0.22, t1, size=8, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
    picture(s, f"env_{i}", src, x + 0.06, 5.68, 2.0, 0.72, fx, fy, z, border=None)
    text(s, x + 0.08, 6.43, 1.98, 0.2, "• " + t2, size=6.5, color=SUB)
# -- TECH STACK (replaces the reference image)
remove(shp(s, "Image 2"))
text(s, 9.78, 1.25, 3.0, 0.3, "TECH STACK", size=13, bold=True, color=INK)
rect(s, 9.78, 1.55, 3.08, 0.012, fill="9AA4B2")
cells = [("PERCEPTION", BLUE, [("ultralytics", "YOLO"), ("opencv", "OpenCV")], "Object detection"),
         ("TRACKING", GREEN, [("icon:timeline", "ByteTrack")], "Multi-object tracking"),
         ("DEPTH / VISION", PURPLE, [("icon:layers", "Depth Anything"), ("icon:visibility", "CLIP")], "Depth & scene cues"),
         ("PLANNING", ORANGE, [("python", "Python"), ("numpy", "NumPy")], "Trajectory & risk logic"),
         ("WEB / DEMO", RED, [("flask", "Flask"), ("html", "HTML"), ("css", "CSS"), ("javascript", "JS")], "Review dashboard"),
         ("RUNTIME", "2C5F7C", [("pytorch", "PyTorch"), ("nvidia", "CUDA")], "GPU inference")]
cw_, ch_ = 1.0, 0.97
for i, (title, col, items, cap) in enumerate(cells):
    x, y = 9.76 + (i % 3) * (cw_ + 0.04), 1.63 + (i // 3) * (ch_ + 0.05)
    rect(s, x, y, cw_, ch_, fill="FFFFFF", line=LINE)
    rect(s, x, y, cw_, 0.19, fill=col)
    text(s, x, y, cw_, 0.19, title, size=6.3, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    n = len(items)
    for j, (lg, lab) in enumerate(items):
        if n <= 2:
            lx, ly, ls = x + 0.08, y + 0.26 + j * 0.28, 0.22
        else:
            lx, ly, ls = x + 0.05 + (j % 2) * 0.48, y + 0.26 + (j // 2) * 0.28, 0.18
        if lg.startswith("icon:"):
            icon(s, lg[5:], lx, ly, ls, {BLUE: "b", GREEN: "g", PURPLE: "n"}.get(col, "n"))
        else:
            logo(s, lg, lx, ly, ls)
        text(s, lx + ls + 0.04, ly, (0.9 if n <= 2 else 0.3) - 0.02, ls, lab, size=6.5 if n <= 2 else 5.5, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.04, y + ch_ - 0.2, cw_ - 0.08, 0.16, cap, size=5.5, color=SUB, align=PP_ALIGN.CENTER)
# -- METHODOLOGY cycle (replaces the reference image)
remove(shp(s, "Image 3"))
text(s, 9.7, 3.88, 3.24, 0.28, "METHODOLOGY", size=12, bold=True, color="1F3E7A", underline=True, align=PP_ALIGN.CENTER)
import math
cx, cy, rx, ry, r0 = 11.32, 5.38, 1.12, 0.86, 0.22
circ = [("camera", "Sense", BLUE), ("visibility", "Detect & track", GREEN), ("brain", "Predict motion", ORANGE),
        ("warning", "Assess path threat", PURPLE), ("route", "Plan trajectory", TEAL), ("loop", "Act & re-plan", RED)]
rect(s, cx - rx, cy - ry, 2 * rx, 2 * ry, fill=None, line="B8C4D4", lw=1.0, dash="dash", shape=MSO_SHAPE.OVAL)
for k, (ic, lab, col) in enumerate(circ):
    a = -math.pi / 2 + k * 2 * math.pi / len(circ)
    px, py = cx + rx * math.cos(a), cy + ry * math.sin(a)
    icon(s, ic, px - r0, py - r0, 2 * r0, "w", circle=col, pad=0.22)
    rect(s, px - r0 - 0.04, py - r0 - 0.04, 0.15, 0.15, fill=col, line="FFFFFF", lw=0.75, shape=MSO_SHAPE.OVAL)
    text(s, px - r0 - 0.04, py - r0 - 0.04, 0.15, 0.15, str(k + 1), size=5.5, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    ly = py + r0 + 0.01 if math.sin(a) >= -0.1 else py - r0 - 0.17
    text(s, px - 0.55, ly, 1.1, 0.16, lab, size=6, bold=True, color=NAVY, align=PP_ALIGN.CENTER)
text(s, cx - 0.62, cy - 0.2, 1.24, 0.22, "PATHSENSE", size=10.5, bold=True, color="1F3E7A", align=PP_ALIGN.CENTER)
text(s, cx - 0.62, cy + 0.02, 1.24, 0.3, "Continuous perception-to-planning loop", size=5.8, color=SUB, align=PP_ALIGN.CENTER)

# ================================================================== 4. PROTOTYPE (concept views - no screenshots)
s = SL[3]
remove(shp(s, "Login Wireframe Border"))
for n in ("Login Wireframe", "Home Wireframe", "Login Final", "Home Final"):
    remove(shp(s, n))
fill(shp(s, "Heading Wireframes"), [[("Concept Views", "b")]])
fill(shp(s, "Caption Login Final"), [[("Perception View", "b")]])
fill(shp(s, "Caption Home Wireframe"), [[("Planning View", "b")]])
fill(shp(s, "Heading Expected Delivery"), [[("Before vs After", "b")]])
place(shp(s, "Heading Wireframes"), x=0.46, y=1.2)
place(shp(s, "Caption Login Final"), x=2.6, y=1.3, w=2.5)
place(shp(s, "Caption Home Wireframe"), x=6.6, y=1.3, w=2.5)
place(shp(s, "Heading Expected Delivery"), x=0.46, y=3.88)
picture(s, "p_perception", "perception", 0.46, 1.62, 4.6, 2.1, 0.5, 0.55, 1.35)
picture(s, "p_planning", "planning", 5.3, 1.62, 4.6, 2.1, 0.5, 0.5, 1.2)
text(s, 0.52, 3.74, 4.5, 0.18, "Road users detected as 3D boxes; predicted motion as dashed arrows", size=7.5, color=SUB, italic=True)
text(s, 5.36, 3.74, 4.5, 0.18, "Candidate paths fanned out; conflicting paths (red) rejected; safe path (teal) kept", size=7.5, color=SUB, italic=True)
picture(s, "p_before", "before", 0.46, 4.3, 4.6, 2.45, 0.45, 0.55, 1.2)
picture(s, "p_after", "corridor", 5.3, 4.3, 4.6, 2.45, 0.45, 0.55, 1.2)
arrow(s, 5.08, 5.52, 5.28, 5.52, color=NAVY, width=2)
for x, lab, col in [(0.56, "BEFORE  ·  lane-following assumption: path runs into the work zone", RED),
                    (5.4, "AFTER  ·  PathSense adapts the corridor around the obstacle", TEAL)]:
    rect(s, x, 4.38, 4.4, 0.26, fill="FFFFFF", line=col, lw=1.0, radius=0.06)
    text(s, x, 4.38, 4.4, 0.26, lab, size=7.5, bold=True, color=col, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
cd = shp(s, "Current Development Box")
place(cd, x=10.2, y=1.62, w=3.0, h=2.6)
cd.text_frame.word_wrap = True
cd.text_frame.clear()
tf = cd.text_frame
paras = [[("Current Development", {"bold": True, "size": 18})],
         [("Working prototype: ", {"bold": True, "size": 11}), ("camera-based perception, tracking, motion understanding and adaptive path planning", {"size": 11})],
         [("Code repository:", {"bold": True, "size": 11})],
         [("github.com/MalyalaKarthik66/pathsense-prototype", {"size": 10, "url": REPO, "underline": True, "color": "1155CC"})],
         [("Live demonstration shown separately during evaluation.", {"size": 10, "italic": True, "color": SUB})]]
for i, para in enumerate(paras):
    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
    p.space_after = Pt(6)
    for t, o in para:
        r = p.add_run(); r.text = t
        r.font.name = "Arial"; r.font.size = Pt(o.get("size", 11)); r.font.bold = o.get("bold", False)
        r.font.italic = o.get("italic", False); r.font.underline = o.get("underline", False)
        r.font.color.rgb = rgb(o.get("color", INK))
        if o.get("url"):
            r.hyperlink.address = o["url"]
rect(s, 10.2, 4.35, 2.95, 1.8, fill="EAF2FB", line="8FB5DE")
text(s, 10.34, 4.43, 2.7, 0.26, "What the prototype demonstrates", size=10, bold=True, color=NAVY)
text(s, 10.34, 4.75, 2.72, 1.35, ["Detection & tracking of mixed Indian road users", "Path-threat reasoning: oncoming vs. entering the path",
     "Adaptive candidate-path planning around obstacles", "Explainable decisions with replay"], size=8.5, color=INK, bullets=True, space=3)
text(s, 10.2, 6.3, 3.0, 0.4, "Visuals on this slide are original concept renders, not application screenshots.", size=7, color=GREY, italic=True)
# page number like the other slides
num = deepcopy(shp(SL[2], "Text 4")._element)
s.shapes._spTree.append(num)
page_number(s, 4)

# ================================================================== 5. FEASIBILITY AND VIABILITY
s = SL[4]
page_number(s, 5)
fill(shp(s, "Text 6"), [[("RISKS → MITIGATION", "b")]])
fill(shp(s, "Text 7"), [[(a + " → ", "b"), (b, "n")] for a, b in [
    ("Unstructured road geometry", "Drivable-corridor estimation and path-aware planning."),
    ("Unpredictable road-user behaviour", "Temporal tracking and short-term motion reasoning."),
    ("Perception uncertainty", "Confidence-aware decisions and future sensor fusion."),
    ("Real-world validation complexity", "Scenario library + simulation + controlled field testing.")]])
fill(shp(s, "Text 8"), [[("FEASIBILITY", "b")]])
fill(shp(s, "Text 9"), [[(a, "b"), (b, "n")] for a, b in [
    ("Technology: ", "Established computer-vision and motion-planning building blocks; camera-based perception to start."),
    ("Architecture: ", "Modular perception → prediction → planning; radar / LiDAR and vehicle-state inputs added progressively; scenario-based simulation supports development.")]])
fill(shp(s, "Text 10"), [[("VIABILITY", "b")]])
fill(shp(s, "Text 11"), [[(a, "b"), (b, "n")] for a, b in [
    ("Relevance: ", "Mixed, irregular traffic on urban roads, village roads, highways and dense traffic."),
    ("Deployment: ", "Modular architecture supports incremental deployment through controlled pilots."),
    ("Future fit: ", "Can integrate with future intelligent-vehicle platforms.")]])
remove(shp(s, "Picture 17"))
remove(shp(s, "Picture 18"))
stages = [("PROTOTYPE", 7.85, 1.26, "Camera-based proof of concept", " for perception, tracking and adaptive path planning."),
          ("PILOT", 9.62, 2.42, "Multi-scenario validation", " with richer sensing, simulation and controlled road testing."),
          ("PRODUCTION", 7.72, 3.58, "Vehicle-grade multi-sensor integration", ", closed-loop validation and automotive safety engineering.")]
for t1, x, y, b1, b2 in stages:
    rect(s, x, y, 3.3, 1.0, fill=BEIGE)
    text(s, x + 0.12, y + 0.05, 3.1, 0.42, t1, size=22, bold=True, color=INK, font="Calibri")
    text(s, x + 0.14, y + 0.48, 3.08, 0.5, [[("●  ", {"size": 8}), (b1, {"bold": True}), (b2, {})]], size=10.5, color=INK, font="Calibri")
arrow(s, 11.25, 1.55, 11.95, 2.38, color=INK, width=3, kind=MSO_CONNECTOR.CURVE)
arrow(s, 9.55, 2.8, 9.0, 3.54, color=INK, width=3, kind=MSO_CONNECTOR.CURVE)
text(s, 7.75, 4.78, 5.23, 0.32, "Potential Collaborators / Partners", size=15, bold=True, color=INK, font="Cambria",
     align=PP_ALIGN.CENTER, underline=True)
partners = [("car", "Automotive OEMs & Tier-1s", "ADAS / AV integration"), ("science", "Research institutes", "IITs, IISc, AV labs"),
            ("layers", "Simulation partners", "e.g. MathWorks toolchain"), ("shield", "Road-safety bodies", "MoRTH, state transport"),
            ("bus", "Fleet operators", "Buses, taxis, logistics"), ("map", "Mapping & data", "OpenStreetMap, IDD")]
for i, (ic, t1, t2) in enumerate(partners):
    x, y = 7.8 + (i % 3) * 1.73, 5.2 + (i // 3) * 0.8
    rect(s, x, y, 1.66, 0.72, fill="FFFFFF", line=LINE, radius=0.06)
    icon(s, ic, x + 0.08, y + 0.17, 0.38, "w", circle=[BLUE, GREEN, PURPLE, RED, ORANGE, TEAL][i], pad=0.2)
    text(s, x + 0.52, y + 0.12, 1.1, 0.3, t1, size=7.5, bold=True, color=NAVY)
    text(s, x + 0.52, y + 0.44, 1.1, 0.2, t2, size=6.3, color=SUB)

# ================================================================== 6. PROOF OF CONCEPT, IMPACT & BENEFITS
s = SL[5]
page_number(s, 6)
fill(shp(s, "Text 109"), [[(a + ": ", "b"), (b, "n")] for a, b in [
    ("Drivers / Passengers", "Improved handling of unpredictable road interactions"),
    ("Urban Mobility", "More adaptive navigation in dense mixed traffic"),
    ("Rural Mobility", "Better operation on roads with limited lane structure"),
    ("Road Safety", "Earlier recognition of path conflicts and adaptive response"),
    ("Future Autonomous Vehicles", "A foundation for Indian-road-aware navigation")]])
for lab_shape, txt_shape, lab, pts in [
        ("Text 117", "Text 118", "SAFETY", ["Path-aware collision avoidance", "Earlier recognition of path conflicts"]),
        ("Text 120", "Text 121", "ADAPTABILITY", ["Handles changing road and traffic conditions", "Works where lane markings are unreliable"]),
        ("Text 123", "Text 124", "SCALABILITY", ["Can evolve from prototype → pilot → production", "Modular: new sensors and platforms plug in"]),
        ("Text 126", "Text 127", "INDIAN-ROAD RELEVANCE", ["Designed around mixed and irregular road behaviour", "Safer shared roads for pedestrians and two-wheelers"])]:
    fill(shp(s, lab_shape), [[(lab, "b")]])
    fill(shp(s, txt_shape), [[(p, "n")] for p in pts])
fill(shp(s, "Text 129"), [[("PoC", "b", {"sz": 8})]])
poc = [("PROBLEM", "Unstructured Indian roads create difficult path-planning conditions"),
       ("OBJECTIVE", "Safe adaptive trajectories without relying solely on lane markings"),
       ("PROTOTYPE", "Camera-based perception, tracking, motion understanding and adaptive planning"),
       ("VALIDATION", "Scenario-based evaluation across mixed-traffic road situations"),
       ("SUCCESS CRITERIA", "Safe path selection, appropriate risk response, robust behaviour across roads"),
       ("NEXT STEPS", "Sensor fusion, closed-loop simulation, broader field trials, vehicle integration")]
for (t1, t2), n in zip(poc, (134, 139, 144, 149, 154, 159)):
    fill(shp(s, f"Text {n}"), [(0, [(t1, "b")]), (1, [(t2, "n")])])
fill(shp(s, "Text 161"), [[("Resources: ", "b"), ("computer vision & motion planning, road-scene video, simulation tools, compute hardware", "n")]])

# ================================================================== 7. BUSINESS MODEL CANVAS
s = SL[6]
page_number(s, 7)
bmc = {
    "Text 8": [("OEMs:", " Automotive OEMs & Tier-1 ADAS suppliers"), ("ACADEMIA:", " IITs, IISc & autonomy research labs"),
               ("SIMULATION:", " MathWorks toolchain (RoadRunner, Automated Driving Toolbox)"), ("DATA:", " Indian driving datasets (IDD), OpenStreetMap"),
               ("GOVT:", " MoRTH, state transport & road-safety bodies"), ("FLEETS:", " Logistics, taxi & public-transport operators")],
    "Text 11": [("PERCEPTION R&D:", " Detection, tracking & depth"), ("PLANNING:", " Adaptive trajectory & risk logic"),
                ("VALIDATION:", " Scenario library & simulation"), ("INTEGRATION:", " Vehicle / ADAS interfaces")],
    "Text 14": [("SOFTWARE STACK:", " Perception-to-planning pipeline"), ("DATA:", " Indian-road scenario library"),
                ("TEAM:", " CV, robotics & software engineers"), ("TOOLS:", " Simulation & compute infrastructure")],
    "Text 17": [("OEMs:", " An Indian-road-aware planning layer"), ("FLEETS:", " Fewer conflicts in mixed traffic"),
                ("ROAD USERS:", " Earlier recognition of path conflicts"), ("RESEARCHERS:", " Scenario-based validation workflow"),
                ("EXPLAINABLE:", " Every decision comes with its reason"), ("MODULAR:", " Camera-first, sensor-ready architecture")],
    "Text 20": [("CO-DEVELOPMENT:", " Pilot programmes with OEMs"), ("SUPPORT:", " Integration & tuning"),
                ("UPDATES:", " Continuous model & scenario updates"), ("TRANSPARENCY:", " Decision logs for audit")],
    "Text 23": [("DIRECT B2B:", " OEM & Tier-1 partnerships"), ("PILOTS:", " Fleet & campus deployments"),
                ("RESEARCH:", " Academic collaborations"), ("SDK / API:", " Licensable software modules")],
    "Text 26": [("PRIMARY:", " Automotive OEMs & ADAS suppliers"), ("SECONDARY:", " Fleet & logistics operators"),
                ("PUBLIC:", " Transport & road-safety agencies"), ("RESEARCH:", " AV labs & testing bodies"),
                ("SCALE-UP:", " City → state → national deployments")],
    "Text 31": [("R&D", " – perception & planning engineering"), ("Data", " – collection, annotation & scenario building"),
                ("Compute", " – training & simulation infrastructure"), ("Validation", " – field testing & safety engineering"),
                ("Operations", " – integration, support & updates")],
    "Text 36": [("Software licensing", " – per-vehicle / per-platform licences for OEMs"), ("Pilot contracts", " – fleet & campus deployments"),
                ("Scenario-library access", " – validation datasets & tools"), ("Integration services", " – customisation & calibration"),
                ("Support & updates", " – annual maintenance subscriptions")],
}
for name, items in bmc.items():
    fill(shp(s, name), [[(a, "b"), (b, "n")] for a, b in items])

# ================================================================== 8. RESEARCH AND REFERENCES
s = SL[7]
page_number(s, 8)
fill(shp(s, "Text 5"), [[("NATIONAL ALIGNMENT", "b")]])
papers = {
    "Text 8": ("AUTONOMOUS DRIVING & PATH PLANNING", "“A Survey of Motion Planning and Control Techniques for Self-Driving Urban Vehicles”",
               "  Paden et al. — IEEE T-IV, 2016", "arxiv.org/abs/1604.07446"),
    "Text 9": ("OBJECT DETECTION & TRACKING", "“ByteTrack: Multi-Object Tracking by Associating Every Detection Box”",
               "  Zhang et al. — ECCV, 2022", "arxiv.org/abs/2110.06864"),
    "Text 10": ("DEPTH ESTIMATION", "“Depth Anything V2”", "  Yang et al. — NeurIPS, 2024", "arxiv.org/abs/2406.09414"),
    "Text 11": ("MOTION PREDICTION", "“Social LSTM: Human Trajectory Prediction in Crowded Spaces”",
                "  Alahi et al. — CVPR, 2016", "openaccess.thecvf.com"),
}
for name, (cat, title, who, url) in papers.items():
    fill(shp(s, name), [(0, [(cat, "b")]), (1, [(title, "i"), (who, "n")]), (1, [(url, "l", {"url": "https://" + url})])])
fill(shp(s, "Text 13"), [[("INDIAN ROAD DATA", "b")]])
fill(shp(s, "Text 16"), [[("SIH & CONTEXT", "b")]])
fill(shp(s, "Text 19"), [[("TECHNICAL DOCS", "b")]])


def link_list(items):
    out = []
    for name, url in items:
        out.append((0, [(name, "b")]))
        out.append((1, [(url, "l", {"url": "https://" + url.split(" ")[0]})]))
    return out


fill(shp(s, "Text 14"), link_list([("IDD – Indian Driving Dataset", "idd.insaan.iiit.ac.in"), ("MoRTH – Road Accidents in India", "morth.nic.in"),
                                   ("OpenStreetMap", "openstreetmap.org"), ("Wikimedia Commons", "commons.wikimedia.org")]))
fill(shp(s, "Text 17"), link_list([("SIH 2026 – PS SIH26037", "sih.gov.in"), ("MathWorks – RoadRunner & ADT", "mathworks.com")]) +
     [(0, [("Problem focus: unmarked roads, mixed traffic, informal merges, obstacles.", "n")])])
fill(shp(s, "Text 20"), link_list([("Ultralytics YOLO", "docs.ultralytics.com"), ("PyTorch", "pytorch.org/docs"),
                                   ("OpenCV", "docs.opencv.org"), ("Flask", "flask.palletsprojects.com")]))
fill(shp(s, "Text 21"), [[("Sources: ", "b"), ("IEEE T-IV · ECCV · NeurIPS · CVPR · IDD (IIIT-H) · MoRTH · OpenStreetMap · SIH 2026 · Ultralytics · PyTorch · OpenCV", "i")]])
for n in range(1, 7):
    remove(next(x for x in s.shapes if x.name == f"Image {n}"))
tiles = [("Road Safety", "MoRTH road-safety goals", "Earlier recognition of path conflicts on Indian roads", RED),
         ("IndiaAI Mission", "AI for Indian conditions", "Perception and planning built for Indian traffic", BLUE),
         ("Atmanirbhar Bharat", "Self-reliant technology", "Home-grown autonomous-driving intelligence", "E8731A"),
         ("Make in India", "Indigenous automotive tech", "ADAS software developed for Indian vehicles", GREEN),
         ("Smart Cities Mission", "Intelligent urban mobility", "Adaptive navigation in dense city traffic", PURPLE),
         ("Digital India", "Software-first innovation", "Open, modular and explainable mobility software", TEAL)]
for i, (t1, t2, t3, col) in enumerate(tiles):
    x, y = 7.02 + (i % 2) * 2.96, 1.55 + (i // 2) * 1.47
    rect(s, x, y, 2.8, 1.33, fill="FFFFFF", line="DDE3EA", radius=0.06)
    rect(s, x + 0.18, y + 0.2, 0.08, 0.5, fill=col)
    text(s, x + 0.36, y + 0.16, 2.35, 0.36, t1, size=15, bold=True, color=col)
    text(s, x + 0.36, y + 0.52, 2.35, 0.2, t2, size=8.5, bold=True, color=NAVY)
    text(s, x + 0.18, y + 0.84, 2.5, 0.4, t3, size=8, color=SUB)

# drop relationships to pictures that were removed (reference-deck images must not stay inside the package)
for s in SL:
    xml = etree.tostring(s._element).decode()
    for rId, rel in list(s.part.rels.items()):
        if rel.reltype == RT.IMAGE and f'"{rId}"' not in xml:
            s.part.drop_rel(rId)

dst = os.path.join(ROOT, "PathSense_SIH_Presentation.pptx")
prs.save(dst)
print("saved", dst, len(prs.slides), "slides")
