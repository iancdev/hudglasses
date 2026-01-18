from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np
import websockets
from websockets.asyncio.server import ServerConnection

from hudserver.audio_features import band_power_ratio, pcm16le_bytes_to_float32, rms as rms_value
from hudserver.logging_utils import setup_logging
from hudserver.protocol import dumps, loads
from hudserver.elevenlabs_stt import ElevenLabsConfig, ElevenLabsRealtimeStt


@dataclass(slots=True)
class Esp32AudioState:
    device_id: str
    role: str
    sample_rate_hz: int
    channels: int
    frame_ms: int
    bytes_per_frame: int
    stt_q: asyncio.Queue[bytes]
    analysis_q: asyncio.Queue[bytes]
    last_rms: float
    last_seen_monotonic: float
    dropped_frames: int
    frames_received: int = 0
    bad_frame_sizes: int = 0


@dataclass(slots=True)
class HeadPoseState:
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    last_seen_monotonic: float


@dataclass(slots=True)
class AndroidClientInfo:
    v: int | None
    client: str | None
    model: str | None
    sdk_int: int | None
    last_seen_monotonic: float


@dataclass(slots=True)
class AndroidMicState:
    device_id: str
    sample_rate_hz: int
    channels: int
    frame_ms: int
    bytes_per_frame: int
    mono_bytes_per_frame: int
    stt_q: asyncio.Queue[bytes]
    analysis_q: asyncio.Queue[bytes]
    last_rms: float
    last_rms_left: float
    last_rms_right: float
    last_seen_monotonic: float
    dropped_frames: int


@dataclass(slots=True)
class RadarTrack:
    track_id: int
    freq_hz: float
    intensity: float
    world_direction_deg: float
    last_seen_monotonic: float


class _SampleRing:
    def __init__(self, max_samples: int) -> None:
        self._max_samples = max(0, int(max_samples))
        self._parts: deque[np.ndarray] = deque()
        self._total_samples = 0

    def append(self, samples: np.ndarray) -> None:
        if self._max_samples <= 0:
            return
        if samples.size == 0:
            return
        if samples.dtype != np.float32:
            samples = samples.astype(np.float32, copy=False)
        if samples.size > self._max_samples:
            samples = samples[-self._max_samples :]
        self._parts.append(samples)
        self._total_samples += int(samples.size)
        while self._total_samples > self._max_samples and self._parts:
            popped = self._parts.popleft()
            self._total_samples -= int(popped.size)

    def get(self) -> np.ndarray:
        if self._total_samples <= 0 or not self._parts:
            return np.zeros((0,), dtype=np.float32)
        if len(self._parts) == 1:
            return self._parts[0]
        return np.concatenate(list(self._parts)).astype(np.float32, copy=False)


class HudServer:
    def __init__(self, host: str, port: int, log_level: str = "INFO") -> None:
        setup_logging(log_level)
        self._logger = logging.getLogger("hudserver")

        self._host = host
        self._port = port

        self._android_events: set[ServerConnection] = set()
        self._android_stt: set[ServerConnection] = set()
        self._android_info: dict[ServerConnection, AndroidClientInfo] = {}
        self._android_mic_by_conn: dict[ServerConnection, AndroidMicState] = {}
        self._android_mic_force_channels: int = 2

        # STT audio input selection:
        # - "auto": prefer ESP32 if present, else Android mic
        # - "android_mic": only Android mic
        # - "esp32": only ESP32
        self._stt_audio_source: str = (os.environ.get("STT_AUDIO_SOURCE") or "auto").strip().lower()

        self._esp32_by_role: dict[str, Esp32AudioState] = {}
        self._head_pose: HeadPoseState | None = None

        self._world_direction_deg: float | None = None
        self._latest_direction_payload: dict[str, Any] = {}
        self._direction_log_last_s: float = 0.0
        self._direction_noise_floor: float = float(os.environ.get("DIRECTION_NOISE_FLOOR", "0.002"))
        self._direction_gain_quad: float = float(os.environ.get("DIRECTION_GAIN_QUAD", "4.5"))
        self._direction_gain_lr: float = float(os.environ.get("DIRECTION_GAIN_LR", "6.0"))
        self._direction_gain_mono: float = float(os.environ.get("DIRECTION_GAIN_MONO", "6.0"))
        self._back_balance_gain_deg: float = float(os.environ.get("BACK_BALANCE_GAIN_DEG", "150.0"))
        self._back_balance_exp: float = float(os.environ.get("BACK_BALANCE_EXP", "0.8"))

        # Frequency-based radar dots (hackathon-friendly multi-source visualization).
        self._radar_window_s: float = float(os.environ.get("RADAR_WINDOW_S", "0.5"))
        self._radar_max_dots: int = int(os.environ.get("RADAR_MAX_DOTS", "3"))
        self._radar_min_freq_hz: float = float(os.environ.get("RADAR_MIN_FREQ_HZ", "200"))
        self._radar_max_freq_hz: float = float(os.environ.get("RADAR_MAX_FREQ_HZ", "4000"))
        # Track "baseline" spectrum so we can detect sources as outliers (boosted bands).
        # Higher alpha -> baseline adapts faster (fewer false positives, but less sensitivity).
        self._radar_baseline_alpha: float = float(os.environ.get("RADAR_BASELINE_ALPHA", "0.03"))
        # Cap how much a sudden spike is allowed to pull the baseline in one update.
        self._radar_baseline_peak_cap: float = float(os.environ.get("RADAR_BASELINE_PEAK_CAP", "2.0"))
        # Minimum relative boost over baseline for a band to be considered a source.
        # (excess / baseline) threshold, i.e. 1.0 means "2x baseline power".
        self._radar_outlier_ratio_thresh: float = float(os.environ.get("RADAR_OUTLIER_RATIO_THRESH", "0.7"))
        self._radar_last_baseline: np.ndarray | None = None
        self._radar_last_compute_s: float = 0.0
        self._radar_dots: list[dict[str, Any]] = []
        self._radar_tracks: dict[int, RadarTrack] = {}
        self._radar_next_track_id: int = 1
        self._radar_track_freq_tol_hz: float = float(os.environ.get("RADAR_TRACK_FREQ_TOL_HZ", "250"))
        self._radar_track_alpha_freq: float = float(os.environ.get("RADAR_TRACK_ALPHA_FREQ", "0.25"))
        self._radar_track_alpha_intensity: float = float(os.environ.get("RADAR_TRACK_ALPHA_INTENSITY", "0.15"))
        self._radar_track_alpha_dir: float = float(os.environ.get("RADAR_TRACK_ALPHA_DIR", "0.15"))
        self._radar_track_decay_tau_s: float = float(os.environ.get("RADAR_TRACK_DECAY_TAU_S", "1.2"))
        self._radar_track_min_intensity: float = float(os.environ.get("RADAR_TRACK_MIN_INTENSITY", "0.15"))

        radar_samples = int(16000 * self._radar_window_s)
        self._radar_buf_fl = _SampleRing(radar_samples)
        self._radar_buf_fr = _SampleRing(radar_samples)
        self._radar_buf_bl = _SampleRing(radar_samples)
        self._radar_buf_br = _SampleRing(radar_samples)
        self._radar_seen_fl: float = 0.0
        self._radar_seen_fr: float = 0.0
        self._radar_seen_bl: float = 0.0
        self._radar_seen_br: float = 0.0

        self._keywords: list[str] = []
        self._keyword_cooldown_s: float = float(os.environ.get("KEYWORD_COOLDOWN_S", "5"))
        self._keyword_last_hit: dict[str, float] = {}

        self._alarm_rms_threshold: float = float(os.environ.get("ALARM_RMS_THRESHOLD", "0.02"))
        self._fire_ratio_threshold: float = float(os.environ.get("FIRE_BAND_RATIO_THRESHOLD", "0.18"))
        self._horn_ratio_threshold: float = float(os.environ.get("HORN_BAND_RATIO_THRESHOLD", "0.20"))
        self._enable_heuristic_alarms: bool = (os.environ.get("ENABLE_HEURISTIC_ALARMS") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        self._reset_alarm_state_on_connect: bool = not self._enable_heuristic_alarms

        self._stop = asyncio.Event()

    async def run(self) -> None:
        self._logger.info("Starting server on %s:%s", self._host, self._port)
        stt_task = asyncio.create_task(self._stt_loop(), name="stt_loop")
        status_task = asyncio.create_task(self._status_loop(), name="status_loop")
        direction_task = asyncio.create_task(self._direction_loop(), name="direction_loop")
        alarms_task: asyncio.Task[None] | None = None
        if self._enable_heuristic_alarms:
            alarms_task = asyncio.create_task(self._alarms_loop(), name="alarms_loop")
        else:
            self._logger.info("Heuristic alarms disabled (set ENABLE_HEURISTIC_ALARMS=1 to enable)")
        try:
            async with websockets.serve(self._route, self._host, self._port, max_size=2 * 1024 * 1024):
                await self._stop.wait()
        finally:
            stt_task.cancel()
            status_task.cancel()
            direction_task.cancel()
            if alarms_task is not None:
                alarms_task.cancel()
            tasks: list[asyncio.Task[None]] = [stt_task, status_task, direction_task]
            if alarms_task is not None:
                tasks.append(alarms_task)
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _route(self, conn: ServerConnection) -> None:
        raw_path = conn.request.path
        parsed = urlparse(raw_path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/events":
            await self._handle_android_events(conn)
            return
        if path == "/stt":
            await self._handle_android_stt(conn)
            return
        if path == "/esp32/audio":
            await self._handle_esp32_audio(conn, query)
            return

        self._logger.warning("Unknown websocket path %s from %s", raw_path, conn.remote_address)
        await conn.close(code=1008, reason="Unknown path")

    async def _handle_android_events(self, conn: ServerConnection) -> None:
        self._android_events.add(conn)
        self._logger.info("Android /events connected from %s", conn.remote_address)
        self._android_info[conn] = AndroidClientInfo(
            v=None,
            client=None,
            model=None,
            sdk_int=None,
            last_seen_monotonic=asyncio.get_running_loop().time(),
        )
        try:
            await conn.send(dumps({"type": "status", "server": "connected"}))
            if self._reset_alarm_state_on_connect:
                # If heuristic alarms are disabled, make sure connected clients don't
                # remain "stuck" in a previous active state.
                await conn.send(dumps({"type": "alarm.fire", "state": "ended", **self._current_direction_payload()}))
                await conn.send(dumps({"type": "alarm.car_horn", "state": "ended", **self._current_direction_payload()}))
            async for msg in conn:
                if not isinstance(msg, str):
                    continue
                try:
                    obj = loads(msg)
                except Exception:
                    continue
                info = self._android_info.get(conn)
                if info is not None:
                    info.last_seen_monotonic = asyncio.get_running_loop().time()
                msg_type = obj.get("type")
                if msg_type == "hello":
                    if info is not None:
                        try:
                            if obj.get("v") is not None:
                                info.v = int(obj.get("v"))
                            if obj.get("client") is not None:
                                info.client = str(obj.get("client"))
                            if obj.get("model") is not None:
                                info.model = str(obj.get("model"))
                            if obj.get("sdkInt") is not None:
                                info.sdk_int = int(obj.get("sdkInt"))
                        except Exception:
                            continue
                elif msg_type == "head_pose":
                    try:
                        yaw = float(obj.get("yaw"))
                        pitch = float(obj.get("pitch"))
                        roll = float(obj.get("roll"))
                    except Exception:
                        continue
                    self._head_pose = HeadPoseState(
                        yaw_deg=yaw,
                        pitch_deg=pitch,
                        roll_deg=roll,
                        last_seen_monotonic=asyncio.get_running_loop().time(),
                    )
                elif msg_type == "config.update":
                    # Optional tuning knobs for demo.
                    self._alarm_rms_threshold = float(obj.get("alarmRmsThreshold", self._alarm_rms_threshold))
                    self._fire_ratio_threshold = float(obj.get("fireRatioThreshold", self._fire_ratio_threshold))
                    self._horn_ratio_threshold = float(obj.get("hornRatioThreshold", self._horn_ratio_threshold))
                    self._keyword_cooldown_s = float(obj.get("keywordCooldownS", self._keyword_cooldown_s))
                    kws = obj.get("keywords")
                    if isinstance(kws, list):
                        cleaned: list[str] = []
                        for k in kws:
                            if not isinstance(k, str):
                                continue
                            kk = " ".join(k.strip().lower().split())
                            if kk:
                                cleaned.append(kk)
                        self._keywords = cleaned[:50]
                elif msg_type == "audio.source":
                    source = str(obj.get("source") or "").strip().lower()
                    if source in ("auto", "android", "android_mic", "esp32"):
                        self._stt_audio_source = "android_mic" if source == "android" else source
                elif msg_type == "status.request":
                    now = asyncio.get_running_loop().time()
                    await conn.send(dumps(self._build_status_payload(now)))
        finally:
            self._android_events.discard(conn)
            self._android_info.pop(conn, None)
            self._logger.info("Android /events disconnected from %s", conn.remote_address)

    async def _handle_android_stt(self, conn: ServerConnection) -> None:
        self._android_stt.add(conn)
        self._logger.info("Android /stt connected from %s", conn.remote_address)
        try:
            await conn.send(dumps({"type": "status", "stt": "connected"}))
            async for msg in conn:
                if isinstance(msg, str):
                    obj = None
                    try:
                        obj = loads(msg)
                    except Exception:
                        continue
                    msg_type = obj.get("type")
                    if msg_type not in ("audio.hello", "hello"):
                        continue
                    audio = obj.get("audio") or {}
                    audio_format = str(audio.get("format") or "pcm_s16le")
                    sample_rate_hz = int(audio.get("sampleRateHz") or 16000)
                    channels = int(audio.get("channels") or 1)
                    frame_ms = int(audio.get("frameMs") or 20)
                    device_id = str(obj.get("deviceId") or "android")
                    if audio_format != "pcm_s16le":
                        self._logger.warning("Android mic deviceId=%s audio.format=%s (expected pcm_s16le)", device_id, audio_format)
                        continue
                    if sample_rate_hz != 16000:
                        self._logger.warning("Android mic deviceId=%s sampleRateHz=%s (expected 16000)", device_id, sample_rate_hz)
                        continue
                    if channels not in (1, 2):
                        self._logger.warning("Android mic deviceId=%s channels=%s (expected 1 or 2)", device_id, channels)
                        continue
                    if self._android_mic_force_channels in (1, 2) and channels != self._android_mic_force_channels:
                        channels = self._android_mic_force_channels
                        self._logger.info("Android mic %s forcing channels=%d", device_id, channels)
                    samples_per_frame = int(sample_rate_hz * (frame_ms / 1000.0))
                    mono_bytes_per_frame = samples_per_frame * 2
                    bytes_per_frame = mono_bytes_per_frame * channels
                    self._android_mic_by_conn[conn] = AndroidMicState(
                        device_id=device_id,
                        sample_rate_hz=sample_rate_hz,
                        channels=channels,
                        frame_ms=frame_ms,
                        bytes_per_frame=bytes_per_frame,
                        mono_bytes_per_frame=mono_bytes_per_frame,
                        stt_q=asyncio.Queue(maxsize=200),
                        analysis_q=asyncio.Queue(maxsize=200),
                        last_rms=0.0,
                        last_rms_left=0.0,
                        last_rms_right=0.0,
                        last_seen_monotonic=asyncio.get_running_loop().time(),
                        dropped_frames=0,
                    )
                    self._logger.info(
                        "Android mic ready deviceId=%s sampleRateHz=%s channels=%s frameMs=%s",
                        device_id,
                        sample_rate_hz,
                        channels,
                        frame_ms,
                    )
                    continue

                if not isinstance(msg, (bytes, bytearray)):
                    continue

                state = self._android_mic_by_conn.get(conn)
                if state is None:
                    # Best-effort default: 16kHz mono PCM, 20ms frames.
                    sample_rate_hz = 16000
                    channels = self._android_mic_force_channels if self._android_mic_force_channels in (1, 2) else 1
                    frame_ms = 20
                    mono_bytes_per_frame = int(sample_rate_hz * (frame_ms / 1000.0)) * 2
                    bytes_per_frame = mono_bytes_per_frame * channels
                    state = AndroidMicState(
                        device_id="android",
                        sample_rate_hz=sample_rate_hz,
                        channels=channels,
                        frame_ms=frame_ms,
                        bytes_per_frame=bytes_per_frame,
                        mono_bytes_per_frame=mono_bytes_per_frame,
                        stt_q=asyncio.Queue(maxsize=200),
                        analysis_q=asyncio.Queue(maxsize=200),
                        last_rms=0.0,
                        last_rms_left=0.0,
                        last_rms_right=0.0,
                        last_seen_monotonic=asyncio.get_running_loop().time(),
                        dropped_frames=0,
                    )
                    self._android_mic_by_conn[conn] = state

                state.last_seen_monotonic = asyncio.get_running_loop().time()

                # Auto-detect mono vs stereo if the client didn't (or couldn't) send a valid audio.hello.
                # For 16kHz/20ms PCM16:
                # - mono is 640 bytes
                # - stereo is 1280 bytes
                if state.channels == 1 and len(msg) == state.mono_bytes_per_frame * 2:
                    state.channels = 2
                    state.bytes_per_frame = state.mono_bytes_per_frame * 2
                    self._logger.info("Android mic %s detected stereo frames; switching channels=2", state.device_id)
                elif state.channels == 2 and len(msg) == state.mono_bytes_per_frame:
                    state.channels = 1
                    state.bytes_per_frame = state.mono_bytes_per_frame
                    self._logger.info("Android mic %s detected mono frames; switching channels=1", state.device_id)

                if len(msg) != state.bytes_per_frame:
                    self._logger.debug(
                        "Android mic %s unexpected frame size=%d expected=%d channels=%d",
                        state.device_id,
                        len(msg),
                        state.bytes_per_frame,
                        state.channels,
                    )

                frame_in = bytes(msg)
                frame_out: bytes | None = None

                # Compute RMS and downmix to mono for STT/analysis.
                try:
                    pcm = np.frombuffer(frame_in, dtype=np.int16)
                    if pcm.size == 0:
                        continue
                    if state.channels == 2:
                        left = pcm[0::2]
                        right = pcm[1::2]
                        n = min(left.size, right.size)
                        if n == 0:
                            continue
                        left = left[:n]
                        right = right[:n]
                        float_left = left.astype(np.float32) / np.float32(32768.0)
                        float_right = right.astype(np.float32) / np.float32(32768.0)
                        state.last_rms_left = float(np.sqrt(np.mean(float_left * float_left)))
                        state.last_rms_right = float(np.sqrt(np.mean(float_right * float_right)))
                        downmix = 0.5 * (float_left + float_right)
                        state.last_rms = float(np.sqrt(np.mean(downmix * downmix)))

                        self._radar_buf_bl.append(float_left)
                        self._radar_buf_br.append(float_right)
                        self._radar_seen_bl = state.last_seen_monotonic
                        self._radar_seen_br = state.last_seen_monotonic

                        mono_i32 = (left.astype(np.int32) + right.astype(np.int32)) // 2
                        frame_out = mono_i32.astype(np.int16).tobytes()
                    else:
                        float_pcm = pcm.astype(np.float32) / np.float32(32768.0)
                        state.last_rms = float(np.sqrt(np.mean(float_pcm * float_pcm)))
                        state.last_rms_left = state.last_rms
                        state.last_rms_right = state.last_rms
                        frame_out = frame_in

                        self._radar_buf_bl.append(float_pcm)
                        self._radar_buf_br.append(float_pcm)
                        self._radar_seen_bl = state.last_seen_monotonic
                        self._radar_seen_br = state.last_seen_monotonic
                except Exception:
                    self._logger.exception("Failed to process Android mic %s audio frame", state.device_id)
                    continue

                if frame_out is None:
                    continue

                if state.stt_q.full():
                    try:
                        _ = state.stt_q.get_nowait()
                        state.dropped_frames += 1
                    except asyncio.QueueEmpty:
                        pass
                try:
                    state.stt_q.put_nowait(frame_out)
                except asyncio.QueueFull:
                    state.dropped_frames += 1

                if state.analysis_q.full():
                    try:
                        _ = state.analysis_q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    state.analysis_q.put_nowait(frame_out)
                except asyncio.QueueFull:
                    pass
        finally:
            self._android_stt.discard(conn)
            self._android_mic_by_conn.pop(conn, None)
            self._logger.info("Android /stt disconnected from %s", conn.remote_address)

    async def _handle_esp32_audio(self, conn: ServerConnection, query: dict[str, list[str]]) -> None:
        device_id = (query.get("deviceId") or [""])[0] or "unknown"
        role = (query.get("role") or [""])[0] or "unknown"
        self._logger.info("ESP32 connected (query) deviceId=%s role=%s from %s", device_id, role, conn.remote_address)

        # Expect a JSON hello first, then binary audio frames.
        hello = await conn.recv()
        if not isinstance(hello, str):
            await conn.close(code=1003, reason="Expected JSON hello")
            return

        # Minimal parsing for now (we will validate more later).
        try:
            import json

            hello_obj: dict[str, Any] = json.loads(hello)
            audio = hello_obj.get("audio") or {}
            audio_format = str(audio.get("format") or "pcm_s16le")
            sample_rate_hz = int(audio.get("sampleRateHz") or 16000)
            frame_ms = int(audio.get("frameMs") or 20)
            channels = int(audio.get("channels") or 1)
            fw_version = str(hello_obj.get("fwVersion") or "")
            v = hello_obj.get("v")
            hello_role = (hello_obj.get("role") or role) or "unknown"
            hello_device_id = (hello_obj.get("deviceId") or device_id) or "unknown"
        except Exception:
            self._logger.exception("Invalid ESP32 hello from %s", conn.remote_address)
            await conn.close(code=1003, reason="Invalid hello")
            return

        if hello_device_id != device_id or hello_role != role:
            self._logger.info(
                "ESP32 hello overrides query deviceId=%s->%s role=%s->%s from %s",
                device_id,
                hello_device_id,
                role,
                hello_role,
                conn.remote_address,
            )

        if audio_format != "pcm_s16le":
            self._logger.warning("ESP32 %s role=%s audio.format=%s (expected pcm_s16le)", hello_device_id, hello_role, audio_format)
        if channels != 1:
            self._logger.warning("ESP32 %s role=%s audio.channels=%d (expected 1)", hello_device_id, hello_role, channels)

        samples_per_frame = int(sample_rate_hz * (frame_ms / 1000.0))
        bytes_per_frame = samples_per_frame * 2 * max(1, channels)

        self._logger.info(
            "ESP32 hello deviceId=%s role=%s v=%s fwVersion=%s audio.format=%s sampleRateHz=%d channels=%d frameMs=%d expectedBytes=%d",
            hello_device_id,
            hello_role,
            v,
            fw_version,
            audio_format,
            sample_rate_hz,
            channels,
            frame_ms,
            bytes_per_frame,
        )

        stt_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)  # ~4s at 20ms frames
        analysis_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)  # ~4s at 20ms frames
        prev = self._esp32_by_role.get(hello_role)
        if prev is not None and prev.device_id != hello_device_id:
            self._logger.info(
                "ESP32 role replacement role=%s prevDeviceId=%s -> newDeviceId=%s",
                hello_role,
                prev.device_id,
                hello_device_id,
            )
        self._esp32_by_role[hello_role] = Esp32AudioState(
            device_id=hello_device_id,
            role=hello_role,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
            frame_ms=frame_ms,
            bytes_per_frame=bytes_per_frame,
            stt_q=stt_q,
            analysis_q=analysis_q,
            last_rms=0.0,
            last_seen_monotonic=asyncio.get_running_loop().time(),
            dropped_frames=0,
        )

        try:
            async for msg in conn:
                if isinstance(msg, str):
                    # Optional diag messages.
                    continue
                if not isinstance(msg, (bytes, bytearray)):
                    continue

                state = self._esp32_by_role.get(hello_role)
                if state is None:
                    continue

                state.last_seen_monotonic = asyncio.get_running_loop().time()
                state.frames_received += 1

                if len(msg) != state.bytes_per_frame:
                    # Allow slightly variable frames, but log so firmware can be fixed.
                    state.bad_frame_sizes += 1
                    # Keep this INFO to reduce log spam when using the UDP bridge / early firmware.
                    if state.bad_frame_sizes <= 3 or (state.bad_frame_sizes % 50) == 0:
                        self._logger.info(
                            "ESP32 %s role=%s unexpected frame size=%d expected=%d badFrameSizes=%d",
                            state.device_id,
                            state.role,
                            len(msg),
                            state.bytes_per_frame,
                            state.bad_frame_sizes,
                        )

                # Compute RMS (0..1) for direction/intensity.
                try:
                    pcm = np.frombuffer(msg, dtype=np.int16)
                    if pcm.size:
                        float_pcm = pcm.astype(np.float32) / np.float32(32768.0)
                        state.last_rms = float(np.sqrt(np.mean(float_pcm * float_pcm)))
                        if state.role == "left":
                            self._radar_buf_fl.append(float_pcm)
                            self._radar_seen_fl = state.last_seen_monotonic
                        elif state.role == "right":
                            self._radar_buf_fr.append(float_pcm)
                            self._radar_seen_fr = state.last_seen_monotonic
                except Exception:
                    self._logger.exception("Failed to compute RMS for ESP32 %s role=%s", state.device_id, state.role)

                # Enqueue audio for downstream processing (STT/classification).
                if state.stt_q.full():
                    try:
                        _ = state.stt_q.get_nowait()
                        state.dropped_frames += 1
                    except asyncio.QueueEmpty:
                        pass
                try:
                    state.stt_q.put_nowait(bytes(msg))
                except asyncio.QueueFull:
                    state.dropped_frames += 1

                if state.analysis_q.full():
                    try:
                        _ = state.analysis_q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    state.analysis_q.put_nowait(bytes(msg))
                except asyncio.QueueFull:
                    # Not critical; analysis can drop.
                    pass
        finally:
            cur = self._esp32_by_role.get(hello_role)
            if cur and cur.device_id == hello_device_id:
                self._esp32_by_role.pop(hello_role, None)
            self._logger.info(
                "ESP32 disconnected deviceId=%s role=%s frames=%s dropped=%s badFrameSizes=%s",
                hello_device_id,
                hello_role,
                getattr(cur, "frames_received", None),
                getattr(cur, "dropped_frames", None),
                getattr(cur, "bad_frame_sizes", None),
            )

    async def _broadcast_events(self, obj: dict[str, Any]) -> None:
        if not self._android_events:
            return
        payload = dumps(obj)
        await self._broadcast(self._android_events, payload)

    async def _broadcast_stt(self, obj: dict[str, Any]) -> None:
        if not self._android_stt:
            return
        payload = dumps(obj)
        await self._broadcast(self._android_stt, payload)

    async def _broadcast(self, conns: set[ServerConnection], payload: str) -> None:
        dead: list[ServerConnection] = []
        for c in list(conns):
            try:
                await c.send(payload)
            except Exception:
                dead.append(c)
        for c in dead:
            conns.discard(c)

    def _build_status_payload(self, now: float) -> dict[str, Any]:
        esp32 = {
            role: {
                "deviceId": s.device_id,
                "role": s.role,
                "sampleRateHz": s.sample_rate_hz,
                "channels": s.channels,
                "frameMs": s.frame_ms,
                "bytesPerFrame": s.bytes_per_frame,
                "lastRms": s.last_rms,
                "droppedFrames": s.dropped_frames,
                "framesReceived": s.frames_received,
                "badFrameSizes": s.bad_frame_sizes,
                "sttQueue": s.stt_q.qsize(),
                "analysisQueue": s.analysis_q.qsize(),
            }
            for role, s in self._esp32_by_role.items()
        }
        android_mic = None
        if self._android_mic_by_conn:
            # Report only the freshest mic sender (hackathon assumption: 1 phone).
            freshest = max(self._android_mic_by_conn.values(), key=lambda s: s.last_seen_monotonic)
            android_mic = {
                "deviceId": freshest.device_id,
                "sampleRateHz": freshest.sample_rate_hz,
                "channels": freshest.channels,
                "frameMs": freshest.frame_ms,
                "lastRms": freshest.last_rms,
                "lastRmsLeft": freshest.last_rms_left,
                "lastRmsRight": freshest.last_rms_right,
                "droppedFrames": freshest.dropped_frames,
                "sttQueue": freshest.stt_q.qsize(),
                "analysisQueue": freshest.analysis_q.qsize(),
                "ageS": float(max(0.0, now - freshest.last_seen_monotonic)),
            }
        head_pose = None
        if self._head_pose is not None:
            head_pose = {
                "yawDeg": self._head_pose.yaw_deg,
                "pitchDeg": self._head_pose.pitch_deg,
                "rollDeg": self._head_pose.roll_deg,
            }

        android_clients: list[dict[str, Any]] = []
        for info in list(self._android_info.values()):
            android_clients.append(
                {
                    "v": info.v,
                    "client": info.client,
                    "model": info.model,
                    "sdkInt": info.sdk_int,
                    "ageS": float(max(0.0, now - info.last_seen_monotonic)),
                }
            )

        return {
            "type": "status",
            "server": "ok",
            "android": {"eventsClients": len(self._android_events), "sttClients": len(self._android_stt), "clients": android_clients},
            "esp32": esp32,
            "androidMic": android_mic,
            "sttAudioSource": self._stt_audio_source,
            "headPose": head_pose,
        }

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now = asyncio.get_running_loop().time()
            await self._broadcast_events(self._build_status_payload(now))

    async def _stt_loop(self) -> None:
        api_key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
        if not api_key:
            self._logger.warning("ELEVENLABS_API_KEY not set; STT disabled")
            while True:
                await asyncio.sleep(10.0)

        cfg = ElevenLabsConfig(
            api_key=api_key,
            host=(os.environ.get("ELEVENLABS_HOST") or "api.elevenlabs.io").strip(),
            model_id=(os.environ.get("ELEVENLABS_MODEL_ID") or None),
            language_code=(os.environ.get("ELEVENLABS_LANGUAGE_CODE") or None),
            commit_strategy=(os.environ.get("ELEVENLABS_COMMIT_STRATEGY") or "vad"),
            vad_silence_threshold_secs=float(os.environ.get("ELEVENLABS_VAD_SILENCE_THRESHOLD_SECS") or "1.2"),
            include_timestamps=(os.environ.get("ELEVENLABS_INCLUDE_TIMESTAMPS") == "1"),
        )
        stt = ElevenLabsRealtimeStt(cfg)

        last_partial_words: list[str] = []

        async def audio_frames() -> Any:
            active_role = "left"
            while True:
                source = self._stt_audio_source

                # 1) ESP32 first (auto + esp32-only)
                if source in ("auto", "esp32"):
                    left = self._esp32_by_role.get("left")
                    right = self._esp32_by_role.get("right")
                    if left and right:
                        l = left.last_rms
                        r = right.last_rms
                        if active_role == "left" and r > l * 1.5:
                            active_role = "right"
                        elif active_role == "right" and l > r * 1.5:
                            active_role = "left"

                    preferred = self._esp32_by_role.get(active_role)
                    fallback = self._esp32_by_role.get("right" if active_role == "left" else "left")
                    state = preferred or fallback
                    if state is not None:
                        try:
                            frame = await asyncio.wait_for(state.stt_q.get(), timeout=0.25)
                        except asyncio.CancelledError:
                            raise
                        except asyncio.TimeoutError:
                            # If user explicitly selected ESP32, don't fall back automatically.
                            if source == "esp32":
                                continue
                        else:
                            yield frame
                            continue
                    elif source == "esp32":
                        await asyncio.sleep(0.05)
                        continue

                # 2) Android mic fallback (auto + android-only)
                if source in ("auto", "android_mic"):
                    if self._android_mic_by_conn:
                        mic = max(self._android_mic_by_conn.values(), key=lambda s: s.last_seen_monotonic)
                        try:
                            frame = await asyncio.wait_for(mic.stt_q.get(), timeout=0.25)
                        except asyncio.CancelledError:
                            raise
                        except asyncio.TimeoutError:
                            # If user explicitly selected Android mic, don't fall back automatically.
                            if source == "android_mic":
                                continue
                        else:
                            yield frame
                            continue
                    elif source == "android_mic":
                        await asyncio.sleep(0.05)
                        continue

                await asyncio.sleep(0.01)

        def compute_delta_words(current_text: str) -> list[str]:
            nonlocal last_partial_words
            words = [w for w in current_text.strip().split() if w]
            if not last_partial_words:
                last_partial_words = words
                return words[:8]
            if len(words) >= len(last_partial_words) and words[: len(last_partial_words)] == last_partial_words:
                delta = words[len(last_partial_words) :]
                last_partial_words = words
                return delta[:8]
            # Partial transcript was revised; don't emit misleading deltas.
            last_partial_words = words
            return []

        async def on_stt_message(msg: dict[str, Any]) -> None:
            nonlocal last_partial_words
            msg_type = msg.get("message_type")
            if msg_type == "partial_transcript":
                text = str(msg.get("text") or "")
                await self._broadcast_stt({"type": "partial", "text": text, "deltaWords": compute_delta_words(text)})
                await self._check_keywords(text)
            elif msg_type in ("committed_transcript", "committed_transcript_with_timestamps"):
                text = str(msg.get("text") or "")
                await self._broadcast_stt({"type": "final", "text": text})
                last_partial_words = []
                await self._check_keywords(text)
            elif msg_type == "session_started":
                await self._broadcast_stt({"type": "status", "stt": "session_started"})
            elif msg_type in ("error", "auth_error", "quota_exceeded", "rate_limited"):
                await self._broadcast_stt({"type": "error", "message": str(msg.get("error") or msg_type)})

        await stt.run(audio_frames(), on_stt_message, sample_rate_hz=16000)

    async def _direction_loop(self) -> None:
        """Continuously derive direction/intensity and send UI placement to Android."""
        while True:
            await asyncio.sleep(0.05)  # 20Hz
            now = asyncio.get_running_loop().time()

            if (now - self._radar_last_compute_s) >= 0.2:
                self._radar_last_compute_s = now
                self._radar_dots = self._compute_radar_dots(now)

            source: str | None = None
            raw_direction_deg: float | None = None
            intensity: float | None = None

            # ESP32: front-left / front-right
            front_left = self._esp32_by_role.get("left")
            front_right = self._esp32_by_role.get("right")
            has_front = (
                front_left
                and front_right
                and (now - front_left.last_seen_monotonic) < 1.0
                and (now - front_right.last_seen_monotonic) < 1.0
            )
            fl = max(0.0, front_left.last_rms) if has_front and front_left else 0.0
            fr = max(0.0, front_right.last_rms) if has_front and front_right else 0.0

            # Android phone: back-left / back-right (requires stereo to be meaningful).
            mic = None
            if self._android_mic_by_conn:
                mic = max(self._android_mic_by_conn.values(), key=lambda s: s.last_seen_monotonic)
                if (now - mic.last_seen_monotonic) >= 1.0:
                    mic = None
            has_back = mic is not None and mic.channels == 2
            bl = max(0.0, mic.last_rms_left) if has_back and mic else 0.0
            br = max(0.0, mic.last_rms_right) if has_back and mic else 0.0

            if has_front and has_back:
                # 4-mic spatial estimate (front-left/front-right + back-left/back-right).
                # Map each mic to a quadrant and take a weighted vector sum.
                w = 0.70710678  # sin/cos(45deg)
                x = w * ((fr - fl) + (br - bl))  # right-positive
                y = w * ((fr + fl) - (br + bl))  # front-positive
                raw_direction_deg = float(np.degrees(np.arctan2(x, y)))
                total = fl + fr + bl + br
                total = max(0.0, float(total) - self._direction_noise_floor)
                intensity = float(np.clip(total * self._direction_gain_quad, 0.0, 1.0))
                source = "quad"
            elif has_front:
                total = fl + fr + 1e-6
                balance = (fr - fl) / total  # -1..+1
                raw_direction_deg = float(np.clip(balance * 90.0, -90.0, 90.0))
                total = max(0.0, float(total) - self._direction_noise_floor)
                intensity = float(np.clip(total * self._direction_gain_lr, 0.0, 1.0))
                source = "front"
            elif has_back:
                total = bl + br + 1e-6
                balance = (br - bl) / total  # -1..+1
                # Phone worn behind the neck: treat this as a "back" array.
                # Map balance into a rear arc around 180deg. Increase sensitivity so
                # small L/R differences show up as more lateral directions.
                gain = float(np.clip(self._back_balance_gain_deg, 0.0, 170.0))
                shaped = self._shape_balance(float(balance))
                raw_direction_deg = self._wrap_deg(180.0 - (shaped * gain))
                total = max(0.0, float(total) - self._direction_noise_floor)
                intensity = float(np.clip(total * self._direction_gain_lr, 0.0, 1.0))
                source = "back"
            else:
                # Last resort: use whichever single mic is available (no direction, intensity only).
                front_left_fresh = front_left is not None and (now - front_left.last_seen_monotonic) < 1.0
                front_right_fresh = front_right is not None and (now - front_right.last_seen_monotonic) < 1.0

                if mic is not None:
                    # Phone worn behind the neck: if we're forced into mono, treat it as "back".
                    one = mic
                    raw_direction_deg = 180.0
                    source = "back"
                elif front_left_fresh:
                    one = front_left
                    raw_direction_deg = 0.0
                    source = "front"
                elif front_right_fresh:
                    one = front_right
                    raw_direction_deg = 0.0
                    source = "front"
                else:
                    continue

                one_rms = float(getattr(one, "last_rms", 0.0))
                one_rms = max(0.0, one_rms - self._direction_noise_floor)
                intensity = float(np.clip(one_rms * self._direction_gain_mono, 0.0, 1.0))

            if raw_direction_deg is None or intensity is None or source is None:
                continue

            direction_deg = self._stabilize_direction(raw_direction_deg)
            ui = self._direction_to_ui(direction_deg, intensity)
            payload = {
                "source": source,
                "directionDeg": direction_deg,
                "rawDirectionDeg": raw_direction_deg,
                "intensity": intensity,
                "radarDots": self._radar_dots,
                **ui,
            }
            self._latest_direction_payload = payload
            self._log_direction_debug(
                now=now,
                source=source,
                raw_direction_deg=raw_direction_deg,
                direction_deg=direction_deg,
                intensity=intensity,
                fl=fl,
                fr=fr,
                bl=bl,
                br=br,
                ui=ui,
            )
            await self._broadcast_events({"type": "direction.ui", **payload})

    def _compute_radar_dots(self, now: float) -> list[dict[str, Any]]:
        # Compute a few frequency-peaks and estimate a direction per peak, then
        # smooth/lock them into short-lived tracks so the HUD shows sustained sources.
        sample_rate_hz = 16000

        has_front = (now - self._radar_seen_fl) < 1.0 and (now - self._radar_seen_fr) < 1.0
        has_back = (now - self._radar_seen_bl) < 1.0 and (now - self._radar_seen_br) < 1.0

        if not has_front and not has_back:
            return []

        fl = self._radar_buf_fl.get() if has_front else None
        fr = self._radar_buf_fr.get() if has_front else None
        bl = self._radar_buf_bl.get() if has_back else None
        br = self._radar_buf_br.get() if has_back else None

        arrays = [a for a in (fl, fr, bl, br) if a is not None and a.size > 0]
        if not arrays:
            return []

        n = int(min(a.size for a in arrays))
        if n < 2048:
            return []

        # Use the most recent aligned window across available channels.
        if fl is not None:
            fl = fl[-n:]
        if fr is not None:
            fr = fr[-n:]
        if bl is not None:
            bl = bl[-n:]
        if br is not None:
            br = br[-n:]

        window = np.hanning(n).astype(np.float32)

        def power(x: np.ndarray) -> np.ndarray:
            x = x.astype(np.float32, copy=False)
            x = x - float(np.mean(x))
            spec = np.fft.rfft(x * window)
            return (spec.real * spec.real + spec.imag * spec.imag).astype(np.float32)

        p_fl = power(fl) if fl is not None else None
        p_fr = power(fr) if fr is not None else None
        p_bl = power(bl) if bl is not None else None
        p_br = power(br) if br is not None else None

        ref = next((p for p in (p_fl, p_fr, p_bl, p_br) if p is not None), None)
        if ref is None:
            return []
        total = np.zeros_like(ref)
        for p in (p_fl, p_fr, p_bl, p_br):
            if p is not None:
                total += p

        freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate_hz)
        mask = (freqs >= self._radar_min_freq_hz) & (freqs <= self._radar_max_freq_hz)
        idx = np.where(mask)[0]
        if idx.size == 0:
            return []

        pose = self._head_pose
        yaw = None
        if pose is not None and (asyncio.get_running_loop().time() - pose.last_seen_monotonic) <= 1.0:
            yaw = pose.yaw_deg

        max_power = float(np.max(total[idx]))
        if max_power <= 0.0:
            return self._emit_radar_tracks(now, yaw)

        # Maintain a slow-moving "baseline" spectrum, then treat boosted (outlier) bands as sources.
        baseline = self._radar_last_baseline
        if baseline is None or baseline.shape != total.shape:
            baseline = total.astype(np.float32, copy=True)
            self._radar_last_baseline = baseline
        else:
            a = float(np.clip(self._radar_baseline_alpha, 0.0, 1.0))
            cap = max(1.0, float(self._radar_baseline_peak_cap))
            # Avoid letting short spikes immediately become "normal".
            clipped = np.minimum(total, baseline * cap)
            baseline[:] = (1.0 - a) * baseline + a * clipped

        eps = 1e-9
        excess = np.maximum(total - baseline, 0.0).astype(np.float32, copy=False)
        max_excess = float(np.max(excess[idx]))
        if max_excess <= 0.0:
            return self._emit_radar_tracks(now, yaw)

        # Pick a few distinct outlier peaks (avoid adjacent bins).
        bin_hz = float(sample_rate_hz) / float(n)
        sep_bins = max(1, int(200.0 / max(bin_hz, 1e-6)))
        # Require a noticeable boost vs baseline and also be among the strongest boosts.
        rel_thresh = max(0.0, float(self._radar_outlier_ratio_thresh))
        abs_thresh = max_excess * 0.25

        # Sort by a combined score so we don't over-favor tiny baseline bins.
        rel = (excess[idx] / (baseline[idx] + eps)).astype(np.float32, copy=False)
        score = (excess[idx] * np.sqrt(rel + 1e-6)).astype(np.float32, copy=False)
        candidates = idx[np.argsort(score)[::-1]]
        peaks: list[int] = []
        for b in candidates:
            if float(excess[b]) < abs_thresh:
                break
            if float(excess[b] / (baseline[b] + eps)) < rel_thresh:
                break
            if any(abs(b - p) < sep_bins for p in peaks):
                continue
            peaks.append(int(b))
            if len(peaks) >= max(1, self._radar_max_dots):
                break
        if not peaks:
            return []

        band_bins = max(1, int(120.0 / max(bin_hz, 1e-6)))

        energies: list[tuple[int, float, float, float, float, float, float]] = []
        for b in peaks:
            lo = max(0, b - band_bins)
            hi = min(total.size - 1, b + band_bins)
            e_fl = float(np.sum(p_fl[lo : hi + 1])) if p_fl is not None else 0.0
            e_fr = float(np.sum(p_fr[lo : hi + 1])) if p_fr is not None else 0.0
            e_bl = float(np.sum(p_bl[lo : hi + 1])) if p_bl is not None else 0.0
            e_br = float(np.sum(p_br[lo : hi + 1])) if p_br is not None else 0.0
            band_total = float(np.sum(total[lo : hi + 1]))
            band_base = float(np.sum(baseline[lo : hi + 1]))
            band_excess = max(0.0, band_total - band_base)
            energies.append((b, e_fl, e_fr, e_bl, e_br, band_total, band_excess))

        max_ex = max((band_excess for *_, band_excess in energies), default=0.0) + 1e-9

        candidates: list[tuple[float, float, float]] = []
        # (freq_hz, intensity, raw_dir_deg)
        w = 0.70710678
        for b, e_fl, e_fr, e_bl, e_br, band_total, band_excess in energies:
            if band_total <= 0.0 or band_excess <= 0.0:
                continue

            # Source strength is based on how much a frequency band exceeds the baseline.
            intensity = float(np.clip(np.sqrt(band_excess / max_ex), 0.0, 1.0))

            # Subtract baseline proportionally so direction is driven by the outlier component.
            scale = float(np.clip(band_excess / (band_total + eps), 0.0, 1.0))
            e_fl *= scale
            e_fr *= scale
            e_bl *= scale
            e_br *= scale

            if has_front and has_back:
                x = w * ((e_fr - e_fl) + (e_br - e_bl))
                y = w * ((e_fr + e_fl) - (e_br + e_bl))
                raw_dir = float(np.degrees(np.arctan2(x, y)))
            elif has_front:
                t = (e_fl + e_fr) + 1e-9
                balance = (e_fr - e_fl) / t
                raw_dir = float(np.clip(balance * 90.0, -90.0, 90.0))
            else:
                # Back-only.
                t = (e_bl + e_br) + 1e-9
                balance = (e_br - e_bl) / t
                gain = float(np.clip(self._back_balance_gain_deg, 0.0, 170.0))
                shaped = self._shape_balance(float(balance))
                raw_dir = self._wrap_deg(180.0 - (shaped * gain))

            # Use an excess-weighted centroid for more stable color/labeling.
            lo = max(0, int(b) - band_bins)
            hi = min(int(total.size - 1), int(b) + band_bins)
            band_excess_bins = excess[lo : hi + 1]
            denom = float(np.sum(band_excess_bins)) + eps
            if denom > eps:
                freq_hz = float(np.sum(freqs[lo : hi + 1] * band_excess_bins) / denom)
            else:
                freq_hz = float(freqs[int(b)])

            candidates.append((float(freq_hz), float(intensity), float(raw_dir)))

        if not candidates:
            # Fade out existing tracks.
            return self._emit_radar_tracks(now, yaw)

        # Track association: greedily match strongest candidates to existing tracks by frequency.
        candidates.sort(key=lambda c: c[1], reverse=True)
        used_tracks: set[int] = set()

        freq_tol = max(0.0, float(self._radar_track_freq_tol_hz))
        a_freq = float(self._radar_track_alpha_freq)
        a_int = float(self._radar_track_alpha_intensity)
        a_dir = float(self._radar_track_alpha_dir)

        for freq_hz, intensity, raw_dir in candidates[: max(1, self._radar_max_dots)]:
            best_id: int | None = None
            best_df = 1e9
            for tid, tr in self._radar_tracks.items():
                if tid in used_tracks:
                    continue
                df = abs(float(tr.freq_hz) - float(freq_hz))
                if df < best_df:
                    best_df = df
                    best_id = tid
            if best_id is None or best_df > freq_tol:
                # New track.
                tid = self._radar_next_track_id
                self._radar_next_track_id += 1
                world_dir = self._wrap_deg(float(yaw) + float(raw_dir)) if yaw is not None else float(raw_dir)
                self._radar_tracks[tid] = RadarTrack(
                    track_id=tid,
                    freq_hz=float(freq_hz),
                    intensity=float(intensity),
                    world_direction_deg=float(world_dir),
                    last_seen_monotonic=float(now),
                )
                used_tracks.add(tid)
                continue

            # Update matched track with EMA smoothing.
            tr = self._radar_tracks[best_id]
            tr.freq_hz = self._ema(tr.freq_hz, float(freq_hz), a_freq)
            tr.intensity = self._ema(tr.intensity, float(intensity), a_int)
            if yaw is not None:
                world_estimate = self._wrap_deg(float(yaw) + float(raw_dir))
                tr.world_direction_deg = self._lerp_angle(tr.world_direction_deg, world_estimate, a_dir)
            else:
                # No head pose: smooth in "relative" space.
                tr.world_direction_deg = self._lerp_angle(tr.world_direction_deg, float(raw_dir), a_dir)
            tr.last_seen_monotonic = float(now)
            used_tracks.add(best_id)

        return self._emit_radar_tracks(now, yaw)

    def _emit_radar_tracks(self, now: float, yaw: float | None) -> list[dict[str, Any]]:
        # Prune/decay tracks and emit the top-N by display intensity.
        tau = max(0.1, float(self._radar_track_decay_tau_s))
        min_i = max(0.0, float(self._radar_track_min_intensity))
        dots: list[dict[str, Any]] = []

        for tid, tr in list(self._radar_tracks.items()):
            age = float(now) - float(tr.last_seen_monotonic)
            if age > 3.0:
                self._radar_tracks.pop(tid, None)
                continue
            decay = float(np.exp(-age / tau))
            display_i = float(tr.intensity) * decay
            if display_i < min_i:
                self._radar_tracks.pop(tid, None)
                continue

            dir_deg = tr.world_direction_deg
            if yaw is not None:
                dir_deg = self._wrap_deg(tr.world_direction_deg - float(yaw))
            ui = self._direction_to_ui(dir_deg, display_i)
            dots.append(
                {
                    "trackId": int(tr.track_id),
                    "freqHz": float(tr.freq_hz),
                    "directionDeg": float(dir_deg),
                    "intensity": float(display_i),
                    "radarX": float(ui.get("radarX", 0.0)),
                    "radarY": float(ui.get("radarY", 0.0)),
                }
            )

        dots.sort(key=lambda d: float(d.get("intensity", 0.0)), reverse=True)
        return dots[: max(1, self._radar_max_dots)]

    async def _alarms_loop(self) -> None:
        """Very lightweight audio heuristics for hackathon demo."""
        sample_rate_hz = 16000
        window_samples = sample_rate_hz  # 1s
        hop_s = 0.2

        buf = np.zeros((0,), dtype=np.float32)

        fire_last_positive = 0.0
        fire_active = False
        fire_last_confidence = 0.0

        horn_last_positive = 0.0
        horn_active = False
        horn_last_confidence = 0.0

        loop = asyncio.get_running_loop()
        next_eval = loop.time()

        active_role = "left"
        while True:
            # Choose an analysis source (stickiness + fallback to avoid blocking).
            left = self._esp32_by_role.get("left")
            right = self._esp32_by_role.get("right")
            android_mic = None
            if self._android_mic_by_conn:
                android_mic = max(self._android_mic_by_conn.values(), key=lambda s: s.last_seen_monotonic)
                if (loop.time() - android_mic.last_seen_monotonic) > 1.0:
                    android_mic = None

            if not left and not right and android_mic is None:
                await asyncio.sleep(0.05)
                continue
            if left and right:
                if active_role == "left" and right.last_rms > left.last_rms * 1.5:
                    active_role = "right"
                elif active_role == "right" and left.last_rms > right.last_rms * 1.5:
                    active_role = "left"

            preferred = self._esp32_by_role.get(active_role)
            fallback = self._esp32_by_role.get("right" if active_role == "left" else "left")

            frame_bytes: bytes | None = None
            for state in (preferred, fallback):
                if state is None:
                    continue
                try:
                    frame_bytes = await asyncio.wait_for(state.analysis_q.get(), timeout=0.25)
                    break
                except asyncio.TimeoutError:
                    continue
            if frame_bytes is None and android_mic is not None:
                try:
                    frame_bytes = await asyncio.wait_for(android_mic.analysis_q.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    frame_bytes = None

            if frame_bytes is None:
                await asyncio.sleep(0.01)
                continue
            frame = pcm16le_bytes_to_float32(frame_bytes)
            if frame.size == 0:
                continue
            buf = np.concatenate([buf, frame])
            if buf.size > window_samples:
                buf = buf[-window_samples:]

            now = loop.time()
            if now < next_eval:
                continue
            next_eval = now + hop_s
            if buf.size < window_samples:
                continue

            total_rms = rms_value(buf)
            # Band ratios (coarse heuristics; tune in the field).
            fire_ratio = band_power_ratio(buf, sample_rate_hz, (2500.0, 3500.0))
            horn_ratio = band_power_ratio(buf, sample_rate_hz, (300.0, 900.0))

            fire_detected = total_rms > self._alarm_rms_threshold and fire_ratio > self._fire_ratio_threshold
            horn_detected = total_rms > self._alarm_rms_threshold and horn_ratio > self._horn_ratio_threshold

            def confidence(total: float, ratio: float, total_th: float, ratio_th: float) -> float:
                if total <= total_th or ratio <= ratio_th:
                    return 0.0
                rms_score = (total - total_th) / max(total_th, 1e-6)
                ratio_score = (ratio - ratio_th) / max(ratio_th, 1e-6)
                return float(np.clip(0.5 * rms_score + 0.5 * ratio_score, 0.0, 1.0))

            fire_confidence = confidence(total_rms, fire_ratio, self._alarm_rms_threshold, self._fire_ratio_threshold)
            horn_confidence = confidence(total_rms, horn_ratio, self._alarm_rms_threshold, self._horn_ratio_threshold)

            if fire_detected:
                fire_last_positive = now
                fire_last_confidence = fire_confidence
            if horn_detected:
                horn_last_positive = now
                horn_last_confidence = horn_confidence

            # PRD: fire holds for 10s after last detection.
            fire_should_be_active = (now - fire_last_positive) < 10.0
            horn_should_be_active = (now - horn_last_positive) < 2.0

            if fire_should_be_active and not fire_active:
                fire_active = True
                await self._broadcast_events(
                    {"type": "alarm.fire", "state": "started", "confidence": fire_last_confidence, **self._current_direction_payload()}
                )
            if fire_active and not fire_should_be_active:
                fire_active = False
                await self._broadcast_events(
                    {"type": "alarm.fire", "state": "ended", "confidence": 0.0, **self._current_direction_payload()}
                )

            if horn_should_be_active and not horn_active:
                horn_active = True
                await self._broadcast_events(
                    {
                        "type": "alarm.car_horn",
                        "state": "started",
                        "confidence": horn_last_confidence,
                        **self._current_direction_payload(),
                    }
                )
            if horn_active and not horn_should_be_active:
                horn_active = False
                await self._broadcast_events(
                    {"type": "alarm.car_horn", "state": "ended", "confidence": 0.0, **self._current_direction_payload()}
                )

    def _direction_to_ui(self, direction_deg: float, intensity: float) -> dict[str, Any]:
        # Map direction to radar coordinates (normalized -1..1) and edge glow.
        theta = np.deg2rad(direction_deg)
        radius = float(np.clip(intensity, 0.0, 1.0))
        radar_x = float(np.sin(theta) * radius)
        radar_y = float(np.cos(theta) * radius)

        if -45.0 <= direction_deg <= 45.0:
            glow_edge = "top"
        elif 45.0 < direction_deg < 135.0:
            glow_edge = "right"
        elif -135.0 < direction_deg < -45.0:
            glow_edge = "left"
        else:
            glow_edge = "bottom"

        return {
            "radarX": radar_x,
            "radarY": radar_y,
            "glowEdge": glow_edge,
            "glowStrength": float(np.clip(intensity, 0.0, 1.0)),
        }

    def _current_direction_payload(self) -> dict[str, Any]:
        return dict(self._latest_direction_payload)

    def _log_direction_debug(
        self,
        *,
        now: float,
        source: str,
        raw_direction_deg: float,
        direction_deg: float,
        intensity: float,
        fl: float,
        fr: float,
        bl: float,
        br: float,
        ui: dict[str, Any],
    ) -> None:
        # Avoid spamming logs; direction loop runs at 20Hz.
        if (now - self._direction_log_last_s) < 1.0:
            return
        self._direction_log_last_s = now

        self._logger.debug(
            "Direction source=%s raw=%.1f stabilized=%.1f intensity=%.2f rms(fl=%.3f fr=%.3f bl=%.3f br=%.3f) ui=(x=%.2f y=%.2f glow=%s strength=%.2f)",
            source,
            raw_direction_deg,
            direction_deg,
            intensity,
            fl,
            fr,
            bl,
            br,
            float(ui.get("radarX", 0.0)),
            float(ui.get("radarY", 0.0)),
            str(ui.get("glowEdge", "")),
            float(ui.get("glowStrength", 0.0)),
        )

    def _wrap_deg(self, deg: float) -> float:
        return ((deg + 180.0) % 360.0) - 180.0

    def _lerp_angle(self, a_deg: float, b_deg: float, t: float) -> float:
        delta = self._wrap_deg(b_deg - a_deg)
        return self._wrap_deg(a_deg + delta * t)

    def _stabilize_direction(self, raw_direction_deg: float) -> float:
        pose = self._head_pose
        if pose is None or (asyncio.get_running_loop().time() - pose.last_seen_monotonic) > 1.0:
            self._world_direction_deg = None
            return raw_direction_deg

        yaw = pose.yaw_deg
        world_estimate = self._wrap_deg(yaw + raw_direction_deg)
        if self._world_direction_deg is None:
            self._world_direction_deg = world_estimate
        else:
            self._world_direction_deg = self._lerp_angle(self._world_direction_deg, world_estimate, 0.2)

        return self._wrap_deg(self._world_direction_deg - yaw)

    def _shape_balance(self, balance: float) -> float:
        """Make small L/R differences more visible without slamming to extremes.

        balance is expected in [-1, 1]. Returns a value in [-1, 1].
        """
        b = float(np.clip(balance, -1.0, 1.0))
        exp = float(np.clip(self._back_balance_exp, 0.1, 1.0))
        return float(np.sign(b) * (abs(b) ** exp))

    def _ema(self, prev: float, obs: float, alpha: float) -> float:
        a = float(np.clip(alpha, 0.0, 1.0))
        return (1.0 - a) * float(prev) + a * float(obs)

    async def _check_keywords(self, text: str) -> None:
        if not self._keywords:
            return
        normalized = " ".join(str(text).lower().split())
        if not normalized:
            return
        now = asyncio.get_running_loop().time()
        for kw in self._keywords:
            if kw not in normalized:
                continue
            last = self._keyword_last_hit.get(kw, 0.0)
            if (now - last) < self._keyword_cooldown_s:
                continue
            self._keyword_last_hit[kw] = now
            await self._broadcast_events(
                {
                    "type": "alert.keyword",
                    "keyword": kw,
                    "text": normalized,
                    **self._current_direction_payload(),
                }
            )
