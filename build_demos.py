"""
Builds the built-in web demos (demos/) from rendered pipeline outputs.

    .venv/Scripts/python.exe build_demos.py          # needs outputs/<clip>_pathsense.mp4 + JSONs, and imageio-ffmpeg

The web app must show demos on a fresh deployment (Render) where outputs/ does not exist, so the curated SIH drives are
committed under demos/<clip>/. Each video is the UNMODIFIED pipeline render re-encoded for the web (H.264, 960x540,
same frame count and frame rate, +faststart), so the per-frame telemetry and events still line up with playback.
The events / frames / stats / timeline files are copied as produced by the pipeline - nothing is edited.
Footage: CC0, L. Shyamal (Wikimedia Commons), see download_samples.py.
"""
import json
import os
import shutil
import subprocess

import cv2
import imageio_ffmpeg

ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUTS = os.path.join(ROOT, "outputs")
DEMOS = os.path.join(ROOT, "demos")

# the drives used in the SIH showcase reel (showcase.py) plus the IISc validation drive; poster = a representative second
CATALOG = [
    {"clip": "india_bangalore", "title": "Dense mixed traffic",
     "place": "Nandidurga Rd, Bengaluru",
     "desc": "Autos, two-wheelers and a signal queue. Pedestrians beside the corridor do not trigger BRAKE; "
             "a pedestrian in the path does.", "poster_s": 76},
    {"clip": "india_newbel", "title": "Narrow road, oncoming traffic",
     "place": "New BEL Rd, Bengaluru",
     "desc": "Oncoming vehicles on their own side keep the path clear; one entering the path escalates to "
             "SLOW DOWN and BRAKE, and the planner picks an offset trajectory.", "poster_s": 48},
    {"clip": "india_cvraman", "title": "Arterial road, slow vehicle ahead",
     "place": "C V Raman Rd, Bengaluru",
     "desc": "A slow vehicle ahead raises SLOW DOWN, escalates briefly to BRAKE, then clears; cut-ins on an arterial.",
     "poster_s": 5},
    {"clip": "blr_iisc", "title": "Pedestrians on the carriageway",
     "place": "IISc campus, Bengaluru",
     "desc": "People walking on the road itself and two-wheelers weaving through: the decision follows who is in "
             "the ego path.", "poster_s": 30},
    {"clip": "ka_kadur", "title": "Rural highway, clear road",
     "place": "Kadur - Chikmagalur, Karnataka",
     "desc": "Curving two-lane highway with nothing in the path: GO STRAIGHT throughout, no false alarms.",
     "poster_s": 12},
]
COPY = ["_events.json", "_frames.json", "_stats.json", "_timeline.png"]


def main():
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    os.makedirs(DEMOS, exist_ok=True)
    out_catalog = []
    for c in CATALOG:
        clip = c["clip"]
        src = os.path.join(OUTPUTS, f"{clip}_pathsense.mp4")
        if not os.path.exists(src):
            raise SystemExit(f"missing {src} - render it first with main.py")
        d = os.path.join(DEMOS, clip)
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, f"{clip}_pathsense.mp4")
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", src, "-an", "-vf", "scale=960:540:flags=lanczos",
                        "-c:v", "libx264", "-preset", "slow", "-crf", "28", "-pix_fmt", "yuv420p",
                        "-profile:v", "high", "-movflags", "+faststart", dst], check=True)
        for suf in COPY:
            s = os.path.join(OUTPUTS, clip + suf)
            if os.path.exists(s):
                shutil.copyfile(s, os.path.join(d, clip + suf))
        cap = cv2.VideoCapture(dst)
        cap.set(cv2.CAP_PROP_POS_MSEC, c["poster_s"] * 1000)
        ok, im = cap.read()
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        if ok:
            cv2.imwrite(os.path.join(d, "poster.jpg"), cv2.resize(im, (640, 360), interpolation=cv2.INTER_AREA),
                        [cv2.IMWRITE_JPEG_QUALITY, 82])
        out_catalog.append({k: v for k, v in c.items() if k != "poster_s"})
        print(f"{clip}: {os.path.getsize(dst) / 1e6:.1f} MB, {n} frames")
    with open(os.path.join(DEMOS, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump({"source": "CC0 footage by L. Shyamal (Wikimedia Commons), processed by the PathSense pipeline",
                   "demos": out_catalog}, f, indent=1)


if __name__ == "__main__":
    main()
