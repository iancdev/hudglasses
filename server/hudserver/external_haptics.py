from __future__ import annotations

import asyncio
import json
import logging
import random
import time

import websockets


def _clamp_int(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


class ExternalHapticsClient:
    """Maintain a WS connection to a haptics device and send (durationMs, intensity) commands."""

    def __init__(
        self,
        *,
        name: str,
        url: str,
        payload_format: str = "csv",
        open_timeout_s: float = 15.0,
        max_queue: int = 100,
        logger: logging.Logger | None = None,
    ) -> None:
        self._name = name
        self._url = url
        self._payload_format = (payload_format or "csv").strip().lower()
        self._open_timeout_s = float(max(0.1, open_timeout_s))
        self._q: asyncio.Queue[str] = asyncio.Queue(maxsize=max(1, int(max_queue)))
        self._logger = logger or logging.getLogger(__name__)

        self.connected: bool = False
        self._last_err_log_s: float = 0.0

    @property
    def url(self) -> str:
        return self._url

    def enqueue_buzz(self, duration_ms: int, intensity: int) -> None:
        duration_ms_i = _clamp_int(duration_ms, 0, 60_000)
        intensity_i = _clamp_int(intensity, 0, 255)

        if self._payload_format == "json":
            payload = json.dumps([duration_ms_i, intensity_i], separators=(",", ":"))
        elif self._payload_format == "tuple":
            payload = f"({duration_ms_i},{intensity_i})"
        else:
            # Default: easiest for small devices to parse.
            payload = f"{duration_ms_i},{intensity_i}"

        if self._q.full():
            try:
                _ = self._q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._q.put_nowait(payload)
        except asyncio.QueueFull:
            # Best-effort; haptics can drop.
            pass

    async def run(self, stop: asyncio.Event) -> None:
        backoff_s = 0.5
        while not stop.is_set():
            try:
                # Many ESP32 websocket servers are simplistic; keep the connection options conservative:
                # - disable ping/pong timeouts (some firmwares don't respond reliably)
                # - small max_queue + drain incoming messages (some firmwares reply per command)
                async with websockets.connect(
                    self._url,
                    open_timeout=self._open_timeout_s,
                    ping_interval=None,
                    close_timeout=2,
                    max_size=64 * 1024,
                    max_queue=8,
                ) as ws:
                    self.connected = True
                    backoff_s = 0.5
                    self._logger.info("External haptics %s connected url=%s", self._name, self._url)

                    async def drain_incoming() -> None:
                        while not stop.is_set():
                            try:
                                # Drain and ignore any device replies so the server doesn't block on send.
                                await ws.recv()
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                return

                    drain_task = asyncio.create_task(drain_incoming(), name=f"external_haptics_{self._name}_drain")
                    while not stop.is_set():
                        try:
                            payload = await asyncio.wait_for(self._q.get(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        await ws.send(payload)
                    drain_task.cancel()
                    await asyncio.gather(drain_task, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                now_s = time.monotonic()
                if (now_s - self._last_err_log_s) > 3.0:
                    self._last_err_log_s = now_s
                    self._logger.info(
                        "External haptics %s disconnected url=%s err=%s (%s); retrying",
                        self._name,
                        self._url,
                        getattr(e, "errno", None),
                        type(e).__name__,
                    )
            finally:
                if self.connected:
                    self.connected = False
                    self._logger.info("External haptics %s disconnected url=%s", self._name, self._url)

            # Jittered backoff to avoid synchronized reconnect storms.
            await asyncio.sleep(backoff_s + random.random() * 0.2)
            backoff_s = min(backoff_s * 1.7, 5.0)
