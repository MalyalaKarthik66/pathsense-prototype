"""
PathSense - Behaviour cues from tracked-object history
Module: behavior.py

Labels short-term behaviour of tracked objects from their BEV trajectory (ego frame), box size, closing speed and the
ego speed estimate - no learned model:

    CUT-IN     same-direction vehicle / two-wheeler that started outside the ego corridor moves laterally into it
    CROSSING   pedestrian / animal moving laterally with a predicted path through the corridor
    ONCOMING   vehicle moving towards the ego vehicle (closing faster than ego speed) near the corridor
    SLOW LEAD  vehicle in the corridor that is (almost) stopped while the ego vehicle moves
    SIDE PASS  object alongside the corridor without lateral motion towards it (informational only)

Evidence rules (to avoid "CUT-IN!" from noise):
  - lateral velocity is a least-squares slope over a 0.8 s window (needs >= 70% of the window observed);
    boxes cut by the image border never enter the history (their lateral position is unreliable),
  - ego yaw (curvy roads, handheld / two-wheeler cameras) makes the whole scene drift sideways in the ego frame, roughly
    proportional to distance; when >= 2 other tracks are available their median drift rate is subtracted,
  - CUT-IN requires a same-direction object: the box must not be expanding quickly (oncoming traffic does),
  - CUT-IN / CROSSING need >= 0.7 m of net (drift-corrected) lateral displacement,
  - a behaviour must hold for 0.3 s (0.5 s for speed-based ONCOMING / SLOW LEAD) before it is confirmed,
  - a confirmed label is held for 1.5 s after the evidence disappears.
All inputs are monocular estimates; these are behaviour cues, not verified intent.
"""

from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from costmap import CLASS_SAFETY_PROFILES

VEHICLES = {"car", "truck", "bus", "motorcycle", "bicycle", "auto-rickshaw"}
VULNERABLE_PEDS = {"person", "cat", "dog", "sheep", "cow", "horse", "elephant"}
BEHAVIORS = ("CUT-IN", "CROSSING", "ONCOMING", "SLOW LEAD", "SIDE PASS")


class BehaviorAnalyzer:
    def __init__(
        self,
        fps: float = 25.0,
        frame_width: Optional[int] = None,
        frame_height: Optional[int] = None,
        window_s: float = 0.8,
        persist_s: float = 0.3,
        persist_speed_s: float = 0.5,
        hold_s: float = 1.5,
        vehicle_half_width_m: float = 0.9,
        corridor_margin_m: float = 0.3,
        max_range_m: float = 25.0,
        max_cutin_growth: float = 0.5
    ):
        fps = fps if fps and np.isfinite(fps) and fps > 0 else 25.0
        self.fps = fps
        self.frame_w, self.frame_h = frame_width, frame_height
        self.win = max(3, int(round(window_s * fps)))
        self.n_persist = max(2, int(round(persist_s * fps)))
        self.n_persist_speed = max(2, int(round(persist_speed_s * fps)))
        self.n_hold = max(1, int(round(hold_s * fps)))
        self.half_w, self.margin, self.max_range = vehicle_half_width_m, corridor_margin_m, max_range_m
        self.max_cutin_growth = max_cutin_growth
        self.hist: Dict[int, deque] = defaultdict(lambda: deque(maxlen=self.win))
        self.streak: Dict[Tuple[int, str], int] = defaultdict(int)
        self.active: Dict[int, Tuple[str, int]] = {}     # track -> (behaviour, last frame with evidence)
        self.counts: Dict[str, int] = defaultdict(int)  # confirmed behaviour onsets

    def _reach(self, cls: str) -> float:
        prof = CLASS_SAFETY_PROFILES.get(cls, CLASS_SAFETY_PROFILES["default"])
        return self.half_w + prof.get("collision_radius", prof["footprint_radius"]) + self.margin

    def _truncated(self, bbox) -> bool:
        if self.frame_w is None or self.frame_h is None:
            return False
        x1, y1, x2, y2 = bbox
        return x1 <= 2 or x2 >= self.frame_w - 2 or y2 >= self.frame_h - 2

    def _fit(self, h: deque) -> Optional[Dict[str, float]]:
        """Lateral velocity (least squares), fitted start/end x, window length and box growth rate."""
        if len(h) < int(0.7 * self.win):
            return None
        t = np.array([e[0] for e in h], dtype=float) / self.fps
        t = t - t[0]
        x = np.array([e[1] for e in h], dtype=float)
        vx, x0 = np.polyfit(t, x, 1)
        s0, s1 = h[0][3], h[-1][3]
        growth = (s1 / s0 - 1.0) / t[-1] if t[-1] > 0 and s0 > 0 else 0.0
        return {"vx": float(vx), "x0": float(x0), "xn": float(x0 + vx * t[-1]), "T": float(t[-1]), "growth": float(growth)}

    def _raw(self, o: Dict[str, Any], fit: Optional[Dict[str, float]], drift_rate: Optional[float],
             ego_speed: Optional[float]) -> Tuple[Optional[str], bool]:
        """Raw per-frame behaviour candidate and whether it is speed-based (longer persistence)."""
        cls, x, y = o.get("class_name", ""), o["bev_x_m"], o["bev_y_m"]
        reach = self._reach(cls)
        closing = o.get("closing_speed_mps") or 0.0
        moving = ego_speed is not None and np.isfinite(ego_speed)
        if cls in VEHICLES and moving and ego_speed >= 2.0 and closing >= ego_speed + 3.0 and abs(x) < reach + 1.0                 and y < 30.0:
            return "ONCOMING", True
        if fit is not None:
            # remove the scene-wide lateral drift caused by ego yaw (proportional to distance)
            corr = (drift_rate or 0.0) * y
            vx = fit["vx"] - corr
            x0 = fit["x0"]
            xn = fit["xn"] - corr * fit["T"]
            towards = np.sign(xn) * vx < 0 or np.sign(x0) != np.sign(xn)
            moved = abs(x0) - abs(xn)
            if cls in VEHICLES and abs(x0) >= reach and towards and abs(vx) >= 0.5 and moved >= 0.7 \
                    and abs(xn) < reach + 0.8 and y < 20.0 and fit["growth"] < self.max_cutin_growth:
                return "CUT-IN", False
            if cls in VULNERABLE_PEDS and abs(vx) >= 0.5 and abs(xn - x0) >= 0.7 and y < 20.0:
                t = np.linspace(0.0, 2.0, 21)
                if np.any(np.abs(xn + vx * t) < reach) and (towards or abs(xn) < reach):
                    return "CROSSING", False
        if moving and ego_speed >= 3.0 and cls in VEHICLES and abs(x) < reach and y < 25.0 \
                and ego_speed - closing < 1.5:
            return "SLOW LEAD", True
        if reach <= abs(x) < reach + 2.5 and y < 12.0 and (fit is None or abs(fit["vx"]) < 0.3):
            return "SIDE PASS", False
        return None, False

    def update(self, objects: List[Dict[str, Any]], frame_idx: int,
               ego_speed_mps: Optional[float] = None) -> List[Tuple[int, str, Dict[str, Any]]]:
        """Annotates objects with o['behavior'] (or None); returns newly confirmed (track_id, behaviour, object)."""
        seen, onsets, fits = set(), [], {}
        valid = []
        # pass 1: history + lateral fits
        for o in objects:
            tid = o.get("track_id", -1)
            o["behavior"] = None
            o["lat_vel_mps"] = None
            if tid is None or tid < 0 or o.get("bev_x_m") is None or o.get("bev_y_m") is None:
                continue
            seen.add(tid)
            if not (0.0 < o["bev_y_m"] <= self.max_range):
                continue
            bbox = o.get("bbox")
            if bbox is not None and self._truncated(bbox):
                self.hist.pop(tid, None)  # position unreliable while cut by the border: restart the history
            else:
                scale = (bbox[3] - bbox[1]) if bbox is not None else 1.0
                self.hist[tid].append((frame_idx, o["bev_x_m"], o["bev_y_m"], max(1.0, scale)))
                fits[tid] = self._fit(self.hist[tid])
            valid.append(o)
        # scene drift rate (lateral m/s per metre of distance), from all tracks with a fit
        rates = {tid: f["vx"] / max(3.0, o["bev_y_m"]) for o in valid for tid, f in [(o["track_id"], fits.get(o["track_id"]))] if f}
        # pass 2: behaviour candidates, persistence and hold
        for o in valid:
            tid = o["track_id"]
            others = [r for t, r in rates.items() if t != tid]
            drift = float(np.median(others)) if len(others) >= 2 else None
            fit = fits.get(tid)
            # drift-corrected lateral velocity (m/s, + = rightwards) for the decision's path-threat model
            o["lat_vel_mps"] = (fit["vx"] - (drift or 0.0) * o["bev_y_m"]) if fit else None
            cand, speed_based = self._raw(o, fit, drift, ego_speed_mps)
            for b in BEHAVIORS:
                self.streak[(tid, b)] = self.streak[(tid, b)] + 1 if b == cand else 0
            need = self.n_persist_speed if speed_based else self.n_persist
            if cand is not None and self.streak[(tid, cand)] >= need:
                prev = self.active.get(tid)
                if prev is None or prev[0] != cand:
                    self.counts[cand] += 1
                    onsets.append((tid, cand, o))
                self.active[tid] = (cand, frame_idx)
            cur = self.active.get(tid)
            if cur is not None and frame_idx - cur[1] > self.n_hold:
                del self.active[tid]
                cur = None
            o["behavior"] = cur[0] if cur else None
        for tid in [t for t in list(self.hist) if t not in seen]:
            if frame_idx - self.hist[tid][-1][0] > self.n_hold:
                self.hist.pop(tid, None)
                self.active.pop(tid, None)
                for b in BEHAVIORS:
                    self.streak.pop((tid, b), None)
        return onsets
