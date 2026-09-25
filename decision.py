"""
PathSense - Decision State (bottom-right HUD)
Module: decision.py

Turns per-frame perception/planning outputs into a stable driving recommendation:

    GO STRAIGHT / STEER LEFT / STEER RIGHT   (state GO)
    SLOW DOWN                                (state CAUTION)
    BRAKE / NO SAFE PATH - BRAKE             (state BRAKE)

Per-frame ("instant") risk combines, for every obstacle projected into the BEV:
  - distance ahead (BEV Y), closing speed / heuristic TTC,
  - overlap with the ego corridor along the *selected* arc (vehicle half-width + obstacle collision radius + margin),
  - vulnerability (pedestrians / animals / bicycles),
  - whether the planner found every candidate arc blocked.
A temporal state machine then debounces it: escalation needs a short persistence (immediate for emergencies),
BRAKE is held for a minimum time, and de-escalation goes BRAKE -> CAUTION -> GO with recovery delays,
so the HUD never flickers between SAFE and BRAKE.

All distances/TTCs are monocular estimates (see depth.py / ttc.py); thresholds are heuristic, not certified.
"""

from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from costmap import CLASS_SAFETY_PROFILES

GO, CAUTION, BRAKE = 0, 1, 2
STATE_NAMES = {GO: "GO", CAUTION: "CAUTION", BRAKE: "BRAKE"}
VULNERABLE = {"person", "bicycle", "cat", "dog", "sheep", "cow", "horse", "elephant"}


class DecisionState:
    def __init__(
        self,
        fps: float = 25.0,
        vehicle_half_width_m: float = 0.9,
        corridor_margin_m: float = 0.3,
        vru_margin_m: float = 1.5,
        lookahead_m: float = 22.0,
        min_gap_m: float = 2.0,             # anything in the corridor closer than this -> BRAKE
        follow_distance_m: float = 8.0,     # in-corridor object closer than this -> SLOW DOWN (used when ego speed unknown)
        follow_headway_s: float = 1.2,      # with ego speed: follow distance = max(min_follow_m, headway * speed)
        min_follow_m: float = 5.0,
        beside_margin_m: float = 0.5,       # "closing beside path" if within collision reach + this margin
        emergency_ttc_s: float = 1.0,       # in-corridor TTC below this -> immediate BRAKE (no debounce)
        brake_ttc_s: float = 2.0,           # in-corridor TTC below this (and within brake_range_m) -> BRAKE
        brake_range_m: float = 15.0,
        caution_ttc_s: float = 4.0,
        vru_brake_distance_m: float = 6.0,
        vru_caution_distance_m: float = 15.0,
        steer_indicate_deg: float = 4.0,
        caution_on_s: float = 0.3,
        brake_on_s: float = 0.2,
        brake_hold_s: float = 1.0,
        brake_release_s: float = 0.6,
        caution_release_s: float = 1.0
    ):
        fps = fps if fps and np.isfinite(fps) and fps > 0 else 25.0
        f = lambda s: max(1, int(round(s * fps)))
        self.half_w, self.margin, self.vru_margin = vehicle_half_width_m, corridor_margin_m, vru_margin_m
        self.lookahead = lookahead_m
        self.min_gap, self.follow_default = min_gap_m, follow_distance_m
        self.headway, self.min_follow, self.beside_margin = follow_headway_s, min_follow_m, beside_margin_m
        self.follow = follow_distance_m
        self.emergency_ttc, self.brake_ttc, self.brake_range, self.caution_ttc = emergency_ttc_s, brake_ttc_s, brake_range_m, caution_ttc_s
        self.vru_brake, self.vru_caution = vru_brake_distance_m, vru_caution_distance_m
        self.steer_indicate = steer_indicate_deg
        self.n_caution_on, self.n_brake_on = f(caution_on_s), f(brake_on_s)
        self.n_brake_hold, self.n_brake_release, self.n_caution_release = f(brake_hold_s), f(brake_release_s), f(caution_release_s)

        self.state = GO
        self.frame = 0
        self.entered_at = 0
        self.cnt_caution = self.cnt_brake = self.cnt_below_brake = self.cnt_clear = 0
        self.state_reason = ""
        self.state_key_id: Optional[int] = None
        self.state_blocked = False
        self.transitions = 0

    # ------------------------------------------------------------------ per-frame assessment
    @staticmethod
    def _path_x_at(path: List[Tuple[float, float]], y: float) -> float:
        if not path:
            return 0.0
        ys = np.array([p[1] for p in path]); xs = np.array([p[0] for p in path])
        if y >= ys[-1]:
            return float(xs[-1])
        return float(np.interp(y, ys, xs))

    def assess(self, objects: List[Dict[str, Any]], path: List[Tuple[float, float]],
               all_blocked: bool) -> Tuple[int, str, Optional[int], bool]:
        """Returns (instant_level, reason, key_track_id, emergency)."""
        best = (GO, "clear path", None, False, float("inf"))  # level, reason, id, emergency, sort distance
        corridor_path = [] if all_blocked else path  # if nothing is drivable, judge against the straight corridor

        def consider(level, reason, obj, emergency=False):
            nonlocal best
            y = obj.get("bev_y_m", 99.0)
            if (level, emergency, -y) > (best[0], best[3], -best[4]):
                best = (level, reason, obj.get("track_id"), emergency, y)

        for o in objects:
            y, x = o.get("bev_y_m"), o.get("bev_x_m")
            if y is None or x is None or not (0.0 < y <= self.lookahead):
                continue
            cls = o.get("class_name", "obstacle")
            prof = CLASS_SAFETY_PROFILES.get(cls, CLASS_SAFETY_PROFILES["default"])
            reach = self.half_w + prof.get("collision_radius", prof["footprint_radius"]) + self.margin
            # Corridor = straight ahead OR along the selected arc: a threat directly ahead keeps SLOW DOWN/BRAKE
            # active even while the planner steers around it (conservative)
            lat = min(abs(x), abs(x - self._path_x_at(corridor_path, y)))
            o["in_corridor"] = bool(lat < reach)  # consumed by the HUD for box colouring
            ttc = o.get("ttc_sec")
            vru = cls in VULNERABLE
            tag = f"{cls} #{o.get('track_id', -1)}"
            if lat < reach:
                if ttc is not None and ttc < self.emergency_ttc:
                    consider(BRAKE, f"{tag} in path, closing fast (TTC {ttc:.1f}s)", o, emergency=True)
                elif y < self.min_gap:
                    consider(BRAKE, f"{tag} in path at {y:.1f} m (below min gap)", o)
                elif ttc is not None and ttc < self.brake_ttc and y < self.brake_range:
                    consider(BRAKE, f"{tag} in path, closing (TTC {ttc:.1f}s, {y:.0f} m)", o)
                elif vru and y < self.vru_brake:
                    consider(BRAKE, f"{tag} in path at {y:.1f} m", o)
                elif ttc is not None and ttc < self.caution_ttc:
                    consider(CAUTION, f"{tag} ahead, closing (TTC {ttc:.1f}s)", o)
                elif vru and y < self.vru_caution:
                    consider(CAUTION, f"{tag} in path at {y:.0f} m", o)
                elif y < self.follow:
                    consider(CAUTION, f"following {tag} at {y:.1f} m", o)
            elif vru and lat < reach + self.vru_margin and y < self.vru_caution:
                consider(CAUTION, f"{tag} near path ({y:.0f} m)", o)
            elif ttc is not None and ttc < self.brake_ttc and lat < reach + self.beside_margin and y < 10.0:
                consider(CAUTION, f"{tag} closing beside path (TTC {ttc:.1f}s)", o)

        if all_blocked:
            consider(BRAKE, "all candidate paths blocked", {"bev_y_m": 0.0, "track_id": best[2]})
        return best[0], best[1], best[2], best[3]

    # ------------------------------------------------------------------ temporal state machine
    def update(self, objects: List[Dict[str, Any]], path: List[Tuple[float, float]], all_blocked: bool,
               steering_deg: float, ego_speed_mps: Optional[float] = None) -> Dict[str, Any]:
        # Following distance scales with (monocular, approximate) ego speed; fixed default when speed is unknown
        if ego_speed_mps is not None and np.isfinite(ego_speed_mps) and ego_speed_mps >= 0:
            self.follow = max(self.min_follow, self.headway * ego_speed_mps)
        else:
            self.follow = self.follow_default
        level, reason, key_id, emergency = self.assess(objects, path, all_blocked)
        self.frame += 1
        self.cnt_brake = self.cnt_brake + 1 if level >= BRAKE else 0
        self.cnt_caution = self.cnt_caution + 1 if level >= CAUTION else 0
        self.cnt_below_brake = self.cnt_below_brake + 1 if level < BRAKE else 0
        self.cnt_clear = self.cnt_clear + 1 if level == GO else 0

        prev = self.state
        if self.state < BRAKE and (emergency or self.cnt_brake >= self.n_brake_on):
            self.state = BRAKE
        elif self.state == GO and self.cnt_caution >= self.n_caution_on:
            self.state = CAUTION
        elif (self.state == BRAKE and self.frame - self.entered_at >= self.n_brake_hold
              and self.cnt_below_brake >= self.n_brake_release):
            self.state = CAUTION  # never BRAKE -> GO directly
            self.cnt_clear = 0
        elif self.state == CAUTION and self.cnt_clear >= self.n_caution_release:
            self.state = GO
        if self.state != prev:
            self.transitions += 1
            self.entered_at = self.frame

        # The displayed reason follows the risk that justifies the current state
        if level >= self.state and level > GO:
            self.state_reason, self.state_key_id = reason, key_id
            self.state_blocked = all_blocked and level == BRAKE and reason == "all candidate paths blocked"
        elif self.state > level:
            if not self.state_reason.startswith("holding"):
                self.state_reason = f"holding: {self.state_reason}" if self.state_reason else "holding"
        if self.state == GO:
            self.state_reason, self.state_key_id, self.state_blocked = "", None, False

        if self.state == BRAKE:
            label = "NO SAFE PATH - BRAKE" if self.state_blocked else "BRAKE"
        elif self.state == CAUTION:
            label = "SLOW DOWN"
        elif abs(steering_deg) >= self.steer_indicate:
            label = "STEER LEFT" if steering_deg < 0 else "STEER RIGHT"
            self.state_reason = "avoiding obstacle cost ahead"
        else:
            label = "GO STRAIGHT"
            self.state_reason = "clear corridor" if level == GO else f"monitoring: {reason}"
        return {"state": STATE_NAMES[self.state], "level": self.state, "label": label, "reason": self.state_reason,
                "key_track_id": self.state_key_id, "instant_level": level, "instant_reason": reason}
