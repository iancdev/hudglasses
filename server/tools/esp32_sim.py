from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
import wave

import websockets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simulate ESP32 audio streaming to hudserver")
    parser.add_argument("--server", required=True, help="ws://host:port/esp32/audio")
    parser.add_argument("--device-id", default="sim-esp32")
    parser.add_argument("--role", choices=["left", "right"], required=True)
    parser.add_argument("--wav", help="Path to mono WAV file (16kHz, 16-bit PCM)")
    parser.add_argument("--tone-hz", type=float, help="Generate a sine wave instead of reading WAV")
    parser.add_argument("--amplitude", type=float, default=0.2, help="Tone amplitude (0..1)")
    parser.add_argument("--duration-s", type=float, default=10.0, help="Tone duration in seconds")
    parser.add_argument("--frame-ms", type=int, default=20)
    parser.add_argument("--sample-rate", type=int, default=16000)
    return parser


def _read_pcm16_frames(wav_path: str, sample_rate: int, frame_ms: int):
    with wave.open(wav_path, "rb") as wf:
        if wf.getnchannels() != 1:
            raise SystemExit("WAV must be mono")
        if wf.getsampwidth() != 2:
            raise SystemExit("WAV must be 16-bit PCM")
        if wf.getframerate() != sample_rate:
            raise SystemExit(f"WAV sample rate must be {sample_rate}Hz (got {wf.getframerate()}Hz)")
        frames_per_chunk = int(sample_rate * (frame_ms / 1000.0))
        while True:
            chunk = wf.readframes(frames_per_chunk)
            if not chunk:
                break
            yield chunk


def _gen_tone_frames(sample_rate: int, frame_ms: int, tone_hz: float, amplitude: float, duration_s: float):
    frames_per_chunk = int(sample_rate * (frame_ms / 1000.0))
    total_samples = int(sample_rate * duration_s)
    amp = max(0.0, min(1.0, amplitude))
    two_pi_f = 2.0 * math.pi * tone_hz
    for start in range(0, total_samples, frames_per_chunk):
        end = min(total_samples, start + frames_per_chunk)
        buf = bytearray()
        for i in range(start, end):
            s = int(math.sin(two_pi_f * (i / sample_rate)) * amp * 32767.0)
            buf += int(s).to_bytes(2, byteorder="little", signed=True)
        yield bytes(buf)


async def main() -> None:
    args = build_parser().parse_args()
    if not args.wav and not args.tone_hz:
        raise SystemExit("Provide --wav or --tone-hz")
    if args.wav and args.tone_hz:
        raise SystemExit("Provide only one of --wav or --tone-hz")
    uri = f"{args.server}?deviceId={args.device_id}&role={args.role}"
    async with websockets.connect(uri, max_size=2 * 1024 * 1024) as ws:
        hello = {
            "v": 1,
            "type": "hello",
            "deviceId": args.device_id,
            "role": args.role,
            "fwVersion": "sim",
            "audio": {"format": "pcm_s16le", "sampleRateHz": args.sample_rate, "channels": 1, "frameMs": args.frame_ms},
        }
        await ws.send(json.dumps(hello))

        frame_s = args.frame_ms / 1000.0
        next_time = time.monotonic()
        if args.wav:
            frames = _read_pcm16_frames(args.wav, args.sample_rate, args.frame_ms)
        else:
            frames = _gen_tone_frames(args.sample_rate, args.frame_ms, args.tone_hz, args.amplitude, args.duration_s)
        for frame in frames:
            await ws.send(frame)
            next_time += frame_s
            sleep = next_time - time.monotonic()
            if sleep > 0:
                await asyncio.sleep(sleep)


if __name__ == "__main__":
    asyncio.run(main())
