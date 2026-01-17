# ESP32 Direction Mic Protocol (Hackathon)

This document defines what the **laptop server** expects from the **ESP32 microphone nodes** during the hackathon demo.

Scope:
- **Phase 1 (required):** ESP32s provide **direction/intensity telemetry only**.
- **Phase 3 (optional later):** ESP32s can optionally provide **audio streaming** as an alternate STT source.

Non-goals (hackathon):
- Perfect localization / TDOA.
- Exact dB SPL calibration.
- OTA updates, secure provisioning, encryption.

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

### 2.2 Transport (Phase 1)
**Preferred:** UDP telemetry (lowest complexity and fine for lossy, high-rate signals).
- ESP32 → laptop: **UDP unicast** to `SERVER_IP:ESP32_UDP_PORT`.
- Each UDP datagram contains exactly **one JSON object** (UTF‑8).

Recommended defaults:
- `ESP32_UDP_PORT = 42100`
- Packet rate: **25 Hz** (acceptable range: 10–50 Hz).
- Maximum payload size: **<= 512 bytes**.

Why UDP:
- Occasional loss is acceptable for telemetry.
- Avoids keeping TCP/WebSocket sessions alive on embedded.

### 2.3 Server Addressing / Provisioning
For hackathon simplicity, ESP32 firmware SHOULD support one of:
1) Hardcoded `SERVER_IP` (fastest).
2) A simple serial / config file update for `SERVER_IP`.
3) Captive portal provisioning (nice-to-have).

Broadcast discovery MAY be attempted, but is not reliable on phone hotspots.

## 3) Message Types (Phase 1)
All messages MUST include:
- `v`: protocol version integer (start with `1`)
- `type`: message type string
- `deviceId`: unique string
- `role`: `"left"` or `"right"`
- `seq`: monotonically increasing integer sequence (wrap ok)
- `tsMs`: device timestamp in milliseconds (monotonic is OK; epoch not required)

### 3.1 `telemetry` (required)
Used by the server to infer **direction and intensity**.

**Schema (v1)**
```json
{
  "v": 1,
  "type": "telemetry",
  "deviceId": "esp32-left-01",
  "role": "left",
  "seq": 1234,
  "tsMs": 45678901,
  "rms": 0.031,
  "peak": 0.12,
  "sampleRateHz": 16000,
  "windowMs": 40,
  "clipping": false,
  "noiseFloor": 0.010,
  "batteryMv": 4020,
  "wifiRssiDbm": -58
}
```

**Field definitions**
- `rms` (required): normalized RMS amplitude over the last window, **0.0–1.0**.
  - Recommended normalization: `rms = sqrt(mean(x^2)) / maxAbs`, where `maxAbs` is the maximum absolute sample value for the ADC/sample format.
- `peak` (optional but recommended): normalized peak amplitude in the window, 0.0–1.0.
- `sampleRateHz` (optional): microphone sampling rate used internally (informational).
- `windowMs` (optional): analysis window duration used for `rms/peak`.
- `clipping` (optional): `true` if clipping was detected in the window.
- `noiseFloor` (optional): device-estimated baseline RMS in quiet conditions (0.0–1.0).
- `batteryMv` / `wifiRssiDbm` (optional): helps debugging reliability during demo.

**Minimum required fields for Phase 1 demo**
```json
{ "v":1, "type":"telemetry", "deviceId":"...", "role":"left|right", "seq":1, "tsMs":1, "rms":0.01 }
```

### 3.2 `hello` (recommended)
Sent once on boot (and optionally every N seconds) so the server can show device status.

```json
{
  "v": 1,
  "type": "hello",
  "deviceId": "esp32-right-01",
  "role": "right",
  "seq": 1,
  "tsMs": 1000,
  "fwVersion": "0.1.0",
  "ip": "192.168.1.50",
  "mac": "AA:BB:CC:DD:EE:FF"
}
```

### 3.3 `diag` (optional)
Used for extra debugging in the field (drop counts, CPU usage, etc.).

## 4) Server-Side Fusion Expectations (What the laptop does)
The server will compute direction and intensity from the most recent left/right telemetry samples:
- `intensity = normalize(rmsL + rmsR)`
- `balance = (rmsR - rmsL) / (rmsR + rmsL + eps)`
- Map `balance` to a coarse `directionDeg` for the HUD (e.g. -90..+90).

Because this is intensity-based (not TDOA), it is expected to be coarse (left/right/front-ish).

## 5) Timing Requirements
- Telemetry update rate target: **25 Hz**.
- End-to-end direction latency target: **< 150 ms** from sound to HUD update.
- Jitter: acceptable; server will smooth.

## 6) Calibration Guidance (Recommended)
To reduce false direction swings:
- On boot, record ~2–3 seconds of “quiet” and compute `noiseFloor`.
- Optionally subtract noise floor before reporting:
  - `rmsAdjusted = max(0, rms - noiseFloor)`

If you implement this, still report both `rms` and `noiseFloor` so the server can tune.

## 7) Error Handling + Resilience
ESP32 firmware SHOULD:
- Keep sending telemetry even if values are near zero (it acts as a heartbeat).
- Restart Wi‑Fi on disconnect and resume sending automatically.
- Cap packet rate if Wi‑Fi is unstable (e.g., fall back from 50 Hz to 10–25 Hz).

## 8) Optional Later Extension (Phase 3): ESP32 Audio Streaming for STT
Not required for the hackathon demo, but we want the protocol to be extendable so we can switch STT audio source later.

If implemented later, choose one:
- **Opus frames over UDP** (recommended for bandwidth), or
- **PCM chunks over TCP/WebSocket** (simpler, more bandwidth).

Proposed message (UDP, JSON header + base64 payload; not ideal but workable for prototypes):
```json
{
  "v": 1,
  "type": "audio_chunk",
  "deviceId": "esp32-left-01",
  "role": "left",
  "seq": 999,
  "tsMs": 55555,
  "audioFormat": "pcm_s16le",
  "sampleRateHz": 16000,
  "channels": 1,
  "audioBase64": "AAABAA..."
}
```

## 9) Open Items the Hardware Team Should Confirm
- Microphone type + sampling method (I2S vs ADC) and achievable stable `sampleRateHz`.
- Typical `rms` range in real environments (quiet room vs loud street).
- Power source and whether `batteryMv` can be measured.
- Expected placement on the user (left/right shoulder, glasses arms, etc.).

