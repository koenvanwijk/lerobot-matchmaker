"""
room.py — Room state and per-role message queues.

Each room has two fan-out queues: one per sender role.
Multiple subscribers (e.g. two operators) each get their own queue copy.

  operator sends → queues["operator"][subscriber_id] → robot reads
  robot sends    → queues["robot"][subscriber_id]    → operator reads

Rooms expire after ROOM_TTL_SECONDS of inactivity (no send or recv).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

VALID_ROLES = {"operator", "robot"}
ROOM_TTL_SECONDS = 300  # 5 minutes of inactivity → room deleted


@dataclass
class Room:
    name: str
    # Fan-out: queues[sender_role][subscriber_id] → asyncio.Queue
    queues: dict[str, dict[str, asyncio.Queue]] = field(
        default_factory=lambda: {"operator": {}, "robot": {}}
    )
    # Backlog: messages sent before any subscriber arrived, per role
    # New subscribers get a copy of all backlog messages on first subscribe.
    _backlog: dict[str, list] = field(
        default_factory=lambda: {"operator": [], "robot": []}
    )
    connected: set[str] = field(default_factory=set)
    last_active: float = field(default_factory=time.monotonic)

    def _touch(self) -> None:
        self.last_active = time.monotonic()

    def subscribe(self, sender_role: str, subscriber_id: str | None = None) -> str:
        """
        Register a new subscriber for messages from sender_role.
        If subscriber_id is provided, reuse it (idempotent).
        Returns the subscriber_id.
        New subscribers receive any backlogged messages immediately.
        """
        if subscriber_id is None:
            subscriber_id = str(uuid.uuid4())
        subs = self.queues[sender_role]
        if subscriber_id not in subs:
            q: asyncio.Queue = asyncio.Queue()
            # Pre-fill with any backlogged messages
            for msg in self._backlog[sender_role]:
                q.put_nowait(msg)
            subs[subscriber_id] = q
        return subscriber_id

    def unsubscribe(self, sender_role: str, subscriber_id: str) -> None:
        self.queues[sender_role].pop(subscriber_id, None)

    async def put(self, sender_role: str, message: dict) -> None:
        if sender_role not in VALID_ROLES:
            raise ValueError(f"Invalid role: {sender_role!r}")
        self._touch()
        subs = self.queues[sender_role]
        if not subs:
            # No subscribers yet — store in backlog for late arrivals
            self._backlog[sender_role].append(message)
            logger.debug("Room %s: no subscribers for %s, added to backlog (len=%d)",
                         self.name, sender_role, len(self._backlog[sender_role]))
        else:
            # Fan out to all current subscribers
            for q in subs.values():
                await q.put(message)
            logger.debug("Room %s: %s → %d subscribers", self.name, sender_role, len(subs))

    async def get(self, sender_role: str, subscriber_id: str, timeout: float = 25.0) -> dict | None:
        """
        Wait up to `timeout` seconds for a message from sender_role.
        Returns None on timeout (caller should retry).
        """
        if sender_role not in VALID_ROLES:
            raise ValueError(f"Invalid role: {sender_role!r}")
        q = self.queues[sender_role].get(subscriber_id)
        if q is None:
            return None
        self._touch()
        try:
            return await asyncio.wait_for(q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def is_expired(self) -> bool:
        return time.monotonic() - self.last_active > ROOM_TTL_SECONDS


class RoomRegistry:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._cleanup_task: asyncio.Task | None = None

    def start(self) -> None:
        """Start background cleanup task. Call from app startup."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("RoomRegistry cleanup task started (TTL=%ds)", ROOM_TTL_SECONDS)

    def stop(self) -> None:
        """Cancel cleanup task. Call from app shutdown."""
        if self._cleanup_task:
            self._cleanup_task.cancel()

    def get_or_create(self, name: str) -> Room:
        if name not in self._rooms:
            self._rooms[name] = Room(name=name)
            logger.info("Room created: %s (total=%d)", name, len(self._rooms))
        return self._rooms[name]

    def list_rooms(self) -> list[dict]:
        now = time.monotonic()
        return [
            {
                "name": r.name,
                "connected": sorted(r.connected),
                "idle_seconds": round(now - r.last_active, 1),
            }
            for r in self._rooms.values()
        ]

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                expired = [name for name, room in self._rooms.items() if room.is_expired()]
                for name in expired:
                    del self._rooms[name]
                    logger.info("Room expired and removed: %s", name)
                if expired:
                    logger.info("Cleanup: removed %d room(s), %d remain", len(expired), len(self._rooms))
            except asyncio.CancelledError:
                break
