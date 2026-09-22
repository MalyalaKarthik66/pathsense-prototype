"""
PathSense - Phase 3: Monocular Relative Depth Estimation
Module: depth.py

Uses pretrained Depth Anything V2 Small (Hugging Face / transformers) to infer dense relative depth
from front-facing dashcam video. Converts relative disparity to a calibrated metric distance
approximation (2m - 60m) and samples the robust median depth inside each tracked bounding box.
Generates side-by-side RGB + Depth heatmap preview video.

NOTE ON CALIBRATION LIMITATION:
Monocular depth networks predict relative disparity, not LiDAR-grade ground truth metric depth.
The metric scaling (2m - 60m) is a heuristic inverse-disparity calibration assuming standard dashcam
perspective geometry. Distance and closing speed values are estimates suitable for situational awareness
and behavioral planning, not certifiable range measurements.
"""

import os
import sys
import time
import json
import argparse
from typing import Dict, List, Any, Optional, Tuple
import cv2
import numpy as np
from PIL import Image
from transformers import pipeline


class DepthEstimator:
    def __init__(self, model_name: str = "depth-anything/Depth-Anything-V2-Small-hf", device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        print(f"[DepthEstimator] Initializing {model_name} on {device}...")
        t0 = time.time()
        self.pipe = pipeline("depth-estimation", model=model_name, device=device)
        print(f"[DepthEstimator] Pipeline initialized in {time.time() - t0:.2f}s")

        # Heuristic calibration limits (meters)
        self.d_min = 2.0
        self.d_max = 60.0

    def infer_depth(self, frame_bgr: np.ndarray, infer_size: Tuple[int, int] = (640, 360)) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Runs monocular depth estimation on a BGR frame.
        For CPU optimization, inferences at infer_size and upsamples to native resolution.
        Returns:
            norm_disp: normalized disparity [0, 1] (1 = closest, 0 = farthest)
            distance_map: estimated metric distance in meters (d_min to d_max)
            heatmap: BGR colorized depth visualization (cv2.COLORMAP_INFERNO)
        """
        orig_h, orig_w = frame_bgr.shape[:2]

        # Resize for efficient inference
        if infer_size is not None and (orig_w != infer_size[0] or orig_h != infer_size[1]):
            small_bgr = cv2.resize(frame_bgr, infer_size, interpolation=cv2.INTER_AREA)
        else:
            small_bgr = frame_bgr

        small_rgb = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(small_rgb)

        # Run pipeline
        res = self.pipe(pil_img)
        depth_pil = res["depth"]
        raw_disp = np.array(depth_pil, dtype=np.float32)

        # Resize raw disparity back to original frame dimensions
        if raw_disp.shape[:2] != (orig_h, orig_w):
            raw_disp = cv2.resize(raw_disp, (orig_w, orig_h), interpolation=cv2.INTER_CUBIC)

        # Robust percentile normalization to remove sky/hood outliers
        p_low = np.percentile(raw_disp, 2)
        p_high = np.percentile(raw_disp, 98)
        disp_clipped = np.clip(raw_disp, p_low, p_high)
        norm_disp = (disp_clipped - p_low) / (p_high - p_low + 1e-6)
        norm_disp = np.clip(norm_disp, 0.0, 1.0)

        # Inverse disparity to metric distance mapping:
        # Z = (d_min * d_max) / (d_min + norm_disp * (d_max - d_min))
        # At norm_disp = 1.0 (closest) -> Z = d_min (2.0m)
        # At norm_disp = 0.0 (farthest) -> Z = d_max (60.0m)
        distance_map = (self.d_min * self.d_max) / (self.d_min + norm_disp * (self.d_max - self.d_min))

        # Colorized visualization (closer = bright yellow/white, farther = dark purple)
        disp_8u = (norm_disp * 255).astype(np.uint8)
        heatmap = cv2.applyColorMap(disp_8u, cv2.COLORMAP_INFERNO)

        return norm_disp, distance_map, heatmap

    def sample_object_depth(self, bbox: List[float], distance_map: np.ndarray) -> float:
        """
        Samples the median depth inside the tracked bounding box.
        Uses the inner 60% of bbox to minimize background edge bleed.
        """
        h_img, w_img = distance_map.shape[:2]
        x1, y1, x2, y2 = bbox

        bw = x2 - x1
        bh = y2 - y1
        ix1 = int(np.clip(x1 + 0.20 * bw, 0, w_img - 1))
        ix2 = int(np.clip(x2 - 0.20 * bw, 0, w_img - 1))
        iy1 = int(np.clip(y1 + 0.20 * bh, 0, h_img - 1))
        iy2 = int(np.clip(y2 - 0.05 * bh, 0, h_img - 1))

        if ix2 <= ix1 or iy2 <= iy1:
            ix1, ix2 = int(np.clip(x1, 0, w_img - 1)), int(np.clip(x2, 0, w_img - 1))
            iy1, iy2 = int(np.clip(y1, 0, h_img - 1)), int(np.clip(y2, 0, h_img - 1))

        box_depths = distance_map[iy1:max(iy1+1, iy2), ix1:max(ix1+1, ix2)]
        if box_depths.size == 0:
            return float(self.d_max)

        median_dist = float(np.median(box_depths))
        return round(median_dist, 2)


def render_side_by_side(
    rgb_frame: np.ndarray,
    heatmap: np.ndarray,
    tracked_objects: List[Dict[str, Any]],
    fps: float,
    frame_idx: int
) -> np.ndarray:
    """
    Renders side-by-side comparison:
    Left: RGB frame with bounding boxes and estimated depth in meters.
    Right: Depth heatmap with bounding boxes.
    """
    canvas_left = rgb_frame.copy()
    canvas_right = heatmap.copy()

    for obj in tracked_objects:
        tid = obj.get("track_id", -1)
        cname = obj.get("class_name", "obj")
        dist = obj.get("estimated_depth_m", None)
        x1, y1, x2, y2 = [int(v) for v in obj["bbox"]]

        color = (0, 255, 128) if tid >= 0 else (180, 180, 180)

        # Left canvas
        cv2.rectangle(canvas_left, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
        label_left = f"#{tid} {cname}"
        if dist is not None:
            label_left += f" | {dist:.1f}m"

        (tw, th), _ = cv2.getTextSize(label_left, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.rectangle(canvas_left, (x1, max(0, y1 - th - 6)), (x1 + tw + 6, y1), color, -1)
        cv2.putText(canvas_left, label_left, (x1 + 3, y1 - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)

        # Right canvas (Heatmap)
        cv2.rectangle(canvas_right, (x1, y1), (x2, y2), (255, 255, 255), 2, cv2.LINE_AA)
        label_right = f"{dist:.1f}m" if dist is not None else f"#{tid}"
        cv2.putText(canvas_right, label_right, (x1 + 4, y2 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2, cv2.LINE_AA)

    # Add header titles
    cv2.putText(canvas_left, "RGB Camera + Detected Tracks", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(canvas_right, "Depth Anything V2 Heatmap", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

    # Combine side-by-side
    combined = np.hstack([canvas_left, canvas_right])
    ch, cw = combined.shape[:2]

    # Global status banner at bottom
    cv2.rectangle(combined, (0, ch - 35), (cw, ch), (15, 15, 15), -1)
    status_text = f"Frame: {frame_idx:04d} | Processing Speed: {fps:4.1f} FPS | Model: Depth-Anything-V2-Small (CPU) | Range: 2.0m - 60.0m (Heuristic)"
    cv2.putText(combined, status_text, (20, ch - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 215, 255), 1, cv2.LINE_AA)

    return combined


def process_video_depth(
    video_path: str,
    tracking_json_path: Optional[str] = None,
    output_video_path: Optional[str] = None,
    output_json_path: Optional[str] = None,
    max_frames: Optional[int] = None
) -> Dict[str, Any]:
    """
    Processes video with monocular depth estimation:
    - Loads precomputed tracking from Phase 2
    - Computes dense depth per frame using Depth Anything V2 Small
    - Samples median distance for each tracked bounding box
    - Saves side-by-side preview video and enriched JSON
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    # Load existing tracking data if provided
    tracking_data = None
    if tracking_json_path and os.path.exists(tracking_json_path):
        print(f"[Phase 3] Loading precomputed tracking data from {tracking_json_path}...")
        with open(tracking_json_path, "r") as f:
            tracking_data = json.load(f)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames_to_process = min(total_frames, max_frames) if max_frames else total_frames

    print(f"\n[Phase 3] Processing Depth: {os.path.basename(video_path)}")
    print(f"  Resolution: {width}x{height} | Native FPS: {fps:.2f} | Frames to process: {frames_to_process}")

    depth_estimator = DepthEstimator()

    writer = None
    if output_video_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_video_path, fourcc, fps, (width * 2, height))

    enriched_data = {
        "metadata": {
            "source_video": video_path,
            "width": width,
            "height": height,
            "fps": fps,
            "total_frames": frames_to_process,
            "depth_model": depth_estimator.model_name,
            "calibration_note": "Monocular relative disparity scaled heuristically to 2m-60m range."
        },
        "frames": []
    }

    frame_idx = 0
    t_start = time.time()
    rolling_fps = 0.0

    while cap.isOpened() and frame_idx < frames_to_process:
        t_frame_start = time.time()
        ret, frame = cap.read()
        if not ret:
            break

        # 1. Infer Depth
        _, distance_map, heatmap = depth_estimator.infer_depth(frame)

        # 2. Get tracked objects for this frame
        current_objects = []
        if tracking_data and "frames" in tracking_data and frame_idx < len(tracking_data["frames"]):
            current_objects = tracking_data["frames"][frame_idx].get("objects", [])

        # 3. Sample median depth for each object
        for obj in current_objects:
            est_depth = depth_estimator.sample_object_depth(obj["bbox"], distance_map)
            obj["estimated_depth_m"] = est_depth

        enriched_data["frames"].append({
            "frame_idx": frame_idx,
            "timestamp_sec": round(frame_idx / fps, 3),
            "objects": current_objects
        })

        dt = time.time() - t_frame_start
        instant_fps = 1.0 / dt if dt > 0 else 0
        rolling_fps = instant_fps if frame_idx == 0 else 0.9 * rolling_fps + 0.1 * instant_fps

        # 4. Write side-by-side preview frame
        if writer is not None:
            sbs_frame = render_side_by_side(frame, heatmap, current_objects, rolling_fps, frame_idx)
            writer.write(sbs_frame)

        frame_idx += 1
        if frame_idx % 20 == 0 or frame_idx == frames_to_process:
            percent = (frame_idx / frames_to_process) * 100.0
            print(f"  Frame {frame_idx:04d}/{frames_to_process:04d} ({percent:5.1f}%) | Speed: {rolling_fps:4.1f} FPS")

    total_time = time.time() - t_start
    avg_fps = frame_idx / total_time if total_time > 0 else 0

    cap.release()
    if writer is not None:
        writer.release()

    enriched_data["metadata"]["processed_frames"] = frame_idx
    enriched_data["metadata"]["elapsed_seconds"] = round(total_time, 2)
    enriched_data["metadata"]["average_fps"] = round(avg_fps, 2)

    if output_json_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_json_path)), exist_ok=True)
        with open(output_json_path, "w") as f:
            json.dump(enriched_data, f, indent=2)
        print(f"  Saved depth tracking data JSON: {output_json_path}")

    if output_video_path:
        print(f"  Saved side-by-side preview video: {output_video_path}")

    print(f"\n[Phase 3 Completed]")
    print(f"  Processed {frame_idx} frames in {total_time:.2f}s (Average: {avg_fps:.2f} FPS)")
    print(f"  Runtime vs Realtime factor: {total_time / (frame_idx / fps):.2f}x native duration")

    return enriched_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PathSense Phase 3: Monocular Relative Depth Estimation")
    parser.add_argument("--input", type=str, default="data/samples/sample_1.mp4", help="Path to input video")
    parser.add_argument("--tracking-json", type=str, default="detect_track_results.json", help="Path to Phase 2 tracking JSON")
    parser.add_argument("--output", type=str, default="depth_preview.mp4", help="Path to side-by-side preview video")
    parser.add_argument("--output-json", type=str, default="depth_results.json", help="Path to output JSON")
    parser.add_argument("--max-frames", type=int, default=None, help="Max frames to process (optional)")
    args = parser.parse_args()

    process_video_depth(
        video_path=args.input,
        tracking_json_path=args.tracking_json,
        output_video_path=args.output,
        output_json_path=args.output_json,
        max_frames=args.max_frames
    )
