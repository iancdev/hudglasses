# HUD Glasses Android App (Hackathon)

This Android app:
- renders the **HUD on the Viture external display only** (black background, landscape),
- uses the **phone screen as a remote** (server URL, status, debug),
- connects to the laptop server websockets:
  - `/events` for direction/alarm/status
  - `/stt` for transcripts

## Setup
1) Open `android/` in Android Studio.
2) Ensure you have the Viture SDK AAR:
   - Download `viture_android_sdk_v1.0.7.tar.xz` and extract `aar/VITURE-SDK-1.0.7.aar`
   - Place it at `android/app/libs/VITURE-SDK-1.0.7.aar`
3) Connect Viture glasses via USB‑C.

## Run
- Launch the app on the phone (Pixel 8a target).
- The phone UI is a remote controller.
- The HUD should appear on the Viture display when detected as an external display.

