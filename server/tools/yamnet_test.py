from __future__ import annotations

import argparse
import os
import sys
import wave

import numpy as np

sys.path.append(os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from hudserver.yamnet_detector import YamnetDetector


def _read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as wf:
        channels = int(wf.getnchannels())
        sample_rate = int(wf.getframerate())
        sampwidth = int(wf.getsampwidth())
        if sampwidth != 2:
            raise SystemExit(f"Unsupported WAV sample width: {sampwidth} bytes (need 16-bit PCM)")
        frames = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels == 2:
        pcm = pcm.reshape((-1, 2))
        pcm = 0.5 * (pcm[:, 0] + pcm[:, 1])
    elif channels != 1:
        raise SystemExit(f"Unsupported WAV channels: {channels} (need mono or stereo)")
    return pcm.astype(np.float32, copy=False), sample_rate


def _resample_linear(x: np.ndarray, src_hz: int, dst_hz: int) -> np.ndarray:
    if src_hz == dst_hz:
        return x.astype(np.float32, copy=False)
    if x.size == 0:
        return x.astype(np.float32, copy=False)
    src_hz_f = float(src_hz)
    dst_hz_f = float(dst_hz)
    src_t = np.arange(x.size, dtype=np.float32) / src_hz_f
    dst_n = int(round(float(x.size) * dst_hz_f / src_hz_f))
    dst_t = np.arange(dst_n, dtype=np.float32) / dst_hz_f
    y = np.interp(dst_t, src_t, x).astype(np.float32, copy=False)
    return np.clip(y, -1.0, 1.0).astype(np.float32, copy=False)


def main() -> None:
    p = argparse.ArgumentParser(description="Quick YAMNet sanity check (prints horn/fire scores + top classes).")
    p.add_argument("--wav", required=True, help="Path to a WAV file (mono or stereo PCM16)")
    p.add_argument("--model", default=None, help="Path to resources/yamnet.h5 (optional)")
    p.add_argument("--class-map", default=None, help="Path to resources/yamnet_class_map.csv (optional)")
    p.add_argument("--topk", type=int, default=8)
    args = p.parse_args()

    pcm, sr = _read_wav(str(args.wav))
    pcm16k = _resample_linear(pcm, sr, 16000)

    det = YamnetDetector(model_path=args.model, class_map_path=args.class_map, sample_rate_hz=16000, topk=int(args.topk))
    det.load()
    scores = det.classify_window(pcm16k)

    print(f"fire_alarm={scores.fire_alarm:.3f}  car_horn={scores.car_horn:.3f}  siren={scores.siren:.3f}")
    for name, s in scores.top:
        print(f"{s:6.3f}  {name}")


if __name__ == "__main__":
    main()
