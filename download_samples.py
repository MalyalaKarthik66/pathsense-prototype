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


if __name__ == "__main__":
    prepare_samples()
