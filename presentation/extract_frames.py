"""
Extracts the real prototype frames used on the PROTOTYPE slide from the processed demo videos.

    .venv/Scripts/python.exe presentation/extract_frames.py

Each processed frame is cropped to the camera view (the HUD's top status bar, which carries the frame counter and
processing rate, and the bottom telemetry strip are cut away); detection boxes, labels and the candidate-trajectory
inset stay exactly as the pipeline drew them. The "before" frame is the same instant from the unprocessed input video.
Output: presentation/assets/frames/*.jpg
"""
import os

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "presentation", "assets", "frames")
os.makedirs(OUT, exist_ok=True)
TOP, BOTTOM = 62, 606            # camera-view rows of the 1280x720 HUD render

# (name, video, frame) - frames chosen from the events log (decision at that instant in brackets)
PROCESSED = [
    ("seq1_go", "outputs/india_newbel_pathsense.mp4", 1365),         # GO STRAIGHT: oncoming car stays on its own side
    ("seq2_slow", "outputs/india_newbel_pathsense.mp4", 1440),       # SLOW DOWN: oncoming vehicle entering the path
    ("seq3_brake", "outputs/india_newbel_pathsense.mp4", 1470),      # BRAKE: pedestrian in the ego path
    ("after_mixed", "outputs/india_bangalore_pathsense.mp4", 180),   # GO STRAIGHT: auto-rickshaw + car, path clear
]
RAW = [("before_mixed", "data/samples/india_bangalore.webm", 180)]


def grab(path, idx):
    cap = cv2.VideoCapture(os.path.join(ROOT, path))
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, im = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"could not read frame {idx} of {path}")
    return im


for name, path, idx in PROCESSED:
    im = grab(path, idx)
    cv2.imwrite(os.path.join(OUT, f"{name}.jpg"), im[TOP:BOTTOM], [cv2.IMWRITE_JPEG_QUALITY, 92])
    print("wrote", name)
for name, path, idx in RAW:
    im = cv2.resize(grab(path, idx), (1280, 720), interpolation=cv2.INTER_AREA)
    cv2.imwrite(os.path.join(OUT, f"{name}.jpg"), im[TOP:BOTTOM], [cv2.IMWRITE_JPEG_QUALITY, 92])
    print("wrote", name)
