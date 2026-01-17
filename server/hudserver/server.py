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


class HudServer:
    def __init__(self, host: str, port: int, log_level: str = "INFO") -> None:
        setup_logging(log_level)
        self._logger = logging.getLogger("hudserver")

        self._host = host
        self._port = port

        self._android_events: set[ServerConnection] = set()
        self._android_stt: set[ServerConnection] = set()

        self._esp32_by_role: dict[str, Esp32AudioState] = {}
        self._head_pose: HeadPoseState | None = None

        self._world_direction_deg: float | None = None
        self._latest_direction_payload: dict[str, Any] = {}

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
        try:
            await conn.send(dumps({"type": "status", "server": "connected"}))
            async for msg in conn:
                if not isinstance(msg, str):
                    continue
                try:
                    obj = loads(msg)
                except Exception:
                    continue
                msg_type = obj.get("type")
                if msg_type == "head_pose":
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
        finally:
            self._android_events.discard(conn)
            self._logger.info("Android /events disconnected from %s", conn.remote_address)

    async def _handle_android_stt(self, conn: ServerConnection) -> None:
        self._android_stt.add(conn)
        self._logger.info("Android /stt connected from %s", conn.remote_address)
        try:
            await conn.send(dumps({"type": "status", "stt": "connected"}))
            async for _msg in conn:
                # Server -> Android only for now.
                pass
        finally:
            self._android_stt.discard(conn)
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
            head_pose = None
            if self._head_pose is not None:
                head_pose = {
                    "yawDeg": self._head_pose.yaw_deg,
                    "pitchDeg": self._head_pose.pitch_deg,
                    "rollDeg": self._head_pose.roll_deg,
                }
            await self._broadcast_events(
                {
                    "type": "status",
                    "server": "ok",
                    "android": {"eventsClients": len(self._android_events), "sttClients": len(self._android_stt)},
                    "esp32": esp32,
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
            include_timestamps=(os.environ.get("ELEVENLABS_INCLUDE_TIMESTAMPS") == "1"),
        )
        stt = ElevenLabsRealtimeStt(cfg)

        async def audio_frames() -> Any:
            active_role = "left"
            while True:
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
                if state is None:
                    await asyncio.sleep(0.05)
                    continue
                try:
                    frame = await asyncio.wait_for(state.stt_q.get(), timeout=0.25)
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    continue
                yield frame

        async def on_stt_message(msg: dict[str, Any]) -> None:
            msg_type = msg.get("message_type")
            if msg_type == "partial_transcript":
                text = str(msg.get("text") or "")
                await self._broadcast_stt({"type": "partial", "text": text})
            elif msg_type in ("committed_transcript", "committed_transcript_with_timestamps"):
                text = str(msg.get("text") or "")
                await self._broadcast_stt({"type": "final", "text": text})
            elif msg_type == "session_started":
                await self._broadcast_stt({"type": "status", "stt": "session_started"})
            elif msg_type in ("error", "auth_error", "quota_exceeded", "rate_limited"):
                await self._broadcast_stt({"type": "error", "message": str(msg.get("error") or msg_type)})

        await stt.run(audio_frames(), on_stt_message, sample_rate_hz=16000)

    async def _direction_loop(self) -> None:
        """Continuously derive direction/intensity and send UI placement to Android."""
        while True:
            await asyncio.sleep(0.05)  # 20Hz
            left = self._esp32_by_role.get("left")
            right = self._esp32_by_role.get("right")
            if not left or not right:
                continue

            l = max(0.0, left.last_rms)
            r = max(0.0, right.last_rms)
            total = l + r + 1e-6
            balance = (r - l) / total  # -1..+1

            raw_direction_deg = float(np.clip(balance * 90.0, -90.0, 90.0))
            intensity = float(np.clip(total * 2.5, 0.0, 1.0))  # heuristic gain

            direction_deg = self._stabilize_direction(raw_direction_deg)
            ui = self._direction_to_ui(direction_deg, intensity)
            payload = {
                "directionDeg": direction_deg,
                "rawDirectionDeg": raw_direction_deg,
                "intensity": intensity,
                **ui,
            }
            self._latest_direction_payload = payload
            await self._broadcast_events({"type": "direction.ui", **payload})

    async def _alarms_loop(self) -> None:
        """Very lightweight audio heuristics for hackathon demo."""
        sample_rate_hz = 16000
        window_samples = sample_rate_hz  # 1s
        hop_s = 0.2

        buf = np.zeros((0,), dtype=np.float32)

        fire_last_positive = 0.0
        fire_active = False

        horn_last_positive = 0.0
        horn_active = False

        loop = asyncio.get_running_loop()
        next_eval = loop.time()

        active_role = "left"
        while True:
            # Choose an analysis source (stickiness + fallback to avoid blocking).
            left = self._esp32_by_role.get("left")
            right = self._esp32_by_role.get("right")
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

            if frame_bytes is None:
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

            if fire_detected:
                fire_last_positive = now
            if horn_detected:
                horn_last_positive = now

            # PRD: fire holds for 10s after last detection.
            fire_should_be_active = (now - fire_last_positive) < 10.0
            horn_should_be_active = (now - horn_last_positive) < 2.0

            if fire_should_be_active and not fire_active:
                fire_active = True
                await self._broadcast_events({"type": "alarm.fire", "state": "started", **self._current_direction_payload()})
            if fire_active and not fire_should_be_active:
                fire_active = False
                await self._broadcast_events({"type": "alarm.fire", "state": "ended", **self._current_direction_payload()})

            if horn_should_be_active and not horn_active:
                horn_active = True
                await self._broadcast_events({"type": "alarm.car_horn", "state": "started", **self._current_direction_payload()})
            if horn_active and not horn_should_be_active:
                horn_active = False
                await self._broadcast_events({"type": "alarm.car_horn", "state": "ended", **self._current_direction_payload()})

    def _direction_to_ui(self, direction_deg: float, intensity: float) -> dict[str, Any]:
        # Map direction to radar coordinates (normalized -1..1) and edge glow.
        theta = np.deg2rad(direction_deg)
        radius = float(np.clip(intensity, 0.0, 1.0))
        radar_x = float(np.sin(theta) * radius)
        radar_y = float(np.cos(theta) * radius)

        if direction_deg < -20:
            glow_edge = "left"
        elif direction_deg > 20:
            glow_edge = "right"
        else:
            glow_edge = "top"

        return {
            "radarX": radar_x,
            "radarY": radar_y,
            "glowEdge": glow_edge,
            "glowStrength": float(np.clip(intensity, 0.0, 1.0)),
        }

    def _current_direction_payload(self) -> dict[str, Any]:
        return dict(self._latest_direction_payload)

    def _wrap_deg(self, deg: float) -> float:
        return ((deg + 180.0) % 360.0) - 180.0

    def _lerp_angle(self, a_deg: float, b_deg: float, t: float) -> float:
        delta = self._wrap_deg(b_deg - a_deg)
        return self._wrap_deg(a_deg + delta * t)

    def _stabilize_direction(self, raw_direction_deg: float) -> float:
        pose = self._head_pose
        if pose is None:
            self._world_direction_deg = None
            return raw_direction_deg

        yaw = pose.yaw_deg
        world_estimate = self._wrap_deg(yaw + raw_direction_deg)
        if self._world_direction_deg is None:
            self._world_direction_deg = world_estimate
        else:
            self._world_direction_deg = self._lerp_angle(self._world_direction_deg, world_estimate, 0.2)

        return self._wrap_deg(self._world_direction_deg - yaw)
