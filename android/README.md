# HUD Glasses Android App (Hackathon)

This Android app:
- renders the **HUD on the Viture external display only** (black background, landscape),
- uses the **phone screen as a remote** (server URL, status, debug),
- connects to the laptop server websockets:
  - `/events` for direction/alarm/status
  - `/stt` for transcripts
- triggers **phone vibration** as a haptics fallback (wristband haptics are via ESP‑NOW bridge; see `docs/Wristband_Protocol.md`)

## Setup
1) Open `android/` in Android Studio.
2) Ensure you have the Viture SDK AAR:
   - Option A (recommended): copy from this repo’s `VitureSDK/aar/VITURE-SDK-1.0.7.aar`
   - Option B: download `viture_android_sdk_v1.0.7.tar.xz` and extract `aar/VITURE-SDK-1.0.7.aar`
   - Place it at `android/app/libs/VITURE-SDK-1.0.7.aar` (not committed)
3) Connect Viture glasses via USB‑C.

## Build Variants
This project has 2 product flavors:
- `nosdk` (default for CI/CLI): builds without the Viture SDK; IMU/head tracking is disabled.
- `viture`: builds with the Viture SDK AAR; enables IMU/head tracking.

## Run
- Launch the app on the phone (Pixel 8a target).
- The phone UI is a remote controller.
- The HUD should appear on the Viture display when detected as an external display.
- The remote includes Viture controls (IMU on/off, 3D on/off, IMU frequency, “HUD Defaults”).

## CLI Build (no Android Studio)
Prereqs:
- JDK 17 (recommended: `brew install openjdk@17`)
- Android SDK Platform 34 + Build-Tools 34.0.0 + Platform-Tools

Commands:
- `export JAVA_HOME="/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"`
- `export ANDROID_SDK_ROOT="$HOME/Library/Android/sdk"`
- `cd android`
- `./gradlew :app:assembleNosdkDebug`
- `./gradlew :app:assembleVitureDebug` (requires the Viture AAR in `android/app/libs/`)
