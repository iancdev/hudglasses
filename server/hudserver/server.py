from __future__ import annotations

import asyncio
import logging
import os
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
    frame_ms: int
    bytes_per_frame: int
    stt_q: asyncio.Queue[bytes]
    analysis_q: asyncio.Queue[bytes]
    last_rms: float
    last_seen_monotonic: float
    dropped_frames: int


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

        self._keywords: list[str] = []
        self._keyword_cooldown_s: float = float(os.environ.get("KEYWORD_COOLDOWN_S", "5"))
        self._keyword_last_hit: dict[str, float] = {}

        self._alarm_rms_threshold: float = float(os.environ.get("ALARM_RMS_THRESHOLD", "0.02"))
        self._fire_ratio_threshold: float = float(os.environ.get("FIRE_BAND_RATIO_THRESHOLD", "0.18"))
        self._horn_ratio_threshold: float = float(os.environ.get("HORN_BAND_RATIO_THRESHOLD", "0.20"))

        self._stop = asyncio.Event()

    async def run(self) -> None:
        self._logger.info("Starting server on %s:%s", self._host, self._port)
        stt_task = asyncio.create_task(self._stt_loop(), name="stt_loop")
        status_task = asyncio.create_task(self._status_loop(), name="status_loop")
        direction_task = asyncio.create_task(self._direction_loop(), name="direction_loop")
        alarms_task = asyncio.create_task(self._alarms_loop(), name="alarms_loop")
        try:
            async with websockets.serve(self._route, self._host, self._port, max_size=2 * 1024 * 1024):
                await self._stop.wait()
        finally:
            stt_task.cancel()
            status_task.cancel()
            direction_task.cancel()
            alarms_task.cancel()
            await asyncio.gather(stt_task, status_task, direction_task, alarms_task, return_exceptions=True)

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
                        float_left = left.astype(np.float32) / 32768.0
                        float_right = right.astype(np.float32) / 32768.0
                        state.last_rms_left = float(np.sqrt(np.mean(float_left * float_left)))
                        state.last_rms_right = float(np.sqrt(np.mean(float_right * float_right)))
                        downmix = 0.5 * (float_left + float_right)
                        state.last_rms = float(np.sqrt(np.mean(downmix * downmix)))

                        mono_i32 = (left.astype(np.int32) + right.astype(np.int32)) // 2
                        frame_out = mono_i32.astype(np.int16).tobytes()
                    else:
                        float_pcm = pcm.astype(np.float32) / 32768.0
                        state.last_rms = float(np.sqrt(np.mean(float_pcm * float_pcm)))
                        state.last_rms_left = state.last_rms
                        state.last_rms_right = state.last_rms
                        frame_out = frame_in
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
        self._logger.info("ESP32 connected deviceId=%s role=%s from %s", device_id, role, conn.remote_address)

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
            hello_role = (hello_obj.get("role") or role) or "unknown"
            hello_device_id = (hello_obj.get("deviceId") or device_id) or "unknown"
        except Exception:
            self._logger.exception("Invalid ESP32 hello from %s", conn.remote_address)
            await conn.close(code=1003, reason="Invalid hello")
            return

        if audio_format != "pcm_s16le":
            self._logger.warning("ESP32 %s role=%s audio.format=%s (expected pcm_s16le)", hello_device_id, hello_role, audio_format)

        samples_per_frame = int(sample_rate_hz * (frame_ms / 1000.0))
        bytes_per_frame = samples_per_frame * 2

        stt_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)  # ~4s at 20ms frames
        analysis_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)  # ~4s at 20ms frames
        self._esp32_by_role[hello_role] = Esp32AudioState(
            device_id=hello_device_id,
            role=hello_role,
            sample_rate_hz=sample_rate_hz,
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

                if len(msg) != state.bytes_per_frame:
                    # Allow slightly variable frames, but log so firmware can be fixed.
                    self._logger.warning(
                        "ESP32 %s role=%s unexpected frame size=%d expected=%d",
                        state.device_id,
                        state.role,
                        len(msg),
                        state.bytes_per_frame,
                    )

                # Compute RMS (0..1) for direction/intensity.
                try:
                    pcm = np.frombuffer(msg, dtype=np.int16)
                    if pcm.size:
                        float_pcm = pcm.astype(np.float32) / 32768.0
                        state.last_rms = float(np.sqrt(np.mean(float_pcm * float_pcm)))
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
            self._logger.info("ESP32 disconnected deviceId=%s role=%s", hello_device_id, hello_role)

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

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now = asyncio.get_running_loop().time()
            esp32 = {
                role: {
                    "deviceId": s.device_id,
                    "role": s.role,
                    "sampleRateHz": s.sample_rate_hz,
                    "frameMs": s.frame_ms,
                    "lastRms": s.last_rms,
                    "droppedFrames": s.dropped_frames,
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
            await self._broadcast_events(
                {
                    "type": "status",
                    "server": "ok",
                    "android": {"eventsClients": len(self._android_events), "sttClients": len(self._android_stt), "clients": android_clients},
                    "esp32": esp32,
                    "androidMic": android_mic,
                    "sttAudioSource": self._stt_audio_source,
                    "headPose": head_pose,
                }
            )

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
                intensity = float(np.clip(total * 1.8, 0.0, 1.0))  # heuristic gain
                source = "quad"
            elif has_front:
                total = fl + fr + 1e-6
                balance = (fr - fl) / total  # -1..+1
                raw_direction_deg = float(np.clip(balance * 90.0, -90.0, 90.0))
                intensity = float(np.clip(total * 2.5, 0.0, 1.0))
                source = "front"
            elif has_back:
                total = bl + br + 1e-6
                balance = (br - bl) / total  # -1..+1
                raw_direction_deg = float(np.clip(balance * 90.0, -90.0, 90.0))
                intensity = float(np.clip(total * 2.5, 0.0, 1.0))
                source = "back"
            else:
                # Last resort: use whichever single mic is available (no direction, intensity only).
                one = (front_left or front_right) or mic
                if one is None:
                    continue
                raw_direction_deg = 0.0
                one_rms = float(getattr(one, "last_rms", 0.0))
                intensity = float(np.clip(max(0.0, one_rms) * 2.5, 0.0, 1.0))
                source = "mono"

            if raw_direction_deg is None or intensity is None or source is None:
                continue

            direction_deg = self._stabilize_direction(raw_direction_deg)
            ui = self._direction_to_ui(direction_deg, intensity)
            payload = {
                "source": source,
                "directionDeg": direction_deg,
                "rawDirectionDeg": raw_direction_deg,
                "intensity": intensity,
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
