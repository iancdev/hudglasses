# ESP32 Audio Stream Protocol (Hackathon)

This document defines what the **laptop server** expects from the **ESP32 microphone nodes** during the hackathon demo.

Per the current architecture:
- ESP32’s **sole job** is to **stream microphone audio** to the laptop.
- The **laptop server does all processing** (direction, intensity, STT, danger sound classification).
- The **Android app is a client renderer** that receives server events and draws the HUD (radar position, edge glow, subtitles).

Non-goals (hackathon):
- Perfect localization / TDOA.
- Secure provisioning / encryption.
- OTA updates.

## 1) Devices and Roles
We will use **two ESP32 devices**:
- `role = "left"`
- `role = "right"`

Each device MUST have:
- A unique `deviceId` (string).
- A fixed `role` (hardcoded in firmware or set via config).

## 2) Network + Transport
### 2.1 Wi‑Fi
- ESP32 devices and the laptop server MUST be on the **same LAN** (phone hotspot is acceptable).

### 2.2 Transport (Phase 1: required)
**Recommended:** WebSocket (binary frames) for reliable, ordered delivery.
- ESP32 → laptop: WebSocket client connects to the laptop server endpoint (example):
  - `ws://SERVER_IP:SERVER_PORT/esp32/audio?deviceId=...&role=left|right`
- After connecting, ESP32 sends one JSON `hello` message, then streams **binary audio frames**.

Why WebSocket:
- Reliable delivery (better for STT than lossy UDP).
- Simple framing (each WebSocket binary message is one audio chunk).

**Acceptable alternate (hackathon-friendly): UDP + laptop bridge**
- ESP32 streams raw PCM16 mono over UDP to the laptop.
- A laptop bridge (`server/tools/udp_to_ws_bridge.py`) converts UDP → this same WebSocket protocol.
- Easiest way to differentiate two ESP32s: **two UDP ports**
  - left mic → UDP port `12345`
  - right mic → UDP port `12346`

This keeps ESP32 firmware very simple while allowing the server to continue using the stable `/esp32/audio` protocol.

### 2.3 Server Addressing / Provisioning
For hackathon simplicity, ESP32 firmware SHOULD support one of:
1) Hardcoded `SERVER_IP` and `SERVER_PORT` (fastest).
2) A simple serial/config update for `SERVER_IP` and `SERVER_PORT`.
3) Captive portal provisioning (nice-to-have).

Broadcast discovery MAY be attempted, but is not reliable on phone hotspots.

## 3) Protocol Messages
### 3.1 `hello` (required, JSON text frame)
Sent once immediately after connecting.

```json
{
  "v": 1,
  "type": "hello",
  "deviceId": "esp32-left-01",
  "role": "left",
  "fwVersion": "0.1.0",
  "audio": {
    "format": "pcm_s16le",
    "sampleRateHz": 16000,
    "channels": 1,
    "frameMs": 20
  }
}
```

Requirements:
- `audio.format` MUST be `pcm_s16le` for hackathon unless explicitly agreed otherwise.
- `channels` MUST be `1` (mono) per device.
- `frameMs` SHOULD be 20ms (10–40ms acceptable).

### 3.2 `audio` (required, binary frames)
After `hello`, ESP32 streams microphone audio as **WebSocket binary messages**.

Each binary message MUST be exactly one chunk of raw PCM:
- Format: **PCM signed 16‑bit little‑endian** (`pcm_s16le`)
- Channels: **1**
- Chunk duration: `frameMs` from the `hello` message

Example sizing at 16kHz, 20ms:
- samples per chunk: `16000 * 0.02 = 320`
- bytes per chunk: `320 * 2 = 640` bytes

If using UDP mode, each UDP datagram SHOULD be 640 bytes (20ms). If datagrams are larger/smaller, the bridge will re-chunk them into 640-byte frames.

The server will:
- Treat each binary frame as contiguous audio for that device/role.
- Buffer per‑device streams and compute:
  - direction/intensity (from left/right energy),
  - STT input audio (mix or select a channel),
  - danger sound classification.

### 3.3 `diag` (optional, JSON text frame)
Useful for field debugging.

```json
{
  "v": 1,
  "type": "diag",
  "deviceId": "esp32-left-01",
  "role": "left",
  "uptimeMs": 123456,
  "wifiRssiDbm": -58,
  "batteryMv": 4020,
  "droppedFrames": 0
}
```

## 4) Optional Server → ESP32 Messages (Nice-to-have)
Not required for the demo, but firmware may optionally support these JSON text frames:

### 4.1 `ack`
```json
{ "v": 1, "type": "ack", "ok": true }
```

### 4.2 `set_config`
Allows changing audio parameters without reflashing.
```json
{
  "v": 1,
  "type": "set_config",
  "audio": { "sampleRateHz": 16000, "frameMs": 20 }
}
```

If unsupported, ESP32 can ignore these messages.

## 5) Timing + Stability Requirements
- Audio should be as continuous as possible (STT degrades with gaps).
- Target end‑to‑end latency: **< 250 ms** from microphone capture to server ingestion.
- ESP32 should stream continuously (silence included) so the server can run VAD/commit strategies smoothly.

## 6) Resilience
ESP32 firmware SHOULD:
- Auto‑reconnect on disconnect and resume streaming.
- Backoff reconnect attempts (e.g., 0.5s → 1s → 2s → 5s).
- Avoid sending partial frames; always send full fixed-size frames.

## 7) Open Items the Hardware Team Should Confirm
- Mic type + sampling method (I2S vs ADC) and stable achievable `sampleRateHz`.
- Can we reliably hold **16kHz mono PCM** on ESP32 for the full demo duration?
- Expected placement on the user (left/right) and how consistent “left vs right” energy will be.
- Power/battery constraints (optional `batteryMv` reporting if available).

## Appendix: Running the UDP bridge
From the laptop (inside `server/.venv`):
```bash
cd server
python tools/udp_to_ws_bridge.py --server ws://<laptop-ip>:8765 --left-port 12345 --right-port 12346
```
