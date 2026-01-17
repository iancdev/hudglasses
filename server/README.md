# HUD Glasses Server (Hackathon)

This is the **laptop “brain”** server. It:
- accepts **ESP32 audio streams** (left/right),
- derives **direction + intensity**,
- runs **danger sound detection** (fire alarm, car horn),
- streams audio to **ElevenLabs Realtime STT** and forwards transcripts,
- broadcasts **HUD events** to the Android client.

## Requirements
- Python 3.10+
- `ELEVENLABS_API_KEY` (optional, but required for live STT)

## Install
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run
```bash
export ELEVENLABS_API_KEY="..."
python main.py --host 0.0.0.0 --port 8765
```

Endpoints:
- ESP32 audio: `ws://<server>:8765/esp32/audio?deviceId=...&role=left|right`
- Android transcripts: `ws://<server>:8765/stt`
- Android events: `ws://<server>:8765/events`

## Quick Test (no hardware)
Simulate an ESP32 streaming a WAV file (16kHz mono PCM recommended):
```bash
python tools/esp32_sim.py --server ws://127.0.0.1:8765/esp32/audio --role left --device-id sim-left --wav path/to/file.wav
```

