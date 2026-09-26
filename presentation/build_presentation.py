"""
Builds PathSense_SIH_Final_Presentation.pptx on the OFFICIAL "SIH FINAL FORMAT" template.

    python presentation/build_presentation.py  [path\\to\\SIH FINAL FORMAT.pptx]

Rules followed
  * The official template is the authority: its 10 slides, order, headers, SIH logo, footer, page numbers, team-name
    badge and title-page structure are kept; only the guidance text boxes are replaced with PathSense content. The
    official pointer texts are kept (verbatim) as section sub-labels.
  * The team's reference deck (finalforsih.pptx) is used only for its TOPIC organisation (proposed solution / problems
    it addresses / differentiation, architecture + tech stack, prototype, feasibility + risks + roadmap, proof of
    concept, impact + benefits, business model canvas, references). Its visual design is NOT copied.
  * PathSense visual identity: teal "path" chevrons, icon badges, white cards; decision colours GO / SLOW / BRAKE.
  * Prototype slide: real frames from the processed demo videos (presentation/extract_frames.py), cropped to the
    camera view. No website/dashboard screenshots, no benchmark numbers anywhere.
Assets: presentation/assets/{frames,scenes,icons,logos}.
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
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "presentation", "assets")
ICONS, LOGOS, SCENES, FRAMES = (os.path.join(ASSETS, d) for d in ("icons", "logos", "scenes", "frames"))
CROPS = os.path.join(ASSETS, "deck")
os.makedirs(CROPS, exist_ok=True)
TEMPLATE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "Downloads", "SIH FINAL FORMAT.pptx")
OUTFILE = os.path.join(ROOT, "PathSense_SIH_Final_Presentation.pptx")

TEAM_NAME, TEAM_ID = "Ctrl Alt Elite", "161638"
PS_ID, THEME = "SIH26037", "Smart Vehicles"
PS_TITLE = "Adaptive Path Planning and Collision Avoidance for Autonomous Vehicles on Unstructured Indian Roads"
REPO = "https://github.com/MalyalaKarthik66/pathsense-prototype"

# PathSense identity inside the SIH template
NAVY, INK, MUTED, LINE = "1B2A41", "243044", "5B6778", "D3DCE6"
SIHBLUE = "1F497D"                      # the template's own pointer colour
TEAL, TEAL_D, TEAL_T, TEAL_TT = "0F9E8E", "0B7468", "E3F4F1", "F2FAF8"
SKY, SKY_T = "2A6FB0", "E6EFF8"
GO, SLOW, BRAKE = "16A34A", "D97706", "DC2626"
GO_T, SLOW_T, BRAKE_T = "E8F6EC", "FDF1E1", "FDECEC"
CARD = "FFFFFF"
FONT = "Arial"

prs = Presentation(TEMPLATE)
SL = list(prs.slides)
assert len(SL) == 10, f"expected the 10-slide official format, got {len(SL)}"


# ------------------------------------------------------------------ template helpers
def shp(s, name):
    return next(x for x in s.shapes if x.name == name)


def remove(sh):
    sh._element.getparent().remove(sh._element)


def set_first_text(el, new):
    """replace the text of the first run inside an element (group/shape), dropping other runs/paragraphs"""
    ps = el.findall(".//" + qn("a:p"))
    p0 = next(p for p in ps if p.findall(qn("a:r")))
    runs = p0.findall(qn("a:r"))
    runs[0].find(qn("a:t")).text = new
    for r in runs[1:]:
        p0.remove(r)
    for p in ps:
        if p is not p0 and p.getparent() is p0.getparent():
            p.getparent().remove(p)


def team_badge(s):
    for sh in s.shapes:
        if sh.shape_type == 6 and "Your Team Name" in "".join(t.text or "" for t in sh._element.iter(qn("a:t"))):
            set_first_text(sh._element, TEAM_NAME)


def header_group(s):
    for sh in s.shapes:
        if sh.shape_type == 6 and sh.top is not None and sh.top < Inches(0.3) and sh.width > Inches(10):
            return sh
    return None


# ------------------------------------------------------------------ drawing helpers
def rgb(h):
    return RGBColor.from_string(h)


def rect(s, x, y, w, h, fill=CARD, line=None, lw=1.0, radius=None, shape=None, dash=None):
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


def text(s, x, y, w, h, paras, size=11.5, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
         space=0, bullets=False, italic=False, line_spacing=None, bullet_color=TEAL):
    """paras: str | [para]; para: str | [(text, {bold,size,color,italic,url,underline})]"""
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
            pPr.set("marL", str(int(Inches(0.2)))); pPr.set("indent", str(-int(Inches(0.2))))
            bc = etree.SubElement(pPr, qn("a:buClr")); etree.SubElement(bc, qn("a:srgbClr")).set("val", bullet_color)
            etree.SubElement(pPr, qn("a:buFont")).set("typeface", "Arial")
            etree.SubElement(pPr, qn("a:buChar")).set("char", "•")
        for t, o in ([(para, {})] if isinstance(para, str) else para):
            r = p.add_run()
            r.text = t
            f = r.font
            f.name = FONT
            f.size = Pt(o.get("size", size))
            f.bold = o.get("bold", bold)
            f.italic = o.get("italic", italic)
            f.underline = o.get("underline", False)
            f.color.rgb = rgb(o.get("color", color))
            if o.get("url"):
                r.hyperlink.address = o["url"]
    return tb


def icon(s, name, x, y, size, color="b", badge=None, pad=0.2, round_=False):
    f = os.path.join(ICONS, f"{name}_{color}.png")
    if badge:
        rect(s, x, y, size, size, fill=badge, radius=None if round_ else size * 0.22,
             shape=MSO_SHAPE.OVAL if round_ else MSO_SHAPE.ROUNDED_RECTANGLE)
        p = size * pad
        return s.shapes.add_picture(f, Inches(x + p), Inches(y + p), Inches(size - 2 * p), Inches(size - 2 * p))
    return s.shapes.add_picture(f, Inches(x), Inches(y), Inches(size), Inches(size))


def logo(s, name, x, y, size):
    return s.shapes.add_picture(os.path.join(LOGOS, f"{name}.png"), Inches(x), Inches(y), Inches(size), Inches(size))


def cover(name, src, w, h, fx=0.5, fy=0.5, zoom=1.0):
    im = Image.open(src).convert("RGB")
    W, H = im.size
    ar = w / h
    cw = W / zoom
    ch = cw / ar
    if ch > H / zoom:
        ch = H / zoom; cw = ch * ar
    x0 = min(max(fx * W - cw / 2, 0), W - cw)
    y0 = min(max(fy * H - ch / 2, 0), H - ch)
    out = os.path.join(CROPS, f"{name}.jpg")
    px = min(int(cw), max(600, int(w * 200)))
    im.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize((px, int(px / ar)), Image.LANCZOS).save(out, quality=90, optimize=True)
    return out


def picture(s, name, src, x, y, w, h, fx=0.5, fy=0.5, zoom=1.0, border=LINE):
    pic = s.shapes.add_picture(cover(name, src, w, h, fx, fy, zoom), Inches(x), Inches(y), Inches(w), Inches(h))
    if border:
        pic.line.color.rgb = rgb(border); pic.line.width = Pt(1)
    return pic


def arrow(s, x1, y1, x2, y2, color=MUTED, width=1.5, head=True):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    if head:
        t = etree.SubElement(c.line._get_or_add_ln(), qn("a:tailEnd"))
        t.set("type", "triangle"); t.set("w", "med"); t.set("len", "med")
    return c


def section(s, x, y, w, title, pointer=None, ic="route"):
    """PathSense section header: teal icon badge + title, with the official SIH pointer as a sub-label"""
    icon(s, ic, x, y, 0.5, "w", badge=TEAL, pad=0.2)
    text(s, x + 0.64, y - 0.02, w - 0.64, 0.32, title, size=17, bold=True, color=NAVY)
    if pointer:
        text(s, x + 0.64, y + 0.3, w - 0.64, 0.24, pointer, size=10.5, italic=True, color=SIHBLUE)


def card(s, x, y, w, h, fill=CARD, line=LINE):
    return rect(s, x, y, w, h, fill=fill, line=line, lw=1.0, radius=0.12)


def chevrons(s, x, y, w, h, steps, colors, size=11, first_pentagon=True):
    """a row of 'path' chevrons (the PathSense motif); steps: [(title, sub)]"""
    n = len(steps)
    ov = h * 0.28
    cw = (w + ov * (n - 1)) / n
    for i, ((t1, t2), col) in enumerate(zip(steps, colors)):
        kind = MSO_SHAPE.PENTAGON if (i == 0 and first_pentagon) else MSO_SHAPE.CHEVRON
        cx = x + i * (cw - ov)
        sh = s.shapes.add_shape(kind, Inches(cx), Inches(y), Inches(cw), Inches(h))
        sh.fill.solid(); sh.fill.fore_color.rgb = rgb(col)
        sh.line.color.rgb = rgb("FFFFFF"); sh.line.width = Pt(1.5)
        sh.shadow.inherit = False
        sh.adjustments[0] = 0.28 if kind == MSO_SHAPE.CHEVRON else 0.28
        pad = ov + 0.08 if i > 0 else 0.15
        paras = [[(t1, {"bold": True, "size": size, "color": "FFFFFF"})]]
        if t2:
            paras.append([(t2, {"size": size - 2, "color": "FFFFFF"})])
        text(s, cx + pad, y, cw - pad - ov - 0.05, h, paras, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


PHASE = ["1F497D", "245C93", "2A6FB0", "1B82A0", "118F95", "0F9E8E", "0B8A7C", "0B7468"]

for s in SL[1:]:
    team_badge(s)

# ================================================================== 1. TITLE PAGE (official structure, fields filled)
s = SL[0]
tb = shp(s, "TextBox 10")
values = {"Problem Statement ID": PS_ID, "Problem Statement Title": PS_TITLE, "Theme": THEME,
          "PS Category": "Software", "Team ID": TEAM_ID, "Team Name": TEAM_NAME}
for p in tb.text_frame.paragraphs:
    full = "".join(r.text for r in p.runs)
    key = next((k for k in values if full.strip().startswith(k)), None)
    if key is None or not p.runs:
        continue
    label = {"Problem Statement ID": "Problem Statement ID – ", "Problem Statement Title": "Problem Statement Title – ",
             "Theme": "Theme – ", "PS Category": "PS Category – ", "Team ID": "Team ID – ", "Team Name": "Team Name – "}[key]
    p.runs[0].text = label
    for r in p.runs[1:]:
        r._r.getparent().remove(r._r)
    r = deepcopy(p.runs[0]._r)
    r.find(qn("a:t")).text = values[key]
    rp = r.find(qn("a:rPr"))
    rp.set("b", "0")
    for tag in ("a:latin", "a:ea", "a:cs", "a:sym"):
        el = rp.find(qn(tag))
        if el is not None:
            el.set("typeface", el.get("typeface").replace(" Bold", ""))
    p.runs[0]._r.addnext(r)
for p in tb.text_frame.paragraphs:
    pPr = p._p.get_or_add_pPr()
    pPr.set("algn", "l")
    for tag in ("a:lnSpc", "a:spcBef", "a:spcAft"):
        el = pPr.find(qn(tag))
        if el is not None:
            pPr.remove(el)
    ls = etree.Element(qn("a:lnSpc")); etree.SubElement(ls, qn("a:spcPct")).set("val", "100000"); pPr.insert(0, ls)
    sa = etree.Element(qn("a:spcAft")); etree.SubElement(sa, qn("a:spcPts")).set("val", "1600"); pPr.insert(1, sa)
    for r in p.runs:
        r.font.size = Pt(24)
tb.top = Inches(3.1)

# ================================================================== 2. IDEA TITLE -> PATHSENSE: proposed solution
s = SL[1]
set_first_text(header_group(s)._element, "PATHSENSE")
for n in ("TextBox 17", "TextBox 18", "TextBox 19"):
    remove(shp(s, n))
text(s, 3.2, 1.5, 13.6, 0.36, "Adaptive Path Planning & Collision Avoidance for Unstructured Indian Roads", size=16, bold=True,
     color=TEAL_D, align=PP_ALIGN.CENTER)
# A. proposed solution
L = 0.6
section(s, L, 2.05, 9.0, "PROPOSED SOLUTION", "Proposed Solution (Describe your Idea/Solution/Prototype) · Detailed explanation of the proposed solution", "bolt")
card(s, L, 2.75, 9.0, 2.3)
text(s, L + 0.25, 2.92, 5.55, 2.0, [
    [("PathSense", {"bold": True, "color": TEAL_D}), (" is an adaptive path-planning and collision-avoidance system for autonomous vehicles on unstructured Indian roads.", {})],
    [("It reasons about ", {}), ("mixed traffic", {"bold": True}), (" — pedestrians, motorcycles, auto-rickshaws, cars, buses, trucks and animals — on roads with ", {}),
     ("missing or unclear lane markings", {"bold": True}), (", informal merges, sudden direction changes, wrong-way movement and temporary obstacles.", {})]],
     size=12, space=8, line_spacing=1.05)
picture(s, "idea_mixed", os.path.join(SCENES, "market.png"), L + 5.95, 2.9, 2.9, 2.0, 0.5, 0.45, 1.25)
text(s, L + 5.95, 4.92, 2.9, 0.13, "concept illustration", size=8, italic=True, color=MUTED, align=PP_ALIGN.RIGHT)
# B. problems it addresses
section(s, L, 5.25, 9.0, "PROBLEMS IT ADDRESSES", "How it addresses the problem", "warning")
probs = [("route", "Unmarked / Unstructured Roads", "Plans in the drivable corridor, not painted lanes"),
         ("car", "Mixed Traffic", "Cars, buses, trucks, 2W, autos, people, animals together"),
         ("brain", "Irregular Road-User Behaviour", "Reads intent from short-term motion history"),
         ("walk", "Pedestrian & Cyclist Interaction", "Acts on people entering the path, not the footpath"),
         ("merge", "Wrong-Way / Informal Movement", "Flags movement that enters the ego path"),
         ("cow", "Animal & Temporary Obstacles", "Plans around cattle, carts and work zones"),
         ("warning", "Sudden Direction Changes", "Re-evaluates every road user every frame"),
         ("loop", "Dynamic Path Replanning", "Keeps choosing a new safe path as the scene changes")]
for i, (ic, t1, t2) in enumerate(probs):
    col, row = i % 2, i // 2
    x, y = L + col * 4.56, 5.95 + row * 0.82
    card(s, x, y, 4.44, 0.72, fill=TEAL_TT, line="CFE7E2")
    text(s, x + 0.12, y, 0.42, 0.72, f"{i + 1:02d}", size=13, bold=True, color=TEAL, anchor=MSO_ANCHOR.MIDDLE)
    icon(s, ic, x + 0.56, y + 0.17, 0.38, "e")
    text(s, x + 1.04, y + 0.08, 3.35, 0.3, t1, size=11.5, bold=True, color=NAVY)
    text(s, x + 1.04, y + 0.38, 3.35, 0.3, t2, size=9.5, color=MUTED)
# C. differentiation & key value proposition
R = 9.95
section(s, R, 2.05, 9.45, "DIFFERENTIATION & KEY VALUE PROPOSITION", "Innovation and uniqueness of the solution", "rocket")
diff = [("Ego-Path-Centric Risk Assessment", "Judges the threat to the vehicle’s own path — not mere proximity."),
        ("Indian Mixed-Traffic Awareness", "Built for autos, two-wheelers, pedestrians and animals."),
        ("Short-Term Motion Understanding", "Tracks each road user’s motion to anticipate conflicts."),
        ("Adaptive Trajectory Generation", "Samples kinematically feasible candidate paths every frame."),
        ("Lane-Independent Planning", "Works where lane markings are faded, partial or absent."),
        ("Safety-Oriented Decision Making", "GO → SLOW → STEER → BRAKE → NO SAFE PATH, each with a reason."),
        ("Dynamic Replanning", "Re-plans continuously as traffic and obstacles change."),
        ("Scalable Multi-Sensor Roadmap", "Camera-first today; radar, LiDAR and vehicle-state inputs later.")]
for i, (t1, t2) in enumerate(diff):
    col, row = i % 2, i // 2
    x, y = R + col * 4.8, 2.75 + row * 1.6
    card(s, x, y, 4.65, 1.48)
    rect(s, x + 0.18, y + 0.2, 0.62, 0.62, fill=TEAL if i % 2 == 0 else SKY, radius=0.14)
    text(s, x + 0.18, y + 0.2, 0.62, 0.62, f"{i + 1:02d}", size=15, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.95, y + 0.16, 3.6, 0.5, t1, size=12.5, bold=True, color=NAVY)
    text(s, x + 0.95, y + 0.66, 3.6, 0.75, t2, size=10.5, color=MUTED)
# D. core idea flow
text(s, L, 9.3, 1.5, 0.8, [[("CORE", {"bold": True})], [("IDEA", {"bold": True})]], size=13, color=TEAL_D, anchor=MSO_ANCHOR.MIDDLE)
chevrons(s, L + 1.45, 9.3, 17.35, 0.8,
         [("Perceive", "road scene"), ("Understand", "road users"), ("Estimate", "short-term movement"), ("Identify", "ego-path threats"),
          ("Generate", "candidate trajectories"), ("Select", "a safe trajectory"), ("Re-plan", "continuously")], PHASE[:7], size=11)

# ================================================================== 3. TECHNICAL APPROACH
s = SL[2]
for n in ("TextBox 17", "TextBox 18"):
    remove(shp(s, n))
section(s, L, 2.05, 12.4, "METHODOLOGY & ARCHITECTURE", "Methodology and process for implementation (Flow Charts/Images/ working prototype)", "layers")
stages = [("camera", "Camera / Road Scene", "front view of mixed traffic"), ("visibility", "Perception", "per-frame scene analysis"),
          ("warning", "Object Detection", "vehicles, people, animals"), ("timeline", "Multi-Object Tracking", "identities & motion history"),
          ("layers", "Depth / Scene Understanding", "distance & free space"), ("brain", "Motion & Threat Estimation", "who is moving where"),
          ("route", "Ego-Path / Drivable Corridor", "the vehicle’s real corridor"), ("loop", "Candidate Trajectories", "kinematic path set"),
          ("shield", "Risk-Aware Path Selection", "reject conflicting paths"), ("speed", "Steering / Decision", "path + action + reason")]
nw, nh, gx = 2.18, 1.25, 0.37
rows_y = [3.2, 5.0]
text(s, L, 2.75, 12.4, 0.3, [[("SENSE & UNDERSTAND  ", {"bold": True, "color": SKY}), ("→ row 1", {"color": MUTED, "size": 9.5}),
                              ("          PLAN & DECIDE  ", {"bold": True, "color": TEAL_D}), ("→ row 2 (right to left)", {"color": MUTED, "size": 9.5})]], size=10.5)
centers = []
for i, (ic, t1, t2) in enumerate(stages):
    r, c = divmod(i, 5)
    c = c if r == 0 else 4 - c
    x, y = L + c * (nw + gx), rows_y[r]
    col = PHASE[min(i, 7)] if i < 5 else [TEAL, TEAL, TEAL_D, TEAL_D, GO][i - 5]
    card(s, x, y, nw, nh, line=col)
    icon(s, ic, x + 0.14, y + 0.15, 0.46, "w", badge=col, pad=0.2)
    text(s, x + 0.12, y + 0.02, nw - 0.2, 0.25, str(i + 1), size=9, bold=True, color=col, align=PP_ALIGN.RIGHT)
    text(s, x + 0.7, y + 0.13, nw - 0.8, 0.55, t1, size=10.5, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.14, y + 0.76, nw - 0.25, 0.44, t2, size=9.5, color=MUTED)
    centers.append((x, y))
for i in range(9):
    (x1, y1), (x2, y2) = centers[i], centers[i + 1]
    if y1 == y2:
        if x2 > x1:
            arrow(s, x1 + nw + 0.02, y1 + nh / 2, x2 - 0.02, y2 + nh / 2, color=SKY, width=2)
        else:
            arrow(s, x1 - 0.02, y1 + nh / 2, x2 + nw + 0.02, y2 + nh / 2, color=TEAL, width=2)
    else:
        arrow(s, x1 + nw / 2, y1 + nh + 0.02, x2 + nw / 2, y2 - 0.02, color=SKY, width=2)
# re-plan loop from decision back to perception
lx0, ly = L + (nw + gx) + nw / 2, rows_y[1] + nh + 0.22
rect(s, L + nw / 2, ly - 0.01, (nw + gx) * 1.0, 0.02, fill=TEAL)
arrow(s, L + nw / 2, ly, L + nw / 2, rows_y[1] + nh + 0.02, color=TEAL, width=2, head=True)
text(s, L + nw / 2 + 0.15, ly + 0.05, 6.0, 0.28, "↺ continuous re-planning as the scene changes", size=10.5, bold=True, color=TEAL_D)
# decision ladder + candidate-path illustration
card(s, L, 6.95, 6.05, 3.2)
text(s, L + 0.25, 7.05, 5.6, 0.3, "DECISION LAYER — escalates with the threat to the ego path", size=11.5, bold=True, color=NAVY)
ladder = [("GO", GO, GO_T, "path clear, or traffic stays on its own side"), ("SLOW DOWN", SLOW, SLOW_T, "a road user is approaching the path"),
          ("STEER", SKY, SKY_T, "a safer offset trajectory exists"), ("BRAKE", BRAKE, BRAKE_T, "a road user is entering / in the path"),
          ("NO SAFE PATH", "7F1D1D", BRAKE_T, "every candidate path is blocked → stop")]
for i, (lab, col, tint, desc) in enumerate(ladder):
    y = 7.45 + i * 0.52
    rect(s, L + 0.25, y, 1.75, 0.42, fill=col, radius=0.21)
    text(s, L + 0.25, y, 1.75, 0.42, lab, size=10.5, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, L + 2.15, y, 3.75, 0.42, desc, size=10.5, color=INK, anchor=MSO_ANCHOR.MIDDLE)
card(s, L + 6.3, 6.95, 6.1, 3.2)
picture(s, "tech_fan", os.path.join(SCENES, "planning.png"), L + 6.45, 7.08, 5.8, 2.52, 0.5, 0.5, 1.15)
text(s, L + 6.45, 9.68, 5.8, 0.4, "Candidate trajectories: conflicting paths (red) rejected, safe path (teal) kept — concept illustration",
     size=9.5, italic=True, color=MUTED)
# tech stack
TX = 13.45
section(s, TX, 2.05, 5.95, "TECH STACK", "Technologies to be used (e.g. programming languages, frameworks, hardware)", "tune")
stack = [(["python"], "Python", "core language"), (["opencv"], "OpenCV", "video & vision"),
         (["ultralytics"], "YOLO", "object detection"), (["pytorch"], "PyTorch", "deep-learning runtime"),
         (["icon:timeline"], "ByteTrack", "multi-object tracking"), (["icon:layers"], "Depth Anything V2", "monocular depth"),
         (["numpy"], "NumPy", "planning & geometry"), (["nvidia"], "CUDA", "GPU acceleration"),
         (["flask"], "Flask", "demo web server"), (["html", "css", "javascript"], "HTML / CSS / JS", "review interface")]
for i, (lgs, name, role) in enumerate(stack):
    col, row = i % 2, i // 2
    x, y = TX + col * 3.0, 2.8 + row * 1.47
    card(s, x, y, 2.88, 1.35)
    if len(lgs) == 1:
        lg = lgs[0]
        if lg.startswith("icon:"):
            icon(s, lg[5:], x + 0.2, y + 0.32, 0.7, "w", badge=TEAL if "timeline" in lg else SKY, pad=0.2)
        else:
            logo(s, lg, x + 0.2, y + 0.32, 0.7)
        tx = x + 1.05
    else:
        for j, lg in enumerate(lgs):
            logo(s, lg, x + 0.14 + j * 0.42, y + 0.45, 0.38)
        tx = x + 1.45
    text(s, tx, y + 0.3, x + 2.8 - tx, 0.42, name, size=12.5 if len(name) < 14 else 11, bold=True, color=NAVY)
    text(s, tx, y + 0.72, x + 2.8 - tx, 0.4, role, size=10, color=MUTED)

# ================================================================== 4. PROTOTYPE (real frames from processed demo videos)
s = SL[3]
remove(shp(s, "TextBox 2"))
hdr = deepcopy(header_group(SL[2])._element)
for bf in hdr.iter(qn("a:blipFill")):                       # the header plate is an invisible image; keep it image-free
    parent = bf.getparent(); idx = list(parent).index(bf); parent.remove(bf); parent.insert(idx, etree.Element(qn("a:noFill")))
s.shapes._spTree.append(hdr)
set_first_text(hdr, "PROTOTYPE")
section(s, L, 2.05, 18.8, "FROM CAMERA VIEW TO DECISION", "Actual output frames from the processed demo video — New BEL Road, Bengaluru (consecutive moments)", "camera")
seq = [("seq1_go", "GO STRAIGHT", GO, "1", "Oncoming car stays on its own side — ego path clear, keep going."),
       ("seq2_slow", "SLOW DOWN", SLOW, "2", "Oncoming vehicle starts entering the ego path — threat approaching."),
       ("seq3_brake", "BRAKE", BRAKE, "3", "Pedestrian in the ego path — collision risk, brake.")]
fw = 5.95
fh = fw * 544 / 1280
for i, (fn, lab, col, n, cap) in enumerate(seq):
    x = L + i * (fw + 0.45)
    rect(s, x, 2.78, fw, 0.44, fill=col, radius=0.08)
    text(s, x + 0.15, 2.78, fw - 0.3, 0.44, [[(f"{n}  ", {"bold": True, "color": "FFFFFF", "size": 12}), (lab, {"bold": True, "color": "FFFFFF", "size": 13})]],
         anchor=MSO_ANCHOR.MIDDLE)
    picture(s, f"proto_{fn}", os.path.join(FRAMES, f"{fn}.jpg"), x, 3.26, fw, fh, border=col)
    text(s, x, 3.34 + fh, fw, 0.5, cap, size=11, color=INK)
    if i < 2:
        arrow(s, x + fw + 0.06, 3.26 + fh / 2, x + fw + 0.4, 3.26 + fh / 2, color=NAVY, width=2.5)
# before vs after
section(s, L, 6.55, 9.4, "BEFORE vs AFTER", "Same instant — raw camera frame vs. PathSense output (Nandidurga Road, Bengaluru)", "visibility")
bw = 4.6
bh = bw * 544 / 1280
for i, (fn, lab, col) in enumerate([("before_mixed", "BEFORE — raw camera view", MUTED), ("after_mixed", "AFTER — PathSense: road users tracked, path clear → GO", GO)]):
    x = L + i * (bw + 0.25)
    picture(s, f"proto_{fn}", os.path.join(FRAMES, f"{fn}.jpg"), x, 7.3, bw, bh, border=col)
    text(s, x, 7.36 + bh, bw, 0.45, lab, size=10.5, bold=True, color=col)
# current development
CX = 10.4
card(s, CX, 6.55, 9.0, 3.6, fill=TEAL_TT, line="CFE7E2")
text(s, CX + 0.3, 6.7, 8.4, 0.35, "CURRENT DEVELOPMENT", size=15, bold=True, color=NAVY)
text(s, CX + 0.3, 7.1, 8.4, 0.62, "Camera-based proof-of-concept demonstrating perception, threat assessment and adaptive path decisions on mixed Indian-road scenes.",
     size=12, italic=True, color=TEAL_D)
text(s, CX + 0.3, 7.82, 5.2, 1.6, ["Detects and tracks cars, buses, two-wheelers, auto-rickshaws, pedestrians and animals",
                                    "Separates traffic on its own side from threats entering the ego path",
                                    "Plans around obstacles with candidate trajectories",
                                    "Explains every decision and replays key moments"], size=10.5, bullets=True, space=3)
rect(s, CX + 5.75, 7.85, 2.95, 1.5, fill=CARD, line=LINE, radius=0.1)
logo(s, "github", CX + 5.92, 8.0, 0.42)
text(s, CX + 6.42, 7.98, 2.2, 0.45, "Source code", size=11, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
text(s, CX + 5.92, 8.5, 2.7, 0.75, [[("github.com/MalyalaKarthik66/pathsense-prototype", {"url": REPO, "underline": True, "color": "1155CC"})]], size=9.5)
text(s, CX + 0.3, 9.6, 8.4, 0.45, "Frames are unedited pipeline output, cropped to the camera view. Source footage: L. Shyamal, Wikimedia Commons (CC0). Live demo shown separately.",
     size=9, italic=True, color=MUTED)

# ================================================================== 5. PROOF OF CONCEPT
s = SL[4]
remove(shp(s, "TextBox 5"))
section(s, L, 2.05, 18.8, "PoC PROCESS — KEY STEPS", "Define Problem & Objective · Set Success Criteria · Plan Scope & Resources · Build Prototype / Model · Test & Validate · Decide Next Steps", "flag")
poc = [("warning", "PROBLEM", "Unstructured Indian roads make lane-based autonomous planning difficult."),
       ("flag", "OBJECTIVE", "Understand dynamic road interactions and generate safe adaptive paths."),
       ("bolt", "PROTOTYPE", "Camera-based perception, tracking, motion understanding and path planning."),
       ("science", "TEST & VALIDATE", "Scenario-based evaluation across diverse Indian-road conditions."),
       ("check", "SUCCESS CRITERIA", "Appropriate path selection and risk response under changing traffic conditions."),
       ("rocket", "NEXT STEPS", "Sensor fusion, simulation / closed-loop validation and controlled vehicle integration.")]
pw_, pg = 2.92, 0.26
for i, (ic, t1, t2) in enumerate(poc):
    x = L + i * (pw_ + pg)
    col = PHASE[i + 1] if i < 5 else TEAL_D
    card(s, x, 2.85, pw_, 3.1)
    rect(s, x, 2.85, pw_, 0.9, fill=col, radius=0.12)
    rect(s, x, 3.45, pw_, 0.3, fill=col)
    text(s, x + 0.2, 2.85, 0.6, 0.9, str(i + 1), size=26, bold=True, color="FFFFFF", anchor=MSO_ANCHOR.MIDDLE)
    icon(s, ic, x + pw_ - 0.7, 3.07, 0.46, "w")
    text(s, x + 0.2, 3.95, pw_ - 0.4, 0.4, t1, size=15, bold=True, color=NAVY)
    text(s, x + 0.2, 4.45, pw_ - 0.4, 1.45, t2, size=14, color=INK, line_spacing=1.05)
    if i < 5:
        arrow(s, x + pw_ + 0.02, 3.3, x + pw_ + pg - 0.02, 3.3, color=NAVY, width=2)
section(s, L, 6.3, 18.8, "SCOPE & RESOURCES", "Plan Scope & Resources", "layers")
blocks = [("IN SCOPE — proof of concept", GO, GO_T, ["Camera-based perception of mixed Indian traffic", "Multi-object tracking and short-term motion cues",
                                                     "Ego-path threat assessment and candidate-path planning", "Explainable GO / SLOW / STEER / BRAKE decisions on recorded road video"]),
          ("LATER — pilot / production", SLOW, SLOW_T, ["Vehicle actuation and closed-loop control", "Radar / LiDAR and vehicle-state fusion",
                                                        "Functional-safety engineering (ISO 26262 / SOTIF)", "Controlled field trials at scale"]),
          ("RESOURCES", SKY, SKY_T, ["Open pretrained models (YOLO, ByteTrack, Depth Anything V2)", "Openly licensed Indian road-scene videos",
                                     "Python / PyTorch stack on a laptop-class GPU", "Simulation tools for the pilot stage"])]
for i, (t1, col, tint, pts) in enumerate(blocks):
    x = L + i * 6.35
    card(s, x, 7.0, 6.1, 3.15, fill=tint, line=tint)
    text(s, x + 0.25, 7.18, 5.6, 0.4, t1, size=15, bold=True, color=col)
    text(s, x + 0.25, 7.72, 5.6, 2.35, pts, size=13.5, bullets=True, bullet_color=col, space=7)

# ================================================================== 6. FEASIBILITY AND VIABILITY
s = SL[5]
for n in ("TextBox 16", "TextBox 18", "TextBox 19", "TextBox 20"):
    remove(shp(s, n))
section(s, L, 2.05, 6.0, "FEASIBILITY", "Analysis of the feasibility of the idea", "check")
card(s, L, 2.75, 6.0, 3.65)
feas = [("Technology", "Established computer-vision, object-tracking, depth-estimation and trajectory-planning techniques provide a practical development base."),
        ("Implementation", "Modular perception → prediction → planning architecture enables incremental development and validation."),
        ("Integration", "Can progressively incorporate vehicle-state, radar and LiDAR inputs."),
        ("Validation", "Scenario-based testing progresses from recorded road scenes to simulation and controlled field trials.")]
text(s, L + 0.25, 2.95, 5.55, 3.4, [[(a + ": ", {"bold": True, "color": TEAL_D}), (b, {})] for a, b in feas], size=13, space=9)
section(s, 6.85, 2.05, 6.4, "RISKS → MITIGATION", "Potential challenges and risks · Strategies for overcoming these challenges", "shield")
risks = [("Unstructured road geometry", "Drivable-corridor and ego-path estimation"),
         ("Irregular road-user behaviour", "Temporal tracking and short-term motion reasoning"),
         ("Perception uncertainty", "Confidence-aware decisions and future sensor fusion"),
         ("Real-world validation complexity", "Progressive scenario-based simulation and controlled testing")]
for i, (r_, m_) in enumerate(risks):
    y = 2.75 + i * 0.93
    card(s, 6.85, y, 2.85, 0.82, fill=BRAKE_T, line="F5C9C9")
    text(s, 7.0, y, 2.6, 0.82, r_, size=11, bold=True, color="9B1C1C", anchor=MSO_ANCHOR.MIDDLE)
    arrow(s, 9.72, y + 0.41, 10.05, y + 0.41, color=NAVY, width=2)
    card(s, 10.08, y, 3.17, 0.82, fill=TEAL_T, line="CFE7E2")
    text(s, 10.22, y, 2.95, 0.82, m_, size=10.5, color=INK, anchor=MSO_ANCHOR.MIDDLE)
section(s, 13.5, 2.05, 5.9, "DATA-DRIVEN / OPERATIONAL VIABILITY", None, "map")
card(s, 13.5, 2.75, 5.9, 2.2)
via = [("Data Foundation", "road video, traffic observations, map / context information and future multi-sensor inputs."),
       ("Operational Value", "adaptive navigation in mixed and unstructured road environments."),
       ("Scalability", "prototype → pilot → production deployment pathway.")]
text(s, 13.72, 2.9, 5.5, 2.0, [[(a + ": ", {"bold": True, "color": TEAL_D}), (b, {})] for a, b in via], size=12, space=6)
card(s, 13.5, 5.08, 5.9, 1.32, fill=SKY_T, line=SKY_T)
text(s, 13.72, 5.15, 5.5, 0.28, "WHY IT MATTERS — India, 2022", size=10.5, bold=True, color=SKY)
text(s, 13.72, 5.45, 5.5, 0.5, [[("4,61,312", {"bold": True, "size": 20, "color": NAVY}), ("  road accidents   ", {"size": 11}),
                                 ("1,68,491", {"bold": True, "size": 20, "color": BRAKE}), ("  deaths", {"size": 11})]])
text(s, 13.72, 6.02, 5.5, 0.3, "Source: MoRTH, Road Accidents in India 2022", size=9, italic=True, color=MUTED)
section(s, L, 6.65, 12.6, "PROTOTYPE → PILOT → PRODUCTION", "Development roadmap", "rocket")
rm = [("PROTOTYPE", "Camera-based proof-of-concept for perception, tracking and adaptive path planning."),
      ("PILOT", "Broader scenario validation, sensor expansion, simulation and controlled road trials."),
      ("PRODUCTION", "Vehicle-grade multi-sensor perception, closed-loop validation and automotive safety engineering.")]
cw3, ov = 4.35, 0.35
for i, (t1, t2) in enumerate(rm):
    x = L + i * (cw3 - ov + 0.12)
    sh = s.shapes.add_shape(MSO_SHAPE.PENTAGON if i == 0 else MSO_SHAPE.CHEVRON, Inches(x), Inches(7.35), Inches(cw3), Inches(2.75))
    sh.fill.solid(); sh.fill.fore_color.rgb = rgb([SKY, "118F95", TEAL_D][i]); sh.line.fill.background(); sh.shadow.inherit = False
    sh.adjustments[0] = 0.13
    pad = 0.25 if i == 0 else ov + 0.2
    text(s, x + pad, 7.6, cw3 - pad - ov - 0.1, 0.45, t1, size=18, bold=True, color="FFFFFF")
    text(s, x + pad, 8.15, cw3 - pad - ov - 0.15, 1.85, t2, size=13, color="FFFFFF", line_spacing=1.05)
section(s, 13.5, 6.65, 5.9, "POTENTIAL COLLABORATIONS", "Industry · academia · government", "science")
collab = [("car", "Automotive OEMs & Tier-1 suppliers"), ("science", "Research labs (IITs, IISc)"), ("layers", "Simulation-tool partners (e.g. MathWorks)"),
          ("shield", "Transport & road-safety departments"), ("bus", "Fleet & public-transport operators"), ("rocket", "Mobility startups")]
for i, (ic, t1) in enumerate(collab):
    col, row = i % 2, i // 2
    x, y = 13.5 + col * 2.98, 7.35 + row * 0.93
    card(s, x, y, 2.9, 0.82)
    icon(s, ic, x + 0.12, y + 0.17, 0.48, "w", badge=TEAL if (col + row) % 2 == 0 else SKY, pad=0.2)
    text(s, x + 0.7, y, 2.12, 0.82, t1, size=10, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)

# ================================================================== 7. IMPACT AND BENEFITS
s = SL[6]
for n in ("TextBox 17", "TextBox 18"):
    remove(shp(s, n))
section(s, L, 2.05, 10.6, "POTENTIAL IMPACTS — TARGET AUDIENCE", "Potential impact on the target audience", "flag")
aud = [("car", "Passengers / Drivers", "Safer interaction with unpredictable road users.", "corridor", 0.45, 0.55, 1.3),
       ("bus", "Urban Mobility", "Adaptive navigation in dense mixed traffic.", "market", 0.5, 0.45, 1.3),
       ("map", "Rural Mobility", "Improved handling of roads without reliable lane markings.", "village", 0.6, 0.7, 1.5),
       ("layers", "Automotive / Mobility Industry", "Foundation for Indian-road-aware autonomous systems.", "perception", 0.5, 0.5, 1.4),
       ("science", "Research & Academia", "Platform for studying unstructured-road perception and planning.", "planning", 0.5, 0.5, 1.2)]
for i, (ic, t1, t2, src, fx, fy, z) in enumerate(aud):
    y = 2.78 + i * 1.48
    card(s, L, y, 10.6, 1.36)
    icon(s, ic, L + 0.22, y + 0.33, 0.7, "w", badge=[SKY, TEAL, "118F95", PHASE[1], TEAL_D][i], pad=0.2)
    text(s, L + 1.15, y + 0.2, 6.7, 0.42, t1, size=16, bold=True, color=NAVY)
    text(s, L + 1.15, y + 0.67, 6.7, 0.6, t2, size=13.5, color=INK)
    picture(s, f"aud_{i}", os.path.join(SCENES, f"{src}.png"), L + 8.1, y + 0.12, 2.35, 1.12, fx, fy, z, border=None)
text(s, L + 8.1, 10.18, 2.35, 0.14, "concept illustrations", size=7.5, italic=True, color=MUTED, align=PP_ALIGN.RIGHT)
BX = 11.5
section(s, BX, 2.05, 7.9, "BENEFITS", "Benefits of the solution (social, economic, environmental, etc.)", "check")
ben = [("shield", "SAFETY", "Better recognition of potential ego-path conflicts.", GO),
       ("loop", "ADAPTABILITY", "Responds to changing traffic and road conditions.", SKY),
       ("rocket", "SCALABILITY", "Prototype → pilot → production pathway.", "118F95"),
       ("map", "INDIAN-ROAD RELEVANCE", "Designed around mixed and irregular traffic behaviour.", TEAL_D)]
for i, (ic, t1, t2, col) in enumerate(ben):
    c_, r_ = i % 2, i // 2
    x, y = BX + c_ * 4.0, 2.78 + r_ * 2.12
    card(s, x, y, 3.88, 1.98)
    icon(s, ic, x + 0.22, y + 0.22, 0.62, "w", badge=col, pad=0.2)
    text(s, x + 0.98, y + 0.22, 2.8, 0.62, t1, size=13, bold=True, color=col, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.22, y + 1.0, 3.45, 0.9, t2, size=14, color=INK)
card(s, BX, 7.1, 7.88, 3.06, fill=TEAL_TT, line="CFE7E2")
text(s, BX + 0.25, 7.25, 7.4, 0.35, "SOCIAL · ECONOMIC · ENVIRONMENTAL", size=14, bold=True, color=TEAL_D)
text(s, BX + 0.25, 7.75, 7.4, 2.35, [
    [("Social: ", {"bold": True, "color": NAVY}), ("safer shared roads for pedestrians, cyclists and two-wheelers.", {})],
    [("Economic: ", {"bold": True, "color": NAVY}), ("fewer conflicts for fleets and home-grown driver-assistance capability.", {})],
    [("Environmental: ", {"bold": True, "color": NAVY}), ("smoother, anticipatory driving with fewer abrupt stops (expected).", {})]],
    size=13.5, space=12)

# ================================================================== 8. BUSINESS MODEL CANVAS (required by the official format)
s = SL[7]
remove(shp(s, "Group 5"))
rect(s, 0, 1.97, 20.0, 8.23, fill="E3E3E3")
text(s, 0.75, 2.2, 7.0, 0.6, "The Business Model Canvas — PathSense", size=24, color="111111")
for x, w, lab, val in [(8.3, 3.7, "Designed for:", "PathSense"), (12.15, 3.55, "Designed by:", TEAM_NAME),
                       (15.85, 1.7, "Date:", "2026"), (17.7, 1.6, "Version:", "Idea stage")]:
    rect(s, x, 2.2, w, 0.6, fill="FFFFFF")
    text(s, x + 0.08, 2.24, w - 0.1, 0.22, lab, size=8.5, bold=True, color="111111")
    text(s, x + 0.08, 2.46, w - 0.1, 0.3, val, size=11, color=NAVY)
GX0, GY0, GW, GH_TOP, GH_BOT = 0.7, 3.0, 18.6, 5.05, 2.08
colw = GW / 5
bmc = [  # (x, y, w, h, title, icon, items)
    (0, 0, 1, 1, "Key Partners", "loop", ["Automotive OEMs", "Mobility companies", "Research institutions (IITs, IISc)", "Sensor / perception providers",
                                           "Simulation platforms (e.g. MathWorks)", "Government / transport organisations"]),
    (1, 0, 1, 0.5, "Key Activities", "bolt", ["Perception development", "Motion prediction", "Path planning", "Scenario validation", "Sensor integration"]),
    (1, 0.5, 1, 0.5, "Key Resources", "layers", ["AI / ML models", "Indian road datasets", "Simulation environments", "Engineering team"]),
    (2, 0, 1, 1, "Value Propositions", "shield", ["Adaptive planning for unstructured roads", "Indian mixed-traffic awareness", "Safety-oriented path decisions",
                                                   "Explainable: every decision has a reason", "Scalable autonomous-navigation architecture"]),
    (3, 0, 1, 0.5, "Customer Relationships", "check", ["Co-development pilots", "Integration & tuning support", "Continuous model and scenario updates"]),
    (3, 0.5, 1, 0.5, "Channels", "truck", ["Direct B2B partnerships", "Pilot deployments with fleets", "Research collaborations", "Licensable SDK / API"]),
    (4, 0, 1, 1, "Customer Segments", "car", ["Automotive manufacturers", "Autonomous mobility companies", "Fleet operators", "Research organisations",
                                               "Smart mobility programmes"]),
]
for c, r, wc, hr, title, ic, items in bmc:
    x, y, w, h = GX0 + c * colw, GY0 + r * GH_TOP, wc * colw, hr * GH_TOP
    rect(s, x, y, w, h, fill="FFFFFF", line="111111", lw=1.5)
    text(s, x + 0.18, y + 0.15, w - 0.8, 0.35, title, size=14, bold=True, color="111111")
    icon(s, ic, x + w - 0.58, y + 0.12, 0.4, "n")
    text(s, x + 0.18, y + 0.62, w - 0.32, h - 0.7, items, size=12.5, bullets=True, bullet_color=TEAL, space=5)
for i, (title, ic, items) in enumerate([("Cost Structure", "tune", ["Engineering R&D", "Data collection & annotation", "Compute & simulation infrastructure",
                                                                    "Validation & safety engineering"]),
                                        ("Revenue Streams", "rocket", ["Software licensing to OEMs", "Pilot / integration contracts",
                                                                       "Scenario-library & validation services", "Support & update subscriptions"])]):
    x, y, w, h = GX0 + i * GW / 2, GY0 + GH_TOP, GW / 2, GH_BOT
    rect(s, x, y, w, h, fill="FFFFFF", line="111111", lw=1.5)
    text(s, x + 0.18, y + 0.12, w - 0.8, 0.35, title, size=14, bold=True, color="111111")
    icon(s, ic, x + w - 0.58, y + 0.1, 0.4, "n")
    half = (len(items) + 1) // 2
    for j, chunk in enumerate((items[:half], items[half:])):
        text(s, x + 0.18 + j * (w - 0.3) / 2, y + 0.58, (w - 0.4) / 2, h - 0.6, chunk, size=12.5, bullets=True, bullet_color=TEAL, space=5)

# ================================================================== 9. NATIONAL SCHEME ALIGNMENTS
s = SL[8]
remove(shp(s, "TextBox 5"))
section(s, L, 2.05, 18.8, "GOVERNMENT / NATIONAL ALIGNMENT", "Connect your solution with national missions", "flag")
missions = [("web", "Digital India", "Software-first, camera-based mobility intelligence built on open digital tooling.", SKY),
            ("bus", "Smart Cities Mission", "Adaptive navigation for dense, mixed urban traffic and future smart-mobility services.", TEAL),
            ("rocket", "Atmanirbhar Bharat", "Indigenous autonomous-driving technology designed for Indian road conditions.", "E8731A"),
            ("loop", "Swachh Bharat / Green India", "Smoother, anticipatory driving with fewer abrupt stops — an expected efficiency benefit.", GO),
            ("brain", "AI for India 2.0", "Applied AI solving an India-specific mobility and road-safety problem.", PHASE[1]),
            ("shield", "National Road Safety (MoRTH)", "Earlier recognition of ego-path conflicts with pedestrians, cyclists and two-wheelers.", BRAKE)]
for i, (ic, t1, t2, col) in enumerate(missions):
    c_, r_ = i % 3, i // 3
    x, y = L + c_ * 6.33, 2.85 + r_ * 3.7
    card(s, x, y, 6.1, 3.45)
    icon(s, ic, x + 0.3, y + 0.35, 0.95, "w", badge=col, pad=0.2, round_=True)
    text(s, x + 1.5, y + 0.35, 4.4, 0.95, t1, size=20, bold=True, color=col, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.3, y + 1.55, 5.5, 0.3, "How PathSense aligns", size=12, bold=True, color=MUTED)
    text(s, x + 0.3, y + 1.95, 5.5, 1.4, t2, size=16, color=INK, line_spacing=1.05)

# ================================================================== 10. RESEARCH AND REFERENCES
s = SL[9]
remove(shp(s, "TextBox 17"))
section(s, L, 2.05, 18.8, "SUPPORTING RESEARCH & SOURCES", "Details / Links of the reference and research work", "science")
refs = [("route", "AUTONOMOUS DRIVING", "Paden et al., “A Survey of Motion Planning and Control Techniques for Self-Driving Urban Vehicles”, IEEE T-IV, 2016", "arxiv.org/abs/1604.07446"),
        ("warning", "OBJECT DETECTION", "Redmon et al., “You Only Look Once: Unified, Real-Time Object Detection”, CVPR, 2016", "arxiv.org/abs/1506.02640"),
        ("timeline", "MULTI-OBJECT TRACKING", "Zhang et al., “ByteTrack: Multi-Object Tracking by Associating Every Detection Box”, ECCV, 2022", "arxiv.org/abs/2110.06864"),
        ("layers", "MONOCULAR DEPTH", "Yang et al., “Depth Anything V2”, NeurIPS, 2024", "arxiv.org/abs/2406.09414"),
        ("brain", "MOTION PREDICTION", "Alahi et al., “Social LSTM: Human Trajectory Prediction in Crowded Spaces”, CVPR, 2016", "openaccess.thecvf.com"),
        ("map", "INDIAN ROAD PERCEPTION", "Varma et al., “IDD: A Dataset for Exploring Problems of Autonomous Navigation in Unconstrained Environments”, WACV, 2019", "idd.insaan.iiit.ac.in"),
        ("flag", "OFFICIAL SOURCE", "Smart India Hackathon 2026 — Problem Statement SIH26037: " + PS_TITLE, "sih.gov.in"),
        ("shield", "ROAD-SAFETY DATA & FOOTAGE", "MoRTH, “Road Accidents in India 2022”; demo road videos: L. Shyamal, Wikimedia Commons (CC0)", "morth.nic.in · commons.wikimedia.org")]
for i, (ic, cat, cit, url) in enumerate(refs):
    c_, r_ = i % 4, i // 4
    x, y = L + c_ * 4.75, 2.85 + r_ * 3.7
    card(s, x, y, 4.55, 3.45)
    icon(s, ic, x + 0.22, y + 0.25, 0.55, "w", badge=TEAL if (c_ + r_) % 2 == 0 else SKY, pad=0.2)
    text(s, x + 0.92, y + 0.25, 3.5, 0.55, cat, size=13, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.22, y + 1.0, 4.1, 1.9, cit, size=14, color=INK, line_spacing=1.05)
    first = url.split(" · ")[0]
    text(s, x + 0.22, y + 2.95, 4.1, 0.35, [[(url, {"url": "https://" + first, "underline": True, "color": "1155CC"})]], size=11.5)

# drop relationships to pictures that were removed
for s in SL:
    xml = etree.tostring(s._element).decode()
    for rId, rel in list(s.part.rels.items()):
        if rel.reltype.endswith("/image") and f'"{rId}"' not in xml:
            s.part.drop_rel(rId)

prs.save(OUTFILE)
print("saved", OUTFILE, len(prs.slides), "slides")
