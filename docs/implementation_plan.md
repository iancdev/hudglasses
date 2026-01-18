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
  - Ingests **ESP32 audio streams** (left/right), runs sound classification, computes direction/intensity, and runs ElevenLabs realtime STT.
- **Android app = HUD client + phone remote**
  - Renders the HUD on the **Viture external display only** (black background, landscape).
  - Uses the **phone screen as a remote UI** (settings, connection state, debug/calibration).
  - Uses Viture SDK head tracking (IMU).
  - Haptics:
    - phone vibration fallback (demo-friendly), and/or
    - wristband haptics via an ESP‑NOW bridge (Android does not speak ESP‑NOW directly).
  - Electrochromic lens control: keep as a requirement from PRD, but **the Viture Android SDK v1.0.7 we reviewed only exposes IMU + 2D/3D mode** (no explicit electrochromic API found), so treat this as **“if supported by SDK/device”**.
- **2× ESP32 = microphone streamers (Phase 1: required)**
  - Each ESP32 streams microphone audio to the laptop (`role = left|right`).
  - ESP32 does **no processing** (no direction inference, no classification, no STT).

Networking:
- Android + laptop on same network (phone hotspot OK).
- ESP32s stream audio to the laptop (see `docs/ESP32_Protocol.md`).
- **Two WebSocket channels (laptop → Android)**:
  - **`/stt`**: dedicated speech-to-text channel (transcript down) for maximum subtitle responsiveness.
  - **`/events`**: all non-STT events (radar placement, edge glow, alarms, status, config).

## 2) MVP Features (Phase 1 — Demo Must-Haves)
### 2.1 HUD (Android/Viture)
- HUD is rendered on the **Viture external display only** (phone screen is not the HUD).
- Viture display: fullscreen black background, landscape orientation.
- Phone screen: remote UI with large controls and visible state (no requirement to be black).
- Viture SDK init (from SDK guide):
  - Use `ArManager.getInstance(context)`, `registerCallback(ArCallback)`, `init()` (requests USB permission).
  - Wait for init result via `ArCallback.onEvent(Constants.EVENT_ID_INIT, ...)`.
  - Enable IMU stream via `ArManager.setImuOn(true)` and optionally set rate via `ArManager.setImuFrequency(...)`.
  - Read head pose from `ArCallback.onImu(...)` (Euler yaw/pitch/roll).
  - Release resources on exit via `ArManager.release()` and `unregisterCallback(...)`.
  - Optional: set 2D/3D mode via `ArManager.set3D(...)` if needed for the glasses mode.
- HUD widgets:
  - **Subtitles panel**: shows partial and final transcript updates in realtime.
  - **Radar**: shows detected sound direction + intensity.
  - **Edge glow**: glows toward sound direction; color-coded by event type:
    - Speech: neutral/white (or cyan).
    - Fire alarm: red.
    - Car horn: yellow.
- Visible connection indicators:
  - Laptop server connected/disconnected.
  - ESP32 audio streaming connected/disconnected.
  - STT active/inactive/error.
  - Wristband connected/disconnected.

### 2.1.1 Viture-Only UI + Phone Remote (Implementation Approach)
Android multi-display plan (standard Android; separate from the Viture SDK):
- Detect external display via `DisplayManager` (e.g., `DISPLAY_CATEGORY_PRESENTATION`).
- Render HUD on that display using either:
  - a `Presentation` attached to the Viture `Display`, or
  - an Activity launched onto the Viture `Display` (via `ActivityOptions.setLaunchDisplayId(...)`).
- Keep the phone UI in a normal Activity on the default display as a remote controller.
- Recommended ownership split:
  - Phone remote Activity (or a bound Service) owns network sockets + Viture SDK (USB permission + IMU).
  - HUD display is “dumb rendering”: it consumes server events and draws.
- If the glasses disconnect:
  - show a “Glasses disconnected” state on the phone remote,
  - optionally fall back to a HUD preview on the phone for debugging.

### 2.2 Speech-to-Text (STT)
- **Android mic is the default STT input** (Phase 1), so the demo works even before ESP32 firmware is ready.
- Keep the ability to switch later to ESP32 STT input (optional) without changing the Android UI.
- Laptop server connects to **ElevenLabs realtime STT** (WebSocket) and streams audio chunks from the ESP32 audio stream.
- Subtitles should update as fast as possible:
  - Forward ElevenLabs `partial_transcript` updates immediately.
  - Forward `committed_transcript` as final lines.
- Optional “per-word” update (if partials are too chunky):
  - Server computes a word-diff delta from successive partial transcripts and emits incremental updates to Android.

### 2.3 Direction + Intensity (Derived on Server from ESP32 Audio)
- Laptop server derives direction/intensity from the left/right ESP32 audio streams:
  - Compute per-stream energy (e.g., RMS) in short windows (20–50ms).
  - Infer coarse direction from left/right balance (good enough for demo).
- Server sends **UI-ready placement** to Android over `/events`, for example:
  - radar position (`radarX`, `radarY` normalized) and color
  - edge glow target (`glowEdge`) and strength (`glowStrength`)
- Android renders what the server sends (keeps Android simple and consistent with the “server is the brain” architecture).

### 2.4 Danger Sound Detection
- Laptop server runs sound classification on the ESP32 audio stream.
- Detect and emit at minimum one class reliably; target both:
  - `alarm.fire`
  - `alarm.car_horn`
- PRD behavior rules:
  - Fire alarm: glow **RED**; after **10 seconds** of no longer detecting, return to normal.
  - Car horn: show **YELLOW** direction on radar.

### 2.5 Wristband Haptics
- Wristband haptics use ESP-NOW (sender bridge → wristband).
- Android does not speak ESP-NOW directly; if Android is the source, route through an ESP32 bridge.
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

**Server → Android (transcript downstream)**
- Messages include:
  - `partial`: latest partial text
    - optional `deltaWords`: list of newly appended words (best-effort)
  - `final`: committed/final text
  - `status`: connection/session status (e.g., `stt=connected`, `stt=session_started`)
  - `error`: STT errors (auth/quota/rate-limit/etc.)

**Android → Server (optional, for phone-mic STT input)**
- `audio.hello` (JSON) then binary audio frames:
  - audio format: `pcm_s16le`
  - sample rate: `16000 Hz`
  - channels: `1`
  - frame size: `~20ms` recommended (but not strictly required)

### 5.2 `/events` (All other events)
Server → Android:
- `direction.ui` (server-derived UI placement: radar coordinates + edge glow)
- `alarm.fire` / `alarm.car_horn` (state + confidence + UI placement)
- `status` (connections, errors, server state)
- `alert.keyword` (Phase 2)

Android → Server:
- `hello` (client info)
- `config.update` (thresholds, toggles, keyword list)
- `head_pose` (optional but recommended: head yaw so the server can make UI placement head-relative)
- `audio.source` (select STT input: `auto|android_mic|esp32`)

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
  - `/stt`: forward partial/final transcripts to Android (dedicated channel).
  - `/events`: broadcast HUD events (radar placement, edge glow, alarms, status); receive config updates and optional head pose.
- Ingest ESP32 audio streams (left/right) (see `docs/ESP32_Protocol.md`).
- Run danger-sound classifier continuously on audio stream.
- Derive direction/intensity from left/right audio and attach UI placement to events.
- Stream STT audio to ElevenLabs and relay transcript updates to Android.

Keep the server simple:
- One process.
- A small in-memory state store:
  - latest direction/intensity
  - active alarms (with timers, including fire 10s hold)
  - current STT session state
- Log errors + connection state for quick debugging during demo.

### 7.2 Android App (Kotlin + Viture SDK)
Responsibilities:
- Render HUD on Viture display only:
  - subtitles, radar, edge glow, status indicators.
- Render phone remote UI:
  - connection state, toggles, thresholds, keyword list (Phase 2), debug.
- Connect to server:
  - WebSocket `/stt` for transcripts
  - WebSocket `/events` for direction/alarms/status/config
- Head tracking:
  - Use Viture SDK (`ArManager` / `ArCallback.onImu`) to read yaw/pitch/roll.
  - Send `head_pose` to the server (recommended) so server-generated UI placement stays accurate as the user turns.
  - Wristband:
  - ESP-NOW sender bridge + send haptic patterns.

### 7.3 ESP32 Devices (Audio streamers)
Responsibilities:
- Capture microphone audio.
- Stream audio frames continuously to laptop (left/right roles).
- Keep provisioning dead simple for hackathon (hardcoded Wi‑Fi or one-time setup).

### 7.4 Wristband Device
Responsibilities:
- ESP-NOW receiver for simple haptic commands:
  - `{ v, patternId, intensity, durationMs }` (see `docs/Wristband_Protocol.md`).

## 8) Milestones (Hackathon Execution Order)
1) **HUD shell on Android/Viture**
   - Multi-display: HUD on Viture external display only + phone remote UI on handset screen.
   - Black background, landscape on Viture; lens off on init (if supported); basic UI layout.
2) **Networking scaffolding**
   - `/events` connect + status indicator + reconnect loop.
3) **ESP32 audio ingestion**
   - ESP32 audio streaming → server ingestion + basic health/status events.
4) **STT channel**
   - ESP32 audio → ElevenLabs realtime STT → `/stt` partial/final subtitles rendering.
5) **Direction UI events**
   - Server derives direction/intensity → emits UI placement → radar + edge glow.
6) **Danger sounds**
   - Fire alarm + car horn detection → alarms on HUD (incl. fire 10s hold).
7) **Wristband haptics**
   - Directional patterns tied to speech + alarm events.
8) **Phase 2: keyword/phrase detection**
   - User-configured phrases → `alert.keyword` → HUD + distinct haptics.

## 9) Key Decisions Locked In (Per your clarifications)
- Draft is the source of truth; PRDs supplement it.
- Phase 1 audio input: **ESP32 audio streams (left/right)**.
- Phase 1 processing location: **server does everything** (direction, classification, STT).
- Phase 2: **user-configurable keyword/phrase detection**.
- Out of scope: sign language interpretation.
- **Separate STT socket** from events socket to prioritize subtitle latency.
