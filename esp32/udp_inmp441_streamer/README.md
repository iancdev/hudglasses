# UDP INMP441 Streamer (Hackathon)

This is a **minimal** ESP32 firmware for streaming INMP441 I2S microphone audio as **PCM16 mono** over **UDP** to a laptop on the same Wi‑Fi network.

This repo’s server expects ESP32 audio over **WebSocket** (`/esp32/audio`), so for hackathon simplicity we run a laptop bridge:
- `server/tools/udp_to_ws_bridge.py` (UDP → WebSocket)

## Why UDP (for hackathon)
- Easiest firmware; proven working in the field.
- Bridge keeps the server protocol stable.

## Build / Flash
- Open `udp_inmp441_streamer.ino` in Arduino IDE or PlatformIO.
- Configure:
  - `WIFI_SSID`, `WIFI_PASSWORD`
  - `LAPTOP_IP`
  - `UDP_PORT` (left=12345, right=12346 recommended)
  - `ROLE` and `DEVICE_ID`

## Recommended settings for our project
- `SAMPLE_RATE_HZ = 16000`
- `FRAME_MS = 20` (`SAMPLES_PER_FRAME = 320`, `BYTES_PER_FRAME = 640`)
- **Disable AGC** (we rely on relative energy left vs right for direction)

## Run the laptop bridge
From `server/` (inside your `.venv`):
```bash
python tools/udp_to_ws_bridge.py --server ws://<laptop-ip>:8765 --left-port 12345 --right-port 12346
```
