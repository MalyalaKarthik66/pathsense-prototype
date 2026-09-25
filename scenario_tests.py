"""
PathSense - Safety scenario regression suite.

Simulates short synthetic sequences (objects with BEV trajectories + depth noise) through the real
TTCComputer -> BEVCostmap -> DynamicArcPlanner -> DecisionState chain and checks the bottom-right decision.
Run after every risk / planner / costmap change:

    python scenario_tests.py            # exit code 0 = all pass
"""

import sys
from typing import Callable, Dict, List, Optional, Set, Tuple
import numpy as np

from ttc import TTCComputer
from costmap import BEVCostmap, CLASS_SAFETY_PROFILES
from planner import DynamicArcPlanner
from decision import DecisionState, GO, CAUTION, BRAKE
from behavior import BehaviorAnalyzer
from detect_track import suppress_riders, apply_rider_memory

FPS = 25.0
W, H = 1280, 720
HEIGHTS = {"car": 1.5, "truck": 3.0, "bus": 3.0, "person": 1.7, "cow": 1.5, "motorcycle": 1.4}

# An actor: (track_id, class_name, x(t), y(t)) with t in seconds; y <= 0 or None means "not visible"
Actor = Tuple[int, str, Callable[[float], float], Callable[[float], Optional[float]]]


def const(v):
    return lambda t: v


def closing(y0, v, stop=None):
    """Distance that shrinks at v m/s (relative), optionally clamped at `stop` metres."""
    return lambda t: max(stop, y0 - v * t) if stop is not None else y0 - v * t


def run(actors: List[Actor], seconds: float, noise: float = 0.03, seed: int = 0, ego_speed: Optional[float] = None):
    rng = np.random.default_rng(seed)
    ttc = TTCComputer(fps=FPS, frame_width=W, frame_height=H)
    cm = BEVCostmap(img_width=W, img_height=H)
    pl = DynamicArcPlanner()
    dec = DecisionState(fps=FPS)
    beh = BehaviorAnalyzer(fps=FPS, frame_width=W, frame_height=H)
    trace = []
    behaviors = set()
    for i in range(int(seconds * FPS)):
        t = i / FPS
        objs = []
        for tid, cls, fx, fy in actors:
            y = fy(t)
            if y is None or y <= 0.3:
                continue
            x = fx(t)
            y_meas = y * (1.0 + noise * rng.standard_normal())
            # Camera-realistic box: physical width projected, clipped to the image; invisible objects are skipped
            half_w = CLASS_SAFETY_PROFILES.get(cls, CLASS_SAFETY_PROFILES["default"])["collision_radius"]
            u1, u2 = cm.cx + (x - half_w) * cm.fx / y, cm.cx + (x + half_w) * cm.fx / y
            if u2 <= 0 or u1 >= W:
                continue
            u1, u2 = max(0.0, u1), min(float(W), u2)
            # vertical extent from the flat-road model (ground contact row) and a typical object height
            v2 = min(float(H), 0.55 * H + cm.fx * 1.30 / y)
            v1 = max(0.0, v2 - HEIGHTS.get(cls, 1.5) * cm.fx / y)
            box = [u1, v1, u2, v2]
            s, v, tt, hz = ttc.update_track(tid, i, y_meas, bbox=box)
            objs.append({"track_id": tid, "class_name": cls, "bbox": box,
                         "smoothed_depth_m": s, "closing_speed_mps": v, "ttc_sec": tt, "hazard_level": hz})
        ttc.prune(i)
        grid, proj = cm.build_costmap(objs)
        _, _, steer, path, _ = pl.evaluate_paths(grid, cm)
        for _, b, _ in beh.update(proj, i, ego_speed_mps=ego_speed):
            behaviors.add(b)
        out = dec.update(proj, path, pl.all_blocked, steer, ego_speed_mps=ego_speed, arc_blocked=pl.lethal_flags)
        trace.append((out["level"], out["label"], steer, out["reason"], out))
    return trace, dec, behaviors


def check(name: str, actors: List[Actor], seconds: float, final: Set[str], max_level: int = BRAKE,
          must_reach: int = GO, max_abs_steer: Optional[float] = None, max_transitions: Optional[int] = None,
          final_level: Optional[Set[int]] = None, ego_speed: Optional[float] = None,
          expect_behavior: Optional[str] = None, forbid_behaviors: Set[str] = frozenset(),
          expect_title: Optional[str] = None) -> bool:
    trace, dec, behaviors = run(actors, seconds, ego_speed=ego_speed)
    levels = [t[0] for t in trace]
    label, reason = trace[-1][1], trace[-1][3]
    steer_max = max(abs(t[2]) for t in trace)
    problems = []
    if expect_behavior and expect_behavior not in behaviors:
        problems.append(f"behaviour {expect_behavior} not detected (got {sorted(behaviors)})")
    if behaviors & set(forbid_behaviors):
        problems.append(f"unexpected behaviour {sorted(behaviors & set(forbid_behaviors))}")
    if expect_title and not any(expect_title.lower() in (t[4].get("title") or "").lower() for t in trace):
        problems.append(f"reason title containing {expect_title!r} never shown")
    if final and label not in final:
        problems.append(f"final label {label!r} not in {sorted(final)}")
    if final_level is not None and levels[-1] not in final_level:
        problems.append(f"final level {levels[-1]} not in {sorted(final_level)}")
    if max(levels) > max_level:
        problems.append(f"reached level {max(levels)} > allowed {max_level}")
    if max(levels) < must_reach:
        problems.append(f"never reached level {must_reach}")
    if max_abs_steer is not None and steer_max > max_abs_steer:
        problems.append(f"|steer| {steer_max:.1f} > {max_abs_steer}")
    if max_transitions is not None and dec.transitions > max_transitions:
        problems.append(f"{dec.transitions} state transitions > {max_transitions}")
    # HUD invariant: NO SAFE PATH is only ever shown together with a BLOCKED path status
    if any(tr[1] == "NO SAFE PATH - BRAKE" and tr[4].get("path_status") != "BLOCKED" for tr in trace):
        problems.append("NO SAFE PATH shown without PATH BLOCKED")
    # BRAKE must never drop straight to GO
    for a, b in zip(levels, levels[1:]):
        if a == BRAKE and b == GO:
            problems.append("direct BRAKE -> GO transition"); break
    ok = not problems
    btxt = ",".join(sorted(behaviors)) if behaviors else "-"
    print(f"  [{'PASS' if ok else 'FAIL'}] {name:52s} final={label:22s} max_level={max(levels)} "
          f"max|steer|={steer_max:4.1f} transitions={dec.transitions} behaviours={btxt}" + ("" if ok else f"  <-- {'; '.join(problems)}"))
    if not ok:
        print(f"         last reason: {reason}")
    return ok


def auto_rickshaw_real_image_check() -> bool:
    """AR2: YOLO + CLIP refinement on two real Bangalore frames (skipped if the clip is not downloaded)."""
    import os, cv2
    path = "data/samples/india_bangalore.webm"
    if not os.path.isfile(path):
        print(f"  [SKIP] {'AR2 auto-rickshaw recognition on real frames':52s} ({path} not present)")
        return True
    from detect_track import DetectorTracker
    from autorickshaw import AutoRickshawClassifier
    det, clf = DetectorTracker(), AutoRickshawClassifier(fps=30.0, every_s=0.0)
    cap, i, frames = cv2.VideoCapture(path), 0, {}
    while cap.grab() and i <= 3710:
        if i in (1000, 1001, 3708, 3709):  # lead auto #62 (~frame 1000); BMTC bus (~frame 3709)
            frames[i] = cap.retrieve()[1]
        i += 1
    ok_auto = ok_bus = False
    for pair, want in (((1000, 1001), "auto"), ((3708, 3709), "bus")):
        for f in pair:
            objs = det.track_frame(frames[f])
            clf.update(frames[f], objs, f)
        classes = {o["class_name"] for o in objs}
        if want == "auto":
            ok_auto = "auto-rickshaw" in classes
        else:
            ok_bus = "bus" in classes or "truck" in classes
    ok = ok_auto and ok_bus and clf.enabled
    print(f"  [{'PASS' if ok else 'FAIL'}] {'AR2 auto-rickshaw recognition on real frames':52s} auto_found={ok_auto} "
          f"bus_kept={ok_bus} classifier_enabled={clf.enabled} {clf.error or ''}")
    return ok


def main() -> int:
    GO_LABELS = {"GO STRAIGHT"}
    results = []
    print("PathSense safety scenario suite")
    # --- the 10 regression scenarios
    results.append(check("1  empty road", [], 3, GO_LABELS, max_level=GO, max_abs_steer=0.5))
    results.append(check("2  vehicle far ahead, same speed (25 m)", [(1, "car", const(0.0), const(25.0))], 4,
                         GO_LABELS | {"SLOW DOWN"}, max_level=CAUTION))
    results.append(check("3  vehicle moderately close, closing (14->8 m @3 m/s)", [(1, "car", const(0.0), closing(14, 3.0, stop=8.0))], 2.5,
                         {"SLOW DOWN"}, max_level=CAUTION, must_reach=CAUTION))
    results.append(check("4  vehicle very close, rapidly closing (8 m @6 m/s)", [(1, "car", const(0.0), closing(8, 6.0, stop=2.5))], 1.5,
                         {"BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=BRAKE))
    results.append(check("5  pedestrian in ego corridor (12 m, ego 5 m/s)", [(1, "person", const(0.3), closing(12, 5.0, stop=4.0))], 2.5,
                         {"BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=BRAKE))
    results.append(check("6  cow in ego corridor (14 m, ego 4 m/s)", [(1, "cow", const(-0.5), closing(14, 4.0, stop=5.0))], 3,
                         {"SLOW DOWN", "BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=CAUTION))
    results.append(check("7  vehicle in adjacent lane (x=+3.5, 12 m)", [(1, "car", const(3.5), const(12.0))], 4,
                         GO_LABELS, max_level=CAUTION, max_abs_steer=3.0))
    results.append(check("8  road completely blocked (3 stopped cars, ego approaching 3 m/s)",
                         [(1, "car", const(-3.0), closing(12, 3.0, stop=2.5)), (2, "car", const(0.0), closing(12, 3.0, stop=2.5)),
                          (3, "car", const(3.0), closing(12, 3.0, stop=2.5))], 4,
                         {"BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=BRAKE))
    results.append(check("9  obstacle outside corridor (person on footpath x=-4.5)", [(1, "person", const(-4.5), closing(12, 3.0, stop=3.0))], 4,
                         GO_LABELS | {"SLOW DOWN"}, max_level=CAUTION))
    results.append(check("10 both lateral paths blocked + closing car ahead",
                         [(1, "person", const(-2.4), const(6.0)), (2, "person", const(2.4), const(6.0)),
                          (3, "car", const(0.0), closing(10, 3.0, stop=4.0))], 3,
                         {"BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=BRAKE))
    # --- cases A-G
    results.append(check("A  vehicle 4 m ahead, rapidly approaching (5 m/s)", [(1, "car", const(0.0), closing(6, 5.0, stop=1.5))], 1.2,
                         {"BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=BRAKE))
    results.append(check("B  vehicle 4 m ahead, same speed", [(1, "car", const(0.0), const(4.0))], 4,
                         {"SLOW DOWN"}, max_level=CAUTION, must_reach=CAUTION))
    results.append(check("C  vehicle 10 m ahead, rapidly closing (6 m/s)", [(1, "car", const(0.0), closing(10, 6.0, stop=3.0))], 1.5,
                         {"SLOW DOWN", "BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=CAUTION))
    results.append(check("D  vehicle 10 m away in adjacent lane", [(1, "car", const(-3.5), const(10.0))], 4,
                         GO_LABELS, max_level=CAUTION, max_abs_steer=3.0))
    results.append(check("E  pedestrian near road, outside corridor (x=+2.8)", [(1, "person", const(2.8), const(9.0))], 4,
                         {"SLOW DOWN"}, max_level=CAUTION, must_reach=CAUTION))
    results.append(check("F  all candidate trajectories blocked (stopped trucks, ego 1.5 m/s)",
                         [(i, "truck", const(x), closing(7, 1.5, stop=1.5)) for i, x in enumerate([-4.0, -1.5, 1.5, 4.0], start=1)], 4.5,
                         {"NO SAFE PATH - BRAKE", "BRAKE"}, must_reach=BRAKE))
    results.append(check("B2 dense queue moving at ego speed (wall @5 m)",
                         [(i, "truck", const(x), const(5.0)) for i, x in enumerate([-4.0, -1.5, 1.5, 4.0], start=1)], 4,
                         {"SLOW DOWN"}, max_level=CAUTION, must_reach=CAUTION))
    results.append(check("G  road clear", [], 2, GO_LABELS, max_level=GO))
    results.append(check("S1 city following: lead 6 m, same speed, ego 15 km/h", [(1, "car", const(0.0), const(6.0))], 4,
                         GO_LABELS, max_level=GO, ego_speed=4.2))
    results.append(check("S2 fast following: lead 10 m, same speed, ego 50 km/h", [(1, "car", const(0.0), const(10.0))], 4,
                         {"SLOW DOWN"}, max_level=CAUTION, must_reach=CAUTION, ego_speed=13.9))
    results.append(check("S3 queue at signal: lead 3 m, ego stopped", [(1, "car", const(0.0), const(3.0))], 4,
                         {"SLOW DOWN"}, max_level=CAUTION, must_reach=CAUTION, ego_speed=0.0))
    results.append(check("S4 lead 1.5 m inside emergency gap, ego stopped", [(1, "car", const(0.0), const(1.5))], 4,
                         {"BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=BRAKE, ego_speed=0.0))
    results.append(check("P1 pedestrian crossing into lane (x 3.5->0 @1.2 m/s, 9 m)",
                         [(1, "person", lambda t: max(0.0, 3.5 - 1.2 * t), closing(9, 2.0, stop=4.0))], 4,
                         {"BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=BRAKE))
    # --- behaviour cues (behavior.py) + escalation
    results.append(check("CI1 motorcycle cut-in from right (x 3.6->0.3, 9 m)",
                         [(1, "motorcycle", lambda t: max(0.3, 3.6 - 1.4 * t), const(9.0))], 4,
                         {"SLOW DOWN", "BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=CAUTION, ego_speed=8.0,
                         expect_behavior="CUT-IN", expect_title="cut-in"))
    results.append(check("CI2 car cut-in from left, closing (x -3.8->-0.2, 9->4 m)",
                         [(1, "car", lambda t: min(-0.2, -3.8 + 1.6 * t), closing(9, 1.5, stop=4.0))], 4,
                         {"SLOW DOWN", "BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=CAUTION, ego_speed=8.0,
                         expect_behavior="CUT-IN"))
    results.append(check("ON1 oncoming vehicle in ego corridor (x=0.6, 16 m/s closing)",
                         [(1, "car", const(0.6), closing(30, 16.0, stop=2.0))], 2,
                         {"BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=BRAKE, ego_speed=8.0, expect_behavior="ONCOMING"))
    results.append(check("ON2 oncoming vehicle in its own lane (x=-3.6, passes by)",
                         [(1, "car", const(-3.6), lambda t: (30 - 16.0 * t) if t < 1.7 else None)], 3,
                         GO_LABELS, max_level=CAUTION, ego_speed=8.0, forbid_behaviors={"CUT-IN"}))
    results.append(check("YAW ego on a curve: parked cars drift sideways (no CUT-IN)",
                         [(1, "car", lambda t: 3.5 - 0.15 * 8 * t, const(8.0)), (2, "car", lambda t: -3.5 - 0.15 * 12 * t, const(12.0)),
                          (3, "car", lambda t: 4.5 - 0.15 * 16 * t, const(16.0))], 2.5,
                         set(), ego_speed=6.0, forbid_behaviors={"CUT-IN"}))
    results.append(check("ON3 oncoming car drifting inward, fast approach (no CUT-IN)",
                         [(1, "car", lambda t: 3.8 - 0.8 * t, closing(22, 14.0, stop=3.0))], 1.5,
                         set(), ego_speed=7.0, forbid_behaviors={"CUT-IN"}))
    results.append(check("AN1 cow crossing the road (x -4->+4 @1 m/s, 12 m, ego 4 m/s)",
                         [(1, "cow", lambda t: -4.0 + 1.0 * t, closing(12, 4.0, stop=5.0))], 3,
                         {"SLOW DOWN", "BRAKE", "NO SAFE PATH - BRAKE"}, must_reach=CAUTION, ego_speed=4.0,
                         expect_behavior="CROSSING"))
    results.append(check("SL1 stopped vehicle ahead (ego 8 m/s)", [(1, "car", const(0.0), closing(30, 8.0, stop=3.0))], 3.5,
                         {"BRAKE", "NO SAFE PATH - BRAKE", "SLOW DOWN"}, must_reach=BRAKE, ego_speed=8.0, expect_behavior="SLOW LEAD"))
    results.append(check("ADJ harmless adjacent vehicle alongside (x=+3.6, 6 m, same speed)",
                         [(1, "car", const(3.6), const(6.0))], 4, GO_LABELS, max_level=GO, max_abs_steer=3.0, ego_speed=8.0,
                         forbid_behaviors={"CUT-IN", "ONCOMING", "CROSSING"}))
    results.append(check("EDGE vehicle alongside, cut by right frame edge (x=+2.6, 2.5 m)",
                         [(1, "car", const(2.6), const(2.5))], 3, GO_LABELS, max_level=GO, ego_speed=6.0))
    results.append(check("AR1 auto-rickshaw alongside (x=+2.4, 6 m): outside corridor for its width",
                         [(1, "auto-rickshaw", const(2.4), const(6.0))], 3, GO_LABELS, max_level=GO, ego_speed=5.0))
    results.append(auto_rickshaw_real_image_check())
    # rider suppression (detector post-processing unit check)
    moto = {"track_id": 1, "class_name": "motorcycle", "bbox": [600, 400, 680, 520], "confidence": 0.8}
    rider = {"track_id": 2, "class_name": "person", "bbox": [605, 330, 675, 480], "confidence": 0.8}
    walker = {"track_id": 3, "class_name": "person", "bbox": [820, 380, 860, 500], "confidence": 0.8}
    tall_rider = {"track_id": 4, "class_name": "person", "bbox": [1069, 150, 1258, 715], "confidence": 0.8}  # close rider, tall box
    edge_moto = {"track_id": 5, "class_name": "motorcycle", "bbox": [1015, 464, 1280, 716], "confidence": 0.8}
    beside = {"track_id": 6, "class_name": "person", "bbox": [930, 380, 1000, 700], "confidence": 0.8}  # pedestrian next to it
    kept = [o["track_id"] for o in suppress_riders([moto, rider, walker])]
    kept2 = [o["track_id"] for o in suppress_riders([edge_moto, tall_rider, beside])]
    # rider memory: rider #4 merged in frame 1; in frame 2 its motorcycle is not detected -> kept as two-wheeler
    mem, riders = {}, set()
    apply_rider_memory(suppress_riders([edge_moto, tall_rider], riders), mem, 1, riders)
    later = apply_rider_memory([dict(tall_rider)], mem, 2, set())
    ok = kept == [1, 3] and kept2 == [5, 6] and later[0]["class_name"] == "motorcycle"
    print(f"  [{'PASS' if ok else 'FAIL'}] {'RS rider merged into motorcycle, pedestrian kept':52s} kept tracks={kept} / close-edge case {kept2}")
    results.append(ok)
    # --- temporal behaviour
    results.append(check("R  recovery: closing car then disappears",
                         [(1, "car", const(0.0), lambda t: (8 - 6 * t) if t < 0.9 else None)], 5,
                         GO_LABELS, must_reach=BRAKE, final_level={GO}))
    results.append(check("N  noisy lead car hovering at corridor edge (no flicker)",
                         [(1, "car", lambda t: 2.3 + 0.3 * np.sin(40 * t), const(9.0))], 5,
                         set(), max_level=CAUTION, max_transitions=3))
    passed = sum(results)
    print(f"\n{passed}/{len(results)} scenarios passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
