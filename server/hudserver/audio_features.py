from __future__ import annotations

import numpy as np


def pcm16le_bytes_to_float32(pcm: bytes) -> np.ndarray:
    """Convert PCM s16le bytes to float32 in [-1, 1]."""
    if not pcm:
        return np.zeros((0,), dtype=np.float32)
    s16 = np.frombuffer(pcm, dtype=np.int16)
    return (s16.astype(np.float32) / 32768.0).copy()


def rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


def band_power_ratio(samples: np.ndarray, sample_rate_hz: int, band_hz: tuple[float, float]) -> float:
    """Return band power / total power for the provided samples."""
    if samples.size == 0:
        return 0.0
    window = np.hanning(samples.size).astype(np.float32)
    x = samples * window
    spec = np.fft.rfft(x)
    power = (spec.real * spec.real + spec.imag * spec.imag).astype(np.float32)
    total = float(np.sum(power)) + 1e-12
    freqs = np.fft.rfftfreq(samples.size, d=1.0 / sample_rate_hz)
    lo, hi = band_hz
    mask = (freqs >= lo) & (freqs <= hi)
    band = float(np.sum(power[mask]))
    return band / total

