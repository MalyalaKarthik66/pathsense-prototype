"""
PathSense - Phase 5: Monocular Ego-Motion (Speed & Position Trace)
Module: ego_motion.py

Estimates ego-vehicle longitudinal speed (km/h) and 2D relative position trace (X, Y in meters)
using sparse pyramidal Lucas-Kanade optical flow on stationary road-surface feature points.
Filters out dynamic moving obstacle regions and the vehicle's own hood to prevent flow corruption.
Applies temporal Exponential Moving Average (EMA) smoothing to reject road bump vibrations,
pitch shifts, and illumination noise.

ASSUMPTIONS & CAMERA GEOMETRY:
- Camera Height (h): 1.30 meters above the flat road surface.
- Camera Pitch Angle: ~0.0 deg (horizon calibrated at cy ≈ 0.55 * H).
- Horizontal Field of View (FOV): 70 deg -> fy ≈ fx ≈ 0.71 * W.
- Road ROI: Strict road surface band (0.65*H to 0.88*H) excluding the vehicle's hood (y > 0.88*H).
- Flat Ground Perspective: Ground distance Y(v) = (fy * h) / (v - cy).
"""

import os
import sys
import time
import json
import argparse
from typing import Dict, List, Any, Optional, Tuple
import cv2
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


class EgoMotionEstimator:
    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        fps: float = 25.0,
        camera_height_m: float = 1.30,
        fov_deg: float = 70.0,
        horizon_ratio: float = 0.55,
        ema_alpha: float = 0.15,
        max_points: int = 250,
        min_points: int = 40
    ):
        self.w = width
        self.h = height
        self.fps = fps
        self.dt = 1.0 / fps
        self.camera_h = camera_height_m
        self.ema_alpha = ema_alpha
        self.max_points = max_points
        self.min_points = min_points

        # Pinhole Camera Intrinsics
        fov_rad = np.radians(fov_deg)
        self.fx = (self.w / 2.0) / np.tan(fov_rad / 2.0)
        self.fy = self.fx
        self.cx = self.w / 2.0
        self.cy = self.h * horizon_ratio  # Calibrated horizon line (typically ~396px on 720p)

        # Pure Road Surface Region of Interest (ROI)
        # Strictly above vehicle hood (y < 0.88*H) and safely below horizon (y > 0.65*H)
        self.roi_y_min = int(0.65 * self.h)
        self.roi_y_max = int(0.88 * self.h)
        self.roi_x_min = int(0.28 * self.w)
        self.roi_x_max = int(0.72 * self.w)

        # Lucas-Kanade parameters
        self.lk_params = dict(
            winSize=(21, 21),
            maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)
        )
        self.feature_params = dict(
            maxCorners=self.max_points,
            qualityLevel=0.01,
            minDistance=8,
            blockSize=7
        )

        # Internal state
        self.prev_gray: Optional[np.ndarray] = None
        self.prev_pts: Optional[np.ndarray] = None
        self.smoothed_speed_mps = 0.0
        self.smoothed_lateral_mps = 0.0
        self.pos_x = 0.0
        self.pos_y = 0.0

    def _create_road_mask(self, obstacle_boxes: List[List[float]]) -> np.ndarray:
        """
        Creates a binary mask selecting the pure road surface and masking out moving obstacles.
        """
        mask = np.zeros((self.h, self.w), dtype=np.uint8)
        mask[self.roi_y_min:self.roi_y_max, self.roi_x_min:self.roi_x_max] = 255

        # Mask out dynamic obstacles (with 15px safety margin)
        for bbox in obstacle_boxes:
            if len(bbox) == 4:
                x1, y1, x2, y2 = [int(v) for v in bbox]
                mx1 = max(0, x1 - 15)
                my1 = max(0, y1 - 15)
                mx2 = min(self.w, x2 + 15)
                my2 = min(self.h, y2 + 15)
                mask[my1:my2, mx1:mx2] = 0

        return mask

    def update(
        self,
        frame_bgr: np.ndarray,
        obstacle_boxes: Optional[List[List[float]]] = None
    ) -> Tuple[float, float, float, float, List[Tuple[Tuple[int, int], Tuple[int, int]]]]:
        """
        Updates ego-motion from the current frame.
        Returns:
            raw_speed_kmh: un-smoothed forward speed (km/h)
            smooth_speed_kmh: EMA-smoothed forward speed (km/h)
            pos_x: cumulative lateral displacement (m)
            pos_y: cumulative forward distance (m)
            flow_tracks: list of ((u0, v0), (u1, v1)) optical flow vectors
        """
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        obstacle_boxes = obstacle_boxes or []
        road_mask = self._create_road_mask(obstacle_boxes)

        flow_tracks = []
        raw_dy_list = []
        raw_dx_list = []

        if self.prev_gray is not None and self.prev_pts is not None and len(self.prev_pts) > 0:
            curr_pts, status, err = cv2.calcOpticalFlowPyrLK(
                self.prev_gray, gray, self.prev_pts, None, **self.lk_params
            )

            if curr_pts is not None and status is not None:
                good_curr = curr_pts[status.flatten() == 1].reshape(-1, 2)
                good_prev = self.prev_pts[status.flatten() == 1].reshape(-1, 2)

                for (u0, v0), (u1, v1) in zip(good_prev, good_curr):
                    # Features must be safely below horizon line
                    if v0 > self.cy + 30 and v1 > self.cy + 30:
                        # Ground longitudinal distance Y(v) = (fy * h) / (v - cy)
                        y0 = (self.fy * self.camera_h) / (v0 - self.cy)
                        y1 = (self.fy * self.camera_h) / (v1 - self.cy)

                        # Lateral ground position X = (u - cx) * Y / fx
                        x0 = (u0 - self.cx) * y0 / self.fx
                        x1 = (u1 - self.cx) * y1 / self.fx

                        # Forward vehicle displacement (positive when feature moves downward):
                        disp_y = y0 - y1
                        disp_x = x0 - x1

                        # Physical sanity check: forward speed between 1 km/h and 140 km/h (0.01m to 1.6m per frame)
                        if 0.02 < disp_y < 1.60 and abs(disp_x) < 0.60:
                            raw_dy_list.append(disp_y)
                            raw_dx_list.append(disp_x)
                            flow_tracks.append(((int(u0), int(v0)), (int(u1), int(v1))))

        # Robust median displacement
        if len(raw_dy_list) >= 4:
            dy = float(np.median(raw_dy_list))
            dx = float(np.median(raw_dx_list))
        else:
            dy = self.smoothed_speed_mps * self.dt
            dx = 0.0

        raw_speed_mps = dy / self.dt
        raw_speed_kmh = max(0.0, raw_speed_mps * 3.6)

        # EMA Smoothing: smooth = alpha * raw + (1 - alpha) * smooth
        if self.smoothed_speed_mps == 0.0 and raw_speed_mps > 0:
            self.smoothed_speed_mps = raw_speed_mps
        else:
            self.smoothed_speed_mps = (
                self.ema_alpha * raw_speed_mps + (1.0 - self.ema_alpha) * self.smoothed_speed_mps
            )
        smooth_speed_kmh = max(0.0, self.smoothed_speed_mps * 3.6)

        # Smooth lateral displacement
        lat_speed_mps = dx / self.dt
        self.smoothed_lateral_mps = (
            self.ema_alpha * lat_speed_mps + (1.0 - self.ema_alpha) * self.smoothed_lateral_mps
        )

        # Position Integration
        self.pos_y += self.smoothed_speed_mps * self.dt
        self.pos_x += self.smoothed_lateral_mps * self.dt

        # Redetect fresh features for next frame
        new_pts = cv2.goodFeaturesToTrack(gray, mask=road_mask, **self.feature_params)
        self.prev_pts = new_pts
        self.prev_gray = gray

        return (
            round(raw_speed_kmh, 1),
            round(smooth_speed_kmh, 1),
            round(self.pos_x, 2),
            round(self.pos_y, 2),
            flow_tracks
        )


def draw_ego_motion_overlay(
    frame: np.ndarray,
    flow_tracks: List[Tuple[Tuple[int, int], Tuple[int, int]]],
    raw_kmh: float,
    smooth_kmh: float,
    pos_x: float,
    pos_y: float,
    frame_idx: int,
    total_frames: int,
    trajectory_pts: List[Tuple[float, float]]
) -> np.ndarray:
    """
    Renders optical flow vectors, digital telemetry speedometer, and an inset 2D path map.
    """
    canvas = frame.copy()
    h, w = canvas.shape[:2]

    # Draw optical flow vectors on road
    for (x0, y0), (x1, y1) in flow_tracks:
        cv2.line(canvas, (x0, y0), (x1, y1), (0, 255, 128), 1, cv2.LINE_AA)
        cv2.circle(canvas, (x1, y1), 2, (0, 215, 255), -1, cv2.LINE_AA)

    # Top Telemetry Banner
    hud_h = 75
    banner_bg = canvas[0:hud_h, 0:w].copy()
    cv2.rectangle(canvas, (0, 0), (w, hud_h), (15, 15, 15), -1)
    cv2.addWeighted(canvas[0:hud_h, 0:w], 0.75, banner_bg, 0.25, 0, canvas[0:hud_h, 0:w])

    cv2.putText(canvas, f"PathSense Ego-Motion Odometry | Frame: {frame_idx:04d}/{total_frames:04d}", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"Tracked Road Features: {len(flow_tracks)} | Sparse LK + Planar Homography", (20, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 215, 255), 1, cv2.LINE_AA)

    # Speedometer Badge
    speed_text = f"Speed: {smooth_kmh:4.1f} km/h"
    cv2.rectangle(canvas, (w - 390, 15), (w - 180, 60), (0, 120, 255), -1)
    cv2.putText(canvas, speed_text, (w - 380, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (255, 255, 255), 2, cv2.LINE_AA)

    # Distance Badge
    dist_text = f"Dist: {pos_y:5.1f} m"
    cv2.rectangle(canvas, (w - 170, 15), (w - 20, 60), (0, 180, 80), -1)
    cv2.putText(canvas, dist_text, (w - 160, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (255, 255, 255), 2, cv2.LINE_AA)

    # Inset 2D Path Minimap (Bottom-Right)
    map_w, map_h = 220, 190
    map_x1, map_y1 = w - map_w - 20, h - map_h - 20
    map_x2, map_y2 = map_x1 + map_w, map_y1 + map_h

    # Minimap background
    cv2.rectangle(canvas, (map_x1, map_y1), (map_x2, map_y2), (20, 25, 30), -1)
    cv2.rectangle(canvas, (map_x1, map_y1), (map_x2, map_y2), (0, 215, 255), 1)
    cv2.putText(canvas, "Ego Trajectory Trace", (map_x1 + 15, map_y1 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 215, 255), 1, cv2.LINE_AA)

    # Center origin on minimap
    cx_map = map_x1 + map_w // 2
    cy_map = map_y2 - 25

    # Grid guide lines
    cv2.line(canvas, (cx_map, map_y1 + 30), (cx_map, map_y2 - 10), (50, 60, 70), 1)
    cv2.line(canvas, (map_x1 + 10, cy_map), (map_x2 - 10, cy_map), (50, 60, 70), 1)

    # Plot scaled trajectory
    max_y = max(60.0, pos_y + 15.0)
    scale_y = (map_h - 60) / max_y
    scale_x = scale_y * 1.8

    if len(trajectory_pts) >= 2:
        poly_pts = []
        for px, py in trajectory_pts:
            screen_x = int(cx_map + px * scale_x)
            screen_y = int(cy_map - py * scale_y)
            poly_pts.append((screen_x, screen_y))
        
        cv2.polylines(canvas, [np.array(poly_pts, dtype=np.int32)], isClosed=False, color=(0, 255, 255), thickness=2, lineType=cv2.LINE_AA)

    # Ego vehicle current marker
    cur_x = int(cx_map + pos_x * scale_x)
    cur_y = int(cy_map - pos_y * scale_y)
    cv2.circle(canvas, (cur_x, cur_y), 5, (0, 0, 255), -1, cv2.LINE_AA)
    cv2.circle(canvas, (cur_x, cur_y), 7, (255, 255, 255), 1, cv2.LINE_AA)

    return canvas


def process_video_ego_motion(
    video_path: str,
    ttc_json_path: Optional[str] = None,
    output_video_path: Optional[str] = None,
    output_json_path: Optional[str] = None,
    speed_plot_path: Optional[str] = "speed_profile.png",
    pos_plot_path: Optional[str] = "position_trace.png",
    max_frames: Optional[int] = None
) -> Dict[str, Any]:
    """
    Processes video frames to extract ego-motion odometry (speed & position trace),
    renders preview video with optical flow vectors and minimap, and exports
    speed_profile.png and position_trace.png.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    # Load obstacle bounding boxes from prior phases if available
    obstacle_data = None
    if ttc_json_path and os.path.exists(ttc_json_path):
        print(f"[Phase 5] Loading obstacle masks from {ttc_json_path}...")
        with open(ttc_json_path, "r") as f:
            obstacle_data = json.load(f)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames_to_process = min(total_frames, max_frames) if max_frames else total_frames

    print(f"\n[Phase 5] Estimating Ego-Motion: {os.path.basename(video_path)}")
    print(f"  Resolution: {width}x{height} | Native FPS: {fps:.2f} | Frames to process: {frames_to_process}")

    estimator = EgoMotionEstimator(width=width, height=height, fps=fps)

    writer = None
    if output_video_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_video_path, fourcc, fps, (width, height))

    ego_data = {
        "metadata": {
            "source_video": video_path,
            "width": width,
            "height": height,
            "fps": fps,
            "total_frames": frames_to_process,
            "camera_height_m": estimator.camera_h,
            "fov_deg": 70.0,
            "smoothing_alpha": estimator.ema_alpha
        },
        "frames": []
    }

    raw_speeds: List[float] = []
    smooth_speeds: List[float] = []
    pos_x_list: List[float] = []
    pos_y_list: List[float] = []
    timestamps: List[float] = []
    trajectory_pts: List[Tuple[float, float]] = []

    frame_idx = 0
    t_start = time.time()

    while cap.isOpened() and frame_idx < frames_to_process:
        ret, frame = cap.read()
        if not ret:
            break

        # Extract obstacle boxes for current frame to mask out dynamic regions
        obstacle_boxes = []
        if obstacle_data and frame_idx < len(obstacle_data.get("frames", [])):
            for obj in obstacle_data["frames"][frame_idx].get("objects", []):
                obstacle_boxes.append(obj.get("bbox", []))

        # Update ego-motion
        raw_kmh, smooth_kmh, px, py, flow_tracks = estimator.update(frame, obstacle_boxes)

        t_sec = round(frame_idx / fps, 3)
        raw_speeds.append(raw_kmh)
        smooth_speeds.append(smooth_kmh)
        pos_x_list.append(px)
        pos_y_list.append(py)
        timestamps.append(t_sec)
        trajectory_pts.append((px, py))

        ego_data["frames"].append({
            "frame_idx": frame_idx,
            "timestamp_sec": t_sec,
            "raw_speed_kmh": raw_kmh,
            "smooth_speed_kmh": smooth_kmh,
            "position_x_m": px,
            "position_y_m": py,
            "active_ground_features": len(flow_tracks)
        })

        if writer is not None:
            overlay_frame = draw_ego_motion_overlay(
                frame, flow_tracks, raw_kmh, smooth_kmh, px, py, frame_idx, frames_to_process, trajectory_pts
            )
            writer.write(overlay_frame)

        frame_idx += 1
        if frame_idx % 50 == 0 or frame_idx == frames_to_process:
            percent = (frame_idx / frames_to_process) * 100.0
            print(f"  Frame {frame_idx:04d}/{frames_to_process:04d} ({percent:5.1f}%) | Speed: {smooth_kmh:.1f} km/h | Dist: {py:.1f}m")

    total_time = time.time() - t_start
    avg_fps = frame_idx / total_time if total_time > 0 else 0

    cap.release()
    if writer is not None:
        writer.release()

    ego_data["metadata"]["processed_frames"] = frame_idx
    ego_data["metadata"]["elapsed_seconds"] = round(total_time, 2)
    ego_data["metadata"]["average_fps"] = round(avg_fps, 2)
    ego_data["metadata"]["final_distance_m"] = round(pos_y_list[-1], 2) if pos_y_list else 0.0
    ego_data["metadata"]["mean_speed_kmh"] = round(float(np.mean(smooth_speeds)), 2) if smooth_speeds else 0.0

    # 1. Plot Speed Profile
    if speed_plot_path:
        os.makedirs(os.path.dirname(os.path.abspath(speed_plot_path)), exist_ok=True)
        plt.figure(figsize=(10, 4.8), dpi=150)
        plt.style.use("seaborn-v0_8-darkgrid" if "seaborn-v0_8-darkgrid" in plt.style.available else "default")
        
        plt.plot(timestamps, raw_speeds, color="#95a5a6", linestyle="--", linewidth=1.1, alpha=0.6, label="Raw Optical Flow Speed")
        plt.plot(timestamps, smooth_speeds, color="#2980b9", linewidth=2.4, label=f"EMA Smoothed Speed (α={estimator.ema_alpha})")
        mean_spd = np.mean(smooth_speeds)
        plt.axhline(mean_spd, color="#e67e22", linestyle=":", linewidth=1.8, label=f"Mean Speed ({mean_spd:.1f} km/h)")
        
        plt.title("PathSense Ego-Vehicle Speed Profile Over Time", fontsize=13, fontweight="bold", pad=12)
        plt.xlabel("Elapsed Time (seconds)", fontsize=10)
        plt.ylabel("Vehicle Speed (km/h)", fontsize=10)
        plt.ylim(0, max(90.0, max(smooth_speeds) * 1.25))
        plt.grid(True, linestyle="--", alpha=0.6)
        plt.legend(loc="upper right", frameon=True)
        plt.tight_layout()
        plt.savefig(speed_plot_path)
        plt.close()
        print(f"  Saved speed profile plot: {speed_plot_path}")

    # 2. Plot 2D Position Trace
    if pos_plot_path:
        os.makedirs(os.path.dirname(os.path.abspath(pos_plot_path)), exist_ok=True)
        plt.figure(figsize=(6.5, 8.5), dpi=150)
        plt.style.use("seaborn-v0_8-darkgrid" if "seaborn-v0_8-darkgrid" in plt.style.available else "default")

        plt.plot(pos_x_list, pos_y_list, color="#27ae60", linewidth=2.8, label="Ego Trajectory Path")
        plt.scatter([pos_x_list[0]], [pos_y_list[0]], color="#2980b9", s=110, zorder=5, label=f"Start (0.0m, 0.0m)")
        plt.scatter([pos_x_list[-1]], [pos_y_list[-1]], color="#c0392b", s=130, marker="X", zorder=5, label=f"End ({pos_x_list[-1]:.1f}m, {pos_y_list[-1]:.1f}m)")

        plt.title("PathSense 2D Ego-Position Trace (Odometry)", fontsize=13, fontweight="bold", pad=12)
        plt.xlabel("Lateral Displacement X (meters)", fontsize=10)
        plt.ylabel("Forward Longitudinal Distance Y (meters)", fontsize=10)
        
        # Determine symmetric x bounds around 0
        max_abs_x = max(5.0, max(abs(min(pos_x_list)), abs(max(pos_x_list))) * 1.5)
        plt.xlim(-max_abs_x, max_abs_x)
        plt.ylim(-2, max(60.0, pos_y_list[-1] * 1.1))
        plt.grid(True, linestyle="--", alpha=0.6)
        plt.legend(loc="upper left", frameon=True)
        plt.tight_layout()
        plt.savefig(pos_plot_path)
        plt.close()
        print(f"  Saved 2D position trace plot: {pos_plot_path}")

    if output_json_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_json_path)), exist_ok=True)
        with open(output_json_path, "w") as f:
            json.dump(ego_data, f, indent=2)
        print(f"  Saved ego-motion data JSON: {output_json_path}")

    if output_video_path:
        print(f"  Saved ego-motion preview video: {output_video_path}")

    print(f"\n[Phase 5 Completed]")
    print(f"  Processed {frame_idx} frames in {total_time:.2f}s ({avg_fps:.2f} FPS)")
    print(f"  Final Longitudinal Distance: {pos_y_list[-1]:.1f}m | Mean Speed: {np.mean(smooth_speeds):.1f} km/h")

    return ego_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PathSense Phase 5: Ego-Motion Estimation")
    parser.add_argument("--input", type=str, default="data/samples/sample_1.mp4", help="Path to input video")
    parser.add_argument("--ttc-json", type=str, default="ttc_results.json", help="Path to Phase 4 TTC JSON")
    parser.add_argument("--output", type=str, default="ego_motion_preview.mp4", help="Path to preview video")
    parser.add_argument("--output-json", type=str, default="ego_motion_results.json", help="Path to output JSON")
    parser.add_argument("--speed-plot", type=str, default="speed_profile.png", help="Path to speed plot")
    parser.add_argument("--pos-plot", type=str, default="position_trace.png", help="Path to position trace plot")
    parser.add_argument("--max-frames", type=int, default=None, help="Max frames to process (optional)")
    args = parser.parse_args()

    process_video_ego_motion(
        video_path=args.input,
        ttc_json_path=args.ttc_json,
        output_video_path=args.output,
        output_json_path=args.output_json,
        speed_plot_path=args.speed_plot,
        pos_plot_path=args.pos_plot,
        max_frames=args.max_frames
    )
