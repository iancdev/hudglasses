# HUD Glasses for the Deaf — Hackathon Implementation Plan

This plan is **hackathon demo–ready** (not “production hardened”), and is written to align with the current docs with **`docs/Draft.md` taking precedence**.

## 0) Project Goal (What we’re building)
Provide a silent, wearable way for Deaf users to understand what’s happening around them by converting ambient audio into:
- **Subtitles** (speech → text) on a HUD (Viture Pro XR via Android).
- **Direction + intensity** cues (radar + edge glow) so users can see where sounds come from.
- **Haptic feedback** on a wristband to reinforce direction/urgency.
- **Danger sound detection** (fire alarm, car horn) with strong visual + haptic alerts.

## 1) Hackathon Constraints + Accepted Architecture
Accepted hackathon architecture:
- **Laptop = “brain” server**
  - Runs sound classification, fuses ESP32 direction telemetry, and runs ElevenLabs realtime STT.
- **Android app = HUD client**
  - Renders HUD on Viture glasses (black background, landscape), uses head tracking, turns off electrochromic lens on init, drives wristband haptics.
- **2× ESP32 = directional microphones (Phase 1: direction only)**
  - Each ESP32 provides direction/intensity telemetry to the laptop (preferred).
  - **Phase 3 / later option**: ESP32 audio may become an STT input source (selectable), but **Phase 1 uses Android mic for STT**.

Networking:
- Android + laptop on same network (phone hotspot OK).
- **Two WebSocket channels**:
  - **`/stt`**: dedicated speech-to-text channel (audio up, transcript down) for maximum subtitle responsiveness.
  - **`/events`**: all non-STT events (direction, alarms, status, config).

## 2) MVP Features (Phase 1 — Demo Must-Haves)
### 2.1 HUD (Android/Viture)
- Fullscreen black background, landscape orientation.
- Viture SDK init:
  - Disable electrochromic lens on app start.
  - Enable head tracking to stabilize direction indicators relative to head orientation.
- HUD widgets:
  - **Subtitles panel**: shows partial and final transcript updates in realtime.
  - **Radar**: shows detected sound direction + intensity.
  - **Edge glow**: glows toward sound direction; color-coded by event type:
    - Speech: neutral/white (or cyan).
    - Fire alarm: red.
    - Car horn: yellow.
- Visible connection indicators:
  - Laptop server connected/disconnected.
  - ESP32 direction telemetry connected/disconnected.
  - STT active/inactive/error.
  - Wristband connected/disconnected.

### 2.2 Speech-to-Text (STT)
- **Android mic is the default STT input**.
- Laptop server connects to **ElevenLabs realtime STT** (WebSocket) and streams audio chunks from Android.
- Subtitles should update as fast as possible:
  - Forward ElevenLabs `partial_transcript` updates immediately.
  - Forward `committed_transcript` as final lines.
- Optional “per-word” update (if partials are too chunky):
  - Server computes a word-diff delta from successive partial transcripts and emits incremental updates to Android.

### 2.3 Direction + Intensity (ESP32 direction-only)
- Two ESP32 devices (left/right) send telemetry to laptop:
  - Example: `{ deviceId, tsMs, rms }` at ~20–50Hz.
- Laptop fuses telemetry into:
  - `directionDeg` (coarse is fine for demo: left/front/right mapped to angles)
  - `intensity` (0..1 normalized)
- Android uses direction+intensity for radar placement, edge glow strength, and wristband haptics.

### 2.4 Danger Sound Detection
- Laptop server runs sound classification on the audio stream (Phase 1: use Android mic PCM stream).
- Detect and emit at minimum one class reliably; target both:
  - `alarm.fire`
  - `alarm.car_horn`
- PRD behavior rules:
  - Fire alarm: glow **RED**; after **10 seconds** of no longer detecting, return to normal.
  - Car horn: show **YELLOW** direction on radar.

### 2.5 Wristband Haptics
- Android connects to wristband via BLE (or via a microcontroller bridge if needed).
- Haptics encode direction (and optionally intensity):
  - Left/right/front patterns (simple pulses) + rate limiting (avoid constant buzzing).
- Fallback (optional): phone vibration when wristband not connected (helps demo resiliency).

## 3) Phase 2 Feature (After Phase 1 Works): User-Configurable Keyword/Phrase Detection
Per your latest clarification:
- **Phase 2**: add keyword/phrase detection that the user can set.
- **Approach (fast + reliable for hackathon): transcript-based matching**
  - On server, watch the transcript stream (partial + committed).
  - When a keyword/phrase matches, emit `alert.keyword` over `/events` and trigger a distinct haptic pattern.
  - Add cooldown (e.g., 5–10s) per phrase to prevent spam.

Android UI for Phase 2:
- Simple settings screen to add/remove keywords/phrases.
- Send config to server on `/events` (`config.update` message).

Notes:
- This avoids the complexity of audio keyword spotting models for hackathon.
- If “yelling ‘look out’” is desired specifically, include `"look out"` as a default phrase.

## 4) Explicit Out of Scope (Per `docs/Draft.md` + your clarifications)
- Sign language to speech interpretation.

## 5) Networking + Protocol Plan (Two WebSockets)
### 5.1 `/stt` (Dedicated STT channel)
Purpose: keep subtitle latency as low as possible and avoid contention with event traffic.

**Android → Server (audio upstream)**
- Stream PCM frames (recommended internal format: mono 16kHz, 16-bit).
- If we keep it JSON for hackathon simplicity, send base64 audio frames; otherwise send binary frames with a small header.

**Server → Android (transcript downstream)**
- Messages include:
  - `partial`: latest partial text
  - `final`: committed/final text
  - optional `deltaWords`: words appended since last update (Phase 1.5 if needed)

### 5.2 `/events` (All other events)
Server → Android:
- `direction.update` (directionDeg + intensity, at ~10–30Hz)
- `alarm.fire` / `alarm.car_horn` (state + confidence + directionDeg + intensity)
- `status` (connections, errors, server state)
- `alert.keyword` (Phase 2)

Android → Server:
- `hello` (client info)
- `config.update` (thresholds, toggles, keyword list)
- `audio.source` (future: Android vs ESP32 STT selector; Phase 1 defaults to Android)

## 6) ElevenLabs STT Integration (Server-Side)
Docs referenced:
- `docs/ElevenLabsSTT.MD` (realtime STT WebSocket)
- `docs/ElevenLabsAuth.mD` (API key + token options)

Implementation notes (from docs):
- Connect to ElevenLabs realtime STT endpoint:
  - `wss://api.elevenlabs.io/v1/speech-to-text/realtime`
- Auth:
  - Use `xi-api-key` header on the server (do not ship API keys on Android).
- Audio messages:
  - Send `message_type: "input_audio_chunk"` with base64 audio payload.
- Latency strategy:
  - Use `commit_strategy=vad` initially to avoid building custom VAD for hackathon.
  - Forward `partial_transcript` immediately to Android `/stt`.

## 7) Component Implementation Details
### 7.1 Laptop Server (Python or Node.js)
Responsibilities:
- Host WebSockets:
  - `/stt`: receive Android audio stream; proxy to ElevenLabs STT; forward partial/final transcripts to Android.
  - `/events`: broadcast direction + alarms + status; receive config updates.
- Ingest ESP32 telemetry (UDP preferred).
- Run danger-sound classifier continuously on audio stream.
- Fuse direction telemetry with current “active audio event” windows (speech + alarms).

Keep the server simple:
- One process.
- A small in-memory state store:
  - latest direction/intensity
  - active alarms (with timers, including fire 10s hold)
  - current STT session state
- Log errors + connection state for quick debugging during demo.

### 7.2 Android App (Kotlin + Viture SDK)
Responsibilities:
- Render HUD:
  - subtitles, radar, edge glow, status indicators.
- Connect to server:
  - WebSocket `/stt` for audio+transcripts
  - WebSocket `/events` for direction/alarms/status/config
- Microphone capture:
  - `AudioRecord` stream to `/stt`.
- Head tracking:
  - Use Viture head pose to keep direction cues consistent as user turns head.
- Wristband:
  - BLE connect + write haptic patterns.

### 7.3 ESP32 Devices (Direction-only)
Responsibilities:
- Sample mic amplitude and compute RMS/intensity.
- Send telemetry packets to laptop at fixed rate.
- Keep provisioning dead simple for hackathon (hardcoded Wi‑Fi or one-time setup).

### 7.4 Wristband Device
Responsibilities:
- BLE peripheral receiving simple haptic commands:
  - `{ pattern, intensity, durationMs }` (actual GATT UUID/payload to be defined with hardware team).

## 8) Milestones (Hackathon Execution Order)
1) **HUD shell on Android/Viture**
   - Black background, landscape, lens off on init, basic UI layout.
2) **Networking scaffolding**
   - `/events` connect + status indicator + reconnect loop.
3) **STT channel**
   - `/stt` audio stream + ElevenLabs realtime STT + partial/final subtitles rendering.
4) **ESP32 direction ingestion**
   - Telemetry → direction.update events → radar + edge glow.
5) **Danger sounds**
   - Fire alarm + car horn detection → alarms on HUD (incl. fire 10s hold).
6) **Wristband haptics**
   - Directional patterns tied to speech + alarm events.
7) **Phase 2: keyword/phrase detection**
   - User-configured phrases → `alert.keyword` → HUD + distinct haptics.

## 9) Key Decisions Locked In (Per your clarifications)
- Draft is the source of truth; PRDs supplement it.
- Phase 1 STT input: **Android mic**.
- Phase 1 direction input: **ESP32 direction/intensity only**.
- Phase 2: **user-configurable keyword/phrase detection**.
- Out of scope: sign language interpretation.
- **Separate STT socket** from events socket to prioritize subtitle latency.

