# Hackathon Demo Runbook

## A) Before the demo
- Put laptop + phone on the same network (phone hotspot is fine).
- Ensure you have:
  - 2× ESP32 mic streamers (left/right) implementing `docs/ESP32_Protocol.md`
  - Wristband implementing `docs/Wristband_Protocol.md` (or phone vibration as fallback)
  - ElevenLabs API key (for live STT)

## B) Start the laptop server
```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export ELEVENLABS_API_KEY="..."
python main.py --host 0.0.0.0 --port 8765
```

## C) Connect ESP32 audio streamers
- Confirm each ESP32 connects to:
  - `ws://<laptop-ip>:8765/esp32/audio?deviceId=...&role=left`
  - `ws://<laptop-ip>:8765/esp32/audio?deviceId=...&role=right`
- They must send a JSON `hello`, then binary PCM16LE frames.

## D) Run the Android app
1) Open `android/` in Android Studio and install to phone.
2) Plug Viture glasses into the phone via USB‑C.
3) Launch the app:
   - Phone screen = remote controller
   - Viture external display = HUD
4) Set server URL to `ws://<laptop-ip>:8765` and press **Connect**.

## E) Connect the wristband
- On the phone remote UI, press **Connect Wristband**.
- If wristband isn’t ready, phone vibration still demonstrates the feature.

## F) Validate quickly
- Server status:
  - Optionally run `python tools/events_print.py` (from `server/`) to see events on the laptop.
- ESP32 status:
  - Remote UI shows ESP32 left/right connected.
- STT:
  - Speak near mics and watch subtitles on the HUD.
- Direction:
  - Make sound on left vs right and watch the radar dot shift.
- Alarms:
  - Play a fire alarm / car horn sample near the mics to trigger alerts (heuristic thresholds may need tuning).
  - If alarms are too sensitive or not triggering, adjust thresholds in the phone remote UI and press **Apply Thresholds**.
