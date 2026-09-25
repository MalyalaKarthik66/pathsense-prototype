"""
Measures live-mode throughput exactly as the web page uses it: JPEG frames (640 px wide) POSTed one at a time to a
running `python app.py` server; each request waits for the previous result (self-throttling, like the browser).

    python app.py                                   # terminal 1
    python live_benchmark.py data/samples/sample_1.mp4   # terminal 2 -> outputs/live_benchmark.json

Reported FPS / latency are measured on this machine; phone-over-Wi-Fi adds network time on top.
"""

import argparse
import json
import os
import statistics
import time

import cv2
import requests


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--server", default="http://127.0.0.1:8000")  # 127.0.0.1, not localhost (IPv6 fallback delay on Windows)
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--stride", type=int, default=3, help="use every Nth video frame (~phone camera cadence)")
    a = ap.parse_args()

    se = requests.Session()
    t = time.time()
    session = se.post(a.server + "/api/live/start", timeout=300).json()["session"]
    warmup_s = time.time() - t
    cap = cv2.VideoCapture(a.video)
    rtt, proc, labels, n, i = [], [], {}, 0, 0
    size = None
    t0 = None
    while n < a.frames:
        ok, fr = cap.read()
        if not ok:
            break
        i += 1
        if i % a.stride:
            continue
        fr = cv2.resize(fr, (640, int(round(fr.shape[0] * 640 / fr.shape[1]))))
        ok, jpg = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 75])
        s = time.time()
        r = se.post(f"{a.server}/api/live/frame?session={session}", data=jpg.tobytes(),
                    headers={"Content-Type": "image/jpeg"}, timeout=60)
        if r.status_code != 200:
            print("server:", r.status_code, r.text[:200])
            time.sleep(1)
            continue
        if t0 is None:
            t0 = s  # first frame includes CUDA warm-up; measure from its start
        j = r.json()
        rtt.append((time.time() - s) * 1000)
        proc.append(j["perf"]["processing_ms"])
        labels[j["decision"]["label"]] = labels.get(j["decision"]["label"], 0) + 1
        size = (j["width"], j["height"])
        n += 1
    elapsed = time.time() - t0
    se.post(a.server + "/api/live/stop")
    res = {"fps": round((n - 1) / elapsed, 1) if n > 1 else None, "frames": n,
           "rtt_median_ms": round(statistics.median(rtt)), "rtt_p90_ms": round(sorted(rtt)[int(0.9 * (len(rtt) - 1))]),
           "processing_median_ms": round(statistics.median(proc)), "width": size[0], "height": size[1],
           "model_load_s": round(warmup_s, 1), "labels": labels, "video": os.path.basename(a.video),
           "where": "same PC (127.0.0.1)", "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        import torch
        res["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    except Exception:
        pass
    os.makedirs("outputs", exist_ok=True)
    with open(os.path.join("outputs", "live_benchmark.json"), "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
