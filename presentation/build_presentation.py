"""
Builds PathSense_SIH_Presentation.pptx (8 slides).

    python presentation/build_presentation.py  [path\to\finalforsih.pptx]

Structure follows the SIH 2026 idea-presentation format:
  1 Title page · 2 Proposed solution · 3 Technical approach · 4 Prototype · 5 Feasibility and viability ·
  6 Proof of concept, impact & benefits · 7 Business model canvas · 8 Research and references.

Slide 1 is the approved SIH title page (taken from the team's earlier deck with PathSense details filled in) and is
kept exactly as it is. Slides 2-8 are built from blank slides in PathSense's own design system.
Pictures: the four Prototype-slide images are real screenshots of the running web app
(presentation/capture_screens.mjs); the slide-2 "How It Works" images are original concept renders
(presentation/hero/render_hero.mjs); icons and technology logos come from make_icons.js / make_logos.js.
No prototype measurements are quoted on the slides.
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

# ================================================================== slides 2-8: PathSense's own design system
# Slide 1 above is the approved title page and is left exactly as it is. The reference deck's slides 2-8 are removed
# and rebuilt from blank slides in an independent layout: navy header band, modular cards, section chips,
# timelines and tables.
for idx in range(len(prs.slides) - 1, 0, -1):
    sld = prs.slides._sldIdLst[idx]
    prs.part.drop_rel(sld.rId)
    prs.slides._sldIdLst.remove(sld)

DNAVY, LIGHT, TEAL_T, AMBER, AMBER_T, CORAL = "12263F", "F2F4F7", "E3F4F1", "E8A33D", "FDF3E1", "D9534F"
BLANK = prs.slide_layouts[0]                      # "DEFAULT": no placeholders
SIH_LOGO = os.path.join(ASSETS, "sih_logo.png")   # extracted from the SIH title-page artwork
SCREENS = os.path.join(ASSETS, "screens")
TOTAL = 8


def new_slide(n, title, subtitle=None):
    s = prs.slides.add_slide(BLANK)
    rect(s, 0, 0, 13.333, 1.05, fill=DNAVY)
    rect(s, 0.32, 0.3, 1.25, 0.46, fill=TEAL, radius=0.23)
    text(s, 0.32, 0.3, 1.25, 0.46, TEAM_NAME, size=10, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 1.8, 0.13, 8.5, 0.2, f"SMART INDIA HACKATHON 2026   ·   {PS_ID}   ·   PATHSENSE", size=7.5, bold=True, color="7FD6CB")
    text(s, 1.8, 0.31, 9.0, 0.46, title, size=24, bold=True, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
    if subtitle:
        text(s, 1.8, 0.75, 9.0, 0.24, subtitle, size=10.5, color="C9D6E8")
    rect(s, 11.2, 0.13, 1.8, 0.8, fill="FFFFFF", radius=0.08)
    s.shapes.add_picture(SIH_LOGO, Inches(11.32), Inches(0.165), Inches(1.56), Inches(1.56 * 661 / 1400))
    text(s, 0.4, 7.1, 6.0, 0.22, "@SIH Idea submission- Template", size=8, color=SUB, anchor=MSO_ANCHOR.MIDDLE)
    rect(s, 12.38, 7.07, 0.55, 0.28, fill=DNAVY, radius=0.06)
    text(s, 12.38, 7.07, 0.55, 0.28, f"{n:02d}", size=9, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    return s


def chip(s, x, y, label, color=TEAL, w=None):
    w = w or (0.3 + 0.07 * len(label))
    rect(s, x, y, w, 0.25, fill=color, radius=0.06)
    text(s, x, y, w, 0.25, label, size=8, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    return w


def card(s, x, y, w, h, fill=LIGHT, line=None):
    return rect(s, x, y, w, h, fill=fill, line=line, radius=0.07)


def screen(name, w, h, fy=0.0):
    """crop a real screenshot to the frame's aspect ratio, keeping the top of the page (no drawing, no edits)"""
    im = Image.open(os.path.join(SCREENS, f"{name}.png")).convert("RGB")
    W, H = im.size
    ar = w / h
    cw, ch = W, W / ar
    if ch > H:
        ch, cw = H, H * ar
    y0 = (H - ch) * fy
    x0 = (W - cw) / 2
    out = os.path.join(CROPS, f"screen_{name}.jpg")
    im.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize((int(w * 300), int(w * 300 / ar)), Image.LANCZOS).save(out, quality=90, optimize=True)
    return out


# ================================================================== 2. PROPOSED SOLUTION
s = new_slide(2, "PATHSENSE", "Adaptive Path Planning & Collision Avoidance for Unstructured Indian Roads")
L = 0.4
chip(s, L, 1.22, "PROPOSED SOLUTION", DNAVY)
text(s, L, 1.54, 8.45, 0.62, [
    [("PathSense", {"bold": True}), (" is an adaptive autonomous-driving intelligence system for Indian roads where lane markings may be absent and traffic behaviour is irregular.", {})],
    [("Core idea: ", {"bold": True}), ("perceive every road user, understand its motion, reason about the vehicle’s real drivable corridor and keep re-planning a collision-free trajectory.", {})]],
    size=9, color=INK, space=2)
flow = [("Perception", "Detects road users & obstacles"), ("Tracking", "Keeps identities & motion over time"),
        ("Motion Understanding", "Short-term movement & interaction risk"), ("Ego-Path Understanding", "The vehicle’s real drivable corridor"),
        ("Adaptive Planning", "Generates & scores collision-free paths"), ("Decision", "GO / SLOW / STEER / BRAKE / NO SAFE PATH")]
shades = ["1F3A5F", "1B4F6E", "17657C", "137A87", "0F8E8B", "0F9E8E"]
cw = 1.46
for i, ((t1, t2), col) in enumerate(zip(flow, shades)):
    x = L + i * (cw - 0.06)
    sh = s.shapes.add_shape(MSO_SHAPE.CHEVRON if i else MSO_SHAPE.PENTAGON, Inches(x), Inches(2.24), Inches(cw), Inches(0.4))
    sh.fill.solid(); sh.fill.fore_color.rgb = rgb(col); sh.line.fill.background(); sh.shadow.inherit = False
    text(s, x + (0.2 if i else 0.08), 2.24, cw - 0.36, 0.4, t1, size=7.3, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.08, 2.67, cw - 0.16, 0.3, t2, size=6.5, color=SUB, align=PP_ALIGN.CENTER)
chip(s, L, 3.07, "PROBLEMS IT ADDRESSES", CORAL)
probs = [("route", "Unmarked Roads", "Lane markings are faded, partial or absent, so lane-following assumptions break."),
         ("car", "Mixed Traffic", "Cars, buses, trucks, two-wheelers, auto-rickshaws, cyclists and pedestrians share one space."),
         ("merge", "Informal Merges", "Vehicles cut in from either side without signalling or lane discipline."),
         ("moto", "Sudden Direction Changes", "Two-wheelers and autos weave, U-turn or stop abruptly."),
         ("block", "Wrong-Way Movement", "Riders and vehicles travel against the flow on the vehicle’s own side."),
         ("walk", "Pedestrian / Cyclist Interaction", "People walk along and across the carriageway without crossings."),
         ("cow", "Animal Crossings", "Cattle and dogs stand on, or wander into, the road."),
         ("warning", "Temporary Obstacles", "Work zones, parked vehicles, handcarts and debris block the usual path.")]
for i, (ic, t1, t2) in enumerate(probs):
    x, y = L + (i % 4) * 2.13, 3.38 + (i // 4) * 0.84
    card(s, x, y, 2.05, 0.77)
    icon(s, ic, x + 0.08, y + 0.08, 0.26, "w", circle=CORAL, pad=0.2)
    text(s, x + 0.4, y + 0.08, 1.6, 0.26, t1, size=8, bold=True, color=DNAVY, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.1, y + 0.38, 1.9, 0.38, t2, size=6.8, color=SUB)
chip(s, L, 5.09, "DIFFERENTIATION & KEY VALUE PROPOSITION", TEAL)
cards = [("Lane-Independent Path Planning", "Plans around the actual drivable corridor instead of depending only on painted lane markings."),
         ("Indian Mixed-Traffic Awareness", "Designed for cars, buses, trucks, motorcycles, auto-rickshaws, pedestrians, bicycles and animals."),
         ("Behaviour-Aware Collision Avoidance", "Considers object movement and potential path conflict rather than distance alone."),
         ("Adaptive Replanning", "Continuously evaluates candidate trajectories as the road scene changes."),
         ("Unstructured-Road Operation", "Targets roads where conventional lane-based assumptions are unreliable."),
         ("Safety-First Decision Layer", "Escalates from normal travel to caution, steering, braking and no-safe-path according to path threat.")]
for i, (t1, t2) in enumerate(cards):
    x, y = L + (i % 3) * 2.84, 5.4 + (i // 3) * 0.8
    card(s, x, y, 2.76, 0.73, fill="FFFFFF", line="D5DDE8")
    text(s, x + 0.08, y + 0.08, 0.45, 0.4, f"{i + 1:02d}", size=16, bold=True, color=TEAL)
    text(s, x + 0.55, y + 0.07, 2.15, 0.22, t1, size=8, bold=True, color=DNAVY)
    text(s, x + 0.55, y + 0.3, 2.15, 0.42, t2, size=6.8, color=SUB)
# How It Works (the approved images are kept exactly as they were)
HX, HY, HW = 9.1, 1.22, 3.83
card(s, HX, HY, HW, 5.73, fill=DNAVY)
text(s, HX + 0.15, HY + 0.12, HW - 0.3, 0.3, "How It Works", size=14, bold=True, color="FFFFFF")
text(s, HX + 0.15, HY + 0.42, HW - 0.3, 0.2, "From the camera view to a safe, explainable path", size=8, color="C9D6E8")
steps = [("1", "Perceive & Track", "Every road user, every frame", "hw_perceive"),
         ("2", "Predict & Plan", "Candidate paths, conflicts rejected", "hw_plan"),
         ("3", "Decide", "GO · SLOW · STEER · BRAKE", "hw_decide")]
iw = HW - 0.3
ih = iw * 0.86 / 2.72
yy = HY + 0.75
for n, t1, t2, nm in steps:
    rect(s, HX + 0.15, yy + 0.02, 0.22, 0.22, fill=TEAL, shape=MSO_SHAPE.OVAL)
    text(s, HX + 0.15, yy + 0.02, 0.22, 0.22, n, size=8, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, HX + 0.45, yy, 1.5, 0.26, t1, size=9, bold=True, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
    text(s, HX + 1.8, yy, iw - 1.65, 0.26, t2, size=7, color="9FE3D9", anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.RIGHT)
    s.shapes.add_picture(os.path.join(CROPS, f"{nm}.jpg"), Inches(HX + 0.15), Inches(yy + 0.3), Inches(iw), Inches(ih))
    yy += 0.3 + ih + 0.14
rect(s, HX + 0.15, 6.52, iw, 0.3, fill=TEAL, radius=0.06)
text(s, HX + 0.15, 6.52, iw, 0.3, "SEE · PREDICT · PLAN — SAFE PATHS ON EVERY ROAD", size=7.5, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

# ================================================================== 3. TECHNICAL APPROACH
s = new_slide(3, "TECHNICAL APPROACH", "PathSense  |  Adaptive Perception-to-Planning Pipeline")
chip(s, L, 1.22, "INPUTS / ROAD SCENE", DNAVY)
ins = [("camera", "Front camera video", "Monocular dashcam or phone"), ("car", "Mixed traffic", "Cars, 2W, autos, people, animals"),
       ("map", "Road context", "Unmarked roads, shoulders, junctions"), ("speed", "Ego motion", "From video; CAN / IMU in pilot")]
for i, (ic, t1, t2) in enumerate(ins):
    x = L + i * 2.16
    card(s, x, 1.53, 2.08, 0.52)
    icon(s, ic, x + 0.08, 1.6, 0.36, "w", circle=DNAVY, pad=0.2)
    text(s, x + 0.52, 1.57, 1.5, 0.22, t1, size=8, bold=True, color=DNAVY)
    text(s, x + 0.52, 1.79, 1.52, 0.22, t2, size=6.8, color=SUB)
phases = [("1 · PERCEIVE", DNAVY, [("camera", "Camera input", "front view, every frame"), ("visibility", "Object detection", "vehicles, people, animals"),
                                  ("timeline", "Multi-object tracking", "IDs & history")]),
          ("2 · UNDERSTAND", TEAL, [("layers", "Depth & scene", "distance, free space"), ("brain", "Motion / threat", "who is moving where"),
                                   ("route", "Ego-path & corridor", "real drivable corridor")]),
          ("3 · PLAN & ACT", AMBER, [("loop", "Candidate trajectories", "kinematic path set"), ("warning", "Risk-aware selection", "reject conflicts"),
                                    ("shield", "Steering / decision", "path + GO … BRAKE")])]
arrow(s, L + 4.3, 2.08, L + 4.3, 2.2, color=GREY, width=1.2)
pw = 2.72
for k, (title, col, stages) in enumerate(phases):
    x = L + k * (pw + 0.18)
    rect(s, x, 2.22, pw, 0.3, fill=col, radius=0.05)
    text(s, x, 2.22, pw, 0.3, title, size=9, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    for j, (ic, t1, t2) in enumerate(stages):
        y = 2.6 + j * 0.8
        card(s, x, y, pw, 0.72, fill="FFFFFF", line="D5DDE8")
        text(s, x + 0.08, y + 0.1, 0.3, 0.5, f"{k * 3 + j + 1}", size=15, bold=True, color=col)
        icon(s, ic, x + 0.4, y + 0.16, 0.4, "w", circle=col, pad=0.2)
        text(s, x + 0.9, y + 0.12, pw - 0.95, 0.24, t1, size=9, bold=True, color=DNAVY)
        text(s, x + 0.9, y + 0.38, pw - 0.95, 0.24, t2, size=7.5, color=SUB)
        if j < 2:
            arrow(s, x + pw / 2, y + 0.72, x + pw / 2, y + 0.8, color=col, width=1.0)
    if k < 2:
        sh = s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x + pw + 0.02), Inches(3.55), Inches(0.14), Inches(0.26))
        sh.fill.solid(); sh.fill.fore_color.rgb = rgb(GREY); sh.line.fill.background()
rect(s, L, 5.03, 8.52, 0.34, fill=DNAVY, radius=0.05)
icon(s, "shield", L + 0.1, 5.07, 0.26, "w")
text(s, L + 0.45, 5.03, 8.0, 0.34, [[("Safety-First Decision Layer   ", {"bold": True}), ("path-threat reasoning  |  persistence & hysteresis  |  reasons", {"color": "C9D6E8"})]],
     size=9, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
chip(s, L, 5.47, "OUTPUTS / DECISIONS", GREEN)
outs = [("route", "Safe trajectory", "Selected collision-free path"), ("shield", "Driving decision", "GO · SLOW · STEER · BRAKE · NO SAFE PATH"),
        ("warning", "Threat alerts", "Which road user, why, how soon"), ("timeline", "Decision log", "Explainable, replayable reasons")]
for i, (ic, t1, t2) in enumerate(outs):
    x = L + i * 2.16
    card(s, x, 5.78, 2.08, 0.55, fill="E8F4EC")
    icon(s, ic, x + 0.08, 5.86, 0.36, "w", circle=GREEN, pad=0.2)
    text(s, x + 0.52, 5.81, 1.5, 0.22, t1, size=8, bold=True, color=DNAVY)
    text(s, x + 0.52, 6.03, 1.52, 0.28, t2, size=6.5, color=SUB)
text(s, L, 6.45, 1.15, 0.45, "Designed for:", size=8, bold=True, color=DNAVY, anchor=MSO_ANCHOR.MIDDLE)
envs = [("Urban dense traffic", "crowds, vendors, turning buses"), ("Village roads", "narrow, unmarked, cattle, tractors"),
        ("Highways & transitions", "merges, trucks, higher speeds"), ("Unmarked city roads", "work zones, wrong-way riders")]
for i, (t1, t2) in enumerate(envs):
    x = L + 1.12 + i * 1.86
    rect(s, x, 6.47, 1.8, 0.42, fill=TEAL_T, radius=0.06)
    text(s, x + 0.06, 6.49, 1.7, 0.38, [[(t1, {"bold": True, "color": DNAVY})], [(t2, {"size": 6.3})]], size=7.3, color=SUB, anchor=MSO_ANCHOR.MIDDLE)
# tech stack table
RX, RW = 9.2, 3.73
chip(s, RX, 1.22, "TECH STACK", DNAVY)
rows = [("Perception", BLUE, [("ultralytics", "YOLO"), ("opencv", "OpenCV")]),
        ("Tracking", GREEN, [("icon:timeline", "ByteTrack")]),
        ("Depth / Vision", PURPLE, [("icon:layers", "Depth Anything"), ("icon:visibility", "CLIP")]),
        ("Planning", ORANGE, [("python", "Python"), ("numpy", "NumPy")]),
        ("Web / Demo", RED, [("flask", "Flask"), ("html", "HTML"), ("css", "CSS"), ("javascript", "JS")]),
        ("Runtime", "2C5F7C", [("pytorch", "PyTorch"), ("nvidia", "CUDA")])]
for i, (cat, col, items) in enumerate(rows):
    y = 1.55 + i * 0.4
    card(s, RX, y, RW, 0.35, fill=LIGHT if i % 2 == 0 else "FFFFFF", line=None if i % 2 == 0 else "E3E8EF")
    text(s, RX + 0.1, y, 1.05, 0.35, cat, size=8, bold=True, color=col, anchor=MSO_ANCHOR.MIDDLE)
    x = RX + 1.15
    for lg, lab in items:
        if lg.startswith("icon:"):
            icon(s, lg[5:], x, y + 0.07, 0.21, {BLUE: "b", GREEN: "g", PURPLE: "n"}.get(col, "n"))
        else:
            logo(s, lg, x, y + 0.07, 0.21)
        lw_ = 0.1 + 0.058 * len(lab)
        text(s, x + 0.25, y, lw_, 0.35, lab, size=7.3, bold=True, color=DNAVY, anchor=MSO_ANCHOR.MIDDLE)
        x += 0.3 + lw_ + (0.04 if len(items) > 2 else 0.12)
text(s, RX, 3.98, RW, 0.2, "GPU-accelerated inference with open pretrained models", size=7, italic=True, color=SUB)
# methodology loop
chip(s, RX, 4.3, "METHODOLOGY", TEAL)
card(s, RX, 4.62, RW, 2.3, fill=TEAL_T)
meth = [("camera", "Sense", BLUE), ("visibility", "Detect & track", GREEN), ("brain", "Predict motion", ORANGE),
        ("warning", "Assess path threat", PURPLE), ("route", "Plan trajectory", TEAL), ("loop", "Act & re-plan", RED)]
xs = [RX + 0.55, RX + 1.86, RX + 3.17]
for k, (ic, lab, col) in enumerate(meth):
    row, c = (0, k) if k < 3 else (1, 5 - k)
    cx_, cy_ = xs[c], 5.0 + row * 1.02
    icon(s, ic, cx_ - 0.24, cy_ - 0.24, 0.48, "w", circle=col, pad=0.22)
    rect(s, cx_ - 0.3, cy_ - 0.3, 0.17, 0.17, fill=DNAVY, shape=MSO_SHAPE.OVAL)
    text(s, cx_ - 0.3, cy_ - 0.3, 0.17, 0.17, str(k + 1), size=6.5, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, cx_ - 0.62, cy_ + 0.27, 1.24, 0.2, lab, size=7, bold=True, color=DNAVY, align=PP_ALIGN.CENTER)
for a, b in [(0, 1), (1, 2)]:
    arrow(s, xs[a] + 0.3, 5.0, xs[b] - 0.3, 5.0, color=DNAVY, width=1.2)
    arrow(s, xs[b] - 0.3, 6.02, xs[a] + 0.3, 6.02, color=DNAVY, width=1.2)
arrow(s, xs[2], 5.49, xs[2], 5.74, color=DNAVY, width=1.2)
arrow(s, xs[0], 5.74, xs[0], 5.49, color=DNAVY, width=1.2)
text(s, RX + 0.9, 5.44, RW - 1.8, 0.34, "Continuous perception-to-planning loop", size=6.8, italic=True, color=SUB, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

# ================================================================== 4. PROTOTYPE - four real screenshots of the web app
s = new_slide(4, "PROTOTYPE", "Screens from the working PathSense web application (research prototype)")
shots = [("1_home", "01", "Landing page", "Entry point; “Try PathSense” opens the app (Demo · Live · Emergency)"),
         ("2_demo", "02", "Demo playback", "Processed drive: detections, bird’s-eye candidate trajectories, decision and reason"),
         ("3_replay", "03", "Replay of key moments", "Each moment lists object, distance, TTC and path status; click to replay"),
         ("4_emergency", "04", "Emergency (simulation mode)", "Confirmation-first workflow; never contacts real services automatically")]
SW, SH = 4.36, 4.36 * 9 / 16
for i, (nm, num, t1, t2) in enumerate(shots):
    x, y = L + (i % 2) * (SW + 0.16), 1.22 + (i // 2) * (SH + 0.43)
    text(s, x, y, 0.4, 0.3, num, size=13, bold=True, color=TEAL, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.42, y, SW - 0.42, 0.3, [[(t1 + "  ", {"bold": True, "color": DNAVY, "size": 9}), (t2, {})]], size=7, color=SUB, anchor=MSO_ANCHOR.MIDDLE)
    pic = s.shapes.add_picture(screen(nm, SW, SH, fy=0.0), Inches(x), Inches(y + 0.32), Inches(SW), Inches(SH))
    pic.line.color.rgb = rgb("C9D3E0"); pic.line.width = Pt(0.75)
PX_ = L + 2 * SW + 0.36
PW_ = 12.93 - PX_
card(s, PX_, 1.22, PW_, 2.75, fill=DNAVY)
text(s, PX_ + 0.18, 1.34, PW_ - 0.3, 2.55, [
    [("Current Development", {"bold": True, "size": 15})],
    [("Working prototype: ", {"bold": True}), ("camera-based perception, tracking, motion understanding and adaptive path planning", {})],
    [("Code repository:", {"bold": True})],
    [("github.com/MalyalaKarthik66/pathsense-prototype", {"size": 8.5, "url": REPO, "underline": True, "color": "9FE3D9"})],
    [("Live demonstration shown separately during evaluation.", {"italic": True, "size": 8.5, "color": "C9D6E8"})]],
    size=9.5, color="FFFFFF", space=6)
card(s, PX_, 4.12, PW_, 2.8, fill=TEAL_T)
text(s, PX_ + 0.18, 4.24, PW_ - 0.3, 0.26, "What the prototype demonstrates", size=10.5, bold=True, color=DNAVY)
text(s, PX_ + 0.18, 4.6, PW_ - 0.32, 2.25, ["Detection & tracking of mixed Indian road users", "Path-threat reasoning: oncoming vs. entering the path",
     "Adaptive candidate-path planning around obstacles", "Explainable decisions with replay"], size=9, color=INK, bullets=True, space=6)

# ================================================================== 5. FEASIBILITY AND VIABILITY
s = new_slide(5, "FEASIBILITY AND VIABILITY")
cols2 = [("FEASIBILITY", "check", DNAVY, [("Technology: ", "Established computer-vision and motion-planning building blocks; camera-based perception to start."),
                                          ("Architecture: ", "Modular perception → prediction → planning; radar / LiDAR and vehicle-state inputs added progressively; scenario-based simulation supports development.")]),
         ("VIABILITY", "rocket", TEAL, [("Relevance: ", "Mixed, irregular traffic on urban roads, village roads, highways and dense traffic."),
                                        ("Deployment: ", "Modular architecture supports incremental deployment through controlled pilots."),
                                        ("Future fit: ", "Can integrate with future intelligent-vehicle platforms.")])]
for i, (t1, ic, col, pts) in enumerate(cols2):
    x = L + i * 4.02
    card(s, x, 1.22, 3.9, 2.05, fill=LIGHT)
    rect(s, x, 1.22, 3.9, 0.42, fill=col, radius=0.07)
    icon(s, ic, x + 0.12, 1.29, 0.28, "w")
    text(s, x + 0.5, 1.22, 3.3, 0.42, t1, size=12, bold=True, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.16, 1.78, 3.6, 1.8, [[(a, {"bold": True, "color": DNAVY}), (b, {})] for a, b in pts], size=9.5, color=INK, bullets=True, space=5)
chip(s, L, 3.45, "RISKS → MITIGATION", CORAL)
risks = [("Unstructured road geometry", "Drivable-corridor estimation and path-aware planning."),
         ("Unpredictable road-user behaviour", "Temporal tracking and short-term motion reasoning."),
         ("Perception uncertainty", "Confidence-aware decisions and future sensor fusion."),
         ("Real-world validation complexity", "Scenario library + simulation + controlled field testing.")]
rect(s, L, 3.77, 7.92, 0.34, fill=DNAVY)
text(s, L + 0.15, 3.77, 3.2, 0.34, "RISK", size=8.5, bold=True, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
text(s, L + 3.85, 3.77, 3.9, 0.34, "MITIGATION", size=8.5, bold=True, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
for i, (r_, m_) in enumerate(risks):
    y = 4.11 + i * 0.7
    rect(s, L, y, 7.92, 0.7, fill=LIGHT if i % 2 == 0 else "FFFFFF", line="E3E8EF")
    icon(s, "warning", L + 0.12, y + 0.22, 0.26, "r")
    text(s, L + 0.48, y, 3.0, 0.7, r_, size=9.5, bold=True, color=DNAVY, anchor=MSO_ANCHOR.MIDDLE)
    arrow(s, L + 3.4, y + 0.35, L + 3.72, y + 0.35, color=TEAL, width=1.5)
    text(s, L + 3.85, y, 3.95, 0.7, m_, size=9.5, color=INK, anchor=MSO_ANCHOR.MIDDLE)
RX = 8.62
RW = 12.93 - RX
chip(s, RX, 1.22, "PROTOTYPE → PILOT → PRODUCTION", DNAVY)
stages = [("PROTOTYPE", DNAVY, "Camera-based proof of concept", " for perception, tracking and adaptive path planning."),
          ("PILOT", TEAL, "Multi-scenario validation", " with richer sensing, simulation and controlled road testing."),
          ("PRODUCTION", AMBER, "Vehicle-grade multi-sensor integration", ", closed-loop validation and automotive safety engineering.")]
rect(s, RX + 0.24, 1.72, 0.04, 2.3, fill="C9D3E0")
for i, (t1, col, b1, b2) in enumerate(stages):
    y = 1.62 + i * 0.95
    rect(s, RX + 0.08, y + 0.02, 0.36, 0.36, fill=col, shape=MSO_SHAPE.OVAL)
    text(s, RX + 0.08, y + 0.02, 0.36, 0.36, str(i + 1), size=10, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, RX + 0.58, y, RW - 0.6, 0.3, t1, size=13, bold=True, color=col)
    text(s, RX + 0.58, y + 0.3, RW - 0.6, 0.55, [[(b1, {"bold": True}), (b2, {})]], size=8.8, color=INK)
chip(s, RX, 4.55, "POTENTIAL COLLABORATORS / PARTNERS", TEAL)
partners = [("car", "Automotive OEMs & Tier-1s", "ADAS / AV integration"), ("science", "Research institutes", "IITs, IISc, AV labs"),
            ("layers", "Simulation partners", "e.g. MathWorks toolchain"), ("shield", "Road-safety bodies", "MoRTH, state transport"),
            ("bus", "Fleet operators", "Buses, taxis, logistics"), ("map", "Mapping & data", "OpenStreetMap, IDD")]
for i, (ic, t1, t2) in enumerate(partners):
    x, y = RX + (i % 2) * (RW / 2 + 0.02), 4.9 + (i // 2) * 0.68
    card(s, x, y, RW / 2 - 0.06, 0.6)
    icon(s, ic, x + 0.07, y + 0.12, 0.36, "w", circle=[BLUE, GREEN, PURPLE, RED, ORANGE, TEAL][i], pad=0.2)
    text(s, x + 0.5, y + 0.07, RW / 2 - 0.6, 0.26, t1, size=7.8, bold=True, color=DNAVY)
    text(s, x + 0.5, y + 0.34, RW / 2 - 0.6, 0.22, t2, size=6.8, color=SUB)

# ================================================================== 6. PROOF OF CONCEPT, IMPACT & BENEFITS
s = new_slide(6, "PROOF OF CONCEPT, IMPACT & BENEFITS")
chip(s, L, 1.22, "PROOF OF CONCEPT", DNAVY)
poc = [("PROBLEM", "Unstructured Indian roads create difficult path-planning conditions"),
       ("OBJECTIVE", "Safe adaptive trajectories without relying solely on lane markings"),
       ("PROTOTYPE", "Camera-based perception, tracking, motion understanding and adaptive planning"),
       ("VALIDATION", "Scenario-based evaluation across mixed-traffic road situations"),
       ("SUCCESS CRITERIA", "Safe path selection, appropriate risk response, robust behaviour across roads"),
       ("NEXT STEPS", "Sensor fusion, closed-loop simulation, broader field trials, vehicle integration")]
pcols = [BLUE, "D4A017", GREEN, PURPLE, ORANGE, TEAL]
seg = 12.53 / 6
rect(s, L + seg / 2, 1.83, seg * 5, 0.04, fill="C9D3E0")
for i, ((t1, t2), col) in enumerate(zip(poc, pcols)):
    cx_ = L + seg * i + seg / 2
    rect(s, cx_ - 0.24, 1.61, 0.48, 0.48, fill=col, line="FFFFFF", lw=2, shape=MSO_SHAPE.OVAL)
    text(s, cx_ - 0.24, 1.61, 0.48, 0.48, str(i + 1), size=13, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    card(s, cx_ - seg / 2 + 0.05, 2.18, seg - 0.1, 1.05)
    text(s, cx_ - seg / 2 + 0.14, 2.25, seg - 0.28, 0.24, t1, size=8.8, bold=True, color=col, align=PP_ALIGN.CENTER)
    text(s, cx_ - seg / 2 + 0.14, 2.5, seg - 0.28, 0.7, t2, size=8, color=INK, align=PP_ALIGN.CENTER)
rect(s, L, 3.33, 12.53, 0.32, fill=TEAL_T, radius=0.05)
text(s, L + 0.15, 3.33, 12.2, 0.32, [[("Resources: ", {"bold": True, "color": DNAVY}), ("computer vision & motion planning, road-scene video, simulation tools, compute hardware", {})]],
     size=9, color=INK, anchor=MSO_ANCHOR.MIDDLE)
chip(s, L, 3.85, "POTENTIAL IMPACTS", TEAL)
text(s, L + 2.0, 3.85, 3.0, 0.25, "Target Audience", size=9, bold=True, color=TEAL, anchor=MSO_ANCHOR.MIDDLE)
imp = [("car", "Drivers / Passengers", "Improved handling of unpredictable road interactions"),
       ("bus", "Urban Mobility", "More adaptive navigation in dense mixed traffic"),
       ("map", "Rural Mobility", "Better operation on roads with limited lane structure"),
       ("shield", "Road Safety", "Earlier recognition of path conflicts and adaptive response"),
       ("rocket", "Future Autonomous Vehicles", "A foundation for Indian-road-aware navigation")]
for i, (ic, t1, t2) in enumerate(imp):
    y = 4.2 + i * 0.55
    card(s, L, y, 6.1, 0.49, fill=LIGHT if i % 2 == 0 else "FFFFFF", line=None if i % 2 == 0 else "E3E8EF")
    icon(s, ic, L + 0.1, y + 0.08, 0.33, "w", circle=TEAL, pad=0.2)
    text(s, L + 0.55, y, 5.45, 0.49, [[(t1 + ": ", {"bold": True, "color": DNAVY}), (t2, {})]], size=9.3, color=INK, anchor=MSO_ANCHOR.MIDDLE)
BX = 6.75
chip(s, BX, 3.85, "BENEFITS", AMBER)
ben = [("shield", "SAFETY", BLUE, ["Path-aware collision avoidance", "Earlier recognition of path conflicts"]),
       ("tune", "ADAPTABILITY", "D4A017", ["Handles changing road and traffic conditions", "Works where lane markings are unreliable"]),
       ("layers", "SCALABILITY", GREEN, ["Can evolve from prototype → pilot → production", "Modular: new sensors and platforms plug in"]),
       ("map", "INDIAN-ROAD RELEVANCE", PURPLE, ["Designed around mixed and irregular road behaviour", "Safer shared roads for pedestrians and two-wheelers"])]
bw = (12.93 - BX - 0.12) / 2
for i, (ic, t1, col, pts) in enumerate(ben):
    x, y = BX + (i % 2) * (bw + 0.12), 4.2 + (i // 2) * 1.38
    card(s, x, y, bw, 1.3, fill="FFFFFF", line="D5DDE8")
    icon(s, ic, x + 0.12, y + 0.12, 0.36, "w", circle=col, pad=0.2)
    text(s, x + 0.56, y + 0.12, bw - 0.6, 0.36, t1, size=9.5, bold=True, color=col, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.14, y + 0.56, bw - 0.25, 0.72, pts, size=8.5, color=INK, bullets=True, space=3)

# ================================================================== 7. BUSINESS MODEL CANVAS
s = new_slide(7, "BUSINESS MODEL CANVAS")
bmc = [("Key Partnerships", "flag", 0, 0, 1, 2, [("OEMs:", " Automotive OEMs & Tier-1 ADAS suppliers"), ("ACADEMIA:", " IITs, IISc & autonomy research labs"),
        ("SIMULATION:", " MathWorks toolchain (RoadRunner, Automated Driving Toolbox)"), ("DATA:", " Indian driving datasets (IDD), OpenStreetMap"),
        ("GOVT:", " MoRTH, state transport & road-safety bodies"), ("FLEETS:", " Logistics, taxi & public-transport operators")]),
       ("Key Activities", "bolt", 1, 0, 1, 1, [("PERCEPTION R&D:", " Detection, tracking & depth"), ("PLANNING:", " Adaptive trajectory & risk logic"),
        ("VALIDATION:", " Scenario library & simulation"), ("INTEGRATION:", " Vehicle / ADAS interfaces")]),
       ("Key Resources", "layers", 1, 1, 1, 1, [("SOFTWARE STACK:", " Perception-to-planning pipeline"), ("DATA:", " Indian-road scenario library"),
        ("TEAM:", " CV, robotics & software engineers"), ("TOOLS:", " Simulation & compute infrastructure")]),
       ("Value Propositions", "check", 2, 0, 1, 2, [("OEMs:", " An Indian-road-aware planning layer"), ("FLEETS:", " Fewer conflicts in mixed traffic"),
        ("ROAD USERS:", " Earlier recognition of path conflicts"), ("RESEARCHERS:", " Scenario-based validation workflow"),
        ("EXPLAINABLE:", " Every decision comes with its reason"), ("MODULAR:", " Camera-first, sensor-ready architecture")]),
       ("Customer Relationships", "phone", 3, 0, 1, 1, [("CO-DEVELOPMENT:", " Pilot programmes with OEMs"), ("SUPPORT:", " Integration & tuning"),
        ("UPDATES:", " Continuous model & scenario updates"), ("TRANSPARENCY:", " Decision logs for audit")]),
       ("Channels", "truck", 3, 1, 1, 1, [("DIRECT B2B:", " OEM & Tier-1 partnerships"), ("PILOTS:", " Fleet & campus deployments"),
        ("RESEARCH:", " Academic collaborations"), ("SDK / API:", " Licensable software modules")]),
       ("Customer Segments", "car", 4, 0, 1, 2, [("PRIMARY:", " Automotive OEMs & ADAS suppliers"), ("SECONDARY:", " Fleet & logistics operators"),
        ("PUBLIC:", " Transport & road-safety agencies"), ("RESEARCH:", " AV labs & testing bodies"), ("SCALE-UP:", " City → state → national deployments")])]
colw, gap, top, rowh = (12.53 - 4 * 0.1) / 5, 0.1, 1.22, 1.9
bcols = [DNAVY, TEAL, "2C5F7C", AMBER, "1B4F6E", "137A87", CORAL]
for k, (t1, ic, c, r, cs, rs, items) in enumerate(bmc):
    x, y = L + c * (colw + gap), top + r * (rowh + gap)
    h = rowh * rs + gap * (rs - 1)
    card(s, x, y, colw, h, fill=LIGHT)
    rect(s, x, y, colw, 0.34, fill=bcols[k], radius=0.07)
    icon(s, ic, x + 0.1, y + 0.06, 0.22, "w")
    text(s, x + 0.38, y, colw - 0.4, 0.34, t1, size=9.5, bold=True, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.1, y + 0.42, colw - 0.18, h - 0.48, [[(a, {"bold": True, "color": DNAVY}), (b, {})] for a, b in items],
         size=8.8, color=INK, bullets=True, space=3)
by = top + 2 * rowh + 2 * gap
bh = 6.95 - by
for k, (t1, ic, items) in enumerate([
        ("Cost Structure", "tune", [("R&D", " – perception & planning engineering"), ("Data", " – collection, annotation & scenario building"),
                                    ("Compute", " – training & simulation infrastructure"), ("Validation", " – field testing & safety engineering"),
                                    ("Operations", " – integration, support & updates")]),
        ("Revenue Streams", "rocket", [("Software licensing", " – per-vehicle / per-platform licences for OEMs"), ("Pilot contracts", " – fleet & campus deployments"),
                                       ("Scenario-library access", " – validation datasets & tools"), ("Integration services", " – customisation & calibration"),
                                       ("Support & updates", " – annual maintenance subscriptions")])]):
    w = (12.53 - gap) / 2
    x = L + k * (w + gap)
    card(s, x, by, w, bh, fill=LIGHT)
    rect(s, x, by, 0.34 + 0.09 * len(t1) + 0.3, 0.32, fill=[DNAVY, TEAL][k], radius=0.07)
    icon(s, ic, x + 0.1, by + 0.05, 0.22, "w")
    text(s, x + 0.38, by, 2.2, 0.32, t1, size=9.5, bold=True, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.12, by + 0.38, w - 0.2, bh - 0.42, [[(a, {"bold": True, "color": DNAVY}), (b, {})] for a, b in items],
         size=9, color=INK, bullets=True, space=2)

# ================================================================== 8. RESEARCH AND REFERENCES
s = new_slide(8, "RESEARCH AND REFERENCES")
chip(s, L, 1.22, "SUPPORTING RESEARCH PAPERS", DNAVY)
papers = [("AUTONOMOUS DRIVING & PATH PLANNING", "“A Survey of Motion Planning and Control Techniques for Self-Driving Urban Vehicles”",
           "Paden et al. — IEEE T-IV, 2016", "arxiv.org/abs/1604.07446"),
          ("OBJECT DETECTION & TRACKING", "“ByteTrack: Multi-Object Tracking by Associating Every Detection Box”",
           "Zhang et al. — ECCV, 2022", "arxiv.org/abs/2110.06864"),
          ("DEPTH ESTIMATION", "“Depth Anything V2”", "Yang et al. — NeurIPS, 2024", "arxiv.org/abs/2406.09414"),
          ("MOTION PREDICTION", "“Social LSTM: Human Trajectory Prediction in Crowded Spaces”", "Alahi et al. — CVPR, 2016", "openaccess.thecvf.com")]
PWd = (8.15 - 0.12) / 2
for i, (cat, title, who, url) in enumerate(papers):
    x, y = L + (i % 2) * (PWd + 0.12), 1.55 + (i // 2) * 1.2
    card(s, x, y, PWd, 1.12, fill=LIGHT)
    text(s, x + 0.14, y + 0.1, PWd - 0.25, 0.2, cat, size=7.8, bold=True, color=TEAL)
    text(s, x + 0.14, y + 0.32, PWd - 0.25, 0.8, [[(title, {"italic": True, "color": DNAVY})], [(who, {"size": 8, "color": SUB})],
                                                  [(url, {"size": 8, "url": "https://" + url, "underline": True, "color": "1155CC"})]], size=9, space=1)
lists = [("INDIAN ROAD DATA", "map", GREEN, [("IDD – Indian Driving Dataset", "idd.insaan.iiit.ac.in"), ("MoRTH – Road Accidents in India", "morth.nic.in"),
                                             ("OpenStreetMap", "openstreetmap.org"), ("Wikimedia Commons", "commons.wikimedia.org")]),
         ("SIH & CONTEXT", "flag", AMBER, [("SIH 2026 – PS SIH26037", "sih.gov.in"), ("MathWorks – RoadRunner & ADT", "mathworks.com")]),
         ("TECHNICAL DOCS", "web", PURPLE, [("Ultralytics YOLO", "docs.ultralytics.com"), ("PyTorch", "pytorch.org/docs"),
                                            ("OpenCV", "docs.opencv.org"), ("Flask", "flask.palletsprojects.com")])]
lw3 = (8.15 - 0.24) / 3
for i, (t1, ic, col, items) in enumerate(lists):
    x = L + i * (lw3 + 0.12)
    card(s, x, 4.05, lw3, 2.5, fill="FFFFFF", line="D5DDE8")
    icon(s, ic, x + 0.12, 4.15, 0.3, "w", circle=col, pad=0.2)
    text(s, x + 0.5, 4.15, lw3 - 0.55, 0.3, t1, size=8.8, bold=True, color=DNAVY, anchor=MSO_ANCHOR.MIDDLE)
    paras = []
    for name, url in items:
        paras.append([(name, {"bold": True, "color": DNAVY})])
        paras.append([(url, {"size": 7.8, "url": "https://" + url.split(" ")[0], "underline": True, "color": "1155CC"})])
    if t1 == "SIH & CONTEXT":
        paras.append([("Problem focus: unmarked roads, mixed traffic, informal merges, obstacles.", {"color": SUB})])
    text(s, x + 0.14, 4.55, lw3 - 0.24, 1.95, paras, size=8.3, space=2)
text(s, L, 6.63, 8.15, 0.3, [[("Sources: ", {"bold": True, "italic": False}), ("IEEE T-IV · ECCV · NeurIPS · CVPR · IDD (IIIT-H) · MoRTH · OpenStreetMap · SIH 2026 · Ultralytics · PyTorch · OpenCV", {})]],
     size=7.5, color=SUB, italic=True, anchor=MSO_ANCHOR.MIDDLE)
RX = 8.8
RW = 12.93 - RX
chip(s, RX, 1.22, "NATIONAL ALIGNMENT", TEAL)
tiles = [("Road Safety", "MoRTH road-safety goals", "Earlier recognition of path conflicts on Indian roads", RED, "shield"),
         ("IndiaAI Mission", "AI for Indian conditions", "Perception and planning built for Indian traffic", BLUE, "brain"),
         ("Atmanirbhar Bharat", "Self-reliant technology", "Home-grown autonomous-driving intelligence", "E8731A", "rocket"),
         ("Make in India", "Indigenous automotive tech", "ADAS software developed for Indian vehicles", GREEN, "car"),
         ("Smart Cities Mission", "Intelligent urban mobility", "Adaptive navigation in dense city traffic", PURPLE, "map"),
         ("Digital India", "Software-first innovation", "Open, modular and explainable mobility software", TEAL, "web")]
for i, (t1, t2, t3, col, ic) in enumerate(tiles):
    y = 1.55 + i * 0.9
    card(s, RX, y, RW, 0.82, fill=LIGHT)
    icon(s, ic, RX + 0.12, y + 0.17, 0.48, "w", circle=col, pad=0.22)
    text(s, RX + 0.72, y + 0.06, RW - 0.8, 0.28, [[(t1, {"bold": True, "color": col, "size": 11}), ("   " + t2, {"bold": True, "size": 7.5, "color": DNAVY})]], size=11,
         anchor=MSO_ANCHOR.MIDDLE)
    text(s, RX + 0.72, y + 0.38, RW - 0.8, 0.38, t3, size=8, color=SUB)

# drop relationships to pictures that were removed (reference-deck images must not stay inside the package)
for s_ in prs.slides:
    xml = etree.tostring(s_._element).decode()
    for rId, rel in list(s_.part.rels.items()):
        if rel.reltype == RT.IMAGE and f'"{rId}"' not in xml:
            s_.part.drop_rel(rId)

dst = os.path.join(ROOT, "PathSense_SIH_Presentation.pptx")
prs.save(dst)
print("saved", dst, len(prs.slides), "slides")
