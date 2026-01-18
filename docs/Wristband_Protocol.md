# Wristband Haptics Protocol (Hackathon)

This document defines what the wristband haptics device expects over **ESP-NOW**.

Goal: a wearable that can produce simple vibration patterns (left/right/front, alarms) when commanded by a sender device.

Protocol version: `v: 1`

## 1) Transport
ESP-NOW (2.4 GHz). Connectionless peer-to-peer packets.

Roles:
- **Sender**: ESP32 bridge (or other ESP-NOW-capable device).
- **Receiver**: wristband device.

Android does not speak ESP-NOW directly; if Android is the source of events, it must route through a bridge (out of scope here).

## 2) Peer Setup (Required vs Optional)
Required:
- Receiver is configured to accept ESP-NOW packets on a fixed Wi‑Fi channel.
- Sender is configured to use the same channel.
- Receiver has the sender’s MAC address added as a peer.

Optional (security):
- Use a PMK/LMK pair if you want encryption. If used, both sides must share the same keys.

Default channel recommendation (can be changed by agreement): `1`

## 3) Packet Format (v1)
ESP-NOW payload is variable length. The **required** command payload is 5 bytes (little endian):

| Byte | Name | Type | Notes |
|---:|---|---|---|
| 0 | `v` | `uint8` | Protocol version (`1`) |
| 1 | `patternId` | `uint8` | Pattern selector |
| 2 | `intensity` | `uint8` | 0..255 |
| 3..4 | `durationMs` | `uint16_le` | 0..65535 |

Optional trailer (if present, receiver should ignore extra bytes it doesn’t understand):
- `seq` (`uint8`): monotonically increasing sequence number for de-duplication.

### Pattern IDs used by the app
- `1`: direction left
- `2`: direction right
- `10`: fire alarm
- `11`: car horn
- `20`: keyword / phrase alert

You can support more IDs as desired; unknown IDs may be ignored.

## 4) Behavior Expectations
- Receiving a command should immediately start (or restart) the specified pattern for `durationMs`.
- Sender should **repeat** each command 2–3 times (short spacing) to improve reliability.
- Receiver should handle quick bursts and may drop duplicate commands (use `seq` if provided).
- If no command arrives for a while, the wristband should stop vibrating (no autonomous vibration).

## 5) Notes / Open Items
- If you prefer a different channel or MAC pairing strategy, coordinate with the sender firmware.
- Battery reporting can be added later as a separate ESP-NOW message type (optional).
