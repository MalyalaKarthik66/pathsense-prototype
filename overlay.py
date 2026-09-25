"""
PathSense - Cockpit HUD compositor
Module: overlay.py

Renders the PathSense HUD on each frame, using the same design system as the web app (web/static/app.css):
  - top bar: PathSense wordmark, frame / processing rate, research-prototype tag, decision pill
  - detection boxes: red = critical object inside the ego corridor, orange = closing but outside the corridor,
    amber = caution, green = monitored; the object responsible for the decision is outlined in white
  - picture-in-picture BEV with candidate trajectories and the selected path
  - bottom panel: speed, recommended steering, path status / threats, decision + reason + responsible object
  - decision-history strip over the whole video
  - accident overlay (POSSIBLE collision chip, CONFIRMED card with the SIMULATED emergency workflow)
Every safety state is written as text (never colour alone). Text uses DejaVu Sans (shipped with matplotlib),
drawn in a single PIL pass per frame.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
    import matplotlib
    _FONT_DIR = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    _HAS_PIL = os.path.isfile(os.path.join(_FONT_DIR, "DejaVuSans.ttf"))
except Exception:  # pragma: no cover
    _HAS_PIL = False

# ---- design tokens (BGR) - keep in sync with web/static/app.css
C_BG = (20, 17, 14)          # #0e1114
C_SURFACE = (34, 29, 24)     # #181d22
C_LINE = (70, 62, 54)
C_TEXT = (245, 243, 240)
C_MUTED = (190, 180, 166)    # #a6b4be
C_ACCENT = (191, 212, 45)    # #2dd4bf teal (brand)
C_GO = (94, 197, 34)         # #22c55e
C_SLOW = (11, 158, 245)      # #f59e0b
C_BRAKE = (68, 68, 239)      # #ef4444
C_NSP = (28, 28, 185)        # #b91c1c
C_STEER = (248, 189, 56)     # #38bdf8
C_ORANGE = (22, 115, 249)    # #f97316

DECISION_COLORS = {"GO": C_GO, "CAUTION": C_SLOW, "BRAKE": C_BRAKE}
LABEL_COLORS = {"GO STRAIGHT": C_GO, "STEER LEFT": C_STEER, "STEER RIGHT": C_STEER,
                "SLOW DOWN": C_SLOW, "BRAKE": C_BRAKE, "NO SAFE PATH - BRAKE": C_NSP}
PATH_COLORS = {"CLEAR": C_GO, "PARTIAL": C_SLOW, "BLOCKED": C_BRAKE}
TOP_BAR_H = 60
BOTTOM_BAR_H = 104


def fit_text(text: str, scale: float, thickness: int, max_w: int) -> str:
    """Truncates text with '...' so it fits within max_w pixels (Hershey font metrics)."""
    if cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0][0] <= max_w:
        return text
    while text and cv2.getTextSize(text + "...", cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0][0] > max_w:
        text = text[:-1]
    return text + "..."


class _Text:
    """Collects text draw calls and renders them in one PIL pass (anti-aliased TrueType)."""
    _fonts: Dict[Tuple[int, bool], Any] = {}

    def __init__(self):
        self.ops = []

    @classmethod
    def font(cls, size: int, bold: bool = False):
        key = (size, bold)
        if key not in cls._fonts:
            cls._fonts[key] = ImageFont.truetype(os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), size)
        return cls._fonts[key]

    def width(self, text: str, size: int, bold: bool = False) -> int:
        if _HAS_PIL:
            return int(self.font(size, bold).getlength(text))
        return cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, size / 30.0, 2 if bold else 1)[0][0]

    def fit(self, text: str, size: int, max_w: int, bold: bool = False) -> str:
        if self.width(text, size, bold) <= max_w:
            return text
        while text and self.width(text + "…", size, bold) > max_w:
            text = text[:-1]
        return text + "…"

    def add(self, xy: Tuple[int, int], text: str, size: int, color, bold: bool = False):
        """xy = top-left of the text box."""
        self.ops.append((xy, text, size, color, bold))

    def flush(self, canvas: np.ndarray) -> np.ndarray:
        if not self.ops:
            return canvas
        if not _HAS_PIL:
            for (x, y), text, size, color, bold in self.ops:
                cv2.putText(canvas, text, (x, y + size), cv2.FONT_HERSHEY_SIMPLEX, size / 30.0, color, 2 if bold else 1, cv2.LINE_AA)
            return canvas
        img = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))  # contiguous -> fast copy
        draw = ImageDraw.Draw(img)
        for (x, y), text, size, color, bold in self.ops:
            draw.text((x, y), text, font=self.font(size, bold), fill=(color[2], color[1], color[0]))
        return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def _rrect(canvas, p1, p2, color, r=8, thickness=-1):
    """Rounded rectangle (filled or outline)."""
    x1, y1 = p1; x2, y2 = p2
    r = max(0, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    if thickness < 0:
        cv2.rectangle(canvas, (x1 + r, y1), (x2 - r, y2), color, -1)
        cv2.rectangle(canvas, (x1, y1 + r), (x2, y2 - r), color, -1)
        for cx, cy in ((x1 + r, y1 + r), (x2 - r, y1 + r), (x1 + r, y2 - r), (x2 - r, y2 - r)):
            cv2.circle(canvas, (cx, cy), r, color, -1, cv2.LINE_AA)
    else:
        cv2.line(canvas, (x1 + r, y1), (x2 - r, y1), color, thickness, cv2.LINE_AA)
        cv2.line(canvas, (x1 + r, y2), (x2 - r, y2), color, thickness, cv2.LINE_AA)
        cv2.line(canvas, (x1, y1 + r), (x1, y2 - r), color, thickness, cv2.LINE_AA)
        cv2.line(canvas, (x2, y1 + r), (x2, y2 - r), color, thickness, cv2.LINE_AA)
        for (cx, cy), a in (((x1 + r, y1 + r), 180), ((x2 - r, y1 + r), 270), ((x2 - r, y2 - r), 0), ((x1 + r, y2 - r), 90)):
            cv2.ellipse(canvas, (cx, cy), (r, r), a, 0, 90, color, thickness, cv2.LINE_AA)


def _blend_rect(canvas, p1, p2, color, alpha):
    x1, y1 = max(0, p1[0]), max(0, p1[1]); x2, y2 = min(canvas.shape[1], p2[0]), min(canvas.shape[0], p2[1])
    if x2 <= x1 or y2 <= y1:
        return
    roi = canvas[y1:y2, x1:x2]
    cv2.addWeighted(roi, 1.0 - alpha, np.full_like(roi, color), alpha, 0, dst=roi)


class HUDOverlay:
    def __init__(self, pip_size: Optional[Tuple[int, int]] = None, pip_margin: int = 16, dark_theme: bool = True):
        # pip_size=None -> sized from the frame (about 45% of the height), so any input resolution fits
        self.pip_size = pip_size
        self.margin = pip_margin
        self.dark = dark_theme
        self._timeline: Optional[np.ndarray] = None  # per-frame decision colours for the history strip
        self._acc_confirmed_at: Optional[int] = None

    def _pip_side(self, w: int, h: int) -> int:
        top, bottom = TOP_BAR_H + self.margin + 22, BOTTOM_BAR_H + 12 + self.margin
        side = self.pip_size[0] if self.pip_size else int(round(0.47 * h))
        return int(max(0, min(side, h - top - bottom, int(0.45 * w))))

    @staticmethod
    def min_canvas_size(width: int, height: int) -> Tuple[int, int]:
        """Output size for a given input: small inputs are upscaled (aspect kept) so the HUD layout fits (>= 1280x720)."""
        scale = max(1.0, 1280.0 / width, 720.0 / height)
        return int(round(width * scale)) // 2 * 2, int(round(height * scale)) // 2 * 2

    # ------------------------------------------------------------------------------------------------ main
    def render_hud_frame(
        self,
        frame_bgr: np.ndarray,
        tracked_objects: List[Dict[str, Any]],
        bev_image: np.ndarray,
        speed_kmh: float,
        steering_deg: float,
        pos_x: float,
        pos_y: float,
        frame_idx: int,
        total_frames: int,
        fps: float,
        planner_blocked: bool = False,
        decision: Optional[Dict[str, Any]] = None,
        accident: Optional[Dict[str, Any]] = None
    ) -> np.ndarray:
        src_h, src_w = frame_bgr.shape[:2]
        out_w, out_h = self.min_canvas_size(src_w, src_h)
        if (out_w, out_h) != (src_w, src_h):
            canvas = cv2.resize(frame_bgr, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
            sx, sy = out_w / src_w, out_h / src_h
            tracked_objects = [{**o, "bbox": [o["bbox"][0] * sx, o["bbox"][1] * sy, o["bbox"][2] * sx, o["bbox"][3] * sy]}
                               for o in tracked_objects if o.get("bbox") is not None]
        else:
            canvas = frame_bgr.copy()
        h, w = canvas.shape[:2]
        tx = _Text()
        if decision is None:  # legacy callers: derive a minimal decision from the planner flag
            label = "NO SAFE PATH - BRAKE" if planner_blocked else ("GO STRAIGHT" if abs(steering_deg) < 4 else
                                                                     ("STEER LEFT" if steering_deg < 0 else "STEER RIGHT"))
            decision = {"state": "BRAKE" if planner_blocked else "GO", "label": label, "title": "", "reason": ""}
        key_id, dec_state = decision.get("key_track_id"), decision.get("state")

        self._draw_objects(canvas, tx, tracked_objects, key_id, dec_state)
        self._draw_pip(canvas, tx, bev_image, w, h)
        self._draw_top_bar(canvas, tx, w, frame_idx, total_frames, fps, decision)
        by1 = h - BOTTOM_BAR_H
        self._draw_timeline(canvas, by1, frame_idx, total_frames, decision["label"])
        self._draw_bottom(canvas, tx, w, h, by1, speed_kmh, steering_deg, decision)
        self._draw_accident(canvas, tx, w, h, frame_idx, accident)
        return tx.flush(canvas)

    # ------------------------------------------------------------------------------------------------ parts
    def _draw_objects(self, canvas, tx, objects, key_id, dec_state):
        for obj in objects:
            tid = obj.get("track_id", -1)
            x1, y1, x2, y2 = [int(v) for v in obj.get("bbox", [0, 0, 0, 0])]
            hazard = obj.get("hazard_level", "SAFE")
            in_path = obj.get("in_corridor", True)
            critical_in_path = hazard == "CRITICAL" and in_path
            if critical_in_path:
                col = C_BRAKE
            elif hazard == "CRITICAL":
                col = C_ORANGE
            elif hazard == "CAUTION":
                col = C_SLOW
            else:
                col = C_GO
            is_key = key_id is not None and tid == key_id and dec_state in ("CAUTION", "BRAKE")
            cv2.rectangle(canvas, (x1, y1), (x2, y2), col, 3 if critical_in_path else 2, cv2.LINE_AA)
            if is_key:
                _rrect(canvas, (x1 - 5, y1 - 5), (x2 + 5, y2 + 5), C_TEXT, r=6, thickness=2)
            dist = obj.get("smoothed_depth_m", obj.get("estimated_depth_m", 15.0))
            ttc = obj.get("ttc_sec")
            label = f"{obj.get('display_class', obj.get('class_name', 'object'))} #{tid} · {dist:.1f} m"
            if ttc is not None:
                label += f" · TTC {ttc:.1f}s"
            beh = obj.get("behavior")
            if beh and beh != "SIDE PASS":
                label += f" · {beh}"
            size = 13
            tw = tx.width(label, size) + 12
            ty1 = max(TOP_BAR_H, y1 - 22)
            _rrect(canvas, (x1, ty1), (x1 + tw, ty1 + 20), col, r=5)
            tx.add((x1 + 6, ty1 + 2), label, size, C_TEXT if critical_in_path or col == C_ORANGE else (15, 15, 15))
            if is_key:
                tag = beh if beh in ("CUT-IN", "CROSSING", "ONCOMING") else ("COLLISION RISK" if dec_state == "BRAKE" else "PATH THREAT")
                tw2 = tx.width(tag, 13, True) + 14
                cx = x1 + max(0, (x2 - x1 - tw2) // 2)
                _rrect(canvas, (cx, y1 + 6), (cx + tw2, y1 + 28), C_BRAKE if dec_state == "BRAKE" else C_SLOW, r=6)
                tx.add((cx + 7, y1 + 9), tag, 13, C_TEXT if dec_state == "BRAKE" else (15, 15, 15), bold=True)

    def _draw_pip(self, canvas, tx, bev_image, w, h):
        side = self._pip_side(w, h)
        if side < 60 or bev_image is None or bev_image.size == 0:
            return
        pip = cv2.resize(bev_image, (side, side), interpolation=cv2.INTER_AREA)
        px1, py1 = w - side - self.margin, TOP_BAR_H + self.margin + 22
        _blend_rect(canvas, (px1 - 6, py1 - 28), (px1 + side + 6, py1 + side + 6), C_BG, 0.85)
        canvas[py1:py1 + side, px1:px1 + side] = pip
        _rrect(canvas, (px1 - 1, py1 - 1), (px1 + side, py1 + side), C_LINE, r=4, thickness=1)
        tx.add((px1, py1 - 24), "BEV · CANDIDATE TRAJECTORIES", 13, C_MUTED, bold=True)

    def _draw_top_bar(self, canvas, tx, w, frame_idx, total_frames, fps, d):
        _blend_rect(canvas, (0, 0), (w, TOP_BAR_H), C_BG, 0.88)
        cv2.line(canvas, (0, TOP_BAR_H), (w, TOP_BAR_H), C_LINE, 1)
        cv2.circle(canvas, (26, 30), 7, C_ACCENT, -1, cv2.LINE_AA)
        tx.add((42, 10), "PathSense", 22, C_TEXT, bold=True)
        tx.add((42 + tx.width("PathSense", 22, True) + 12, 17), "Adaptive Path Planning & Collision Avoidance", 13, C_MUTED)
        info = f"FRAME {frame_idx:04d}/{total_frames:04d}   ·   {fps:4.1f} FPS   ·   RESEARCH PROTOTYPE"
        # decision pill (text + colour)
        label = d["label"]
        pill = label if d["state"] != "CAUTION" else "SLOW DOWN"
        pw = tx.width(pill, 18, True) + 36
        col = LABEL_COLORS.get(label, C_GO)
        _rrect(canvas, (w - pw - 16, 12), (w - 16, TOP_BAR_H - 12), col, r=18)
        tx.add((w - pw - 16 + 18, 17), pill, 18, C_TEXT if col in (C_BRAKE, C_NSP) else (15, 15, 15), bold=True)
        iw = tx.width(info, 12)
        tx.add((w - pw - 40 - iw, 22), info, 12, C_MUTED)

    def _draw_timeline(self, canvas, by1, frame_idx, total_frames, label):
        """Thin decision-history strip above the bottom panel (whole video mapped to the frame width)."""
        if not total_frames or total_frames <= 0:
            return
        h, w = canvas.shape[:2]
        if self._timeline is None or len(self._timeline) != total_frames:
            self._timeline = np.zeros((total_frames, 3), np.uint8)
            self._timeline[:] = (50, 45, 40)
        if 0 <= frame_idx < total_frames:
            self._timeline[frame_idx] = LABEL_COLORS.get(label, (128, 128, 128))
        strip = cv2.resize(self._timeline[None, :, :], (w, 5), interpolation=cv2.INTER_NEAREST)
        canvas[by1 - 6:by1 - 1, 0:w] = strip
        cx = int((frame_idx + 0.5) / total_frames * w)
        cv2.line(canvas, (cx, by1 - 10), (cx, by1 - 1), C_TEXT, 2)

    def _section_label(self, tx, x, y, text):
        tx.add((x, y), text, 11, C_MUTED, bold=True)

    def _draw_bottom(self, canvas, tx, w, h, by1, speed_kmh, steering_deg, d):
        _blend_rect(canvas, (0, by1), (w, h), C_BG, 0.9)
        cv2.line(canvas, (0, by1), (w, by1), C_LINE, 1)
        y0 = by1 + 12
        # 1. speed
        x = 28
        self._section_label(tx, x, y0, "EGO SPEED")
        tx.add((x, y0 + 18), f"{speed_kmh:.0f}", 34, C_TEXT, bold=True)
        tx.add((x + tx.width(f"{speed_kmh:.0f}", 34, True) + 6, y0 + 36), "km/h", 14, C_MUTED)
        tx.add((x, y0 + 64), "monocular estimate", 11, C_MUTED)
        # 2. steering
        x = 190
        cv2.line(canvas, (x - 20, by1 + 16), (x - 20, h - 14), C_LINE, 1)
        self._section_label(tx, x, y0, "RECOMMENDED STEERING")
        direction = "CENTER" if abs(steering_deg) < 1.5 else ("RIGHT" if steering_deg > 0 else "LEFT")
        scol = C_TEXT if abs(steering_deg) < 4 else C_STEER
        val = f"{steering_deg:+.1f}°"
        tx.add((x, y0 + 18), val, 30, scol, bold=True)
        tx.add((x + tx.width(val, 30, True) + 8, y0 + 34), direction, 14, C_MUTED, bold=True)
        cx, cy, r = x + 205, by1 + 52, 26
        cv2.circle(canvas, (cx, cy), r, C_SURFACE, -1, cv2.LINE_AA)
        cv2.circle(canvas, (cx, cy), r, C_LINE, 2, cv2.LINE_AA)
        a = np.radians(steering_deg * 3.0)  # exaggerated for legibility
        cv2.line(canvas, (cx, cy), (int(cx + (r - 6) * np.sin(a)), int(cy - (r - 6) * np.cos(a))), scol, 3, cv2.LINE_AA)
        cv2.circle(canvas, (cx, cy), 4, C_TEXT, -1, cv2.LINE_AA)
        # 3. path status + threats
        x = 460
        cv2.line(canvas, (x - 20, by1 + 16), (x - 20, h - 14), C_LINE, 1)
        self._section_label(tx, x, y0, "PATH")
        path = d.get("path_status") or "CLEAR"
        chip = path + (f"  {d['blocked_arcs']}/{d['n_arcs']}" if path == "PARTIAL" and d.get("n_arcs") else "")
        cw = tx.width(chip, 15, True) + 22
        pcol = PATH_COLORS.get(path, C_GO)
        _rrect(canvas, (x, y0 + 20), (x + cw, y0 + 46), pcol, r=13)
        tx.add((x + 11, y0 + 24), chip, 15, C_TEXT if path == "BLOCKED" else (15, 15, 15), bold=True)
        thr = d.get("threats", 0) or 0
        tx.add((x, y0 + 56), f"{thr} threat{'s' if thr != 1 else ''} to ego path", 13, C_TEXT if thr else C_MUTED)
        # 4. decision
        x = 680
        cv2.line(canvas, (x - 20, by1 + 16), (x - 20, h - 14), C_LINE, 1)
        avail = w - x - 24
        self._section_label(tx, x, y0, "DECISION")
        label = d["label"]
        lcol = LABEL_COLORS.get(label, C_TEXT)
        cv2.circle(canvas, (x + 7, y0 + 34), 7, lcol, -1, cv2.LINE_AA)
        tx.add((x + 22, y0 + 18), tx.fit(label, 26, avail - 22, True), 26, lcol if lcol != C_NSP else C_BRAKE, bold=True)
        title = d.get("title") or ""
        if d.get("holding") and label != "NO SAFE PATH - BRAKE" and not title.lower().startswith(("holding", "clearing")):
            title = f"{title} (holding)"
        tx.add((x, y0 + 52), tx.fit(title, 15, avail), 15, C_TEXT)
        k = d.get("key")
        if label == "NO SAFE PATH - BRAKE":
            det = (f"{d.get('blocked_arcs')}/{d.get('n_arcs')} candidate trajectories blocked" if d.get("blocked_now", True)
                   else f"clearing · {d.get('blocked_arcs')}/{d.get('n_arcs')} blocked now")
            if k and k.get("class_name") and k.get("distance_m") is not None:
                det += f" · nearest {k['class_name']} {k['distance_m']:.1f} m"
        elif k and k.get("class_name"):
            det = f"{k['class_name']} #{k.get('track_id')} · {k['distance_m']:.1f} m · " + \
                  (f"TTC {k['ttc_s']:.1f} s" if k.get("ttc_s") is not None else "TTC n/a")
            if k.get("behavior") and k["behavior"] != "SIDE PASS":
                det += f" · {k['behavior']}"
        else:
            det = d.get("reason", "") or ""
        tx.add((x, y0 + 72), tx.fit(det, 13, avail), 13, C_MUTED)

    def _draw_accident(self, canvas, tx, w, h, frame_idx, acc):
        if not acc or acc.get("state") in (None, "NORMAL"):
            self._acc_confirmed_at = None if not acc else self._acc_confirmed_at
            return
        if acc["state"] == "POSSIBLE":
            text = f"POSSIBLE COLLISION · confirming ({int(100 * acc.get('confidence', 0))}%)"
            tw = tx.width(text, 15, True) + 28
            _rrect(canvas, (16, TOP_BAR_H + 12), (16 + tw, TOP_BAR_H + 44), C_SLOW, r=16)
            tx.add((30, TOP_BAR_H + 18), text, 15, (15, 15, 15), bold=True)
            return
        if self._acc_confirmed_at is None:
            self._acc_confirmed_at = frame_idx
        conf = int(100 * acc.get("confidence", 0))
        if frame_idx - self._acc_confirmed_at > 240:  # after ~8 s keep a compact badge so the video stays visible
            text = f"ACCIDENT EVENT LOGGED · {conf}% · emergency workflow SIMULATED"
            tw = tx.width(text, 14, True) + 28
            _rrect(canvas, (16, TOP_BAR_H + 12), (16 + tw, TOP_BAR_H + 42), C_BRAKE, r=15)
            tx.add((30, TOP_BAR_H + 18), text, 14, C_TEXT, bold=True)
            return
        x1, y1, cw, chh = 24, TOP_BAR_H + 20, 520, 236
        _blend_rect(canvas, (x1, y1), (x1 + cw, y1 + chh), C_BG, 0.92)
        _rrect(canvas, (x1, y1), (x1 + cw, y1 + chh), C_BRAKE, r=12, thickness=2)
        _rrect(canvas, (x1, y1), (x1 + cw, y1 + 50), C_BRAKE, r=12)
        cv2.rectangle(canvas, (x1, y1 + 30), (x1 + cw, y1 + 50), C_BRAKE, -1)
        tx.add((x1 + 18, y1 + 11), "ACCIDENT DETECTED", 24, C_TEXT, bold=True)
        tx.add((x1 + cw - 18 - tx.width(f"{conf}% confidence", 14, True), y1 + 18), f"{conf}% confidence", 14, C_TEXT, bold=True)
        rows = [("Emergency workflow initiated", "DEMO / SIMULATION"),
                ("Emergency contact", "SIMULATED NOTIFICATION"),
                ("Nearby hospital", "AMBULANCE REQUEST — SIMULATION"),
                ("Evidence", ", ".join(acc.get("cues", []))[:48])]
        for i, (k, v) in enumerate(rows):
            yy = y1 + 64 + 36 * i
            tx.add((x1 + 18, yy), k, 13, C_MUTED)
            tx.add((x1 + 18, yy + 15), v, 15, C_TEXT, bold=True)
        tx.add((x1 + 18, y1 + chh - 22), "Research prototype — nothing was sent or called.", 12, C_MUTED)
