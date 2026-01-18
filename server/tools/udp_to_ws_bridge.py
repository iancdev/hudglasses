from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
from dataclasses import dataclass
from typing import Literal

import websockets


Role = Literal["left", "right"]


@dataclass(slots=True)
class RoleCfg:
    role: Role
    udp_port: int
    device_id: str


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, *, role: Role, udp_port: int, queue: asyncio.Queue[bytes], chunk_bytes: int) -> None:
        self._role = role
        self._port = int(udp_port)
        self._queue = queue
        self._chunk_bytes = int(chunk_bytes)
        self._buf = bytearray()
        self._total_received = 0
        self._total_emitted = 0
        self._last_log_s = 0.0

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        if not data:
            return
        self._total_received += len(data)
        self._buf.extend(data)

        # Avoid unbounded growth if the sender bursts or chunking is mismatched.
        max_buf = self._chunk_bytes * 40
        if len(self._buf) > max_buf:
            drop = len(self._buf) - max_buf
            del self._buf[:drop]

        while len(self._buf) >= self._chunk_bytes:
            chunk = bytes(self._buf[: self._chunk_bytes])
            del self._buf[: self._chunk_bytes]
            if self._queue.full():
                try:
                    _ = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                self._queue.put_nowait(chunk)
                self._total_emitted += len(chunk)
            except asyncio.QueueFull:
                pass

        loop = asyncio.get_running_loop()
        now = loop.time()
        if (now - self._last_log_s) >= 2.0:
            self._last_log_s = now
            logging.getLogger("udp_bridge").info(
                "UDP %s port=%d recv=%dB emit=%dB buf=%dB",
                self._role,
                self._port,
                self._total_received,
                self._total_emitted,
                len(self._buf),
            )

    def connection_made(self, transport: asyncio.BaseTransport) -> None:  # type: ignore[override]
        sock = transport.get_extra_info("socket")
        if isinstance(sock, socket.socket):
            self._port = sock.getsockname()[1]


async def _ws_sender(
    *,
    server_base: str,
    cfg: RoleCfg,
    queue: asyncio.Queue[bytes],
    sample_rate_hz: int,
    frame_ms: int,
) -> None:
    log = logging.getLogger("udp_bridge")
    uri = f"{server_base.rstrip('/')}/esp32/audio?deviceId={cfg.device_id}&role={cfg.role}"
    backoff_s = 0.5
    while True:
        try:
            async with websockets.connect(uri, max_size=2 * 1024 * 1024) as ws:
                hello = {
                    "v": 1,
                    "type": "hello",
                    "deviceId": cfg.device_id,
                    "role": cfg.role,
                    "fwVersion": "udp_bridge",
                    "audio": {"format": "pcm_s16le", "sampleRateHz": sample_rate_hz, "channels": 1, "frameMs": frame_ms},
                }
                await ws.send(json.dumps(hello))
                log.info("WS connected role=%s deviceId=%s -> %s", cfg.role, cfg.device_id, uri)
                backoff_s = 0.5

                while True:
                    chunk = await queue.get()
                    await ws.send(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("WS reconnect role=%s in %.1fs (%s)", cfg.role, backoff_s, e)
            await asyncio.sleep(backoff_s)
            backoff_s = min(5.0, backoff_s * 1.8)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Bridge UDP PCM16 audio (2 ESP32s) into hudserver WebSocket endpoints.")
    parser.add_argument("--server", required=True, help="Base server URL like ws://HOST:8765 (no path)")
    parser.add_argument("--left-port", type=int, default=12345, help="UDP port for LEFT ESP32")
    parser.add_argument("--right-port", type=int, default=12346, help="UDP port for RIGHT ESP32")
    parser.add_argument("--left-device-id", default="esp32-left-udp", help="deviceId to report for LEFT")
    parser.add_argument("--right-device-id", default="esp32-right-udp", help="deviceId to report for RIGHT")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--frame-ms", type=int, default=20)
    parser.add_argument(
        "--chunk-bytes",
        type=int,
        default=640,
        help="How many bytes to forward per WS binary message (recommended 640 = 16kHz*20ms*2B)",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("udp_bridge")

    server_base = str(args.server).rstrip("/")
    sample_rate_hz = int(args.sample_rate)
    frame_ms = int(args.frame_ms)
    chunk_bytes = int(args.chunk_bytes)

    left_cfg = RoleCfg(role="left", udp_port=int(args.left_port), device_id=str(args.left_device_id))
    right_cfg = RoleCfg(role="right", udp_port=int(args.right_port), device_id=str(args.right_device_id))

    left_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=400)
    right_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=400)

    loop = asyncio.get_running_loop()
    left_transport, _ = await loop.create_datagram_endpoint(
        lambda: _UdpProtocol(role="left", udp_port=left_cfg.udp_port, queue=left_q, chunk_bytes=chunk_bytes),
        local_addr=("0.0.0.0", left_cfg.udp_port),
    )
    right_transport, _ = await loop.create_datagram_endpoint(
        lambda: _UdpProtocol(role="right", udp_port=right_cfg.udp_port, queue=right_q, chunk_bytes=chunk_bytes),
        local_addr=("0.0.0.0", right_cfg.udp_port),
    )

    log.info(
        "Listening UDP left=%d right=%d -> %s (frame_ms=%d sample_rate=%d chunk_bytes=%d)",
        left_cfg.udp_port,
        right_cfg.udp_port,
        server_base,
        frame_ms,
        sample_rate_hz,
        chunk_bytes,
    )

    try:
        await asyncio.gather(
            _ws_sender(server_base=server_base, cfg=left_cfg, queue=left_q, sample_rate_hz=sample_rate_hz, frame_ms=frame_ms),
            _ws_sender(server_base=server_base, cfg=right_cfg, queue=right_q, sample_rate_hz=sample_rate_hz, frame_ms=frame_ms),
        )
    finally:
        left_transport.close()
        right_transport.close()


if __name__ == "__main__":
    asyncio.run(main())
