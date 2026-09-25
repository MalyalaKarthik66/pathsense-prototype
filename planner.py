"""
PathSense - Phase 7: Dynamic Arc Path Planning & Steering Angle Estimation
Module: planner.py

Generates a fan of forward candidate circular arcs from the ego vehicle, evaluates each
path against the BEV obstacle costmap, and selects the lowest-cost collision-free trajectory.
Converts the chosen path curvature into a recommended steering angle via a kinematic bicycle model.
Renders candidate arcs and the chosen optimal path onto the BEV costmap preview.

VEHICLE MODEL & ASSUMPTIONS:
- Kinematic Bicycle Model: tan(delta) = L * kappa  ==>  delta = arctan(L * kappa).
- Wheelbase (L): 2.70 meters (standard passenger car / crossover).
- Maximum Steering Angle (delta_max): +/- 15 degrees (curvature kappa_max ≈ 0.099 m^-1). Wider fans let
  sharp arcs turn 90 deg before reaching obstacles and score as free, so a blocked road never triggered BRAKE.
- Lookahead Distance (s_max): 22.0 meters ahead.
"""

import os
import sys
import time
import json
import argparse
from typing import Dict, List, Any, Optional, Tuple
import cv2
import numpy as np

from costmap import BEVCostmap, render_composite_preview


class DynamicArcPlanner:
    def __init__(
        self,
        wheelbase_m: float = 2.70,
        max_steering_deg: float = 15.0,
        num_candidates: int = 17,
        lookahead_m: float = 22.0,
        step_m: float = 0.50,
        weight_obstacle: float = 1.0,
        weight_center_bias: float = 60.0,  # 12 caused a constant 4-12 deg drift away from adjacent-lane cars
        weight_smoothness: float = 15.0,
        steer_ema_alpha: float = 0.30,
        vehicle_half_width_m: float = 0.9,
        lethal_cost: float = 240.0
    ):
        self.L = wheelbase_m
        self.lethal_cost = lethal_cost
        self.half_width = vehicle_half_width_m
        self.max_steer_deg = max_steering_deg
        self.max_steer_rad = np.radians(max_steering_deg)
        self.num_candidates = num_candidates
        self.lookahead = lookahead_m
        self.step_m = step_m
        self.w_obs = weight_obstacle
        self.w_center = weight_center_bias
        self.w_smooth = weight_smoothness
        self.steer_alpha = steer_ema_alpha

        # Maximum curvature from max steering angle
        self.max_kappa = np.tan(self.max_steer_rad) / self.L

        # Generate curvature candidates (symmetric from -kappa_max to +kappa_max)
        self.candidate_kappas = np.linspace(-self.max_kappa, self.max_kappa, self.num_candidates)

        # Precompute candidate arc waypoints (X, Y) relative to ego (0, 0)
        self.candidate_paths = [self._generate_arc(k) for k in self.candidate_kappas]

        # Reference point count (straight path) used to length-normalise obstacle cost across arcs
        self.ref_points = len(self.candidate_paths[self.num_candidates // 2])

        # State
        self.prev_chosen_kappa = 0.0
        self.smoothed_steer_deg = 0.0
        self.all_blocked = False  # True when every candidate collides with an obstacle footprint

    def _generate_arc(self, kappa: float) -> List[Tuple[float, float]]:
        """
        Generates 2D waypoints along an arc of constant curvature kappa.
        X is lateral (positive right), Y is longitudinal forward.
        """
        s_vals = np.arange(0.0, self.lookahead + self.step_m, self.step_m)
        points = []

        if abs(kappa) < 1e-4:
            # Straight line forward
            for s in s_vals:
                points.append((0.0, float(s)))
        else:
            # Circular arc, truncated at 90 deg heading change: beyond that the arc curls sideways/backwards,
            # which is not a forward trajectory and would leave the grid (falsely scoring as free space)
            s_vals = s_vals[np.abs(kappa) * s_vals <= np.pi / 2.0]
            for s in s_vals:
                # Heading psi = kappa * s
                # X(s) = (1 - cos(kappa * s)) / kappa
                # Y(s) = sin(kappa * s) / kappa
                x = (1.0 - np.cos(kappa * s)) / kappa
                y = np.sin(kappa * s) / kappa
                points.append((float(round(x, 3)), float(round(y, 3))))

        return points

    def evaluate_paths(
        self,
        costmap: np.ndarray,
        costmap_builder: BEVCostmap
    ) -> Tuple[int, float, float, List[Tuple[float, float]], List[float]]:
        """
        Scores all candidate arcs across the BEV costmap and selects the optimal path.
        Returns:
            best_idx: index of selected candidate
            best_kappa: optimal curvature
            best_steer_deg: recommended steering angle in degrees
            best_path: list of (x, y) waypoints of chosen arc
            path_costs: list of total costs for all candidate arcs
        """
        path_costs = []
        lethal_flags = []
        occupancy = getattr(costmap_builder, "occupancy", None)
        rows, cols = costmap_builder.grid_rows, costmap_builder.grid_cols

        def in_grid(c: int, r: int) -> bool:
            return 0 <= r < rows and 0 <= c < cols

        for idx, (kappa, path) in enumerate(zip(self.candidate_kappas, self.candidate_paths)):
            obs_cost = 0.0
            lethal_collision = False

            for (xm, ym) in path:
                if not (costmap_builder.min_x <= xm <= costmap_builder.max_x and
                        costmap_builder.min_y <= ym <= costmap_builder.max_y):
                    continue

                # Sample vehicle body centre and sides (+/- half width)
                samples = [costmap_builder.coord_to_grid(xm + dx, ym) for dx in (0.0, -self.half_width, self.half_width)]
                pt_cost = max((float(costmap[r, c]) for c, r in samples if in_grid(c, r)), default=0.0)

                # Collision = body overlaps any obstacle footprint (any class), or near-lethal cost
                if pt_cost >= self.lethal_cost or (occupancy is not None and any(occupancy[r, c] for c, r in samples if in_grid(c, r))):
                    lethal_collision = True

                # Weight closer obstacles heavier (1 / sqrt(y))
                dist_weight = 1.0 / np.sqrt(max(1.0, ym))
                obs_cost += pt_cost * dist_weight

            # Length-normalise so short (sharply curved) arcs are not favoured just for sampling fewer points
            obs_cost *= self.ref_points / max(1, len(path))

            if lethal_collision:
                obs_cost += 50000.0  # Heavy collision penalty
            lethal_flags.append(lethal_collision)

            # Center bias penalty: encourages staying centered unless evading
            center_penalty = self.w_center * (abs(kappa) / self.max_kappa) ** 2 * 100.0

            # Smoothness penalty: discourages sudden steering twitch
            smooth_penalty = self.w_smooth * (abs(kappa - self.prev_chosen_kappa) / self.max_kappa) ** 2 * 100.0

            total_cost = self.w_obs * obs_cost + center_penalty + smooth_penalty
            path_costs.append(total_cost)

        # Select candidate with minimum cost. If every candidate collides we still return the least-bad arc,
        # but flag it so the HUD can command braking instead of presenting it as a safe path.
        self.all_blocked = all(lethal_flags)
        best_idx = int(np.argmin(path_costs))
        best_kappa = self.candidate_kappas[best_idx]
        self.prev_chosen_kappa = best_kappa

        # Convert curvature to steering angle: delta = arctan(L * kappa)
        raw_steer_rad = np.arctan(self.L * best_kappa)
        raw_steer_deg = float(np.degrees(raw_steer_rad))

        # Temporal EMA smoothing on steering angle
        self.smoothed_steer_deg = (
            self.steer_alpha * raw_steer_deg + (1.0 - self.steer_alpha) * self.smoothed_steer_deg
        )

        best_path = self.candidate_paths[best_idx]
        return best_idx, best_kappa, round(self.smoothed_steer_deg, 2), best_path, path_costs

    def render_bev_with_plan(
        self,
        costmap: np.ndarray,
        projected_objects: List[Dict[str, Any]],
        costmap_builder: BEVCostmap,
        best_path: List[Tuple[float, float]],
        steering_deg: float,
        canvas_size: int = 500
    ) -> np.ndarray:
        """
        Renders BEV costmap with candidate paths (faint lines) and selected path (bold green).
        """
        # Base costmap
        bev_canvas = costmap_builder.render_bev_image(
            costmap, projected_objects, canvas_size=canvas_size, planned_path=None
        )

        def to_pixel(xm: float, ym: float) -> Tuple[int, int]:
            px = int((xm - costmap_builder.min_x) / (costmap_builder.max_x - costmap_builder.min_x) * canvas_size)
            py = int((costmap_builder.max_y - ym) / (costmap_builder.max_y - costmap_builder.min_y) * canvas_size)
            return px, py

        # 1. Draw candidate fan in faint gray/cyan
        for path in self.candidate_paths[::2]:  # Draw every second candidate to avoid visual clutter
            poly = [to_pixel(x, y) for x, y in path]
            cv2.polylines(bev_canvas, [np.array(poly, dtype=np.int32)], isClosed=False, color=(60, 90, 110), thickness=1, lineType=cv2.LINE_AA)

        # 2. Draw chosen path in bold neon green (or yellow if evading)
        poly_best = [to_pixel(x, y) for x, y in best_path]
        path_color = (0, 255, 0) if abs(steering_deg) < 4.0 else (0, 215, 255)
        cv2.polylines(bev_canvas, [np.array(poly_best, dtype=np.int32)], isClosed=False, color=path_color, thickness=3, lineType=cv2.LINE_AA)

        # 3. Steering Angle Gauge at top of BEV panel
        steer_str = f"Steering: {steering_deg:+.1f} deg"
        dir_str = "CENTER" if abs(steering_deg) < 1.5 else ("RIGHT ->" if steering_deg > 0 else "<- LEFT")
        cv2.putText(bev_canvas, f"{steer_str} ({dir_str})", (15, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 0), 1, cv2.LINE_AA)

        return bev_canvas


def draw_steering_indicator(frame: np.ndarray, steering_deg: float) -> np.ndarray:
    """
    Overlays a clean analog steering arc / wheel gauge in the top-right corner of the dashcam frame.
    """
    canvas = frame.copy()
    h, w = canvas.shape[:2]

    # Steering gauge center
    gx, gy = w - 100, 85
    r = 38

    # Background circle
    cv2.circle(canvas, (gx, gy), r, (25, 30, 35), -1, cv2.LINE_AA)
    cv2.circle(canvas, (gx, gy), r, (0, 215, 255), 1, cv2.LINE_AA)

    # Center marker
    cv2.line(canvas, (gx, gy - r + 4), (gx, gy - r + 10), (180, 180, 180), 1, cv2.LINE_AA)

    # Steering needle / pointer
    steer_rad = np.radians(steering_deg)
    # Needle points upwards at 0 deg, rotates with steer_rad
    nx = int(gx + (r - 6) * np.sin(steer_rad))
    ny = int(gy - (r - 6) * np.cos(steer_rad))
    cv2.line(canvas, (gx, gy), (nx, ny), (0, 255, 128), 3, cv2.LINE_AA)
    cv2.circle(canvas, (gx, gy), 4, (255, 255, 255), -1, cv2.LINE_AA)

    label = f"{steering_deg:+.1f}* "
    (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.40, 1)
    cv2.putText(canvas, label, (gx - lw // 2, gy + r + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 215, 255), 1, cv2.LINE_AA)

    return canvas


def process_video_planner(
    video_path: str,
    costmap_json_path: str,
    output_video_path: Optional[str] = None,
    output_json_path: Optional[str] = None,
    max_frames: Optional[int] = None
) -> Dict[str, Any]:
    """
    Runs dynamic arc path planner over BEV costmap for each frame of the video.
    Produces annotated preview video showing candidate fan, selected path, and steering angle.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not os.path.exists(costmap_json_path):
        raise FileNotFoundError(f"Costmap JSON not found: {costmap_json_path}")

    with open(costmap_json_path, "r") as f:
        costmap_data = json.load(f)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames_to_process = min(total_frames, max_frames) if max_frames else total_frames

    print(f"\n[Phase 7] Running Path Planner: {os.path.basename(video_path)}")
    print(f"  Resolution: {width}x{height} | Native FPS: {fps:.2f} | Frames to process: {frames_to_process}")

    costmap_builder = BEVCostmap(img_width=width, img_height=height)
    planner = DynamicArcPlanner(wheelbase_m=2.70, max_steering_deg=15.0, num_candidates=17)

    writer = None
    if output_video_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_video_path, fourcc, fps, (width + height, height))

    planner_results = {
        "metadata": {
            "source_video": video_path,
            "wheelbase_m": planner.L,
            "max_steering_deg": planner.max_steer_deg,
            "candidate_count": planner.num_candidates,
            "lookahead_m": planner.lookahead,
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
        if frame_idx < len(costmap_data.get("frames", [])):
            current_objects = costmap_data["frames"][frame_idx].get("objects", [])

        # 1. Build costmap for this frame
        cost_grid, projected_objs = costmap_builder.build_costmap(current_objects)

        # 2. Plan path
        best_idx, best_kappa, steer_deg, best_path, path_costs = planner.evaluate_paths(
            cost_grid, costmap_builder
        )

        planner_results["frames"].append({
            "frame_idx": frame_idx,
            "timestamp_sec": round(frame_idx / fps, 3),
            "steering_angle_deg": steer_deg,
            "curvature": round(best_kappa, 4),
            "candidate_idx": best_idx,
            "selected_path": best_path[:10]  # First 10 waypoints
        })

        dt = time.time() - t_frame_start
        instant_fps = 1.0 / dt if dt > 0 else 0
        rolling_fps = instant_fps if frame_idx == 0 else 0.9 * rolling_fps + 0.1 * instant_fps

        # 3. Render BEV with planned path
        if writer is not None:
            bev_with_plan = planner.render_bev_with_plan(
                cost_grid, projected_objs, costmap_builder, best_path, steer_deg, canvas_size=height
            )
            # Add steering gauge to dashcam frame
            annotated_cam = draw_steering_indicator(frame, steer_deg)
            # Combine side-by-side
            composite = np.hstack([annotated_cam, bev_with_plan])
            cw = composite.shape[1]

            # Bottom status banner
            cv2.rectangle(composite, (0, height - 35), (cw, height), (15, 15, 15), -1)
            turn_dir = "CENTER" if abs(steer_deg) < 1.5 else ("RIGHT" if steer_deg > 0 else "LEFT")
            status_text = f"PathSense Planner | Frame: {frame_idx:04d}/{frames_to_process:04d} | Steer: {steer_deg:+.1f} deg ({turn_dir}) | Speed: {rolling_fps:.1f} FPS"
            cv2.putText(composite, status_text, (20, height - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 215, 255), 1, cv2.LINE_AA)

            writer.write(composite)

        frame_idx += 1
        if frame_idx % 50 == 0 or frame_idx == frames_to_process:
            percent = (frame_idx / frames_to_process) * 100.0
            print(f"  Frame {frame_idx:04d}/{frames_to_process:04d} ({percent:5.1f}%) | Steer: {steer_deg:+.1f} deg | Speed: {rolling_fps:4.1f} FPS")

    total_time = time.time() - t_start
    avg_fps = frame_idx / total_time if total_time > 0 else 0

    cap.release()
    if writer is not None:
        writer.release()

    planner_results["metadata"]["processed_frames"] = frame_idx
    planner_results["metadata"]["elapsed_seconds"] = round(total_time, 2)
    planner_results["metadata"]["average_fps"] = round(avg_fps, 2)

    if output_json_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_json_path)), exist_ok=True)
        with open(output_json_path, "w") as f:
            json.dump(planner_results, f, indent=2)
        print(f"  Saved planner results JSON: {output_json_path}")

    if output_video_path:
        print(f"  Saved planner preview video: {output_video_path}")

    print(f"\n[Phase 7 Completed]")
    print(f"  Processed {frame_idx} frames in {total_time:.2f}s ({avg_fps:.2f} FPS)")

    return planner_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PathSense Phase 7: Dynamic Arc Planner")
    parser.add_argument("--input", type=str, default="data/samples/sample_1.mp4", help="Path to input video")
    parser.add_argument("--costmap-json", type=str, default="costmap_results.json", help="Path to Phase 6 Costmap JSON")
    parser.add_argument("--output", type=str, default="planner_preview.mp4", help="Path to preview video")
    parser.add_argument("--output-json", type=str, default="planner_results.json", help="Path to output JSON")
    parser.add_argument("--max-frames", type=int, default=None, help="Max frames to process (optional)")
    args = parser.parse_args()

    process_video_planner(
        video_path=args.input,
        costmap_json_path=args.costmap_json,
        output_video_path=args.output,
        output_json_path=args.output_json,
        max_frames=args.max_frames
    )
