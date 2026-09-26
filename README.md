# PathSense: Adaptive Path Planning & Collision Avoidance on Indian Roads

> **Smart India Hackathon Problem Statement 26037**
> *"Adaptive Path Planning and Collision Avoidance for Autonomous Vehicles on Unstructured Indian Roads."*
>
> PathSense is a **camera-only research/hackathon prototype**. From a single front-facing video it produces a
> **candidate trajectory and a decision recommendation** (GO / STEER / SLOW DOWN / BRAKE) with an explanation.
> It is **not** a production autonomous-driving system: all distances, speeds and TTCs are monocular estimates,
> nothing is certified, and there is no vehicle actuation.

---

## 1. Architecture

```
Front camera video
   │
   ├─► YOLOv8n + ByteTrack ─► rider merge ─► auto-rickshaw refinement (CLIP, track level)   detect_track.py / autorickshaw.py
   │          │
   │          ├─► Depth Anything V2 (relative) ─► road-plane calibration ─► per-object distance   depth.py
   │          │          │
   │          │          └─► TTC: EMA depth derivative, gated by box expansion                   ttc.py
   │          │
   ├─► Road optical flow ─► ego speed / odometry (flat-road model)                            ego_motion.py
   │
   └─► BEV costmap (class vulnerability, collision radii, frame-edge aware)                    costmap.py
          │
          ├─► Dynamic arc planner: 17 bicycle-model arcs, ±15°, per-arc collision test          planner.py
          ├─► Behaviour cues from track history: CUT-IN / CROSSING / ONCOMING / SLOW LEAD      behavior.py
          └─► Decision state machine (corridor + TTC + blocked arcs + hysteresis)               decision.py
                 │
                 ├─► HUD: boxes, BEV + arcs, steering, decision panel with reason, history strip   overlay.py
                 └─► Event log + timeline + replay (audit trail)                                  events.py
```

`main.py` runs everything on any video OpenCV can decode and writes, per input `<name>`:
`outputs/<name>_pathsense.mp4` (HUD video), `outputs/<name>_stats.json` (run statistics),
`outputs/<name>_events.json` (decision/behaviour events) and `outputs/<name>_timeline.png`.

| File | Responsibility |
|---|---|
| `main.py` | End-to-end CLI, statistics, event log, `--replay-event` |
| `detect_track.py` | YOLOv8 + ByteTrack, degenerate-box filter, rider/two-wheeler merge |
| `autorickshaw.py` | Zero-shot CLIP refinement of YOLO truck/bus tracks to auto-rickshaw |
| `depth.py` | Depth Anything V2 + road-plane calibration (+ original heuristic mode) |
| `ttc.py` | Closing speed / TTC with box-expansion gate |
| `ego_motion.py` | Ego speed and 2D odometry from road optical flow |
| `costmap.py` | BEV costmap, class profiles, hard-collision occupancy |
| `planner.py` | Candidate arcs, scoring, steering, per-arc collision flags |
| `behavior.py` | Cut-in / crossing / oncoming / slow-lead / side-pass cues |
| `decision.py` | GO / SLOW DOWN / BRAKE / NO SAFE PATH state machine with reasons |
| `overlay.py` | HUD compositor and decision panel |
| `events.py` | Event log, timeline PNG, replay CLI |
| `scenario_tests.py` | Safety regression suite |
| `download_samples.py` | Sample and Indian validation clip download |
| `showcase.py` | Labelled demo reel from rendered outputs |

---

## 2. Depth: road-plane calibration (not metric)

Depth Anything V2 outputs **affine-invariant relative inverse depth**. On the visible road surface the flat-road
pinhole model gives $1/Z = (v - c_y)/(f_y h)$, so per frame PathSense fits $1/Z = a\,d + b$ on unoccupied road
pixels (robust least squares, EMA-smoothed) and maps every pixel through that fit (`--depth-mode ground`, default).
`--depth-mode heuristic` keeps the original fixed 2–60 m mapping.

Camera geometry: 70° horizontal FOV, horizon $c_y = 0.55H$, height 1.30 m by default. Horizon and height are
**self-calibrated** online from detected cars ($y_2 = c_y + (h/W_{car})\,w$, car width ≈ 1.75 m) and accepted only
with ≥ 200 car boxes spanning ≥ 2.5× in width and plausible values ($0.40H \le c_y \le 0.70H$, $0.8 \le h \le 2.0$ m).
Override with `--horizon-ratio`, `--camera-height`, or `--no-auto-geometry`.

Validation against an independent car-width prior (no ground-truth range available):

| Clip | Heuristic: median ratio / within ±30% | Road-plane calibrated |
|---|---|---|
| Bangalore (Nandidurga Rd) | 0.46 / 3% | 0.99 / 69% |
| Highway sample_1 | 0.80 / 82% | 0.87 / 84% |
| Highway sample_2 | 0.81 / 92% | 0.94 / 97% |

These are estimates that depend on the flat-road assumption and camera geometry, **not** calibrated metric depth.

## 3. TTC / closing speed

Per track: EMA-smoothed distance, finite difference over 0.2 s, EMA-smoothed closing speed, TTC = distance /
closing speed (0.1–25 s). Tracks unseen for > 1 s restart. Because depth noise on small, distant boxes can fake
large closing speeds, the depth TTC is **cross-checked with bounding-box expansion** over 0.4 s
($TTC_{img} = \Delta t / (s_t/s_{t-k} - 1)$): if the box is not growing the TTC is dropped; if expansion implies a much
slower approach, the expansion TTC is used; young tracks cannot be CRITICAL until confirmed. TTC is heuristic and
scale-invariant (depth calibration does not change it).

## 4. Ego-motion

Sparse Lucas–Kanade flow on road features (obstacles masked), projected with the flat-road model into metric
displacement, median-filtered and EMA-smoothed into ego speed and a 2D position trace. It uses the self-calibrated
horizon / camera height when available. Speed is a monocular estimate; lateral odometry drifts on long drives.

## 5. BEV costmap

80 × 80 grid (0.5 m cells, ±20 m lateral, 40 m ahead). Each object is projected with its distance and box centre
(boxes cut by the left/right image border are placed from their visible inner edge) and painted with a
class-specific core and Gaussian halo; halos combine with element-wise max.

| Class | Base cost | Footprint r | Collision r | σ | Risk |
|---|---|---|---|---|---|
| Pedestrian (`person`) | 255 | 0.8 m | 0.5 m | 2.5 m | Lethal / maximum |
| Animals (`cow`, `horse`, `elephant`, `dog`, `sheep`, `cat`) | 250–255 | 0.6–2.2 m | 0.4–1.6 m | 2.0–3.0 m | Lethal / unpredictable |
| Two-wheelers (`motorcycle`, `bicycle`) | 220–240 | 1.0–1.2 m | 0.6–0.7 m | 2.0–2.2 m | High vulnerability |
| `auto-rickshaw` | 200 | 1.3 m | 0.8 m | 2.0 m | Three-wheeler |
| Heavy (`bus`, `truck`) | 220 | 2.5 m | 1.5 m | 2.8 m | High damage potential |
| Passenger car | 180 | 1.8 m | 1.1 m | 2.0 m | Standard traffic |

**Hard collision occupancy** uses the collision radius and only for objects that are closing critically (TTC < 2 s,
box-expansion confirmed) or inside the 2 m emergency gap. Pedestrians and animals always add their collision radius
+ 0.3 m to the hard occupancy (driving through a person is a collision at any speed). Their wide soft halo only ranks
arcs: since round 6 it no longer blocks an arc by itself, because a halo-lethal rule let people walking beside the road
(0.5 m outside the corridor, on both sides) force `NO SAFE PATH` through proximity alone (regression M6b).
Person boxes riding a detected motorcycle/bicycle are merged into the two-wheeler (not treated as pedestrians);
a merged rider whose bike drops out of detection is kept as a two-wheeler for up to 2 s (track-level rider memory).
A rider is only recognised once the two-wheeler is visible in the frame.

## 6. Dynamic trajectory planner

17 constant-curvature candidate arcs over ±15° steering (kinematic bicycle model, wheelbase 2.7 m, 22 m lookahead,
truncated at 90° heading). Each arc samples the vehicle body (±0.9 m) on the costmap: length-normalised obstacle
cost + centre bias + smoothness; an arc is **blocked** only if the body physically overlaps hard occupancy. The
lowest-cost arc is the **candidate trajectory**; steering = atan(L·κ), EMA-smoothed. If every arc is blocked the
planner reports it (→ `NO SAFE PATH`).

## 7. Decision state machine (bottom-right HUD)

Each object is checked against the ego corridor (straight ahead **or** along the selected arc; 0.9 m half-width +
object collision radius + 0.3 m):

| Instant level | Condition (in corridor unless stated) |
|---|---|
| **BRAKE** (immediate) | TTC < 1.0 s |
| **BRAKE** | closer than 2.0 m; TTC < 2.0 s within 15 m; pedestrian/animal/bicycle within 6 m; planner: all arcs blocked |
| **SLOW DOWN** | TTC < 4.0 s; pedestrian/animal within 15 m; cut-in / crossing / oncoming cue; stopped lead; closer than max(5 m, 1.2 s × ego speed). Outside the corridor: see *directional path-threat model* below |
| **GO** | otherwise (`STEER LEFT/RIGHT` when the planner steers ≥ 4°) |

Temporal state machine: SLOW DOWN needs 0.3 s persistence, BRAKE 0.2 s (TTC < 1 s is immediate); BRAKE is held
≥ 1.0 s and released after 0.6 s below BRAKE, always to SLOW DOWN first (shown as "Clearing after BRAKE");
SLOW DOWN returns to GO after 1.0 s clear. **`NO SAFE PATH` is shown only when the planner finds every candidate
arc blocked** (debounced: all arcs blocked for 0.3 s to show it, unblocked for 0.3 s to withdraw it, so the label
cannot flicker) — never for a single object; a BRAKE held after the arcs clear is titled *Holding BRAKE*. The path
chip uses the same debounced flag, so `NO SAFE PATH` always appears with `PATH BLOCKED` (checked in every
regression scenario). `STEER LEFT/RIGHT` labels are debounced the same way.

The panel shows: decision, reason title (e.g. *Slow vehicle ahead*, *Motorcycle cut-in*, *Collision risk*,
*All candidate trajectories blocked*), the responsible object (class, id, distance, TTC, behaviour), the path status
from the planner's per-arc test (`PATH CLEAR` / `PARTIAL k/17` / `BLOCKED`) and the number of objects raising risk.
The responsible object is outlined in white; red boxes = critical in-path objects, orange = closing but outside the
corridor. A thin strip above the instrument bar shows the decision history of the whole video.

### Directional path-threat model (objects outside the corridor)

A threat is a **conflict with the ego path**, not "an object is close". Each object gets a `path_conflict` class from
its clearance to the corridor, its drift-corrected lateral velocity (`behavior.py`) and its predicted clearance within
min(TTC, 3 s). India drives on the **left** (`traffic_side="left"`): oncoming traffic is expected on the right.

| Class | Meaning | Decision |
|---|---|---|
| `IN_PATH` | inside the corridor | in-corridor rules above |
| `ENTERING` | predicted to enter the corridor | **BRAKE** if TTC < 2 s within 15 m, or a pedestrian/animal within 6 m; else SLOW DOWN ("car cut-in", "person crossing into path") |
| `APPROACHING` | moving toward the corridor (≥ 0.3 m/s), within 15 m | SLOW DOWN ("… drifting toward path") |
| `NEAR` | close but parallel / moving away | SLOW DOWN only for a pedestrian/animal < 0.8 m not moving away, a wrong-way vehicle < 1.5 m, or a same-direction vehicle < 0.5 m with TTC < 2 s within 10 m |
| `CLEAR` | no conflict | GO |

So an oncoming car staying on its own side → GO (monitor); drifting toward us → SLOW DOWN; entering the corridor →
BRAKE; all arcs blocked → NO SAFE PATH. For an oncoming vehicle on its own side the monocular lateral velocity is unreliable (at high closing speed a passing vehicle's estimated x shrinks with depth error), so it is ENTERING only at < 0.5 m from the corridor edge while moving in at ≥ 1 m/s, otherwise at most APPROACHING within 1 m (measured on the Bangalore clip, regression DIR10). A pedestrian walking along the shoulder → at most CAUTION; stepping toward the
path → SLOW DOWN; entering / crossing → BRAKE. Regression scenarios DIR1–DIR9 cover these cases.

## 8. Event timeline and replay (audit trail)

Every decision change and every confirmed behaviour cue is logged with frame, time, decision, reason, responsible
object (track id, class, distance, TTC, behaviour), steering, ego speed and path status.

```powershell
python events.py list outputs\india_bangalore_events.json                 # all events
python events.py list outputs\india_bangalore_events.json --label BRAKE   # only BRAKE decisions
python events.py show outputs\india_bangalore_events.json 12              # "why did it brake here?" card + clip + still
python main.py --input data\samples\india_bangalore.webm --replay-event 12  # same, from the input path
python events.py timeline outputs\india_bangalore_events.json            # regenerate the timeline PNG
```

`show` prints Frame / Decision / Reason / Path / Object / Distance / TTC / Steering and exports a ±3 s clip and an
annotated still from the rendered HUD video.

Demo reel: `python showcase.py` builds `outputs/pathsense_showcase.mp4` (~65 s) from the rendered outputs. It is an
edited selection of **unmodified** pipeline output; every segment is preceded by a card naming the source clip and
time range, and the intro card states that distances/TTC are monocular estimates.

## 9. Behaviour cues: cut-in, crossing, oncoming

`behavior.py` uses the existing ByteTrack tracks' BEV positions, box size, closing speed and ego speed (no learned
model). Lateral velocity is a least-squares slope over 0.8 s; boxes cut by the image border never enter the history;
when ≥ 2 other tracks are visible their median lateral drift per metre of distance (ego yaw on curves) is subtracted;
a cue must persist 0.3 s (0.5 s for speed-based cues) and is held 1.5 s.

| Cue | Evidence |
|---|---|
| `CUT-IN` | same-direction vehicle/two-wheeler (box expanding < 0.5/s, i.e. not oncoming) that started outside the corridor, moved ≥ 0.7 m towards it at ≥ 0.5 m/s (drift-corrected) and reached its edge |
| `CROSSING` | pedestrian/animal with ≥ 0.5 m/s lateral speed and ≥ 0.7 m net lateral motion whose path enters the corridor within 2 s |
| `ONCOMING` | closing speed ≥ ego speed + 3 m/s near the corridor (object moving towards the ego vehicle) |
| `SLOW LEAD` | in-corridor vehicle with estimated own speed < 1.5 m/s while ego ≥ 3 m/s |
| `SIDE PASS` | alongside the corridor without lateral motion (informational, never escalates) |

These are cues from monocular estimates, not verified intent.

## 10. Auto-rickshaw recognition

COCO YOLO reports autos as `truck`/`bus` (on the Bangalore clip ~47 of 60 sampled truck/bus boxes were autos).
YOLO-World zero-shot was tested and **rejected** (the lead auto stayed "truck" at 0.74–0.78; it also installs extra
packages at runtime). Instead `autorickshaw.py` re-checks only YOLO truck/bus tracks with zero-shot **CLIP ViT-B/32**
(via the already-required `transformers`), at most ~1 sample per track per second, and relabels a track as
`auto-rickshaw` only when ≥ 2 samples have mean p(auto) ≥ 0.7. On 60 hand-checked Bangalore crops (threshold 0.6):
46/47 autos recognised, 1 false positive (a small yellow goods tempo). The HUD shows `auto (est.)`; the YOLO class
is kept in the events. Cost ≈ 1 ms/frame on average, ~0.3 GB extra VRAM; disable with `--no-auto-classifier`.

## 11. Dataset / video validation

All clips are processed by the same pipeline (RTX 3050 Laptop GPU, 8.6–9.0 FPS at 720p measured on the current
code; 0.3× real time). Rows marked *(prev. round)* were not re-rendered (their outputs were removed). `delhi_cattle` was re-run in round 6 only to verify the planner change; its output was removed again to respect the curated `outputs/`. BRAKE % baseline is the round-4 value. Indian clips are from Wikimedia Commons (`python download_samples.py --indian`). "Baseline" is commit
`ecfb166` (before the decision HUD / events / behaviour / auto-rickshaw work).

| Clip | Location | Licence (author) | Duration / FPS | Scenario | BRAKE % (baseline → now) | SLOW DOWN % | GO % | Cut-in / crossing / oncoming cues | Autos |
|---|---|---|---|---|---|---|---|---|---|
| `india_bangalore` | Bangalore, Nandidurga Rd | CC0 (L. Shyamal) | 127 s / 30 | dense mixed traffic, autos, signal queue | 5.1 → 6.4 | 87.1 | 6.5 | 2 / 1 / 4 | 12 |
| `india_newbel` | Bangalore, New BEL Rd | CC0 (L. Shyamal) | 96 s / 30 | narrow road, pedestrians, oncoming | 13.1 → 9.1 | 43.2 | 47.7 | 1 / 0 / 5 | 1 |
| `india_cvraman` | Bangalore, C V Raman Rd | CC0 (L. Shyamal) | 48 s / 30 | arterial, cut-ins | 4.1 → 2.1 | 41.9 | 56.0 | 0 / 0 / 0 | 1 |
| `ka_kadur` | Kadur–Chikmagalur, Karnataka | CC0 (L. Shyamal) | 48 s / 29.6 | rural highway, curves | 0 → 0 | 0 | 100.0 | 0 / 0 / 0 | 0 |
| `tn_anamalai` (prev. round) | Anamalai, Tamil Nadu | CC BY-SA 3.0 (T. R. Shankar Raman) | 25 s / 25 | narrow rural road, oncoming | 8.4 → 8.4 | 29.5 | 62.1 | 2 / 2 / 0 | 0 |
| `blr_iisc` | IISc campus, Bangalore | CC0 (L. Shyamal) | 86 s / 30 | pedestrians walking on the carriageway, two-wheelers | 23.5 → 18.4 | 54.9 | 26.7 | 0 / 0 / 0 | 0 |
| `delhi_cattle` (verified; output removed) | Lutyens Delhi | CC BY-SA 3.0 (Fowler&fowler) | 45 s / 30* | cattle crossing at a traffic island (angled in-car camera, near stop) | 65.1 → 53.5 | 42.6 | 3.9 | 9 / 7 / 0 | 0 |
| `frederiksted_pier` | Frederiksted pier, St. Croix (left-hand traffic) | CC BY 3.0 (John Edwards) | 45 s / 30 (480p) | pedestrians walking beside and crossing in front of a slow taxi — **in-vehicle camera, dashboard visible** | new → 0.9 | 59.6 | 39.5 | 1 / 11 / 0 | 0 |
| `hyd_traffic` (prev. round) | Hyderabad | CC BY-SA 4.0 (Oleg Yunakov) | 56 s / 60 | dense two-wheelers/autos at a signal — **filmed from inside an auto (robustness only)** | 39.3 → 34.9 | 55.7 | 9.4 | 8 / 4 / 0 | 1 |
| `hp_rohtang_seg` (prev. round) | Rohtang Pass, Himachal | CC BY 3.0 (KSOFTECH) | 150 s / 30 (360p) | unpaved mountain road, queue — **motorcycle-mounted camera (robustness only)** | 39.8 → 38.1 | 49.6 | 12.4 | 5 / 8 / 5 | 0 |
| `sample_1`, `sample_2` (outputs removed) | US highway (Udacity) | project video | 12 s + 10 s / 25 | highway regression | 0 → 0 | 0 | 100 | 0 / 0 / 0 | 0 |

\* container reports 600 FPS; the pipeline estimates 29.98 FPS from frame timestamps.

Behaviour and auto counts are confirmed track-level onsets. Every BRAKE episode, its reason and the responsible
object are listed in `outputs/<clip>_events.json`. Observed false / borderline BRAKE sources: pedestrians walking along
the edge of narrow roads (conservative pedestrian halo), a scooter rider whose scooter is below the image border (seen
as a pedestrian until the scooter appears), BRAKE holds right after a threat passes. Clips from two-wheeler / in-auto
cameras violate the car-mounted camera model; their numbers are reported for robustness, not accuracy.

### Round 6 before → after (planner no longer blocks arcs by proximity halos)

| Clip | BRAKE % | of which NO SAFE PATH % | BRAKE episodes |
|---|---|---|---|
| india_bangalore | 7.2 → 6.4 | 0.4 → 0 | 8 → 7 |
| india_newbel | 14.5 → 9.1 | 3.7 → 0.3 | 8 → 7 |
| india_cvraman | 4.1 → 2.1 | 0 → 0 | 2 → 1 |
| blr_iisc | 22.7 → 18.4 | 8.0 → 3.7 | 12 → 12 |
| delhi_cattle | 65.8 → 53.5 | 55.8 → 42.3 | — → 8 |
| ka_kadur | 0 → 0 | 0 → 0 | 0 → 0 |

Oncoming traffic on its own side still does not trigger BRAKE on the Bangalore clip; cattle and pedestrians that are
actually in the path still produce BRAKE / NO SAFE PATH.

### Crossing-pedestrian clip (`frederiksted_pier`)

Source: [Wikimedia Commons — *Driver view taxi ride into Frederiksted, St. Croix, U.S. Virgin Islands 3-6-18*](https://commons.wikimedia.org/wiki/File:Driver_view_taxi_ride_into_Frederiksted,_St._Croix,_U.S._Virgin_Islands_3-6-18.webm),
John Edwards, **CC BY 3.0**. Derivative: 480p Commons transcode, excerpt 161–206 s (45 s), every second frame
(29.97 FPS), saved as `data/candidates/frederiksted_pier.mp4` (not committed). St. Croix drives on the left, like India.
Result: pedestrians walking beside the pier road → GO STRAIGHT; people crossing 13–22 m ahead → SLOW DOWN
("Pedestrian crossing into path"); a pedestrian stepping in at 3.8 m → BRAKE. Because the taxi is slow and most
crossings are 10 m+ ahead, the clip shows SLOW DOWN far more than a sustained BRAKE (0.9 % BRAKE). Limitation: the
camera is inside the vehicle with the dashboard in view (default camera geometry).

## 12. Known limitations

- Camera-only: no LiDAR/radar/CAN; all distances, speeds and TTCs are monocular estimates (road-plane calibrated,
  flat-road assumption), not metric ground truth. Performance ≈ 9 FPS on an RTX 3050 laptop GPU — not real time.
- A single front camera cannot see objects directly beside the vehicle (at 1.5 m ahead only ±1.05 m is in view).
- The decision is a recommendation from a fixed-width corridor; there is no lane/drivable-area segmentation, no
  speed planning and no prediction beyond short behaviour cues.
- Close following in dense traffic is (correctly but often) shown as SLOW DOWN; some borderline BRAKEs remain
  (pedestrians along narrow road edges, BRAKE holds after a threat passes).
- Behaviour cues and auto-rickshaw labels are heuristic / zero-shot estimates. Without an ego-yaw measurement,
  a single object passed on a curve can still be labelled CUT-IN (drift correction needs ≥ 2 other tracks), and
  YOLO false positives (e.g. a striped tree trunk as `person`) propagate into cues.
- Two-wheeler / hand-held camera footage (Hyderabad signal, Rohtang ride) violates the car-mounted geometry
  (camera height, corridor width); decisions there are shown for robustness only.
- Accident detection is conservative and camera-only: a vehicle that fills the frame, motion-blurred, at contact range
  is not detected, so a real ego rear-end was not confirmed (see §16).
- Validation footage is limited to CC-licensed Commons clips (mostly Bangalore, daytime); no night drive, no
  intersection turns / U-turns, no Mumbai/Delhi city drive.

## 13. Installation & run commands (Windows PowerShell)

```powershell
cd pathsense-prototype
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126   # NVIDIA GPU (CUDA >= 12.6)
# pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu  # CPU only
pip install -r requirements.txt
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"

python download_samples.py --indian        # highway samples + CC-licensed Indian validation clips (Wikimedia Commons)

python main.py --input data\samples\india_bangalore.webm      # -> outputs\india_bangalore_pathsense.mp4 (+ stats/events/timeline)
python main.py --input "C:\path\to\my_video.mp4"              # any video
python main.py --input data\samples\sample_1.mp4 --depth-mode heuristic --no-auto-classifier   # original behaviour
python main.py --input my_video.mp4 --horizon-ratio 0.60 --camera-height 1.2                  # known camera mounting
```

First run downloads YOLOv8n (~6 MB), Depth-Anything-V2-Small (~100 MB) and CLIP ViT-B/32 (~600 MB).

## 14. Scenario regression suite

```powershell
python scenario_tests.py        # exit code 0 = all scenarios pass
```

Synthetic multi-second scenarios through the real TTC → costmap → planner → behaviour → decision chain with depth
noise and camera-realistic (clipped) boxes: clear road, far/slow/close/stopped lead, fast closing vehicle,
pedestrian/cow in path, pedestrian and cow crossing, motorcycle and car cut-in, oncoming vehicle in the corridor and
in its own lane, adjacent harmless vehicles, frame-edge vehicle, fully blocked road, both sides blocked, queue at a
signal, recovery after BRAKE, no-flicker hysteresis, rider suppression, auto-rickshaw corridor geometry, and a
real-image auto-rickshaw check (skipped if the Bangalore clip is not downloaded).
A path-threat test matrix M1–M14 (+M6b) checks: oncoming vehicle safe / drifting / entering / blocking; left- and
right-footpath pedestrians walking, close, approaching, crossing; a pedestrian occupying the path; BRAKE held while a
crossing pedestrian stays in the path; recovery through hysteresis without re-BRAKE; pedestrians on both footpaths
never forcing BRAKE. Directional tests DIR1–DIR10 (harmless / drifting / entering oncoming vehicle, shoulder / close / crossing pedestrian,
motorcycle overtaking, real cut-in, wrong-way vehicle) and accident tests ACC1–ACC12 (normal driving, hard braking,
queue, brief contact, confirmed collision, latching, single-frame spike, reset, frame-edge truncation, congestion
creep-to-stop, near-field reflection, ego rear-end) run in the same suite.

## 15. Web app (landing page, demo + replay, live camera, emergency simulation)

```powershell
python app.py                       # desktop: http://localhost:8000
python app.py --lan --https         # phone on the same Wi-Fi: https://<PC-LAN-IP>:8443 (self-signed certificate)
```

`app.py` (Flask) serves `web/` and calls the same pipeline — nothing is re-implemented in the browser.

The site opens on a **landing page** (`#home`: product statement, an abstract generated canvas visual of a road with
road users and the ego path — no prototype imagery — and short "how it works" sections). **Try PathSense** goes
straight into the app, whose navigation is only **Demo · Live · Emergency** (`#demo`, `#live`, `#emergency`).

- **Demo** – processed drives from `outputs/` (only videos that currently exist), a player synced with per-frame
  telemetry (`<name>_frames.json`): decision, reason, path status, speed, steering, nearest threat, distance, TTC;
  a clickable decision timeline; **Upload** runs `main.run_pipeline` on the GPU with live progress and plays the result
  (outputs go to `outputs/web/`). Videos are written as H.264 (`avc1`) so browsers can play them; older `mp4v`
  renders are flagged.
- **Replay** (inside Demo) – key moments of the selected drive (BRAKE / SLOW DOWN onsets, cut-ins, crossings,
  oncoming, accident alerts; filters Key moments / Brake / Road users / All). Selecting one shows the decision, reason,
  object, distance, TTC, behaviour, path status and steering, and **Replay** plays it from 2 s before.
- **Live** – the page captures the camera (`getUserMedia`), sends ≤ 640 px JPEG frames to the PC one at a time
  (self-throttling) and draws the returned decision, boxes, TTC, path and a mini BEV. FPS and latency shown are
  measured. Browsers only allow the camera on `https://` or `localhost`, hence `--https` for phones (accept the
  certificate warning once; `certs/` is gitignored). `python live_benchmark.py <video>` measures live throughput
  against a running server and writes `outputs/live_benchmark.json`.
- **Emergency** – contact settings stored locally in `config/emergency.json` (gitignored, editable, deletable),
  browser geolocation or a clearly labelled DEMO LOCATION (never invented), nearby hospitals from OpenStreetMap
  (Overpass API), and the simulated workflow panel with **Review event** / **Confirm emergency action**.
- The former Events and System pages were removed from the visitor-facing UI; their APIs remain
  (`/api/demos/<name>/events`, `/api/system`, `/api/system/run-tests`) for tooling and the presentation build.
- Dark / light theme, responsive from phone to desktop.

## 16. Accident detection and emergency response (DEMO / SIMULATION)

`accident.py` · `AccidentDetector` is deliberately conservative:

- primary cues: `IMPACT` (in-corridor object < 2 m, closing fast **and tracked approaching ≥ 1 m within 1.5 s**),
  `OBJ_COLLISION` (two road users newly overlapping at similar depth after converging), `DISRUPTION` (abrupt box-shape
  change of a nearby vehicle, ignoring frame-edge truncation and occlusion); a primary cue must persist ≥ 3 frames in
  0.5 s → `POSSIBLE`;
- supporting cues: `JOLT` (frame-difference spike), `EGO_STOP` (abrupt stop);
- `CONFIRMED` needs post-collision stillness in the world frame within 3 s, confidence ≥ 0.7 **and** physical evidence
  (impact / jolt / abrupt stop) or two visual cues while the ego vehicle moves. Ego creeping to a halt in congestion is
  not evidence. CONFIRMED stays latched until reset.

`EmergencyService` — `detect_accident`, `get_location`, `find_nearby_hospital`, `prepare_emergency_message`,
`request_user_confirmation`, `send_notification` — runs in **demo mode with a simulated notifier**: it shows
*ACCIDENT DETECTED*, *SIMULATED SMS SENT*, the nearest hospital and *AMBULANCE REQUEST — SIMULATION*. **Nothing is
sent and nobody is called**; a real provider would need authorisation, credentials outside Git and explicit user
confirmation. In a real emergency call 112.

Real-footage check: on a public-domain dashcam rear-end clip (Wikimedia Commons, first 30 s) the detector did **not**
confirm the accident — in the last second before contact the cutting-in car fills the frame, heavily motion-blurred,
and YOLOv8n no longer detects it (confidence < 0.2). The web demo therefore uses a clearly labelled demo trigger;
the accident logic itself is covered by ACC1–ACC12.

A second public-domain clip (multi-car rear-end collision on a highway, seen from a following car) is not confirmed
either: the crash is between third-party vehicles far ahead; PathSense shows NO SAFE PATH while approaching the stopped
vehicles. Both clips are on Wikimedia Commons, tagged public domain ("CCTV footage does not create a copyright"):
[`The van in front suddenly changed lanes…`](https://commons.wikimedia.org/wiki/File:The_van_in_front_suddenly_changed_lanes_and_caused_the_vehicle_to_rear-end_collision.webm)
(processed with `--max-frames 900`, the first 30 s) and
[`Multiple cars rear-end collision on highway`](https://commons.wikimedia.org/wiki/File:Multiple_cars_rear-end_collision_on_highway.webm).
Across the six Indian validation drives the detector produced no false accident confirmation.

## 17. SIH presentation

`PathSense_SIH_Final_Presentation.pptx` (+ `.pdf`) is the SIH idea deck, built on the **official SIH FINAL FORMAT
template** (10 slides: title page, idea/proposed solution, technical approach, prototype, proof of concept, feasibility and
viability, impact and benefits, business model canvas, national scheme alignments, research and references). The SIH
branding, headers, footer, logo and title-page structure are kept; the official pointer texts appear as section
sub-labels. Topic organisation follows the team's reference deck; the visual treatment is PathSense's own. The
Prototype slide uses real frames from the processed demo videos (`presentation/extract_frames.py`, CC0 footage by
L. Shyamal); no dashboard/website screenshots and no benchmark numbers appear anywhere.
Build: `python presentation/build_presentation.py "<path to SIH FINAL FORMAT.pptx>"`. Concept renders:
`node presentation/hero/render_hero.mjs`; logos: `presentation/make_logos.js`. Icons: `presentation/make_icons.js` (react-icons, MIT).

