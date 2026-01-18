from __future__ import annotations

import argparse
import asyncio
import audioop
import json
import logging
import signal
import threading
import traceback
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

import websockets

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore[assignment]


Role = Literal["left", "right"]
RoleMode = Literal["left", "right", "both"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Relay a local microphone into hudserver (/esp32/audio).")
    parser.add_argument("--server", required=True, help="ws://HOST:PORT (or ws://HOST:PORT/esp32/audio)")
    parser.add_argument("--role", choices=["left", "right", "both"], required=True, help="Which ESP32 role(s) to act as")
    parser.add_argument("--device", help='Input device index, or substring match (e.g. "USB")')
    parser.add_argument("--list-devices", action="store_true", help="List input devices and exit")
    parser.add_argument("--device-id", default="usb-mic", help="deviceId to report (both mode uses -left/-right suffixes)")
    parser.add_argument("--sample-rate", type=int, default=16000, help="Output sample rate to send to server (default: 16000)")
    parser.add_argument("--frame-ms", type=int, default=20, help="Frame size in ms to send to server (default: 20)")
    parser.add_argument(
        "--input-sample-rate",
        type=int,
        help="Input capture sample rate; default is the device's PortAudio default samplerate",
    )
    parser.add_argument("--input-channels", type=int, choices=[1, 2], help="Force input channels (1 or 2)")
    parser.add_argument(
        "--queue-max-frames",
        type=int,
        default=50,
        help="Max queued output frames per role (lower = lower latency; default 50 ~= 1s at 20ms)",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def _print_devices() -> None:
    if sd is None:
        raise SystemExit("sounddevice is not installed (pip install -r usb-relay/requirements.txt)")

    devices = sd.query_devices()
    default_in = sd.default.device[0] if sd.default.device else None
    print("Input devices:")
    for i, d in enumerate(devices):
        if int(d.get("max_input_channels") or 0) <= 0:
            continue
        name = str(d.get("name") or "")
        chans = int(d.get("max_input_channels") or 0)
        rate = d.get("default_samplerate")
        star = " *" if default_in == i else ""
        print(f"[{i:2d}] ch={chans} defaultRate={rate} {name}{star}")


def _resolve_device(device_arg: str | None) -> int | None:
    if sd is None:
        raise SystemExit("sounddevice is not installed (pip install -r usb-relay/requirements.txt)")

    if device_arg is None:
        return None

    try:
        idx = int(device_arg)
    except ValueError:
        idx = None
    if idx is not None:
        devices = sd.query_devices()
        if idx < 0 or idx >= len(devices):
            raise SystemExit(f"--device index out of range: {idx}")
        if int(devices[idx].get("max_input_channels") or 0) <= 0:
            raise SystemExit(f"--device {idx} is not an input-capable device")
        return idx

    needle = device_arg.strip().lower()
    if not needle:
        return None

    devices = sd.query_devices()
    matches: list[int] = []
    for i, d in enumerate(devices):
        if int(d.get("max_input_channels") or 0) <= 0:
            continue
        name = str(d.get("name") or "").lower()
        if needle in name:
            matches.append(i)

    if not matches:
        raise SystemExit(f'No input device matches "{device_arg}". Use --list-devices.')
    if len(matches) > 1:
        print(f'Multiple devices match "{device_arg}":')
        for i in matches:
            d = devices[i]
            print(f"  [{i}] {d.get('name')}")
        raise SystemExit("Be more specific, or pass the device index.")
    return matches[0]


def _esp32_audio_endpoint(server: str) -> str:
    s = str(server).rstrip("/")
    if s.endswith("/esp32/audio"):
        return s
    return s + "/esp32/audio"


@dataclass(frozen=True, slots=True)
class AudioCfg:
    input_device: int | None
    input_sample_rate_hz: int
    input_channels: int
    output_sample_rate_hz: int
    frame_ms: int

    @property
    def bytes_per_frame(self) -> int:
        samples_per_frame = int(self.output_sample_rate_hz * (self.frame_ms / 1000.0))
        return samples_per_frame * 2  # mono PCM16


def _queue_put_drop_oldest(q: asyncio.Queue[bytes], frame: bytes) -> None:
    if q.full():
        try:
            _ = q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        q.put_nowait(frame)
    except asyncio.QueueFull:
        # Best-effort; drop frame.
        pass


async def _audio_process_loop(
    *,
    input_q: asyncio.Queue[bytes],
    out_by_role: dict[Role, asyncio.Queue[bytes]],
    cfg: AudioCfg,
    role_mode: RoleMode,
) -> None:
    log = logging.getLogger("usb_relay.audio")
    rate_state = None
    bufs: dict[Role, bytearray] = {r: bytearray() for r in out_by_role}
    bytes_per_frame = cfg.bytes_per_frame

    log.info(
        "Audio pipeline input=%sHz ch=%d -> output=%sHz mono frameMs=%d bytesPerFrame=%d roleMode=%s",
        cfg.input_sample_rate_hz,
        cfg.input_channels,
        cfg.output_sample_rate_hz,
        cfg.frame_ms,
        bytes_per_frame,
        role_mode,
    )

    while True:
        data = await input_q.get()

        if cfg.input_sample_rate_hz != cfg.output_sample_rate_hz:
            data, rate_state = audioop.ratecv(
                data,
                2,
                cfg.input_channels,
                cfg.input_sample_rate_hz,
                cfg.output_sample_rate_hz,
                rate_state,
            )

        if not data:
            continue

        if cfg.input_channels == 2:
            if role_mode in ("left", "both"):
                bufs["left"].extend(audioop.tomono(data, 2, 1, 0))
            if role_mode in ("right", "both"):
                bufs["right"].extend(audioop.tomono(data, 2, 0, 1))
        else:
            if role_mode == "both":
                bufs["left"].extend(data)
                bufs["right"].extend(data)
            elif role_mode == "left":
                bufs["left"].extend(data)
            else:
                bufs["right"].extend(data)

        for role, buf in bufs.items():
            q = out_by_role.get(role)
            if q is None:
                continue
            while len(buf) >= bytes_per_frame:
                frame = bytes(buf[:bytes_per_frame])
                del buf[:bytes_per_frame]
                _queue_put_drop_oldest(q, frame)


async def _ws_sender(*, server: str, role: Role, device_id: str, audio: AudioCfg, q: asyncio.Queue[bytes]) -> None:
    log = logging.getLogger(f"usb_relay.ws.{role}")
    base = _esp32_audio_endpoint(server)
    uri = f"{base}?deviceId={quote(device_id)}&role={quote(role)}"
    backoff_s = 0.5
    while True:
        try:
            async with websockets.connect(
                uri,
                max_size=2 * 1024 * 1024,
                open_timeout=5,
                ping_interval=20,
                ping_timeout=20,
            ) as ws:
                hello = {
                    "v": 1,
                    "type": "hello",
                    "deviceId": device_id,
                    "role": role,
                    "fwVersion": "usb_relay",
                    "audio": {
                        "format": "pcm_s16le",
                        "sampleRateHz": audio.output_sample_rate_hz,
                        "channels": 1,
                        "frameMs": audio.frame_ms,
                    },
                }
                await ws.send(json.dumps(hello, separators=(",", ":")))
                log.info("WS connected deviceId=%s -> %s", device_id, uri)
                backoff_s = 0.5

                drained = 0
                while not q.empty():
                    try:
                        _ = q.get_nowait()
                        drained += 1
                    except asyncio.QueueEmpty:
                        break
                if drained:
                    log.info("Drained %d stale frames before send loop", drained)

                while True:
                    frame = await q.get()
                    await ws.send(frame)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("WS reconnect in %.1fs (%s)", backoff_s, e)
            await asyncio.sleep(backoff_s)
            backoff_s = min(5.0, backoff_s * 1.8)


async def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("usb_relay")

    if args.list_devices:
        _print_devices()
        return

    if sd is None:
        raise SystemExit("sounddevice is not installed (pip install -r usb-relay/requirements.txt)")

    role_mode: RoleMode = str(args.role)
    roles: list[Role]
    if role_mode == "both":
        roles = ["left", "right"]
    elif role_mode == "left":
        roles = ["left"]
    else:
        roles = ["right"]

    device = _resolve_device(args.device)
    dev_info = sd.query_devices(device, "input") if device is not None else sd.query_devices(kind="input")
    max_ch = int(dev_info.get("max_input_channels") or 0)
    if max_ch <= 0:
        raise SystemExit("Selected device has no input channels")

    input_rate = int(args.input_sample_rate or int(float(dev_info.get("default_samplerate") or 48000)))
    if input_rate <= 0:
        raise SystemExit("--input-sample-rate must be > 0")

    if args.input_channels is not None:
        in_ch = int(args.input_channels)
    else:
        in_ch = 2 if max_ch >= 2 else 1
    if in_ch == 2 and max_ch < 2:
        log.warning("Device only supports mono; falling back to 1 channel")
        in_ch = 1

    out_rate = int(args.sample_rate)
    if out_rate <= 0:
        raise SystemExit("--sample-rate must be > 0")
    frame_ms = int(args.frame_ms)
    if frame_ms <= 0:
        raise SystemExit("--frame-ms must be > 0")

    audio_cfg = AudioCfg(
        input_device=device,
        input_sample_rate_hz=input_rate,
        input_channels=in_ch,
        output_sample_rate_hz=out_rate,
        frame_ms=frame_ms,
    )

    bytes_per_frame = audio_cfg.bytes_per_frame
    if (bytes_per_frame % 2) != 0:
        raise SystemExit("Internal error: bytes_per_frame must be even (PCM16 alignment)")

    if role_mode == "both" and in_ch == 1:
        log.info("Input is mono; duplicating audio to both left/right roles")

    queue_max_frames = max(1, int(args.queue_max_frames))
    input_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=queue_max_frames * 4)
    out_by_role: dict[Role, asyncio.Queue[bytes]] = {r: asyncio.Queue(maxsize=queue_max_frames) for r in roles}

    device_id_base = str(args.device_id)
    device_id_by_role: dict[Role, str]
    if role_mode == "both":
        device_id_by_role = {"left": f"{device_id_base}-left", "right": f"{device_id_base}-right"}
    else:
        device_id_by_role = {roles[0]: device_id_base}

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    stop_flag = threading.Event()
    fatal_exc: BaseException | None = None

    def _request_stop() -> None:
        if stop_flag.is_set():
            return
        stop_flag.set()
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    def _enqueue_input(data: bytes) -> None:
        if stop_flag.is_set():
            return
        if input_q.full():
            try:
                _ = input_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            input_q.put_nowait(data)
        except asyncio.QueueFull:
            pass

    def callback(indata, frames, time_info, status) -> None:  # type: ignore[no-untyped-def]
        if stop_flag.is_set():
            return
        if status:
            loop.call_soon_threadsafe(log.warning, "Audio status: %s", status)
        loop.call_soon_threadsafe(_enqueue_input, bytes(indata))

    blocksize = int(audio_cfg.input_sample_rate_hz * (audio_cfg.frame_ms / 1000.0))
    if blocksize <= 0:
        raise SystemExit("Internal error: computed blocksize <= 0")

    log.info(
        "Starting capture device=%s name=%s inputRate=%dHz inputCh=%d blocksize=%d outputRate=%dHz frameMs=%d",
        device if device is not None else "(default)",
        dev_info.get("name"),
        audio_cfg.input_sample_rate_hz,
        audio_cfg.input_channels,
        blocksize,
        audio_cfg.output_sample_rate_hz,
        audio_cfg.frame_ms,
    )

    tasks: list[asyncio.Task[None]] = []
    tasks.append(
        asyncio.create_task(
            _audio_process_loop(input_q=input_q, out_by_role=out_by_role, cfg=audio_cfg, role_mode=role_mode),
            name="audio_process",
        )
    )
    for role in roles:
        tasks.append(
            asyncio.create_task(
                _ws_sender(
                    server=str(args.server),
                    role=role,
                    device_id=device_id_by_role[role],
                    audio=audio_cfg,
                    q=out_by_role[role],
                ),
                name=f"ws_sender_{role}",
            )
        )

    def _on_task_done(t: asyncio.Task[None]) -> None:
        nonlocal fatal_exc
        if stop_flag.is_set():
            return
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        if exc is None:
            return
        fatal_exc = exc
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log.error("Task crashed name=%s\n%s", t.get_name(), tb.rstrip())
        _request_stop()

    for t in tasks:
        t.add_done_callback(_on_task_done)

    try:
        with sd.RawInputStream(
            device=device,
            samplerate=audio_cfg.input_sample_rate_hz,
            channels=audio_cfg.input_channels,
            dtype="int16",
            blocksize=blocksize,
            callback=callback,
        ):
            await stop_event.wait()
    finally:
        stop_flag.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if fatal_exc is not None:
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
