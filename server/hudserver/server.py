from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets
from websockets.asyncio.server import ServerConnection

from hudserver.logging_utils import setup_logging


@dataclass(slots=True)
class Esp32Client:
    device_id: str
    role: str
    sample_rate_hz: int
    frame_ms: int
    last_seen_monotonic: float


class HudServer:
    def __init__(self, host: str, port: int, log_level: str = "INFO") -> None:
        setup_logging(log_level)
        self._logger = logging.getLogger("hudserver")

        self._host = host
        self._port = port

        self._android_events: set[ServerConnection] = set()
        self._android_stt: set[ServerConnection] = set()

        self._esp32_by_role: dict[str, Esp32Client] = {}

        self._stop = asyncio.Event()

    async def run(self) -> None:
        self._logger.info("Starting server on %s:%s", self._host, self._port)
        async with websockets.serve(self._route, self._host, self._port, max_size=2 * 1024 * 1024):
            await self._stop.wait()

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
            async for _msg in conn:
                # TODO: parse incoming config/head_pose.
                pass
        finally:
            self._android_events.discard(conn)
            self._logger.info("Android /events disconnected from %s", conn.remote_address)

    async def _handle_android_stt(self, conn: ServerConnection) -> None:
        self._android_stt.add(conn)
        self._logger.info("Android /stt connected from %s", conn.remote_address)
        try:
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
            sample_rate_hz = int(audio.get("sampleRateHz") or 16000)
            frame_ms = int(audio.get("frameMs") or 20)
            hello_role = (hello_obj.get("role") or role) or "unknown"
            hello_device_id = (hello_obj.get("deviceId") or device_id) or "unknown"
        except Exception:
            self._logger.exception("Invalid ESP32 hello from %s", conn.remote_address)
            await conn.close(code=1003, reason="Invalid hello")
            return

        self._esp32_by_role[hello_role] = Esp32Client(
            device_id=hello_device_id,
            role=hello_role,
            sample_rate_hz=sample_rate_hz,
            frame_ms=frame_ms,
            last_seen_monotonic=asyncio.get_running_loop().time(),
        )

        try:
            async for msg in conn:
                if isinstance(msg, str):
                    # Optional diag messages.
                    continue
                # msg is bytes (audio frame). Processing pipeline comes next iteration.
                self._esp32_by_role[hello_role].last_seen_monotonic = asyncio.get_running_loop().time()
        finally:
            cur = self._esp32_by_role.get(hello_role)
            if cur and cur.device_id == hello_device_id:
                self._esp32_by_role.pop(hello_role, None)
            self._logger.info("ESP32 disconnected deviceId=%s role=%s", hello_device_id, hello_role)

