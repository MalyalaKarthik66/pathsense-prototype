"""
PathSense - Phase 8: Final Cockpit HUD Compositor
Module: overlay.py

Composites all analytical vision and planning streams into a high-definition automotive HUD:
1. Full-resolution dashcam feed with bounding boxes, track IDs, distance, closing velocity, and TTC.
2. Picture-in-Picture (PiP) BEV costmap panel with obstacle footprints, safety inflation halos,
   and dynamic optimal path arc.
3. Upper and lower digital telemetry instrument clusters:
   - Digital speedometer (km/h)
   - Analog steering wheel / turn indicator dial (degrees)
   - Ego position odometry (X, Y in meters)
   - Master three-tier Collision Warning Banner (SAFE, CAUTION, CRITICAL)
"""

from typing import Dict, List, Any, Optional, Tuple
import cv2
import numpy as np


DECISION_COLORS = {"GO": (0, 255, 128), "CAUTION": (0, 165, 255), "BRAKE": (0, 0, 255)}
LABEL_COLORS = {"GO STRAIGHT": (0, 220, 110), "STEER LEFT": (0, 215, 255), "STEER RIGHT": (0, 215, 255),
                "SLOW DOWN": (0, 165, 255), "BRAKE": (0, 0, 255), "NO SAFE PATH - BRAKE": (0, 0, 150)}
PATH_COLORS = {"CLEAR": (0, 200, 100), "PARTIAL": (0, 180, 255), "BLOCKED": (0, 0, 230)}
BOTTOM_BAR_H = 100


def fit_text(text: str, scale: float, thickness: int, max_w: int) -> str:
    """Truncates text with '...' so it fits within max_w pixels."""
    if cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0][0] <= max_w:
        return text
    while text and cv2.getTextSize(text + "...", cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0][0] > max_w:
        text = text[:-1]
    return text + "..."


class HUDOverlay:
    def __init__(
        self,
        pip_size: Optional[Tuple[int, int]] = None,
        pip_margin: int = 20,
        dark_theme: bool = True
    ):
        # pip_size=None -> sized from the frame (340px at 720p), so any input resolution fits
        self.pip_size = pip_size
        self.margin = pip_margin
        self.dark = dark_theme
        self._timeline: Optional[np.ndarray] = None  # per-frame decision colours for the history strip

    def _pip_side(self, w: int, h: int) -> int:
        top, bottom = self.margin + 75, BOTTOM_BAR_H + 8 + self.margin  # below top banner, above strip + timeline
        side = self.pip_size[0] if self.pip_size else int(round(0.47 * h))
        return int(max(0, min(side, h - top - bottom, int(0.45 * w))))

    @staticmethod
    def min_canvas_size(width: int, height: int) -> Tuple[int, int]:
        """Output size for a given input: small inputs are upscaled (aspect kept) so the HUD layout fits (>= 1280x720)."""
        scale = max(1.0, 1280.0 / width, 720.0 / height)
        return int(round(width * scale)) // 2 * 2, int(round(height * scale)) // 2 * 2

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
        decision: Optional[Dict[str, Any]] = None
    ) -> np.ndarray:
        """
        Renders the complete PathSense cockpit HUD.
        Frames smaller than 1280x720 are upscaled (with their boxes) so the fixed-size widgets fit.
        """
        src_h, src_w = frame_bgr.shape[:2]
        out_w, out_h = self.min_canvas_size(src_w, src_h)
        if (out_w, out_h) != (src_w, src_h):
            canvas = cv2.resize(frame_bgr, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
            sx, sy = out_w / src_w, out_h / src_h
            tracked_objects = [
                {**o, "bbox": [o["bbox"][0] * sx, o["bbox"][1] * sy, o["bbox"][2] * sx, o["bbox"][3] * sy]}
                for o in tracked_objects if o.get("bbox") is not None
            ]
        else:
            canvas = frame_bgr.copy()
        h, w = canvas.shape[:2]

        has_critical = False
        has_caution = False
        min_ttc = None
        key_id = decision.get("key_track_id") if decision else None
        dec_state = decision.get("state") if decision else None

        # 1. Render Detection & Tracking Bounding Boxes with TTC Tags
        for obj in tracked_objects:
            tid = obj.get("track_id", -1)
            cname = obj.get("class_name", "vehicle")
            bbox = obj.get("bbox", [0, 0, 0, 0])
            dist = obj.get("smoothed_depth_m", obj.get("estimated_depth_m", 15.0))
            closing_kmh = obj.get("closing_speed_kmh", 0.0)
            ttc = obj.get("ttc_sec", None)
            hazard = obj.get("hazard_level", "SAFE")

            if hazard == "CRITICAL":
                has_critical = True
                if min_ttc is None or (ttc is not None and ttc < min_ttc):
                    min_ttc = ttc
            elif hazard == "CAUTION":
                has_caution = True
                if min_ttc is None or (ttc is not None and ttc < min_ttc):
                    min_ttc = ttc

            # Colour code: red is reserved for critical objects inside the ego corridor; objects that are closing
            # but outside the corridor (oncoming, roadside, being overtaken) are orange
            in_path = obj.get("in_corridor", True) if decision else True
            critical_in_path = hazard == "CRITICAL" and in_path
            if critical_in_path:
                box_color = (0, 0, 255)       # Red
            elif hazard == "CRITICAL":
                box_color = (0, 120, 255)     # Orange: closing fast, not in path
            elif hazard == "CAUTION":
                box_color = (0, 200, 255)     # Amber
            else:
                box_color = (0, 255, 128)     # Green

            x1, y1, x2, y2 = [int(v) for v in bbox]
            thick = 3 if critical_in_path else 2
            cv2.rectangle(canvas, (x1, y1), (x2, y2), box_color, thick, cv2.LINE_AA)
            is_key = key_id is not None and tid == key_id and dec_state in ("CAUTION", "BRAKE")
            if is_key:
                cv2.rectangle(canvas, (x1 - 4, y1 - 4), (x2 + 4, y2 + 4), (255, 255, 255), 2, cv2.LINE_AA)

            # High-visibility corner brackets for critical in-path objects
            if critical_in_path:
                cl = min(22, (x2 - x1) // 3)
                cv2.line(canvas, (x1, y1), (x1 + cl, y1), (0, 0, 255), 4)
                cv2.line(canvas, (x1, y1), (x1, y1 + cl), (0, 0, 255), 4)
                cv2.line(canvas, (x2, y2), (x2 - cl, y2), (0, 0, 255), 4)
                cv2.line(canvas, (x2, y2), (x2, y2 - cl), (0, 0, 255), 4)

            # TTC Badge Text
            ttc_str = f"TTC: {ttc:.1f}s" if ttc is not None else "TTC: N/A"
            label = f"#{tid} {obj.get('display_class', cname)} | {dist:.1f}m | {ttc_str}"
            if abs(closing_kmh) > 1.0:
                label += f" | {closing_kmh:+.0f}km/h"
            if obj.get("behavior") and obj["behavior"] != "SIDE PASS":
                label += f" | {obj['behavior']}"

            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.44, 1)
            tag_y1 = max(0, y1 - th - 8)
            cv2.rectangle(canvas, (x1, tag_y1), (x1 + tw + 8, y1), box_color, -1)
            text_color = (255, 255, 255) if critical_in_path else (0, 0, 0)
            cv2.putText(canvas, label, (x1 + 4, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.44, text_color, 1, cv2.LINE_AA)

            # In-box tag only for the object that drives the current decision
            if is_key or (decision is None and hazard == "CRITICAL"):
                warn = obj.get("behavior") if obj.get("behavior") in ("CUT-IN", "CROSSING", "ONCOMING") else \
                    ("COLLISION RISK" if dec_state == "BRAKE" or decision is None else "PATH THREAT")
                (ww, wh), _ = cv2.getTextSize(warn, cv2.FONT_HERSHEY_SIMPLEX, 0.50, 2)
                wx = x1 + max(0, (x2 - x1 - ww) // 2)
                wy = y1 + 24
                tag_bg = (0, 0, 220) if dec_state == "BRAKE" or decision is None else (0, 140, 230)
                cv2.rectangle(canvas, (wx - 4, wy - wh - 3), (wx + ww + 4, wy + 3), tag_bg, -1)
                cv2.putText(canvas, warn, (wx, wy), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (255, 255, 255), 2, cv2.LINE_AA)

        # 2. Picture-in-Picture (PiP) BEV Costmap Panel (Top-Right)
        pip_w = pip_h = self._pip_side(w, h)
        if pip_w >= 60 and bev_image is not None and bev_image.size > 0:
            pip_resized = cv2.resize(bev_image, (pip_w, pip_h), interpolation=cv2.INTER_LINEAR)

            px1 = w - pip_w - self.margin
            py1 = self.margin + 75  # Below top banner
            px2 = px1 + pip_w
            py2 = py1 + pip_h

            # Semi-transparent backing for PiP
            pip_bg = canvas[py1-4:py2+4, px1-4:px2+4].copy()
            cv2.rectangle(canvas, (px1-4, py1-4), (px2+4, py2+4), (10, 15, 20), -1)
            cv2.addWeighted(canvas[py1-4:py2+4, px1-4:px2+4], 0.85, pip_bg, 0.15, 0, canvas[py1-4:py2+4, px1-4:px2+4])
            canvas[py1:py2, px1:px2] = pip_resized
            cv2.rectangle(canvas, (px1, py1), (px2, py2), (0, 215, 255), 2)

            # PiP Header tag
            cv2.rectangle(canvas, (px1, py1 - 20), (px1 + 140, py1), (0, 215, 255), -1)
            cv2.putText(canvas, "BEV PLANNER", (px1 + 6, py1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 1, cv2.LINE_AA)

        # 3. Top Master Status Banner
        top_bar_h = 70
        top_bg = canvas[0:top_bar_h, 0:w].copy()
        cv2.rectangle(canvas, (0, 0), (w, top_bar_h), (12, 16, 20), -1)
        cv2.addWeighted(canvas[0:top_bar_h, 0:w], 0.80, top_bg, 0.20, 0, canvas[0:top_bar_h, 0:w])

        # Project Branding & Frame Telemetry
        cv2.putText(canvas, "PathSense: Adaptive AV Planning (SIH PS 26037)", (20, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, f"Frame: {frame_idx:04d}/{total_frames:04d} | Processing: {fps:.1f} FPS | Monocular CV", (20, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (0, 215, 255), 1, cv2.LINE_AA)

        # Master Threat Banner (Center/Right): follows the debounced decision state when available
        if decision is not None:
            st = decision["state"]
            banner_bg_col = DECISION_COLORS[st]
            b_txt_col = (255, 255, 255) if st == "BRAKE" else (0, 0, 0)
            if st == "BRAKE":
                status_title = decision["label"]
            elif st == "CAUTION":
                status_title = "CAUTION: SLOW DOWN"
            else:
                status_title = "PATH CLEAR" if decision["label"] == "GO STRAIGHT" else decision["label"]
        elif has_critical:
            banner_bg_col = (0, 0, 255)
            status_title = f"CRITICAL COLLISION ALERT (TTC: {min_ttc:.1f}s)" if min_ttc else "CRITICAL COLLISION ALERT"
            b_txt_col = (255, 255, 255)
        elif has_caution:
            banner_bg_col = (0, 165, 255)
            status_title = f"CAUTION: CLOSING OBJECT (TTC: {min_ttc:.1f}s)" if min_ttc else "CAUTION: CLOSING OBJECT"
            b_txt_col = (0, 0, 0)
        else:
            banner_bg_col = (0, 220, 100)
            status_title = "SYSTEM STATUS: SAFE"
            b_txt_col = (0, 0, 0)

        (bw, bh), _ = cv2.getTextSize(status_title, cv2.FONT_HERSHEY_SIMPLEX, 0.62, 2)
        bx1 = w - bw - self.margin - 40
        cv2.rectangle(canvas, (bx1, 14), (w - self.margin, 56), banner_bg_col, -1)
        cv2.putText(canvas, status_title, (bx1 + 14, 41), cv2.FONT_HERSHEY_SIMPLEX, 0.62, b_txt_col, 2, cv2.LINE_AA)

        # 4. Bottom Cockpit Instrument Strip
        bottom_bar_h = BOTTOM_BAR_H
        by1 = h - bottom_bar_h
        bot_bg = canvas[by1:h, 0:w].copy()
        cv2.rectangle(canvas, (0, by1), (w, h), (12, 16, 20), -1)
        cv2.addWeighted(canvas[by1:h, 0:w], 0.85, bot_bg, 0.15, 0, canvas[by1:h, 0:w])
        cv2.line(canvas, (0, by1), (w, by1), (0, 215, 255), 1)

        # Instrument 1: Speedometer
        gauge1_x = 40
        cv2.putText(canvas, "EGO SPEED", (gauge1_x, by1 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (160, 180, 200), 1, cv2.LINE_AA)
        cv2.putText(canvas, f"{speed_kmh:4.1f}", (gauge1_x, by1 + 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, "km/h", (gauge1_x + 95, by1 + 60), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 215, 255), 1, cv2.LINE_AA)

        # Divider
        cv2.line(canvas, (gauge1_x + 160, by1 + 15), (gauge1_x + 160, h - 15), (50, 65, 80), 1)

        # Instrument 2: Steering Wheel Angle & Turn Dial
        gauge2_x = gauge1_x + 190
        cv2.putText(canvas, "RECOMMENDED STEERING", (gauge2_x, by1 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (160, 180, 200), 1, cv2.LINE_AA)
        
        turn_dir = "CENTER" if abs(steering_deg) < 1.5 else ("RIGHT" if steering_deg > 0 else "LEFT")
        steer_val_str = f"{steering_deg:+.1f} deg"
        steer_col = (0, 255, 128) if abs(steering_deg) < 5.0 else ((0, 215, 255) if abs(steering_deg) < 12.0 else (0, 140, 255))
        cv2.putText(canvas, steer_val_str, (gauge2_x, by1 + 60), cv2.FONT_HERSHEY_SIMPLEX, 0.85, steer_col, 2, cv2.LINE_AA)
        # Lay out direction label and dial from measured text widths so they never overlap
        (sv_tw, _), _ = cv2.getTextSize(steer_val_str, cv2.FONT_HERSHEY_SIMPLEX, 0.85, 2)
        dir_str = f"({turn_dir})"
        (dir_tw, _), _ = cv2.getTextSize(dir_str, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.putText(canvas, dir_str, (gauge2_x + sv_tw + 8, by1 + 60), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 180, 200), 1, cv2.LINE_AA)

        # Mini Steering Wheel Dial (Visual needle)
        dw_cx, dw_cy = max(gauge2_x + 245, gauge2_x + sv_tw + dir_tw + 16 + 22), by1 + 45
        dw_r = 22
        cv2.circle(canvas, (dw_cx, dw_cy), dw_r, (40, 50, 60), -1, cv2.LINE_AA)
        cv2.circle(canvas, (dw_cx, dw_cy), dw_r, (0, 215, 255), 1, cv2.LINE_AA)
        steer_rad = np.radians(steering_deg)
        nx = int(dw_cx + (dw_r - 4) * np.sin(steer_rad))
        ny = int(dw_cy - (dw_r - 4) * np.cos(steer_rad))
        cv2.line(canvas, (dw_cx, dw_cy), (nx, ny), steer_col, 2, cv2.LINE_AA)
        cv2.circle(canvas, (dw_cx, dw_cy), 3, (255, 255, 255), -1, cv2.LINE_AA)

        # Divider
        cv2.line(canvas, (gauge2_x + 295, by1 + 15), (gauge2_x + 295, h - 15), (50, 65, 80), 1)

        # Instrument 3: 2D Relative Position Trace Odometry
        gauge3_x = gauge2_x + 325
        cv2.putText(canvas, "ODOMETRY POSITION (X, Y)", (gauge3_x, by1 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (160, 180, 200), 1, cv2.LINE_AA)
        pos_str = f"X: {pos_x:+.1f}m | Y: {pos_y:5.1f}m"
        cv2.putText(canvas, pos_str, (gauge3_x, by1 + 60), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)

        # Divider placed after the measured text width so long odometry values never overlap the next gauge
        (pos_tw, _), _ = cv2.getTextSize(pos_str, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)
        div3_x = gauge3_x + max(250, pos_tw + 15)
        cv2.line(canvas, (div3_x, by1 + 15), (div3_x, h - 15), (50, 65, 80), 1)

        # Instrument 4: Decision (debounced state + reason) or raw planner state
        gauge4_x = div3_x + 25
        avail = w - gauge4_x - 12
        if decision is not None:
            self._draw_timeline(canvas, by1, frame_idx, total_frames, decision["label"])
            self._draw_decision_panel(canvas, gauge4_x, by1, avail, decision, speed_kmh)
            return canvas
        cv2.putText(canvas, "PLANNER STATE", (gauge4_x, by1 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (160, 180, 200), 1, cv2.LINE_AA)
        if planner_blocked:
            plan_state, state_col = "NO SAFE PATH - BRAKE", (0, 0, 255)
        elif abs(steering_deg) >= 4.0:
            plan_state, state_col = "EVASIVE MANEUVER", (0, 215, 255)
        else:
            plan_state, state_col = "LANE KEEPING", (0, 255, 128)
        cv2.putText(canvas, plan_state, (gauge4_x, by1 + 60), cv2.FONT_HERSHEY_SIMPLEX, 0.70, state_col, 2, cv2.LINE_AA)

        return canvas

    def _draw_timeline(self, canvas: np.ndarray, by1: int, frame_idx: int, total_frames: int, label: str):
        """Thin decision-history strip above the bottom bar (whole video mapped to the frame width)."""
        if not total_frames or total_frames <= 0:
            return
        h, w = canvas.shape[:2]
        if self._timeline is None or len(self._timeline) != total_frames:
            self._timeline = np.zeros((total_frames, 3), np.uint8)
            self._timeline[:] = (45, 45, 45)
        if 0 <= frame_idx < total_frames:
            self._timeline[frame_idx] = LABEL_COLORS.get(label, (128, 128, 128))
        strip = cv2.resize(self._timeline[None, :, :], (w, 6), interpolation=cv2.INTER_NEAREST)
        canvas[by1 - 7:by1 - 1, 0:w] = strip
        cx = int((frame_idx + 0.5) / total_frames * w)
        cv2.line(canvas, (cx, by1 - 10), (cx, by1 - 1), (255, 255, 255), 2)

    def _draw_decision_panel(self, canvas: np.ndarray, x: int, by1: int, avail: int, d: Dict[str, Any], speed_kmh: float):
        font = cv2.FONT_HERSHEY_SIMPLEX
        grey, white = (160, 180, 200), (235, 240, 245)
        # Row 1: title, path status chip, threat count
        cv2.putText(canvas, "DECISION", (x, by1 + 18), font, 0.40, grey, 1, cv2.LINE_AA)
        path = d.get("path_status") or "CLEAR"
        chip = f"PATH {path}" + (f" {d['blocked_arcs']}/{d['n_arcs']}" if path == "PARTIAL" and d.get("n_arcs") else "")
        (cw, ch), _ = cv2.getTextSize(chip, font, 0.40, 1)
        thr = f"THREATS {d.get('threats', 0)}"
        (tw, _), _ = cv2.getTextSize(thr, font, 0.40, 1)
        chip_x = x + avail - cw - tw - 22
        cv2.rectangle(canvas, (chip_x - 5, by1 + 5), (chip_x + cw + 5, by1 + 23), PATH_COLORS.get(path, grey), -1)
        cv2.putText(canvas, chip, (chip_x, by1 + 19), font, 0.40, (0, 0, 0) if path != "BLOCKED" else white, 1, cv2.LINE_AA)
        cv2.putText(canvas, thr, (x + avail - tw, by1 + 19), font, 0.40, white if d.get("threats") else grey, 1, cv2.LINE_AA)
        # Row 2: the decision itself
        label = d["label"]
        cv2.putText(canvas, fit_text(label, 0.74, 2, avail), (x, by1 + 47), font, 0.74, LABEL_COLORS.get(label, white), 2, cv2.LINE_AA)
        # Row 3: reason title
        title = d.get("title") or ""
        if d.get("holding") and label != "NO SAFE PATH - BRAKE" and not title.lower().startswith(("holding", "clearing")):
            title = f"{title} (holding)"
        cv2.putText(canvas, fit_text(title, 0.48, 1, avail), (x, by1 + 69), font, 0.48, white, 1, cv2.LINE_AA)
        # Row 4: responsible object details (or context when clear)
        k = d.get("key")
        if label == "NO SAFE PATH - BRAKE":
            det = (f"{d.get('blocked_arcs')}/{d.get('n_arcs')} candidate arcs collide" if d.get("blocked_now", True)
                   else f"clearing: {d.get('blocked_arcs')}/{d.get('n_arcs')} arcs blocked now")
            if k and k.get("class_name"):
                det += f" | nearest {k['class_name']} {k['distance_m']:.1f} m"
        elif k and k.get("class_name"):
            ttc = f"TTC {k['ttc_s']:.1f}s" if k.get("ttc_s") is not None else "TTC N/A"
            det = f"{k['class_name']} #{k.get('track_id')} | {k['distance_m']:.1f} m | {ttc}"
            if k.get("behavior") and k["behavior"] != "SIDE PASS":
                det += f" | {k['behavior']}"
        else:
            det = f"speed {speed_kmh:.0f} km/h | {d.get('reason', '')}"
        cv2.putText(canvas, fit_text(det, 0.42, 1, avail), (x, by1 + 90), font, 0.42, grey, 1, cv2.LINE_AA)
