# usb-relay

Capture audio from a locally attached microphone (e.g. USB mic) and relay it into the existing HUD server’s ESP32 ingest endpoint:

- `ws://<server>:<port>/esp32/audio?deviceId=...&role=left|right`

This matches `docs/ESP32_Protocol.md` (JSON `hello`, then binary PCM16 frames).

## Install

```bash
cd usb-relay
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### macOS notes

`sounddevice` uses PortAudio. If you see missing PortAudio errors:

```bash
brew install portaudio
```

You may also need to grant microphone permission to your terminal / Python.

## List devices

```bash
python usb_relay.py --list-devices
```

## Run

Start the Python server (on the laptop):

```bash
cd server
python main.py --host 0.0.0.0 --port 8765
```

Relay as **left**:

```bash
python usb_relay.py --server ws://127.0.0.1:8765 --role left --device "USB" --device-id usb-mic
```

Relay as **right**:

```bash
python usb_relay.py --server ws://127.0.0.1:8765 --role right --device "USB" --device-id usb-mic
```

Relay as **both**:

- If the input device is stereo, L/R are split to `role=left` and `role=right`.
- If the input device is mono, the mono stream is duplicated to both roles.

```bash
python usb_relay.py --server ws://127.0.0.1:8765 --role both --device "USB" --device-id usb-mic
```
