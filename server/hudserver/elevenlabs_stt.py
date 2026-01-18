from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import ssl
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

import certifi
import websockets


@dataclass(frozen=True, slots=True)
class ElevenLabsConfig:
    api_key: str
    host: str = "api.elevenlabs.io"
    model_id: str | None = None
    language_code: str | None = None
    audio_format: str = "pcm_16000"
    commit_strategy: str = "vad"
    vad_silence_threshold_secs: float = 1.2
    include_timestamps: bool = False


class ElevenLabsRealtimeStt:
    def __init__(self, cfg: ElevenLabsConfig) -> None:
        self._cfg = cfg
        self._logger = logging.getLogger("hudserver.elevenlabs")

    async def run(
        self,
        audio_frames: AsyncIterator[bytes],
        on_message: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        sample_rate_hz: int = 16000,
        reconnect_delay_s: float = 1.0,
    ) -> None:
        while True:
            try:
                await self._run_once(audio_frames, on_message, sample_rate_hz=sample_rate_hz)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("ElevenLabs STT session error; reconnecting")
                await asyncio.sleep(reconnect_delay_s)

    async def _run_once(
        self,
        audio_frames: AsyncIterator[bytes],
        on_message: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        sample_rate_hz: int,
    ) -> None:
        uri = self._build_uri()
        self._logger.info("Connecting ElevenLabs STT: %s", uri)

        ssl_ctx: ssl.SSLContext | None = None
        if os.environ.get("ELEVENLABS_INSECURE_SSL") == "1":
            self._logger.warning("ELEVENLABS_INSECURE_SSL=1; TLS verification disabled (unsafe)")
            ssl_ctx = ssl._create_unverified_context()
        else:
            cafile = (os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or "").strip()
            if cafile:
                self._logger.info("Using TLS CA bundle from env: %s", cafile)
                ssl_ctx = ssl.create_default_context(cafile=cafile)
            else:
                ca = certifi.where()
                self._logger.info("Using certifi CA bundle: %s", ca)
                ssl_ctx = ssl.create_default_context(cafile=ca)

        async with websockets.connect(
            uri,
            additional_headers={"xi-api-key": self._cfg.api_key},
            ssl=ssl_ctx,
            max_size=2 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            async def sender() -> None:
                async for frame in audio_frames:
                    payload = {
                        "message_type": "input_audio_chunk",
                        "audio_base_64": base64.b64encode(frame).decode("ascii"),
                        "commit": False,
                        "sample_rate": sample_rate_hz,
                    }
                    await ws.send(json.dumps(payload, separators=(",", ":")))

            async def receiver() -> None:
                async for msg in ws:
                    if not isinstance(msg, str):
                        continue
                    try:
                        obj = json.loads(msg)
                    except Exception:
                        continue
                    await on_message(obj)

            await asyncio.gather(sender(), receiver())

    def _build_uri(self) -> str:
        parts: list[str] = []
        if self._cfg.model_id:
            parts.append(f"model_id={self._cfg.model_id}")
        if self._cfg.language_code:
            parts.append(f"language_code={self._cfg.language_code}")
        if self._cfg.audio_format:
            parts.append(f"audio_format={self._cfg.audio_format}")
        if self._cfg.commit_strategy:
            parts.append(f"commit_strategy={self._cfg.commit_strategy}")
        if self._cfg.vad_silence_threshold_secs:
            parts.append(f"vad_silence_threshold_secs={self._cfg.vad_silence_threshold_secs}")
        if self._cfg.include_timestamps:
            parts.append("include_timestamps=true")
        query = ("?" + "&".join(parts)) if parts else ""
        return f"wss://{self._cfg.host}/v1/speech-to-text/realtime{query}"
