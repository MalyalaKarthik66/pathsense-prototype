"""PathSense - labelled demo reel builder.

Builds outputs/pathsense_showcase.mp4 from already rendered outputs/<clip>_pathsense.mp4 files.
Every segment is preceded by a title card (source clip, time range, what it shows) and carries a small caption,
so the reel is explicitly an edited selection of unmodified pipeline output."""
import os, sys, json
import cv2
import numpy as np

REPO = os.path.dirname(os.path.abspath(__file__))
W, H, FPS = 1280, 720, 30.0


def card(lines, secs=2.0):
    img = np.full((H, W, 3), 18, np.uint8)
    cv2.putText(img, lines[0], (60, 260), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 215, 255), 2, cv2.LINE_AA)
    import textwrap
    wrapped = [w for ln in lines[1:] for w in textwrap.wrap(ln, 68)]
    for k, ln in enumerate(wrapped):
        cv2.putText(img, ln, (60, 320 + 40 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (230, 230, 230), 1, cv2.LINE_AA)
    return [img] * int(secs * FPS)


def segment(clip, t0, t1, caption):
    cap = cv2.VideoCapture(f"{REPO}/outputs/{clip}_pathsense.mp4")
    fps = cap.get(cv2.CAP_PROP_FPS) or FPS
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t0 * fps))
    frames, n, step = [], int((t1 - t0) * fps), fps / FPS
    acc = 0.0
    for _ in range(n):
        ok, f = cap.read()
        if not ok:
            break
        acc += 1.0
        while acc >= step:  # resample to 30 FPS output
            g = cv2.resize(f, (W, H))
            cv2.rectangle(g, (0, 72), (W, 96), (0, 0, 0), -1)
            cv2.putText(g, caption, (12, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 215, 255), 1, cv2.LINE_AA)
            frames.append(g); acc -= step
    return frames


DEFAULT_PLAN = [
 {
  "clip": "india_bangalore",
  "t0": 0,
  "t1": 8,
  "title": "Pedestrian at road edge: BRAKE -> recovery",
  "what": "BRAKE held 1 s, 'Clearing after BRAKE', back to GO; planner steers right around the pedestrian"
 },
 {
  "clip": "india_newbel",
  "t0": 45,
  "t1": 50.5,
  "title": "Oncoming vehicle - path conflict",
  "what": "ONCOMING cue, BRAKE, all arcs blocked -> NO SAFE PATH (PATH BLOCKED), recovery"
 },
 {
  "clip": "india_cvraman",
  "t0": 3,
  "t1": 8.5,
  "title": "Slow vehicle ahead -> collision risk",
  "what": "SLOW DOWN (slow vehicle ahead) escalates to BRAKE at TTC < 1 s, then clears"
 },
 {
  "clip": "india_bangalore",
  "t0": 57,
  "t1": 62,
  "title": "Pedestrians at the road edge",
  "what": "SLOW DOWN 'Pedestrian near path' - no BRAKE for people outside the corridor"
 },
 {
  "clip": "india_bangalore",
  "t0": 74,
  "t1": 82,
  "title": "Dense mixed traffic, autos, signal queue",
  "what": "auto (est.) labels, 'Following closely', CUT-IN cue, BRAKE for a closing car"
 },
 {
  "clip": "india_newbel",
  "t0": 71,
  "t1": 74.5,
  "title": "Candidate trajectory: STEER LEFT",
  "what": "planner selects an offset arc around obstacle cost (BEV panel)"
 },
 {
  "clip": "delhi_cattle",
  "t0": 9,
  "t1": 13,
  "title": "Cattle on the road (Delhi)",
  "what": "animals are lethal-class obstacles: NO SAFE PATH while they block every arc"
 },
 {
  "clip": "ka_kadur",
  "t0": 10,
  "t1": 16,
  "title": "Rural highway, clear road",
  "what": "GO STRAIGHT / PATH CLEAR - no false alarms"
 }
]


if __name__ == "__main__":
    # optional custom plan: python showcase.py plan.json  ([{"clip","t0","t1","title","what"}, ...])
    plan = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else DEFAULT_PLAN
    out = cv2.VideoWriter(f"{REPO}/outputs/pathsense_showcase.mp4", cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for f in card(["PathSense - SIH PS 26037 demo reel",
                   "Camera-only research/hackathon prototype: candidate trajectory + decision recommendation",
                   "Edited selection of UNMODIFIED pipeline output; each segment is labelled with its source clip and time",
                   "Distances / TTC are monocular estimates, not metric ground truth"], 4.0):
        out.write(f)
    total = 4.0
    for s in plan:
        for f in card([s["title"], f"source: {s['clip']}  {s['t0']:.0f}-{s['t1']:.0f} s", s["what"]]):
            out.write(f)
        fr = segment(s["clip"], s["t0"], s["t1"], f"{s['clip']} {s['t0']:.0f}-{s['t1']:.0f}s | {s['title']}")
        for f in fr:
            out.write(f)
        total += 2.0 + len(fr) / FPS
    out.release()
    print(f"showcase: outputs/pathsense_showcase.mp4 ({total:.0f} s)")
