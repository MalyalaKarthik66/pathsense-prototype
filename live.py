"""
PathSense - live camera processing (web Live mode)
Module: live.py

LivePipeline runs the SAME modules as main.py (YOLOv8 + ByteTrack, road-plane-calibrated depth, TTC, ego-motion,
BEV costmap, candidate-trajectory planner, behaviour cues, decision state, accident detector) on single frames sent
by the browser (getUserMedia). For responsiveness frames are processed at <= 640 px width; the browser sends the next
frame only after the previous result arrives (self-throttling, no queue build-up). Timing-dependent modules use the
measured frame interval. Reported FPS / latency are measured, not estimated.

Research prototype - not a certified autonomous-driving or safety system.
"""

import threading
import time
from collections import deque
from typing import Any, Dict, Optional

import cv2
import numpy as np

from detect_track import DetectorTracker
from autorickshaw import AutoRickshawClassifier
from depth import DepthEstimator
from ttc import TTCComputer
from ego_motion import EgoMotionEstimator
from costmap import BEVCostmap
from planner import DynamicArcPlanner
from behavior import BehaviorAnalyzer
from decision import DecisionState
from accident import AccidentDetector

LIVE_WIDTH = 640
NOMINAL_FPS = 8.0


class LivePipeline:
    def __init__(self, device: Optional[str] = None, auto_classifier: bool = True):
        self.device = device
        self.detector = DetectorTracker(device=device)
        self.depth = DepthEstimator(device=device)
        self.auto_cls = AutoRickshawClassifier(fps=NOMINAL_FPS, device=device, enabled=auto_classifier)
        self.lock = threading.Lock()
        self.size = None
        self.frame_idx = 0
        self.last_t: Optional[float] = None
        self.intervals: deque = deque(maxlen=20)
        self.proc_ms: deque = deque(maxlen=20)
        self.prev_small = None
        self._reset_geometry(LIVE_WIDTH, 360)

    def _reset_geometry(self, w: int, h: int):
        self.size = (w, h)
        self.ttc = TTCComputer(fps=NOMINAL_FPS, frame_width=w, frame_height=h)
        self.ego = EgoMotionEstimator(width=w, height=h, fps=NOMINAL_FPS)
        self.costmap = BEVCostmap(img_width=w, img_height=h)
        self.planner = DynamicArcPlanner()
        self.behavior = BehaviorAnalyzer(fps=NOMINAL_FPS, frame_width=w, frame_height=h)
        self.decision = DecisionState(fps=NOMINAL_FPS)
        self.accident = AccidentDetector(fps=NOMINAL_FPS, frame_width=w)
        self.frame_idx = 0

    def reset(self):
        with self.lock:
            self._reset_geometry(*self.size)
            self.detector = DetectorTracker(device=self.device)  # fresh ByteTrack state
            self.intervals.clear(); self.proc_ms.clear(); self.last_t = None; self.prev_small = None

    def process_jpeg(self, data: bytes) -> Dict[str, Any]:
        arr = np.frombuffer(data, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("could not decode frame (expected JPEG/PNG)")
        return self.process(frame)

    def process(self, frame: np.ndarray) -> Dict[str, Any]:
        with self.lock:
            t0 = time.time()
            if self.last_t is not None:
                self.intervals.append(t0 - self.last_t)
            self.last_t = t0
            h0, w0 = frame.shape[:2]
            if w0 > LIVE_WIDTH:
                frame = cv2.resize(frame, (LIVE_WIDTH, int(round(h0 * LIVE_WIDTH / w0))), interpolation=cv2.INTER_AREA)
            h, w = frame.shape[:2]
            if (w, h) != self.size:
                self._reset_geometry(w, h)
            dt = float(np.median(self.intervals)) if self.intervals else 1.0 / NOMINAL_FPS
            dt = min(max(dt, 1 / 30.0), 1.0)
            # timing-dependent modules follow the measured frame interval
            self.ttc.dt = dt
            self.ego.dt = dt
            i = self.frame_idx

            objs = self.detector.track_frame(frame)
            self.auto_cls.update(frame, objs, i)
            _, dmap, _ = self.depth.infer_depth(frame, objects=objs)
            processed = []
            for o in objs:
                o = dict(o)
                o["estimated_depth_m"] = self.depth.sample_object_depth(o["bbox"], dmap)
                if o["track_id"] >= 0:
                    s, v, tt, hz = self.ttc.update_track(o["track_id"], i, o["estimated_depth_m"], bbox=o["bbox"])
                else:
                    s, v, tt, hz = o["estimated_depth_m"], 0.0, None, "SAFE"
                o.update(smoothed_depth_m=s, closing_speed_mps=v, ttc_sec=tt, hazard_level=hz)
                processed.append(o)
            self.ttc.prune(i)
            _, speed_kmh, _, _, _ = self.ego.update(frame, [o["bbox"] for o in objs])
            grid, proj = self.costmap.build_costmap(processed)
            _, _, steer, path, _ = self.planner.evaluate_paths(grid, self.costmap)
            self.behavior.update(proj, i, ego_speed_mps=speed_kmh / 3.6)
            d = self.decision.update(proj, path, self.planner.all_blocked, steer, ego_speed_mps=speed_kmh / 3.6,
                                     arc_blocked=self.planner.lethal_flags)
            small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90), interpolation=cv2.INTER_AREA)
            jolt = float(cv2.absdiff(small, self.prev_small).mean()) if self.prev_small is not None else None
            self.prev_small = small
            acc = self.accident.update(i, proj, speed_kmh, jolt)
            self.frame_idx += 1
            proc = (time.time() - t0) * 1000.0
            self.proc_ms.append(proc)

            key = d.get("key") or {}
            return {
                "frame": i, "width": w, "height": h,
                "decision": {"label": d["label"], "state": d["state"], "title": d.get("title", ""),
                             "reason": d.get("reason", ""), "path": d.get("path_status"), "threats": d.get("threats", 0),
                             "blocked_arcs": d.get("blocked_arcs"), "n_arcs": d.get("n_arcs"),
                             "key": {"id": key.get("track_id"), "cls": key.get("class_name"),
                                     "dist": _r(key.get("distance_m")), "ttc": _r(key.get("ttc_s")),
                                     "beh": key.get("behavior")} if key else None},
                "steering_deg": round(float(steer), 1), "speed_kmh": round(float(speed_kmh), 1),
                "objects": [{"id": o.get("track_id"), "cls": o.get("display_class", o.get("class_name")),
                             "box": [round(o["bbox"][0] / w, 4), round(o["bbox"][1] / h, 4), round(o["bbox"][2] / w, 4),
                                     round(o["bbox"][3] / h, 4)],
                             "dist": _r(o.get("bev_y_m")), "x": _r(o.get("bev_x_m")), "ttc": _r(o.get("ttc_sec")),
                             "hazard": o.get("hazard_level"), "in_path": bool(o.get("in_corridor")),
                             "beh": o.get("behavior"), "key": o.get("track_id") == d.get("key_track_id")}
                            for o in proj],
                "bev": {"path": [[round(x, 2), round(y, 2)] for x, y in path[::4]]},
                "accident": {"state": acc["state"], "confidence": acc["confidence"], "cues": acc["cues"]},
                "perf": {"processing_ms": round(proc, 1),
                         "server_fps": round(1.0 / dt, 1) if self.intervals else None},
            }


def _r(v, nd=1):
    return None if v is None else round(float(v), nd)
