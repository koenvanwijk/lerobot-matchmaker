"""
room.py — Room state and per-role message queues.

Each room has two message queues: one for messages sent by the operator,
one for messages sent by the robot. Receivers long-poll their peer's queue.

  operator sends → queue["operator"] → robot reads
  robot sends    → queue["robot"]    → operator reads
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

VALID_ROLES = {"operator", "robot"}


@dataclass
class Room:
    name: str
    # queues keyed by sender role
    queues: dict[str, asyncio.Queue] = field(
        default_factory=lambda: {"operator": asyncio.Queue(), "robot": asyncio.Queue()}
    )
    # tracks connected roles for logging
    connected: set[str] = field(default_factory=set)

    async def put(self, sender_role: str, message: dict) -> None:
        assert sender_role in VALID_ROLES
        await self.queues[sender_role].put(message)
        logger.debug("Room %s: queued message from %s (qsize=%d)",
                     self.name, sender_role, self.queues[sender_role].qsize())

    async def get(self, sender_role: str, timeout: float = 25.0) -> dict | None:
        """
        Wait up to `timeout` seconds for a message sent by `sender_role`.
        Returns None on timeout (caller should loop back).
        """
        assert sender_role in VALID_ROLES
        try:
            return await asyncio.wait_for(self.queues[sender_role].get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None


class RoomRegistry:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def get_or_create(self, name: str) -> Room:
        if name not in self._rooms:
            self._rooms[name] = Room(name=name)
            logger.info("Room created: %s", name)
        return self._rooms[name]

    def list_rooms(self) -> list[str]:
        return list(self._rooms.keys())
