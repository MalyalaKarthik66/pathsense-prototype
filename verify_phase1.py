"""
PathSense - Phase 1 Verification Script
Verifies:
1. All core dependencies (torch, torchvision, cv2, ultralytics, numpy, scipy, matplotlib)
2. Sample video clips in data/samples/ (playability, frame count, resolution, FPS)
"""

import os
import sys

def verify():
    print("=" * 60)
    print("PathSense Phase 1 Verification")
    print("=" * 60)

    # 1. Verify Imports
    print("\n[1/2] Verifying Core Dependencies...")
    import torch
    import torchvision
    import cv2
    import numpy as np
    import matplotlib
    import scipy
    import ultralytics

    print(f"  PyTorch:     {torch.__version__} (CUDA available: {torch.cuda.is_available()})")
    print(f"  Torchvision: {torchvision.__version__}")
    print(f"  OpenCV:      {cv2.__version__}")
    print(f"  NumPy:       {np.__version__}")
    print(f"  Ultralytics: {ultralytics.__version__}")
    print(f"  Matplotlib:  {matplotlib.__version__}")
    print(f"  SciPy:       {scipy.__version__}")
    print("  --> All dependencies imported successfully!")

    # 2. Verify Sample Video Clips
    print("\n[2/2] Verifying Sample Video Clips in data/samples/...")
    samples_dir = os.path.join(os.path.dirname(__file__), "data", "samples")
    sample_files = ["sample_1.mp4", "sample_2.mp4"]

    for sample_name in sample_files:
        sample_path = os.path.join(samples_dir, sample_name)
        if not os.path.exists(sample_path):
            print(f"  ERROR: {sample_name} not found at {sample_path}")
            sys.exit(1)

        size_mb = os.path.getsize(sample_path) / (1024 * 1024)
        cap = cv2.VideoCapture(sample_path)
        if not cap.isOpened():
            print(f"  ERROR: OpenCV could not open {sample_name}")
            sys.exit(1)

        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0

        # Read test frames
        frames_read = 0
        for _ in range(min(15, total_frames)):
            ret, frame = cap.read()
            if ret and frame is not None:
                frames_read += 1
            else:
                break
        cap.release()

        print(f"\n  File: {sample_name}")
        print(f"    Path:         {sample_path}")
        print(f"    File Size:    {size_mb:.2f} MB")
        print(f"    Resolution:   {width}x{height}")
        print(f"    FPS:          {fps:.2f}")
        print(f"    Total Frames: {total_frames} ({duration:.2f} seconds)")
        print(f"    Decode Test:  {frames_read}/15 sample frames decoded cleanly")

        if frames_read == 0:
            print(f"  ERROR: Could not decode frames from {sample_name}")
            sys.exit(1)

    print("\n" + "=" * 60)
    print("PHASE 1 VERIFICATION PASSED: All dependencies & sample clips verified!")
    print("=" * 60)

if __name__ == "__main__":
    verify()
