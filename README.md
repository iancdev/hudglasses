# HUD Glasses (Hackathon)

Goal: provide Deaf users with **silent, wearable awareness** by turning audio into:
- **subtitles** (speech → text),
- **direction/intensity** (radar + edge glow),
- **danger alerts** (fire alarm, car horn),
- **haptics** (wristband).

Docs:
- Project overview: `docs/Draft.md` (takes precedence), `docs/PRD.MD`, `docs/PRD_Enhanced.MD`
- Server plan: `docs/implementation_plan.md`
- ESP32 audio protocol: `docs/ESP32_Protocol.md`
- Wristband BLE protocol: `docs/Wristband_Protocol.md`

## Quickstart (Demo)
1) Start the laptop server:
   - See `server/README.md`
2) Stream audio from ESP32s:
   - Implement `docs/ESP32_Protocol.md`, or simulate via `server/tools/esp32_sim.py`
3) Run the Android app:
   - See `android/README.md`
   - Connect the phone + laptop to the same Wi‑Fi / hotspot.
   - Set server URL to `ws://<laptop-ip>:8765` and press Connect.

## Build + Install Android (CLI)
This repo has two Android product flavors:
- `nosdk` (no Viture SDK)
- `viture` (uses Viture SDK AAR for IMU/head tracking)

Prereqs:
- JDK 17 (recommended: `brew install openjdk@17`)
- Android SDK (Platform 34 + Build-Tools + Platform-Tools)
- For `viture`: `android/app/libs/VITURE-SDK-1.0.7.aar` present (not committed)

Commands:
- `export JAVA_HOME="/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"`
- `export ANDROID_SDK_ROOT="$HOME/Library/Android/sdk"`
- `cd android`
- `cat > local.properties <<EOF
sdk.dir=$ANDROID_SDK_ROOT
EOF`
- `./gradlew :app:installNosdkDebug`
- `./gradlew :app:installVitureDebug`

## Debug Crash Logs (Android)
- `adb logcat -c`
- `adb shell am start -n dev.iancdev.hudglasses/.RemoteActivity`
- `adb logcat | rg -n \"FATAL EXCEPTION|AndroidRuntime|dev\\.iancdev\\.hudglasses\"`
