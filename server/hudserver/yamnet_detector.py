from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class YamnetScores:
    fire_alarm: float
    car_horn: float
    siren: float
    top: list[tuple[str, float]]


def _repo_root() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    # hudserver/ -> server/ -> repo root
    return os.path.normpath(os.path.join(here, "..", ".."))


def default_model_path() -> str:
    return os.path.join(_repo_root(), "resources", "yamnet.h5")


def default_class_map_path() -> str:
    return os.path.join(_repo_root(), "resources", "yamnet_class_map.csv")


def load_yamnet_class_names(class_map_csv_path: str) -> list[str]:
    # CSV columns: index,mid,display_name
    # Keep parsing dependency-free (no pandas).
    names: list[str] = []
    with open(class_map_csv_path, "r", encoding="utf-8") as f:
        header = f.readline()
        if "display_name" not in header:
            raise ValueError("Invalid YAMNet class map header")
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Split first 2 commas; display_name may be quoted and contain commas.
            parts = line.split(",", 2)
            if len(parts) != 3:
                continue
            display = parts[2].strip()
            if display.startswith('"') and display.endswith('"'):
                display = display[1:-1]
            names.append(display)
    # YAMNet uses 521 classes.
    if len(names) < 100:
        raise ValueError(f"YAMNet class map seems too small ({len(names)} classes)")
    return names


class YamnetDetector:
    def __init__(
        self,
        *,
        model_path: str | None = None,
        class_map_path: str | None = None,
        sample_rate_hz: int = 16000,
        # Defaults taken from the official YAMNet AudioSet class map:
        # - 393: "Smoke detector, smoke alarm"
        # - 394: "Fire alarm"
        # - 302: "Vehicle horn, car horn, honking"
        # - 312: "Air horn, truck horn"
        fire_alarm_class_idxs: tuple[int, ...] = (393, 394),
        car_horn_class_idxs: tuple[int, ...] = (302, 312),
        # - 390: "Siren"
        # - 316: "Emergency vehicle"
        # - 317: "Police car (siren)"
        # - 318: "Ambulance (siren)"
        # - 319: "Fire engine, fire truck (siren)"
        siren_class_idxs: tuple[int, ...] = (390, 316, 317, 318, 319),
        topk: int = 5,
    ) -> None:
        self._model_path = model_path or default_model_path()
        self._class_map_path = class_map_path or default_class_map_path()
        self._sample_rate_hz = int(sample_rate_hz)
        self._fire_idxs = tuple(int(i) for i in fire_alarm_class_idxs)
        self._horn_idxs = tuple(int(i) for i in car_horn_class_idxs)
        self._siren_idxs = tuple(int(i) for i in siren_class_idxs)
        self._topk = max(0, int(topk))

        self._tf = None
        self._model = None
        self._class_names = load_yamnet_class_names(self._class_map_path)

    @property
    def model_path(self) -> str:
        return self._model_path

    @property
    def class_names(self) -> list[str]:
        return self._class_names

    def load(self) -> None:
        # Lazy import so the server can still run without TF installed (it will just disable YAMNet alarms).
        import tensorflow as tf  # type: ignore

        try:
            import tf_keras  # type: ignore  # noqa: F401
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "Missing dependency: tf-keras. Install with `pip install tf-keras==2.20.1` (macOS arm64)."
            ) from e

        from hudserver.yamnet_model import yamnet_model

        self._tf = tf
        self._model = yamnet_model()
        self._model.load_weights(self._model_path)
        # Warm up once to avoid first-hit latency spikes.
        warm = np.zeros((self._sample_rate_hz,), dtype=np.float32)
        _ = self._predict_scores(warm)

    def _ensure_loaded(self) -> None:
        if self._model is None:
            raise RuntimeError("YAMNet model not loaded")

    def _predict_scores(self, waveform_16k: np.ndarray) -> np.ndarray:
        self._ensure_loaded()
        tf = self._tf
        model = self._model
        if tf is None or model is None:
            raise RuntimeError("YAMNet model not loaded")

        x = np.asarray(waveform_16k, dtype=np.float32)
        if x.ndim != 1:
            x = x.reshape((-1,))

        out = model(np.expand_dims(x, 0), training=False)

        # Keras returns tensors; some exports return a tuple/list.
        if isinstance(out, (tuple, list)):
            out0 = out[0]
        else:
            out0 = out
        scores = np.array(out0)
        if scores.ndim == 3:
            # (batch, frames, classes)
            scores = scores[0]
        if scores.ndim == 1:
            # (classes,)
            scores = scores.reshape((1, -1))
        if scores.ndim != 2:
            raise RuntimeError(f"Unexpected YAMNet output shape: {scores.shape}")
        return scores.astype(np.float32, copy=False)

    def classify_window(self, waveform_16k: np.ndarray) -> YamnetScores:
        scores = self._predict_scores(waveform_16k)
        if scores.size == 0:
            return YamnetScores(fire_alarm=0.0, car_horn=0.0, siren=0.0, top=[])

        # Aggregate across frames.
        mx = scores.max(axis=0)

        def safe_max(idxs: tuple[int, ...]) -> float:
            best = 0.0
            for i in idxs:
                if 0 <= i < mx.size:
                    best = max(best, float(mx[i]))
            return best

        fire = safe_max(self._fire_idxs)
        horn = safe_max(self._horn_idxs)
        siren = safe_max(self._siren_idxs)

        top: list[tuple[str, float]] = []
        if self._topk > 0:
            k = min(self._topk, int(mx.size))
            order = np.argsort(mx)[::-1][:k]
            for i in order:
                name = self._class_names[int(i)] if 0 <= int(i) < len(self._class_names) else f"class_{int(i)}"
                top.append((name, float(mx[int(i)])))

        return YamnetScores(
            fire_alarm=float(fire),
            car_horn=float(horn),
            siren=float(siren),
            top=top,
        )
