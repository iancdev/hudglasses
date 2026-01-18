# Localization + Head Tracking Update Plan

This document describes the plan to improve audio source localization and tracking using:
- **4 microphones** mounted at the **corners** of a trapezoid neckband (torso-fixed).
- **Head pose** from the Viture SDK (head-fixed).
- **Torso pose** from the phone IMU (neckband-fixed, phone mounted sideways on the back edge).

The key design constraint is that the **microphone array does not rotate with the head**. Therefore:
- Audio localization is computed in the **neckband/torso frame**.
- HUD rendering is computed in the **head frame** by applying a **head↔torso yaw** correction.

## Summary of Current State

### Inputs we already have
- **Front mics**: ESP32 `role=left|right` streaming PCM16 to `/esp32/audio`.
- **Back mics**: phone stereo audio frames to `/stt` (2ch) providing `last_rms_left/right`.
- **Head pose**: Viture IMU sends JSON on `/events`:
  - `type: "head_pose"`, with `yaw`, `pitch`, `roll` (degrees).

### Current direction logic (server)
- 2-mic direction uses RMS balance `(R-L)/(L+R)` mapped into a front arc (ESP32) or rear arc (phone stereo).
- 4-mic direction uses a simplified “quadrant” vector sum assuming a square layout.
- Radar “multi-source” dots are computed per frequency band outliers, but world-stabilization currently assumes the mic array rotates with head yaw.

## Desired Behavior
- Sources are estimated in the **torso frame** using 2 or 4 mics.
- When the user turns their head, dots should move appropriately on the HUD (head-relative) without the underlying tracks “jumping”.
- Multiple sources should remain stable over time via frequency clustering + temporal tracking.

## Geometry (Neckband Trapezoid)

Given (mm):
- Back edge (phone side): `154.30`
- Front edge (ESP32 side): `184.451`
- Side length: `132.846`

Assuming an isosceles trapezoid, depth is:
```text
depth = sqrt(side^2 - ((front-back)/2)^2) ≈ 131.99 mm
```

Corner mic positions in a neckband coordinate frame (X right, Y forward), using half-depth `d = depth/2`:
```text
Back-L   (-back/2,  -d)
Back-R   (+back/2,  -d)
Front-L  (-front/2, +d)
Front-R  (+front/2, +d)
```

These are “good enough” for an energy-based direction model; exact millimeter precision is not required for the demo.

## Phase 1 — Stream Torso Pose (Phone IMU) to Server

### Why
The neckband is torso-fixed while the HUD is head-fixed. We need head↔torso relative yaw to correctly render torso-estimated directions on the head display.

### Approach
- Use Android rotation-vector sensor to compute a **quaternion** (preferred over yaw-only because the neckband is tilted downward and the phone is mounted sideways).
- Also compute a torso yaw angle around gravity from the same rotation matrix for convenience/debugging.
- Send on `/events` at 30–60Hz (or throttled as needed).

### Message (`torso_pose`)
```json
{
  "type": "torso_pose",
  "v": 1,
  "yawDeg": 12.3,
  "q": { "x": 0.01, "y": 0.02, "z": 0.03, "w": 0.99 },
  "tMonotonicMs": 123456789
}
```

Server stores the latest torso pose and timestamp.

## Phase 2 — Add a “Calibrate” Button (IMU Alignment Only)

We do **not** need to calibrate phone stereo L/R mapping (it is confirmed correct).

### Purpose
Establish a neutral reference so we can compute head↔torso relative yaw without requiring absolute north alignment.

### Calibration interaction
- User stands still.
- User looks straight forward relative to torso.
- Press **Calibrate**.

### Message (`calibrate.pose_zero`)
```json
{ "type": "calibrate.pose_zero", "v": 1 }
```

### Server calibration state
On receipt, server snapshots:
- `head0_yaw_deg` from the latest `head_pose`
- `torso0_yaw_deg` from the latest `torso_pose`

After this, compute:
```text
head_rel  = wrap(head_yaw - head0_yaw)
torso_rel = wrap(torso_yaw - torso0_yaw)
delta_yaw = wrap(head_rel - torso_rel)  # head rotation relative to torso
```

## Phase 3 — Compute Direction in the Torso Frame Using Geometry

### 4-mic direction (torso frame)
Use energy-weighted centroid of the 4 corner mics:
```text
E_i = RMS_i^2   (or band-power for radar dots)
v   = Σ(E_i * p_i)
dir_torso = atan2(v_x, v_y)
```

This improves “side” accuracy vs the current 45° quadrant assumption and uses the known trapezoid layout.

### 2-mic fallback
When only a front or back pair is available, keep the existing balance-based mapping:
- Front: map into `[-90°, +90°]`
- Back: map into rear arc around `180°` (existing shaped balance)

## Phase 4 — Render in the Head Frame (Head Tracking Integration)

Everything computed above is torso-relative. Convert to head-relative for UI:
```text
dir_head = wrap(dir_torso - delta_yaw)
```

Use `dir_head` to compute:
- `radarX`, `radarY`
- `glowEdge`, `glowStrength`

## Phase 5 — Multi-Source Tracking (Better Stability with IMU)

### Track in torso frame; render in head frame
- Keep radar tracks as `(freq_centroid, torso_direction, intensity)` and smooth in torso space.
- On each UI broadcast, compute head-relative direction using the latest `delta_yaw`.

This avoids “dots jumping” when the user turns their head.

### Confidence weighting
When updating tracks:
- confidence increases with outlier strength vs baseline and band energy
- higher confidence -> faster updates
- lower confidence -> heavier smoothing / stricter association gating

## Phase 6 — Calibration Beyond IMU (Recommended, Optional)

These are not strictly required to ship the feature, but improve quality significantly.

1) **Per-mic gain match**
- Play pink noise front-center and back-center.
- Match speech-band energy (300–3000 Hz) across the 4 channels.

2) **Coarse spectral normalization**
- Average spectrum per mic during pink noise.
- Normalize per mic so the “outlier vs baseline” detector is comparable across phone vs ESP32.

## Implementation Notes (Code Touch Points)

### Android
- Add a phone IMU controller that streams `torso_pose` on `/events`.
- Add a **Calibrate** button that sends `calibrate.pose_zero`.
- (Optional) increase Viture pose send rate (currently throttled to ~20Hz) if smoother head motion is needed.

### Server
- Accept and store `torso_pose`.
- Handle `calibrate.pose_zero` and store reference offsets.
- Update direction logic:
  - compute direction in torso frame (geometry-based)
  - convert to head frame using `delta_yaw`
- Update radar dot tracking:
  - track in torso space
  - convert to head space at broadcast time using current `delta_yaw`

## Acceptance Tests

1) Speaker at torso-forward (0°), head forward:
- Dot appears at top/forward.

2) Rotate head ±60° while speaker stays fixed:
- Dot shifts appropriately on HUD, no track hopping.

3) Two sources at different angles and/or different spectral peaks:
- Two stable dots with distinct colors (freq-based), minimal merging.

## Known Limitations
- This is still primarily ILD (amplitude-based) localization, not full TDOA triangulation.
- True “pinpoint” localization across devices would require tighter time sync (timestamps/sample counters).
