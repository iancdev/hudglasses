# remote-demo

Scripts to connect to an Android device over **wireless ADB** and stream the screen to a desktop for demos.

## Prereqs
- `adb` (Android Platform Tools)
- Recommended: `scrcpy` (best low-latency mirroring over ADB)

Verify:
```bash
adb version
scrcpy --version
```

If `scrcpy` is missing on macOS:
```bash
brew install scrcpy
```

## Setup (wireless ADB)
You said the device is already paired, so usually you only need to connect:

```bash
export PHONE_IP="192.168.x.y"
export ADB_PORT="5555" # or the port shown in Android “Wireless debugging”
./remote-demo/bin/adb_connect.sh
```

If you already have an ADB serial (like `192.168.x.y:5555`), set:
```bash
export ADB_SERIAL="192.168.x.y:5555"
```

## Stream the screen
```bash
./remote-demo/bin/stream.sh
```

To stream the AR/external display, first list available displays:
```bash
./remote-demo/bin/list_displays.sh
```

Then pick the display id (often `1` for a second display, but it varies):
```bash
export DISPLAY_ID=1
./remote-demo/bin/stream.sh
```

Tuning knobs:
```bash
export FPS=60
export BITRATE=16M
export WINDOW_TITLE="HUD Glasses Demo"
./remote-demo/bin/stream.sh
```

## Record a demo video
```bash
export RECORD_PATH="remote-demo/out/demo.mkv"
./remote-demo/bin/record.sh
```

## Notes / troubleshooting
- If `adb devices -l` shows no device, re-run `./remote-demo/bin/adb_connect.sh`.
- On Android “Wireless debugging”, there are typically two different ports:
  - a **pairing** port, and
  - a **connect** port shown under “IP address & port”.
  Use the **connect** port with `adb connect`.
- If you’re trying to stream an **external display** (AR glasses), `scrcpy` may mirror only the primary display depending on device/OS. If your AR content is not visible, we may need to:
  - force the app to render on the primary display, or
  - use a scrcpy version/flag that supports non-default displays (varies by scrcpy version/device).
