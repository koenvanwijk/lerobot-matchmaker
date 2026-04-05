"""
Entry point: python -m lerobot_matchmaker  or  lerobot-matchmaker (CLI script)

Usage:
  lerobot-matchmaker [--host HOST] [--port PORT]
  python -m lerobot_matchmaker --port 8080
"""

from __future__ import annotations

import argparse
import logging

from aiohttp import web

from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="LeRobot WebRTC signaling server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    app = create_app()
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
