"""
PathSense: Adaptive Path Planning and Collision Avoidance for Autonomous Vehicles
Main End-to-End Pipeline CLI (Smart India Hackathon Problem Statement 26037)

Executes the full 8-phase perception, telemetry, and planning pipeline:
1. Object Detection & Multi-Object Tracking (YOLOv8 + ByteTrack)
2. Monocular Relative Depth Estimation (Depth Anything V2 Small)
3. Time-to-Collision & Relative Closing Speed (EMA-smoothed differentiation, heuristic)
4. Monocular Ego-Motion Odometry (Road Surface Optical Flow + flat-ground projection)
5. Bird's-Eye-View (BEV) Occupancy Costmap (Vulnerability hierarchy + Gaussian inflation)
6. Dynamic Arc Path Planning & Bicycle Model Steering Angle (Trajectory rollout & scoring)
7. Decision State (GO / SLOW DOWN / BRAKE with corridor, TTC and temporal hysteresis)
8. Cockpit HUD Compositing & Final Video Generation

Works on any video OpenCV can decode (mp4, avi, mov, mkv, ...); resolution, FPS and length are read from the file.

Usage:
  python main.py --input data/samples/sample_1.mp4 --output outputs/sample_1_result.mp4
  python main.py --input path/to/my_video.mp4                      # -> outputs/my_video_pathsense.mp4
  python main.py --input my_video.mov --max-frames 300 --device cpu
"""

import os
import sys
import time
import json
import argparse
from collections import Counter
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
from decision import DecisionState

DEFAULT_FPS = 25.0


def resolve_device(device: str) -> str:
    import torch
    if device == "auto":
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    if device.startswith("cuda") and not torch.cuda.is_available():
        print(f"[Warning] --device {device} requested but CUDA is not available to PyTorch; falling back to CPU.")
        return "cpu"
    return "cuda:0" if device == "cuda" else device


def default_output_path(input_video: str) -> str:
    stem = os.path.splitext(os.path.basename(input_video))[0]
    return os.path.join("outputs", f"{stem}_pathsense.mp4")


def verify_output_video(path: str) -> Dict[str, Any]:
    """Re-opens the written video and counts decodable frames."""
    cap = cv2.VideoCapture(path)
    info = {"opened": cap.isOpened(), "frames": 0, "width": 0, "height": 0, "fps": 0.0}
    if cap.isOpened():
        info["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        info["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        info["fps"] = round(cap.get(cv2.CAP_PROP_FPS), 2)
        while True:
            ok, _ = cap.read()
            if not ok:
                break
            info["frames"] += 1
    cap.release()
    return info


def run_pipeline(
    input_video: str,
    output_video: Optional[str] = None,
    yolo_model: str = "yolov8n.pt",
    max_frames: Optional[int] = None,
    precomputed_depth_json: Optional[str] = None,
    device: str = "auto",
    conf: float = 0.30,
    imgsz: int = 640,
    stats_json: Optional[str] = None,
    depth_mode: str = "ground",
    horizon_ratio: float = 0.55,
    camera_height_m: float = 1.30,
    auto_geometry: bool = True
) -> Dict[str, Any]:
    print("=" * 70)
    print("  PROJECT PATHSENSE: AUTONOMOUS VEHICLE PERCEPTION & PLANNING")
    print("  Smart India Hackathon PS 26037 - Unstructured Road Environments")
    print("=" * 70)

    if not os.path.isfile(input_video):
        raise FileNotFoundError(f"Input video not found: {input_video}")
    output_video = output_video or default_output_path(input_video)

    cap = cv2.VideoCapture(input_video)
    if not cap.isOpened():
        raise IOError(f"OpenCV could not open video (unsupported codec or corrupted file): {input_video}")

    # Read the first frame up front: the real frame size can differ from container metadata,
    # and an empty/corrupted file should fail here with a clear message
    ok, first_frame = cap.read()
    if not ok or first_frame is None:
        cap.release()
        raise IOError(f"Video contains no decodable frames: {input_video}")
    height, width = first_frame.shape[:2]

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or not np.isfinite(fps) or fps < 1.0 or fps > 240.0:
        print(f"[Warning] Video reports invalid FPS ({fps}); assuming {DEFAULT_FPS}.")
        fps = DEFAULT_FPS
    reported_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    total_frames = reported_frames if reported_frames > 0 else None  # None -> unknown, read until EOF
    if max_frames:
        frames_to_process = min(total_frames, max_frames) if total_frames else max_frames
    else:
        frames_to_process = total_frames
    display_total = frames_to_process or 0

    device = resolve_device(device)
    gpu_name = None
    if device.startswith("cuda"):
        import torch
        gpu_name = torch.cuda.get_device_name(0)
        torch.cuda.reset_peak_memory_stats()

    hud = HUDOverlay(pip_margin=20)
    out_w, out_h = HUDOverlay.min_canvas_size(width, height)

    print(f"\n[Configuration]")
    print(f"  Input Video:        {input_video}")
    print(f"  Output Video:       {output_video}")
    print(f"  Input Resolution:   {width}x{height}")
    print(f"  Output Resolution:  {out_w}x{out_h}")
    print(f"  Frame Rate:         {fps:.2f} FPS")
    if frames_to_process:
        print(f"  Frames to Process:  {frames_to_process} ({frames_to_process / fps:.1f}s)")
    else:
        print(f"  Frames to Process:  unknown (container has no frame count; reading until end)")
    print(f"  Device:             {device}" + (f" ({gpu_name})" if gpu_name else ""))
    print(f"  YOLO Model:         {yolo_model} (conf={conf}, imgsz={imgsz})")
    depth_desc = "road-plane calibrated" if depth_mode == "ground" else "fixed heuristic 2-60 m"
    auto_desc = " (auto-refined from detected cars)" if depth_mode == "ground" and auto_geometry else ""
    print(f"  Depth Model:        Depth-Anything-V2-Small, distance mode '{depth_mode}' ({depth_desc}; not metric)")
    print(f"  Camera Geometry:    horizon {horizon_ratio:.2f}*H, height {camera_height_m:.2f} m{auto_desc}")
    print(f"  Wheelbase (L):      2.70 m")
    print(f"  Grid Size:          40m x 40m (0.5m / cell)")

    # 1. Initialize Pipeline Modules
    print("\n[Initialization] Initializing pipeline components...")
    t_init_start = time.time()

    detector = DetectorTracker(model_name=yolo_model, conf_thresh=conf, device=device, imgsz=imgsz)
    depth_estimator = DepthEstimator(device=device, mode=depth_mode, horizon_ratio=horizon_ratio,
                                     camera_height_m=camera_height_m, auto_geometry=auto_geometry)
    ttc_computer = TTCComputer(fps=fps, frame_width=width, frame_height=height)
    ego_motion = EgoMotionEstimator(width=width, height=height, fps=fps, camera_height_m=camera_height_m,
                                    horizon_ratio=horizon_ratio)
    costmap_builder = BEVCostmap(img_width=width, img_height=height)
    planner = DynamicArcPlanner(wheelbase_m=2.70, max_steering_deg=15.0, num_candidates=17)
    decision = DecisionState(fps=fps)
    geometry_synced = False

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
    writer = cv2.VideoWriter(output_video, fourcc, fps, (out_w, out_h))
    if not writer.isOpened():
        cap.release()
        raise IOError(f"Could not open video writer for {output_video} (mp4v codec). Try an output path ending in .mp4")

    # 3. Execution Loop
    print("\n[Processing] Starting end-to-end processing...")
    frame_idx = 0
    t_loop_start = time.time()
    rolling_fps = 0.0
    smooth_spd, steer_deg, px, py = 0.0, 0.0, 0.0, 0.0
    stage_time = Counter()
    stats = {
        "detections": 0, "frames_with_detections": 0, "track_ids": set(), "depth_frames": 0,
        "ttc_values": 0, "hazard_counts": Counter(), "ego_frames_with_flow": 0, "bev_frames_with_obstacles": 0,
        "planner_candidate_hist": Counter(), "planner_blocked_frames": 0, "max_abs_steer_deg": 0.0,
        "sum_abs_steer_deg": 0.0, "decision_labels": Counter(), "decision_states": Counter(), "brake_episodes": [],
        "frames_with_critical": 0
    }
    brake_start = None
    frame = first_frame

    while frames_to_process is None or frame_idx < frames_to_process:
        if frame_idx > 0:
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            if frame.shape[:2] != (height, width):
                frame = cv2.resize(frame, (width, height))
        t_f0 = time.time()

        # A. Detection & Tracking (YOLOv8 + ByteTrack)
        t = time.time()
        raw_tracks = detector.track_frame(frame)
        obstacle_boxes = [obj["bbox"] for obj in raw_tracks]
        stage_time["detect_track"] += time.time() - t

        # B. Monocular Depth Estimation
        # Use depth cache if available to skip redundant inference in multi-pass testing
        t = time.time()
        if precomputed_depths and frame_idx < len(precomputed_depths.get("frames", [])):
            cached_frame = precomputed_depths["frames"][frame_idx]
            depth_lookup = {o["track_id"]: o.get("estimated_depth_m", 15.0) for o in cached_frame.get("objects", [])}
            for obj in raw_tracks:
                obj["estimated_depth_m"] = depth_lookup.get(obj["track_id"], 15.0)
        else:
            _, distance_map, _ = depth_estimator.infer_depth(frame, objects=raw_tracks)
            for obj in raw_tracks:
                obj["estimated_depth_m"] = depth_estimator.sample_object_depth(obj["bbox"], distance_map)
            stats["depth_frames"] += 1
        stage_time["depth"] += time.time() - t

        # C. Time-to-Collision & Closing Velocity (EMA-smoothed)
        t = time.time()
        processed_objects = []
        for obj in raw_tracks:
            obj_copy = dict(obj)
            tid = obj_copy.get("track_id", -1)
            raw_d = obj_copy.get("estimated_depth_m", 15.0)
            if tid >= 0:
                s_d, c_v, ttc, hazard = ttc_computer.update_track(tid, frame_idx, raw_d, bbox=obj_copy["bbox"])
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
        ttc_computer.prune(frame_idx)
        stage_time["ttc"] += time.time() - t

        # D. Ego-Motion Odometry (Speed & Position)
        t = time.time()
        geo = depth_estimator.geometry
        if not geometry_synced and geo.get("source") == "self-calibrated":
            # Share the self-calibrated horizon / camera height with the flat-road odometry
            ego_motion.cy, ego_motion.camera_h = geo["horizon_px"], geo["camera_height_m"]
            geometry_synced = True
            print(f"  [Geometry] self-calibrated at frame {frame_idx}: horizon {geo['horizon_px']:.0f}px "
                  f"({geo['horizon_px'] / height:.2f}*H), camera height {geo['camera_height_m']:.2f} m")
        raw_spd, smooth_spd, px, py, flow_tracks = ego_motion.update(frame, obstacle_boxes)
        stage_time["ego_motion"] += time.time() - t

        # E. BEV Costmap Construction
        t = time.time()
        cost_grid, projected_objs = costmap_builder.build_costmap(processed_objects)
        stage_time["costmap"] += time.time() - t

        # F. Dynamic Arc Path Planning & Steering Angle
        t = time.time()
        best_idx, best_kappa, steer_deg, best_path, _ = planner.evaluate_paths(
            cost_grid, costmap_builder
        )
        stage_time["planner"] += time.time() - t

        # F2. Decision state (corridor + TTC + blocked paths + temporal hysteresis)
        t = time.time()
        decision_out = decision.update(projected_objs, best_path, planner.all_blocked, steer_deg,
                                       ego_speed_mps=smooth_spd / 3.6)
        stage_time["decision"] += time.time() - t

        # G. Render BEV Image with Planned Path
        t = time.time()
        bev_image = planner.render_bev_with_plan(
            cost_grid, projected_objs, costmap_builder, best_path, steer_deg, canvas_size=340
        )

        # H. HUD Cockpit Compositor
        hud_frame = hud.render_hud_frame(
            frame_bgr=frame,
            tracked_objects=projected_objs,
            bev_image=bev_image,
            speed_kmh=smooth_spd,
            steering_deg=steer_deg,
            pos_x=px,
            pos_y=py,
            frame_idx=frame_idx,
            total_frames=display_total,
            fps=rolling_fps,
            planner_blocked=planner.all_blocked,
            decision=decision_out
        )
        if hud_frame.shape[:2] != (out_h, out_w):
            hud_frame = cv2.resize(hud_frame, (out_w, out_h))

        writer.write(hud_frame)
        stage_time["render_write"] += time.time() - t

        # Stage statistics (for validation / reporting)
        stats["detections"] += len(raw_tracks)
        stats["frames_with_detections"] += int(len(raw_tracks) > 0)
        stats["track_ids"].update(o["track_id"] for o in raw_tracks if o["track_id"] >= 0)
        stats["ttc_values"] += sum(1 for o in processed_objects if o["ttc_sec"] is not None)
        stats["hazard_counts"].update(o["hazard_level"] for o in processed_objects)
        stats["ego_frames_with_flow"] += int(len(flow_tracks) >= 4)
        stats["bev_frames_with_obstacles"] += int(bool(cost_grid.any()))
        stats["planner_candidate_hist"][best_idx] += 1
        stats["planner_blocked_frames"] += int(planner.all_blocked)
        stats["max_abs_steer_deg"] = max(stats["max_abs_steer_deg"], abs(steer_deg))
        stats["sum_abs_steer_deg"] += abs(steer_deg)
        stats["decision_labels"][decision_out["label"]] += 1
        stats["decision_states"][decision_out["state"]] += 1
        stats["frames_with_critical"] += int(any(o["hazard_level"] == "CRITICAL" for o in processed_objects))
        if decision_out["state"] == "BRAKE" and brake_start is None:
            brake_start = (frame_idx, decision_out["reason"])
        elif decision_out["state"] != "BRAKE" and brake_start is not None:
            stats["brake_episodes"].append((brake_start[0], frame_idx - 1, brake_start[1]))
            brake_start = None

        dt = time.time() - t_f0
        inst_fps = 1.0 / dt if dt > 0 else 0
        rolling_fps = inst_fps if frame_idx == 0 else 0.9 * rolling_fps + 0.1 * inst_fps

        frame_idx += 1
        if frame_idx % 25 == 0 or frame_idx == frames_to_process:
            progress = f"{frame_idx:04d}/{frames_to_process:04d} ({frame_idx / frames_to_process * 100.0:5.1f}%)" \
                if frames_to_process else f"{frame_idx:04d}"
            print(f"  Frame {progress} | Tracks: {len(raw_tracks):2d} | Speed: {smooth_spd:4.1f} km/h | Steer: {steer_deg:+5.1f} deg "
                  f"| {decision_out['label']:<20s} | Rate: {rolling_fps:4.1f} FPS")

    total_time = time.time() - t_loop_start
    avg_fps = frame_idx / total_time if total_time > 0 else 0

    cap.release()
    writer.release()
    if brake_start is not None:
        stats["brake_episodes"].append((brake_start[0], frame_idx - 1, brake_start[1]))

    if frame_idx == 0:
        raise RuntimeError(f"No frames were processed from {input_video}")

    peak_gpu_mb = None
    if device.startswith("cuda"):
        import torch
        peak_gpu_mb = round(torch.cuda.max_memory_allocated() / (1024 * 1024), 1)

    out_info = verify_output_video(output_video)

    summary = {
        "input_video": input_video,
        "output_video": output_video,
        "input_resolution": [width, height],
        "input_fps": round(fps, 2),
        "input_reported_frames": reported_frames,
        "frames_processed": frame_idx,
        "processing_seconds": round(total_time, 2),
        "processing_fps": round(avg_fps, 2),
        "realtime_factor": round(avg_fps / fps, 3),
        "device": device,
        "gpu_name": gpu_name,
        "peak_torch_gpu_mem_mb": peak_gpu_mb,
        "stage_ms_per_frame": {k: round(v / frame_idx * 1000.0, 1) for k, v in stage_time.items()},
        "detections_total": stats["detections"],
        "frames_with_detections": stats["frames_with_detections"],
        "unique_track_ids": len(stats["track_ids"]),
        "depth_inference_frames": stats["depth_frames"],
        "ttc_estimates": stats["ttc_values"],
        "hazard_counts": dict(stats["hazard_counts"]),
        "ego_frames_with_flow": stats["ego_frames_with_flow"],
        "final_speed_kmh": smooth_spd,
        "final_travel_m": py,
        "bev_frames_with_obstacles": stats["bev_frames_with_obstacles"],
        "planner_distinct_candidates": len(stats["planner_candidate_hist"]),
        "planner_blocked_frames": stats["planner_blocked_frames"],
        "max_abs_steer_deg": round(stats["max_abs_steer_deg"], 2),
        "mean_abs_steer_deg": round(stats["sum_abs_steer_deg"] / frame_idx, 2),
        "frames_with_critical_pct": round(100.0 * stats["frames_with_critical"] / frame_idx, 1),
        "planner_blocked_pct": round(100.0 * stats["planner_blocked_frames"] / frame_idx, 1),
        "decision_label_pct": {k: round(100.0 * v / frame_idx, 1) for k, v in stats["decision_labels"].most_common()},
        "decision_state_pct": {k: round(100.0 * v / frame_idx, 1) for k, v in stats["decision_states"].most_common()},
        "decision_transitions": decision.transitions,
        "decision_transitions_per_min": round(decision.transitions / (frame_idx / fps / 60.0), 1),
        "brake_episodes": len(stats["brake_episodes"]),
        "brake_episode_median_s": round(float(np.median([(e[1] - e[0] + 1) / fps for e in stats["brake_episodes"]])), 2)
        if stats["brake_episodes"] else 0.0,
        "brake_episode_list": [{"start_frame": a, "end_frame": b, "seconds": round((b - a + 1) / fps, 2), "reason": r}
                               for a, b, r in stats["brake_episodes"]],
        "depth_geometry": depth_estimator.geometry,
        "depth_heuristic_fallback_frames": depth_estimator.frames_heuristic_fallback,
        "output_check": out_info,
    }

    print("\n" + "=" * 70)
    print("  PIPELINE RUN COMPLETE!")
    print(f"  Output Video:      {os.path.abspath(output_video)}")
    print(f"  Frames Processed:  {frame_idx}")
    print(f"  Total Duration:    {total_time:.2f} seconds ({avg_fps:.2f} FPS, {avg_fps / fps:.2f}x real-time)")
    print(f"  Stage ms/frame:    {summary['stage_ms_per_frame']}")
    print(f"  Tracks / TTC:      {summary['unique_track_ids']} unique IDs, {summary['ttc_estimates']} TTC estimates, hazards {summary['hazard_counts']}")
    print(f"  Planner:           {summary['planner_distinct_candidates']} distinct arcs chosen, {summary['planner_blocked_frames']} blocked frames, max |steer| {summary['max_abs_steer_deg']} deg")
    print(f"  Decision:          {summary['decision_label_pct']} | {summary['decision_transitions_per_min']} transitions/min | "
          f"{summary['brake_episodes']} BRAKE episodes (median {summary['brake_episode_median_s']} s)")
    print(f"  Depth geometry:    {summary['depth_geometry']}")
    print(f"  Final Travelled:   {py:.1f} meters (monocular estimate)")
    if peak_gpu_mb is not None:
        print(f"  Peak GPU memory:   {peak_gpu_mb} MB (PyTorch allocations)")
    print(f"  Output check:      reopened={out_info['opened']}, frames={out_info['frames']}, {out_info['width']}x{out_info['height']} @ {out_info['fps']} FPS")
    print("=" * 70)

    if not out_info["opened"] or out_info["frames"] == 0:
        raise IOError(f"Output video {output_video} could not be re-opened or has no frames")

    if stats_json:
        os.makedirs(os.path.dirname(os.path.abspath(stats_json)), exist_ok=True)
        with open(stats_json, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"  Run statistics saved to {stats_json}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PathSense Autonomous Vehicle Pipeline (SIH PS 26037)")
    parser.add_argument("--input", type=str, required=True, help="Path to any input dashcam video (mp4/avi/mov/mkv...)")
    parser.add_argument("--output", type=str, default=None, help="Output HUD video path (default: outputs/<input_name>_pathsense.mp4)")
    parser.add_argument("--yolo-model", type=str, default="yolov8n.pt", help="YOLOv8 weights (yolov8n.pt, yolov8s.pt)")
    parser.add_argument("--device", type=str, default="auto", help="auto | cuda | cuda:0 | cpu")
    parser.add_argument("--conf", type=float, default=0.30, help="YOLO confidence threshold")
    parser.add_argument("--imgsz", type=int, default=640, help="YOLO inference image size")
    parser.add_argument("--max-frames", type=int, default=None, help="Limit frames to process")
    parser.add_argument("--depth-cache", type=str, default=None, help="Optional precomputed depth JSON cache")
    parser.add_argument("--stats-json", type=str, default=None, help="Optional path to save run statistics JSON")
    parser.add_argument("--depth-mode", choices=["ground", "heuristic"], default="ground",
                        help="ground: road-plane calibrated distances (default); heuristic: original fixed 2-60 m mapping")
    parser.add_argument("--horizon-ratio", type=float, default=0.55, help="Initial horizon row as a fraction of image height")
    parser.add_argument("--camera-height", type=float, default=1.30, help="Initial camera height above the road (m)")
    parser.add_argument("--no-auto-geometry", action="store_true", help="Disable self-calibration of horizon/camera height")
    args = parser.parse_args()

    try:
        run_pipeline(
            input_video=args.input,
            output_video=args.output,
            yolo_model=args.yolo_model,
            max_frames=args.max_frames,
            precomputed_depth_json=args.depth_cache,
            device=args.device,
            conf=args.conf,
            imgsz=args.imgsz,
            stats_json=args.stats_json,
            depth_mode=args.depth_mode,
            horizon_ratio=args.horizon_ratio,
            camera_height_m=args.camera_height,
            auto_geometry=not args.no_auto_geometry
        )
    except (FileNotFoundError, IOError, RuntimeError) as e:
        print(f"\n[Error] {e}")
        sys.exit(1)
