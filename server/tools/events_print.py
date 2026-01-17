from __future__ import annotations

import argparse
import asyncio

import websockets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print /events websocket messages")
    parser.add_argument("--url", default="ws://127.0.0.1:8765/events")
    return parser


async def main() -> None:
    args = build_parser().parse_args()
    async with websockets.connect(args.url) as ws:
        async for msg in ws:
            print(msg)


if __name__ == "__main__":
    asyncio.run(main())

