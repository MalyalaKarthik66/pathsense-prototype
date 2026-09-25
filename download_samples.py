"""
PathSense - Sample Video Download and Preparation Script
Downloads open driving dashcam clips and prepares 10-20s sample clips in data/samples/
"""

import os
import sys
import urllib.request
import cv2

SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "data", "samples")

# High-reliability sample video sources
SAMPLE_URLS = {
    "project_video.mp4": "https://raw.githubusercontent.com/udacity/CarND-Advanced-Lane-Lines/master/project_video.mp4",
    "challenge_video.mp4": "https://raw.githubusercontent.com/udacity/CarND-Advanced-Lane-Lines/master/challenge_video.mp4"
}


def reporthook(block_num, block_size, total_size):
    downloaded = block_num * block_size
    if total_size > 0:
        percent = min(100.0, (downloaded / total_size) * 100.0)
        sys.stdout.write(f"\rDownloading: {percent:5.1f}% ({downloaded / (1024*1024):.1f}MB / {total_size / (1024*1024):.1f}MB)")
    else:
        sys.stdout.write(f"\rDownloading: {downloaded / (1024*1024):.1f}MB")
    sys.stdout.flush()


def trim_clip(input_path: str, output_path: str, start_sec: float = 0.0, duration_sec: float = 15.0):
    """
    Extracts a subclip of duration_sec starting from start_sec using OpenCV.
    Ensures standardized FPS and clean MP4 encoding.
    """
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise IOError(f"Failed to open video {input_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    start_frame = int(start_sec * fps)
    max_frames = int(duration_sec * fps)

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

    print(f"\nTrimming {os.path.basename(input_path)} -> {os.path.basename(output_path)}")
    print(f"  Start: {start_sec:.1f}s (frame {start_frame}), Duration: {duration_sec:.1f}s ({max_frames} frames)")
    print(f"  Resolution: {width}x{height}, FPS: {fps:.2f}")

    written = 0
    while cap.isOpened() and written < max_frames:
        ret, frame = cap.read()
        if not ret:
            break
        out.write(frame)
        written += 1

    cap.release()
    out.release()
    print(f"  Successfully wrote {written} frames ({written / fps:.1f}s) to {output_path}")


def prepare_samples():
    os.makedirs(SAMPLES_DIR, exist_ok=True)
    raw_dir = os.path.join(SAMPLES_DIR, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    # 1. Download sample 1 source
    raw_sample1 = os.path.join(raw_dir, "project_video.mp4")
    if not os.path.exists(raw_sample1) or os.path.getsize(raw_sample1) < 1000000:
        print(f"Downloading primary sample source from {SAMPLE_URLS['project_video.mp4']}...")
        urllib.request.urlretrieve(SAMPLE_URLS["project_video.mp4"], raw_sample1, reporthook)
        print("\nDownload complete.")

    sample_1_path = os.path.join(SAMPLES_DIR, "sample_1.mp4")
    # Extract 12 seconds with vehicle interactions (frames 15s to 27s have vehicles changing lanes and passing)
    trim_clip(raw_sample1, sample_1_path, start_sec=20.0, duration_sec=12.0)

    # 2. Extract a second sample (curved road / different traffic condition)
    sample_2_path = os.path.join(SAMPLES_DIR, "sample_2.mp4")
    trim_clip(raw_sample1, sample_2_path, start_sec=35.0, duration_sec=10.0)

    print("\nSample preparation complete.")
    print(f"  Sample 1: {sample_1_path} ({os.path.getsize(sample_1_path)/(1024*1024):.2f} MB)")
    print(f"  Sample 2: {sample_2_path} ({os.path.getsize(sample_2_path)/(1024*1024):.2f} MB)")


# Indian validation clips: all from Wikimedia Commons under CC0 / CC BY / CC BY-SA (attribution listed).
# (file name on Commons, local path, licence, author, what it is used for)
INDIAN_CLIPS = [
    ("Bangalore 20220814 155101.webm", "samples/india_bangalore.webm", "CC0", "L. Shyamal", "Bangalore Nandidurga Rd: dense mixed traffic, autos, signal queue"),
    ("New BEL road 20220726 111234.webm", "samples/india_newbel.webm", "CC0", "L. Shyamal", "Bangalore New BEL Rd: narrow road, pedestrians, two-wheelers"),
    ("Bangalore 20220814 154815.webm", "samples/india_cvraman.webm", "CC0", "L. Shyamal", "Bangalore C V Raman Rd: arterial, cut-ins"),
    ("Kadur Chikmagalur road 20210731.webm", "candidates/ka_kadur.webm", "CC0", "L. Shyamal", "Karnataka rural highway, curves"),
    ("Anamalai road VID20180327151309.webm", "candidates/tn_anamalai.webm", "CC BY-SA 3.0", "T. R. Shankar Raman", "Tamil Nadu narrow rural road, oncoming"),
    ("IISc drive 2024.webm", "candidates/blr_iisc.webm", "CC0", "L. Shyamal", "Bangalore campus road, two-wheelers"),
    ("Traffic in Hyderabad.webm", "candidates/hyd_traffic.webm", "CC BY-SA 4.0", "Oleg Yunakov", "Hyderabad signal: dense two-wheelers/autos (rider view)"),
    ("Stray Cattle in Lutyens Delhi.webm", "candidates/delhi_cattle.webm", "CC BY-SA 3.0", "Fowler&fowler", "Delhi: cattle crossing at a traffic island"),
    ("Ladakh Road Trip july 2017 - River crossing - Rohtang Pass Manali India Deadliest Road Drive.webm",
     "candidates/hp_rohtang.webm", "CC BY 3.0", "KSOFTECH", "Himachal unpaved mountain road (12:00-14:30 segment used)"),
]


def download_indian_clips():
    """Downloads the Indian validation clips (720p transcodes where available) from Wikimedia Commons."""
    import hashlib, time
    import urllib.parse
    import requests
    headers = {"User-Agent": "PathSense-SIH-prototype (github.com/MalyalaKarthik66/pathsense-prototype)"}
    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    for name, rel, lic, author, use in INDIAN_CLIPS:
        out = os.path.join(base_dir, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        print(f"\n{rel}  <-  commons.wikimedia.org/wiki/File:{name.replace(' ', '_')}\n  {lic}, {author}: {use}")
        if os.path.exists(out) and os.path.getsize(out) > 1_000_000:
            print("  already present"); continue
        fn = name.replace(" ", "_"); h = hashlib.md5(fn.encode()).hexdigest(); q = urllib.parse.quote(fn)
        urls = [f"https://upload.wikimedia.org/wikipedia/commons/transcoded/{h[0]}/{h[:2]}/{q}/{q}.720p.vp9.webm",
                f"https://upload.wikimedia.org/wikipedia/commons/{h[0]}/{h[:2]}/{q}"]
        for url in urls:
            r = None
            for _ in range(5):
                r = requests.get(url, headers=headers, stream=True, timeout=120)
                if r.status_code != 429:
                    break
                time.sleep(30)  # Commons rate limit
            if r is None or r.status_code != 200:
                continue
            with open(out, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            print(f"  saved {os.path.getsize(out) / 2**20:.1f} MB")
            break
        else:
            print("  FAILED to download")
        time.sleep(5)
    rohtang = os.path.join(base_dir, "candidates", "hp_rohtang.webm")
    seg = os.path.join(base_dir, "candidates", "hp_rohtang_seg.mp4")
    if os.path.exists(rohtang) and not os.path.exists(seg):
        trim_clip(rohtang, seg, start_sec=12 * 60, duration_sec=150)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Download PathSense sample / validation clips")
    ap.add_argument("--indian", action="store_true", help="Also download the CC-licensed Indian validation clips from Wikimedia Commons")
    ap.add_argument("--indian-only", action="store_true", help="Only download the Indian validation clips")
    a = ap.parse_args()
    if not a.indian_only:
        prepare_samples()
    if a.indian or a.indian_only:
        download_indian_clips()
