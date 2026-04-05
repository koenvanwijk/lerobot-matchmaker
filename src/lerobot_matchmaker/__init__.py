"""lerobot_matchmaker — WebRTC signaling and matchmaking server for lerobot-remote."""

from .server import create_app

__all__ = ["create_app"]
