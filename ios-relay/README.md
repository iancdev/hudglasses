# iOS Relay (Next.js)

This is a tiny web app meant to run on the **laptop** and be opened from an **iPhone on the same Wi‑Fi**. It captures the iPhone microphone and relays it into the existing server’s ESP32 WebSocket endpoints:

- `ws://<server>:<port>/esp32/audio?role=left`
- `ws://<server>:<port>/esp32/audio?role=right`

This matches the protocol in `docs/ESP32_Protocol.md` (JSON `hello`, then binary PCM16 frames).

## Why there’s a relay WebSocket (`/ws`)

iOS Safari requires a **secure context** for microphone access, and pages served over `https://` cannot open insecure `ws://` connections to the Python server (mixed‑content blocked). This app hosts a secure websocket at `/ws` and the Node server forwards the audio to the Python server over `ws://` locally.

## Run

1) Start the Python server (on the laptop):
```bash
cd server
python main.py --host 0.0.0.0 --port 8765
```

2) Install deps for the relay:
```bash
cd ios-relay
npm install
```

3) (Recommended for iOS) run with HTTPS:

- Put a cert + key at `ios-relay/.cert/cert.pem` and `ios-relay/.cert/key.pem` (gitignored).
- Then:
```bash
cd ios-relay
npm run dev:https
```

### Quick dev cert options

- `mkcert` (best UX; trusted if you install the CA on your iPhone):
  - `mkcert -cert-file .cert/cert.pem -key-file .cert/key.pem 10.19.130.231 localhost 127.0.0.1`
- `openssl` (local CA + server cert with SAN; install the CA on your iPhone to avoid warnings):
  - `mkdir -p .cert`
  - `openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -keyout .cert/ca.key.pem -out .cert/ca.cert.pem -subj "/CN=ios-relay-dev-ca" -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" >/dev/null`
  - `openssl req -new -newkey rsa:2048 -nodes -keyout .cert/key.pem -out .cert/server.csr.pem -subj "/CN=10.19.130.231" -addext "subjectAltName=IP:10.19.130.231,IP:127.0.0.1,DNS:localhost" >/dev/null`
  - `printf "%s\n" "basicConstraints=critical,CA:FALSE" "keyUsage=critical,digitalSignature,keyEncipherment" "extendedKeyUsage=serverAuth" "subjectAltName=IP:10.19.130.231,IP:127.0.0.1,DNS:localhost" > .cert/server.ext`
  - `openssl x509 -req -in .cert/server.csr.pem -CA .cert/ca.cert.pem -CAkey .cert/ca.key.pem -CAcreateserial -out .cert/server.cert.pem -days 365 -sha256 -extfile .cert/server.ext >/dev/null`
  - `cat .cert/server.cert.pem .cert/ca.cert.pem > .cert/cert.pem`
  - (Optional, for iOS install): `openssl x509 -in .cert/ca.cert.pem -outform der -out .cert/ca.cer`

4) Open on the iPhone:

- `https://<laptop-ip>:3000`
- Enter the Python server IP/port (defaults match the hackathon server: `10.19.130.231:8765`)
- Select mic (if multiple) → Start

## Notes

- Audio sent is **16kHz stereo**, split into **two mono PCM16LE** streams framed at **20ms (640 bytes per channel)** and forwarded to `left` and `right`.
- If you only need a single stream, it’s easy to change the relay to connect only one role.
