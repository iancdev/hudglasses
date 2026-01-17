# HUD Glasses for the Deaf (Hackathon MVP / Proof of Concept)
The HUD Glasses project is a production-ready application for Android, designed to work for Viture Pro XR Glasses (connected to an Android device via USB-C), using its SDKs, to provide speech-to-text in the form of subtitles, a radar using microphones to pinpoint speaker location, and ElevenLabs API for the speech-to-text service.

Refer to `Draft.md`.

## Recommendation (for Hackathon MVP)
To ship an MVP as fast as possible for a demo:
- Use **native Android** for the HUD + Viture integration (rendering, head tracking, electrochromic lens control).
- Offload audio processing (STT + sound classification + fusion logic) to a **computer (PC) server**, and treat the Android app as a **client** that renders the HUD and triggers haptics.

This keeps the Android side simple while still working with the Viture glasses (USB-C to Android), and lets us iterate quickly on the PC.

## Architecture (Hackathon Mode)
**Data flow**
1) **Audio source** :
   - Android Mic (Primary)
   - ESP32 Microphones (2 seperate devices for directional, so combine for processing) (implement later, have an optoin to switch to this but have android mic as primary)
2) **PC server**:
   - Sound classification (speaking / fire alarm / car horn)
   - Speech-to-text via ElevenLabs when speech is detected
   - Direction/intensity fusion (if ESP32 provides direction/intensity)
   - Sends simple events to Android over local Wi‑Fi (WebSocket)
3) **Android app (HUD client)**:
   - Displays the HUD on the Viture glasses (black background, landscape)
   - Uses Viture SDK (head tracking; electrochromic lens off on init)
   - Displays subtitles + radar + edge glow
   - Triggers haptics on the wristband (via BLE or via microcontroller bridge)

## Tech Stack
Creating a native Android application using Kotlin and the Viture SDK, we can interact with the glasses natively.
- Viture SDK (Android)
- Native Android (Kotlin; UI can be Compose)
- PC Server (Python or Node.js) for hackathon iteration speed
- WebSocket (PC → Android) for realtime HUD events
- ESP32 (2 microphones connected to 2 separate ESP32, which becomes a data source for directional audio detection)
- ElevenLabs API for speech to text (live)

For production (post-hackathon), we can migrate more of the processing onto the Android device, but for MVP we keep the PC as the “brain”.

## Setup
- Viture Pro XR glasses displays the HUD, connects to an Android device via USB-C to display the HUD.
- The Android device displays a black background in horizontal orientation to ensure HUD display shows up on the glasses like AR. The Android device’s function is to display what would be shown on the glasses.
- Using the Viture SDK, the application sends a command on initialization to turn off the electrochromic lens.
- The target device is Pixel 8a.
- The user wears a wristband that emits haptic feedback to show which direction detected audio is coming from.

**Networking (Hackathon Mode)**
- Android and PC are on the same local network (phone hotspot is fine).
- PC runs a WebSocket server; Android connects as a client and receives events.
- ESP32s send either:
  - direction/intensity events to the PC (preferred for MVP), or
  - audio stream to the PC (only if needed for POC).

## Detected Audios
- Speaking
- Fire Alarm
- Car Horn

## Detected Audio Behavior
- Speaking would activate haptics, show on the radar, and uses speech to text using ElevenLabs API to show who is speaking, and what they're saying.
- Fire Alarm would cause the HUD to glow RED and warn the user that there is a current fire alarm. After 10 seconds of no longer detecting the fire alarm audio, the HUD returns to normal. The radar shows detected location here as well.
- Car horn, the Radar would show yellow for which direction the car horn is coming from.

## HUD behavior
The HUD proactively, and passively detects for speech (via PC server in hackathon mode), and activates the ElevenLabs API for speech to text when audio is detected.

Using the head tracking API from Viture SDK to show the accurate relative location of where the audio is coming from. The edges of the screen glows based on where the audio is coming from.

## Event Contract (PC → Android)
Keep messages extremely small and UI-focused for MVP.
For speech detection, using ElevenLabs API streaming, we should have a seperate protocol to ensure that each word is streamed as soon as possible.
Example WebSocket event (JSON):
```json
{
  "type": "speech",
  "directionDeg": 45,
  "intensity": 0.72,
  "speakerLabel": "Someone",
  "text": "Hello, can you help me?",
  "timestampMs": 1730000000000
}
```

Other event types:
- `alarm.fire`
- `alarm.car_horn`
- `status` (server connected / disconnected)

## MVP Scope (Hackathon)
- Show subtitles on the HUD in realtime (from PC → Android).
- Show radar direction + edge glow for detected audio direction.
- Trigger wristband haptics on direction change / active speech.
- Detect at least one dangerous sound reliably (fire alarm or car horn).


