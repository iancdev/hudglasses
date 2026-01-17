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
    audio_q: asyncio.Queue[bytes]
    last_rms: float
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

        self._esp32_by_role: dict[str, Esp32AudioState] = {}

        self._stop = asyncio.Event()

    async def run(self) -> None:
        self._logger.info("Starting server on %s:%s", self._host, self._port)
        stt_task = asyncio.create_task(self._stt_loop(), name="stt_loop")
        status_task = asyncio.create_task(self._status_loop(), name="status_loop")
        try:
            async with websockets.serve(self._route, self._host, self._port, max_size=2 * 1024 * 1024):
                await self._stop.wait()
        finally:
            stt_task.cancel()
            status_task.cancel()
            await asyncio.gather(stt_task, status_task, return_exceptions=True)

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
                    _obj = loads(msg)
                except Exception:
                    continue
                # TODO: config.update / head_pose support in later iterations.
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

        audio_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)  # ~4s at 20ms frames
        self._esp32_by_role[hello_role] = Esp32AudioState(
            device_id=hello_device_id,
            role=hello_role,
            sample_rate_hz=sample_rate_hz,
            frame_ms=frame_ms,
            bytes_per_frame=bytes_per_frame,
            audio_q=audio_q,
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
                if state.audio_q.full():
                    try:
                        _ = state.audio_q.get_nowait()
                        state.dropped_frames += 1
                    except asyncio.QueueEmpty:
                        pass
                try:
                    state.audio_q.put_nowait(bytes(msg))
                except asyncio.QueueFull:
                    state.dropped_frames += 1
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
                }
                for role, s in self._esp32_by_role.items()
            }
            await self._broadcast_events(
                {
                    "type": "status",
                    "server": "ok",
                    "android": {"eventsClients": len(self._android_events), "sttClients": len(self._android_stt)},
                    "esp32": esp32,
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
            # Default: prefer left, fallback to right.
            while True:
                state = self._esp32_by_role.get("left") or self._esp32_by_role.get("right")
                if state is None:
                    await asyncio.sleep(0.1)
                    continue
                try:
                    frame = await asyncio.wait_for(state.audio_q.get(), timeout=0.5)
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
