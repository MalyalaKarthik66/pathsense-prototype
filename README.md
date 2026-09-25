# PathSense: Adaptive Path Planning & Collision Avoidance for Autonomous Vehicles

> **Smart India Hackathon Problem Statement 26037**  
> *"Adaptive Path Planning and Collision Avoidance for Autonomous Vehicles on Unstructured Indian Roads."*  
> **Project Codename**: `PathSense`

---

## 1. System Architecture

PathSense is an end-to-end monocular perception and trajectory planning prototype built entirely with off-the-shelf pretrained deep networks and classical geometric computer vision. It requires **no external CAN-bus, GPS, or LiDAR sensors** — all spatial, temporal, and motion states are inferred strictly from a single front-facing dashcam video.

```
                              [Front-Facing Dashcam Video]
                                           │
             ┌─────────────────────────────┼─────────────────────────────┐
             ▼                             ▼                             ▼
    [Phase 2: YOLOv8 + ByteTrack]  [Phase 3: Depth Anything V2]   [Phase 5: Road Optical Flow]
     Detect & Track Road Users      Dense Relative Disparity       Stationary Road Features
             │                             │                             │
             └──────────────┬──────────────┘                             ▼
                            ▼                                   [Ego-Motion Odometry]
                  [Phase 4: TTC & Velocity]                      Forward Speed & Position
                   EMA Multi-Frame Deriv.                                │
                            │                                            │
                            ▼                                            │
                  [Phase 6: BEV Costmap]                                 │
                   Vulnerability Hierarchy                               │
                   Gaussian Safety Halos                                 │
                            │                                            │
                            ▼                                            │
                  [Phase 7: Dynamic Planner]                             │
                   Arc Rollout & Scoring                                 │
                   Bicycle Model Steering                                │
                            │                                            │
                            └──────────────────────┬─────────────────────┘
                                                   ▼
                                      [Phase 8: Cockpit HUD Overlay]
                                        PiP BEV + Digital Gauges
```

---

## 2. Repository Structure

```
pathsense-prototype/
├── data/
│   └── samples/                     # created by download_samples.py (not committed)
│       ├── sample_1.mp4             # Udacity project_video 20-32s (12.0s, highway)
│       ├── sample_2.mp4             # Udacity project_video 35-45s (10.0s, highway)
│       ├── india_bangalore.webm     # Bangalore, Nandidurga Rd drive (127 s) - CC0, L. Shyamal, Wikimedia Commons
│       ├── india_newbel.webm        # Bangalore, New BEL Rd drive (96 s)     - CC0, L. Shyamal, Wikimedia Commons
│       └── india_cvraman.webm       # Bangalore, C V Raman Rd drive (48 s)   - CC0, L. Shyamal, Wikimedia Commons
├── detect_track.py                  # Phase 2: YOLOv8 nano/small + ByteTrack multi-object tracking
├── depth.py                         # Phase 3: Monocular relative depth via Depth Anything V2
├── ttc.py                           # Phase 4: Time-to-Collision & relative closing velocity with EMA
├── ego_motion.py                    # Phase 5: Road planar optical flow odometry (Speed & Position)
├── costmap.py                       # Phase 6: Bird's-Eye-View (BEV) 2D obstacle costmap with inflation
├── planner.py                       # Phase 7: Dynamic candidate arc rollout & bicycle model steering
├── decision.py                      # Decision state: GO / SLOW DOWN / BRAKE with corridor, TTC and hysteresis
├── overlay.py                       # Phase 8: Cockpit HUD compositor & telemetry widgets
├── main.py                          # Phase 8: Full end-to-end pipeline CLI
├── scenario_tests.py                # Safety scenario regression suite (run after every risk/planner change)
├── download_samples.py              # Video dataset download & trimming utility
├── requirements.txt                 # Pinned Python dependencies
└── README.md                        # Documentation & engineering specifications
```

---

## 3. Engineering Assumptions & Calibration Parameters

### Monocular Pinhole Camera Geometry
- **Mounting Height ($h$):** $1.30\text{ m}$ above the flat road plane.
- **Horizon Line ($c_y$):** Calibrated at $0.55 \cdot \text{Height}$ ($\approx 396\text{ px}$ on $1280 \times 720$), accounting for standard dashcam downward pitch.
- **Horizontal Field of View (FOV):** $70^\circ$, giving focal lengths $f_x = f_y \approx \frac{W/2}{\tan(35^\circ)} \approx 0.714 \cdot W$.
- **Principal Point:** $(c_x, c_y) = (W/2, 0.55 \cdot H)$.
- **Self-calibration (default):** horizon and camera height are re-estimated online from detected cars
  (box bottom row vs box width, assuming a ~1.75 m car width): $y_2 = c_y + (h / W_{car}) \cdot w$.
  The estimate is accepted only with ≥200 car boxes spanning ≥2.5× in width and plausible values
  ($0.40H \le c_y \le 0.70H$, $0.8 \le h \le 2.0$ m); otherwise the defaults above are kept.
  Override with `--horizon-ratio`, `--camera-height`, or disable with `--no-auto-geometry`.

### Vehicle Kinematic Bicycle Model
- **Wheelbase ($L$):** $2.70\text{ m}$ (standard passenger car / compact crossover).
- **Steering Angle Calculation:**
  $$\delta = \arctan(L \cdot \kappa)$$
  where $\kappa$ is the chosen trajectory curvature ($m^{-1}$).
- **Steering Limits:** $\delta \in [-15^\circ, +15^\circ]$ ($\kappa_{\max} \approx 0.099\text{ m}^{-1}$), 17 candidate arcs, centre-bias weight 60.
- **Steering Smoothing:** Temporal Exponential Moving Average ($\alpha_{\delta} = 0.30$).

### Top-Down BEV Costmap Grid
- **Longitudinal Range ($Y$):** $0.0\text{ m}$ to $40.0\text{ m}$ ahead of the front bumper.
- **Lateral Range ($X$):** $-20.0\text{ m}$ (left) to $+20.0\text{ m}$ (right).
- **Discretization:** $0.50\text{ m}$ per cell $\rightarrow 80 \times 80$ cell matrix.
- **Ego Position:** Centered at bottom $(X=0.0\text{ m}, Y=0.0\text{ m}) \rightarrow (\text{col } 40, \text{row } 79)$.

---

## 4. Calibration Caveats & Scoping Limitations

> [!IMPORTANT]
> ### 1. Monocular Depth: Road-Plane Calibration (not metric)
> Depth Anything V2 outputs **affine-invariant relative inverse depth**, not metric ground truth.
> - `--depth-mode ground` (default): per frame, the raw output on unoccupied road pixels is fitted to the flat-road
>   model $1/Z = (v - c_y)/(f_y h)$ (robust least squares, EMA-smoothed), and every pixel is mapped through that fit.
> - `--depth-mode heuristic`: the original fixed mapping of per-frame normalised disparity to 2–60 m.
>
> Validation against an independent car-width prior (no ground-truth range available):
>
> | Clip | Heuristic: median ratio / within ±30% | Road-plane calibrated |
> |---|---|---|
> | Bangalore (Nandidurga Rd) | 0.46 / 3% | 0.99 / 69% |
> | Highway sample_1 | 0.80 / 82% | 0.87 / 84% |
> | Highway sample_2 | 0.81 / 92% | 0.94 / 97% |
>
> The calibrated values depend on the flat-road assumption, FOV and camera geometry; they are estimates, not
> LiDAR-grade measurements. TTC is scale-invariant, so calibration does not change TTC itself.
>
> ### 2. Monocular Speed & Ego-Motion Odometry
> Ego-vehicle speed is derived purely from planar homography on road texture optical flow. Without physical wheel speed sensors or CAN-bus telemetry, speed values represent camera-estimated forward velocity.
>
> ### 3. Known Behaviour on Dense Indian Traffic (three Bangalore drive clips)
> - With the heuristic depth mode, range was compressed ~2× and the planner showed `NO SAFE PATH - BRAKE` in 99% of
>   the Nandidurga Rd clip. With road-plane depth, rider merging, border-aware lateral placement, TTC box-expansion
>   gating and the decision state, BRAKE is 4–13% of frames on the Indian clips and 0% on the highway samples
>   (see `outputs/*_stats.json` for per-run figures and every BRAKE episode with its reason).
> - Close-following in slow traffic is reported as `SLOW DOWN` and is the most frequent state in dense clips.
> - Oncoming / roadside objects often have a small heuristic TTC; they are coloured orange and only drive the
>   decision if they are inside or next to the ego corridor.
> - A single front camera cannot see objects directly beside the vehicle (at 1.5 m ahead only ±1.05 m is in view).
> - COCO has no auto-rickshaw class: autos are detected as `truck`/`bus` (class can flip between frames).
> - Ego speed and lateral odometry (X) are monocular estimates; X drifts substantially over long urban drives.
>
> ### 4. Prototype Scope vs Pitch Presentation
> This prototype currently provides **Dynamic Obstacle-Aware Tracking, Collision Avoidance, and Trajectory Planning** (pedestrians, cows/cattle, two-wheelers, cars, buses, trucks).  
> **Static road surface hazard segmentation** (potholes, unmarked dirt shoulders, broken edges) is designated as the **Phase 2 Production Roadmap**, and should be presented as future work rather than an operational module in this prototype.

---

## 5. Vulnerability Hierarchy & Cost Inflation

To address unstructured Indian road conditions with mixed traffic and high vulnerability variances, obstacles are categorized with distinct cost weights and inflation radii:

| Class | Base Cost | Footprint Radius | Collision Radius | Gaussian Inflation $\sigma$ | Risk Level |
|---|---|---|---|---|---|
| **Pedestrian (`person`)** | 255 | 0.8 m | 0.5 m | 2.5 m | Lethal / Maximum |
| **Animals (`cow`, `horse`, `elephant`, `dog`, `sheep`, `cat`)** | 250–255 | 0.6–2.2 m | 0.4–1.6 m | 2.0–3.0 m | Lethal / Unpredictable |
| **Two-Wheelers (`motorcycle`, `bicycle`)** | 220–240 | 1.0–1.2 m | 0.6–0.7 m | 2.0–2.2 m | High Vulnerability |
| **Heavy Commercial (`bus`, `truck`)** | 220 | 2.5 m | 1.5 m | 2.8 m | High Damage Potential |
| **Passenger Vehicle (`car`)** | 180 | 1.8 m | 1.1 m | 2.0 m | Standard Traffic |

Overlapping obstacle halos are aggregated using element-wise maximum ($\max(\text{Cost}_A, \text{Cost}_B)$) and strictly clamped to $[0, 255]$ to maintain consistent cost gradients.

**Hard collision check.** The footprint circle is a conservative soft-cost core; the planner's hard collision
test uses the smaller *collision radius* (≈ physical half-width + margin), and only for obstacles that are
closing critically (TTC < 2 s, confirmed by box expansion) or inside the 2 m emergency gap. Costs ≥ 240
(pedestrians, animals, bicycles near their core) are always treated as lethal. Person boxes riding a detected
motorcycle/bicycle are merged into the two-wheeler (they are not pedestrians). Boxes cut off by the left/right
image border are placed laterally from their visible inner edge.

## 5b. Decision State (bottom-right HUD)

`decision.py` turns perception + planning into a debounced recommendation. Per frame, each obstacle is checked
against the ego corridor (straight ahead **or** along the selected arc; half-width 0.9 m + obstacle collision
radius + 0.3 m):

| Instant level | Condition (in corridor unless stated) |
|---|---|
| **BRAKE** (immediate) | TTC < 1.0 s |
| **BRAKE** | closer than 2.0 m; or TTC < 2.0 s within 15 m; or pedestrian/animal/bicycle within 6 m; or planner: all candidate arcs blocked |
| **SLOW DOWN** | TTC < 4.0 s; or pedestrian/animal within 15 m; or closer than the following distance max(5 m, 1.2 s × ego speed); outside the corridor: pedestrian/animal within 1.5 m of it, or a TTC < 2 s object within 0.5 m of it |
| **GO** | otherwise (`STEER LEFT/RIGHT` when the planner steers ≥ 4°) |

Temporal state machine: SLOW DOWN needs 0.3 s persistence, BRAKE 0.2 s (TTC < 1 s is immediate); BRAKE is held
≥ 1.0 s and released only after 0.6 s below BRAKE, always to SLOW DOWN first; SLOW DOWN returns to GO after 1.0 s
clear. `NO SAFE PATH - BRAKE` is shown only when the BRAKE was triggered by the planner finding every arc blocked.
The reason for the current state (object, distance, TTC) is printed under the label.

Safety regression suite (25 synthetic multi-second scenarios through the real TTC → costmap → planner → decision
chain, with depth noise and camera-realistic boxes):

```powershell
python scenario_tests.py
```

---

## 6. Installation & Quickstart

### Prerequisites
- Python 3.10+ (tested with 3.10.11 on Windows 11)
- NVIDIA GPU optional (tested on RTX 3050 Laptop, driver 592.27); runs on CPU otherwise, much slower
- Internet on first run: YOLOv8 weights (~6 MB) and Depth-Anything-V2-Small (~100 MB) download automatically

```powershell
# 1. Navigate to repository
cd pathsense-prototype

# 2. Create & activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install PyTorch matched to your hardware FIRST (plain `pip install torch` on Windows is CPU-only)
#    NVIDIA GPU (driver with CUDA >= 12.6, check `nvidia-smi`):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
#    ...or CPU only:
# pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 4. Remaining dependencies
pip install -r requirements.txt

# 5. Verify GPU
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"

# 6. Sample clips (videos are not committed): downloads Udacity project_video.mp4 (~25 MB) and trims
#    data/samples/sample_1.mp4 and data/samples/sample_2.mp4
python download_samples.py
```

---

## 7. Execution Guide

### End-to-End Pipeline
Run the full 8-phase pipeline on any video OpenCV can decode (mp4/avi/mov/mkv). Resolution, FPS and
length are read from the file; inputs smaller than 1280x720 are upscaled for the HUD.

```powershell
# Run full pipeline on the sample
python main.py --input data/samples/sample_1.mp4 --output outputs/sample_1_result.mp4

# Any other video (output defaults to outputs/<name>_pathsense.mp4)
python main.py --input "C:\path\to\my_video.mp4"

# Options: model, device, frame limit, run statistics
python main.py --input data/samples/sample_2.mp4 --yolo-model yolov8s.pt --device cpu --max-frames 200 --stats-json outputs/sample_2_stats.json

# Depth mode / camera geometry
python main.py --input data/samples/india_bangalore.webm --depth-mode heuristic      # original 2-60 m mapping
python main.py --input my_video.mp4 --horizon-ratio 0.60 --camera-height 1.2        # known camera mounting
```

The HUD labels "TTC" and distances are **heuristic** (relative monocular depth scaled to 2–60 m), not
calibrated metric measurements. When every candidate arc collides with a closing/near obstacle, the
planner reports `NO SAFE PATH - BRAKE` instead of presenting a path as safe.

### Running Individual Modules Independently

```powershell
# Phase 1: Verify environment & sample clips
python verify_phase1.py

# Phase 2: Object Detection & ByteTrack tracking
python detect_track.py --input data/samples/sample_1.mp4 --output detect_track_preview.mp4 --output-json detect_track_results.json

# Phase 3: Monocular Relative Depth Estimation
python depth.py --input data/samples/sample_1.mp4 --tracking-json detect_track_results.json --output depth_preview.mp4 --output-json depth_results.json

# Phase 4: Time-to-Collision & Closing Speed
python ttc.py --input data/samples/sample_1.mp4 --depth-json depth_results.json --output ttc_preview.mp4 --output-json ttc_results.json

# Phase 5: Ego-Motion Speed & 2D Position Odometry
python ego_motion.py --input data/samples/sample_1.mp4 --ttc-json ttc_results.json --speed-plot speed_profile.png --pos-plot position_trace.png

# Phase 6: Bird's-Eye-View (BEV) Occupancy Costmap
python costmap.py --input data/samples/sample_1.mp4 --ttc-json ttc_results.json --output costmap_preview.mp4 --output-json costmap_results.json

# Phase 7: Dynamic Arc Planner & Steering Angle
python planner.py --input data/samples/sample_1.mp4 --costmap-json costmap_results.json --output planner_preview.mp4 --output-json planner_results.json
```
