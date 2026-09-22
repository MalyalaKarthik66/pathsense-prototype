"""
PathSense - Phase 2: Object Detection and Multi-Object Tracking
Module: detect_track.py

Uses YOLOv8 (Ultralytics) with ByteTrack to detect and track relevant road objects
(pedestrians, vehicles, two-wheelers, and animals) across consecutive video frames.
Produces structured tracking data (JSON) and an annotated preview video.
"""

import os
import sys
import time
import json
import argparse
from typing import Dict, List, Any, Optional
import cv2
import numpy as np
from ultralytics import YOLO

# Relevant COCO classes for unstructured road environments
TARGET_CLASSES: Dict[int, str] = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    15: "cat",
    16: "dog",
    17: "horse",
    18: "sheep",
    19: "cow",
    20: "elephant"
}

# Distinct color palette (BGR) for consistent track rendering
COLOR_PALETTE = [
    (0, 215, 255),   # Amber/Gold
    (0, 255, 128),   # Spring Green
    (255, 105, 180), # Hot Pink
    (30, 144, 255),  # Dodger Blue
    (255, 165, 0),   # Orange
    (147, 112, 219), # Medium Purple
    (0, 255, 255),   # Cyan
    (50, 205, 50),   # Lime Green
    (255, 69, 0),    # Red-Orange
    (138, 43, 226)   # Blue Violet
]


def get_color_for_id(track_id: int) -> tuple:
    return COLOR_PALETTE[track_id % len(COLOR_PALETTE)]


class DetectorTracker:
    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        conf_thresh: float = 0.30,
        iou_thresh: float = 0.50,
        target_classes: Optional[Dict[int, str]] = None
    ):
        self.model_name = model_name
        self.conf_thresh = conf_thresh
        self.iou_thresh = iou_thresh
        self.target_classes = target_classes or TARGET_CLASSES
        self.target_class_ids = list(self.target_classes.keys())

        print(f"[DetectorTracker] Initializing YOLOv8 model: {model_name}...")
        self.model = YOLO(model_name)
        print("[DetectorTracker] Model initialized successfully.")

    def track_frame(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """
        Runs tracking on a single frame using ByteTrack.
        Returns a list of detected/tracked objects:
        [{"track_id": int, "class_id": int, "class_name": str, "bbox": [x1, y1, x2, y2], "conf": float}]
        """
        results = self.model.track(
            source=frame,
            persist=True,
            tracker="bytetrack.yaml",
            classes=self.target_class_ids,
            conf=self.conf_thresh,
            iou=self.iou_thresh,
            verbose=False
        )

        tracked_objects = []
        if not results or len(results) == 0:
            return tracked_objects

        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return tracked_objects

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        cls_ids = boxes.cls.cpu().numpy().astype(int)
        
        # Track IDs may be None if ByteTrack hasn't assigned an ID in early/lost detections
        track_ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else [-1] * len(xyxy)

        for i in range(len(xyxy)):
            tid = int(track_ids[i])
            cid = int(cls_ids[i])
            cname = self.target_classes.get(cid, self.model.names.get(cid, "unknown"))
            bbox = [float(round(coord, 1)) for coord in xyxy[i]]
            conf = float(round(confs[i], 3))

            tracked_objects.append({
                "track_id": tid,
                "class_id": cid,
                "class_name": cname,
                "bbox": bbox,
                "confidence": conf
            })

        return tracked_objects


def draw_tracking_overlay(frame: np.ndarray, tracked_objects: List[Dict[str, Any]], fps: float, frame_idx: int) -> np.ndarray:
    """
    Renders bounding boxes, tracking labels, and HUD overlay onto the frame.
    """
    canvas = frame.copy()
    h, w = canvas.shape[:2]

    # Draw tracked objects
    for obj in tracked_objects:
        tid = obj["track_id"]
        cname = obj["class_name"]
        conf = obj["confidence"]
        x1, y1, x2, y2 = [int(v) for v in obj["bbox"]]

        color = get_color_for_id(tid) if tid >= 0 else (180, 180, 180)

        # Draw bounding box
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)

        # Label tag
        label = f"#{tid} {cname} {conf:.2f}" if tid >= 0 else f"{cname} {conf:.2f}"
        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        
        # Tag background
        tag_y1 = max(0, y1 - th - 6)
        tag_y2 = y1
        cv2.rectangle(canvas, (x1, tag_y1), (x1 + tw + 6, tag_y2), color, -1)
        cv2.putText(
            canvas, label, (x1 + 3, tag_y2 - 3),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA
        )

    # Top-left info HUD panel
    hud_bg = canvas[10:75, 10:280].copy()
    cv2.rectangle(canvas, (10, 10), (280, 75), (0, 0, 0), -1)
    cv2.addWeighted(canvas[10:75, 10:280], 0.7, hud_bg, 0.3, 0, canvas[10:75, 10:280])
    cv2.rectangle(canvas, (10, 10), (280, 75), (0, 215, 255), 1)

    cv2.putText(
        canvas, f"Frame: {frame_idx:04d} | Tracks: {len(tracked_objects)}",
        (18, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA
    )
    cv2.putText(
        canvas, f"Processing Rate: {fps:.1f} FPS (YOLOv8+ByteTrack)",
        (18, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 215, 255), 1, cv2.LINE_AA
    )

    return canvas


def process_video(
    video_path: str,
    output_video_path: Optional[str] = None,
    output_json_path: Optional[str] = None,
    max_frames: Optional[int] = None
) -> Dict[str, Any]:
    """
    Processes video end-to-end for Phase 2:
    - Runs detector + ByteTrack per frame
    - Saves output video with rendered boxes and IDs
    - Exports per-frame tracking telemetry to JSON
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Input video not found: {video_path}")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Failed to open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames_to_process = min(total_frames, max_frames) if max_frames else total_frames

    print(f"\n[Phase 2] Processing Video: {os.path.basename(video_path)}")
    print(f"  Resolution: {width}x{height} | Native FPS: {fps:.2f} | Total Frames: {frames_to_process}")

    tracker = DetectorTracker()

    writer = None
    if output_video_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_video_path, fourcc, fps, (width, height))

    results_data = {
        "metadata": {
            "source_video": video_path,
            "width": width,
            "height": height,
            "fps": fps,
            "total_frames": frames_to_process,
            "model": tracker.model_name
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

        tracked_objects = tracker.track_frame(frame)

        results_data["frames"].append({
            "frame_idx": frame_idx,
            "timestamp_sec": round(frame_idx / fps, 3),
            "objects": tracked_objects
        })

        dt = time.time() - t_frame_start
        instant_fps = 1.0 / dt if dt > 0 else 0
        rolling_fps = instant_fps if frame_idx == 0 else 0.9 * rolling_fps + 0.1 * instant_fps

        if writer is not None:
            annotated_frame = draw_tracking_overlay(frame, tracked_objects, rolling_fps, frame_idx)
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

    results_data["metadata"]["processed_frames"] = frame_idx
    results_data["metadata"]["elapsed_seconds"] = round(total_time, 2)
    results_data["metadata"]["average_fps"] = round(avg_fps, 2)

    if output_json_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_json_path)), exist_ok=True)
        with open(output_json_path, "w") as f:
            json.dump(results_data, f, indent=2)
        print(f"  Saved tracking data JSON: {output_json_path}")

    if output_video_path:
        print(f"  Saved preview video: {output_video_path}")

    print(f"\n[Phase 2 Completed]")
    print(f"  Processed {frame_idx} frames in {total_time:.2f}s (Average: {avg_fps:.2f} FPS)")
    print(f"  Runtime vs Realtime factor: {total_time / (frame_idx / fps):.2f}x native duration")

    return results_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PathSense Phase 2: YOLOv8 + ByteTrack")
    parser.add_argument("--input", type=str, default="data/samples/sample_1.mp4", help="Path to input video")
    parser.add_argument("--output", type=str, default="detect_track_preview.mp4", help="Path to output preview video")
    parser.add_argument("--output-json", type=str, default="detect_track_results.json", help="Path to output JSON")
    parser.add_argument("--max-frames", type=int, default=None, help="Max frames to process (optional)")
    args = parser.parse_args()

    process_video(
        video_path=args.input,
        output_video_path=args.output,
        output_json_path=args.output_json,
        max_frames=args.max_frames
    )
