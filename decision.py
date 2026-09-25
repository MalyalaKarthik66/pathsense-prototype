"""
PathSense - Decision State (bottom-right HUD)
Module: decision.py

Turns per-frame perception/planning outputs into a stable driving recommendation:

    GO STRAIGHT / STEER LEFT / STEER RIGHT   (state GO)
    SLOW DOWN                                (state CAUTION)
    BRAKE / NO SAFE PATH - BRAKE             (state BRAKE)

The output also carries a short reason title (e.g. "Slow vehicle ahead", "Motorcycle cut-in"), the responsible
object (class, distance, TTC, behaviour), the path status from the planner's per-arc collision test
(CLEAR / PARTIAL / BLOCKED) and the number of objects currently raising risk. "NO SAFE PATH" is shown only while
the planner finds every candidate arc blocked in the current frame.

Per-frame ("instant") risk combines, for every obstacle projected into the BEV:
  - distance ahead (BEV Y), closing speed / heuristic TTC,
  - overlap with the ego corridor along the *selected* arc (vehicle half-width + obstacle collision radius + margin),
  - vulnerability (pedestrians / animals / bicycles),
  - behaviour cues from behavior.py (cut-in / crossing / oncoming raise at least SLOW DOWN near the path),
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
        label_debounce_s: float = 0.3,      # NO SAFE PATH / STEER label must persist this long before it is shown
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
        self.n_label = f(label_debounce_s)
        self.blocked_shown, self.cnt_blocked_flip = False, 0
        self.steer_shown, self.cnt_steer_flip = "", 0

        self.state = GO
        self.frame = 0
        self.entered_at = 0
        self.cnt_caution = self.cnt_brake = self.cnt_below_brake = self.cnt_clear = 0
        self.state_reason = ""
        self.state_title = ""
        self.state_title_level = GO
        self.state_key_id: Optional[int] = None
        self.state_key: Optional[Dict[str, Any]] = None
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

    @staticmethod
    def _key_snapshot(o: Dict[str, Any]) -> Dict[str, Any]:
        return {"track_id": o.get("track_id"), "class_name": o.get("class_name"), "distance_m": o.get("bev_y_m"),
                "lateral_m": o.get("bev_x_m"), "ttc_s": o.get("ttc_sec"), "behavior": o.get("behavior"),
                "in_corridor": o.get("in_corridor")}

    def assess(self, objects: List[Dict[str, Any]], path: List[Tuple[float, float]],
               all_blocked: bool) -> Tuple[int, str, Optional[int], bool]:
        """Returns (instant_level, reason, key_track_id, emergency); details in self.last_assessment."""
        best = {"level": GO, "emergency": False, "y": float("inf"), "title": "Path clear", "detail": "", "obj": None}
        threats = 0
        corridor_path = [] if all_blocked else path  # if nothing is drivable, judge against the straight corridor

        def consider(level, title, detail, obj, emergency=False):
            nonlocal best, threats
            threats += 1
            y = obj.get("bev_y_m", 99.0)
            if (level, emergency, -y) > (best["level"], best["emergency"], -best["y"]):
                best = {"level": level, "emergency": emergency, "y": y, "title": title, "detail": detail, "obj": obj}

        for o in objects:
            y, x = o.get("bev_y_m"), o.get("bev_x_m")
            o["in_corridor"] = False
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
            beh = o.get("behavior")
            what = cls.replace("person", "pedestrian").capitalize()
            tag = f"{cls} #{o.get('track_id', -1)}"
            ttc_s = f", TTC {ttc:.1f}s" if ttc is not None else ""
            kind = "Pedestrian" if cls == "person" else ("Animal" if vru and cls != "bicycle" else what)
            if lat < reach:
                if ttc is not None and ttc < self.emergency_ttc:
                    consider(BRAKE, "Collision risk", f"{tag} in path, closing fast (TTC {ttc:.1f}s)", o, emergency=True)
                elif y < self.min_gap:
                    consider(BRAKE, "Too close", f"{tag} in path at {y:.1f} m (below min gap)", o)
                elif ttc is not None and ttc < self.brake_ttc and y < self.brake_range:
                    consider(BRAKE, "Oncoming vehicle - path conflict" if beh == "ONCOMING" else "Collision risk",
                             f"{tag} in path, closing (TTC {ttc:.1f}s, {y:.0f} m)", o)
                elif vru and y < self.vru_brake:
                    consider(BRAKE, f"{kind} in path", f"{tag} in path at {y:.1f} m", o)
                elif beh in ("CUT-IN", "ONCOMING", "CROSSING"):
                    title = {"CUT-IN": f"{what} cut-in", "ONCOMING": "Oncoming vehicle", "CROSSING": f"{kind} crossing"}[beh]
                    consider(CAUTION, title, f"{tag} {beh.lower()} at {y:.1f} m{ttc_s}", o)
                elif ttc is not None and ttc < self.caution_ttc:
                    consider(CAUTION, "Slow vehicle ahead" if beh == "SLOW LEAD" or not vru else f"{kind} ahead",
                             f"{tag} ahead, closing (TTC {ttc:.1f}s)", o)
                elif vru and y < self.vru_caution:
                    consider(CAUTION, f"{kind} ahead", f"{tag} in path at {y:.0f} m", o)
                elif beh == "SLOW LEAD" and y < self.follow * 2:
                    consider(CAUTION, "Slow vehicle ahead", f"{tag} nearly stopped at {y:.1f} m", o)
                elif y < self.follow:
                    consider(CAUTION, "Following closely", f"following {tag} at {y:.1f} m", o)
            elif beh in ("CUT-IN", "CROSSING") and lat < reach + 0.8 and y < 15.0:
                title = f"{what} cut-in" if beh == "CUT-IN" else f"{kind} crossing"
                consider(CAUTION, title, f"{tag} moving into path at {y:.1f} m{ttc_s}", o)
            elif beh == "ONCOMING" and lat < reach + 1.0 and y < 20.0:
                consider(CAUTION, "Oncoming vehicle", f"{tag} oncoming near path at {y:.0f} m{ttc_s}", o)
            elif vru and lat < reach + self.vru_margin and y < self.vru_caution:
                consider(CAUTION, f"{kind} near path", f"{tag} near path ({y:.0f} m)", o)
            elif ttc is not None and ttc < self.brake_ttc and lat < reach + self.beside_margin and y < 10.0:
                consider(CAUTION, "Vehicle closing beside path", f"{tag} closing beside path (TTC {ttc:.1f}s)", o)

        if all_blocked:
            consider(BRAKE, "All candidate trajectories blocked", "all candidate paths blocked",
                     {"bev_y_m": 0.0, "track_id": best["obj"].get("track_id") if best["obj"] else None,
                      "_blocked": True, "_key": best["obj"]})
        self.last_assessment = {**best, "threats": threats}
        obj = best["obj"]
        reason = best["detail"] if obj is None or not obj.get("_blocked") else "all candidate paths blocked"
        if best["level"] == GO:
            reason = "clear path"
        key_id = obj.get("track_id") if obj else None
        return best["level"], reason, key_id, best["emergency"]

    # ------------------------------------------------------------------ temporal state machine
    def update(self, objects: List[Dict[str, Any]], path: List[Tuple[float, float]], all_blocked: bool,
               steering_deg: float, ego_speed_mps: Optional[float] = None,
               arc_blocked: Optional[List[bool]] = None) -> Dict[str, Any]:
        # Following distance scales with (monocular, approximate) ego speed; fixed default when speed is unknown
        if ego_speed_mps is not None and np.isfinite(ego_speed_mps) and ego_speed_mps >= 0:
            self.follow = max(self.min_follow, self.headway * ego_speed_mps)
        else:
            self.follow = self.follow_default
        level, reason, key_id, emergency = self.assess(objects, path, all_blocked)
        a = self.last_assessment
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

        # The displayed reason / key object follow the risk that justifies the current state
        holding = False
        if level >= self.state and level > GO:
            obj = a["obj"]
            key_obj = obj.get("_key") if obj and obj.get("_blocked") else obj
            self.state_reason, self.state_title, self.state_key_id = reason, a["title"], key_id
            self.state_title_level = level
            self.state_key = self._key_snapshot(key_obj) if key_obj else None
        elif self.state > level:
            holding = True
            if self.state_key_id is not None:  # refresh the held object's live values if it is still tracked
                live = next((o for o in objects if o.get("track_id") == self.state_key_id), None)
                if live is not None:
                    self.state_key = self._key_snapshot(live)
        if self.state == GO:
            self.state_reason, self.state_title, self.state_key_id, self.state_key = "", "", None, None

        n_arcs = len(arc_blocked) if arc_blocked else 0
        n_blocked = int(sum(arc_blocked)) if arc_blocked else (1 if all_blocked else 0)

        # Label debounce: NO SAFE PATH is shown only after every arc has been blocked for label_debounce_s, and is
        # withdrawn after the same time unblocked -> no frame-by-frame NO SAFE PATH <-> BRAKE flicker
        self.cnt_blocked_flip = self.cnt_blocked_flip + 1 if all_blocked != self.blocked_shown else 0
        if self.cnt_blocked_flip >= self.n_label:
            self.blocked_shown, self.cnt_blocked_flip = all_blocked, 0
        steer_dir = "" if abs(steering_deg) < self.steer_indicate else ("STEER LEFT" if steering_deg < 0 else "STEER RIGHT")
        self.cnt_steer_flip = self.cnt_steer_flip + 1 if steer_dir != self.steer_shown else 0
        if self.cnt_steer_flip >= self.n_label:
            self.steer_shown, self.cnt_steer_flip = steer_dir, 0

        # Path status from the planner's per-arc collision test; BLOCKED uses the same debounced flag as the label,
        # so the chip and the NO SAFE PATH label can never contradict each other
        if self.blocked_shown:
            path_status = "BLOCKED"
        elif n_blocked > 0:
            path_status = "PARTIAL"
        else:
            path_status = "CLEAR"

        if self.state == BRAKE:
            # NO SAFE PATH only when every candidate arc is (persistently) blocked - never for a single-object BRAKE
            label = "NO SAFE PATH - BRAKE" if self.blocked_shown else "BRAKE"
            if self.blocked_shown:
                title = "All candidate trajectories blocked"
            elif holding and self.state_title == "All candidate trajectories blocked":
                title = "Holding BRAKE - paths were blocked"
            else:
                title = self.state_title
        elif self.state == CAUTION:
            label = "SLOW DOWN"
            # after a BRAKE is released, do not keep showing the BRAKE reason as if it were the SLOW DOWN reason
            title = "Clearing after BRAKE" if holding and self.state_title_level > CAUTION else self.state_title
        elif self.steer_shown:
            label = self.steer_shown
            title = "Avoiding obstacle cost ahead"
            self.state_reason = "planner selected an offset trajectory"
        else:
            label = "GO STRAIGHT"
            title = "Path clear" if level == GO else "Monitoring"
            self.state_reason = "clear corridor" if level == GO else f"monitoring: {reason}"
        reason_out = f"holding: {self.state_reason}" if holding and self.state > GO else self.state_reason
        return {"state": STATE_NAMES[self.state], "level": self.state, "label": label, "title": title,
                "reason": reason_out, "holding": holding and self.state > GO, "key_track_id": self.state_key_id,
                "key": self.state_key if self.state > GO else None, "path_status": path_status,
                "blocked_arcs": n_blocked, "n_arcs": n_arcs, "blocked_now": bool(all_blocked), "threats": a["threats"],
                "instant_level": level, "instant_reason": reason}
