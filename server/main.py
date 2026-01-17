from __future__ import annotations

import argparse
import asyncio
import os

from hudserver.server import HudServer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HUD Glasses hackathon server")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    return parser


async def _amain() -> int:
    args = build_parser().parse_args()
    server = HudServer(host=args.host, port=args.port, log_level=args.log_level)
    await server.run()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()

