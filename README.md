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
│   └── samples/
│       ├── sample_1.mp4             # Primary cruise & curved road clip (12.0s, 300 frames)
│       └── sample_closing.mp4       # Closing hazard & deceleration clip (8.0s, 200 frames)
├── detect_track.py                  # Phase 2: YOLOv8 nano/small + ByteTrack multi-object tracking
├── depth.py                         # Phase 3: Monocular relative depth via Depth Anything V2
├── ttc.py                           # Phase 4: Time-to-Collision & relative closing velocity with EMA
├── ego_motion.py                    # Phase 5: Road planar optical flow odometry (Speed & Position)
├── costmap.py                       # Phase 6: Bird's-Eye-View (BEV) 2D obstacle costmap with inflation
├── planner.py                       # Phase 7: Dynamic candidate arc rollout & bicycle model steering
├── overlay.py                       # Phase 8: Cockpit HUD compositor & telemetry widgets
├── main.py                          # Phase 8: Full end-to-end pipeline CLI
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

### Vehicle Kinematic Bicycle Model
- **Wheelbase ($L$):** $2.70\text{ m}$ (standard passenger car / compact crossover).
- **Steering Angle Calculation:**
  $$\delta = \arctan(L \cdot \kappa)$$
  where $\kappa$ is the chosen trajectory curvature ($m^{-1}$).
- **Steering Limits:** $\delta \in [-30^\circ, +30^\circ]$ ($\kappa_{\max} \approx 0.214\text{ m}^{-1}$).
- **Steering Smoothing:** Temporal Exponential Moving Average ($\alpha_{\delta} = 0.30$).

### Top-Down BEV Costmap Grid
- **Longitudinal Range ($Y$):** $0.0\text{ m}$ to $40.0\text{ m}$ ahead of the front bumper.
- **Lateral Range ($X$):** $-20.0\text{ m}$ (left) to $+20.0\text{ m}$ (right).
- **Discretization:** $0.50\text{ m}$ per cell $\rightarrow 80 \times 80$ cell matrix.
- **Ego Position:** Centered at bottom $(X=0.0\text{ m}, Y=0.0\text{ m}) \rightarrow (\text{col } 40, \text{row } 79)$.

---

## 4. Calibration Caveats & Scoping Limitations

> [!IMPORTANT]
> ### 1. Monocular Relative Depth Limitation
> Pretrained monocular depth networks (Depth Anything V2, MiDaS) output **relative inverse disparity**, not metric ground truth. The $[2.0\text{ m}, 60.0\text{ m}]$ mapping is a **heuristic calibration** derived from inverse-disparity scaling:
> $$Z = \frac{d_{\min} \cdot d_{\max}}{d_{\min} + D_{\text{norm}} \cdot (d_{\max} - d_{\min})}$$
> Distance and Time-to-Collision (TTC) values are situational awareness estimates for behavioral decision-making, not certifiable LiDAR-grade measurements.
>
> ### 2. Monocular Speed & Ego-Motion Odometry
> Ego-vehicle speed is derived purely from planar homography on road texture optical flow. Without physical wheel speed sensors or CAN-bus telemetry, speed values represent camera-estimated forward velocity.
>
> ### 3. Prototype Scope vs Pitch Presentation
> This prototype currently provides **Dynamic Obstacle-Aware Tracking, Collision Avoidance, and Trajectory Planning** (pedestrians, cows/cattle, two-wheelers, cars, buses, trucks).  
> **Static road surface hazard segmentation** (potholes, unmarked dirt shoulders, broken edges) is designated as the **Phase 2 Production Roadmap**, and should be presented as future work rather than an operational module in this prototype.

---

## 5. Vulnerability Hierarchy & Cost Inflation

To address unstructured Indian road conditions with mixed traffic and high vulnerability variances, obstacles are categorized with distinct cost weights and inflation radii:

| Class | Base Cost | Footprint Radius | Gaussian Inflation $\sigma$ | Risk Level |
|---|---|---|---|---|
| **Pedestrian (`person`)** | 255 | 0.8 m | 2.5 m | Lethal / Maximum |
| **Cattle / Animals (`cow`, `horse`, `dog`)** | 255 | 1.6 m | 2.8 m | Lethal / Unpredictable |
| **Two-Wheelers (`motorcycle`, `bicycle`)** | 220–240 | 1.0–1.2 m | 2.0–2.2 m | High Vulnerability |
| **Heavy Commercial (`bus`, `truck`)** | 220 | 2.5 m | 2.8 m | High Damage Potential |
| **Passenger Vehicle (`car`)** | 180 | 1.8 m | 2.0 m | Standard Traffic |

Overlapping obstacle halos are aggregated using element-wise maximum ($\max(\text{Cost}_A, \text{Cost}_B)$) and strictly clamped to $[0, 255]$ to maintain consistent cost gradients.

---

## 6. Installation & Quickstart

### Prerequisites
- Python 3.10+ (Python 3.12 recommended)
- Windows / Linux / macOS

```powershell
# 1. Navigate to repository
cd pathsense-prototype

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\activate   # On Windows

# 3. Install CPU PyTorch & dependencies
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

---

## 7. Execution Guide

### End-to-End Pipeline
Run the full 8-phase pipeline on any dashcam video:

```powershell
# Run full pipeline on test video
python main.py --input data/samples/sample_1.mp4 --output pathsense_output.mp4

# Run with custom YOLO model or frame limits
python main.py --input data/samples/sample_closing.mp4 --output pathsense_closing.mp4 --yolo-model yolov8s.pt --max-frames 200
```

### Running Individual Modules Independently

```powershell
# Phase 1: Verify environment & sample clips
python verify_phase1.py

# Phase 2: Object Detection & ByteTrack tracking
python detect_track.py --input data/samples/sample_1.mp4 --output detect_track_preview.mp4 --output-json detect_track_results.json

# Phase 3: Monocular Relative Depth Estimation
python depth.py --input data/samples/sample_1.mp4 --tracking-json detect_track_results.json --output depth_preview.mp4 --output-json depth_results.json

# Phase 4: Time-to-Collision & Closing Speed
python ttc.py --input data/samples/sample_closing.mp4 --depth-json sample_closing_depth.json --output ttc_preview_closing.mp4 --output-json sample_closing_ttc.json

# Phase 5: Ego-Motion Speed & 2D Position Odometry
python ego_motion.py --input data/samples/sample_1.mp4 --ttc-json ttc_results.json --speed-plot speed_profile.png --pos-plot position_trace.png

# Phase 6: Bird's-Eye-View (BEV) Occupancy Costmap
python costmap.py --input data/samples/sample_1.mp4 --ttc-json ttc_results.json --output costmap_preview.mp4 --output-json costmap_results.json

# Phase 7: Dynamic Arc Planner & Steering Angle
python planner.py --input data/samples/sample_1.mp4 --costmap-json costmap_results.json --output planner_preview.mp4 --output-json planner_results.json
```
