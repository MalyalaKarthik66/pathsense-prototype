"""
PathSense: Adaptive Path Planning and Collision Avoidance for Autonomous Vehicles
Main End-to-End Pipeline CLI (Smart India Hackathon Problem Statement 26037)

Executes the full 8-phase perception, telemetry, and planning pipeline:
1. Object Detection & Multi-Object Tracking (YOLOv8 + ByteTrack)
2. Monocular Relative Depth Estimation (Depth Anything V2 Small)
3. Time-to-Collision & Relative Closing Speed (EMA-smoothed differentiation)
4. Monocular Ego-Motion Odometry (Road Surface Optical Flow + Planar Homography)
5. Bird's-Eye-View (BEV) Occupancy Costmap (Vulnerability hierarchy + Gaussian inflation)
6. Dynamic Arc Path Planning & Bicycle Model Steering Angle (Trajectory rollout & scoring)
7. Cockpit HUD Compositing & Final Video Generation

Usage:
  python main.py --input data/samples/sample_1.mp4 --output pathsense_output.mp4
  python main.py --input data/samples/sample_closing.mp4 --output pathsense_closing_output.mp4 --max-frames 200
"""

import os
import sys
import time
import json
import argparse
from typing import Dict, List, Any, Optional
import cv2
import numpy as np

from detect_track import DetectorTracker
from depth import DepthEstimator
from ttc import TTCComputer
from ego_motion import EgoMotionEstimator
from costmap import BEVCostmap
from planner import DynamicArcPlanner
from overlay import HUDOverlay


def run_pipeline(
    input_video: str,
    output_video: str,
    yolo_model: str = "yolov8n.pt",
    max_frames: Optional[int] = None,
    precomputed_depth_json: Optional[str] = None
):
    print("=" * 70)
    print("  PROJECT PATHSENSE: AUTONOMOUS VEHICLE PERCEPTION & PLANNING")
    print("  Smart India Hackathon PS 26037 - Unstructured Road Environments")
    print("=" * 70)

    if not os.path.exists(input_video):
        raise FileNotFoundError(f"Input video not found: {input_video}")

    cap = cv2.VideoCapture(input_video)
    if not cap.isOpened():
        raise IOError(f"Failed to open video: {input_video}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames_to_process = min(total_frames, max_frames) if max_frames else total_frames

    print(f"\n[Configuration]")
    print(f"  Input Video:        {input_video}")
    print(f"  Output Video:       {output_video}")
    print(f"  Resolution:         {width}x{height}")
    print(f"  Frame Rate:         {fps:.2f} FPS")
    print(f"  Total Frames:       {frames_to_process} ({frames_to_process / fps:.1f}s)")
    print(f"  YOLO Model:         {yolo_model}")
    print(f"  Depth Model:        Depth-Anything-V2-Small (CPU)")
    print(f"  Wheelbase (L):      2.70 m")
    print(f"  Grid Size:          40m x 40m (0.5m / cell)")

    # 1. Initialize Pipeline Modules
    print("\n[Initialization] Initializing pipeline components...")
    t_init_start = time.time()
    
    detector = DetectorTracker(model_name=yolo_model)
    depth_estimator = DepthEstimator(device="cpu")
    ttc_computer = TTCComputer(fps=fps)
    ego_motion = EgoMotionEstimator(width=width, height=height, fps=fps)
    costmap_builder = BEVCostmap(img_width=width, img_height=height)
    planner = DynamicArcPlanner(wheelbase_m=2.70, max_steering_deg=30.0, num_candidates=17)
    hud = HUDOverlay(pip_size=(340, 340), pip_margin=20)

    # Optional precomputed depth cache for instant benchmarking
    precomputed_depths = None
    if precomputed_depth_json and os.path.exists(precomputed_depth_json):
        print(f"  Loaded precomputed depth cache from {precomputed_depth_json}")
        with open(precomputed_depth_json, "r") as f:
            precomputed_depths = json.load(f)

    print(f"[Initialization] All modules ready in {time.time() - t_init_start:.2f}s")

    # 2. Prepare Output Video Writer
    os.makedirs(os.path.dirname(os.path.abspath(output_video)), exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_video, fourcc, fps, (width, height))

    # 3. Execution Loop
    print("\n[Processing] Starting end-to-end processing...")
    frame_idx = 0
    t_loop_start = time.time()
    rolling_fps = 0.0

    while cap.isOpened() and frame_idx < frames_to_process:
        t_f0 = time.time()
        ret, frame = cap.read()
        if not ret:
            break

        # A. Detection & Tracking (YOLOv8 + ByteTrack)
        raw_tracks = detector.track_frame(frame)
        obstacle_boxes = [obj["bbox"] for obj in raw_tracks]

        # B. Monocular Depth Estimation
        # Use depth cache if available to skip redundant inference in multi-pass testing
        if precomputed_depths and frame_idx < len(precomputed_depths.get("frames", [])):
            cached_frame = precomputed_depths["frames"][frame_idx]
            depth_lookup = {o["track_id"]: o.get("estimated_depth_m", 15.0) for o in cached_frame.get("objects", [])}
            for obj in raw_tracks:
                obj["estimated_depth_m"] = depth_lookup.get(obj["track_id"], 15.0)
        else:
            _, distance_map, _ = depth_estimator.infer_depth(frame)
            for obj in raw_tracks:
                obj["estimated_depth_m"] = depth_estimator.sample_object_depth(obj["bbox"], distance_map)

        # C. Time-to-Collision & Closing Velocity (EMA-smoothed)
        processed_objects = []
        for obj in raw_tracks:
            obj_copy = dict(obj)
            tid = obj_copy.get("track_id", -1)
            raw_d = obj_copy.get("estimated_depth_m", 15.0)
            if tid >= 0:
                s_d, c_v, ttc, hazard = ttc_computer.update_track(tid, frame_idx, raw_d)
                obj_copy["smoothed_depth_m"] = s_d
                obj_copy["closing_speed_mps"] = c_v
                obj_copy["closing_speed_kmh"] = round(c_v * 3.6, 1)
                obj_copy["ttc_sec"] = ttc
                obj_copy["hazard_level"] = hazard
            else:
                obj_copy["smoothed_depth_m"] = raw_d
                obj_copy["closing_speed_mps"] = 0.0
                obj_copy["closing_speed_kmh"] = 0.0
                obj_copy["ttc_sec"] = None
                obj_copy["hazard_level"] = "SAFE"
            processed_objects.append(obj_copy)

        # D. Ego-Motion Odometry (Speed & Position)
        raw_spd, smooth_spd, px, py, _ = ego_motion.update(frame, obstacle_boxes)

        # E. BEV Costmap Construction
        cost_grid, projected_objs = costmap_builder.build_costmap(processed_objects)

        # F. Dynamic Arc Path Planning & Steering Angle
        best_idx, best_kappa, steer_deg, best_path, _ = planner.evaluate_paths(
            cost_grid, costmap_builder
        )

        # G. Render BEV Image with Planned Path
        bev_image = planner.render_bev_with_plan(
            cost_grid, projected_objs, costmap_builder, best_path, steer_deg, canvas_size=340
        )

        # H. HUD Cockpit Compositor
        hud_frame = hud.render_hud_frame(
            frame_bgr=frame,
            tracked_objects=processed_objects,
            bev_image=bev_image,
            speed_kmh=smooth_spd,
            steering_deg=steer_deg,
            pos_x=px,
            pos_y=py,
            frame_idx=frame_idx,
            total_frames=frames_to_process,
            fps=rolling_fps
        )

        writer.write(hud_frame)

        dt = time.time() - t_f0
        inst_fps = 1.0 / dt if dt > 0 else 0
        rolling_fps = inst_fps if frame_idx == 0 else 0.9 * rolling_fps + 0.1 * inst_fps

        frame_idx += 1
        if frame_idx % 25 == 0 or frame_idx == frames_to_process:
            pct = (frame_idx / frames_to_process) * 100.0
            print(f"  Frame {frame_idx:04d}/{frames_to_process:04d} ({pct:5.1f}%) | Speed: {smooth_spd:4.1f} km/h | Steer: {steer_deg:+5.1f} deg | Rate: {rolling_fps:4.1f} FPS")

    total_time = time.time() - t_loop_start
    avg_fps = frame_idx / total_time if total_time > 0 else 0

    cap.release()
    writer.release()

    print("\n" + "=" * 70)
    print("  PIPELINE RUN COMPLETE!")
    print(f"  Output Video:      {output_video}")
    print(f"  Frames Processed:  {frame_idx}")
    print(f"  Total Duration:    {total_time:.2f} seconds ({avg_fps:.2f} FPS)")
    print(f"  Final Travelled:   {py:.1f} meters")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PathSense Autonomous Vehicle Pipeline (SIH PS 26037)")
    parser.add_argument("--input", type=str, default="data/samples/sample_1.mp4", help="Path to input dashcam video")
    parser.add_argument("--output", type=str, default="pathsense_output.mp4", help="Path to final composite HUD video")
    parser.add_argument("--yolo-model", type=str, default="yolov8n.pt", help="YOLOv8 weights (yolov8n.pt, yolov8s.pt)")
    parser.add_argument("--max-frames", type=int, default=None, help="Limit frames to process")
    parser.add_argument("--depth-cache", type=str, default=None, help="Optional precomputed depth JSON cache")
    args = parser.parse_args()

    run_pipeline(
        input_video=args.input,
        output_video=args.output,
        yolo_model=args.yolo_model,
        max_frames=args.max_frames,
        precomputed_depth_json=args.depth_cache
    )
