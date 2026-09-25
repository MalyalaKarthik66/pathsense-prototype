"""
PathSense - Phase 6: Bird's-Eye-View (BEV) Occupancy Costmap
Module: costmap.py

Projects detected and tracked objects into a metric top-down 2D grid ahead of the ego-vehicle.
Assigns obstacle cost values based on vulnerability classes (pedestrians/animals > two-wheelers > cars/trucks)
and applies 2D Gaussian inflation halos around obstacles for safety margins.
Renders high-resolution BEV heatmaps with distance rings and ego footprint.

COORDINATES & GRID SPECS:
- Forward range (Y): 0.0m to 40.0m ahead of ego bumper (80 cells @ 0.5m/cell).
- Lateral range (X): -20.0m (left) to +20.0m (right) (80 cells @ 0.5m/cell).
- Grid dimensions: 80 x 80 cells (0.5m resolution).
- Ego Vehicle: Centered at bottom (X = 0.0m, Y = 0.0m) -> grid (row=79, col=40).
"""

import os
import sys
import time
import json
import argparse
from typing import Dict, List, Any, Optional, Tuple
import cv2
import numpy as np

# Class-specific cost weights and inflation radii (tuned for unstructured Indian road dynamics)
# footprint_radius: core of the soft cost field (conservative circle, covers object length)
# collision_radius: approx. physical half-width + small margin, used only for the planner's hard collision check
CLASS_SAFETY_PROFILES: Dict[str, Dict[str, Any]] = {
    "person":    {"base_cost": 255, "footprint_radius": 0.8, "collision_radius": 0.5, "inflation_sigma": 2.5, "color": (0, 0, 255)},
    "bicycle":   {"base_cost": 240, "footprint_radius": 1.0, "collision_radius": 0.6, "inflation_sigma": 2.2, "color": (0, 69, 255)},
    "motorcycle":{"base_cost": 220, "footprint_radius": 1.2, "collision_radius": 0.7, "inflation_sigma": 2.0, "color": (0, 140, 255)},
    "cat":       {"base_cost": 250, "footprint_radius": 0.6, "collision_radius": 0.4, "inflation_sigma": 2.0, "color": (128, 0, 255)},
    "dog":       {"base_cost": 250, "footprint_radius": 0.8, "collision_radius": 0.5, "inflation_sigma": 2.2, "color": (128, 0, 255)},
    "sheep":     {"base_cost": 250, "footprint_radius": 0.9, "collision_radius": 0.6, "inflation_sigma": 2.2, "color": (128, 0, 255)},
    "cow":       {"base_cost": 255, "footprint_radius": 1.6, "collision_radius": 1.0, "inflation_sigma": 2.8, "color": (0, 0, 220)},
    "horse":     {"base_cost": 250, "footprint_radius": 1.6, "collision_radius": 1.0, "inflation_sigma": 2.6, "color": (0, 0, 220)},
    "elephant":  {"base_cost": 255, "footprint_radius": 2.2, "collision_radius": 1.6, "inflation_sigma": 3.0, "color": (0, 0, 220)},
    "car":       {"base_cost": 180, "footprint_radius": 1.8, "collision_radius": 1.1, "inflation_sigma": 2.0, "color": (0, 215, 255)},
    "bus":       {"base_cost": 220, "footprint_radius": 2.5, "collision_radius": 1.5, "inflation_sigma": 2.8, "color": (0, 165, 255)},
    "truck":     {"base_cost": 220, "footprint_radius": 2.5, "collision_radius": 1.5, "inflation_sigma": 2.8, "color": (0, 165, 255)},
    "default":   {"base_cost": 180, "footprint_radius": 1.5, "collision_radius": 1.0, "inflation_sigma": 2.0, "color": (0, 255, 255)}
}


class BEVCostmap:
    def __init__(
        self,
        range_x: Tuple[float, float] = (-20.0, 20.0),
        range_y: Tuple[float, float] = (0.0, 40.0),
        resolution: float = 0.50,
        img_width: int = 1280,
        img_height: int = 720,
        fov_deg: float = 70.0,
        near_field_m: float = 2.0,  # physical emergency gap; keep equal to DecisionState.min_gap_m
        hard_hazard_levels: Tuple[str, ...] = ("CRITICAL",)
    ):
        self.near_field_m = near_field_m
        self.hard_hazard_levels = hard_hazard_levels
        self.min_x, self.max_x = range_x
        self.min_y, self.max_y = range_y
        self.res = resolution
        self.w_img = img_width
        self.h_img = img_height

        # Grid dimensions: 80 x 80
        self.grid_cols = int(round((self.max_x - self.min_x) / self.res))
        self.grid_rows = int(round((self.max_y - self.min_y) / self.res))

        # Camera intrinsics
        fov_rad = np.radians(fov_deg)
        self.fx = (self.w_img / 2.0) / np.tan(fov_rad / 2.0)
        self.cx = self.w_img / 2.0

        # Precompute coordinate meshgrid (X in meters, Y in meters)
        xs = np.linspace(self.min_x + self.res/2, self.max_x - self.res/2, self.grid_cols)
        ys = np.linspace(self.max_y - self.res/2, self.min_y + self.res/2, self.grid_rows)
        self.grid_X, self.grid_Y = np.meshgrid(xs, ys)
        self.occupancy = np.zeros((self.grid_rows, self.grid_cols), dtype=bool)

    def project_bbox_to_3d(self, bbox: List[float], depth_m: float) -> Tuple[float, float]:
        """
        Projects bounding box ground contact point and depth into ego-vehicle 3D coordinates:
        X (lateral right in meters), Y (forward longitudinal in meters).
        """
        x1, y1, x2, y2 = bbox
        u_center = (x1 + x2) / 2.0
        # X = (u - cx) * Z / fx
        x_m = (u_center - self.cx) * depth_m / self.fx
        y_m = depth_m
        return round(float(x_m), 2), round(float(y_m), 2)

    def coord_to_grid(self, x_m: float, y_m: float) -> Tuple[int, int]:
        """
        Converts real-world (X, Y) in meters to grid indices (col, row).
        """
        col = int((x_m - self.min_x) / self.res)
        row = int((self.max_y - y_m) / self.res)
        return col, row

    def build_costmap(self, tracked_objects: List[Dict[str, Any]]) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """
        Constructs the 2D BEV costmap matrix for a single frame.
        Returns:
            costmap: (grid_rows, grid_cols) uint8 array [0, 255]
            projected_objects: list of objects with (x_bev, y_bev) coordinates added
        """
        cost_grid = np.zeros((self.grid_rows, self.grid_cols), dtype=np.float32)
        # Physical obstacle footprints (independent of class cost), used by the planner for hard collision checks
        self.occupancy = np.zeros((self.grid_rows, self.grid_cols), dtype=bool)
        projected = []

        for obj in tracked_objects:
            cname = obj.get("class_name", "default")
            depth_m = obj.get("smoothed_depth_m", obj.get("estimated_depth_m", None))
            bbox = obj.get("bbox", None)

            if depth_m is None or bbox is None or len(bbox) != 4:
                continue
            if not np.isfinite(depth_m) or depth_m <= 0.0 or not np.all(np.isfinite(bbox)):
                continue

            x_m, y_m = self.project_bbox_to_3d(bbox, depth_m)
            # Boxes cut off by the left/right image border (vehicles alongside / being overtaken): the visible centre
            # lies inside the true centre, which would pull the object into the ego corridor. The visible inner edge
            # is a real object edge, so place the centre half an object width beyond it (never further inward).
            half = CLASS_SAFETY_PROFILES.get(cname, CLASS_SAFETY_PROFILES["default"]).get("collision_radius", 1.0)
            if bbox[0] <= 2 and bbox[2] < self.w_img - 2:
                x_m = min(x_m, round(float((bbox[2] - self.cx) * depth_m / self.fx - half), 2))
            elif bbox[2] >= self.w_img - 2 and bbox[0] > 2:
                x_m = max(x_m, round(float((bbox[0] - self.cx) * depth_m / self.fx + half), 2))
            obj_copy = dict(obj)
            obj_copy["bev_x_m"] = x_m
            obj_copy["bev_y_m"] = y_m
            projected.append(obj_copy)

            # Check if within grid bounds
            if not (self.min_x <= x_m <= self.max_x and self.min_y <= y_m <= self.max_y):
                continue

            profile = CLASS_SAFETY_PROFILES.get(cname, CLASS_SAFETY_PROFILES["default"])
            base_cost = profile["base_cost"]
            footprint_r = profile["footprint_radius"]
            sigma = profile["inflation_sigma"]

            # Compute Euclidean distance map from this obstacle center
            dist_sq = (self.grid_X - x_m)**2 + (self.grid_Y - y_m)**2
            dist = np.sqrt(dist_sq)

            # Footprint: lethal cost
            footprint_mask = dist <= footprint_r

            # Inflation halo: Gaussian decay
            inflation_mask = (dist > footprint_r) & (dist <= footprint_r + 3.0 * sigma)
            halo_cost = base_cost * np.exp(-((dist - footprint_r)**2) / (2.0 * sigma**2))

            # Combine
            obj_cost = np.zeros_like(cost_grid)
            obj_cost[footprint_mask] = base_cost
            obj_cost[inflation_mask] = halo_cost[inflation_mask]

            # Aggregate into grid using max
            cost_grid = np.maximum(cost_grid, obj_cost)

            # Hard-collision footprint: obstacles closing critically (TTC < 2 s) or inside the physical emergency gap.
            # Following distance and moderate closing are handled by the decision layer (SLOW DOWN), so a lead vehicle
            # is not turned into a wall that forces swerving or a spurious NO SAFE PATH.
            if obj.get("hazard_level", "SAFE") in self.hard_hazard_levels or y_m <= self.near_field_m:
                self.occupancy |= dist <= profile.get("collision_radius", footprint_r)

        costmap_uint8 = np.clip(cost_grid, 0, 255).astype(np.uint8)
        return costmap_uint8, projected

    def render_bev_image(
        self,
        costmap: np.ndarray,
        projected_objects: List[Dict[str, Any]],
        canvas_size: int = 500,
        planned_path: Optional[List[Tuple[float, float]]] = None
    ) -> np.ndarray:
        """
        Renders an attractive, high-contrast visual BEV display from the costmap.
        """
        # 1. Colorize costmap: 0 = Dark Navy/Charcoal, 255 = Lethal Red
        # Resize costmap to canvas size
        costmap_colored = cv2.applyColorMap(costmap, cv2.COLORMAP_TURBO)
        bev_canvas = cv2.resize(costmap_colored, (canvas_size, canvas_size), interpolation=cv2.INTER_LINEAR)

        # Darken low-cost free space so roads look clean
        free_mask = cv2.resize(costmap, (canvas_size, canvas_size), interpolation=cv2.INTER_NEAREST) < 15
        bev_canvas[free_mask] = (bev_canvas[free_mask].astype(np.float32) * 0.25).astype(np.uint8)

        # Helper to convert (x_m, y_m) to canvas pixel coordinates
        def to_pixel(xm: float, ym: float) -> Tuple[int, int]:
            px = int((xm - self.min_x) / (self.max_x - self.min_x) * canvas_size)
            py = int((self.max_y - ym) / (self.max_y - self.min_y) * canvas_size)
            return px, py

        # 2. Draw Range Rings (10m, 20m, 30m, 40m)
        ego_px, ego_py = to_pixel(0.0, 0.0)
        ring_color = (60, 75, 90)

        for ring_dist in [10.0, 20.0, 30.0]:
            _, ring_top_py = to_pixel(0.0, ring_dist)
            radius_px = ego_py - ring_top_py
            cv2.circle(bev_canvas, (ego_px, ego_py), radius_px, ring_color, 1, cv2.LINE_AA)
            cv2.putText(
                bev_canvas, f"{int(ring_dist)}m", (ego_px + 8, ring_top_py + 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (140, 160, 180), 1, cv2.LINE_AA
            )

        # 3. Draw Lane Guideline Corridors (-3.5m, 0m, +3.5m)
        for lane_x in [-3.5, 0.0, 3.5]:
            lx1, ly1 = to_pixel(lane_x, 0.0)
            lx2, ly2 = to_pixel(lane_x, 40.0)
            l_color = (80, 95, 110) if lane_x != 0.0 else (100, 120, 140)
            cv2.line(bev_canvas, (lx1, ly1), (lx2, ly2), l_color, 1, cv2.LINE_AA)

        # 4. Draw Planned Path (if provided)
        if planned_path and len(planned_path) >= 2:
            path_pixels = [to_pixel(px, py) for px, py in planned_path]
            cv2.polylines(bev_canvas, [np.array(path_pixels, dtype=np.int32)], isClosed=False, color=(0, 255, 0), thickness=3, lineType=cv2.LINE_AA)

        # 5. Draw Obstacle Markers & Labels
        for obj in projected_objects:
            xm = obj.get("bev_x_m", 0.0)
            ym = obj.get("bev_y_m", 0.0)
            cname = obj.get("class_name", "obj")
            tid = obj.get("track_id", -1)
            hazard = obj.get("hazard_level", "SAFE")

            if self.min_x <= xm <= self.max_x and self.min_y <= ym <= self.max_y:
                ox, oy = to_pixel(xm, ym)
                m_color = (0, 0, 255) if hazard == "CRITICAL" else (0, 255, 255)
                # Outer circle
                cv2.circle(bev_canvas, (ox, oy), 5, m_color, -1, cv2.LINE_AA)
                cv2.circle(bev_canvas, (ox, oy), 7, (255, 255, 255), 1, cv2.LINE_AA)
                # Text tag
                label = f"#{tid} {cname[:3]}"
                cv2.putText(bev_canvas, label, (ox + 8, oy + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 255, 255), 1, cv2.LINE_AA)

        # 6. Draw Ego Vehicle Icon (Bottom Center)
        # Vehicle dimensions: ~1.8m width, 4.0m length
        vx1, vy1 = to_pixel(-0.9, 0.0)
        vx2, vy2 = to_pixel(0.9, 4.0)
        cv2.rectangle(bev_canvas, (vx1, vy2), (vx2, vy1), (255, 120, 0), -1) # Blue ego vehicle
        cv2.rectangle(bev_canvas, (vx1, vy2), (vx2, vy1), (255, 255, 255), 1)
        # Direction arrow
        cv2.arrowedLine(bev_canvas, (ego_px, vy1), (ego_px, vy2), (255, 255, 255), 2, cv2.LINE_AA, tipLength=0.4)
        cv2.putText(bev_canvas, "EGO", (ego_px - 14, vy1 + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 255, 255), 1, cv2.LINE_AA)

        # 7. Add Header Border
        cv2.rectangle(bev_canvas, (0, 0), (canvas_size, 26), (15, 20, 25), -1)
        cv2.putText(bev_canvas, "BEV Costmap (-20m to +20m, 40m Ahead)", (10, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 215, 255), 1, cv2.LINE_AA)
        cv2.rectangle(bev_canvas, (0, 0), (canvas_size - 1, canvas_size - 1), (0, 215, 255), 1)

        return bev_canvas


def render_composite_preview(
    dashcam_frame: np.ndarray,
    bev_panel: np.ndarray,
    tracked_objects: List[Dict[str, Any]],
    frame_idx: int,
    total_frames: int,
    fps: float
) -> np.ndarray:
    """
    Renders side-by-side composite:
    Left: Dashcam view with 3D projection tags (X, Y) and bounding boxes.
    Right: Top-down BEV Costmap panel.
    """
    h, w = dashcam_frame.shape[:2]
    canvas_left = dashcam_frame.copy()

    # Draw 3D projection tags on bounding boxes
    for obj in tracked_objects:
        tid = obj.get("track_id", -1)
        cname = obj.get("class_name", "obj")
        bbox = obj.get("bbox", [0, 0, 0, 0])
        xm = obj.get("bev_x_m", None)
        ym = obj.get("bev_y_m", None)
        hazard = obj.get("hazard_level", "SAFE")
        color = (0, 0, 255) if hazard == "CRITICAL" else (0, 255, 128)

        x1, y1, x2, y2 = [int(v) for v in bbox]
        cv2.rectangle(canvas_left, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)

        if xm is not None and ym is not None:
            tag = f"#{tid} {cname} | X:{xm:+.1f}m Y:{ym:.1f}m"
        else:
            tag = f"#{tid} {cname}"

        (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.rectangle(canvas_left, (x1, max(0, y1 - th - 6)), (x1 + tw + 6, y1), color, -1)
        cv2.putText(canvas_left, tag, (x1 + 3, y1 - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)

    # Resize BEV panel to match frame height
    bev_resized = cv2.resize(bev_panel, (h, h), interpolation=cv2.INTER_LINEAR)

    # Composite side-by-side: [Dashcam (W x H) | BEV (H x H)]
    composite = np.hstack([canvas_left, bev_resized])
    cw = composite.shape[1]

    # Global banner at bottom
    cv2.rectangle(composite, (0, h - 35), (cw, h), (15, 15, 15), -1)
    status_text = f"PathSense BEV Pipeline | Frame: {frame_idx:04d}/{total_frames:04d} | Processing: {fps:.1f} FPS | Grid: 40mx40m (0.5m/cell)"
    cv2.putText(composite, status_text, (20, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 215, 255), 1, cv2.LINE_AA)

    return composite


def process_video_costmap(
    video_path: str,
    ttc_json_path: str,
    output_video_path: Optional[str] = None,
    output_json_path: Optional[str] = None,
    max_frames: Optional[int] = None
) -> Dict[str, Any]:
    """
    Processes video, builds 2D BEV costmaps per frame, and exports composite preview video.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not os.path.exists(ttc_json_path):
        raise FileNotFoundError(f"TTC JSON not found: {ttc_json_path}")

    with open(ttc_json_path, "r") as f:
        tracking_data = json.load(f)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames_to_process = min(total_frames, max_frames) if max_frames else total_frames

    print(f"\n[Phase 6] Building BEV Costmaps: {os.path.basename(video_path)}")
    print(f"  Resolution: {width}x{height} | Native FPS: {fps:.2f} | Frames to process: {frames_to_process}")

    costmap_builder = BEVCostmap(img_width=width, img_height=height)

    writer = None
    if output_video_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
        # Composite width: width + height (BEV is height x height)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_video_path, fourcc, fps, (width + height, height))

    costmap_results = {
        "metadata": {
            "source_video": video_path,
            "grid_resolution_m": costmap_builder.res,
            "grid_range_x_m": [costmap_builder.min_x, costmap_builder.max_x],
            "grid_range_y_m": [costmap_builder.min_y, costmap_builder.max_y],
            "grid_shape": [costmap_builder.grid_rows, costmap_builder.grid_cols],
            "total_frames": frames_to_process
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

        current_objects = []
        if frame_idx < len(tracking_data.get("frames", [])):
            current_objects = tracking_data["frames"][frame_idx].get("objects", [])

        # Build costmap
        cost_grid, projected_objs = costmap_builder.build_costmap(current_objects)
        bev_image = costmap_builder.render_bev_image(cost_grid, projected_objs)

        costmap_results["frames"].append({
            "frame_idx": frame_idx,
            "timestamp_sec": round(frame_idx / fps, 3),
            "objects": projected_objs,
            "active_obstacles_in_grid": len(projected_objs)
        })

        dt = time.time() - t_frame_start
        instant_fps = 1.0 / dt if dt > 0 else 0
        rolling_fps = instant_fps if frame_idx == 0 else 0.9 * rolling_fps + 0.1 * instant_fps

        if writer is not None:
            composite_frame = render_composite_preview(
                frame, bev_image, projected_objs, frame_idx, frames_to_process, rolling_fps
            )
            writer.write(composite_frame)

        frame_idx += 1
        if frame_idx % 50 == 0 or frame_idx == frames_to_process:
            percent = (frame_idx / frames_to_process) * 100.0
            print(f"  Frame {frame_idx:04d}/{frames_to_process:04d} ({percent:5.1f}%) | Speed: {rolling_fps:4.1f} FPS")

    total_time = time.time() - t_start
    avg_fps = frame_idx / total_time if total_time > 0 else 0

    cap.release()
    if writer is not None:
        writer.release()

    costmap_results["metadata"]["processed_frames"] = frame_idx
    costmap_results["metadata"]["elapsed_seconds"] = round(total_time, 2)
    costmap_results["metadata"]["average_fps"] = round(avg_fps, 2)

    if output_json_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_json_path)), exist_ok=True)
        with open(output_json_path, "w") as f:
            json.dump(costmap_results, f, indent=2)
        print(f"  Saved costmap results JSON: {output_json_path}")

    if output_video_path:
        print(f"  Saved costmap preview video: {output_video_path}")

    print(f"\n[Phase 6 Completed]")
    print(f"  Processed {frame_idx} frames in {total_time:.2f}s ({avg_fps:.2f} FPS)")

    return costmap_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PathSense Phase 6: BEV Costmap")
    parser.add_argument("--input", type=str, default="data/samples/sample_1.mp4", help="Path to input video")
    parser.add_argument("--ttc-json", type=str, default="ttc_results.json", help="Path to Phase 4 TTC JSON")
    parser.add_argument("--output", type=str, default="costmap_preview.mp4", help="Path to preview video")
    parser.add_argument("--output-json", type=str, default="costmap_results.json", help="Path to output JSON")
    parser.add_argument("--max-frames", type=int, default=None, help="Max frames to process (optional)")
    args = parser.parse_args()

    process_video_costmap(
        video_path=args.input,
        ttc_json_path=args.ttc_json,
        output_video_path=args.output,
        output_json_path=args.output_json,
        max_frames=args.max_frames
    )
