"""
PathSense - Phase 3: Monocular Relative Depth Estimation
Module: depth.py

Uses pretrained Depth Anything V2 Small (Hugging Face / transformers) to infer dense relative depth
from front-facing dashcam video and samples the robust median distance inside each tracked bounding box.
Generates side-by-side RGB + Depth heatmap preview video.

Two distance modes:
- "ground" (default): road-plane calibration. Depth Anything's raw output is an affine-invariant inverse depth,
  so on the visible road surface 1/Z = a * disp + b. Under the flat-road pinhole model a road pixel in row v has
  1/Z = (v - cy) / (fy * h). Fitting (a, b) per frame on unoccupied road pixels (EMA-smoothed) turns the relative
  map into a geometry-consistent distance estimate. Horizon cy and camera height h default to 0.55*H / 1.30 m and are
  refined online from detected cars (box bottom vs box width, assuming ~1.75 m car width) when enough evidence exists.
- "heuristic": the original fixed per-frame mapping of normalised disparity to 2-60 m.

NOTE ON CALIBRATION LIMITATION:
Neither mode is calibrated metric depth. "ground" depends on the flat-road assumption, the FOV/horizon/camera-height
geometry and a typical car width; on Indian urban footage it removed the ~2x range compression of the heuristic mode
(validated against width-prior distances). Values are estimates for situational awareness and behavioral planning,
not certifiable range measurements.
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


class GroundPlaneCalibrator:
    """Maps Depth Anything relative disparity to distance using the road plane (see module docstring)."""

    def __init__(
        self,
        width: int,
        height: int,
        fov_deg: float = 70.0,
        horizon_ratio: float = 0.55,
        camera_height_m: float = 1.30,
        auto_geometry: bool = True,
        car_width_m: float = 1.75,
        fit_ema_alpha: float = 0.20,
        min_distance_m: float = 1.0,
        max_distance_m: float = 80.0
    ):
        self.w, self.h_img = width, height
        self.fx = (width / 2.0) / np.tan(np.radians(fov_deg) / 2.0)
        self.fy = self.fx
        self.cy = horizon_ratio * height
        self.cam_h = camera_height_m
        self.auto_geometry = auto_geometry
        self.car_w = car_width_m
        self.alpha = fit_ema_alpha
        self.z_min, self.z_max = min_distance_m, max_distance_m
        self.geometry_source = "default"
        self.ab: Optional[np.ndarray] = None  # EMA-smoothed (a, b) of 1/Z = a*disp + b
        self.last_fit_r2 = 0.0
        self._car_obs: List[Tuple[float, float]] = []
        self._obs_at_last_refit = 0

    @property
    def calibrated(self) -> bool:
        return self.ab is not None

    def observe_vehicles(self, objects: List[Dict[str, Any]]):
        """Accumulates (box width, box bottom) of untruncated cars to self-calibrate horizon and camera height."""
        if not self.auto_geometry:
            return
        for o in objects:
            x1, y1, x2, y2 = o["bbox"]
            if (o.get("class_name") == "car" and o.get("confidence", 0.0) > 0.5 and x1 > 3 and x2 < self.w - 3
                    and y2 < self.h_img - 3 and 25 < x2 - x1 < 0.5 * self.w and y2 > 0.45 * self.h_img):
                self._car_obs.append((x2 - x1, y2))
        if len(self._car_obs) > 6000:
            self._car_obs = self._car_obs[-6000:]
        if len(self._car_obs) >= 200 and len(self._car_obs) - self._obs_at_last_refit >= 100:
            self._obs_at_last_refit = len(self._car_obs)
            self._refit_geometry()

    def _refit_geometry(self):
        obs = np.array(self._car_obs, dtype=np.float64)
        ws, ys = obs[:, 0], obs[:, 1]
        keep = np.ones(len(ws), bool)
        cy, k = self.cy, 0.0
        for _ in range(5):  # trimmed least squares of  y2 = cy + (h / car_w) * w
            A = np.vstack([np.ones(keep.sum()), ws[keep]]).T
            (cy, k), *_ = np.linalg.lstsq(A, ys[keep], rcond=None)
            res = np.abs(ys - (cy + k * ws))
            keep = res < max(8.0, np.percentile(res, 80))
        cam_h = k * self.car_w
        spread = np.percentile(ws, 90) / max(1.0, np.percentile(ws, 10))
        # Accept only well-conditioned, physically plausible estimates; otherwise keep the defaults
        if spread >= 2.5 and 0.40 * self.h_img <= cy <= 0.70 * self.h_img and 0.8 <= cam_h <= 2.0:
            self.cy, self.cam_h, self.geometry_source = float(cy), float(cam_h), "self-calibrated"

    def fit_frame(self, disp: np.ndarray, road_mask: np.ndarray) -> bool:
        """Robust per-frame fit of 1/Z = a*disp + b on road pixels; disp/mask are at inference resolution."""
        rh, rw = disp.shape
        rows, cols = np.nonzero(road_mask)
        if len(rows) < 60:
            return False
        if len(rows) > 600:
            idx = np.linspace(0, len(rows) - 1, 600).astype(int)
            rows, cols = rows[idx], cols[idx]
        return self.fit_samples(rows * (self.h_img / rh), disp[rows, cols])

    def fit_samples(self, v: np.ndarray, disp_values: np.ndarray) -> bool:
        """Fit from road samples: image rows v (native-resolution pixels) and their raw disparities."""
        v = np.asarray(v, dtype=np.float64)
        ok = v > self.cy + 0.08 * self.cy
        if ok.sum() < 60:
            return False
        d = np.asarray(disp_values, dtype=np.float64)[ok]
        inv = (v[ok] - self.cy) / (self.fy * self.cam_h)
        keep = np.ones(len(d), bool)
        a = b = 0.0
        for _ in range(4):
            A = np.vstack([d[keep], np.ones(keep.sum())]).T
            (a, b), *_ = np.linalg.lstsq(A, inv[keep], rcond=None)
            res = np.abs(inv - (a * d + b))
            keep = res < max(1e-3, 1.5 * np.percentile(res, 75))
        r2 = 1.0 - np.var(inv[keep] - (a * d[keep] + b)) / max(1e-12, np.var(inv[keep]))
        self.last_fit_r2 = float(r2)
        if a <= 0 or r2 < 0.5:
            return False
        ab = np.array([a, b])
        self.ab = ab if self.ab is None else self.alpha * ab + (1.0 - self.alpha) * self.ab
        return True

    def to_distance(self, disp):
        inv = self.ab[0] * np.asarray(disp, dtype=np.float32) + self.ab[1]
        return np.clip(1.0 / np.maximum(inv, 1.0 / self.z_max), self.z_min, self.z_max).astype(np.float32)

    def road_mask(self, shape: Tuple[int, int], obstacle_boxes: List[List[float]]) -> np.ndarray:
        """Lower-central road trapezoid (above the hood line) minus detected objects, at inference resolution."""
        rh, rw = shape
        sx, sy = rw / self.w, rh / self.h_img
        mask = np.zeros((rh, rw), bool)
        top = int(max(self.cy / self.h_img + 0.03, 0.50) * rh)
        bot = int(0.88 * rh)
        for r in range(top, bot):
            frac = (r - top) / max(1, bot - top)
            half = 0.10 + 0.25 * frac
            mask[r, int((0.5 - half) * rw):int((0.5 + half) * rw)] = True
        for x1, y1, x2, y2 in obstacle_boxes:
            mask[max(0, int(y1 * sy) - 2):int(y2 * sy) + 3, max(0, int(x1 * sx) - 2):int(x2 * sx) + 3] = False
        return mask


class DepthEstimator:
    def __init__(
        self,
        model_name: str = "depth-anything/Depth-Anything-V2-Small-hf",
        device: Optional[str] = None,
        mode: str = "ground",
        horizon_ratio: float = 0.55,
        camera_height_m: float = 1.30,
        auto_geometry: bool = True
    ):
        if mode not in ("ground", "heuristic"):
            raise ValueError(f"Unknown depth mode: {mode}")
        self.mode = mode
        self.horizon_ratio = horizon_ratio
        self.camera_height_m = camera_height_m
        self.auto_geometry = auto_geometry
        self.calibrator: Optional[GroundPlaneCalibrator] = None
        self.frames_heuristic_fallback = 0
        if device is None:
            import torch
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.model_name = model_name
        self.device = device
        print(f"[DepthEstimator] Initializing {model_name} on {device}...")
        t0 = time.time()
        import torch
        # FP16 on GPU roughly halves depth latency; CPU stays FP32
        dtype = torch.float16 if str(device).startswith("cuda") else torch.float32
        self.pipe = pipeline("depth-estimation", model=model_name, device=device, dtype=dtype)
        print(f"[DepthEstimator] Pipeline initialized in {time.time() - t0:.2f}s")

        # Heuristic calibration limits (meters)
        self.d_min = 2.0
        self.d_max = 60.0

    def infer_depth(
        self,
        frame_bgr: np.ndarray,
        infer_long_side: int = 640,
        objects: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Runs monocular depth estimation on a BGR frame.
        Downscales (aspect-preserving) so the long side is at most infer_long_side, then upsamples to native resolution.
        objects: current detections (bbox, class_name, confidence); in "ground" mode they are excluded from the road
                 fit and cars are used to self-calibrate horizon / camera height.
        Returns:
            norm_disp: normalized disparity [0, 1] (1 = closest, 0 = farthest)
            distance_map: estimated distance in meters (see module docstring), NOT calibrated metric depth
            heatmap: BGR colorized depth visualization (cv2.COLORMAP_INFERNO)
        """
        orig_h, orig_w = frame_bgr.shape[:2]

        # Aspect-preserving downscale for efficient inference (never distorts non-16:9 inputs)
        scale = infer_long_side / float(max(orig_w, orig_h)) if infer_long_side else 1.0
        if scale < 1.0:
            small_size = (max(1, int(round(orig_w * scale))), max(1, int(round(orig_h * scale))))
            small_bgr = cv2.resize(frame_bgr, small_size, interpolation=cv2.INTER_AREA)
        else:
            small_bgr = frame_bgr

        small_rgb = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(small_rgb)

        # Run pipeline
        res = self.pipe(pil_img)
        ground_distance = None
        if self.mode == "ground":
            ground_distance = self._ground_distance(res, orig_w, orig_h, objects or [])
        depth_pil = res["depth"]
        raw_disp = np.array(depth_pil, dtype=np.float32)
        raw_disp = np.nan_to_num(raw_disp, nan=0.0, posinf=0.0, neginf=0.0)

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
        if ground_distance is not None:
            distance_map = ground_distance
        elif self.mode == "ground":
            self.frames_heuristic_fallback += 1  # no road fit yet: heuristic mapping for this frame

        # Colorized visualization (closer = bright yellow/white, farther = dark purple)
        disp_8u = (norm_disp * 255).astype(np.uint8)
        heatmap = cv2.applyColorMap(disp_8u, cv2.COLORMAP_INFERNO)

        return norm_disp, distance_map, heatmap

    def _ground_distance(self, res: Dict[str, Any], orig_w: int, orig_h: int,
                         objects: List[Dict[str, Any]]) -> Optional[np.ndarray]:
        """Road-plane-calibrated distance map at native resolution, or None while no valid fit exists."""
        raw = res.get("predicted_depth")
        if raw is None:
            return None
        disp = np.nan_to_num(raw.float().cpu().numpy().squeeze().astype(np.float32))
        if self.calibrator is None or (self.calibrator.w, self.calibrator.h_img) != (orig_w, orig_h):
            self.calibrator = GroundPlaneCalibrator(orig_w, orig_h, horizon_ratio=self.horizon_ratio,
                                                    camera_height_m=self.camera_height_m,
                                                    auto_geometry=self.auto_geometry)
        cal = self.calibrator
        cal.observe_vehicles(objects)
        cal.fit_frame(disp, cal.road_mask(disp.shape, [o["bbox"] for o in objects]))
        if not cal.calibrated:
            return None
        dist = cal.to_distance(disp)
        if dist.shape != (orig_h, orig_w):
            dist = cv2.resize(dist, (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)
        return dist

    @property
    def geometry(self) -> Dict[str, Any]:
        """Current camera geometry used for distances (horizon row, camera height, source)."""
        c = self.calibrator
        if self.mode != "ground" or c is None:
            return {"mode": self.mode}
        return {"mode": self.mode, "horizon_px": round(c.cy, 1), "camera_height_m": round(c.cam_h, 2),
                "source": c.geometry_source, "calibrated": c.calibrated, "last_fit_r2": round(c.last_fit_r2, 2)}

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
        if not np.isfinite(median_dist) or median_dist <= 0.0:
            return float(self.d_max)
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
    status_text = f"Frame: {frame_idx:04d} | Processing Speed: {fps:4.1f} FPS | Model: Depth-Anything-V2-Small | Range: 2.0m - 60.0m (Heuristic)"
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
