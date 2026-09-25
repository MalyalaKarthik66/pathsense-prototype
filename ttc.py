"""
PathSense - Phase 4: Time-to-Collision (TTC) & Closing Speed Estimation
Module: ttc.py

Computes relative closing velocity and Time-to-Collision (TTC) for each tracked object
using historical depth measurements, finite differences, and temporal EMA smoothing.
Handles edge cases (receding, stationary, near-zero velocity, sensor noise) gracefully.
Highlights critical collision hazards (TTC < 2.0s) in vivid red with warning indicators.
"""

import os
import sys
import time
import json
import argparse
from collections import defaultdict, deque
from typing import Dict, List, Any, Optional, Tuple
import cv2
import numpy as np


class TTCComputer:
    def __init__(
        self,
        fps: float = 25.0,
        history_len: int = 15,
        ema_alpha_dist: float = 0.30,
        ema_alpha_speed: float = 0.25,
        min_closing_speed_mps: float = 0.40,  # ~1.4 km/h minimum closing threshold
        critical_ttc_sec: float = 2.0,        # Critical hazard threshold (< 2.0s)
        caution_ttc_sec: float = 4.0,         # Caution hazard threshold (< 4.0s)
        lookback_sec: float = 0.20,           # Finite-difference window (time-based, so noise does not depend on FPS)
        frame_width: Optional[int] = None,
        frame_height: Optional[int] = None,
        expansion_gate: bool = True,          # confirm depth-based TTC with bounding-box expansion (see update_track)
        expansion_window_sec: float = 0.40,
        gate_factor: float = 2.0
    ):
        self.fps = fps if fps and np.isfinite(fps) and fps > 0 else 25.0
        self.dt = 1.0 / self.fps
        self.frame_w, self.frame_h = frame_width, frame_height
        self.expansion_gate = expansion_gate
        self.exp_frames = max(2, int(round(expansion_window_sec * self.fps)))
        self.gate_factor = gate_factor
        self.box_history: Dict[int, deque] = defaultdict(lambda: deque(maxlen=self.exp_frames + 1))
        self.lookback_frames = max(2, int(round(lookback_sec * self.fps)))
        history_len = max(history_len, self.lookback_frames + 1)
        # A track unseen for longer than this is treated as new when it reappears (stale EMA would fake closing speed)
        self.max_gap_frames = max(2, int(round(1.0 * self.fps)))
        self.last_seen: Dict[int, int] = {}
        self.history_len = history_len
        self.alpha_d = ema_alpha_dist
        self.alpha_v = ema_alpha_speed
        self.min_closing_v = min_closing_speed_mps
        self.critical_ttc = critical_ttc_sec
        self.caution_ttc = caution_ttc_sec

        # State per track_id:
        # history: deque of (frame_idx, raw_depth)
        self.depth_history: Dict[int, deque] = defaultdict(lambda: deque(maxlen=self.history_len))
        # smoothed_depth: float
        self.smoothed_depth: Dict[int, float] = {}
        # smoothed_closing_speed: float (m/s)
        self.smoothed_closing_speed: Dict[int, float] = {}

    def _image_ttc(self, track_id: int, frame_idx: int, bbox: Optional[List[float]]) -> Tuple[str, Optional[float]]:
        """
        Scale-expansion TTC from the bounding box: TTC_img = dt / (s_t / s_{t-k} - 1), independent of the depth map.
        Returns (status, ttc_img); status is "ok" (ttc_img None when the box is not growing), "pending" (track too young),
        or "unverifiable" (no box / box cut by the image border on both a side and the bottom: large, very close object).
        """
        if not self.expansion_gate or bbox is None or self.frame_w is None or self.frame_h is None:
            return "unverifiable", None
        x1, y1, x2, y2 = bbox
        side_cut = x1 <= 2 or x2 >= self.frame_w - 2
        bottom_cut = y2 >= self.frame_h - 2
        if side_cut and bottom_cut:
            self.box_history.pop(track_id, None)
            return "unverifiable", None
        scale = (x2 - x1) if bottom_cut else (y2 - y1)  # use the dimension that is not truncated
        mode = "w" if bottom_cut else "h"
        hist = self.box_history[track_id]
        if hist and hist[-1][2] != mode:
            hist.clear()  # measured dimension changed: restart
        hist.append((frame_idx, max(1.0, scale), mode))
        if len(hist) <= self.exp_frames:
            return "pending", None
        f0, s0, _ = hist[0]
        dt = (frame_idx - f0) * self.dt
        growth = hist[-1][1] / s0 - 1.0
        if dt <= 0 or growth <= 0.01:
            return "ok", None
        return "ok", dt / growth

    def update_track(self, track_id: int, frame_idx: int, raw_depth: float,
                     bbox: Optional[List[float]] = None) -> Tuple[float, float, Optional[float], str]:
        """
        Updates distance history for a track and calculates:
        If bbox is given (and the frame size is known), the depth-based TTC is cross-checked with the box-expansion TTC:
        depth noise on small / distant boxes can fake large closing speeds, but cannot make the box grow. A depth TTC is
        kept only if the box expansion agrees within gate_factor; otherwise the box-based TTC is used (None if the box
        is not growing). Tracks without usable box history cannot be CRITICAL yet (capped at CAUTION).
        Returns:
            smoothed_d: filtered distance in meters
            closing_speed: filtered closing velocity in m/s (positive = approaching)
            ttc: time-to-collision in seconds (or None if N/A / receding)
            hazard_level: 'CRITICAL', 'CAUTION', or 'SAFE'
        """
        # 0. Reset state for tracks that re-appear after a long gap, and drop invalid depth samples
        last = self.last_seen.get(track_id)
        if last is not None and frame_idx - last > self.max_gap_frames:
            self._forget(track_id)
        self.last_seen[track_id] = frame_idx

        if raw_depth is None or not np.isfinite(raw_depth) or raw_depth <= 0.0:
            if track_id not in self.smoothed_depth:
                return 0.0, 0.0, None, "SAFE"
            raw_depth = self.smoothed_depth[track_id]  # hold last estimate

        # 1. Update distance with EMA
        if track_id not in self.smoothed_depth:
            self.smoothed_depth[track_id] = raw_depth
        else:
            self.smoothed_depth[track_id] = self.alpha_d * raw_depth + (1.0 - self.alpha_d) * self.smoothed_depth[track_id]

        curr_smooth_d = self.smoothed_depth[track_id]
        history = self.depth_history[track_id]
        history.append((frame_idx, curr_smooth_d))

        # 2. Finite difference over a fixed time window (lookback_sec) for stable differentiation at any FPS
        lookback = min(self.lookback_frames, len(history) - 1)
        if lookback >= 2:
            prev_frame_idx, prev_smooth_d = history[-1 - lookback]
            delta_frames = frame_idx - prev_frame_idx
            if delta_frames > 0:
                delta_t = delta_frames * self.dt
                # Closing speed is negative rate of change of distance: v_closing = -(d_t - d_t-k) / dt
                raw_closing_v = -(curr_smooth_d - prev_smooth_d) / delta_t
            else:
                raw_closing_v = 0.0
        else:
            raw_closing_v = 0.0

        # 3. EMA smooth closing velocity
        if track_id not in self.smoothed_closing_speed:
            self.smoothed_closing_speed[track_id] = max(0.0, raw_closing_v)
        else:
            self.smoothed_closing_speed[track_id] = (
                self.alpha_v * raw_closing_v + (1.0 - self.alpha_v) * self.smoothed_closing_speed[track_id]
            )

        curr_closing_v = self.smoothed_closing_speed[track_id]

        # 4. Compute TTC: TTC = d / v_closing
        if curr_closing_v >= self.min_closing_v and curr_smooth_d > 0.5:
            raw_ttc = curr_smooth_d / curr_closing_v
            # Cap realistic TTC range (0.1s to 25.0s)
            if 0.1 <= raw_ttc <= 25.0:
                ttc = round(raw_ttc, 2)
            else:
                ttc = None
        else:
            ttc = None  # Receding, stationary, or divergence

        # 4b. Cross-check with bounding-box expansion (independent of the depth map)
        status, ttc_img = self._image_ttc(track_id, frame_idx, bbox)
        if ttc is not None and status == "ok":
            if ttc_img is None:
                ttc = None                                   # box not growing: depth "closing" is noise
            elif ttc_img > self.gate_factor * ttc:
                ttc = round(min(ttc_img, 25.0), 2)           # expansion says slower approach: trust it
        elif ttc is not None and status == "pending" and ttc < self.critical_ttc:
            ttc = self.critical_ttc                          # too young to confirm: at most CAUTION until confirmed

        # 5. Classify Hazard Level
        if ttc is not None and ttc < self.critical_ttc:
            hazard = "CRITICAL"
        elif ttc is not None and ttc < self.caution_ttc:
            hazard = "CAUTION"
        else:
            hazard = "SAFE"

        return round(curr_smooth_d, 2), round(curr_closing_v, 2), ttc, hazard

    def _forget(self, track_id: int):
        self.depth_history.pop(track_id, None)
        self.smoothed_depth.pop(track_id, None)
        self.smoothed_closing_speed.pop(track_id, None)
        self.last_seen.pop(track_id, None)
        self.box_history.pop(track_id, None)

    def prune(self, frame_idx: int):
        """Drops state of tracks not seen for more than max_gap_frames (bounded memory on long videos)."""
        stale = [tid for tid, last in self.last_seen.items() if frame_idx - last > self.max_gap_frames]
        for tid in stale:
            self._forget(tid)


def get_hazard_color(hazard: str) -> Tuple[int, int, int]:
    """Returns BGR color code for hazard level."""
    if hazard == "CRITICAL":
        return (0, 0, 255)      # Bright Red
    elif hazard == "CAUTION":
        return (0, 165, 255)    # Amber / Orange
    else:
        return (0, 255, 128)    # Spring Green


def draw_ttc_overlay(
    frame: np.ndarray,
    tracked_objects: List[Dict[str, Any]],
    frame_idx: int,
    fps: float
) -> np.ndarray:
    """
    Renders bounding boxes with TTC labels, closing velocities, and collision warnings.
    """
    canvas = frame.copy()
    h, w = canvas.shape[:2]

    has_critical_collision = False
    min_ttc_in_frame: Optional[float] = None

    for obj in tracked_objects:
        tid = obj.get("track_id", -1)
        cname = obj.get("class_name", "vehicle")
        bbox = obj.get("bbox", [0, 0, 0, 0])
        dist = obj.get("smoothed_depth_m", obj.get("estimated_depth_m", 15.0))
        closing_v_kmh = obj.get("closing_speed_kmh", 0.0)
        ttc = obj.get("ttc_sec", None)
        hazard = obj.get("hazard_level", "SAFE")

        if hazard == "CRITICAL":
            has_critical_collision = True
            if min_ttc_in_frame is None or (ttc is not None and ttc < min_ttc_in_frame):
                min_ttc_in_frame = ttc
        elif hazard == "CAUTION" and min_ttc_in_frame is None:
            min_ttc_in_frame = ttc

        color = get_hazard_color(hazard)
        thickness = 3 if hazard == "CRITICAL" else 2
        x1, y1, x2, y2 = [int(v) for v in bbox]

        # Draw bounding box
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)

        # Highlight corners for critical obstacles
        if hazard == "CRITICAL":
            corner_len = min(20, (x2 - x1) // 3)
            # Top-left corner
            cv2.line(canvas, (x1, y1), (x1 + corner_len, y1), (0, 0, 255), 4)
            cv2.line(canvas, (x1, y1), (x1, y1 + corner_len), (0, 0, 255), 4)
            # Bottom-right corner
            cv2.line(canvas, (x2, y2), (x2 - corner_len, y2), (0, 0, 255), 4)
            cv2.line(canvas, (x2, y2), (x2, y2 - corner_len), (0, 0, 255), 4)

        # Formulate TTC badge text
        if ttc is not None:
            ttc_str = f"TTC: {ttc:.1f}s"
        else:
            ttc_str = "TTC: N/A"

        label = f"#{tid} {cname} | {dist:.1f}m | {ttc_str}"
        if abs(closing_v_kmh) > 1.0:
            label += f" | {closing_v_kmh:+.0f}km/h"

        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        tag_y1 = max(0, y1 - th - 8)
        tag_y2 = y1

        # Background badge
        cv2.rectangle(canvas, (x1, tag_y1), (x1 + tw + 8, tag_y2), color, -1)
        cv2.putText(
            canvas, label, (x1 + 4, tag_y2 - 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0) if hazard != "CRITICAL" else (255, 255, 255),
            1, cv2.LINE_AA
        )

        # In-box critical collision tag
        if hazard == "CRITICAL":
            warn_tag = "COLLISION ALERT!"
            (ww, wh), _ = cv2.getTextSize(warn_tag, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            wx = x1 + max(0, (x2 - x1 - ww) // 2)
            wy = y1 + 25
            cv2.rectangle(canvas, (wx - 4, wy - wh - 4), (wx + ww + 4, wy + 4), (0, 0, 200), -1)
            cv2.putText(canvas, warn_tag, (wx, wy), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)

    # Master HUD Top Banner
    hud_h = 75
    overlay_top = canvas[0:hud_h, 0:w].copy()
    cv2.rectangle(canvas, (0, 0), (w, hud_h), (20, 20, 20), -1)
    cv2.addWeighted(canvas[0:hud_h, 0:w], 0.75, overlay_top, 0.25, 0, canvas[0:hud_h, 0:w])

    # Left: Frame & FPS telemetry
    cv2.putText(canvas, f"PathSense Collision Monitor | Frame: {frame_idx:04d}", (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"Active Tracks: {len(tracked_objects)} | Rate: {fps:.1f} FPS", (15, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 215, 255), 1, cv2.LINE_AA)

    # Center-Right: Collision Hazard Status Banner
    if has_critical_collision:
        banner_color = (0, 0, 255)
        status_text = f"COLLISION WARNING! (TTC: {min_ttc_in_frame:.1f}s)" if min_ttc_in_frame else "COLLISION WARNING!"
    elif min_ttc_in_frame and min_ttc_in_frame < 4.0:
        banner_color = (0, 165, 255)
        status_text = f"CAUTION: CLOSING (TTC: {min_ttc_in_frame:.1f}s)"
    else:
        banner_color = (0, 255, 128)
        status_text = "STATUS: SAFE (No Immediate Hazard)"

    (bw, bh), _ = cv2.getTextSize(status_text, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
    bx1 = w - bw - 40
    cv2.rectangle(canvas, (bx1, 15), (w - 20, 60), banner_color, -1)
    text_color = (255, 255, 255) if has_critical_collision else (0, 0, 0)
    cv2.putText(canvas, status_text, (bx1 + 10, 43), cv2.FONT_HERSHEY_SIMPLEX, 0.65, text_color, 2, cv2.LINE_AA)

    return canvas


def process_video_ttc(
    video_path: str,
    depth_json_path: str,
    output_video_path: Optional[str] = None,
    output_json_path: Optional[str] = None,
    max_frames: Optional[int] = None
) -> Dict[str, Any]:
    """
    Processes video frames, computes TTC per track, and generates annotated preview video.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not os.path.exists(depth_json_path):
        raise FileNotFoundError(f"Depth JSON file not found: {depth_json_path}")

    with open(depth_json_path, "r") as f:
        depth_data = json.load(f)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Failed to open video {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames_to_process = min(total_frames, max_frames) if max_frames else total_frames

    print(f"\n[Phase 4] Computing TTC: {os.path.basename(video_path)}")
    print(f"  Resolution: {width}x{height} | Native FPS: {fps:.2f} | Frames to process: {frames_to_process}")

    ttc_computer = TTCComputer(fps=fps)

    writer = None
    if output_video_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_video_path, fourcc, fps, (width, height))

    ttc_data = {
        "metadata": {
            "source_video": video_path,
            "width": width,
            "height": height,
            "fps": fps,
            "total_frames": frames_to_process,
            "critical_ttc_threshold_sec": ttc_computer.critical_ttc,
            "caution_ttc_threshold_sec": ttc_computer.caution_ttc
        },
        "frames": []
    }

    frame_idx = 0
    critical_alert_count = 0
    t_start = time.time()
    rolling_fps = 0.0

    while cap.isOpened() and frame_idx < frames_to_process:
        t_frame_start = time.time()
        ret, frame = cap.read()
        if not ret:
            break

        # Get objects for current frame
        current_objects = []
        if frame_idx < len(depth_data.get("frames", [])):
            current_objects = depth_data["frames"][frame_idx].get("objects", [])

        # Process TTC for each object
        processed_objects = []
        frame_has_critical = False

        for obj in current_objects:
            obj_copy = dict(obj)
            tid = obj_copy.get("track_id", -1)
            raw_depth = obj_copy.get("estimated_depth_m", 20.0)

            if tid >= 0:
                smooth_d, closing_v, ttc, hazard = ttc_computer.update_track(tid, frame_idx, raw_depth)
                obj_copy["smoothed_depth_m"] = smooth_d
                obj_copy["closing_speed_mps"] = closing_v
                obj_copy["closing_speed_kmh"] = round(closing_v * 3.6, 1)
                obj_copy["ttc_sec"] = ttc
                obj_copy["hazard_level"] = hazard

                if hazard == "CRITICAL":
                    frame_has_critical = True
            else:
                obj_copy["smoothed_depth_m"] = raw_depth
                obj_copy["closing_speed_mps"] = 0.0
                obj_copy["closing_speed_kmh"] = 0.0
                obj_copy["ttc_sec"] = None
                obj_copy["hazard_level"] = "SAFE"

            processed_objects.append(obj_copy)

        if frame_has_critical:
            critical_alert_count += 1

        ttc_data["frames"].append({
            "frame_idx": frame_idx,
            "timestamp_sec": round(frame_idx / fps, 3),
            "objects": processed_objects
        })

        dt = time.time() - t_frame_start
        instant_fps = 1.0 / dt if dt > 0 else 0
        rolling_fps = instant_fps if frame_idx == 0 else 0.9 * rolling_fps + 0.1 * instant_fps

        if writer is not None:
            annotated_frame = draw_ttc_overlay(frame, processed_objects, frame_idx, rolling_fps)
            writer.write(annotated_frame)

        frame_idx += 1
        if frame_idx % 25 == 0 or frame_idx == frames_to_process:
            percent = (frame_idx / frames_to_process) * 100.0
            print(f"  Frame {frame_idx:04d}/{frames_to_process:04d} ({percent:5.1f}%) | Speed: {rolling_fps:4.1f} FPS")

    total_time = time.time() - t_start
    avg_fps = frame_idx / total_time if total_time > 0 else 0

    cap.release()
    if writer is not None:
        writer.release()

    ttc_data["metadata"]["processed_frames"] = frame_idx
    ttc_data["metadata"]["elapsed_seconds"] = round(total_time, 2)
    ttc_data["metadata"]["average_fps"] = round(avg_fps, 2)
    ttc_data["metadata"]["frames_with_critical_alert"] = critical_alert_count

    if output_json_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_json_path)), exist_ok=True)
        with open(output_json_path, "w") as f:
            json.dump(ttc_data, f, indent=2)
        print(f"  Saved TTC tracking data JSON: {output_json_path}")

    if output_video_path:
        print(f"  Saved TTC preview video: {output_video_path}")

    print(f"\n[Phase 4 Completed]")
    print(f"  Processed {frame_idx} frames in {total_time:.2f}s ({avg_fps:.2f} FPS)")
    print(f"  Critical Hazard Frames (TTC < 2.0s): {critical_alert_count}/{frame_idx}")

    return ttc_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PathSense Phase 4: Time-to-Collision (TTC)")
    parser.add_argument("--input", type=str, default="data/samples/sample_1.mp4", help="Path to input video")
    parser.add_argument("--depth-json", type=str, default="depth_results.json", help="Path to Phase 3 depth JSON")
    parser.add_argument("--output", type=str, default="ttc_preview.mp4", help="Path to preview video")
    parser.add_argument("--output-json", type=str, default="ttc_results.json", help="Path to output JSON")
    parser.add_argument("--max-frames", type=int, default=None, help="Max frames to process (optional)")
    args = parser.parse_args()

    process_video_ttc(
        video_path=args.input,
        depth_json_path=args.depth_json,
        output_video_path=args.output,
        output_json_path=args.output_json,
        max_frames=args.max_frames
    )
