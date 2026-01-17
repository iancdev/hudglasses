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

