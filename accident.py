"""
PathSense - Accident detection + emergency response workflow (research demo)
Module: accident.py

AccidentDetector - conservative, multi-cue, temporally confirmed collision detection from the existing pipeline
state (tracked objects, BEV distances, closing speed/TTC, ego speed, frame-difference "jolt"):

  primary cues (a collision-like interaction is required):
    IMPACT        in-corridor object closer than 2 m and closing fast (TTC < 0.8 s or closing > 2 m/s)
    OBJ_COLLISION two tracked road users whose boxes newly overlap (IoU > 0.25, previously < 0.1) after approaching
                  each other at similar depth (|dZ| < 2.5 m) - excludes ordinary perspective occlusion
    DISRUPTION    tracked vehicle whose box shape changes abruptly (aspect ratio +/-45% within 0.5 s), e.g. spin/rollover
  supporting cues (never trigger alone):
    JOLT          frame-difference spike (> 3.5x its running median): camera shake at impact
    EGO_STOP      ego speed drop >= 15 km/h within 1 s to < 5 km/h
  post-collision evidence (needed to confirm):
    STATIONARY    the involved object(s) stay (almost) still for >= 1 s, or the ego vehicle stays stopped

  state machine: NORMAL -> POSSIBLE (primary cue) -> CONFIRMED (confidence >= 0.7 with post-collision evidence within
  3 s) ; POSSIBLE without confirmation returns to NORMAL (logged as unconfirmed). CONFIRMED is latched until reset().
  One noisy frame can never raise an emergency.

EmergencyService - emergency workflow behind a small interface. The default adapter is a SIMULATION: nothing is sent
or called. A real provider could be plugged in later only after authorisation, and only after explicit user
confirmation; credentials must never be committed.
"""

import json
import math
import os
import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

NORMAL, POSSIBLE, CONFIRMED = "NORMAL", "POSSIBLE", "CONFIRMED"
ROAD_USERS = {"car", "truck", "bus", "motorcycle", "bicycle", "auto-rickshaw", "person"}
VEHICLES = {"car", "truck", "bus", "motorcycle", "bicycle", "auto-rickshaw"}


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _overlap_frac(a, b) -> float:
    """Fraction of box a covered by box b."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy / max(1.0, (a[2] - a[0]) * (a[3] - a[1]))


def _center(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


class AccidentDetector:
    def __init__(self, fps: float = 25.0, confirm_window_s: float = 3.0, stationary_s: float = 1.0,
                 confirm_threshold: float = 0.7, frame_width: Optional[int] = None):
        self.frame_width = frame_width  # boxes clipped by the image edge change shape without any real deformation
        fps = fps if fps and np.isfinite(fps) and fps > 0 else 25.0
        self.fps = fps
        f = lambda s: max(1, int(round(s * fps)))
        self.n_half, self.n_1s, self.n_confirm, self.n_still = f(0.5), f(1.0), f(confirm_window_s), f(stationary_s)
        self.confirm_threshold = confirm_threshold
        self.boxes: Dict[int, deque] = defaultdict(lambda: deque(maxlen=f(1.5)))
        self.depth: Dict[int, float] = {}
        self.depth_hist: Dict[int, deque] = defaultdict(lambda: deque(maxlen=f(1.5)))
        self.pair_iou: Dict[Tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=f(1.0)))
        self.speeds: deque = deque(maxlen=f(1.5))
        self.jolts: deque = deque(maxlen=f(3.0))
        self.recent: deque = deque(maxlen=f(1.0))   # recent supporting cues (frame, name)
        self.primary_hits: deque = deque(maxlen=f(0.5))  # frames with a primary cue (persistence check)
        self.n_primary_min = 3
        self.reset()

    def reset(self):
        self.state = NORMAL
        self.state_since = 0
        self.confidence = 0.0
        self.cues: List[str] = []
        self.involved: List[int] = []
        self.primary_frame: Optional[int] = None
        self.possible_events = 0
        self.unconfirmed_events = 0
        self.confirmed_frame: Optional[int] = None

    # ---------------------------------------------------------------- cues
    def _jolt_cue(self, jolt: Optional[float]) -> bool:
        if jolt is None:
            return False
        spike = False
        if len(self.jolts) >= int(0.5 * self.jolts.maxlen):
            med = float(np.median(self.jolts))
            spike = med > 0 and jolt > 3.5 * med and jolt > 4.0
        self.jolts.append(jolt)
        return spike

    def _ego_stop_cue(self, speed_kmh: Optional[float]) -> bool:
        if speed_kmh is None:
            return False
        self.speeds.append(speed_kmh)
        if len(self.speeds) <= self.n_1s:
            return False
        before = self.speeds[-1 - self.n_1s]
        return before - speed_kmh >= 15.0 and speed_kmh < 5.0

    def _primary_cues(self, frame_idx: int, objects: List[Dict[str, Any]]) -> Tuple[List[str], List[int]]:
        cues, involved = [], []
        cur = {o["track_id"]: o for o in objects if o.get("track_id", -1) >= 0 and o.get("class_name") in ROAD_USERS}
        for tid, o in cur.items():
            self.boxes[tid].append((frame_idx, list(o["bbox"]), o.get("closing_speed_mps")))
            if o.get("bev_y_m") is not None:
                self.depth[tid] = o["bev_y_m"]
                self.depth_hist[tid].append(o["bev_y_m"])
        # IMPACT: in-corridor, very close and closing fast - and actually tracked approaching (>= 1 m closer than
        # within the last 1.5 s). An object that appears already "at 1.5 m" (window reflection, bonnet, occluder)
        # with noisy depth is not an impact.
        for tid, o in cur.items():
            y, ttc, clos = o.get("bev_y_m"), o.get("ttc_sec"), o.get("closing_speed_mps") or 0.0
            hist = self.depth_hist.get(tid) or []
            approached = len(hist) >= self.n_half and max(hist) - (y or 0.0) >= 1.0
            if o.get("in_corridor") and y is not None and y < 2.0 and approached                     and ((ttc is not None and ttc < 0.8) or clos > 2.0):
                cues.append("IMPACT"); involved.append(tid)
        # OBJ_COLLISION: newly overlapping boxes of two road users that approached each other at similar depth
        ids = sorted(cur)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                if not ({cur[a]["class_name"], cur[b]["class_name"]} & VEHICLES):
                    continue
                iou = _iou(cur[a]["bbox"], cur[b]["bbox"])
                hist = self.pair_iou[(a, b)]
                hist.append(iou)
                if iou <= 0.25 or len(hist) < self.n_half:
                    continue
                if min(list(hist)[:-max(1, self.n_half // 3)]) >= 0.1:
                    continue  # they have overlapped all along: occlusion, not a new contact
                za, zb = self.depth.get(a), self.depth.get(b)
                if za is None or zb is None or abs(za - zb) > 2.5:
                    continue
                ha, hb = self.boxes[a], self.boxes[b]
                if len(ha) > self.n_half and len(hb) > self.n_half:
                    (ax0, ay0), (bx0, by0) = _center(ha[-1 - self.n_half][1]), _center(hb[-1 - self.n_half][1])  # noqa
                    (ax1, ay1), (bx1, by1) = _center(ha[-1][1]), _center(hb[-1][1])
                    d0, d1 = math.hypot(ax0 - bx0, ay0 - by0), math.hypot(ax1 - bx1, ay1 - by1)
                    if d0 > 0 and d1 < 0.7 * d0:
                        cues.append("OBJ_COLLISION"); involved += [a, b]
        # DISRUPTION: abrupt change of a vehicle's box shape (spin / rollover / violent deflection)
        for tid, o in cur.items():
            if o["class_name"] not in VEHICLES or (o.get("bev_y_m") or 99) > 25:
                continue
            h = self.boxes[tid]
            if len(h) > self.n_half:
                b0, b1 = h[-1 - self.n_half][1], h[-1][1]
                if self.frame_width and any(b[0] < 4 or b[2] > self.frame_width - 4 for b in (b0, b1)):
                    continue  # entering / leaving the frame: shape change is truncation, not a collision
                if any(_overlap_frac(b1, other["bbox"]) > 0.15 for oid, other in cur.items() if oid != tid):
                    continue  # partly occluded by another road user: the visible box shape is unreliable
                r0 = (b0[2] - b0[0]) / max(1.0, b0[3] - b0[1])
                r1 = (b1[2] - b1[0]) / max(1.0, b1[3] - b1[1])
                if r0 > 0 and abs(r1 / r0 - 1.0) > 0.45 and (b1[2] - b1[0]) > 40:
                    cues.append("DISRUPTION"); involved.append(tid)
        for tid in [t for t, h in self.boxes.items() if h and frame_idx - h[-1][0] > self.n_1s * 2]:
            self.boxes.pop(tid, None); self.depth.pop(tid, None); self.depth_hist.pop(tid, None)
        return cues, involved

    def _post_collision_still(self, frame_idx: int, speed_kmh: Optional[float]) -> bool:
        """Post-collision evidence in the WORLD frame: ego stopped, or every involved object at rest.
        While the ego vehicle moves, an object is at rest only if it closes at ~ego speed (an object that merely
        keeps a constant image position is travelling with us, not stopped)."""
        # ego standing still is post-collision evidence only after an ABRUPT stop; creeping to a halt in
        # congestion (cattle, queues) is normal traffic
        ego_stopped = "EGO_STOP" in self.cues and len(self.speeds) >= self.n_still \
            and max(list(self.speeds)[-self.n_still:]) < 3.0
        ego_mps = (speed_kmh or 0.0) / 3.6
        still = []
        for tid in set(self.involved):
            h = [e for e in self.boxes.get(tid, []) if frame_idx - e[0] <= self.n_still]
            if len(h) < int(0.8 * self.n_still):
                still.append(False)
                continue
            if ego_mps > 1.5:
                clos = [e[2] for e in h if e[2] is not None]
                still.append(bool(clos) and np.mean([abs(c - ego_mps) < 2.0 for c in clos]) >= 0.8)
            else:
                c = np.array([_center(b) for _, b, _ in h])
                size = max(20.0, float(np.mean([b[2] - b[0] for _, b, _ in h])))
                still.append(float(np.max(np.linalg.norm(c - c[0], axis=1))) < 0.15 * size)
        return ego_stopped or (bool(still) and all(still))

    def _confidence(self, still: bool) -> float:
        n_primary = len({c for c in self.cues if c in ("IMPACT", "OBJ_COLLISION", "DISRUPTION")})
        conf = 0.40 + 0.10 * (n_primary - 1) + (0.15 if "JOLT" in self.cues else 0) \
            + (0.15 if "EGO_STOP" in self.cues else 0) + (0.25 if still else 0)
        return round(min(0.99, conf), 2)

    # ---------------------------------------------------------------- update
    def update(self, frame_idx: int, objects: List[Dict[str, Any]], ego_speed_kmh: Optional[float] = None,
               jolt: Optional[float] = None) -> Dict[str, Any]:
        jolt_cue = self._jolt_cue(jolt)
        stop_cue = self._ego_stop_cue(ego_speed_kmh)
        primary, involved = self._primary_cues(frame_idx, objects)
        if jolt_cue:
            self.recent.append((frame_idx, "JOLT"))
        if stop_cue:
            self.recent.append((frame_idx, "EGO_STOP"))
        support = sorted({n for f, n in self.recent if frame_idx - f <= self.n_1s})

        if primary:
            self.primary_hits.append(frame_idx)
        persistent = sum(1 for f in self.primary_hits if frame_idx - f < self.primary_hits.maxlen) >= self.n_primary_min
        if self.state == NORMAL and primary and persistent:
            self.state, self.state_since, self.primary_frame = POSSIBLE, frame_idx, frame_idx
            self.involved = list(dict.fromkeys(involved))
            self.cues = sorted(set(primary) | set(support))
            self.possible_events += 1
            self.confidence = self._confidence(still=False)
        elif self.state == POSSIBLE:
            if primary:
                self.involved = list(dict.fromkeys(self.involved + involved))
                self.cues = sorted(set(self.cues) | set(primary))
            self.cues = sorted(set(self.cues) | set(support))
            still = frame_idx - self.primary_frame >= self.n_still and self._post_collision_still(frame_idx, ego_speed_kmh)
            if still and "STATIONARY" not in self.cues:
                self.cues = sorted(set(self.cues) | {"STATIONARY"})
            self.confidence = self._confidence(still)
            # conservative: needs physical evidence (impact / camera jolt / abrupt ego stop), or two independent visual
            # collision cues while the ego vehicle is still moving (a third-party crash seen from a moving car)
            physical = bool({"IMPACT", "JOLT", "EGO_STOP"} & set(self.cues))
            n_visual = len({"OBJ_COLLISION", "DISRUPTION"} & set(self.cues))
            ego_moving = (ego_speed_kmh or 0.0) > 10.0
            if still and self.confidence >= self.confirm_threshold and (physical or (n_visual >= 2 and ego_moving)):
                self.state, self.state_since, self.confirmed_frame = CONFIRMED, frame_idx, frame_idx
            elif frame_idx - self.primary_frame > self.n_confirm:
                self.state, self.state_since, self.confidence = NORMAL, frame_idx, 0.0
                self.unconfirmed_events += 1
                self.cues, self.involved = [], []
        if self.state == NORMAL:
            self.confidence = 0.0
        return {"state": self.state, "confidence": self.confidence, "cues": list(self.cues),
                "involved_track_ids": list(self.involved), "since_frame": self.state_since}


# ==================================================================== emergency workflow
OVERPASS_ENDPOINTS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")


class SimulatedNotifier:
    """Demo adapter: records what WOULD be sent. Never sends SMS/e-mail/calls."""
    name = "simulation"

    def send(self, channel: str, recipient: str, message: str) -> Dict[str, Any]:
        return {"status": "SIMULATED", "channel": channel, "recipient": recipient, "sent": False,
                "note": "Simulation only - no message was sent", "time": time.strftime("%Y-%m-%d %H:%M:%S")}


class EmergencyService:
    """
    detect_accident() / get_location() / find_nearby_hospital() / prepare_emergency_message() /
    request_user_confirmation() / send_notification(). Demo mode (default) uses SimulatedNotifier; a real provider
    would only be used in confirmed mode, after explicit user confirmation, with credentials kept out of Git.
    """

    def __init__(self, config_path: str = "config/emergency.json", mode: str = "demo", notifier=None):
        self.config_path = config_path
        self.mode = mode
        self.notifier = notifier or SimulatedNotifier()

    # -- configuration (local prototype storage, gitignored)
    def load_contact(self) -> Dict[str, Any]:
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_contact(self, data: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {k: str(v)[:120] for k, v in data.items()
                   if k in ("contact_name", "contact_phone", "contact_email", "driver_name", "vehicle", "demo_lat", "demo_lon")}
        os.makedirs(os.path.dirname(os.path.abspath(self.config_path)), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(allowed, f, indent=1)
        return allowed

    def delete_contact(self):
        if os.path.exists(self.config_path):
            os.remove(self.config_path)

    # -- workflow
    @staticmethod
    def detect_accident(status: Dict[str, Any]) -> bool:
        return status.get("state") == CONFIRMED

    def get_location(self, browser_lat: Optional[float] = None, browser_lon: Optional[float] = None) -> Dict[str, Any]:
        if browser_lat is not None and browser_lon is not None:
            return {"lat": float(browser_lat), "lon": float(browser_lon), "source": "browser geolocation"}
        cfg = self.load_contact()
        if cfg.get("demo_lat") and cfg.get("demo_lon"):
            return {"lat": float(cfg["demo_lat"]), "lon": float(cfg["demo_lon"]), "source": "DEMO LOCATION (configured)"}
        return {"lat": None, "lon": None, "source": "unavailable"}

    @staticmethod
    def find_nearby_hospital(lat: float, lon: float, radius_m: int = 6000, timeout_s: float = 12.0) -> Dict[str, Any]:
        """Nearest hospitals / ambulance stations from OpenStreetMap (Overpass API). Read-only lookup."""
        import requests
        q = f"""[out:json][timeout:10];(
          nwr(around:{radius_m},{lat},{lon})["amenity"="hospital"];
          nwr(around:{radius_m},{lat},{lon})["emergency"="ambulance_station"];
        );out center tags 40;"""
        last_err = None
        for url in OVERPASS_ENDPOINTS:  # public OpenStreetMap mirrors, tried in order
            try:
                r = requests.post(url, data={"data": q}, timeout=timeout_s, headers={"User-Agent": "PathSense-SIH-prototype"})
                r.raise_for_status()
                break
            except Exception as e:
                last_err = e
        else:
            kind = "network unreachable" if isinstance(last_err, requests.ConnectionError) else type(last_err).__name__
            return {"source": "OpenStreetMap (Overpass API)", "results": [],
                    "error": f"hospital lookup unavailable ({kind}) - check the internet connection"}
        try:
            items = []
            for e in r.json().get("elements", []):
                t = e.get("tags", {})
                la, lo = e.get("lat", e.get("center", {}).get("lat")), e.get("lon", e.get("center", {}).get("lon"))
                if la is None or lo is None:
                    continue
                d = 6371.0 * 2 * math.asin(math.sqrt(math.sin(math.radians(la - lat) / 2) ** 2 + math.cos(math.radians(lat))
                                                     * math.cos(math.radians(la)) * math.sin(math.radians(lo - lon) / 2) ** 2))
                items.append({"name": t.get("name") or t.get("name:en") or "Unnamed hospital",
                              "phone": t.get("phone") or t.get("contact:phone") or t.get("emergency:phone"),
                              "kind": "ambulance station" if t.get("emergency") == "ambulance_station" else "hospital",
                              "emergency_dept": t.get("emergency"), "distance_km": round(d, 2), "lat": la, "lon": lo})
            items.sort(key=lambda h: h["distance_km"])
            return {"source": "OpenStreetMap (Overpass API)", "results": items[:5]}
        except Exception as e:
            return {"source": "OpenStreetMap (Overpass API)", "results": [], "error": f"unexpected response ({type(e).__name__})"}

    def prepare_emergency_message(self, event: Dict[str, Any], location: Dict[str, Any]) -> str:
        cfg = self.load_contact()
        loc = (f"{location['lat']:.5f}, {location['lon']:.5f} ({location['source']})"
               if location.get("lat") is not None else "location unavailable")
        who = cfg.get("driver_name") or "the driver"
        veh = f" ({cfg['vehicle']})" if cfg.get("vehicle") else ""
        return (f"PathSense: possible road accident detected for {who}{veh} at {event.get('time', '')}. "
                f"Confidence {int(100 * event.get('confidence', 0))}%. Location: {loc}. "
                "Automated message from a research prototype - please call to confirm.")

    @staticmethod
    def request_user_confirmation() -> Dict[str, Any]:
        return {"required": True, "prompt": "Confirm emergency action?",
                "note": "Demo mode: confirming only records the action; nothing is sent or called."}

    def send_notification(self, message: str, confirmed_by_user: bool = False) -> Dict[str, Any]:
        cfg = self.load_contact()
        results = []
        if cfg.get("contact_phone"):
            results.append(self.notifier.send("sms", cfg["contact_phone"], message))
        if cfg.get("contact_email"):
            results.append(self.notifier.send("email", cfg["contact_email"], message))
        if not results:
            results.append({"status": "NO CONTACT CONFIGURED", "sent": False})
        return {"mode": self.mode, "adapter": self.notifier.name, "confirmed_by_user": confirmed_by_user,
                "results": results}
