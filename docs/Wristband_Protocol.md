# Wristband Haptics Protocol (Hackathon)

This document defines what the Android app expects from the **wristband haptics device**.

Goal: a wearable that can produce simple vibration patterns (left/right/front, alarms) when commanded by the phone.

## 1) Transport
BLE peripheral (wristband) + BLE central (Android phone).

## 2) Advertising
The wristband should advertise with a device name prefix (default in app):
- `HUD-Wristband`

The Android app scans and connects to the first device whose name starts with this prefix.

## 3) GATT
The Android app expects:
- A single **custom service UUID** (default):
  - `0000feed-0000-1000-8000-00805f9b34fb`
- A single **writable characteristic UUID** for commands (default):
  - `0000beef-0000-1000-8000-00805f9b34fb`

The characteristic should support Write With Response or Write Without Response (either is fine for hackathon).

## 4) Command Payload (v1)
Android writes exactly 4 bytes (little endian) to the command characteristic:

| Byte | Name | Type | Notes |
|---:|---|---|---|
| 0 | `patternId` | `uint8` | Pattern selector |
| 1 | `intensity` | `uint8` | 0..255 |
| 2..3 | `durationMs` | `uint16_le` | 0..65535 |

### Pattern IDs used by the app
- `1`: direction left
- `2`: direction right
- `10`: fire alarm
- `11`: car horn
- `20`: keyword / phrase alert

You can support more IDs as desired; unknown IDs may be ignored.

## 5) Behavior Expectations
- Receiving a command should immediately start (or restart) the specified pattern for `durationMs`.
- Repeated commands may arrive quickly; firmware should handle rapid updates gracefully.
- If no command arrives for a while, the wristband should stop vibrating (no autonomous vibration).

## 6) Notes / Open Items
- If you prefer different UUIDs, tell the Android team and we’ll update the app config fields.
- If you want to include battery reporting, we can add a read characteristic (optional).
