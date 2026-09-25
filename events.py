"""
PathSense - Event timeline, replay and audit trail
Module: events.py

During a pipeline run, EventLogger records every change of the bottom-right decision (GO STRAIGHT, STEER LEFT/RIGHT,
SLOW DOWN, BRAKE, NO SAFE PATH) and every confirmed behaviour cue (CUT-IN, CROSSING, ONCOMING, SLOW LEAD), together
with the reason, responsible object (track id, class, distance, TTC, behaviour), steering, ego speed and path status.
It is saved as outputs/<video>_events.json and drawn as outputs/<video>_timeline.png.

CLI (answers "why did the system brake here?"):
    python events.py list     outputs/<video>_events.json [--type decision|behavior] [--label BRAKE]
    python events.py show     outputs/<video>_events.json <event_id> [--video outputs/<video>_pathsense.mp4]
    python events.py timeline outputs/<video>_events.json [--out timeline.png]
`show` prints the event card and exports a +/-3 s clip and an annotated still from the rendered HUD video.
"""

import os
import sys
import json
import argparse
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

LABEL_COLORS = {  # matplotlib RGB
    "GO STRAIGHT": "#2ecc71", "STEER LEFT": "#1abc9c", "STEER RIGHT": "#16a085",
    "SLOW DOWN": "#f39c12", "BRAKE": "#e74c3c", "NO SAFE PATH - BRAKE": "#8e0000",
}


def _r(v, nd=2):
    return None if v is None else round(float(v), nd)


class EventLogger:
    def __init__(self, video: str, fps: float, min_segment_s: float = 0.0):
        self.video, self.fps = video, fps
        self.events: List[Dict[str, Any]] = []
        self.segments: List[Dict[str, Any]] = []
        self._cur: Optional[Dict[str, Any]] = None

    def _base(self, frame_idx: int, etype: str) -> Dict[str, Any]:
        return {"id": len(self.events), "type": etype, "frame": int(frame_idx), "time_s": round(frame_idx / self.fps, 2)}

    def log_frame(self, frame_idx: int, decision: Dict[str, Any], steering_deg: float, ego_speed_kmh: float):
        """Call once per frame with the DecisionState output; records an event when the displayed decision changes."""
        label = decision["label"]
        if self._cur is not None and self._cur["label"] == label:
            self._cur["end_frame"] = int(frame_idx)
            return
        if self._cur is not None:
            self._close(frame_idx - 1)
        key = decision.get("key") or {}
        ev = self._base(frame_idx, "decision")
        ev.update({
            "decision": label, "state": decision["state"], "title": decision.get("title", ""),
            "reason": decision.get("reason", ""), "path_status": decision.get("path_status"),
            "blocked_arcs": decision.get("blocked_arcs"), "n_arcs": decision.get("n_arcs"),
            "threats": decision.get("threats"), "steering_deg": _r(steering_deg, 1), "ego_speed_kmh": _r(ego_speed_kmh, 1),
            "object": {"track_id": key.get("track_id"), "class_name": key.get("class_name"),
                       "distance_m": _r(key.get("distance_m")), "lateral_m": _r(key.get("lateral_m")),
                       "ttc_s": _r(key.get("ttc_s")), "behavior": key.get("behavior")} if key else None,
        })
        self.events.append(ev)
        self._cur = {"label": label, "start_frame": int(frame_idx), "end_frame": int(frame_idx), "event_id": ev["id"]}

    def log_behavior(self, frame_idx: int, track_id: int, behavior: str, obj: Dict[str, Any], ego_speed_kmh: float):
        ev = self._base(frame_idx, "behavior")
        ev.update({"behavior": behavior, "ego_speed_kmh": _r(ego_speed_kmh, 1),
                   "object": {"track_id": track_id, "class_name": obj.get("class_name"), "distance_m": _r(obj.get("bev_y_m")),
                              "lateral_m": _r(obj.get("bev_x_m")), "ttc_s": _r(obj.get("ttc_sec")),
                              "closing_speed_mps": _r(obj.get("closing_speed_mps"))}})
        self.events.append(ev)

    def _close(self, end_frame: int):
        c = self._cur
        c["end_frame"] = int(end_frame)
        dur = round((c["end_frame"] - c["start_frame"] + 1) / self.fps, 2)
        self.events[c["event_id"]]["duration_s"] = dur
        self.segments.append({"decision": c["label"], "start_frame": c["start_frame"], "end_frame": c["end_frame"],
                              "start_s": round(c["start_frame"] / self.fps, 2), "duration_s": dur, "event_id": c["event_id"]})

    def finish(self, last_frame: int) -> Dict[str, Any]:
        if self._cur is not None:
            self._close(last_frame)
            self._cur = None
        return {"video": self.video, "fps": self.fps, "frames": int(last_frame) + 1,
                "events": self.events, "segments": self.segments}

    def save(self, path: str, last_frame: int, rendered_video: Optional[str] = None) -> Dict[str, Any]:
        data = self.finish(last_frame)
        data["rendered_video"] = rendered_video
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=1)
        return data


# ---------------------------------------------------------------------------------------------- CLI helpers
def event_card(ev: Dict[str, Any]) -> str:
    o = ev.get("object") or {}
    lines = [f"Event #{ev['id']} ({ev['type']})",
             f"  Frame:     {ev['frame']}  (t = {ev['time_s']:.2f} s)"]
    if ev["type"] == "decision":
        lines += [f"  Decision:  {ev['decision']}" + (f"  (lasted {ev['duration_s']} s)" if "duration_s" in ev else ""),
                  f"  Reason:    {ev.get('title') or '-'}  |  {ev.get('reason') or '-'}",
                  f"  Path:      {ev.get('path_status')} ({ev.get('blocked_arcs')}/{ev.get('n_arcs')} candidate arcs blocked), "
                  f"threats: {ev.get('threats')}",
                  f"  Steering:  {ev.get('steering_deg')} deg   Ego speed: {ev.get('ego_speed_kmh')} km/h (monocular estimate)"]
    else:
        lines += [f"  Behaviour: {ev['behavior']}", f"  Ego speed: {ev.get('ego_speed_kmh')} km/h (monocular estimate)"]
    if o:
        ttc = f"{o['ttc_s']} s" if o.get("ttc_s") is not None else "N/A"
        lines.append(f"  Object:    {o.get('class_name')} #{o.get('track_id')}  distance {o.get('distance_m')} m, "
                     f"lateral {o.get('lateral_m')} m, TTC {ttc}" + (f", behaviour {o['behavior']}" if o.get("behavior") else ""))
    else:
        lines.append("  Object:    -")
    return "\n".join(lines)


def draw_timeline(data: Dict[str, Any], out_png: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fps, n = data["fps"], data["frames"]
    fig, ax = plt.subplots(figsize=(14, 2.8), dpi=120)
    for s in data["segments"]:
        ax.barh(1, (s["end_frame"] - s["start_frame"] + 1) / fps, left=s["start_frame"] / fps, height=0.6,
                color=LABEL_COLORS.get(s["decision"], "#95a5a6"), edgecolor="none")
    for e in data["events"]:
        t = e["time_s"]
        if e["type"] == "behavior":
            ax.plot(t, 0.25, marker="v", color="#34495e", markersize=7)
            ax.text(t, 0.02, f"{e['behavior']}", rotation=90, fontsize=6, ha="center", va="bottom", color="#34495e")
        elif e["decision"] in ("BRAKE", "NO SAFE PATH - BRAKE"):
            ax.text(t, 1.38, f"#{e['id']}", fontsize=7, ha="center", color="#8e0000")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in LABEL_COLORS.values()]
    ax.legend(handles, LABEL_COLORS.keys(), loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=6, fontsize=8, frameon=False)
    ax.set_xlim(0, n / fps); ax.set_ylim(-0.05, 1.6); ax.set_yticks([])
    ax.set_xlabel("time (s)   |   bar = decision, #id = BRAKE event id, triangles = behaviour cues")
    ax.set_title(f"PathSense decision timeline - {os.path.basename(data['video'])}", fontsize=10)
    plt.tight_layout(); plt.savefig(out_png); plt.close(fig)


def replay(data: Dict[str, Any], event_id: int, video: Optional[str], out_dir: str = "outputs", pad_s: float = 3.0) -> str:
    ev = next((e for e in data["events"] if e["id"] == event_id), None)
    if ev is None:
        raise SystemExit(f"No event #{event_id} (valid ids: 0..{len(data['events']) - 1})")
    card = event_card(ev)
    print(card)
    if not video or not os.path.isfile(video):
        print(f"\n(rendered video not found: {video}; pass --video to export a clip)")
        return card
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or data["fps"]
    start, end = max(0, int(ev["frame"] - pad_s * fps)), int(ev["frame"] + pad_s * fps)
    stem = os.path.splitext(os.path.basename(video))[0]
    os.makedirs(out_dir, exist_ok=True)
    clip_path = os.path.join(out_dir, f"{stem}_event{event_id}.mp4")
    still_path = os.path.join(out_dir, f"{stem}_event{event_id}.jpg")
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    writer, i = None, start
    while i <= end:
        ok, frame = cap.read()
        if not ok:
            break
        if writer is None:
            writer = cv2.VideoWriter(clip_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (frame.shape[1], frame.shape[0]))
        if i == ev["frame"]:
            still = frame.copy()
            lines = card.splitlines()
            box_h = 22 * len(lines) + 16
            cv2.rectangle(still, (10, 80), (min(still.shape[1] - 10, 900), 80 + box_h), (15, 15, 15), -1)
            for k, ln in enumerate(lines):
                cv2.putText(still, ln, (20, 104 + 22 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.imwrite(still_path, still)
        writer.write(frame)
        i += 1
    cap.release()
    if writer is not None:
        writer.release()
    print(f"\nClip:  {clip_path}  (+/-{pad_s:.0f} s around frame {ev['frame']})\nStill: {still_path}")
    return card


def main(argv=None):
    ap = argparse.ArgumentParser(description="PathSense event timeline / replay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("events_json"); p.add_argument("--type"); p.add_argument("--label")
    p = sub.add_parser("show"); p.add_argument("events_json"); p.add_argument("event_id", type=int); p.add_argument("--video")
    p = sub.add_parser("timeline"); p.add_argument("events_json"); p.add_argument("--out")
    a = ap.parse_args(argv)
    data = json.load(open(a.events_json))
    if a.cmd == "list":
        for e in data["events"]:
            if a.type and e["type"] != a.type:
                continue
            if a.label and e.get("decision") != a.label and e.get("behavior") != a.label:
                continue
            o = e.get("object") or {}
            what = e.get("decision") or e.get("behavior")
            obj = f"{o.get('class_name')} #{o.get('track_id')} {o.get('distance_m')} m TTC {o.get('ttc_s')}" if o else "-"
            dur = f"{e['duration_s']:5.1f}s" if "duration_s" in e else "      "
            print(f"#{e['id']:4d}  t={e['time_s']:7.2f}s  f={e['frame']:5d}  {dur}  {what:22s}  {e.get('title', ''):34s}  {obj}")
    elif a.cmd == "show":
        replay(data, a.event_id, a.video or data.get("rendered_video"))
    else:
        out = a.out or os.path.splitext(a.events_json)[0].replace("_events", "") + "_timeline.png"
        draw_timeline(data, out)
        print(f"Timeline: {out}")


if __name__ == "__main__":
    main()
